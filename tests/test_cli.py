"""End-to-end tests for the command-line interface (file outputs)."""

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from cisco_exos_translator.cli import main

CONFIG = (
    "hostname SW1\nvlan 10\n name USERS\ninterface Gi1/0/1\n switchport access vlan 10\n"
    " ip access-group A in\nip access-list extended A\n permit ip any any\n"
)
STACK = "switch 1 priority 15\nswitch 2 priority 10\ninterface Gi2/0/1\n shutdown\n"


def run_cli(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


class CliTests(unittest.TestCase):
    def test_no_args_prints_usage(self):
        code, out, _ = run_cli([])
        self.assertEqual(code, 1)
        self.assertIn("Usage:", out)

    def test_missing_file(self):
        code, _, err = run_cli(["/nonexistent/switch.cfg"])
        self.assertEqual(code, 1)
        self.assertIn("Error: failed to read '/nonexistent/switch.cfg'", err)

    def test_first_run_writes_mapping_xsf_and_pol(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "sw1.cfg"
            cfg.write_text(CONFIG)
            code, out, err = run_cli([str(cfg)])
            self.assertEqual(code, 0)
            self.assertIn("sw1.map.json written", out)
            self.assertIn("sw1.cfg -> ", out)
            self.assertIn("1 .pol file(s)", out)
            self.assertEqual(err, "")
            xsf = (Path(tmp) / "sw1.xsf").read_text()
            self.assertIn('configure snmp sysName "SW1"', xsf)
            self.assertIn("configure access-list A ports 1 ingress", xsf)
            self.assertTrue((Path(tmp) / "sw1-acls" / "A.pol").exists())
            self.assertFalse((Path(tmp) / "sw1.stack-setup.txt").exists())
            mapping = json.loads((Path(tmp) / "sw1.map.json").read_text())
            self.assertEqual(mapping["vlans"], {"10": "USERS"})

    def test_second_run_applies_mapping_edits_and_keeps_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "sw1.cfg"
            cfg.write_text(CONFIG)
            run_cli([str(cfg)])
            map_path = Path(tmp) / "sw1.map.json"
            mapping = json.loads(map_path.read_text())
            mapping["vlans"]["10"] = "STAFF"
            mapping["ports"]["GigabitEthernet1/0/1"] = "5"
            map_path.write_text(json.dumps(mapping))
            code, out, err = run_cli([str(cfg)])
            self.assertEqual(code, 0)
            self.assertNotIn("written", out)
            xsf = (Path(tmp) / "sw1.xsf").read_text()
            self.assertIn('create vlan "STAFF" tag 10', xsf)
            self.assertIn('configure vlan "STAFF" add ports 5 untagged', xsf)
            self.assertEqual(json.loads(map_path.read_text()), mapping)  # never overwritten

    def test_invalid_mapping_json_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "sw1.cfg"
            cfg.write_text(CONFIG)
            (Path(tmp) / "sw1.map.json").write_text("{oops")
            code, _, err = run_cli([str(cfg)])
            self.assertEqual(code, 1)
            self.assertIn("Error: invalid JSON in mapping file", err)

    def test_stack_runbook_and_warning_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "stack.cfg"
            cfg.write_text(STACK + "spanning-tree mode rapid-pvst\n")
            code, out, err = run_cli([str(cfg)])
            self.assertEqual(code, 0)
            self.assertIn("stack.stack-setup.txt (run before loading the .xsf)", out)
            self.assertIn("1 untranslated line(s)", err)
            self.assertIn("see the WARNINGS header", err)
            self.assertIn("load script stack.xsf", (Path(tmp) / "stack.stack-setup.txt").read_text())

    def test_multiple_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            a, b = Path(tmp) / "a.cfg", Path(tmp) / "b.cfg"
            a.write_text(CONFIG)
            b.write_text("hostname B\n")
            code, out, _ = run_cli([str(a), str(b)])
            self.assertEqual(code, 0)
            self.assertTrue((Path(tmp) / "a.xsf").exists())
            self.assertIn('configure snmp sysName "B"', (Path(tmp) / "b.xsf").read_text())


if __name__ == "__main__":
    unittest.main()
