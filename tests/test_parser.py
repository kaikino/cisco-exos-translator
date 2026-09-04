"""Tests for the parser pass (blocks -> ParsedConfig IR)."""

import unittest

from cisco_exos_translator import parse_cisco_config
from cisco_exos_translator.models import PhysicalInterface, PortChannelInterface


def unsupported(parsed):
    items = list(parsed.unsupported_lines)
    for iface in parsed.interfaces.values():
        items.extend(iface.unsupported_lines)
    return sorted((u.line_number, u.text, u.reason) for u in items)


class GlobalTests(unittest.TestCase):
    def test_hostname_and_stack(self):
        parsed = parse_cisco_config(
            "hostname SW1\nswitch 1 provision ws-c3850-48p\nswitch 1 priority 15\nswitch 2 priority 10\n"
        )
        self.assertEqual(parsed.hostname, "SW1")
        self.assertEqual(sorted(parsed.stack_members), [1, 2])
        self.assertEqual(parsed.stack_members[1].provision_model, "ws-c3850-48p")
        self.assertEqual(parsed.stack_members[1].priority, 15)
        self.assertIsNone(parsed.stack_members[2].provision_model)

    def test_stack_members_inferred_from_port_names(self):
        parsed = parse_cisco_config("interface Gi3/0/1\n shutdown\n")
        self.assertEqual(list(parsed.stack_members), [3])

    def test_end_and_ip_routing_are_consumed(self):
        parsed = parse_cisco_config("ip routing\nend\n")
        self.assertEqual(parsed.unsupported_lines, [])

    def test_unknown_global_lines_are_kept_with_line_numbers(self):
        parsed = parse_cisco_config("hostname X\nspanning-tree mode rapid-pvst\nntp server 1.1.1.1\n")
        self.assertEqual(
            [(u.line_number, u.text, u.context) for u in parsed.unsupported_lines],
            [(2, "spanning-tree mode rapid-pvst", "global"), (3, "ntp server 1.1.1.1", "global")],
        )

    def test_static_routes(self):
        parsed = parse_cisco_config(
            "ip route 0.0.0.0 0.0.0.0 10.0.0.1\nip route 192.168.1.0 255.255.255.0 10.0.0.2\n"
            "ip route 10.9.0.0 255.0.255.0 10.0.0.3\nip route 10.8.0.0 255.255.0.0 Null0\n"
        )
        self.assertEqual(
            parsed.static_routes, [("0.0.0.0/0", "10.0.0.1"), ("192.168.1.0/24", "10.0.0.2")]
        )
        self.assertEqual([u.line_number for u in parsed.unsupported_lines], [3, 4])


class VlanTests(unittest.TestCase):
    def test_lists_ranges_and_names(self):
        parsed = parse_cisco_config('vlan 10\n name USERS\nvlan 20,30\nvlan 40-42\n name "GUEST"\n')
        self.assertEqual(sorted(parsed.vlans), [10, 20, 30, 40, 41, 42])
        self.assertEqual(parsed.vlans[10].name, "USERS")
        self.assertIsNone(parsed.vlans[20].name)
        for vid in (40, 41, 42):
            self.assertEqual(parsed.vlans[vid].name, "GUEST")
        self.assertEqual(parsed.vlans[10].source_lines, [1])

    def test_bad_vlan_header_is_reported(self):
        parsed = parse_cisco_config("vlan 10-5\n name BAD\n")
        self.assertEqual(parsed.vlans, {})
        self.assertEqual(parsed.warnings, ["Line 1: failed to parse VLAN header: invalid VLAN range: '10-5'"])
        self.assertEqual([u.line_number for u in parsed.unsupported_lines], [1])

    def test_unknown_vlan_subcommand_is_reported(self):
        parsed = parse_cisco_config("vlan 10\n name A\n state active\n")
        self.assertEqual([(u.line_number, u.text) for u in parsed.unsupported_lines], [(3, "state active")])


