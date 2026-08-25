# Validation pass: cross-reference checks that produce warnings.
# Each warning is also recorded as a structured finding when a collector is
# supplied, so the warning text is authored once and used for both outputs.

from __future__ import annotations

import re

from .findings import FindingsCollector, Severity, SourceRef, TranslationStatus, WarningSink
from .models import BaseInterface, ParsedConfig, PortChannelInterface

# unsupported-line contexts/texts that belong to an ACL, for incompleteness checks
RE_ACL_CONTEXT = re.compile(r"^ip\s+access-list\s+\S+\s+(\S+)$", re.IGNORECASE)
RE_ACL_GLOBAL = re.compile(r"^access-list\s+(\d+)\s", re.IGNORECASE)


def validate_parsed_config(
    config: ParsedConfig, collector: FindingsCollector | None = None
) -> list[str]:
    # Validate cross-references; return warning strings.
    sink = WarningSink(collector, category="input")
    defined_vlans = set(config.vlans.keys())

    def check_interface(name: str, iface: BaseInterface, context_label: str) -> None:
        source = SourceRef(
            line=iface.source_lines[0] if iface.source_lines else None,
            cisco_object=name,
        )

        # Routed (L3) interface; addressed SVIs are translated (VLAN ipaddress)
        # so only the rest is out of scope
        if iface.mode == "routed" and not (
            iface.ip_address and iface.interface_type.lower() == "vlan"
        ):
            sink.warn(
                "interface.routed_out_of_scope",
                f"{context_label} {name}: routed interface (no switchport / ip address) "
                f"is outside L2 conversion scope",
                feature="port",
                status=TranslationStatus.UNSUPPORTED,
                object_name=name,
                reason="the translator emits L2 membership plus SVI addresses only",
                source=source,
                action="port the routed interface configuration to EXOS manually",
            )

        # Access VLAN exists
        if iface.access_vlan is not None and iface.access_vlan not in defined_vlans:
            sink.warn(
                "vlan.referenced_undefined",
                f"{context_label} {name}: access VLAN {iface.access_vlan} "
                f"is referenced but not defined",
                feature="vlan",
                object_name=name,
                reason="the Cisco config uses a VLAN it never declares",
                source=source,
                metadata={"vlan": iface.access_vlan, "role": "access"},
            )

        # Trunk allowed VLANs exist
        for vid in sorted(iface.trunk_allowed_vlans):
            if vid not in defined_vlans:
                sink.warn(
                    "vlan.referenced_undefined",
                    f"{context_label} {name}: trunk allowed VLAN {vid} "
                    f"is referenced but not defined",
                    feature="vlan",
                    object_name=name,
                    reason="the Cisco config uses a VLAN it never declares",
                    source=source,
                    metadata={"vlan": vid, "role": "trunk_allowed"},
                )

        # Mode consistency checks
        if iface.mode == "access" and (
            iface.trunk_allowed_vlans or iface.trunk_native_vlan is not None
        ):
            sink.warn(
                "interface.mode_conflict",
                f"{context_label} {name}: interface has access mode but trunk VLAN settings",
                feature="port",
                object_name=name,
                reason="conflicting switchport settings; the access VLAN wins in the output",
                source=source,
            )
        if iface.mode == "trunk" and iface.access_vlan is not None:
            sink.warn(
                "interface.mode_conflict",
                f"{context_label} {name}: interface has trunk mode but access VLAN set",
                feature="port",
                object_name=name,
                reason="conflicting switchport settings; the trunk settings win in the output",
                source=source,
            )

    # Check all interfaces (physical and logical)
    for name, iface in sorted(config.interfaces.items()):
        label = "Port-channel" if isinstance(iface, PortChannelInterface) else "Interface"
        check_interface(name, iface, label)

    # Bundles must have members
    for po_id, po in sorted(config.port_channels.items()):
        if not po.members:
            sink.warn(
                "lag.no_members",
                f"Port-channel{po_id}: Port-channel exists but has no member interfaces",
                feature="lag",
                object_name=f"Port-channel{po_id}",
                reason="a bundle with no members cannot become an EXOS sharing group",
                source=SourceRef(cisco_object=f"Port-channel{po_id}"),
            )

    # ACLs with untranslated ACEs are semantically incomplete (a skipped deny
    # over-permits); count them per ACL and warn once
    dropped: dict[str, int] = {}
    lines: dict[str, list] = {}
    for u in config.unsupported_lines:
        m = RE_ACL_CONTEXT.match(u.context) or RE_ACL_GLOBAL.match(u.text)
        if m:
            dropped[m.group(1)] = dropped.get(m.group(1), 0) + 1
            lines.setdefault(m.group(1), []).append(u.line_number)
    for acl in sorted(dropped):
        sink.warn(
            "acl.incomplete",
            f"ACL {acl}: {dropped[acl]} rule(s) could not be translated; the "
            f"generated ACL is incomplete -- review before applying",
            feature="acl",
            status=TranslationStatus.PARTIALLY_TRANSLATED,
            severity=Severity.ERROR,
            object_name=acl,
            reason="one or more ACEs use constructs outside the supported subset; "
                   "a dropped deny over-permits traffic",
            source=SourceRef(cisco_object=acl, lines=sorted(set(lines[acl]))),
            action="add the missing rules to the generated .pol file by hand before applying it",
            metadata={"dropped_rules": dropped[acl]},
        )

    return sink.messages
