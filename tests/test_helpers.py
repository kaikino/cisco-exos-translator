"""Unit tests for VLAN list, interface name, and address helpers."""

import unittest

from cisco_exos_translator.helpers import (
    canonicalize_interface_name,
    expand_interface_range,
    interface_sort_key,
    netmask_to_cidr,
    parse_interface_identity,
    parse_port_channel_id,
    parse_vlan_list,
    wildcard_to_cidr,
)


class ParseVlanListTests(unittest.TestCase):
    def test_mixed_list_and_ranges(self):
        self.assertEqual(parse_vlan_list("10,20,30-32"), {10, 20, 30, 31, 32})
        self.assertEqual(parse_vlan_list("1-3,7"), {1, 2, 3, 7})

    def test_whitespace_and_trailing_comma(self):
        self.assertEqual(parse_vlan_list(" 10 , 20, "), {10, 20})

    def test_invalid_inputs_raise(self):
        for bad in ("", "abc", "all", "30-20", "0", "4095", "1-5000"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    parse_vlan_list(bad)


class CanonicalizeInterfaceNameTests(unittest.TestCase):
    def test_abbreviations(self):
        cases = {
            "Gi1/0/1": "GigabitEthernet1/0/1",
            "Te1/1/1": "TenGigabitEthernet1/1/1",
            "Twe1/0/1": "TwentyFiveGigE1/0/1",
            "Tw1/0/1": "TwoGigabitEthernet1/0/1",
            "Fi1/0/1": "FiveGigabitEthernet1/0/1",
            "Fo1/1/1": "FortyGigabitEthernet1/1/1",
            "Hu1/0/1": "HundredGigE1/0/1",
            "Fa0/1": "FastEthernet0/1",
            "Po1": "Port-channel1",
            "po 1": "Port-channel1",
            "port-channel 1": "Port-channel1",
            "Port-channel1": "Port-channel1",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(canonicalize_interface_name(raw), expected)

    def test_full_names_are_case_normalized(self):
        self.assertEqual(canonicalize_interface_name("gigabitethernet1/0/1"), "GigabitEthernet1/0/1")

    def test_interface_keyword_is_stripped(self):
        self.assertEqual(canonicalize_interface_name("interface Gi1/0/1"), "GigabitEthernet1/0/1")

    def test_unknown_types_pass_through(self):
        self.assertEqual(canonicalize_interface_name("Vlan10"), "Vlan10")
        self.assertEqual(canonicalize_interface_name("Loopback0"), "Loopback0")


class ParseInterfaceIdentityTests(unittest.TestCase):
    def test_stacked_numbering(self):
        self.assertEqual(parse_interface_identity("Gi1/0/1"), ("GigabitEthernet", 1, 0, 1))
        self.assertEqual(parse_interface_identity("Te2/1/4"), ("TenGigabitEthernet", 2, 1, 4))

    def test_two_part_names_have_no_numbering(self):
        # only <member>/<module>/<port> is decomposed; other shapes keep the type only
        self.assertEqual(parse_interface_identity("GigabitEthernet0/1"), ("GigabitEthernet", None, None, None))

    def test_logical_interfaces(self):
        self.assertEqual(parse_interface_identity("Po1"), ("Port-channel", None, None, None))
        self.assertEqual(parse_interface_identity("Vlan10"), ("Vlan", None, None, None))

    def test_port_channel_id(self):
        self.assertEqual(parse_port_channel_id("Po12"), 12)
        self.assertEqual(parse_port_channel_id("interface port-channel 3"), 3)
        self.assertIsNone(parse_port_channel_id("Gi1/0/1"))


class ExpandInterfaceRangeTests(unittest.TestCase):
    def test_simple_range(self):
        self.assertEqual(
            expand_interface_range("GigabitEthernet1/0/1-3"),
            ["GigabitEthernet1/0/1", "GigabitEthernet1/0/2", "GigabitEthernet1/0/3"],
        )

    def test_abbreviated_and_mixed(self):
        self.assertEqual(
            expand_interface_range("Gi1/0/1-2,Gi1/0/5"),
            ["GigabitEthernet1/0/1", "GigabitEthernet1/0/2", "GigabitEthernet1/0/5"],
        )

    def test_spaces_after_commas_and_around_dash(self):
        self.assertEqual(
            expand_interface_range("GigabitEthernet1/0/1, GigabitEthernet1/0/3, GigabitEthernet1/0/5 - 6"),
            ["GigabitEthernet1/0/1", "GigabitEthernet1/0/3", "GigabitEthernet1/0/5", "GigabitEthernet1/0/6"],
        )

    def test_natural_sort_and_dedup(self):
        self.assertEqual(
            expand_interface_range("Gi1/0/10,Gi1/0/2,Gi1/0/2"),
            ["GigabitEthernet1/0/2", "GigabitEthernet1/0/10"],
        )

    def test_invalid_inputs_raise(self):
        for bad in ("", "Gi1/0/5-2"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    expand_interface_range(bad)


class InterfaceSortKeyTests(unittest.TestCase):
    def test_natural_order_across_types_members_and_bundles(self):
        names = ["Po10", "Te1/1/1", "Gi2/0/1", "Gi1/0/10", "Vlan10", "Gi1/0/2", "Po2"]
        self.assertEqual(
            sorted(names, key=interface_sort_key),
            ["Gi1/0/2", "Gi1/0/10", "Gi2/0/1", "Te1/1/1", "Po2", "Po10", "Vlan10"],
        )


class AddressHelperTests(unittest.TestCase):
    def test_wildcard_to_cidr(self):
        self.assertEqual(wildcard_to_cidr("10.1.0.0", "0.0.0.255"), "10.1.0.0/24")
        self.assertEqual(wildcard_to_cidr("10.2.0.5", "0.0.0.0"), "10.2.0.5/32")
        self.assertEqual(wildcard_to_cidr("0.0.0.0", "255.255.255.255"), "0.0.0.0/0")
        self.assertIsNone(wildcard_to_cidr("10.1.0.0", "0.0.255.0"))  # non-contiguous
        self.assertIsNone(wildcard_to_cidr("10.1.0.0", "0.0.0.300"))  # malformed

    def test_netmask_to_cidr(self):
        self.assertEqual(netmask_to_cidr("10.0.0.1", "255.255.255.252"), "10.0.0.1/30")
        self.assertEqual(netmask_to_cidr("0.0.0.0", "0.0.0.0"), "0.0.0.0/0")
        self.assertIsNone(netmask_to_cidr("10.0.0.1", "255.255.0.255"))
        self.assertIsNone(netmask_to_cidr("10.0.0.1", "255.255.255"))


if __name__ == "__main__":
    unittest.main()