class InterfaceTests(unittest.TestCase):
    def test_access_port(self):
        parsed = parse_cisco_config(
            "interface GigabitEthernet1/0/1\n description User port\n switchport mode access\n"
            " switchport access vlan 10\n shutdown\n"
        )
        iface = parsed.interfaces["GigabitEthernet1/0/1"]
        self.assertIsInstance(iface, PhysicalInterface)
        self.assertEqual((iface.mode, iface.access_vlan, iface.description), ("access", 10, "User port"))
        self.assertEqual((iface.stack_member, iface.module, iface.port), (1, 0, 1))
        self.assertTrue(iface.shutdown)

    def test_no_shutdown_after_shutdown(self):
        parsed = parse_cisco_config("interface Gi1/0/1\n shutdown\n no shutdown\n")
        self.assertFalse(parsed.interfaces["GigabitEthernet1/0/1"].shutdown)

    def test_interface_range_expansion(self):
        parsed = parse_cisco_config("interface range Gi1/0/2-4, Gi1/0/10\n switchport access vlan 10\n")
        self.assertEqual(
            list(parsed.interfaces),
            ["GigabitEthernet1/0/2", "GigabitEthernet1/0/3", "GigabitEthernet1/0/4", "GigabitEthernet1/0/10"],
        )
        for iface in parsed.interfaces.values():
            self.assertEqual(iface.access_vlan, 10)
            self.assertEqual(iface.source_lines, [1])

    def test_bad_range_is_reported(self):
        parsed = parse_cisco_config("interface range Gi1/0/5-2\n shutdown\n")
        self.assertEqual(parsed.interfaces, {})
        self.assertEqual(
            parsed.warnings, ["Line 1: interface range expansion failed: invalid port range in 'Gi1/0/5-2'"]
        )

    def test_same_interface_in_two_blocks_merges(self):
        parsed = parse_cisco_config(
            "interface Gi1/0/1\n description first\ninterface GigabitEthernet1/0/1\n switchport access vlan 10\n"
        )
        iface = parsed.interfaces["GigabitEthernet1/0/1"]
        self.assertEqual((iface.description, iface.access_vlan), ("first", 10))
        self.assertEqual(iface.source_lines, [1, 3])

    def test_trunk_allowed_add_remove_and_native(self):
        parsed = parse_cisco_config(
            "vlan 10,20,30-32,40\ninterface Te1/1/1\n switchport mode trunk\n switchport trunk allowed vlan 10,20,30-32\n"
            " switchport trunk allowed vlan add 40\n switchport trunk allowed vlan remove 20\n"
            " switchport trunk native vlan 10\n"
        )
        trunk = parsed.interfaces["TenGigabitEthernet1/1/1"]
        self.assertEqual(trunk.mode, "trunk")
        self.assertEqual(trunk.trunk_allowed_vlans, {10, 30, 31, 32, 40})
        self.assertEqual(trunk.trunk_native_vlan, 10)
        self.assertEqual(parsed.warnings, [])

    def test_add_remove_without_explicit_list_warns(self):
        parsed = parse_cisco_config(
            "vlan 10,20\ninterface Gi1/0/1\n switchport trunk allowed vlan add 20\n"
            " switchport trunk allowed vlan remove 10\n"
        )
        iface = parsed.interfaces["GigabitEthernet1/0/1"]
        self.assertEqual(iface.trunk_allowed_vlans, {20})
        self.assertEqual(
            parsed.warnings,
            [
                "Line 3: trunk allowed vlan add applied on GigabitEthernet1/0/1 without prior explicit allowed list",
                "Line 4: trunk allowed vlan remove applied on GigabitEthernet1/0/1 without prior explicit allowed list",
            ],
        )

    def test_trunk_allowed_keywords_are_unsupported(self):
        parsed = parse_cisco_config(
            "interface Gi1/0/1\n switchport trunk allowed vlan all\n"
            "interface Gi1/0/2\n switchport trunk allowed vlan except 10\n"
        )
        self.assertEqual([t for _, t, _ in unsupported(parsed)],
                         ["switchport trunk allowed vlan all", "switchport trunk allowed vlan except 10"])

    def test_unknown_interface_lines_are_kept(self):
        parsed = parse_cisco_config("interface Gi1/0/1\n switchport mode access\n spanning-tree portfast\n")
        self.assertEqual(unsupported(parsed), [(3, "spanning-tree portfast", "unsupported or unhandled interface command")])

    def test_routed_ports(self):
        parsed = parse_cisco_config(
            "interface Gi1/0/1\n no switchport\n ip address 10.0.0.1 255.255.255.252\n"
            "interface Gi1/0/2\n ip address dhcp\n"
            "interface Gi1/0/3\n ip address 10.0.1.1 255.255.255.0 secondary\n"
        )
        gi1 = parsed.interfaces["GigabitEthernet1/0/1"]
        self.assertEqual((gi1.mode, gi1.ip_address), ("routed", "10.0.0.1/30"))
        self.assertEqual(parsed.interfaces["GigabitEthernet1/0/2"].mode, "routed")
        gi3 = parsed.interfaces["GigabitEthernet1/0/3"]
        self.assertEqual((gi3.mode, gi3.ip_address), ("routed", None))
        self.assertEqual([r for _, _, r in unsupported(parsed)], ["secondary IP address is not supported"])

    def test_svi_address(self):
        parsed = parse_cisco_config("interface Vlan10\n ip address 10.10.10.1 255.255.255.0\n")
        svi = parsed.interfaces["Vlan10"]
        self.assertEqual((svi.interface_type, svi.mode, svi.ip_address), ("Vlan", "routed", "10.10.10.1/24"))

    def test_access_group(self):
        parsed = parse_cisco_config(
            "interface Gi1/0/1\n ip access-group A in\ninterface Gi1/0/2\n ip access-group B out\n"
        )
        self.assertEqual(parsed.interfaces["GigabitEthernet1/0/1"].access_group_in, "A")
        self.assertIsNone(parsed.interfaces["GigabitEthernet1/0/2"].access_group_in)
        self.assertEqual([r for _, _, r in unsupported(parsed)], ["egress ACL (ip access-group out) is not supported"])


