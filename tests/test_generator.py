"""Tests for the EXOS generator (ParsedConfig + mapping -> .xsf / .pol)."""

import unittest

from cisco_exos_translator import (
    build_default_mapping,
    generate_exos_config,
    generate_stack_setup,
    parse_cisco_config,
)


def gen(text, mapping=None):
    config = parse_cisco_config(text)
    xsf, warnings, pols = generate_exos_config(config, mapping)
    return xsf, warnings, pols


def script_lines(xsf):
    # the non-comment, non-blank lines of the .xsf, i.e. what the switch executes
    return [l for l in xsf.splitlines() if l and not l.startswith("#")]


class DefaultMappingTests(unittest.TestCase):
    def test_structure_and_natural_order(self):
        m = build_default_mapping(parse_cisco_config(
            "vlan 10\n name USERS\ninterface Gi1/0/10\n channel-group 1 mode active\n"
            "interface Gi1/0/9\n channel-group 1 mode active\ninterface Gi1/0/2\n shutdown\n"
            "monitor session 1 source interface Gi1/0/2\nmonitor session 1 filter ip access-group X\n"
            "monitor session 1 destination interface Gi1/0/3\n"
        ))
        self.assertEqual(set(m), {"_help", "vlans", "ports", "uplinks", "lags", "mirrors", "mirror_egress_mode"})
        self.assertEqual(m["vlans"], {"10": "USERS"})
        self.assertEqual(list(m["ports"]), ["GigabitEthernet1/0/2", "GigabitEthernet1/0/3", "GigabitEthernet1/0/9", "GigabitEthernet1/0/10"])
        self.assertEqual(m["lags"], {"Port-channel1": {"master": "9", "mode": "lacp"}})
        self.assertEqual(m["uplinks"], {"start": None})
        self.assertEqual(m["mirrors"], {"1": "monitor_1"})
        self.assertEqual(m["mirror_egress_mode"], {"1": "acl"})

    def test_svis_are_not_ports(self):
        m = build_default_mapping(parse_cisco_config("interface Vlan10\n ip address 1.1.1.1 255.255.255.0\n"))
        self.assertEqual(m["ports"], {})


class VlanTests(unittest.TestCase):
    def test_names_are_sanitized_and_deduped(self):
        xsf, warnings, _ = gen(
            'vlan 10\n name Guest WiFi!\nvlan 11\n name 3rdFloor\nvlan 40-41\n name GUEST\n'
            "vlan 50\n name " + "A" * 40 + "\n"
        )
        lines = script_lines(xsf)
        self.assertIn('create vlan "Guest_WiFi_" tag 10', lines)
        self.assertIn('create vlan "V_3rdFloor" tag 11', lines)
        self.assertIn('create vlan "GUEST" tag 40', lines)
        self.assertIn('create vlan "GUEST_41" tag 41', lines)
        self.assertIn('create vlan "' + "A" * 32 + '" tag 50', lines)

    def test_vlan_1_is_default_and_never_created(self):
        xsf, warnings, _ = gen("vlan 1\n name MGMT\ninterface Gi1/0/1\n switchport access vlan 1\n")
        lines = script_lines(xsf)
        self.assertFalse(any(l.startswith("create vlan") for l in lines))
        self.assertIn("configure vlan Default add ports 1 untagged", lines)
        self.assertIn("VLAN 1: Cisco name 'MGMT' ignored; EXOS uses the built-in Default VLAN for tag 1", warnings)

    def test_referenced_undefined_vlan_is_auto_created(self):
        xsf, warnings, _ = gen("interface Gi1/0/1\n switchport access vlan 99\n")
        self.assertIn('create vlan "VLAN_99" tag 99', script_lines(xsf))
        self.assertIn("VLAN 99: referenced by a port but never defined; auto-creating it in the EXOS output", warnings)

    def test_mapping_renames_vlan(self):
        config = parse_cisco_config("vlan 10\n name USERS\ninterface Gi1/0/1\n switchport access vlan 10\n")
        mapping = build_default_mapping(config)
        mapping["vlans"]["10"] = "STAFF"
        mapping["vlans"]["abc"] = "X"
        xsf, warnings, _ = generate_exos_config(config, mapping)
        self.assertIn('create vlan "STAFF" tag 10', script_lines(xsf))
        self.assertIn('configure vlan "STAFF" add ports 1 untagged', script_lines(xsf))
        self.assertIn("mapping: VLAN key 'abc' is not a number; ignored", warnings)


