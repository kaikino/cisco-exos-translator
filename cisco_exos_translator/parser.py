# parser pass: populate dataclasses from scanned config blocks

from __future__ import annotations

import re

from .helpers import (
    canonicalize_interface_name,
    expand_interface_range,
    parse_interface_identity,
    parse_port_channel_id,
    parse_vlan_list,
)
from .models import (
    BaseInterface,
    ConfigBlock,
    ParsedConfig,
    PhysicalInterface,
    PortChannelInterface,
    ScannedLine,
    StackMember,
    UnsupportedLine,
    Vlan,
)
from .scanner import RE_BLOCK_IFACE, RE_BLOCK_IFACE_RANGE, RE_BLOCK_VLAN

# command line pattern matching

# Global commands
#   e.g. "hostname SW-CORE-01"                  -> capture (1) "SW-CORE-01"
RE_HOSTNAME = re.compile(r"^hostname\s+(\S+)", re.IGNORECASE)
#   e.g. "switch 1 provision ws-c3850-48p"      -> capture (1) member id (2) model
RE_SWITCH_PROVISION = re.compile(
    r"^switch\s+(\d+)\s+provision\s+(.+)$", re.IGNORECASE
)
#   e.g. "switch 1 priority 15"                 -> capture (1) member id (2) priority
RE_SWITCH_PRIORITY = re.compile(r"^switch\s+(\d+)\s+priority\s+(\d+)$", re.IGNORECASE)

# VLAN-block sub-commands
#   e.g. "name USERS"                           -> capture (1) "USERS"
RE_VLAN_NAME = re.compile(r"^name\s+(.+)$", re.IGNORECASE)

# Interface-block sub-commands
#   e.g. "description Uplink to core"           -> capture (1) "Uplink to core"
RE_DESCRIPTION = re.compile(r"^description\s+(.+)$", re.IGNORECASE)
#   e.g. "shutdown"      (administratively down)
RE_SHUTDOWN = re.compile(r"^shutdown$", re.IGNORECASE)
#   e.g. "no shutdown"   (administratively up)
RE_NO_SHUTDOWN = re.compile(r"^no\s+shutdown$", re.IGNORECASE)
#   e.g. "switchport mode access/trunk"         -> capture (1) mode
RE_SW_MODE = re.compile(r"^switchport\s+mode\s+(access|trunk)$", re.IGNORECASE)
#   e.g. "switchport access vlan 10"            -> capture (1) "10"
RE_SW_ACCESS_VLAN = re.compile(r"^switchport\s+access\s+vlan\s+(\d+)$", re.IGNORECASE)
#   e.g. "switchport trunk allowed vlan 10,20,30-40"  -> capture (1) "10,20,30-40"
#   (sets the allowed list explicitly; see add/remove variants below)
RE_TRUNK_ALLOWED = re.compile(
    r"^switchport\s+trunk\s+allowed\s+vlan\s+(.+)$", re.IGNORECASE
)
#   e.g. "switchport trunk allowed vlan add 40" -> capture (1) "40" (union with list)
RE_TRUNK_ALLOWED_ADD = re.compile(
    r"^switchport\s+trunk\s+allowed\s+vlan\s+add\s+(.+)$", re.IGNORECASE
)
#   e.g. "switchport trunk allowed vlan remove 20" -> capture (1) "20" (subtract)
RE_TRUNK_ALLOWED_REMOVE = re.compile(
    r"^switchport\s+trunk\s+allowed\s+vlan\s+remove\s+(.+)$", re.IGNORECASE
)
#   e.g. "switchport trunk native vlan 10"      -> capture (1) "10"
RE_TRUNK_NATIVE = re.compile(
    r"^switchport\s+trunk\s+native\s+vlan\s+(\d+)$", re.IGNORECASE
)
#   e.g. "channel-group 1 mode active"          -> capture (1) po id, (2) mode
#   active/passive => LACP, on => static, auto/desirable => PAgP
RE_CHANNEL_GROUP = re.compile(
    r"^channel-group\s+(\d+)\s+mode\s+(active|passive|on|auto|desirable)$",
    re.IGNORECASE,
)
#   e.g. "no switchport"  -> port becomes routed (L3), i.e. out of L2 scope
RE_NO_SWITCHPORT = re.compile(r"^no\s+switchport$", re.IGNORECASE)
#   e.g. "ip address 10.0.0.1 255.255.255.252"  -> also implies a routed (L3) port
RE_IP_ADDRESS = re.compile(r"^ip\s+address\s+", re.IGNORECASE)


# _get_or_create_* helpers return the IR object for a key if it exists or creates it otherwise

# stack id -> StackMember
def _get_or_create_stack_member(
    config: ParsedConfig, member_id: int
) -> StackMember:
    if member_id not in config.stack_members:
        config.stack_members[member_id] = StackMember(member_id=member_id)
    return config.stack_members[member_id]

