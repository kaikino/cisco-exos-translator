# generator pass to render EXOS config (.xsf) from a ParsedConfig

from __future__ import annotations

import re

from .models import ParsedConfig, PhysicalInterface


# Translate a Cisco <member>/<module>/<port> name to an EXOS port string
# EXOS uses "<slot>:<port>" on a stack or bare "<port>" standalone; only module 0
# maps cleanly, so a non-zero (uplink) module becomes a placeholder to replace
def _exos_port(iface: PhysicalInterface, stacked: bool, warnings: list[str]) -> str:
    slot = iface.stack_member
    module = iface.module
    port = iface.port

    # No numbering parsed: emit the name verbatim for manual correction
    if slot is None or port is None:
        warnings.append(
            f"{iface.canonical_name}: could not derive an EXOS port number; "
            f"emitted the name verbatim for manual correction"
        )
        return iface.canonical_name

    # Uplink/expansion module: EXOS port number is platform-specific and unknown,
    # so emit a distinct, invalid placeholder carrying the Cisco module/port
    if module not in (0, None):
        placeholder = f"{{uplink-m{module}-p{port}}}"
        token = f"{slot}:{placeholder}" if stacked else placeholder
        warnings.append(
            f"{iface.canonical_name}: uplink/expansion module {module} has no "
            f"fixed EXOS port number; emitted placeholder '{token}' -- replace "
            f"it with the real port from the target platform's port map"
        )
        return token

    return f"{slot}:{port}" if stacked else str(port)


# Sanitize a Cisco VLAN name to EXOS rules: starts with a letter, alnum/_, <=32
def _sanitize_vlan_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", name)
    if not cleaned or not cleaned[0].isalpha():
        cleaned = f"V_{cleaned}"
    return cleaned[:32]


