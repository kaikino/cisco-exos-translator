# Assembles the findings document for one translated file.
#
# The parse and generate stages have already recorded their findings on the
# collector; this module adds the two things only a whole-run view can produce:
# the classification of every untranslated source line, and the aggregate
# "what did translate" findings that make the summary counts meaningful.

from __future__ import annotations

from typing import Optional

from .feature_support import classify, safe_label
from .findings import (
    FindingsCollector,
    MigrationFindings,
    Severity,
    SourceRef,
    TranslationStatus,
    sha256_text,
)
from .models import ParsedConfig, PhysicalInterface

# Cap on how many object names a single aggregate finding carries, so a
# 400-port config does not produce a megabyte of JSON.
MAX_LISTED = 100

# Cap on distinct untranslated commands reported individually; matches the
# spirit of the .xsf banner, which also ranks and truncates.
MAX_UNSUPPORTED_COMMANDS = 60


def _listed(names) -> dict:
    names = list(names)
    out = {"count": len(names), "objects": names[:MAX_LISTED]}
    if len(names) > MAX_LISTED:
        out["objects_truncated"] = True
    return out


def _is_svi(iface) -> bool:
    return isinstance(iface, PhysicalInterface) and iface.interface_type.lower() == "vlan"


def add_scope_findings(config: ParsedConfig, collector: FindingsCollector) -> None:
    # One finding per distinct untranslated command, classified by the feature
    # support registry. Deduping by command text (not by line) keeps the output
    # proportional to the config's variety rather than its length.
    items = list(config.unsupported_lines)
    for iface in config.interfaces.values():
        items.extend(iface.unsupported_lines)
    if not items:
        return

    groups: dict = {}
    for item in items:
        rule = classify(item.text)
        label = safe_label(item.text, rule)
        key = (rule.feature, label)
        entry = groups.setdefault(
            key, {"rule": rule, "lines": [], "count": 0, "contexts": set(), "reasons": set()}
        )
        entry["count"] += 1
        entry["lines"].append(item.line_number)
        if item.context:
            entry["contexts"].add(item.context)
        if item.reason:
            entry["reasons"].add(item.reason)

    # Deterministic order: most frequent first, then feature, then command.
    ranked = sorted(
        groups.items(), key=lambda kv: (-kv[1]["count"], kv[0][0], kv[0][1])
    )
    for (feature, label), entry in ranked[:MAX_UNSUPPORTED_COMMANDS]:
        rule = entry["rule"]
        lines = sorted(set(entry["lines"]))
        parser_reasons = sorted(entry["reasons"])
        collector.record(
            code=f"unsupported.{feature}",
            category="scope",
            feature=feature,
            status=rule.status,
            severity=Severity.WARNING,
            message=(
                f"'{label}' ({entry['count']} line(s)) has no EXOS output: {rule.reason}"
            ),
            object_name=label,
            reason=rule.reason,
            source=SourceRef(
                lines=lines[:20],
                block=sorted(entry["contexts"])[0] if entry["contexts"] else None,
            ),
            action=rule.action,
            metadata={
                "occurrences": entry["count"],
                "parser_reasons": parser_reasons,
            },
        )

    if len(ranked) > MAX_UNSUPPORTED_COMMANDS:
        rest = ranked[MAX_UNSUPPORTED_COMMANDS:]
        collector.record(
            code="unsupported.truncated",
            category="scope",
            feature="general",
            status=TranslationStatus.UNSUPPORTED,
            severity=Severity.WARNING,
            message=(
                f"{len(rest)} further distinct command(s) "
                f"({sum(e['count'] for _, e in rest)} line(s)) were not translated and "
                f"are not listed individually"
            ),
            reason="the per-command list is capped to keep the findings file usable",
            action="see the 'Not translated' section of the .xsf WARNINGS header",
        )


