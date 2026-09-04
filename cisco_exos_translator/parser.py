# parser pass: populate dataclasses from scanned config blocks, then run the
# post-processing and validation passes (parse_cisco_config is the entry point)

from __future__ import annotations

import re

from .helpers import (
    canonicalize_interface_name,
    expand_interface_range,
    interface_sort_key,
    netmask_to_cidr,
    parse_interface_identity,
    parse_port_channel_id,
    parse_vlan_list,
    wildcard_to_cidr,
)
from .models import (
    AclRule,
    BaseInterface,
    ConfigBlock,
    MonitorSession,
    ParsedConfig,
    PhysicalInterface,
    PortChannelInterface,
    ScannedLine,
    StackMember,
    UnsupportedLine,
    Vlan,
)
from .scanner import (
    RE_BLOCK_ACL,
    RE_BLOCK_IFACE,
    RE_BLOCK_IFACE_RANGE,
    RE_BLOCK_VLAN,
    scan_config,
)
from .validation import validate_parsed_config

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
#   e.g. "ip access-group SERVERS-IN in"        -> capture (1) name (2) direction
RE_ACCESS_GROUP = re.compile(
    r"^ip\s+access-group\s+(\S+)\s+(in|out)$", re.IGNORECASE
)
#   e.g. "access-list 100 permit tcp any any eq 22" -> capture (1) number (2) ACE
RE_NUMBERED_ACL = re.compile(r"^access-list\s+(\d+)\s+(.+)$", re.IGNORECASE)
#   dotted-quad token, e.g. "10.1.0.0"
RE_DOTTED_QUAD = re.compile(r"^\d+\.\d+\.\d+\.\d+$")
#   e.g. "no switchport"  -> port becomes routed (L3), i.e. out of L2 scope
RE_NO_SWITCHPORT = re.compile(r"^no\s+switchport$", re.IGNORECASE)
#   e.g. "ip address 10.0.0.1 255.255.255.0 {secondary}" -> capture addr, mask
RE_IP_ADDRESS_FULL = re.compile(
    r"^ip\s+address\s+(\d+\.\d+\.\d+\.\d+)\s+(\d+\.\d+\.\d+\.\d+)(\s+secondary)?$",
    re.IGNORECASE,
)
#   e.g. "ip address dhcp" and other forms -> still marks the port as routed
RE_IP_ADDRESS = re.compile(r"^ip\s+address\s+", re.IGNORECASE)
#   e.g. "ip route 0.0.0.0 0.0.0.0 10.0.0.1" -> dest, mask, next-hop (IP only)
RE_IP_ROUTE = re.compile(
    r"^ip\s+route\s+(\d+\.\d+\.\d+\.\d+)\s+(\d+\.\d+\.\d+\.\d+)\s+(\d+\.\d+\.\d+\.\d+)$",
    re.IGNORECASE,
)
#   "ip routing" is realized per-VLAN via "enable ipforwarding vlan", so the
#   global line is consumed without output
RE_IP_ROUTING = re.compile(r"^ip\s+routing$", re.IGNORECASE)
#   e.g. "monitor session 1 source interface Gi1/0/1 - 2 , Gi1/0/5 rx"
#   -> capture (1) session id (2) the rest of the line
RE_MONITOR_SESSION = re.compile(r"^monitor\s+session\s+(\d+)\s+(.+)$", re.IGNORECASE)
#   the rest of a source line: "interface <list> {rx|tx|both}" or
#   "vlan <list> {rx|tx|both}"; "remote vlan" (RSPAN) deliberately not matched
RE_MONITOR_SOURCE = re.compile(
    r"^source\s+(interface|vlan)\s+(.+?)(?:\s+(rx|tx|both))?$", re.IGNORECASE
)
#   the rest of a destination line: "interface <list> {encapsulation replicate}"
RE_MONITOR_DEST = re.compile(
    r"^destination\s+interface\s+(.+?)(\s+encapsulation\s+replicate)?$",
    re.IGNORECASE,
)
#   the rest of a filter line: "filter ip access-group <name|number>" (FSPAN)
RE_MONITOR_FILTER = re.compile(
    r"^filter\s+ip\s+access-group\s+(\S+)$", re.IGNORECASE
)


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

    m = RE_ACCESS_GROUP.match(text)
    if m:
        if m.group(2).lower() == "in":
            iface.access_group_in = m.group(1)
        else:
            unsupported("egress ACL (ip access-group out) is not supported")
        return

    m = RE_IP_ADDRESS_FULL.match(text)
    if m:
        iface.mode = "routed"
        if m.group(3):
            unsupported("secondary IP address is not supported")
            return
        cidr = netmask_to_cidr(m.group(1), m.group(2))
        if cidr is None:
            unsupported("invalid netmask")
            return
        iface.ip_address = cidr
        return

    if RE_NO_SWITCHPORT.match(text) or RE_IP_ADDRESS.match(text):
        iface.mode = "routed"
        return

    unsupported("unsupported or unhandled interface command")


