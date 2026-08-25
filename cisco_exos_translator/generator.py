# generator pass to render EXOS config (.xsf) from a ParsedConfig
# translation decisions (vlan names, port numbers, lag master/mode) come from a
# mapping dict: derived defaults the user can override via <name>.map.json

from __future__ import annotations

import re

from .findings import (
    FindingsCollector,
    Severity,
    SourceRef,
    TranslationStatus,
    WarningSink,
)
from .models import ParsedConfig, PhysicalInterface


# SVIs ("interface Vlan10") are logical L3 interfaces, not ports; excluded from
# port translation even when they carry no ip address line
def _is_svi(iface: PhysicalInterface) -> bool:
    return iface.interface_type.lower() == "vlan"


# "Vlan10" -> 10; None if the SVI name has no usable VLAN id
RE_SVI_ID = re.compile(r"^vlan(\d+)$", re.IGNORECASE)


def _svi_vlan_id(iface: PhysicalInterface) -> int | None:
    m = RE_SVI_ID.match(iface.canonical_name)
    return int(m.group(1)) if m else None


# Translate a Cisco <member>/<module>/<port> name to an EXOS port string
# EXOS uses "<slot>:<port>" on a stack or bare "<port>" standalone; only module 0
# maps cleanly, so a non-zero (uplink) module becomes a placeholder to replace
def _exos_port(iface: PhysicalInterface, stacked: bool) -> str:
    slot = iface.stack_member
    module = iface.module
    port = iface.port

    # no numbering parsed: emit the name verbatim for manual correction
    if slot is None or port is None:
        return iface.canonical_name

    # uplink/expansion module: EXOS port number is platform-specific and unknown,
    # so emit a distinct, invalid placeholder carrying the Cisco module/port
    if module not in (0, None):
        placeholder = f"{{uplink-m{module}-p{port}}}"
        return f"{slot}:{placeholder}" if stacked else placeholder

    return f"{slot}:{port}" if stacked else str(port)


# placeholder emitted for uplink-module ports, e.g. "{uplink-m1-p2}"
RE_UPLINK = re.compile(r"\{uplink-m(\d+)-p(\d+)\}")


# resolve uplink placeholders via the mapping's uplink rule: EXOS uplinks are
# numbered right after the base ports, so module port P -> start + P - 1
# explicit per-port edits already replaced their placeholder and are unaffected
def _resolve_uplinks(
    port_map: dict[str, str], uplinks: dict | None, sink: WarningSink
) -> None:
    start = (uplinks or {}).get("start")
    if start is None:
        return
    if not isinstance(start, int) or start < 1:
        sink.warn(
            "mapping.invalid_uplink_start",
            f"mapping: uplinks.start '{start}' is not a positive integer; ignored",
            feature="port",
            category="mapping",
            reason="the mapping file value is not usable",
            action="set uplinks.start to the first uplink port number on the target switch",
        )
        return
    for name, exos in port_map.items():
        m = RE_UPLINK.search(exos)
        if m:
            port_map[name] = RE_UPLINK.sub(str(start + int(m.group(2)) - 1), exos)


# Sanitize a Cisco VLAN name to EXOS rules: starts with a letter, alnum/_, <=32
def _sanitize_vlan_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", name)
    if not cleaned or not cleaned[0].isalpha():
        cleaned = f"V_{cleaned}"
    return cleaned[:32]


