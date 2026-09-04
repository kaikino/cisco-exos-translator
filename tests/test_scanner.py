"""Unit tests for the scanner (block extraction) pass."""

import unittest

from cisco_exos_translator.scanner import scan_config


class ScanConfigTests(unittest.TestCase):
    def test_blocks_and_line_numbers(self):
        text = "hostname X\n!\n! Last change\nvlan 10\n name A\n!\ninterface Gi1/0/1\n shutdown\n\nend\n"
        blocks = scan_config(text)
        self.assertEqual([b.kind for b in blocks], ["global", "vlan", "interface", "global"])
        self.assertEqual(blocks[0].body[0].text, "hostname X")
        self.assertEqual(blocks[1].header.line_number, 4)
        self.assertEqual(blocks[1].body[0].text, "name A")
        self.assertEqual(blocks[2].header.text, "interface Gi1/0/1")
        self.assertEqual(blocks[2].body[0].line_number, 8)
        self.assertEqual(blocks[3].body[0].text, "end")

    def test_comment_lines_are_dropped(self):
        blocks = scan_config("! comment\n!\nhostname X\n")
        self.assertEqual(len(blocks), 1)
        self.assertEqual([l.text for l in blocks[0].body], ["hostname X"])

    def test_interface_range_kind(self):
        blocks = scan_config("interface range Gi1/0/1-4\n switchport mode access\n")
        self.assertEqual(blocks[0].kind, "interface_range")
        self.assertEqual(blocks[0].context, "interface range Gi1/0/1-4")

    def test_acl_block_kind(self):
        blocks = scan_config("ip access-list extended X\n permit ip any any\n")
        self.assertEqual(blocks[0].kind, "acl")
        self.assertEqual(blocks[0].body[0].text, "permit ip any any")

    def test_vlan_global_commands_are_not_blocks(self):
        blocks = scan_config("vlan internal allocation policy ascending\nvlan 10,20-22\n")
        self.assertEqual([b.kind for b in blocks], ["global", "vlan"])

    def test_indented_line_without_header_is_global(self):
        blocks = scan_config(" stray\nhostname X\n")
        self.assertEqual([b.kind for b in blocks], ["global"])
        self.assertEqual([l.text for l in blocks[0].body], ["stray", "hostname X"])

    def test_empty_input(self):
        self.assertEqual(scan_config(""), [])


if __name__ == "__main__":
    unittest.main()
