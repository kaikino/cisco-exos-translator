# Findings model, collector and JSON document tests.

from __future__ import annotations

import json
import unittest

import main
from cisco_exos_translator.feature_support import classify
from cisco_exos_translator.findings import (
    Finding,
    FindingsCollector,
    Severity,
    SourceRef,
    TranslationStatus,
)
from cisco_exos_translator.findings_builder import build_findings
from cisco_exos_translator.generator import build_default_mapping, generate_exos_config

# A config that exercises every status: a clean access port (translated), an
# uplink-module port (partially translated), an undefined VLAN reference
# (assumption), a spanning-tree line (unsupported), an ACE the parser rejects
# (partially translated ACL) and a mode conflict (warning).
CONFIG = """\
hostname TEST-SW
!
spanning-tree mode rapid-pvst
!
vlan 10
 name USERS
!
interface GigabitEthernet1/0/1
 switchport mode access
 switchport access vlan 10
!
interface GigabitEthernet1/0/2
 switchport mode access
 switchport access vlan 77
 switchport trunk native vlan 10
!
interface TenGigabitEthernet1/1/1
 switchport mode trunk
!
ip access-list extended FILTER
 permit tcp any any eq 22
 permit tcp any gt 1023 any
!
interface Vlan10
 ip address 10.0.10.1 255.255.255.0
 ip access-group FILTER in
!
"""


def translate(text: str = CONFIG):
    collector = FindingsCollector()
    config = main.parse_cisco_config(text, collector)
    mapping = build_default_mapping(config)
    _, _, pol_files = generate_exos_config(config, mapping, [], collector)
    document = build_findings(
        config, mapping, pol_files, collector,
        input_name="test.cfg",
        input_text=text,
        artifacts={"exos_config": "test.xsf", "migration_report": None},
    )
    return document


class SchemaTest(unittest.TestCase):
    def test_document_shape(self):
        doc = translate().to_dict()
        self.assertEqual(doc["schema_version"], "1.0")
        self.assertIn("translator_version", doc)
        self.assertEqual(doc["input"]["filename"], "test.cfg")
        self.assertEqual(len(doc["input"]["sha256"]), 64)
        self.assertEqual(doc["input"]["line_count"], len(CONFIG.splitlines()))
        self.assertEqual(doc["input"]["hostname"], "TEST-SW")
        self.assertEqual(
            set(doc["summary"]),
            {"translated", "partially_translated", "unsupported", "assumptions",
             "warnings", "errors", "total_findings"},
        )
        self.assertIn("exos_config", doc["artifacts"])
        self.assertEqual(doc["generation"]["translation"], "deterministic")

    def test_serializes_to_json(self):
        text = translate().to_json()
        reloaded = json.loads(text)
        self.assertEqual(reloaded["findings"][0]["id"].count("#"), 1)
        for finding in reloaded["findings"]:
            self.assertEqual(
                set(finding) - {"object", "reason", "source", "output_ref",
                                "action", "metadata"},
                {"id", "code", "category", "feature", "status", "severity", "message"},
            )
            self.assertIn(finding["status"], TranslationStatus.ALL)
            self.assertIn(finding["severity"], Severity.ALL)

    def test_no_local_paths_in_document(self):
        doc = translate().to_dict()
        blob = json.dumps(doc)
        self.assertNotIn("/Users/", blob)
        self.assertNotIn("/tmp/", blob)