# Map every VLAN id that must exist in EXOS (defined + referenced) to a name
def _build_vlan_name_map(config: ParsedConfig, sink: WarningSink) -> dict[int, str]:
    # Collect VLANs referenced by any port (access / trunk allowed / native)
    # or by an addressed SVI (its VLAN must exist to carry the IP)
    referenced: set[int] = set()
    for iface in config.interfaces.values():
        if iface.access_vlan is not None:
            referenced.add(iface.access_vlan)
        referenced.update(iface.trunk_allowed_vlans)
        if iface.trunk_native_vlan is not None:
            referenced.add(iface.trunk_native_vlan)
        if iface.ip_address and isinstance(iface, PhysicalInterface) and _is_svi(iface):
            vid = _svi_vlan_id(iface)
            if vid is not None:
                referenced.add(vid)

    # Referenced-but-undefined VLANs are still created so the output is valid
    # (tag 1 is excluded: it maps to the built-in Default, not an auto-created VLAN)
    defined = set(config.vlans.keys())
    for vid in sorted(referenced - defined):
        if vid == 1:
            continue
        sink.warn(
            "vlan.auto_created",
            f"VLAN {vid}: referenced by a port but never defined; "
            f"auto-creating it in the EXOS output",
            feature="vlan",
            status=TranslationStatus.ASSUMPTION_MADE,
            object_name=f"VLAN {vid}",
            reason="a port references the VLAN, so the .xsf would be invalid without it",
            source=SourceRef(cisco_object=f"vlan {vid}"),
            action=f"confirm VLAN {vid} should exist on the EXOS switch",
            metadata={"vlan": vid},
        )

    name_map: dict[int, str] = {}
    used_names: set[str] = set()
    for vid in sorted(defined | referenced):
        # Tag 1 is the EXOS built-in "Default" VLAN; reuse it, never recreate it
        if vid == 1:
            v = config.vlans.get(1)
            if v and v.name and v.name.lower() != "default":
                sink.warn(
                    "vlan.default_name_ignored",
                    f"VLAN 1: Cisco name '{v.name}' ignored; EXOS uses the "
                    f"built-in Default VLAN for tag 1",
                    feature="vlan",
                    status=TranslationStatus.ASSUMPTION_MADE,
                    object_name="VLAN 1",
                    reason="tag 1 maps to the EXOS built-in Default VLAN, which is not renamed",
                    metadata={"vlan": 1, "cisco_name": v.name},
                )
            name_map[1] = "Default"
            used_names.add("Default")
            continue
        raw = config.vlans[vid].name if vid in config.vlans and config.vlans[vid].name else None
        base = _sanitize_vlan_name(raw) if raw else f"VLAN_{vid}"
        # Suffix the tag if the base name is already taken
        name = f"{base}_{vid}" if base in used_names else base
        used_names.add(name)
        name_map[vid] = name
    return name_map


# Decide whether a bundle defaults to LACP (True) or static (False)
# active/passive -> LACP, on -> static, auto/desirable -> PAgP (flagged, LACP)
def _lacp_for_modes(po_id: int, modes: set[str], sink: WarningSink) -> bool:
    if modes & {"auto", "desirable"}:
        sink.warn(
            "lag.pagp_defaulted_to_lacp",
            f"Port-channel{po_id}: PAgP mode(s) {sorted(modes & {'auto', 'desirable'})} "
            f"have no EXOS equivalent; defaulting to LACP (override via the mapping file)",
            feature="lag",
            status=TranslationStatus.ASSUMPTION_MADE,
            object_name=f"Port-channel{po_id}",
            reason="EXOS sharing supports LACP or static only",
            action="confirm the peer runs LACP, or set the LAG to 'static' in the mapping file",
            metadata={"cisco_modes": sorted(modes)},
        )
        return True
    if modes == {"on"}:
        return False  # static bundle
    if "on" in modes and modes & {"active", "passive"}:
        sink.warn(
            "lag.mixed_modes",
            f"Port-channel{po_id}: mixed static ('on') and LACP member modes; "
            f"defaulting to LACP",
            feature="lag",
            status=TranslationStatus.ASSUMPTION_MADE,
            object_name=f"Port-channel{po_id}",
            reason="the bundle's members disagree; one EXOS sharing mode must be chosen",
            action="confirm the intended aggregation mode for this bundle",
            metadata={"cisco_modes": sorted(modes)},
        )
    return True


# collect channel modes of a bundle's member ports
def _member_modes(config: ParsedConfig, po) -> set[str]:
    return {
        config.interfaces[m].channel_mode
        for m in po.members
        if isinstance(config.interfaces.get(m), PhysicalInterface)
        and config.interfaces[m].channel_mode
    }


# build the default translation mapping: every decision the user may override
def build_default_mapping(config: ParsedConfig) -> dict:
    stacked = len(config.stack_members) > 1
    throwaway = WarningSink()  # warnings re-raised at generation time

    vlans = {
        str(vid): name
        for vid, name in _build_vlan_name_map(config, throwaway).items()
    }

    ports: dict[str, str] = {}
    for name, iface in sorted(config.interfaces.items()):
        if isinstance(iface, PhysicalInterface) and not _is_svi(iface):
            ports[name] = _exos_port(iface, stacked)

    lags: dict[str, dict] = {}
    for po_id, po in sorted(config.port_channels.items()):
        if not po.members:
            continue
        member_ports = [ports[m] for m in sorted(po.members) if m in ports]
        if not member_ports:
            continue
        lacp = _lacp_for_modes(po_id, _member_modes(config, po), throwaway)
        lags[f"Port-channel{po_id}"] = {
            "master": member_ports[0],
            "mode": "lacp" if lacp else "static",
        }

    return {
        "_help": [
            "Translation mapping: derived defaults from the Cisco config.",
            "Edit values and re-run the translator; your edits win over defaults.",
            "vlans: Cisco tag -> EXOS VLAN name (tag 1 is the built-in Default).",
            "ports: Cisco interface -> EXOS port; {uplink-mN-pP} tokens are",
            "       unresolved uplink-module ports (see uplinks below), or",
            "       replace them here individually.",
            "uplinks: set start to the target switch's first uplink port number",
            "       (base ports + 1, e.g. 49 on a 48-port switch); every",
            "       {uplink-mN-pP} then resolves to start + P - 1 automatically.",
            "lags: master must be one of the LAG's member ports; mode is",
            "      'lacp' or 'static'.",
            "Entries for interfaces no longer in the Cisco config are ignored;",
            "new interfaces get derived defaults until added here.",
        ],
        "vlans": vlans,
        "ports": ports,
        "uplinks": {"start": None},
        "lags": lags,
    }