# consume one address spec from the token list: any | host A | A wildcard | A
# returns a CIDR string or None (= any); raises ValueError on unsupported forms
def _take_acl_addr(toks: list[str], allow_bare: bool) -> str | None:
    if not toks:
        raise ValueError("missing address")
    t = toks.pop(0)
    if t.lower() == "any":
        return None
    if t.lower() == "host":
        if not toks or not RE_DOTTED_QUAD.match(toks[0]):
            raise ValueError("invalid host address")
        return f"{toks.pop(0)}/32"
    if not RE_DOTTED_QUAD.match(t):
        raise ValueError(f"unsupported address token '{t}'")
    # "A wildcard" when the next token is also dotted-quad; bare A (standard) = host
    if toks and RE_DOTTED_QUAD.match(toks[0]):
        cidr = wildcard_to_cidr(t, toks.pop(0))
        if cidr is None:
            raise ValueError("non-contiguous wildcard mask")
        return None if cidr.endswith("/0") else cidr
    if allow_bare:
        return f"{t}/32"
    raise ValueError("missing wildcard mask")


# consume an optional "eq N" / "range N M" port spec; raises on other operators
def _take_acl_port(toks: list[str]) -> tuple[int, int] | None:
    if not toks:
        return None
    op = toks[0].lower()
    if op in ("gt", "lt", "neq"):
        raise ValueError(f"port operator '{op}' not supported")
    if op == "eq":
        if len(toks) < 2 or not toks[1].isdigit():
            raise ValueError("only numeric ports are supported")
        toks.pop(0)
        p = int(toks.pop(0))
        return (p, p)
    if op == "range":
        if len(toks) < 3 or not (toks[1].isdigit() and toks[2].isdigit()):
            raise ValueError("only numeric port ranges are supported")
        toks.pop(0)
        return (int(toks.pop(0)), int(toks.pop(0)))
    return None


# parse one ACE line into an AclRule; raises ValueError for anything outside
# the supported subset (caller reports it as an unsupported line, never dropped)
def _parse_ace(text: str, extended: bool, line_no: int) -> AclRule:
    toks = text.split()
    if toks and toks[0].isdigit():  # optional sequence number
        toks.pop(0)
    if not toks:
        raise ValueError("empty ACE")

    if toks[0].lower() == "remark":
        return AclRule(action="remark", remark=" ".join(toks[1:]), line_number=line_no)

    action = toks.pop(0).lower()
    if action not in ("permit", "deny"):
        raise ValueError(f"unsupported ACL command '{action}'")
    rule = AclRule(action=action, line_number=line_no)

    if extended:
        if not toks:
            raise ValueError("missing protocol")
        proto = toks.pop(0).lower()
        if proto in ("tcp", "udp", "icmp"):
            rule.protocol = proto
        elif proto.isdigit():
            rule.protocol = proto
        elif proto != "ip":
            raise ValueError(f"protocol '{proto}' not supported")
        rule.source = _take_acl_addr(toks, allow_bare=False)
        if rule.protocol in ("tcp", "udp"):
            rule.source_port = _take_acl_port(toks)
        rule.destination = _take_acl_addr(toks, allow_bare=False)
        if rule.protocol in ("tcp", "udp"):
            rule.destination_port = _take_acl_port(toks)
    else:
        rule.source = _take_acl_addr(toks, allow_bare=True)

    if toks:  # never partially translate an ACE: leftover tokens reject the line
        raise ValueError(f"unsupported token '{toks[0]}'")
    return rule