# VLAN id -> Vlan
def _get_or_create_vlan(config: ParsedConfig, vlan_id: int, line_no: int) -> Vlan:
    if vlan_id not in config.vlans:
        config.vlans[vlan_id] = Vlan(vlan_id=vlan_id, source_lines=[line_no])
    elif line_no not in config.vlans[vlan_id].source_lines:
        config.vlans[vlan_id].source_lines.append(line_no)
    return config.vlans[vlan_id]

# interface name -> BaseInterface
def _get_or_create_interface(
    config: ParsedConfig, raw_name: str, line_no: int
) -> BaseInterface:
    # Key interfaces by canonical name so "Gi1/0/1" and "GigabitEthernet1/0/1"
    # resolve to the same object. On first sight, create the right subclass:
    # a Port-channel name -> PortChannelInterface, anything else ->
    # PhysicalInterface (deriving stack/module/port, e.g. Gi2/0/1 -> member 2).
    canonical = canonicalize_interface_name(raw_name)
    if canonical not in config.interfaces:
        iface_type, member, module, port = parse_interface_identity(raw_name)
        if iface_type == "Port-channel":
            po_id = parse_port_channel_id(raw_name)
            assert po_id is not None  # guaranteed when iface_type == Port-channel
            config.interfaces[canonical] = PortChannelInterface(
                name=raw_name.strip(),
                canonical_name=canonical,
                interface_type=iface_type,
                id=po_id,
                source_lines=[line_no],
            )
        else:
            config.interfaces[canonical] = PhysicalInterface(
                name=raw_name.strip(),
                canonical_name=canonical,
                interface_type=iface_type,
                stack_member=member,
                module=module,
                port=port,
                source_lines=[line_no],
            )
    else:
        if line_no not in config.interfaces[canonical].source_lines:
            config.interfaces[canonical].source_lines.append(line_no)
    return config.interfaces[canonical]

# port-channel id -> PortChannelInterface
def _get_or_create_port_channel(
    config: ParsedConfig, po_id: int, line_no: int
) -> PortChannelInterface:
    # A bundle is just the logical interface keyed "Port-channel<id>"; reuse the
    # interface upsert so there is a single creation path and single object.
    iface = _get_or_create_interface(config, f"Port-channel{po_id}", line_no)
    assert isinstance(iface, PortChannelInterface)
    return iface

# applies a single config body line to an interface
def _apply_interface_line(
    iface: BaseInterface,
    line: ScannedLine,
    context: str,
    config: ParsedConfig,
    explicit_allowed: set[str],
) -> None:
    text = line.text

    def unsupported(reason: str) -> None:
        iface.unsupported_lines.append(
            UnsupportedLine(
                line_number=line.line_number,
                context=context,
                text=text,
                reason=reason,
            )
        )

    m = RE_DESCRIPTION.match(text)
    if m:
        iface.description = m.group(1).strip()
        return

    if RE_SHUTDOWN.match(text):
        iface.shutdown = True
        return

    if RE_NO_SHUTDOWN.match(text):
        iface.shutdown = False
        return

    m = RE_SW_MODE.match(text)
    if m:
        iface.mode = m.group(1).lower()
        return

    m = RE_SW_ACCESS_VLAN.match(text)
    if m:
        iface.access_vlan = int(m.group(1))
        return

    m = RE_TRUNK_ALLOWED_REMOVE.match(text)
    if m:
        try:
            to_remove = parse_vlan_list(m.group(1))
        except ValueError as exc:
            unsupported(str(exc))
            return
        if iface.canonical_name not in explicit_allowed:
            config.warnings.append(
                f"Line {line.line_number}: trunk allowed vlan remove applied "
                f"on {iface.canonical_name} without prior explicit allowed list"
            )
        iface.trunk_allowed_vlans -= to_remove
        return

    m = RE_TRUNK_ALLOWED_ADD.match(text)
    if m:
        try:
            to_add = parse_vlan_list(m.group(1))
        except ValueError as exc:
            unsupported(str(exc))
            return
        if iface.canonical_name not in explicit_allowed:
            config.warnings.append(
                f"Line {line.line_number}: trunk allowed vlan add applied "
                f"on {iface.canonical_name} without prior explicit allowed list"
            )
        iface.trunk_allowed_vlans |= to_add
        return

    m = RE_TRUNK_ALLOWED.match(text)
    if m:
        try:
            iface.trunk_allowed_vlans = parse_vlan_list(m.group(1))
            explicit_allowed.add(iface.canonical_name)
        except ValueError as exc:
            unsupported(str(exc))
        return

    m = RE_TRUNK_NATIVE.match(text)
    if m:
        iface.trunk_native_vlan = int(m.group(1))
        return

    m = RE_CHANNEL_GROUP.match(text)
    if m:
        # channel-group is only meaningful on a physical member port; a bundle
        # cannot itself join a bundle.
        if isinstance(iface, PhysicalInterface):
            iface.channel_group = int(m.group(1))
            iface.channel_mode = m.group(2).lower()
        else:
            unsupported("channel-group is not valid on a logical interface")
        return

    if RE_NO_SWITCHPORT.match(text) or RE_IP_ADDRESS.match(text):
        iface.mode = "routed"
        return

    unsupported("unsupported or unhandled interface command")


