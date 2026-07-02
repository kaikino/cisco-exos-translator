# Intermediate representation dataclasses for parsed Cisco L2 config.

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class UnsupportedLine:
    # a config line that could not be parsed or is outside L2 scope
    line_number: int
    context: str
    text: str
    reason: str


@dataclass
class Vlan:
    # a VLAN definition from 'vlan <id>' blocks
    vlan_id: int
    name: Optional[str] = None
    source_lines: list[int] = field(default_factory=list)


@dataclass
class BaseInterface:
    # switchport/L2 settings common to physical ports and port-channels.
    # these map directly to EXOS VLAN membership on the port (physical) or on
    # the bundle's master port (port-channel).
    name: str  # raw name as seen in the config
    canonical_name: str  # normalized, e.g. "GigabitEthernet1/0/1"
    interface_type: str  # canonical type token, e.g. "GigabitEthernet"
    description: Optional[str] = None
    mode: Optional[str] = None  # "access", "trunk", "routed", or None
    access_vlan: Optional[int] = None
    trunk_allowed_vlans: set[int] = field(default_factory=set)
    trunk_native_vlan: Optional[int] = None
    shutdown: bool = False
    source_lines: list[int] = field(default_factory=list)
    unsupported_lines: list[UnsupportedLine] = field(default_factory=list)


@dataclass
class PhysicalInterface(BaseInterface):
    # a physical port, with stack numbering and optional LAG membership
    # stack-style numbering parsed from the name (Gi2/0/1 -> member 2, mod 0,
    # port 1); EXOS port strings are derived from these
    stack_member: Optional[int] = None
    module: Optional[int] = None
    port: Optional[int] = None
    channel_group: Optional[int] = None # which bundle this port joins
    channel_mode: Optional[str] = None


@dataclass
class PortChannelInterface(BaseInterface):
    # a logical EtherChannel / Port-channel bundle. holds both the bundle's L2
    # config (inherited) and its aggregation (members). EXOS generation reads
    # each member's channel_mode to decide LACP vs static.
    id: int = -1
    members: list[str] = field(default_factory=list)  # canonical member names

    def __post_init__(self) -> None:
        if self.id < 0:
            raise ValueError("PortChannelInterface requires a non-negative id")


# Stack member provisioning from global config
@dataclass
class StackMember:

    member_id: int
    provision_model: Optional[str] = None
    priority: Optional[int] = None


# Top-level normalized representation of a Cisco L2 config
@dataclass
class ParsedConfig:
    hostname: Optional[str] = None
    vlans: dict[int, Vlan] = field(default_factory=dict)
    # all interfaces (physical and port-channel) keyed by canonical name
    interfaces: dict[str, BaseInterface] = field(default_factory=dict)
    stack_members: dict[int, StackMember] = field(default_factory=dict)
    unsupported_lines: list[UnsupportedLine] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    # Physical ports only, keyed by canonical name (sorted)
    @property
    def physical_interfaces(self) -> dict[str, PhysicalInterface]:
        return {
            name: iface
            for name, iface in sorted(self.interfaces.items())
            if isinstance(iface, PhysicalInterface)
        }

    # ort-channel bundles, keyed by bundle id (sorted)
    @property
    def port_channels(self) -> dict[int, PortChannelInterface]:
        bundles = (
            iface
            for iface in self.interfaces.values()
            if isinstance(iface, PortChannelInterface)
        )
        return {po.id: po for po in sorted(bundles, key=lambda p: p.id)}


# a single non-empty config line with metadata from the scanner
@dataclass
class ScannedLine:
    line_number: int
    indent: int
    text: str


# a top-level or nested configuration block extracted by the scanner
@dataclass
class ConfigBlock:
    kind: str  # "global" | "vlan" | "interface" | "interface_range"
    header: Optional[ScannedLine]
    body: list[ScannedLine]
    context: str