# session id -> MonitorSession
def _get_or_create_monitor_session(
    config: ParsedConfig, session_id: int, line_no: int
) -> MonitorSession:
    if session_id not in config.monitor_sessions:
        config.monitor_sessions[session_id] = MonitorSession(session_id=session_id)
    sess = config.monitor_sessions[session_id]
    if line_no not in sess.source_lines:
        sess.source_lines.append(line_no)
    return sess


# expand a SPAN interface list ("Gi1/0/1 - 2 , Gi1/0/5") and reject anything
# that is not a well-formed physical port or Port-channel (raises ValueError)
def _expand_monitor_interfaces(text: str) -> list[str]:
    names = expand_interface_range(text)
    for name in names:
        iface_type, member, _module, port = parse_interface_identity(name)
        if iface_type == "Port-channel":
            continue
        if member is None or port is None:
            raise ValueError(f"unrecognized interface '{name}'")
    return names


# apply one "monitor session <id> <rest>" line; raises ValueError for anything
# outside local SPAN (RSPAN/ERSPAN, filters) so the caller reports it
def _apply_monitor_line(
    config: ParsedConfig, session_id: int, rest: str, line: ScannedLine
) -> None:
    m = RE_MONITOR_SOURCE.match(rest)
    if m:
        direction = (m.group(3) or "both").lower()  # Cisco default is both
        if m.group(1).lower() == "interface":
            names = _expand_monitor_interfaces(m.group(2))
            sess = _get_or_create_monitor_session(config, session_id, line.line_number)
            for name in names:
                # register the port so it gets a mapping entry / EXOS number
                _get_or_create_interface(config, name, line.line_number)
                if (name, direction) not in sess.source_ports:
                    sess.source_ports.append((name, direction))
        else:
            # allow IOS's spaced ranges ("100 - 200") before list parsing
            vids = sorted(parse_vlan_list(re.sub(r"\s*-\s*", "-", m.group(2))))
            sess = _get_or_create_monitor_session(config, session_id, line.line_number)
            for vid in vids:
                if (vid, direction) not in sess.source_vlans:
                    sess.source_vlans.append((vid, direction))
        return

    m = RE_MONITOR_DEST.match(rest)
    if m:
        names = _expand_monitor_interfaces(m.group(1))
        sess = _get_or_create_monitor_session(config, session_id, line.line_number)
        for name in names:
            _get_or_create_interface(config, name, line.line_number)
            if name not in sess.destination_ports:
                sess.destination_ports.append(name)
        if m.group(2):
            sess.encapsulation_replicate = True
        return

    m = RE_MONITOR_FILTER.match(rest)
    if m:
        sess = _get_or_create_monitor_session(config, session_id, line.line_number)
        if sess.filter_acl is not None and sess.filter_acl != m.group(1):
            raise ValueError("multiple SPAN filter ACLs on one session")
        sess.filter_acl = m.group(1)
        return

    low = rest.lower()
    if "remote" in low.split():
        raise ValueError("RSPAN (remote vlan) is not supported")
    if low.startswith("type"):
        raise ValueError("only local SPAN is supported (no ERSPAN/capture types)")
    if low.startswith("filter"):
        raise ValueError("only 'filter ip access-group' is supported")
    raise ValueError("unsupported monitor session command")