class PortTests(unittest.TestCase):
    def test_access_port(self):
        xsf, _, _ = gen(
            "vlan 10\n name USERS\ninterface Gi1/0/1\n description Desk\n switchport mode access\n"
            " switchport access vlan 10\n shutdown\n"
        )
        self.assertEqual(
            script_lines(xsf),
            [
                'create vlan "USERS" tag 10',
                'configure ports 1 description-string "Desk"',
                "configure vlan Default delete ports 1",
                'configure vlan "USERS" add ports 1 untagged',
                "disable ports 1",
            ],
        )

    def test_trunk_with_allowed_list_and_native(self):
        xsf, warnings, _ = gen(
            "vlan 10,20,30\ninterface Gi1/0/1\n switchport mode trunk\n"
            " switchport trunk allowed vlan 10,20\n switchport trunk native vlan 10\n"
        )
        lines = script_lines(xsf)
        self.assertIn("configure vlan Default delete ports 1", lines)
        self.assertIn('configure vlan "VLAN_10" add ports 1 untagged', lines)
        self.assertIn('configure vlan "VLAN_20" add ports 1 tagged', lines)
        self.assertNotIn('configure vlan "VLAN_30" add ports 1 tagged', lines)
        self.assertNotIn('configure vlan "VLAN_10" add ports 1 tagged', lines)

    def test_trunk_without_allowed_list_expands_with_warning(self):
        xsf, warnings, _ = gen("vlan 10,20\ninterface Gi1/0/1\n switchport mode trunk\n")
        lines = script_lines(xsf)
        self.assertIn('configure vlan "VLAN_10" add ports 1 tagged', lines)
        self.assertIn('configure vlan "VLAN_20" add ports 1 tagged', lines)
        self.assertNotIn("configure vlan Default delete ports 1", lines)
        self.assertIn(
            "GigabitEthernet1/0/1: trunk has no allowed-VLAN list (Cisco carries all VLANs); "
            "expanded to all 2 non-Default VLANs defined on this switch",
            warnings,
        )

    def test_hostname_and_empty_port_skipped(self):
        xsf, _, _ = gen("hostname SW1\ninterface Gi1/0/1\n spanning-tree portfast\n")
        self.assertEqual(script_lines(xsf), ['configure snmp sysName "SW1"'])
        self.assertIn("#   - 'spanning-tree portfast' (line 3)", xsf)

    def test_routed_port_is_skipped(self):
        xsf, warnings, _ = gen("interface Gi1/0/1\n no switchport\n ip address 10.0.0.1 255.255.255.0\n")
        self.assertEqual(script_lines(xsf), [])
        self.assertIn("# GigabitEthernet1/0/1: routed interface skipped (L3, out of scope)", xsf)
        self.assertIn("GigabitEthernet1/0/1: routed (L3) interface is out of L2 scope; skipped", warnings)

    def test_ports_in_natural_order(self):
        xsf, _, _ = gen("interface Gi1/0/10\n shutdown\ninterface Gi1/0/2\n shutdown\n")
        self.assertEqual(script_lines(xsf), ["disable ports 2", "disable ports 10"])


