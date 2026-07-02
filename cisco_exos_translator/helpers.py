# VLAN list and interface name parsing helpers

from __future__ import annotations

import re
from typing import Optional

# Canonical long names for each interface type we recognize.
INTERFACE_TYPE_NAMES = [
    "TwentyFiveGigE",
    "TenGigabitEthernet",
    "FortyGigabitEthernet",
    "HundredGigE",
    "GigabitEthernet",
    "FastEthernet",
    "Port-channel",
]

# Abbreviated prefix -> canonical long name
INTERFACE_ABBREV_MAP: list[tuple[str, str]] = [
    ("Twe", "TwentyFiveGigE"),
    ("Te", "TenGigabitEthernet"),
    ("Fo", "FortyGigabitEthernet"),
    ("Hu", "HundredGigE"),
    ("Gi", "GigabitEthernet"),
    ("Fa", "FastEthernet"),
    ("Po", "Port-channel"),
]

# One token of a Cisco VLAN list: a lone id "20" or an inclusive range "30-32"
RE_VLAN_TOKEN = re.compile(r"^(\d+)(?:-(\d+))?$")

# "Port-channel1", "port-channel 1", "Po1" all collapse to "Port-channel<id>"
RE_PORT_CHANNEL = re.compile(r"^(?:port-channel|po)\s*(\d+)$", re.IGNORECASE)

# A canonical physical interface: <Type><stack>/<module>/<port>
# e.g. "GigabitEthernet1/0/1" -> ("GigabitEthernet", "1", "0", "1")
RE_PHYSICAL_IDENTITY = re.compile(
    r"^(" + "|".join(INTERFACE_TYPE_NAMES) + r")(\d+)/(\d+)/(\d+)$",
    re.IGNORECASE,
)

# One precompiled "<full type name><trailing>" matcher per type, reused across
# calls instead of recompiling inside the hot path.
_RE_TYPE_PREFIXES = [
    (name, re.compile(rf"^{re.escape(name)}(\d.*)$", re.IGNORECASE))
    for name in INTERFACE_TYPE_NAMES
]
_RE_ABBREV_PREFIXES = [
    (long_name, re.compile(rf"^{re.escape(abbrev)}(\d.*)$", re.IGNORECASE))
    for abbrev, long_name in INTERFACE_ABBREV_MAP
]

# Split an interface-range list on the commas
RE_RANGE_SEPARATOR = re.compile(r",\s*(?=[A-Za-z])")
# A single "<prefix>/<start>-<end>" port range
RE_PORT_RANGE = re.compile(r"^(.+/)(\d+)\s*-\s*(\d+)$")


# Parse a Cisco VLAN list string into a set of VLAN IDs
def parse_vlan_list(text: str) -> set[int]:
    result: set[int] = set()
    for token in text.split(","):
        token = token.strip()
        if not token:
            continue
        match = RE_VLAN_TOKEN.match(token)  # "30" -> (30, None); "30-32" -> (30, 32)
        if not match:
            raise ValueError(f"invalid VLAN token: {token!r}")
        start = int(match.group(1))
        end = int(match.group(2)) if match.group(2) else start
        if start > end:
            raise ValueError(f"invalid VLAN range: {token!r}")
        if start < 1 or end > 4094:
            raise ValueError(f"VLAN ID out of range 1-4094: {token!r}")
        result.update(range(start, end + 1))
    if not result:
        raise ValueError(f"empty VLAN list: {text!r}")
    return result


# Normalize an interface name to canonical Cisco form
# e.g. "Gi1/0/1" -> "GigabitEthernet1/0/1"
def canonicalize_interface_name(name: str) -> str:
    name = name.strip()
    if name.lower().startswith("interface "):
        name = name[len("interface ") :].strip()

    po_match = RE_PORT_CHANNEL.match(name)
    if po_match:
        return f"Port-channel{po_match.group(1)}"

    # Full type names first, then abbreviations, so "Te..." resolves to
    # TenGigabitEthernet rather than being left untouched
    for type_name, pattern in _RE_TYPE_PREFIXES:
        match = pattern.match(name)
        if match:
            return f"{type_name}{match.group(1)}"

    for long_name, pattern in _RE_ABBREV_PREFIXES:
        match = pattern.match(name)
        if match:
            return f"{long_name}{match.group(1)}"

    return name


# Extract interface type and stack/module/port numbering
# e.g. GigabitEthernet1/0/1 -> ("GigabitEthernet", 1, 0, 1)
def parse_interface_identity(
    name: str,
) -> tuple[str, Optional[int], Optional[int], Optional[int]]:
    canonical = canonicalize_interface_name(name)

    if RE_PORT_CHANNEL.match(canonical):
        return ("Port-channel", None, None, None)

    phys_match = RE_PHYSICAL_IDENTITY.match(canonical)
    if phys_match:
        # group(1) is already canonical case because the name was canonicalized
        return (
            phys_match.group(1),
            int(phys_match.group(2)),
            int(phys_match.group(3)),
            int(phys_match.group(4)),
        )

    # Unknown shape (no stack/module/port): fall back to the leading type token
    type_match = re.match(r"^([A-Za-z-]+)", canonical)
    return (type_match.group(1) if type_match else canonical, None, None, None)


# Return numeric Port-channel ID from a canonical or raw name, else None
def parse_port_channel_id(name: str) -> Optional[int]:
    match = RE_PORT_CHANNEL.match(canonicalize_interface_name(name))
    return int(match.group(1)) if match else None


# Expand interface range notation into sorted, normalized names
def expand_interface_range(text: str) -> list[str]:
    text = text.strip()
    if not text:
        raise ValueError("empty interface range")

    result: list[str] = []
    seen: set[str] = set()

    def add(canonical: str) -> None:
        if canonical not in seen:
            seen.add(canonical)
            result.append(canonical)

    for fragment in RE_RANGE_SEPARATOR.split(text):
        fragment = fragment.strip()
        if not fragment:
            continue
        range_match = RE_PORT_RANGE.match(fragment)
        if range_match:
            base = range_match.group(1).rstrip("/")
            start_port = int(range_match.group(2))
            end_port = int(range_match.group(3))
            if start_port > end_port:
                raise ValueError(f"invalid port range in {fragment!r}")
            for port in range(start_port, end_port + 1):
                add(canonicalize_interface_name(f"{base}/{port}"))
        else:
            add(canonicalize_interface_name(fragment))

    if not result:
        raise ValueError(f"no interfaces expanded from {text!r}")

    return sorted(result)
