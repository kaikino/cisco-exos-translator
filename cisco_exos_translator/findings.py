# Structured migration findings: the machine-readable record of what the
# deterministic translation did, alongside the .xsf it produced.
#
# Nothing here talks to an LLM. The findings document is built by the normal
# pipeline and is complete on its own; the AI summary (ai.py) is a consumer.

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Optional

# Bumped when the JSON shape changes in a way consumers must notice.
SCHEMA_VERSION = "1.0"

# The translator has no packaging metadata, so its version lives here.
TRANSLATOR_VERSION = "0.1.0"


# What happened to a Cisco construct in the EXOS output.
class TranslationStatus:
    TRANSLATED = "translated"
    PARTIALLY_TRANSLATED = "partially_translated"
    UNSUPPORTED = "unsupported"
    ASSUMPTION_MADE = "assumption_made"
    # Used for findings that are not about one construct's translation outcome:
    # input-validity problems, mapping-file problems, artifact failures.
    WARNING = "warning"
    ERROR = "error"

    ALL = (
        TRANSLATED,
        PARTIALLY_TRANSLATED,
        UNSUPPORTED,
        ASSUMPTION_MADE,
        WARNING,
        ERROR,
    )


# How much the finding should worry the operator, independent of status.
# A finding can be status=assumption_made with severity=warning.
class Severity:
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"

    ALL = (INFO, WARNING, ERROR)


# Where in the Cisco config this finding came from. Every field is optional:
# the scanner preserves line numbers and block context, but not every finding
# has one (aggregate findings cover many lines).
@dataclass
class SourceRef:
    line: Optional[int] = None
    lines: Optional[list] = None  # list[int], when a finding covers several
    block: Optional[str] = None  # Cisco block header, e.g. "interface Gi1/0/1"
    cisco_object: Optional[str] = None  # interface / VLAN / ACL / Po identifier

    def to_dict(self) -> dict:
        out = {}
        if self.line is not None:
            out["line"] = self.line
        if self.lines:
            out["lines"] = list(self.lines)
        if self.block:
            out["block"] = self.block
        if self.cisco_object:
            out["cisco_object"] = self.cisco_object
        return out


@dataclass
class Finding:
    # code is the stable rule identifier ("acl.incomplete"); id is the stable
    # per-document identifier the collector assigns ("acl.incomplete#1").
    code: str
    category: str  # "input" | "scope" | "translation" | "mapping" | "artifact"
    feature: str  # "vlan" | "port" | "lag" | "acl" | "l3" | "stack" | "system"
    status: str
    severity: str
    message: str
    object_name: Optional[str] = None
    reason: Optional[str] = None
    source: Optional[SourceRef] = None
    output_ref: Optional[str] = None  # artifact the finding points at
    action: Optional[str] = None  # assumption / manual-review detail
    metadata: dict = field(default_factory=dict)
    id: str = ""

    def to_dict(self) -> dict:
        out = {
            "id": self.id,
            "code": self.code,
            "category": self.category,
            "feature": self.feature,
            "status": self.status,
            "severity": self.severity,
            "message": self.message,
        }
        if self.object_name:
            out["object"] = self.object_name
        if self.reason:
            out["reason"] = self.reason
        if self.source:
            source = self.source.to_dict()
            if source:
                out["source"] = source
        if self.output_ref:
            out["output_ref"] = self.output_ref
        if self.action:
            out["action"] = self.action
        if self.metadata:
            out["metadata"] = _jsonable(self.metadata)
        return out


# Sets and tuples show up in the IR (VLAN id sets, port ranges); normalize them
# so the JSON is stable and comparable across runs.
def _jsonable(value):
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (set, frozenset)):
        return [_jsonable(v) for v in sorted(value)]
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


class FindingsCollector:
    # Ordered, de-duplicating store of findings. Instance state only -- one
    # collector per translated file, passed explicitly into each stage.
    def __init__(self) -> None:
        self._findings: list[Finding] = []
        self._keys: set[tuple] = set()
        self._code_counts: dict[str, int] = {}

    def add(self, finding: Finding) -> Optional[Finding]:
        # Returns the stored finding, or None if it duplicated an earlier one.
        key = (
            finding.code,
            finding.object_name,
            finding.message,
            finding.source.line if finding.source else None,
        )
        if key in self._keys:
            return None
        self._keys.add(key)
        n = self._code_counts.get(finding.code, 0) + 1
        self._code_counts[finding.code] = n
        finding.id = f"{finding.code}#{n}"
        self._findings.append(finding)
        return finding

    def record(self, code: str, category: str, feature: str, status: str,
               severity: str, message: str, **kwargs) -> Optional[Finding]:
        return self.add(
            Finding(
                code=code,
                category=category,
                feature=feature,
                status=status,
                severity=severity,
                message=message,
                **kwargs,
            )
        )

    @property
    def findings(self) -> list:
        return list(self._findings)

    def __len__(self) -> int:
        return len(self._findings)

    def summary(self) -> dict:
        # Status counts and severity counts are deliberately independent, so
        # these six numbers do not sum to len(findings).
        by_status = {s: 0 for s in TranslationStatus.ALL}
        by_severity = {s: 0 for s in Severity.ALL}
        for f in self._findings:
            by_status[f.status] = by_status.get(f.status, 0) + 1
            by_severity[f.severity] = by_severity.get(f.severity, 0) + 1
        return {
            "translated": by_status[TranslationStatus.TRANSLATED],
            "partially_translated": by_status[TranslationStatus.PARTIALLY_TRANSLATED],
            "unsupported": by_status[TranslationStatus.UNSUPPORTED],
            "assumptions": by_status[TranslationStatus.ASSUMPTION_MADE],
            "warnings": by_severity[Severity.WARNING],
            "errors": by_severity[Severity.ERROR],
            "total_findings": len(self._findings),
        }


# Generator/validation stages emit a human-readable warning string (which ends
# up in the .xsf WARNINGS banner) and, when a collector is attached, the
# structured finding for the same event. This keeps one warning pipeline: the
# message text is authored once and used for both.
class WarningSink:
    def __init__(self, collector: Optional[FindingsCollector] = None,
                 category: str = "translation") -> None:
        # messages keeps insertion order *and duplicates*, so banner text is
        # byte-identical to what the generator produced before findings existed.
        self.messages: list[str] = []
        self.collector = collector
        self.category = category

    def warn(self, code: str, message: str, *, feature: str = "general",
             status: str = TranslationStatus.WARNING,
             severity: str = Severity.WARNING,
             category: Optional[str] = None, record: bool = True,
             **kwargs) -> None:
        # record=False emits the banner text only, for events an earlier stage
        # already recorded as a finding with better context.
        self.messages.append(message)
        if record and self.collector is not None:
            self.collector.record(
                code=code,
                category=category or self.category,
                feature=feature,
                status=status,
                severity=severity,
                message=message,
                **kwargs,
            )


@dataclass
class MigrationFindings:
    # The whole findings document for one input file.
    input_name: str
    input_sha256: str
    input_lines: int
    hostname: Optional[str]
    findings: list
    summary: dict
    artifacts: dict
    generation: dict

    def to_dict(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "translator_version": TRANSLATOR_VERSION,
            "input": {
                "filename": self.input_name,
                "sha256": self.input_sha256,
                "line_count": self.input_lines,
                "hostname": self.hostname,
            },
            "summary": self.summary,
            "findings": [f.to_dict() for f in self.findings],
            "artifacts": self.artifacts,
            "generation": self.generation,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=False) + "\n"


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