class PortChannelTests(unittest.TestCase):
    def test_members_link_in_natural_order(self):
        parsed = parse_cisco_config(
            "interface Gi1/0/10\n channel-group 1 mode active\n"
            "interface Gi1/0/9\n channel-group 1 mode active\n"
            "interface Port-channel1\n description LAG\n switchport mode trunk\n switchport trunk allowed vlan 10,20\n"
        )
        po = parsed.port_channels[1]
        self.assertIsInstance(po, PortChannelInterface)
        self.assertEqual(po.members, ["GigabitEthernet1/0/9", "GigabitEthernet1/0/10"])
        self.assertEqual((po.mode, po.trunk_allowed_vlans, po.description), ("trunk", {10, 20}, "LAG"))
        member = parsed.interfaces["GigabitEthernet1/0/10"]
        self.assertEqual((member.channel_group, member.channel_mode), (1, "active"))

    def test_bundle_created_without_port_channel_block(self):
        parsed = parse_cisco_config("interface Gi1/0/1\n channel-group 7 mode on\n")
        self.assertEqual(parsed.port_channels[7].members, ["GigabitEthernet1/0/1"])
        self.assertIsNone(parsed.port_channels[7].mode)

    def test_channel_group_on_bundle_is_unsupported(self):
        parsed = parse_cisco_config("interface Port-channel1\n channel-group 2 mode active\n")
        self.assertEqual([r for _, _, r in unsupported(parsed)], ["channel-group is not valid on a logical interface"])