# Map every VLAN id that must exist in EXOS (defined + referenced) to a name
def _build_vlan_name_map(config: ParsedConfig, warnings: list[str]) -> dict[int, str]:
    # Collect VLANs referenced by any port (access / trunk allowed / native)
    referenced: set[int] = set()
    for iface in config.interfaces.values():
        if iface.access_vlan is not None:
            referenced.add(iface.access_vlan)
        referenced.update(iface.trunk_allowed_vlans)
        if iface.trunk_native_vlan is not None:
            referenced.add(iface.trunk_native_vlan)

    # Referenced-but-undefined VLANs are still created so the output is valid
    # (tag 1 is excluded: it maps to the built-in Default, not an auto-created VLAN)
    defined = set(config.vlans.keys())
    for vid in sorted(referenced - defined):
        if vid == 1:
            continue
        warnings.append(
            f"VLAN {vid}: referenced by a port but never defined; "
            f"auto-creating it in the EXOS output"
        )

    name_map: dict[int, str] = {}
    used_names: set[str] = set()
    for vid in sorted(defined | referenced):
        # Tag 1 is the EXOS built-in "Default" VLAN; reuse it, never recreate it
        if vid == 1:
            v = config.vlans.get(1)
            if v and v.name and v.name.lower() != "default":
                warnings.append(
                    f"VLAN 1: Cisco name '{v.name}' ignored; EXOS uses the "
                    f"built-in Default VLAN for tag 1"
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
    port: str, iface, vlan_names: dict[int, str]
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
        for vid in sorted(iface.trunk_allowed_vlans):
            if vid == native:  # native is already added untagged
                continue
            lines.append(f'configure vlan "{vlan_names[vid]}" add ports {port} tagged')
    elif iface.access_vlan is not None:
        lines.extend(_untagged_lines(port, iface.access_vlan, vlan_names))

    return lines


# Decide whether a bundle emits the "lacp" keyword (dynamic) or is static
# active/passive -> LACP, on -> static, auto/desirable -> PAgP (flagged, LACP)
def _lacp_for_modes(po_id: int, modes: set[str], warnings: list[str]) -> bool:
    if modes & {"auto", "desirable"}:
        warnings.append(
            f"Port-channel{po_id}: PAgP mode(s) {sorted(modes & {'auto', 'desirable'})} "
            f"have no EXOS equivalent; emitting LACP as the closest match"
        )
        return True
    if modes == {"on"}:
        return False  # static bundle
    if "on" in modes and modes & {"active", "passive"}:
        warnings.append(
            f"Port-channel{po_id}: mixed static ('on') and LACP member modes; "
            f"emitting LACP"
        )
    return True


def generate_exos_config(config: ParsedConfig) -> str:
    warnings: list[str] = []
    out: list[str] = []

    # A multi-member stack uses "slot:port"; a single switch uses bare "port"
    stacked = len(config.stack_members) > 1

    # Resolve every physical port to its EXOS name once, then flag any distinct
    # Cisco ports that still collapse to the same EXOS port
    port_map: dict[str, str] = {}
    collisions: dict[str, list[str]] = {}
    for name, iface in sorted(config.interfaces.items()):
        if isinstance(iface, PhysicalInterface):
            exos = _exos_port(iface, stacked, warnings)
            port_map[name] = exos
            collisions.setdefault(exos, []).append(name)
    for exos, sources in sorted(collisions.items()):
        if len(sources) > 1:
            warnings.append(
                f"EXOS port '{exos}' is the target of multiple Cisco interfaces "
                f"({', '.join(sources)}); their config is merged and almost "
                f"certainly wrong -- assign distinct ports manually"
            )

    vlan_names = _build_vlan_name_map(config, warnings)

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
                warnings.append(
                    f"Stack member {member_id}: Cisco model "
                    f"'{member.provision_model}' cannot be mapped to an EXOS "
                    f"slot type; configure the stack slot on the EXOS hardware"
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
    if config.port_channels:
        out.append("")
        out.append("# Link aggregation (sharing)")
        for po_id, po in sorted(config.port_channels.items()):
            if not po.members:
                warnings.append(
                    f"Port-channel{po_id}: no member ports; skipped in EXOS output"
                )
                continue

            # Resolve members to EXOS ports; the lowest is the master
            member_ports: list[str] = []
            modes: set[str] = set()
            for canonical in sorted(po.members):
                iface = config.interfaces.get(canonical)
                if not isinstance(iface, PhysicalInterface):
                    continue
                member_ports.append(port_map[canonical])
                if iface.channel_mode:
                    modes.add(iface.channel_mode)

            if not member_ports:
                continue

            master = member_ports[0]
            grouping = ",".join(member_ports)
            suffix = " lacp" if _lacp_for_modes(po_id, modes, warnings) else ""
            out.append(
                f"enable sharing {master} grouping {grouping} "
                f"algorithm address-based L3_L4{suffix}"
            )

    # Port configuration
    out.append("")
    out.append("# Port configuration")

    # Bundle L2 config applies to the LAG master port
    for po_id, po in sorted(config.port_channels.items()):
        if not po.members:
            continue
        master_name = sorted(po.members)[0]
        master_iface = config.interfaces.get(master_name)
        if not isinstance(master_iface, PhysicalInterface):
            continue
        master = port_map[master_name]

        out.append(f"# Port-channel{po_id}")
        if po.description:
            out.append(f'configure ports {master} description-string "{po.description}"')
        out.extend(_membership_lines(master, po, vlan_names))
        if po.shutdown:
            out.append(f"disable ports {master}")

        # An individually shut-down member is still disabled
        for canonical in sorted(po.members):
            m_iface = config.interfaces.get(canonical)
            if isinstance(m_iface, PhysicalInterface) and m_iface.shutdown:
                out.append(f"disable ports {port_map[canonical]}")

    # Standalone physical ports (not bundled, not routed)
    for name, iface in sorted(config.interfaces.items()):
        if not isinstance(iface, PhysicalInterface):
            continue
        if name in bundled:
            continue
        if iface.mode == "routed":
            warnings.append(
                f"{name}: routed (L3) interface is out of L2 scope; skipped"
            )
            out.append(f"# {name}: routed interface skipped (L3, out of scope)")
            continue

        port = port_map[name]
        membership = _membership_lines(port, iface, vlan_names)

        # Skip ports with nothing to configure
        if not (iface.description or membership or iface.shutdown):
            continue

        if iface.description:
            out.append(f'configure ports {port} description-string "{iface.description}"')
        out.extend(membership)
        if iface.shutdown:
            out.append(f"disable ports {port}")

    # Prepend a warning banner so issues travel with the .xsf
    if warnings:
        banner = ["# GENERATION WARNINGS — review before deploying:"]
        for w in warnings:
            banner.append(f"#   - {w}")
        banner.append("")
        out = banner + out

    return "\n".join(out) + "\n"