def add_translated_findings(
    config: ParsedConfig,
    mapping: dict,
    pol_files: dict,
    collector: FindingsCollector,
) -> None:
    # Aggregate "this worked" findings -- one per feature area, not one per
    # command, so the counts are useful without bloating the file.
    problem_objects = {
        f.object_name
        for f in collector.findings
        if f.status != TranslationStatus.TRANSLATED and f.object_name
    }

    def record(code, feature, message, **kwargs):
        collector.record(
            code=code,
            category="translation",
            feature=feature,
            status=TranslationStatus.TRANSLATED,
            severity=Severity.INFO,
            message=message,
            **kwargs,
        )

    if config.hostname:
        record(
            "translated.hostname",
            "system",
            f"hostname '{config.hostname}' translated to the EXOS SNMP sysName",
            object_name=config.hostname,
        )

    vlans = {
        int(tag): name
        for tag, name in (mapping.get("vlans") or {}).items()
        if str(tag).isdigit() and int(tag) != 1
    }
    if vlans:
        record(
            "translated.vlans",
            "vlan",
            f"{len(vlans)} VLAN(s) created in the EXOS output",
            metadata={
                "vlans": _listed([f"{tag} -> {vlans[tag]}" for tag in sorted(vlans)])
            },
        )

    ports = {
        name: exos
        for name, exos in (mapping.get("ports") or {}).items()
        if name not in problem_objects
        and getattr(config.interfaces.get(name), "mode", None) != "routed"
    }
    if ports:
        record(
            "translated.ports",
            "port",
            f"{len(ports)} port(s) mapped to EXOS ports with their VLAN membership",
            metadata={
                "ports": _listed([f"{n} -> {ports[n]}" for n in sorted(ports)])
            },
        )

    lags = mapping.get("lags") or {}
    if lags:
        record(
            "translated.lags",
            "lag",
            f"{len(lags)} link aggregation group(s) translated to EXOS sharing",
            metadata={
                "lags": _listed(
                    [
                        f"{name} -> master {entry.get('master')} ({entry.get('mode')})"
                        for name, entry in sorted(lags.items())
                    ]
                )
            },
        )

    if pol_files:
        record(
            "translated.acls",
            "acl",
            f"{len(pol_files)} ACL(s) written as EXOS policy files and applied ingress",
            metadata={"policies": _listed(sorted(pol_files))},
            action="upload the .pol files to the switch before loading the .xsf",
        )

    svis = [
        name
        for name, iface in sorted(config.interfaces.items())
        if _is_svi(iface) and iface.ip_address and not iface.shutdown
    ]
    if svis or config.static_routes:
        record(
            "translated.l3",
            "l3",
            f"{len(svis)} SVI address(es) and {len(config.static_routes)} static route(s) "
            f"translated",
            metadata={
                "svis": _listed(svis),
                "static_routes": _listed(
                    [f"{dest} via {gw}" for dest, gw in config.static_routes]
                ),
            },
        )


def build_findings(
    config: ParsedConfig,
    mapping: dict,
    pol_files: dict,
    collector: FindingsCollector,
    *,
    input_name: str,
    input_text: str,
    artifacts: dict,
    ai_report: Optional[dict] = None,
) -> MigrationFindings:
    add_scope_findings(config, collector)
    add_translated_findings(config, mapping, pol_files, collector)

    summary = collector.summary()
    if summary["errors"]:
        status = "completed_with_errors"
    elif summary["warnings"]:
        status = "completed_with_warnings"
    else:
        status = "completed"

    generation = {"status": status, "translation": "deterministic"}
    generation["ai_report"] = ai_report or {"requested": False, "status": "disabled"}

    return MigrationFindings(
        input_name=input_name,
        input_sha256=sha256_text(input_text),
        input_lines=len(input_text.splitlines()),
        hostname=config.hostname,
        findings=collector.findings,
        summary=summary,
        artifacts=artifacts,
        generation=generation,
    )