# Add a port as an untagged member of a VLAN
# EXOS ports start untagged in Default, so move them out first unless the target
# is Default (tag 1) itself
def _untagged_lines(port: str, vid: int, vlan_names: dict[int, str]) -> list[str]:
    if vid == 1:
        return [f"configure vlan Default add ports {port} untagged"]
    return [
        f"configure vlan Default delete ports {port}",
        f'configure vlan "{vlan_names[vid]}" add ports {port} untagged',
    ]


# Render a port's switchport mode as EXOS "add ports" lines
# access -> untagged on the access VLAN; trunk -> native untagged, rest tagged
def _membership_lines(
    port: str, iface, vlan_names: dict[int, str], sink: WarningSink
) -> list[str]:
    lines: list[str] = []

    # Trunk if explicitly trunk or it carries any trunk VLAN settings
    is_trunk = (
        iface.mode == "trunk"
        or bool(iface.trunk_allowed_vlans)
        or iface.trunk_native_vlan is not None
    )

    if is_trunk:
        native = iface.trunk_native_vlan
        if native is not None:
            lines.extend(_untagged_lines(port, native, vlan_names))

        # A trunk with no allowed list carries all VLANs in Cisco; expand to
        # every VLAN this config defines (excluding Default, which stays
        # untagged) so the EXOS trunk actually carries traffic
        allowed = iface.trunk_allowed_vlans
        if not allowed:
            allowed = {vid for vid in vlan_names if vid != 1}
            sink.warn(
                "trunk.expanded_all_vlans",
                f"{iface.canonical_name}: trunk has no allowed-VLAN list (Cisco "
                f"carries all VLANs); expanded to all {len(allowed)} non-Default "
                f"VLANs defined on this switch",
                feature="port",
                status=TranslationStatus.ASSUMPTION_MADE,
                object_name=iface.canonical_name,
                reason="Cisco trunks carry every VLAN by default; EXOS membership is explicit",
                source=SourceRef(
                    cisco_object=iface.canonical_name,
                    line=iface.source_lines[0] if iface.source_lines else None,
                ),
                action="prune the VLAN list on this trunk if it should not carry all of them",
                metadata={"expanded_vlans": sorted(allowed)},
            )

        for vid in sorted(allowed):
            if vid == native:  # native is already added untagged
                continue
            lines.append(f'configure vlan "{vlan_names[vid]}" add ports {port} tagged')
    elif iface.access_vlan is not None:
        lines.extend(_untagged_lines(port, iface.access_vlan, vlan_names))

    return lines


# Render one Cisco ACL as an EXOS policy file (.pol): ordered entries, remarks
# preserved as # comments, plus a trailing deny-all (Cisco's implicit deny;
# EXOS permits unmatched traffic by default)
def _acl_policy(acl_name: str, rules) -> str:
    lines = [f"# translated from Cisco ACL {acl_name}"]
    n = 0
    for r in rules:
        if r.action == "remark":
            lines.append(f"# {r.remark}")
            continue
        n += 10
        conds = []
        if r.protocol:
            conds.append(f"protocol {r.protocol}")
        if r.source:
            conds.append(f"source-address {r.source}")
        if r.source_port:
            lo, hi = r.source_port
            conds.append(f"source-port {lo}" if lo == hi else f"source-port {lo} - {hi}")
        if r.destination:
            conds.append(f"destination-address {r.destination}")
        if r.destination_port:
            lo, hi = r.destination_port
            conds.append(
                f"destination-port {lo}" if lo == hi else f"destination-port {lo} - {hi}"
            )
        lines.append(f"entry r{n} {{")
        lines.append("    if {")
        for c in conds:
            lines.append(f"        {c};")
        lines.append("    } then {")
        lines.append(f"        {r.action};")
        lines.append("    }")
        lines.append("}")
    # match all IPv4 (not an empty match): Cisco's implicit deny applies to IP
    # traffic only -- an unconditional deny here would also drop ARP etc.
    lines.append("# Cisco implicit deny (IPv4 only)")
    lines.append("entry implicit_deny {")
    lines.append("    if {")
    lines.append("        source-address 0.0.0.0/0;")
    lines.append("    } then {")
    lines.append("        deny;")
    lines.append("    }")
    lines.append("}")
    return "\n".join(lines) + "\n"