class StackAndUplinkTests(unittest.TestCase):
    def test_stacked_slot_port_naming_and_review_comments(self):
        xsf, warnings, _ = gen(
            "switch 1 provision ws-c3850-48p\nswitch 1 priority 15\nswitch 2 priority 10\n"
            "interface Gi2/0/1\n shutdown\n"
        )
        self.assertIn("disable ports 2:1", script_lines(xsf))
        self.assertIn("#   slot 1: was 'ws-c3850-48p'", xsf)
        self.assertIn("# configure stacking slot 2 priority 10", xsf)
        self.assertIn("Stack member 1: Cisco model 'ws-c3850-48p' cannot be mapped to an EXOS slot type; configure the stack slot on the EXOS hardware", warnings)

    def test_uplink_placeholder_and_resolution(self):
        text = "interface Te1/1/2\n shutdown\n"
        xsf, warnings, _ = gen(text)
        self.assertIn("disable ports {uplink-m1-p2}", script_lines(xsf))
        self.assertTrue(any("unresolved uplink placeholder" in w for w in warnings))

        config = parse_cisco_config(text)
        mapping = build_default_mapping(config)
        mapping["uplinks"]["start"] = 49
        xsf, warnings, _ = generate_exos_config(config, mapping)
        self.assertIn("disable ports 50", script_lines(xsf))
        self.assertFalse(any("uplink placeholder" in w for w in warnings))

    def test_invalid_uplink_start_is_ignored(self):
        config = parse_cisco_config("interface Te1/1/1\n shutdown\n")
        mapping = build_default_mapping(config)
        mapping["uplinks"]["start"] = "49"
        _, warnings, _ = generate_exos_config(config, mapping)
        self.assertIn("mapping: uplinks.start '49' is not a positive integer; ignored", warnings)

    def test_port_collision_is_flagged(self):
        config = parse_cisco_config("interface Gi1/0/1\n shutdown\ninterface Gi1/0/2\n shutdown\n")
        mapping = build_default_mapping(config)
        mapping["ports"]["GigabitEthernet1/0/2"] = "1"
        _, warnings, _ = generate_exos_config(config, mapping)
        self.assertTrue(any(w.startswith("EXOS port '1' is the target of multiple Cisco interfaces") for w in warnings))

    def test_unparseable_port_name_is_emitted_verbatim_with_warning(self):
        xsf, warnings, _ = gen("interface GigabitEthernet0/1\n shutdown\n")
        self.assertIn("disable ports GigabitEthernet0/1", script_lines(xsf))
        self.assertIn("GigabitEthernet0/1: could not derive an EXOS port number; emitted the name verbatim -- correct it in the mapping file", warnings)

    def test_stack_setup_runbook(self):
        config = parse_cisco_config("hostname S\nswitch 1 priority 15\nswitch 2 priority 10\n")
        text = generate_stack_setup(config, "s.xsf")
        self.assertIn("# Stack bring-up runbook for S -- run BEFORE loading s.xsf", text)
        self.assertIn("enable stacking-support", text)
        self.assertIn("configure stacking slot 1 priority 15", text)
        self.assertIn("load script s.xsf", text)
        self.assertIsNone(generate_stack_setup(parse_cisco_config("hostname S\n"), "s.xsf"))


class LagTests(unittest.TestCase):
    LAG = (
        "vlan 10,20\ninterface Gi1/0/10\n description member B\n channel-group 1 mode {m}\n"
        "interface Gi1/0/9\n description member A\n shutdown\n channel-group 1 mode {m}\n"
        "interface Port-channel1\n description LAG\n switchport mode trunk\n switchport trunk allowed vlan 10,20\n"
    )

    def test_lacp_bundle_config_on_master(self):
        xsf, _, _ = gen(self.LAG.format(m="active"))
        lines = script_lines(xsf)
        self.assertIn("enable sharing 9 grouping 9,10 algorithm address-based L3_L4 lacp", lines)
        self.assertIn('configure ports 9 description-string "member A"', lines)
        self.assertIn('configure ports 10 description-string "member B"', lines)
        self.assertIn('configure ports 9 description-string "LAG"', lines)
        self.assertIn('configure vlan "VLAN_10" add ports 9 tagged', lines)
        self.assertNotIn('configure vlan "VLAN_10" add ports 10 tagged', lines)
        self.assertIn("disable ports 9", lines)
        self.assertNotIn("disable ports 10", lines)

    def test_static_and_pagp(self):
        xsf, warnings, _ = gen(self.LAG.format(m="on"))
        self.assertIn("enable sharing 9 grouping 9,10 algorithm address-based L3_L4", script_lines(xsf))
        xsf, warnings, _ = gen(self.LAG.format(m="desirable"))
        self.assertIn("enable sharing 9 grouping 9,10 algorithm address-based L3_L4 lacp", script_lines(xsf))
        self.assertIn("Port-channel1: PAgP mode(s) ['desirable'] have no EXOS equivalent; defaulting to LACP (override via the mapping file)", warnings)

    def test_mixed_modes_warn(self):
        _, warnings, _ = gen(
            "interface Gi1/0/1\n channel-group 1 mode on\ninterface Gi1/0/2\n channel-group 1 mode active\n"
        )
        self.assertIn("Port-channel1: mixed static ('on') and LACP member modes; defaulting to LACP", warnings)

    def test_mapping_overrides_master_and_mode(self):
        config = parse_cisco_config(self.LAG.format(m="active"))
        mapping = build_default_mapping(config)
        mapping["lags"]["Port-channel1"] = {"master": "10", "mode": "static"}
        xsf, warnings, _ = generate_exos_config(config, mapping)
        self.assertIn("enable sharing 10 grouping 9,10 algorithm address-based L3_L4", script_lines(xsf))
        mapping["lags"]["Port-channel1"] = {"master": "7", "mode": "bogus"}
        xsf, warnings, _ = generate_exos_config(config, mapping)
        self.assertIn("Port-channel1: mapping master '7' is not one of the member ports; using '9'", warnings)
        self.assertIn("Port-channel1: mapping mode 'bogus' is not 'lacp'/'static'; using the derived default", warnings)

    def test_empty_bundle_skipped(self):
        xsf, warnings, _ = gen("interface Port-channel2\n switchport mode trunk\n")
        self.assertEqual(script_lines(xsf), [])
        self.assertIn("Port-channel2: no member ports; skipped in EXOS output", warnings)


