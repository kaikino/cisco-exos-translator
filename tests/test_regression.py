# The EXOS output must not change. Fixtures were captured from the translator
# before migration findings were introduced; any diff here is a regression
# unless it is a documented, deliberate fix.

from __future__ import annotations

import unittest
from pathlib import Path

import main
from cisco_exos_translator.generator import build_default_mapping, generate_exos_config

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"

CONFIGS = ["sample.cfg", "demo.cfg", "stack-demo.cfg", "docs/cisco-acl-list.cfg"]


class GoldenOutputTest(unittest.TestCase):
    def translate(self, name):
        config = main.parse_cisco_config_file(str(ROOT / name))
        mapping = build_default_mapping(config)
        return generate_exos_config(config, mapping, [])

    def test_xsf_output_unchanged(self):
        for name in CONFIGS:
            with self.subTest(config=name):
                text, _, _ = self.translate(name)
                expected = FIXTURES / f"{Path(name).stem}.xsf.expected"
                self.assertEqual(text, expected.read_text(encoding="utf-8"))

    def test_policy_files_unchanged(self):
        _, _, pol_files = self.translate("docs/cisco-acl-list.cfg")
        self.assertEqual(sorted(pol_files), ["SERVERS_IN", "V_100"])
        for name, content in pol_files.items():
            with self.subTest(policy=name):
                expected = FIXTURES / f"acl-{name}.pol.expected"
                self.assertEqual(content, expected.read_text(encoding="utf-8"))

    def test_collector_does_not_change_output(self):
        # Passing a collector must not alter a single byte of the .xsf.
        from cisco_exos_translator.findings import FindingsCollector

        for name in CONFIGS:
            with self.subTest(config=name):
                plain, plain_warnings, _ = self.translate(name)
                config = main.parse_cisco_config_file(str(ROOT / name))
                collector = FindingsCollector()
                with_findings, warnings, _ = generate_exos_config(
                    config, build_default_mapping(config), [], collector
                )
                self.assertEqual(plain, with_findings)
                self.assertEqual(plain_warnings, warnings)
                self.assertGreater(len(collector), 0)


if __name__ == "__main__":
    unittest.main()