# Summarize every Cisco line the parser could not translate (global and
# per-interface), deduped by command text with counts and example line numbers
def _unsupported_summary(config: ParsedConfig, max_unique: int = 40) -> list[str]:
    items = list(config.unsupported_lines)
    for iface in config.interfaces.values():
        items.extend(iface.unsupported_lines)
    if not items:
        return []

    counts: dict[str, int] = {}
    examples: dict[str, list[int]] = {}
    for u in items:
        key = u.text.strip()
        counts[key] = counts.get(key, 0) + 1
        examples.setdefault(key, []).append(u.line_number)

    lines = [
        f"# Not translated ({len(items)} line(s) outside this tool's L2 scope; "
        f"review for anything you must port manually):"
    ]
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    for text, n in ranked[:max_unique]:
        # a range block expands to many interfaces sharing one source line, so
        # dedupe the example line numbers
        uniq = sorted(set(examples[text]))
        ex = ", ".join(str(x) for x in uniq[:3])
        if n > 1:
            more = ", ..." if len(uniq) > 3 else ""
            lines.append(f"#   - '{text}' x{n} (lines {ex}{more})")
        else:
            lines.append(f"#   - '{text}' (line {ex})")
    if len(ranked) > max_unique:
        rest = sum(n for _, n in ranked[max_unique:])
        lines.append(
            f"#   ... and {len(ranked) - max_unique} more distinct commands "
            f"({rest} line(s)) -- see the source config"
        )
    return lines


# Commented reference of the active translation, prepended to the .xsf
# the editable source of these values is the .map.json file
def _translation_reference(
    config: ParsedConfig, vlan_names: dict[int, str], port_map: dict[str, str],
    lag_map: dict[int, tuple[str, list[str], bool]],
) -> list[str]:
    lines = ["# Translation reference (from the mapping file; edit it and re-run to change)"]

    # VLANs: Cisco tag -> EXOS name, flagging names we invented or changed
    lines.append("# VLANs (Cisco tag -> EXOS name):")
    for vid in sorted(vlan_names):
        name = vlan_names[vid]
        cisco = config.vlans[vid].name if vid in config.vlans else None
        if vid == 1:
            note = "  (built-in Default)"
        elif not cisco:
            note = "  (auto-named)"
        elif name != cisco:
            note = "  (was '" + cisco + "')"
        else:
            note = ""
        lines.append(f"#   {vid} -> {name}{note}")

    # Ports: Cisco name -> EXOS port (skip routed, which are out of L2 scope)
    port_items = [
        (name, port_map[name])
        for name in sorted(port_map)
        if getattr(config.interfaces.get(name), "mode", None) != "routed"
    ]
    if port_items:
        lines.append("# Ports (Cisco name -> EXOS port):")
        for cisco_name, exos in port_items:
            note = "  (uplink placeholder; replace)" if "{uplink" in exos else ""
            lines.append(f"#   {cisco_name} -> {exos}{note}")

    # LAG: Cisco Port-channel -> EXOS master port (bundles are keyed by master)
    if lag_map:
        lines.append("# LAG (Cisco Port-channel -> EXOS master port):")
        for po_id, (master, members, lacp) in sorted(lag_map.items()):
            mode = "lacp" if lacp else "static"
            lines.append(
                f"#   Port-channel{po_id} -> {master}  "
                f"(members: {', '.join(members)}; {mode})"
            )

    lines.append("")
    return lines