# parse top-level lines against global expressions: hostname and stack provisioning
def _parse_global_block(config: ParsedConfig, block: ConfigBlock) -> None:
    for line in block.body:
        text = line.text

        m = RE_HOSTNAME.match(text)
        if m:
            config.hostname = m.group(1)
            continue

        m = RE_SWITCH_PROVISION.match(text)
        if m:
            member = _get_or_create_stack_member(config, int(m.group(1)))
            member.provision_model = m.group(2).strip()
            continue

        m = RE_SWITCH_PRIORITY.match(text)
        if m:
            member = _get_or_create_stack_member(config, int(m.group(1)))
            member.priority = int(m.group(2))
            continue

        config.unsupported_lines.append(
            UnsupportedLine(
                line_number=line.line_number,
                context="global",
                text=text,
                reason="unsupported or unhandled global command",
            )
        )


# parse a "vlan ..." block into one or more Vlan objects.
def _parse_vlan_block(config: ParsedConfig, block: ConfigBlock) -> None:
    assert block.header is not None
    header_match = RE_BLOCK_VLAN.match(block.header.text)
    if not header_match:
        return

    # expand the header VLAN list into sorted integer IDs
    try:
        vlan_ids = sorted(parse_vlan_list(header_match.group(1)))
    except ValueError as exc:
        config.warnings.append(
            f"Line {block.header.line_number}: failed to parse VLAN header: {exc}"
        )
        config.unsupported_lines.append(
            UnsupportedLine(
                line_number=block.header.line_number,
                context=block.context,
                text=block.header.text,
                reason=str(exc),
            )
        )
        return

    vlans = [_get_or_create_vlan(config, vid, block.header.line_number) for vid in vlan_ids]

    for line in block.body:
        m = RE_VLAN_NAME.match(line.text)
        if m:
            # Strip optional surrounding quotes, then apply the name to every
            # VLAN declared in this block's header.
            name = m.group(1).strip().strip('"')
            for vlan in vlans:
                vlan.name = name
            continue

        config.unsupported_lines.append(
            UnsupportedLine(
                line_number=line.line_number,
                context=block.context,
                text=line.text,
                reason="unsupported or unhandled vlan command",
            )
        )


# parse an "interface ..." or "interface range ..." block
def _parse_interface_block(
    config: ParsedConfig,
    block: ConfigBlock,
    is_range: bool,
    explicit_allowed: set[str],
) -> None:
    assert block.header is not None

    # pull the interface-name portion from the header using the matching pattern
    if is_range:
        header_match = RE_BLOCK_IFACE_RANGE.match(block.header.text)
    else:
        header_match = RE_BLOCK_IFACE.match(block.header.text)

    if not header_match:
        return

    # handle both a single name and full range notation and return canonical names
    try:
        interface_names = expand_interface_range(header_match.group(1).strip())
    except ValueError as exc:
        config.warnings.append(
            f"Line {block.header.line_number}: interface range expansion failed: {exc}"
        )
        config.unsupported_lines.append(
            UnsupportedLine(
                line_number=block.header.line_number,
                context=block.context,
                text=block.header.text,
                reason=str(exc),
            )
        )
        return

    for raw_name in interface_names:
        # create a PhysicalInterface or PortChannelInterface as necessary
        iface = _get_or_create_interface(
            config, raw_name, block.header.line_number
        )

        # apply every sub-command line in the block body to this interface
        for line in block.body:
            _apply_interface_line(
                iface, line, block.context, config, explicit_allowed
            )


# Post-pass: attach physical interfaces to their bundle via channel-group
def _link_port_channel_members(config: ParsedConfig) -> None:
    # take copy of config.interfaces
    physical = [
        iface
        for iface in config.interfaces.values()
        if isinstance(iface, PhysicalInterface)
    ]
    for iface in physical:
        channel_group = iface.channel_group
        if channel_group is None:
            continue
        po = _get_or_create_port_channel(
            config,
            channel_group,
            iface.source_lines[0] if iface.source_lines else 0,
        )
        if iface.canonical_name not in po.members:
            po.members.append(iface.canonical_name)

    # Sort + de-dup members so serialized output is deterministic
    for po in config.port_channels.values():
        po.members = sorted(set(po.members))


# Ensure a StackMember exists for every stack ID observed on a physical port
def _infer_stack_members(config: ParsedConfig) -> None:
    for iface in config.interfaces.values():
        if isinstance(iface, PhysicalInterface) and iface.stack_member is not None:
            _get_or_create_stack_member(config, iface.stack_member)