class L3Tests(unittest.TestCase):
    def test_svi_and_routes(self):
        xsf, warnings, _ = gen(
            "ip routing\nvlan 10\n name USERS\ninterface Vlan10\n ip address 10.10.10.1 255.255.255.0\n"
            "interface Vlan20\n ip address 10.20.20.1 255.255.255.0\ninterface Vlan30\n shutdown\n"
            " ip address 10.30.30.1 255.255.255.0\ninterface Vlan40\n no ip address\n"
            "ip route 0.0.0.0 0.0.0.0 10.10.10.254\nip route 192.168.0.0 255.255.0.0 10.10.10.253\n"
        )
        lines = script_lines(xsf)
        self.assertIn('configure vlan "USERS" ipaddress 10.10.10.1/24', lines)
        self.assertIn('enable ipforwarding vlan "USERS"', lines)
        self.assertIn('create vlan "VLAN_20" tag 20', lines)  # SVI-only VLAN auto-created
        self.assertIn('configure vlan "VLAN_20" ipaddress 10.20.20.1/24', lines)
        self.assertNotIn('configure vlan "VLAN_30" ipaddress 10.30.30.1/24', lines)
        self.assertIn("configure iproute add default 10.10.10.254", lines)
        self.assertIn("configure iproute add 192.168.0.0/16 10.10.10.253", lines)
        self.assertIn("Vlan30: SVI is shutdown; L3 config not emitted", warnings)
        self.assertIn("Vlan40: SVI with no IP address; skipped", warnings)


