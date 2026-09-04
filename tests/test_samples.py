"""End-to-end checks on the sample configs shipped in the repository."""

import unittest
from pathlib import Path

from cisco_exos_translator import (
    build_default_mapping,
    generate_exos_config,
    generate_stack_setup,
    parse_cisco_config,
)

ROOT = Path(__file__).resolve().parent.parent


def translate(rel_path):
    config = parse_cisco_config((ROOT / rel_path).read_text(encoding="utf-8"))
    xsf, warnings, pols = generate_exos_config(config, build_default_mapping(config))
    return config, xsf, warnings, pols


class SampleCfgTests(unittest.TestCase):
    """sample.cfg: 2-member stack, LAG, uplink placeholder, SPAN + FSPAN."""

    @classmethod
    def setUpClass(cls):
        cls.config, cls.xsf, cls.warnings, cls.pols = translate("sample.cfg")

    def test_parse(self):
        c = self.config
        self.assertEqual(c.hostname, "SW-STACK-01")
        self.assertEqual(sorted(c.vlans), [10, 20, 30, 40, 41, 42])
        self.assertEqual(sorted(c.stack_members), [1, 2])
        self.assertEqual(c.port_channels[1].members, ["GigabitEthernet1/0/10", "GigabitEthernet1/0/11"])
        self.assertEqual(c.interfaces["TenGigabitEthernet1/1/1"].trunk_allowed_vlans, {10, 30, 31, 32, 40})
        self.assertEqual(sorted(c.monitor_sessions), [1, 2])
        self.assertEqual(c.monitor_sessions[2].filter_acl, "MIRROR_PING")
        self.assertEqual(c.unsupported_lines, [])
        self.assertEqual(
            c.warnings,
            [
                "Interface GigabitEthernet1/0/48: access VLAN 99 is referenced but not defined",
                "Interface GigabitEthernet2/0/1: routed interface (no switchport / ip address) is outside L2 conversion scope",
                "Interface TenGigabitEthernet1/1/1: trunk allowed VLAN 31 is referenced but not defined",
                "Interface TenGigabitEthernet1/1/1: trunk allowed VLAN 32 is referenced but not defined",
            ],
        )

    def test_generated_script(self):
        lines = self.xsf.splitlines()
        for expected in (
            'configure snmp sysName "SW-STACK-01"',
            'create vlan "GUEST_41" tag 41',
            "enable sharing 1:10 grouping 1:10,1:11 algorithm address-based L3_L4 lacp",
            'configure vlan "USERS" add ports 1:10 untagged',
            "disable ports 1:2",
            'configure vlan "USERS" add ports 1:{uplink-m1-p1} untagged',
            "configure mirror monitor_1 to port 1:47",
            'configure mirror monitor_1 add vlan "VLAN_20"',
            "configure access-list monitor_2_filter ports 1:5 egress",
            "enable mirror monitor_2",
        ):
            self.assertIn(expected, lines, expected)
        self.assertEqual(list(self.pols), ["monitor_2_filter"])
        self.assertIn("mirror monitor_2;", self.pols["monitor_2_filter"])
        self.assertIn("configure stacking slot 1 priority 15", generate_stack_setup(self.config, "sample.xsf"))

    def test_deterministic(self):
        _, xsf2, _, pols2 = translate("sample.cfg")
        self.assertEqual(self.xsf, xsf2)
        self.assertEqual(self.pols, pols2)


class DemoCfgTests(unittest.TestCase):
    """demo.cfg: standalone switch with unsupported lines and an LACP bundle."""

    def test_translation(self):
        config, xsf, warnings, pols = translate("demo.cfg")
        self.assertEqual(config.warnings, [])
        lines = xsf.splitlines()
        self.assertIn("enable sharing 9 grouping 9,10 algorithm address-based L3_L4 lacp", lines)
        self.assertIn('configure vlan "USERS" add ports 1 untagged', lines)
        self.assertIn("disable ports 12", lines)
        self.assertIn("#   - 'switchport voice vlan 100' x8 (lines 19)", lines)
        self.assertNotIn("#   - 'end' (line 50)", lines)
        self.assertEqual(pols, {})
        self.assertIsNone(generate_stack_setup(config, "demo.xsf"))


class StackDemoCfgTests(unittest.TestCase):
    """stack-demo.cfg: cross-member LAG on a 2-node stack."""

    def test_translation(self):
        config, xsf, warnings, pols = translate("stack-demo.cfg")
        lines = xsf.splitlines()
        self.assertIn("enable sharing 1:10 grouping 1:10,2:10 algorithm address-based L3_L4 lacp", lines)
        self.assertIn('configure ports 2:10 description-string "Server LAG member B"', lines)
        self.assertIn('configure vlan "SERVERS" add ports 2:8 untagged', lines)
        runbook = generate_stack_setup(config, "stack-demo.xsf")
        self.assertIn("#   Cisco switch 2: ws-c3850-12s, priority 10", runbook)


class AclListCfgTests(unittest.TestCase):
    """docs/cisco-acl-list.cfg: every ACL form, applied and warned."""

    def test_translation(self):
        config, xsf, warnings, pols = translate("docs/cisco-acl-list.cfg")
        self.assertEqual(sorted(pols), ["SERVERS_IN", "V_100"])
        lines = xsf.splitlines()
        self.assertIn("configure access-list SERVERS_IN ports 1 ingress", lines)
        self.assertIn("configure access-list V_100 ports 2 ingress", lines)
        self.assertIn('configure access-list SERVERS_IN vlan "USERS" ingress', lines)
        self.assertIn('configure vlan "USERS" ipaddress 10.10.10.1/24', lines)
        self.assertIn("#   - 'ip access-group 100 out' (line 67)", lines)
        self.assertIn("ACL WARN-EXAMPLES: 5 rule(s) could not be translated; the generated ACL is incomplete -- review before applying", config.warnings)
        self.assertIn("ACL MGMT: defined but not applied to any translated target; skipped", warnings)
        # every entry has a non-empty match, including "deny ip any any"
        self.assertNotIn("    if {\n    } then", pols["SERVERS_IN"])
        self.assertEqual(pols["SERVERS_IN"].count("source-address 0.0.0.0/0"), 2)


if __name__ == "__main__":
    unittest.main()
