"""Tests for the user-editable mapping file (.map.json) handling."""

import json
import os
import tempfile
import unittest
from pathlib import Path

from cisco_exos_translator.mapping import load_mapping, merge_mapping, write_mapping


class FileTests(unittest.TestCase):
    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.map.json"
            write_mapping(path, {"vlans": {"10": "A"}})
            self.assertEqual(load_mapping(path), {"vlans": {"10": "A"}})
            self.assertTrue(path.read_text().endswith("}\n"))

    def test_invalid_json_and_non_object(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.json"
            bad.write_text("{not json")
            with self.assertRaises(ValueError):
                load_mapping(bad)
            arr = Path(tmp) / "arr.json"
            arr.write_text("[1, 2]")
            with self.assertRaises(ValueError):
                load_mapping(arr)


class MergeTests(unittest.TestCase):
    def setUp(self):
        self.defaults = {
            "_help": ["x"],
            "vlans": {"10": "USERS", "20": "VLAN_20"},
            "ports": {"GigabitEthernet1/0/1": "1"},
            "uplinks": {"start": None},
            "lags": {"Port-channel1": {"master": "9", "mode": "lacp"}},
            "mirrors": {"1": "monitor_1"},
            "mirror_egress_mode": {"1": "acl"},
        }

    def test_user_values_win_and_missing_entries_are_noted(self):
        merged, notes = merge_mapping(self.defaults, {"vlans": {"10": "STAFF"}, "uplinks": {"start": 49}})
        self.assertEqual(merged["vlans"], {"10": "STAFF", "20": "VLAN_20"})
        self.assertEqual(merged["uplinks"], {"start": 49})
        self.assertIn("mapping: no entry for vlans '20' in the mapping file; using the derived default", notes)
        self.assertIn("mapping: no entry for ports 'GigabitEthernet1/0/1' in the mapping file; using the derived default", notes)
        # defaults are not mutated
        self.assertEqual(self.defaults["vlans"]["10"], "USERS")

    def test_unknown_entries_are_ignored_with_a_note(self):
        merged, notes = merge_mapping(self.defaults, {"ports": {"GigabitEthernet9/9/9": "99"}})
        self.assertNotIn("GigabitEthernet9/9/9", merged["ports"])
        self.assertIn("mapping: unknown ports entry 'GigabitEthernet9/9/9' ignored (not in the current Cisco config)", notes)

    def test_lag_entries_merge_per_field(self):
        merged, _ = merge_mapping(self.defaults, {"lags": {"Port-channel1": {"mode": "static"}}})
        self.assertEqual(merged["lags"]["Port-channel1"], {"master": "9", "mode": "static"})

    def test_non_object_section_is_ignored(self):
        merged, notes = merge_mapping(self.defaults, {"vlans": "oops"})
        self.assertEqual(merged["vlans"], self.defaults["vlans"])
        self.assertIn("mapping: section 'vlans' is not an object; ignored", notes)


if __name__ == "__main__":
    unittest.main()