class AclTests(unittest.TestCase):
    ACL = (
        "vlan 10\n name USERS\nip access-list extended SERVERS-IN\n remark Allow ssh\n"
        " 10 permit tcp any host 10.2.0.5 eq 22\n 20 deny ip any any\n"
    )

    def test_policy_file_and_port_apply(self):
        xsf, warnings, pols = gen(self.ACL + "interface Gi1/0/1\n switchport access vlan 10\n ip access-group SERVERS-IN in\n")
        self.assertEqual(list(pols), ["SERVERS_IN"])
        self.assertEqual(
            pols["SERVERS_IN"],
            "# translated from Cisco ACL SERVERS-IN\n"
            "# Allow ssh\n"
            "entry r10 {\n    if {\n        protocol tcp;\n        destination-address 10.2.0.5/32;\n"
            "        destination-port 22;\n    } then {\n        permit;\n    }\n}\n"
            "entry r20 {\n    if {\n        source-address 0.0.0.0/0;\n    } then {\n        deny;\n    }\n}\n"
            "# Cisco implicit deny (IPv4 only)\n"
            "entry implicit_deny {\n    if {\n        source-address 0.0.0.0/0;\n    } then {\n        deny;\n    }\n}\n",
        )
        self.assertIn("configure access-list SERVERS_IN ports 1 ingress", script_lines(xsf))

    def test_router_acl_on_svi_applies_to_vlan_with_warning(self):
        xsf, warnings, pols = gen(self.ACL + "interface Vlan10\n ip address 10.10.10.1 255.255.255.0\n ip access-group SERVERS-IN in\n")
        self.assertIn('configure access-list SERVERS_IN vlan "USERS" ingress', script_lines(xsf))
        self.assertTrue(any("stricter than the Cisco router ACL" in w for w in warnings))

    def test_acl_on_bundle_applies_to_master(self):
        xsf, _, pols = gen(
            self.ACL + "interface Gi1/0/1\n channel-group 1 mode active\ninterface Gi1/0/2\n channel-group 1 mode active\n"
            "interface Port-channel1\n ip access-group SERVERS-IN in\n"
        )
        self.assertIn("configure access-list SERVERS_IN ports 1 ingress", script_lines(xsf))
        self.assertNotIn("configure access-list SERVERS_IN ports 2 ingress", script_lines(xsf))

    def test_unapplied_undefined_and_routed_acls_warn(self):
        xsf, warnings, pols = gen(
            self.ACL + "interface Gi1/0/1\n ip access-group NOPE in\ninterface Gi1/0/2\n no switchport\n ip access-group SERVERS-IN in\n"
        )
        self.assertEqual(pols, {})
        self.assertIn("ACL NOPE: referenced by 'ip access-group' but never defined; not applied", warnings)
        self.assertIn("ACL SERVERS-IN: defined but not applied to any translated target; skipped", warnings)
        self.assertIn("GigabitEthernet1/0/2: ACL SERVERS-IN on a routed physical port; not translated (routed ports are out of scope)", warnings)

    def test_standard_acl_and_port_ranges(self):
        _, _, pols = gen(
            "access-list 10 permit 10.1.0.0 0.0.0.255\naccess-list 100 permit udp any eq 53 10.2.0.0 0.0.255.255 range 1 1024\n"
            "interface Gi1/0/1\n ip access-group 10 in\ninterface Gi1/0/2\n ip access-group 100 in\n"
        )
        self.assertIn("        source-address 10.1.0.0/24;\n", pols["V_10"])
        self.assertIn("        protocol udp;\n        source-port 53;\n        destination-address 10.2.0.0/16;\n        destination-port 1 - 1024;\n", pols["V_100"])


