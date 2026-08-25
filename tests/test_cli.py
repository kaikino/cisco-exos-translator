# CLI behaviour: artifacts, opt-in AI, and failure isolation.

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

import main
from cisco_exos_translator import ai

CONFIG = """\
hostname EDGE-1
!
vlan 10
 name USERS
!
vlan 20
 name VOICE
!
interface GigabitEthernet1/0/1
 description Desk 1
 switchport mode access
 switchport access vlan 10
!
interface GigabitEthernet1/0/2
 switchport mode trunk
 switchport trunk allowed vlan 10,20
!
"""

SENTINEL_KEY = "sk-ant-THIS-MUST-NEVER-BE-WRITTEN"


class FakeClient(ai.LLMClient):
    def __init__(self, reply="## Executive summary\nOK", error=None):
        self.reply = reply
        self.error = error

    def complete(self, system, prompt):
        if self.error:
            raise self.error
        return self.reply

    def describe(self):
        return {"provider": "fake", "model": "fake-1"}


class CliTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.cfg = self.tmp / "edge1.cfg"
        self.cfg.write_text(CONFIG, encoding="utf-8")
        # A credential is present in the environment for every test, so any
        # leak into an artifact or the console shows up as a failure.
        self._old_key = os.environ.get("ANTHROPIC_API_KEY")
        os.environ["ANTHROPIC_API_KEY"] = SENTINEL_KEY
        self.addCleanup(self._restore_key)

    def _restore_key(self):
        if self._old_key is None:
            os.environ.pop("ANTHROPIC_API_KEY", None)
        else:
            os.environ["ANTHROPIC_API_KEY"] = self._old_key

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def assert_no_credentials(self, *console):
        for path in self.tmp.rglob("*"):
            if path.is_file():
                self.assertNotIn(SENTINEL_KEY, path.read_text(encoding="utf-8"), str(path))
        for text in console:
            self.assertNotIn(SENTINEL_KEY, text)

    def install_fake_ai(self, client):
        original = ai.AnthropicLLMClient
        ai.AnthropicLLMClient = lambda *a, **kw: client
        self.addCleanup(setattr, ai, "AnthropicLLMClient", original)


class DefaultRunTest(CliTestCase):
    def test_translation_and_findings_without_ai(self):
        code, out, err = self.run_cli(str(self.cfg))
        self.assertEqual(code, 0)
        xsf = self.tmp / "edge1.xsf"
        findings = self.tmp / "edge1.findings.json"
        self.assertTrue(xsf.exists())
        self.assertTrue(findings.exists())
        self.assertFalse((self.tmp / "edge1.migration-report.md").exists())
        self.assertIn('create vlan "USERS" tag 10', xsf.read_text(encoding="utf-8"))

        document = json.loads(findings.read_text(encoding="utf-8"))
        self.assertEqual(document["artifacts"]["exos_config"], "edge1.xsf")
        self.assertIsNone(document["artifacts"]["migration_report"])
        self.assertEqual(document["generation"]["ai_report"],
                         {"requested": False, "status": "disabled"})
        self.assertGreater(document["summary"]["translated"], 0)
        self.assert_no_credentials(out, err)

    def test_no_findings_flag(self):
        code, _, _ = self.run_cli(str(self.cfg), "--no-findings")
        self.assertEqual(code, 0)
        self.assertTrue((self.tmp / "edge1.xsf").exists())
        self.assertFalse((self.tmp / "edge1.findings.json").exists())

    def test_usage_without_arguments(self):
        code, out, _ = self.run_cli()
        self.assertEqual(code, 1)
        self.assertIn("Usage: main.py", out)

    def test_unreadable_input_writes_nothing(self):
        code, _, err = self.run_cli(str(self.tmp / "missing.cfg"))
        self.assertEqual(code, 1)
        self.assertIn("failed to read", err)
        self.assertFalse((self.tmp / "missing.xsf").exists())

    def test_multiple_inputs_and_output_dir(self):
        second = self.tmp / "edge2.cfg"
        second.write_text(CONFIG.replace("EDGE-1", "EDGE-2"), encoding="utf-8")
        outdir = self.tmp / "artifacts"
        code, _, _ = self.run_cli(str(self.cfg), str(second), "--output-dir", str(outdir))
        self.assertEqual(code, 0)
        for stem in ("edge1", "edge2"):
            self.assertTrue((outdir / f"{stem}.xsf").exists())
            self.assertTrue((outdir / f"{stem}.findings.json").exists())
            self.assertTrue((outdir / f"{stem}.map.json").exists())
        # Inputs are untouched; artifacts only land in the output directory.
        self.assertFalse((self.tmp / "edge1.xsf").exists())
        names = {
            json.loads((outdir / f"{s}.findings.json").read_text())["input"]["filename"]
            for s in ("edge1", "edge2")
        }
        self.assertEqual(names, {"edge1.cfg", "edge2.cfg"})

    def test_rerun_is_byte_identical(self):
        self.run_cli(str(self.cfg))
        first_xsf = (self.tmp / "edge1.xsf").read_text(encoding="utf-8")
        first_findings = (self.tmp / "edge1.findings.json").read_text(encoding="utf-8")
        self.run_cli(str(self.cfg))
        self.assertEqual(first_xsf, (self.tmp / "edge1.xsf").read_text(encoding="utf-8"))
        self.assertEqual(
            first_findings, (self.tmp / "edge1.findings.json").read_text(encoding="utf-8")
        )