def generate_exos_config(
    config: ParsedConfig,
    mapping: dict | None = None,
    mapping_notes: list[str] | None = None,
    collector: FindingsCollector | None = None,
) -> tuple[str, list[str], dict[str, str]]:
    # Returns the .xsf text, the translation warnings, and the ACL policy
    # files as {policy_name: .pol content} (empty when the config has no
    # applied ACLs). When a collector is supplied, every warning is also
    # recorded as a structured finding.
    sink = WarningSink(collector, category="translation")
    for note in mapping_notes or []:
        sink.warn(
            "mapping.note",
            note,
            feature="mapping",
            category="mapping",
            severity=Severity.INFO,
            reason="difference between the mapping file and the current Cisco config",
        )
    out: list[str] = []

    if mapping is None:
        mapping = build_default_mapping(config)

    # vlan names: rebuild defaults for their warnings, then mapping values win
    vlan_names = _build_vlan_name_map(config, sink)
    for key, name in (mapping.get("vlans") or {}).items():
        try:
            vid = int(key)
        except ValueError:
            sink.warn(
                "mapping.bad_vlan_key",
                f"mapping: VLAN key '{key}' is not a number; ignored",
                feature="vlan",
                category="mapping",
                reason="VLAN keys in the mapping file must be numeric Cisco tags",
            )
            continue
        if vid in vlan_names:
            vlan_names[vid] = name

    # port names come from the mapping; resolve uplink placeholders through the
    # uplink rule, then validate what is left in place
    port_map: dict[str, str] = dict(mapping.get("ports") or {})
    _resolve_uplinks(port_map, mapping.get("uplinks"), sink)
    collisions: dict[str, list[str]] = {}
    for name, exos in sorted(port_map.items()):
        iface = config.interfaces.get(name)
        if getattr(iface, "mode", None) == "routed":
            continue  # excluded from output anyway
        if "{uplink" in exos:
            sink.warn(
                "port.unresolved_uplink",
                f"{name}: unresolved uplink placeholder '{exos}'; set "
                f"uplinks.start in the mapping file (first uplink port number) "
                f"or replace this port entry individually",
                feature="port",
                status=TranslationStatus.PARTIALLY_TRANSLATED,
                severity=Severity.ERROR,
                object_name=name,
                reason="uplink-module ports have no platform-independent EXOS port number",
                source=SourceRef(cisco_object=name),
                output_ref=exos,
                action="replace the placeholder with the real EXOS port before deploying",
            )
        elif exos == name:
            sink.warn(
                "port.no_exos_number",
                f"{name}: could not derive an EXOS port number; emitted the "
                f"name verbatim -- correct it in the mapping file",
                feature="port",
                status=TranslationStatus.PARTIALLY_TRANSLATED,
                severity=Severity.ERROR,
                object_name=name,
                reason="the Cisco interface name did not parse into stack/module/port",
                source=SourceRef(cisco_object=name),
                action="set the EXOS port for this interface in the mapping file",
            )
        collisions.setdefault(exos, []).append(name)
    for exos, sources in sorted(collisions.items()):
        if len(sources) > 1:
            sink.warn(
                "port.collision",
                f"EXOS port '{exos}' is the target of multiple Cisco interfaces "
                f"({', '.join(sources)}); their config is merged and almost "
                f"certainly wrong -- assign distinct ports in the mapping file",
                feature="port",
                status=TranslationStatus.ERROR,
                severity=Severity.ERROR,
                object_name=exos,
                reason="two Cisco interfaces resolved to the same EXOS port",
                output_ref=exos,
                action="assign distinct EXOS ports in the mapping file and re-run",
                metadata={"cisco_interfaces": sources},
            )

    # resolve each LAG once: master (mapping wins), member ports, lacp/static
    lag_settings = mapping.get("lags") or {}
    lag_map: dict[int, tuple[str, list[str], bool]] = {}
    for po_id, po in sorted(config.port_channels.items()):
        if not po.members:
            sink.warn(
                "lag.skipped_no_members",
                f"Port-channel{po_id}: no member ports; skipped in EXOS output",
                feature="lag",
                status=TranslationStatus.UNSUPPORTED,
                object_name=f"Port-channel{po_id}",
                reason="an EXOS sharing group needs at least one member port",
                source=SourceRef(cisco_object=f"Port-channel{po_id}"),
            )
            continue
        member_ports = [port_map[m] for m in sorted(po.members) if m in port_map]
        if not member_ports:
            continue
        default_lacp = _lacp_for_modes(po_id, _member_modes(config, po), sink)

        entry = lag_settings.get(f"Port-channel{po_id}") or {}
        master = entry.get("master") or member_ports[0]
        if master not in member_ports:
            sink.warn(
                "mapping.bad_lag_master",
                f"Port-channel{po_id}: mapping master '{master}' is not one of "
                f"the member ports; using '{member_ports[0]}'",
                feature="lag",
                category="mapping",
                object_name=f"Port-channel{po_id}",
                reason="the EXOS sharing master must be one of the grouped ports",
            )
            master = member_ports[0]
        mode = entry.get("mode")
        if mode not in (None, "lacp", "static"):
            sink.warn(
                "mapping.bad_lag_mode",
                f"Port-channel{po_id}: mapping mode '{mode}' is not "
                f"'lacp'/'static'; using the derived default",
                feature="lag",
                category="mapping",
                object_name=f"Port-channel{po_id}",
                reason="only 'lacp' and 'static' are valid mapping modes",
            )
            mode = None
        lacp = default_lacp if mode is None else (mode == "lacp")
        lag_map[po_id] = (master, member_ports, lacp)

    # Bundle members get their L2 config from the LAG master, not individually
    bundled: set[str] = set()
    for po in config.port_channels.values():
        bundled.update(po.members)

    # System
    out.append("# System")
    if config.hostname:
        out.append(f'configure snmp sysName "{config.hostname}"')

    # Stacking: Cisco SKU can't map to an EXOS slot type, so emit review comments
    # Only shown if a member carries provisioning or priority info
    if any(m.provision_model or m.priority is not None for m in config.stack_members.values()):
        out.append("")
        out.append("# Stacking (review: EXOS stacking is configured on-hardware)")
        for member_id, member in sorted(config.stack_members.items()):
            if member.provision_model:
                sink.warn(
                    "stack.model_not_mappable",
                    f"Stack member {member_id}: Cisco model "
                    f"'{member.provision_model}' cannot be mapped to an EXOS "
                    f"slot type; configure the stack slot on the EXOS hardware",
                    feature="stack",
                    status=TranslationStatus.UNSUPPORTED,
                    object_name=f"switch {member_id}",
                    reason="EXOS slot types are hardware-specific and set on the switch",
                    action="bring the stack up on the hardware before loading the .xsf",
                    metadata={"cisco_model": member.provision_model},
                )
                out.append(f"#   slot {member_id}: was '{member.provision_model}'")
            if member.priority is not None:
                out.append(
                    f"# configure stacking slot {member_id} priority {member.priority}"
                )

    # VLANs
    out.append("")
    out.append("# VLANs")
    for vid in sorted(vlan_names):
        if vid == 1:
            continue  # skip Default VLAN
        out.append(f'create vlan "{vlan_names[vid]}" tag {vid}')

    # Link aggregation
    if lag_map:
        out.append("")
        out.append("# Link aggregation (sharing)")
        for po_id, (master, member_ports, lacp) in sorted(lag_map.items()):
            grouping = ",".join(member_ports)
            suffix = " lacp" if lacp else ""
            out.append(
                f"enable sharing {master} grouping {grouping} "
                f"algorithm address-based L3_L4{suffix}"
            )

    # Port configuration
    out.append("")
    out.append("# Port configuration")

    # Bundle L2 config applies to the LAG master port
    for po_id, (master, member_ports, _lacp) in sorted(lag_map.items()):
        po = config.port_channels[po_id]

        out.append(f"# Port-channel{po_id}")
        if po.description:
            out.append(f'configure ports {master} description-string "{po.description}"')
        out.extend(_membership_lines(master, po, vlan_names, sink))
        if po.shutdown:
            out.append(f"disable ports {master}")

        # An individually shut-down member is still disabled
        for canonical in sorted(po.members):
            m_iface = config.interfaces.get(canonical)
            if isinstance(m_iface, PhysicalInterface) and m_iface.shutdown:
                out.append(f"disable ports {port_map[canonical]}")

    # Standalone physical ports (not bundled, not routed, not SVIs)
    for name, iface in sorted(config.interfaces.items()):
        if not isinstance(iface, PhysicalInterface):
            continue
        if name in bundled:
            continue
        if _is_svi(iface):
            # addressed SVIs are handled by the L3 section below
            if not iface.ip_address:
                sink.warn(
                    "svi.no_address",
                    f"{name}: SVI with no IP address; skipped",
                    feature="l3",
                    status=TranslationStatus.UNSUPPORTED,
                    severity=Severity.INFO,
                    object_name=name,
                    reason="an SVI without an address produces no EXOS output",
                    source=SourceRef(cisco_object=name),
                )
            continue
        if iface.mode == "routed":
            # validation already recorded this interface as out of scope
            sink.warn(
                "interface.routed_skipped",
                f"{name}: routed (L3) interface is out of L2 scope; skipped",
                record=False,
            )
            out.append(f"# {name}: routed interface skipped (L3, out of scope)")
            continue
        if name not in port_map:
            sink.warn(
                "port.no_mapping_entry",
                f"{name}: no port mapping entry; skipped",
                feature="port",
                status=TranslationStatus.UNSUPPORTED,
                severity=Severity.ERROR,
                object_name=name,
                reason="the mapping file has no EXOS port for this interface",
                source=SourceRef(cisco_object=name),
                action="add a ports entry for this interface in the mapping file",
            )
            continue

        port = port_map[name]
        membership = _membership_lines(port, iface, vlan_names, sink)

        # Skip ports with nothing to configure
        if not (iface.description or membership or iface.shutdown):
            continue

        if iface.description:
            out.append(f'configure ports {port} description-string "{iface.description}"')
        out.extend(membership)
        if iface.shutdown:
            out.append(f"disable ports {port}")

    # L3: addressed SVIs -> VLAN IP + per-VLAN forwarding, then static routes
    l3_lines: list[str] = []
    for name, iface in sorted(config.interfaces.items()):
        if not (isinstance(iface, PhysicalInterface) and _is_svi(iface) and iface.ip_address):
            continue
        vid = _svi_vlan_id(iface)
        if vid is None or vid not in vlan_names:
            continue
        if iface.shutdown:
            sink.warn(
                "svi.shutdown",
                f"{name}: SVI is shutdown; L3 config not emitted",
                feature="l3",
                status=TranslationStatus.UNSUPPORTED,
                object_name=name,
                reason="the source SVI is administratively down",
                source=SourceRef(cisco_object=name),
                action="enable and configure the VLAN IP on EXOS if it is needed",
            )
            continue
        vlan = "Default" if vid == 1 else f'"{vlan_names[vid]}"'
        l3_lines.append(f"configure vlan {vlan} ipaddress {iface.ip_address}")
        l3_lines.append(f"enable ipforwarding vlan {vlan}")
    for dest, gw in config.static_routes:
        target = "default" if dest == "0.0.0.0/0" else dest
        l3_lines.append(f"configure iproute add {target} {gw}")
    if l3_lines:
        out.append("")
        out.append("# L3 (SVI addresses, routing)")
        out.extend(l3_lines)

    # ACLs: Cisco ACLs referenced by "ip access-group <name> in" become EXOS
    # policy files (.pol, emitted as separate outputs) applied ingress; the
    # .pol files must be on the switch before this script is loaded
    # Targets: switchports -> "ports N"; SVIs (router ACLs) -> 'vlan "NAME"'
    applied: dict[str, list[str]] = {}  # acl name -> EXOS apply targets
    for name, iface in sorted(config.interfaces.items()):
        acl = iface.access_group_in
        if not acl:
            continue
        if isinstance(iface, PhysicalInterface):
            if _is_svi(iface):
                vid = _svi_vlan_id(iface)
                if vid is None or vid not in vlan_names:
                    sink.warn(
                        "acl.svi_vlan_missing",
                        f"{name}: ACL {acl} is a router ACL but the SVI's VLAN "
                        f"is not in the output; not applied",
                        feature="acl",
                        status=TranslationStatus.UNSUPPORTED,
                        severity=Severity.ERROR,
                        object_name=acl,
                        reason="the ACL's target VLAN does not exist in the EXOS output",
                        source=SourceRef(cisco_object=name),
                    )
                    continue
                vlan = "Default" if vid == 1 else f'"{vlan_names[vid]}"'
                applied.setdefault(acl, []).append(f"vlan {vlan}")
                # EXOS VLAN ACLs also filter bridged intra-VLAN traffic, which
                # a Cisco router ACL never touches
                sink.warn(
                    "acl.router_acl_widened",
                    f"{name}: ACL {acl} applied to vlan {vlan} is stricter than "
                    f"the Cisco router ACL: EXOS also filters intra-VLAN "
                    f"(bridged) traffic, not just routed traffic",
                    feature="acl",
                    status=TranslationStatus.PARTIALLY_TRANSLATED,
                    object_name=acl,
                    reason="EXOS applies a VLAN ACL to bridged traffic as well as routed",
                    source=SourceRef(cisco_object=name),
                    output_ref=f"{acl} -> vlan {vlan}",
                    action="verify intra-VLAN traffic is not broken by the wider match",
                )
                continue
            if iface.mode == "routed":
                sink.warn(
                    "acl.on_routed_port",
                    f"{name}: ACL {acl} on a routed physical port; not "
                    f"translated (routed ports are out of scope)",
                    feature="acl",
                    status=TranslationStatus.UNSUPPORTED,
                    object_name=acl,
                    reason="routed physical ports are outside the translator's scope",
                    source=SourceRef(cisco_object=name),
                )
                continue
            if name in bundled:
                continue
            if name in port_map:
                applied.setdefault(acl, []).append(f"ports {port_map[name]}")
        else:  # bundle: ACL applies on the LAG master port
            po_id = next(
                (pid for pid, po in config.port_channels.items()
                 if po.canonical_name == name), None
            )
            if po_id in lag_map:
                applied.setdefault(acl, []).append(f"ports {lag_map[po_id][0]}")

    for acl in sorted(set(config.acls) - set(applied)):
        sink.warn(
            "acl.defined_not_applied",
            f"ACL {acl}: defined but not applied to any translated target; skipped",
            feature="acl",
            status=TranslationStatus.UNSUPPORTED,
            severity=Severity.INFO,
            object_name=acl,
            reason="no translated interface references this ACL with 'ip access-group ... in'",
            source=SourceRef(cisco_object=acl),
        )
    pol_files: dict[str, str] = {}  # policy name -> .pol file content
    if applied:
        out.append("")
        out.append("# ACLs (policy files; upload each <name>.pol to the switch")
        out.append("# BEFORE loading this script)")
    for acl, targets in sorted(applied.items()):
        rules = config.acls.get(acl)
        if not rules:
            sink.warn(
                "acl.referenced_undefined",
                f"ACL {acl}: referenced by 'ip access-group' but never defined; "
                f"not applied",
                feature="acl",
                status=TranslationStatus.ERROR,
                severity=Severity.ERROR,
                object_name=acl,
                reason="the Cisco config applies an ACL it never defines",
                source=SourceRef(cisco_object=acl),
                action="supply the missing ACL, or remove the reference",
            )
            continue
        # policy name must equal the .pol basename; reuse the VLAN name rules
        base = _sanitize_vlan_name(acl)
        pol_files[base] = _acl_policy(acl, rules)
        for target in sorted(set(targets)):
            out.append(f"configure access-list {base} {target} ingress")

    # Prepend the translation reference, then the warning banner, so both travel
    # with the .xsf (warnings first, then the reference, then the config)
    out = _translation_reference(config, vlan_names, port_map, lag_map) + out

    # One banner for all warnings, grouped by source: input problems in the
    # Cisco config, lines dropped as untranslatable, and translation decisions
    input_warnings = config.warnings
    unsupported = _unsupported_summary(config)
    if input_warnings or unsupported or sink.messages:
        banner = ["# WARNINGS — review before deploying"]
        if input_warnings:
            banner.append("# Input (problems in the Cisco config):")
            banner.extend(f"#   - {w}" for w in input_warnings)
        banner.extend(unsupported)
        if sink.messages:
            banner.append("# Translation (decisions made converting to EXOS):")
            banner.extend(f"#   - {w}" for w in sink.messages)
        banner.append("")
        out = banner + out

    return "\n".join(out) + "\n", sink.messages, pol_files


