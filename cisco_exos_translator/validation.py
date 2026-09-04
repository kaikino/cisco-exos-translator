# Validation pass: cross-reference checks that produce warnings.

from __future__ import annotations

import re

from .helpers import interface_sort_key
from .models import BaseInterface, ParsedConfig, PortChannelInterface

# unsupported-line contexts/texts that belong to an ACL, for incompleteness checks
RE_ACL_CONTEXT = re.compile(r"^ip\s+access-list\s+\S+\s+(\S+)$", re.IGNORECASE)
RE_ACL_GLOBAL = re.compile(r"^access-list\s+(\d+)\s", re.IGNORECASE)


def validate_parsed_config(config: ParsedConfig) -> list[str]:
    # Validate cross-references; return warning strings.
    warnings: list[str] = []
    defined_vlans = set(config.vlans.keys())

    def check_interface(name: str, iface: BaseInterface, context_label: str) -> None:
        # Routed (L3) interface; addressed SVIs are translated (VLAN ipaddress)
        # so only the rest is out of scope
        if iface.mode == "routed" and not (
            iface.ip_address and iface.interface_type.lower() == "vlan"
        ):
            warnings.append(
                f"{context_label} {name}: routed interface (no switchport / ip address) "
                f"is outside L2 conversion scope"
            )

        # Access VLAN exists
        if iface.access_vlan is not None and iface.access_vlan not in defined_vlans:
            warnings.append(
                f"{context_label} {name}: access VLAN {iface.access_vlan} "
                f"is referenced but not defined"
            )

        # Trunk allowed VLANs exist
        for vid in sorted(iface.trunk_allowed_vlans):
            if vid not in defined_vlans:
                warnings.append(
                    f"{context_label} {name}: trunk allowed VLAN {vid} "
                    f"is referenced but not defined"
                )

        # Native VLAN exists (tag 1 is exempt: it is the EXOS built-in Default)
        native = iface.trunk_native_vlan
        if native is not None and native != 1 and native not in defined_vlans:
            warnings.append(
                f"{context_label} {name}: trunk native VLAN {native} "
                f"is referenced but not defined"
            )

        # Mode consistency checks
        if iface.mode == "access" and (
            iface.trunk_allowed_vlans or iface.trunk_native_vlan is not None
        ):
            warnings.append(
                f"{context_label} {name}: interface has access mode but trunk VLAN settings"
            )
        if iface.mode == "trunk" and iface.access_vlan is not None:
            warnings.append(
                f"{context_label} {name}: interface has trunk mode but access VLAN set"
            )

    # Check all interfaces (physical and logical), in natural port order
    for name, iface in sorted(config.interfaces.items(), key=lambda kv: interface_sort_key(kv[0])):
        label = "Port-channel" if isinstance(iface, PortChannelInterface) else "Interface"
        check_interface(name, iface, label)

    # Bundles must have members
    for po_id, po in sorted(config.port_channels.items()):
        if not po.members:
            warnings.append(
                f"Port-channel{po_id}: Port-channel exists but has no member interfaces"
            )

    # SPAN sessions need both a source and a destination to be translatable
    for sid, sess in sorted(config.monitor_sessions.items()):
        has_source = bool(sess.source_ports or sess.source_vlans)
        if not sess.destination_ports and has_source:
            warnings.append(
                f"monitor session {sid}: no destination interface; "
                f"session not translated"
            )
        if sess.destination_ports and not has_source:
            warnings.append(
                f"monitor session {sid}: no source interface/vlan; "
                f"session not translated"
            )
        overlap = set(sess.destination_ports) & {p for p, _ in sess.source_ports}
        for name in sorted(overlap):
            warnings.append(
                f"monitor session {sid}: {name} is both a source and the "
                f"destination"
            )
        for vid, _direction in sess.source_vlans:
            # tag 1 is exempt: it maps to the EXOS built-in Default VLAN
            if vid != 1 and vid not in defined_vlans:
                warnings.append(
                    f"monitor session {sid}: source VLAN {vid} "
                    f"is referenced but not defined"
                )
        if sess.filter_acl is not None and sess.filter_acl not in config.acls:
            warnings.append(
                f"monitor session {sid}: filter ACL {sess.filter_acl} is "
                f"referenced but not defined; sources mirrored unfiltered"
            )

    # ACLs with untranslated ACEs are semantically incomplete (a skipped deny
    # over-permits); count them per ACL and warn once
    dropped: dict[str, int] = {}
    for u in config.unsupported_lines:
        m = RE_ACL_CONTEXT.match(u.context) or RE_ACL_GLOBAL.match(u.text)
        if m:
            dropped[m.group(1)] = dropped.get(m.group(1), 0) + 1
    for acl in sorted(dropped):
        warnings.append(
            f"ACL {acl}: {dropped[acl]} rule(s) could not be translated; the "
            f"generated ACL is incomplete -- review before applying"
        )

    return warnings