class AiSummaryTest(CliTestCase):
    def test_ai_summary_uses_the_client_and_records_the_artifact(self):
        self.install_fake_ai(FakeClient("## Executive summary\nTwo VLANs translated."))
        code, out, err = self.run_cli(str(self.cfg), "--ai-summary")
        self.assertEqual(code, 0)
        report = self.tmp / "edge1.migration-report.md"
        self.assertTrue(report.exists())
        body = report.read_text(encoding="utf-8")
        self.assertIn("Not deployable configuration", body)
        self.assertIn("Two VLANs translated.", body)

        document = json.loads((self.tmp / "edge1.findings.json").read_text())
        self.assertEqual(document["artifacts"]["migration_report"],
                         "edge1.migration-report.md")
        self.assertEqual(document["generation"]["ai_report"]["status"], "generated")
        self.assertIn("structured finding(s)", err)  # the privacy disclosure
        self.assert_no_credentials(out, err)

    def test_ai_failure_leaves_deterministic_artifacts_intact(self):
        self.install_fake_ai(FakeClient(error=ai.AIRequestError("provider unreachable")))
        code, out, err = self.run_cli(str(self.cfg), "--ai-summary")
        self.assertEqual(code, 0)
        self.assertIn("AI summary not generated", err)
        self.assertFalse((self.tmp / "edge1.migration-report.md").exists())

        xsf = (self.tmp / "edge1.xsf").read_text(encoding="utf-8")
        self.assertIn('create vlan "USERS" tag 10', xsf)
        document = json.loads((self.tmp / "edge1.findings.json").read_text())
        self.assertEqual(document["generation"]["ai_report"]["status"], "failed")
        self.assertIn("provider unreachable", document["generation"]["ai_report"]["error"])
        self.assertIsNone(document["artifacts"]["migration_report"])
        self.assert_no_credentials(out, err)

    def test_ai_configuration_error_is_reported_not_raised(self):
        self.install_fake_ai(
            FakeClient(error=ai.AIConfigurationError("the optional 'anthropic' package is not installed"))
        )
        code, _, err = self.run_cli(str(self.cfg), "--ai-summary")
        self.assertEqual(code, 0)
        self.assertIn("not installed", err)
        self.assertTrue((self.tmp / "edge1.findings.json").exists())

    def test_findings_are_identical_with_and_without_ai(self):
        self.run_cli(str(self.cfg))
        without = json.loads((self.tmp / "edge1.findings.json").read_text())
        self.install_fake_ai(FakeClient())
        self.run_cli(str(self.cfg), "--ai-summary")
        with_ai = json.loads((self.tmp / "edge1.findings.json").read_text())
        self.assertEqual(without["findings"], with_ai["findings"])
        self.assertEqual(without["summary"], with_ai["summary"])


if __name__ == "__main__":
    unittest.main()
