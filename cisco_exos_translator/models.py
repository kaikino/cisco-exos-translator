# Intermediate representation dataclasses for parsed Cisco L2 config.

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


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