class ClassificationTest(unittest.TestCase):
    def setUp(self):
        self.doc = translate().to_dict()
        self.by_code = {}
        for finding in self.doc["findings"]:
            self.by_code.setdefault(finding["code"], []).append(finding)

    def statuses(self, code):
        return {f["status"] for f in self.by_code.get(code, [])}

    def test_translated_items(self):
        self.assertEqual(self.statuses("translated.vlans"), {"translated"})
        self.assertEqual(self.statuses("translated.ports"), {"translated"})
        self.assertEqual(self.statuses("translated.acls"), {"translated"})

    def test_unsupported_feature_is_named_and_classified(self):
        stp = self.by_code["unsupported.spanning-tree"][0]
        self.assertEqual(stp["status"], "unsupported")
        self.assertEqual(stp["feature"], "spanning-tree")
        self.assertIn("spanning tree is not translated", stp["reason"])
        self.assertIn("lines", stp["source"])

    def test_partially_translated_uplink_port(self):
        uplink = self.by_code["port.unresolved_uplink"][0]
        self.assertEqual(uplink["status"], "partially_translated")
        self.assertEqual(uplink["severity"], "error")
        self.assertEqual(uplink["object"], "TenGigabitEthernet1/1/1")

    def test_partially_translated_acl(self):
        acl = self.by_code["acl.incomplete"][0]
        self.assertEqual(acl["status"], "partially_translated")
        self.assertEqual(acl["metadata"]["dropped_rules"], 1)
        self.assertIn("gt", self.by_code["unsupported.acl"][0]["metadata"]["parser_reasons"][0])

    def test_assumption_is_distinguishable_and_actionable(self):
        assumption = self.by_code["vlan.auto_created"][0]
        self.assertEqual(assumption["status"], "assumption_made")
        self.assertTrue(assumption["action"])
        self.assertEqual(assumption["metadata"]["vlan"], 77)

    def test_warning_from_input_validation(self):
        conflict = self.by_code["interface.mode_conflict"][0]
        self.assertEqual(conflict["status"], "warning")
        self.assertEqual(conflict["severity"], "warning")
        self.assertEqual(conflict["source"]["cisco_object"], "GigabitEthernet1/0/2")

    def test_error_status_for_undefined_acl(self):
        collector = FindingsCollector()
        config = main.parse_cisco_config(
            "hostname X\n!\nvlan 10\n name A\n!\n"
            "interface GigabitEthernet1/0/1\n switchport access vlan 10\n"
            " ip access-group NOPE in\n!\n",
            collector,
        )
        mapping = build_default_mapping(config)
        generate_exos_config(config, mapping, [], collector)
        codes = {f.code: f for f in collector.findings}
        self.assertEqual(codes["acl.referenced_undefined"].status, TranslationStatus.ERROR)
        self.assertEqual(codes["acl.referenced_undefined"].severity, Severity.ERROR)

    def test_feature_registry_defaults_to_unsupported(self):
        rule = classify("some-command nobody-implemented")
        self.assertEqual(rule.status, TranslationStatus.UNSUPPORTED)
        self.assertEqual(rule.feature, "unclassified")

    def test_credential_lines_are_not_copied_into_findings(self):
        text = "hostname X\nusername admin secret 5 $1$abc$SUPERSECRETHASH\n"
        doc = translate(text).to_dict()
        blob = json.dumps(doc)
        self.assertNotIn("SUPERSECRETHASH", blob)
        creds = [f for f in doc["findings"] if f["feature"] == "credentials"]
        self.assertEqual(creds[0]["object"], "username admin <redacted>")


class CollectorTest(unittest.TestCase):
    def make(self, message="dup", line=1):
        return Finding(
            code="test.code",
            category="translation",
            feature="vlan",
            status=TranslationStatus.WARNING,
            severity=Severity.WARNING,
            message=message,
            object_name="VLAN 10",
            source=SourceRef(line=line),
        )

    def test_duplicates_are_dropped(self):
        collector = FindingsCollector()
        self.assertIsNotNone(collector.add(self.make()))
        self.assertIsNone(collector.add(self.make()))
        self.assertEqual(len(collector), 1)

    def test_distinct_findings_are_kept_and_numbered(self):
        collector = FindingsCollector()
        collector.add(self.make("a", line=1))
        collector.add(self.make("b", line=2))
        self.assertEqual([f.id for f in collector.findings],
                         ["test.code#1", "test.code#2"])

    def test_summary_counts_match_findings(self):
        doc = translate()
        summary = doc.summary
        findings = doc.findings
        for key, status in (
            ("translated", TranslationStatus.TRANSLATED),
            ("partially_translated", TranslationStatus.PARTIALLY_TRANSLATED),
            ("unsupported", TranslationStatus.UNSUPPORTED),
            ("assumptions", TranslationStatus.ASSUMPTION_MADE),
        ):
            self.assertEqual(summary[key], sum(1 for f in findings if f.status == status), key)
        self.assertEqual(summary["warnings"],
                         sum(1 for f in findings if f.severity == Severity.WARNING))
        self.assertEqual(summary["errors"],
                         sum(1 for f in findings if f.severity == Severity.ERROR))
        self.assertEqual(summary["total_findings"], len(findings))

    def test_ordering_is_deterministic(self):
        first = translate().to_json()
        second = translate().to_json()
        self.assertEqual(first, second)
        ids = [f["id"] for f in json.loads(first)["findings"]]
        self.assertEqual(len(ids), len(set(ids)))


if __name__ == "__main__":
    unittest.main()
