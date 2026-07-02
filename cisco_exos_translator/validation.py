# Validation pass: cross-reference checks that produce warnings.

from __future__ import annotations

from .models import BaseInterface, ParsedConfig, PortChannelInterface


def validate_parsed_config(config: ParsedConfig) -> list[str]:
    # Validate cross-references; return warning strings.
    warnings: list[str] = []
    defined_vlans = set(config.vlans.keys())

    def check_interface(name: str, iface: BaseInterface, context_label: str) -> None:
        # Routed (L3) interface
        if iface.mode == "routed":
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

    # Check all interfaces (physical and logical)
    for name, iface in sorted(config.interfaces.items()):
        label = "Port-channel" if isinstance(iface, PortChannelInterface) else "Interface"
        check_interface(name, iface, label)

    # Bundles must have members
    for po_id, po in sorted(config.port_channels.items()):
        if not po.members:
            warnings.append(
                f"Port-channel{po_id}: Port-channel exists but has no member interfaces"
            )

    return warnings