class MirrorTests(unittest.TestCase):
    SPAN = (
        "vlan 20\nmonitor session 1 source interface Gi1/0/1 - 2 rx\nmonitor session 1 source interface Gi1/0/3 tx\n"
        "monitor session 1 source vlan 20\n"
        "monitor session 1 destination interface Gi1/0/12 encapsulation replicate\n"
        "interface Gi1/0/12\n description Probe\n switchport access vlan 20\n"
    )

    def test_mirror_instance_lines_in_order(self):
        xsf, warnings, pols = gen(self.SPAN)
        lines = script_lines(xsf)
        start = lines.index("configure vlan Default delete ports 12")
        self.assertEqual(
            lines[start:],
            [
                "configure vlan Default delete ports 12",
                "create mirror monitor_1",
                "configure mirror monitor_1 to port 12",
                "configure mirror monitor_1 add port 1 ingress",
                "configure mirror monitor_1 add port 2 ingress",
                "configure mirror monitor_1 add port 3 egress",
                'configure mirror monitor_1 add vlan "VLAN_20"',
                "enable mirror monitor_1",
            ],
        )
        # the destination port keeps only its description
        self.assertIn('configure ports 12 description-string "Probe"', lines)
        self.assertNotIn('configure vlan "VLAN_20" add ports 12 untagged', lines)
        self.assertTrue(any(w.startswith("GigabitEthernet1/0/12: SPAN destination port") for w in warnings))
        self.assertIn("monitor session 1: EXOS VLAN mirroring is ingress-only; source VLAN 20 (both) mirrors ingress traffic only", warnings)
        self.assertFalse(any("preserves VLAN tags" in w for w in warnings))
        self.assertEqual(pols, {})

    def test_default_mirror_reuse_and_rename(self):
        config = parse_cisco_config(self.SPAN)
        mapping = build_default_mapping(config)
        mapping["mirrors"]["1"] = "DefaultMirror"
        xsf, _, _ = generate_exos_config(config, mapping)
        lines = script_lines(xsf)
        self.assertNotIn("create mirror DefaultMirror", lines)
        self.assertIn("enable mirror DefaultMirror", lines)
        mapping["mirrors"]["1"] = "probe one"
        xsf, warnings, _ = generate_exos_config(config, mapping)
        self.assertIn("create mirror probe_one", script_lines(xsf))
        self.assertIn("monitor session 1: mirror name 'probe one' sanitized to 'probe_one' (EXOS object naming rules)", warnings)

    def test_encapsulation_warning_and_multiple_sessions(self):
        xsf, warnings, _ = gen(
            "monitor session 1 source interface Gi1/0/1\nmonitor session 1 destination interface Gi1/0/11\n"
            "monitor session 2 source interface Gi1/0/2\nmonitor session 2 destination interface Gi1/0/12\n"
        )
        lines = script_lines(xsf)
        self.assertIn("configure mirror monitor_1 add port 1 ingress-and-egress", lines)
        self.assertIn("create mirror monitor_2", lines)
        self.assertTrue(any(w.startswith("monitor session 1: EXOS mirroring preserves VLAN tags") for w in warnings))
        self.assertTrue(any(w.startswith("2 mirror instances translated") for w in warnings))

    def test_port_channel_source_expands_and_lag_destination_skips(self):
        xsf, warnings, _ = gen(
            "interface Gi1/0/1\n channel-group 1 mode active\ninterface Gi1/0/2\n channel-group 1 mode active\n"
            "monitor session 1 source interface Po1 rx\nmonitor session 1 destination interface Gi1/0/12\n"
            "monitor session 2 source interface Gi1/0/5\nmonitor session 2 destination interface Gi1/0/2\n"
            "monitor session 3 source interface Gi1/0/5\nmonitor session 3 destination interface Po1\n"
        )
        lines = script_lines(xsf)
        self.assertIn("configure mirror monitor_1 add port 1 ingress", lines)
        self.assertIn("configure mirror monitor_1 add port 2 ingress", lines)
        self.assertNotIn("create mirror monitor_2", lines)
        self.assertNotIn("create mirror monitor_3", lines)
        self.assertIn("monitor session 1: source Port-channel1 expanded to its member ports (1, 2)", warnings)
        self.assertTrue(any(w.startswith("monitor session 2: destination GigabitEthernet1/0/2 is a LAG member") for w in warnings))
        self.assertTrue(any(w.startswith("monitor session 3: destination Port-channel1 is not a physical port") for w in warnings))

    def test_multiple_destinations_use_port_list(self):
        xsf, _, _ = gen("monitor session 1 source interface Gi1/0/1\nmonitor session 1 destination interface Gi1/0/11 - 12\n")
        self.assertIn("configure mirror monitor_1 to port-list 11,12", script_lines(xsf))