class AclTests(unittest.TestCase):
    def test_named_extended_acl(self):
        parsed = parse_cisco_config(
            "ip access-list extended SERVERS-IN\n remark Allow web\n 10 permit tcp any host 10.2.0.5 eq 22\n"
            " 20 permit tcp 10.1.0.0 0.0.0.255 host 10.2.0.5 range 8000 8080\n 30 permit 47 any any\n"
            " 40 deny ip any any\n"
        )
        rules = parsed.acls["SERVERS-IN"]
        self.assertEqual([r.action for r in rules], ["remark", "permit", "permit", "permit", "deny"])
        self.assertEqual(rules[0].remark, "Allow web")
        r10 = rules[1]
        self.assertEqual((r10.protocol, r10.source, r10.destination, r10.destination_port), ("tcp", None, "10.2.0.5/32", (22, 22)))
        r20 = rules[2]
        self.assertEqual((r20.source, r20.destination_port), ("10.1.0.0/24", (8000, 8080)))
        self.assertEqual(rules[3].protocol, "47")
        self.assertEqual((rules[4].protocol, rules[4].source, rules[4].destination), (None, None, None))
        self.assertEqual(parsed.unsupported_lines, [])

    def test_numbered_acls(self):
        parsed = parse_cisco_config(
            "access-list 10 permit 10.99.0.1\naccess-list 10 permit 10.1.0.0 0.0.0.255\naccess-list 10 deny any\n"
            "access-list 100 permit udp any host 10.2.0.10 range 5000 5100\naccess-list 100 permit icmp any any\n"
            "access-list 500 permit ip any any\n"
        )
        std = parsed.acls["10"]
        self.assertEqual([r.source for r in std], ["10.99.0.1/32", "10.1.0.0/24", None])
        ext = parsed.acls["100"]
        self.assertEqual((ext[0].protocol, ext[0].destination, ext[0].destination_port), ("udp", "10.2.0.10/32", (5000, 5100)))
        self.assertEqual(ext[1].protocol, "icmp")
        self.assertNotIn("500", parsed.acls)
        self.assertEqual([u.line_number for u in parsed.unsupported_lines], [6])

    def test_source_port_on_extended_acl(self):
        parsed = parse_cisco_config("ip access-list extended X\n permit tcp any eq 53 any\n")
        self.assertEqual(parsed.acls["X"][0].source_port, (53, 53))

    def test_unsupported_aces_are_reported_not_dropped(self):
        parsed = parse_cisco_config(
            "ip access-list extended W\n permit tcp any any established\n deny ip any any log\n"
            " permit tcp any any eq www\n permit tcp any gt 1023 any\n permit ip 10.1.0.0 0.0.255.0 any\n"
            " permit ospf any any\n evaluate FOO\n"
        )
        self.assertEqual(parsed.acls["W"], [])
        self.assertEqual(
            [r for _, _, r in unsupported(parsed)],
            [
                "unsupported token 'established'",
                "unsupported token 'log'",
                "only numeric ports are supported",
                "port operator 'gt' not supported",
                "non-contiguous wildcard mask",
                "protocol 'ospf' not supported",
                "unsupported ACL command 'evaluate'",
            ],
        )
        self.assertIn("ACL W: 7 rule(s) could not be translated; the generated ACL is incomplete -- review before applying", parsed.warnings)


class MonitorSessionTests(unittest.TestCase):
    def test_local_span(self):
        parsed = parse_cisco_config(
            "monitor session 1 source interface Gi1/0/1 - 2 , Gi1/0/5 rx\n"
            "monitor session 1 source interface Po1\n"
            "monitor session 1 source vlan 20 , 30 - 31 tx\n"
            "monitor session 1 destination interface Gi1/0/47 encapsulation replicate\n"
        )
        sess = parsed.monitor_sessions[1]
        self.assertEqual(
            sess.source_ports,
            [("GigabitEthernet1/0/1", "rx"), ("GigabitEthernet1/0/2", "rx"), ("GigabitEthernet1/0/5", "rx"), ("Port-channel1", "both")],
        )
        self.assertEqual(sess.source_vlans, [(20, "tx"), (30, "tx"), (31, "tx")])
        self.assertEqual(sess.destination_ports, ["GigabitEthernet1/0/47"])
        self.assertTrue(sess.encapsulation_replicate)
        self.assertIn("GigabitEthernet1/0/47", parsed.interfaces)  # registered for the port map
        self.assertEqual(parsed.unsupported_lines, [])

    def test_filter_acl(self):
        parsed = parse_cisco_config(
            "monitor session 2 filter ip access-group PING\nmonitor session 2 filter ip access-group OTHER\n"
        )
        self.assertEqual(parsed.monitor_sessions[2].filter_acl, "PING")
        self.assertEqual([r for _, _, r in unsupported(parsed)], ["multiple SPAN filter ACLs on one session"])

    def test_rspan_erspan_and_other_forms_are_reported(self):
        parsed = parse_cisco_config(
            "monitor session 3 source remote vlan 900\nmonitor session 4 type erspan-source\n"
            "monitor session 5 filter vlan 10\nmonitor session 6 destination remote vlan 900\n"
            "monitor session 7 source interface Vlan10\n"
        )
        self.assertEqual(
            [r for _, _, r in unsupported(parsed)],
            [
                "RSPAN (remote vlan) is not supported",
                "only local SPAN is supported (no ERSPAN/capture types)",
                "only 'filter ip access-group' is supported",
                "RSPAN (remote vlan) is not supported",
                "unrecognized interface 'Vlan10'",
            ],
        )
        self.assertEqual(parsed.monitor_sessions, {})


if __name__ == "__main__":
    unittest.main()