# Stack bring-up runbook for configs with 2+ stack members
# EXOS stacking is a mode change with per-node reboots and a fresh config
# context, so it cannot live in the .xsf; this emits the ordered steps instead.
# Returns None for standalone configs
def generate_stack_setup(config: ParsedConfig, xsf_name: str) -> str | None:
    members = config.stack_members
    if len(members) < 2:
        return None

    out: list[str] = []
    host = config.hostname or "the stack"
    out.append(f"# Stack bring-up runbook for {host} -- run BEFORE loading {xsf_name}")
    out.append(f"# Derived from the Cisco config: {len(members)} stack members")
    for mid, m in sorted(members.items()):
        model = m.provision_model or "model not in config"
        prio = f", priority {m.priority}" if m.priority is not None else ""
        out.append(f"#   Cisco switch {mid}: {model}{prio}")
    out.append("#")
    out.append("# Stacking is a mode change (reboots, fresh config context), so these")
    out.append("# steps cannot be part of the .xsf. Follow them in order.")
    out.append("")

    out.append("# Phase 0 -- prerequisites: same EXOS version on all nodes (show version),")
    out.append("# stack cables connected (ring recommended)")
    out.append("")

    out.append("# Phase 1 -- on EACH switch individually (console or per-switch mgmt);")
    out.append("# turns the stack ports on (they stop being data ports)")
    out.append("show stacking-support  # already Enabled on every node? skip to Phase 2")
    out.append("enable stacking-support")
    out.append("save configuration")
    out.append("reboot")
    out.append("")

    out.append("# Phase 2 -- after all nodes are back, ONLY on the switch that was")
    out.append("# Cisco member 1 (slot numbers must match the Cisco member numbers so")
    out.append("# the slot:port config in the .xsf lands on the right ports)")
    out.append(f"show stacking          # expect {len(members)} nodes, topology Ring")
    out.append("enable stacking        # accept Easy Setup; this node becomes slot 1;")
    out.append("                       # easy setup reboots the whole stack itself")
    out.append("# if Easy Setup is NOT offered (parameters kept from an earlier stack):")
    out.append("# show stacking configuration   -> 'e' flag on every node, slots correct")
    out.append("# reboot stack-topology         -> plain reboot restarts this node only")
    out.append("")

    out.append("# Phase 3 -- after the stack reboot, on the slot-1 console")
    out.append("# NOTE: stacking mode boots a fresh config context -- re-establish any")
    out.append("# management access (mgmt IP etc.) your way before continuing remotely")
    priolines = [
        f"configure stacking slot {mid} priority {m.priority}"
        for mid, m in sorted(members.items())
        if m.priority is not None
    ]
    if priolines:
        out.append("# master-election priorities from the Cisco config (higher wins on both)")
        out.extend(priolines)
    out.append("save configuration")
    out.append("")

    out.append("# Phase 4 -- verify, then load the translated config")
    out.append("show stacking          # all nodes Active, slot 1 Master")
    out.append("show slot              # all slots Operational")
    out.append(f"load script {xsf_name}")
    out.append("save configuration")
    return "\n".join(out) + "\n"