class FilteredMirrorTests(unittest.TestCase):
    FSPAN = (
        "ip access-list extended MIRROR_PING\n remark only ICMP for the DNS server\n"
        " permit icmp host 100.86.255.2 any\n permit icmp any host 100.86.255.2\n deny ip 10.9.0.0 0.0.255.255 any\n"
        "monitor session 1 source interface Gi1/0/1 - 2\nmonitor session 1 filter ip access-group MIRROR_PING\n"
        "monitor session 1 destination interface Gi1/0/12\n"
    )

    def test_filter_policy_bound_before_enable(self):
        xsf, warnings, pols = gen(self.FSPAN)
        lines = script_lines(xsf)
        self.assertEqual(
            lines[lines.index("create mirror monitor_1"):],
            [
                "create mirror monitor_1",
                "configure mirror monitor_1 to port 12",
                "configure access-list monitor_1_filter ports 1 ingress",
                "configure access-list monitor_1_filter ports 1 egress",
                "configure access-list monitor_1_filter ports 2 ingress",
                "configure access-list monitor_1_filter ports 2 egress",
                "enable mirror monitor_1",
            ],
        )
        pol = pols["monitor_1_filter"]
        self.assertIn("# only ICMP for the DNS server", pol)
        self.assertIn("        protocol icmp;\n        source-address 100.86.255.2/32;\n    } then {\n        permit;\n        mirror monitor_1;\n", pol)
        # a Cisco deny only means "don't mirror": it must not block traffic
        self.assertIn("entry r30 {\n    if {\n        source-address 10.9.0.0/16;\n    } then {\n        permit;\n    }\n}", pol)
        self.assertNotIn("deny", pol.replace("# selects traffic", ""))
        self.assertTrue(any("egress ACL mirror action is platform-dependent" in w for w in warnings))
        # a filter ACL is consumed by the mirror, never reported as unapplied
        self.assertFalse(any(w.startswith("ACL MIRROR_PING: defined but not applied") for w in warnings))

    def test_whole_port_egress_mode(self):
        config = parse_cisco_config(self.FSPAN)
        mapping = build_default_mapping(config)
        mapping["mirror_egress_mode"]["1"] = "whole-port"
        xsf, warnings, pols = generate_exos_config(config, mapping)
        lines = script_lines(xsf)
        self.assertIn("configure mirror monitor_1 add port 1 egress", lines)
        self.assertIn("configure access-list monitor_1_filter ports 1 ingress", lines)
        self.assertNotIn("configure access-list monitor_1_filter ports 1 egress", lines)
        self.assertLess(lines.index("configure mirror monitor_1 add port 1 egress"), lines.index("enable mirror monitor_1"))
        self.assertTrue(any("mirror_egress_mode 'whole-port'" in w for w in warnings))
        self.assertIn("(egress: whole-port)", xsf)

    def test_invalid_egress_mode_falls_back(self):
        config = parse_cisco_config(self.FSPAN)
        mapping = build_default_mapping(config)
        mapping["mirror_egress_mode"]["1"] = "nope"
        _, warnings, _ = generate_exos_config(config, mapping)
        self.assertIn("monitor session 1: mapping mirror_egress_mode 'nope' is not 'acl'/'whole-port'; using 'acl'", warnings)

    def test_vlan_sources_are_unfiltered(self):
        xsf, warnings, _ = gen(self.FSPAN + "monitor session 1 source vlan 20\n")
        self.assertIn('configure mirror monitor_1 add vlan "VLAN_20"', script_lines(xsf))
        self.assertIn("monitor session 1: the SPAN filter does not apply to VLAN sources; they are mirrored unfiltered", warnings)

    def test_filter_acl_without_rules_mirrors_whole_port(self):
        xsf, warnings, pols = gen(
            "ip access-list extended EMPTY\n remark nothing here\n"
            "monitor session 1 source interface Gi1/0/1\nmonitor session 1 filter ip access-group EMPTY\n"
            "monitor session 1 destination interface Gi1/0/12\n"
        )
        self.assertIn("configure mirror monitor_1 add port 1 ingress-and-egress", script_lines(xsf))
        self.assertEqual(pols, {})
        self.assertIn("monitor session 1: filter ACL EMPTY has no translatable rules; sources mirrored unfiltered", warnings)


class BannerTests(unittest.TestCase):
    def test_warning_groups_and_reference(self):
        xsf, _, _ = gen(
            "vlan 10\n name USERS\ninterface Gi1/0/1\n switchport access vlan 99\n spanning-tree portfast\n"
            "interface range Gi1/0/2-3\n spanning-tree portfast\n"
        )
        head = xsf.split("# System")[0]
        self.assertIn("# WARNINGS — review before deploying", head)
        self.assertIn("# Input (problems in the Cisco config):", head)
        self.assertIn("#   - Interface GigabitEthernet1/0/1: access VLAN 99 is referenced but not defined", head)
        self.assertIn("# Not translated (3 line(s) outside this tool's L2 scope; review for anything you must port manually):", head)
        self.assertIn("#   - 'spanning-tree portfast' x3 (lines 5, 7)", head)
        self.assertIn("# Translation (decisions made converting to EXOS):", head)
        self.assertIn("# Translation reference (from the mapping file; edit it and re-run to change)", head)
        self.assertIn("#   10 -> USERS", head)
        self.assertIn("#   99 -> VLAN_99  (auto-named)", head)
        self.assertIn("#   GigabitEthernet1/0/1 -> 1", head)

    def test_clean_config_has_no_banner(self):
        xsf, warnings, _ = gen("hostname X\nvlan 10\n name A\n")
        self.assertFalse(xsf.startswith("# WARNINGS"))
        self.assertEqual(warnings, [])

    def test_unsupported_summary_truncates(self):
        text = "".join(f"unknown-cmd-{i}\n" for i in range(45))
        xsf, _, _ = gen(text)
        self.assertIn("#   ... and 5 more distinct commands (5 line(s)) -- see the source config", xsf)


if __name__ == "__main__":
    unittest.main()