# parse an "ip access-list standard|extended <name>" block
def _parse_acl_block(config: ParsedConfig, block: ConfigBlock) -> None:
    assert block.header is not None
    header_match = RE_BLOCK_ACL.match(block.header.text)
    if not header_match:
        return
    extended = header_match.group(1).lower() == "extended"
    rules = config.acls.setdefault(header_match.group(2), [])

    for line in block.body:
        try:
            rules.append(_parse_ace(line.text, extended, line.line_number))
        except ValueError as exc:
            config.unsupported_lines.append(
                UnsupportedLine(
                    line_number=line.line_number,
                    context=block.context,
                    text=line.text,
                    reason=str(exc),
                )
            )


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

        if RE_IP_ROUTING.match(text):
            continue  # realized per-VLAN via "enable ipforwarding vlan"

        if text.lower() == "end":
            continue  # running-config terminator, not a command

        m = RE_MONITOR_SESSION.match(text)
        if m:
            try:
                _apply_monitor_line(config, int(m.group(1)), m.group(2).strip(), line)
            except ValueError as exc:
                config.unsupported_lines.append(
                    UnsupportedLine(
                        line_number=line.line_number,
                        context="global",
                        text=text,
                        reason=str(exc),
                    )
                )
            continue

        m = RE_IP_ROUTE.match(text)
        if m:
            cidr = netmask_to_cidr(m.group(1), m.group(2))
            if cidr is not None:
                config.static_routes.append((cidr, m.group(3)))
                continue
            # invalid mask falls through to the unsupported-line report

        m = RE_NUMBERED_ACL.match(text)
        if m:
            num = int(m.group(1))
            # standard 1-99/1300-1999, extended 100-199/2000-2699
            standard = 1 <= num <= 99 or 1300 <= num <= 1999
            extended = 100 <= num <= 199 or 2000 <= num <= 2699
            if standard or extended:
                try:
                    config.acls.setdefault(str(num), []).append(
                        _parse_ace(m.group(2), extended, line.line_number)
                    )
                    continue
                except ValueError as exc:
                    config.unsupported_lines.append(
                        UnsupportedLine(
                            line_number=line.line_number,
                            context="global",
                            text=text,
                            reason=str(exc),
                        )
                    )
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

    # Sort (natural port order) + de-dup members so output is deterministic
    # and the first member is the lowest-numbered port (the default LAG master)
    for po in config.port_channels.values():
        po.members = sorted(set(po.members), key=interface_sort_key)


# Ensure a StackMember exists for every stack ID observed on a physical port
def _infer_stack_members(config: ParsedConfig) -> None:
    for iface in config.interfaces.values():
        if isinstance(iface, PhysicalInterface) and iface.stack_member is not None:
            _get_or_create_stack_member(config, iface.stack_member)


# Parse Cisco IOS/IOS-XE running-config text into a ParsedConfig IR.
# Orchestrates: scanner -> per-block parsers -> post-processing -> validation.
def parse_cisco_config(text: str) -> ParsedConfig:
    config = ParsedConfig()

    # Pass 1: scanner (text -> blocks)
    blocks = scan_config(text)

    # Pass 2: block parsers (explicit_allowed is shared across blocks so a
    # later "add/remove" can tell whether the trunk list was set explicitly)
    explicit_allowed: set[str] = set()
    for block in blocks:
        if block.kind == "global":
            _parse_global_block(config, block)
        elif block.kind == "vlan":
            _parse_vlan_block(config, block)
        elif block.kind == "acl":
            _parse_acl_block(config, block)
        elif block.kind == "interface":
            _parse_interface_block(config, block, is_range=False, explicit_allowed=explicit_allowed)
        elif block.kind == "interface_range":
            _parse_interface_block(config, block, is_range=True, explicit_allowed=explicit_allowed)

    # Pass 3: post-processing
    _link_port_channel_members(config)
    _infer_stack_members(config)

    # Pass 4: cross-reference validation
    config.warnings.extend(validate_parsed_config(config))

    return config

