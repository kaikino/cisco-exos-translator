"""Tests for the cross-reference validation pass."""

import unittest

from cisco_exos_translator import parse_cisco_config


class VlanReferenceTests(unittest.TestCase):
    def test_undefined_access_trunk_and_native_vlans(self):
        parsed = parse_cisco_config(
            "vlan 10\ninterface Gi1/0/1\n switchport access vlan 99\n"
            "interface Gi1/0/2\n switchport mode trunk\n switchport trunk allowed vlan 10,31\n"
            " switchport trunk native vlan 77\n"
            "interface Gi1/0/3\n switchport mode trunk\n switchport trunk native vlan 1\n"
        )
        self.assertEqual(
            parsed.warnings,
            [
                "Interface GigabitEthernet1/0/1: access VLAN 99 is referenced but not defined",
                "Interface GigabitEthernet1/0/2: trunk allowed VLAN 31 is referenced but not defined",
                "Interface GigabitEthernet1/0/2: trunk native VLAN 77 is referenced but not defined",
            ],
        )

    def test_mode_conflicts(self):
        parsed = parse_cisco_config(
            "vlan 10\ninterface Gi1/0/1\n switchport mode access\n switchport trunk native vlan 10\n"
            "interface Gi1/0/2\n switchport mode trunk\n switchport access vlan 10\n"
        )
        self.assertEqual(
            parsed.warnings,
            [
                "Interface GigabitEthernet1/0/1: interface has access mode but trunk VLAN settings",
                "Interface GigabitEthernet1/0/2: interface has trunk mode but access VLAN set",
            ],
        )

    def test_warnings_follow_natural_port_order(self):
        parsed = parse_cisco_config(
            "interface Gi1/0/10\n switchport access vlan 5\ninterface Gi1/0/2\n switchport access vlan 5\n"
        )
        self.assertEqual([w.split(":")[0] for w in parsed.warnings],
                         ["Interface GigabitEthernet1/0/2", "Interface GigabitEthernet1/0/10"])


class RoutedAndBundleTests(unittest.TestCase):
    def test_routed_port_warned_but_addressed_svi_is_not(self):
        parsed = parse_cisco_config(
            "interface Gi1/0/1\n no switchport\ninterface Vlan10\n ip address 10.0.0.1 255.255.255.0\n"
        )
        self.assertEqual(
            parsed.warnings,
            ["Interface GigabitEthernet1/0/1: routed interface (no switchport / ip address) is outside L2 conversion scope"],
        )

    def test_empty_port_channel(self):
        parsed = parse_cisco_config("interface Port-channel3\n switchport mode access\n")
        self.assertEqual(parsed.warnings, ["Port-channel3: Port-channel exists but has no member interfaces"])

    def test_bundle_vlan_warning_once(self):
        parsed = parse_cisco_config(
            "vlan 10\ninterface Port-channel5\n switchport mode trunk\n switchport trunk allowed vlan 10,99\n"
            "interface Gi1/0/1\n channel-group 5 mode active\n"
        )
        self.assertEqual(parsed.warnings, ["Port-channel Port-channel5: trunk allowed VLAN 99 is referenced but not defined"])


class MonitorSessionTests(unittest.TestCase):
    def test_incomplete_sessions(self):
        parsed = parse_cisco_config(
            "monitor session 1 source interface Gi1/0/1\n"
            "monitor session 2 destination interface Gi1/0/2\n"
        )
        self.assertEqual(
            parsed.warnings,
            [
                "monitor session 1: no destination interface; session not translated",
                "monitor session 2: no source interface/vlan; session not translated",
            ],
        )

    def test_overlap_undefined_vlan_and_missing_filter(self):
        parsed = parse_cisco_config(
            "vlan 10\nmonitor session 1 source interface Gi1/0/1\nmonitor session 1 source vlan 1,10,20\n"
            "monitor session 1 destination interface Gi1/0/1\nmonitor session 1 filter ip access-group NOPE\n"
        )
        self.assertEqual(
            parsed.warnings,
            [
                "monitor session 1: GigabitEthernet1/0/1 is both a source and the destination",
                "monitor session 1: source VLAN 20 is referenced but not defined",
                "monitor session 1: filter ACL NOPE is referenced but not defined; sources mirrored unfiltered",
            ],
        )


if __name__ == "__main__":
    unittest.main()
