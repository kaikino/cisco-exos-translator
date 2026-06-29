# scanner pass to produce ConfigBlocks

from __future__ import annotations

import re

from .models import ConfigBlock, ScannedLine

# block header patterns
#   e.g. "vlan 10"  /  "vlan 10,20,30-40"            -> capture "10,20,30-40"
RE_BLOCK_VLAN = re.compile(r"^vlan\s+(.+)$", re.IGNORECASE)
#   e.g. "interface range GigabitEthernet1/0/1-24"   -> capture "GigabitEthernet1/0/1-24"
RE_BLOCK_IFACE_RANGE = re.compile(r"^interface\s+range\s+(.+)$", re.IGNORECASE)
#   e.g. "interface GigabitEthernet1/0/1"            -> capture "GigabitEthernet1/0/1"
RE_BLOCK_IFACE = re.compile(r"^interface\s+(.+)$", re.IGNORECASE)

#   bare "!" separator line
RE_COMMENT_LINE = re.compile(r"^\s*!\s*$")


def scan_config(text: str) -> list[ConfigBlock]:
    blocks: list[ConfigBlock] = []
    global_lines: list[ScannedLine] = []

    raw_lines = text.splitlines()
    scanned: list[ScannedLine] = []

    # tokenize raw text as ScannedLine records
    for idx, raw in enumerate(raw_lines, start=1):
        if RE_COMMENT_LINE.match(raw):  # drop "!" line
            continue
        stripped = raw.rstrip()
        if not stripped.strip(): # drop blank line
            continue
        indent = len(stripped) - len(stripped.lstrip())
        scanned.append(
            ScannedLine(line_number=idx, indent=indent, text=stripped.strip())
        )

    # group scanned lines into blocks sequentially
    i = 0
    while i < len(scanned):
        line = scanned[i]

        # treat indented line without a header as global
        if line.indent != 0:
            global_lines.append(line)
            i += 1
            continue

        # check if line is a header
        vlan_match = RE_BLOCK_VLAN.match(line.text)
        if_range_match = RE_BLOCK_IFACE_RANGE.match(line.text)
        iface_match = None if if_range_match else RE_BLOCK_IFACE.match(line.text)
        if vlan_match or if_range_match or iface_match:
            # flush pending global lines
            if global_lines:
                blocks.append(
                    ConfigBlock(
                        kind="global",
                        header=None,
                        body=list(global_lines),
                        context="global",
                    )
                )
                global_lines = []

            # determine block type
            if vlan_match:
                kind = "vlan"
                context = line.text
            elif if_range_match:
                kind = "interface_range"
                context = line.text
            else:
                kind = "interface"
                context = line.text

            # consume successive indented lines as this block's body
            body: list[ScannedLine] = []
            i += 1
            while i < len(scanned) and scanned[i].indent > 0:
                body.append(scanned[i])
                i += 1

            blocks.append(
                ConfigBlock(kind=kind, header=line, body=body, context=context)
            )
        else:
            # treat 0-indent non-header line as global
            global_lines.append(line)
            i += 1

    # flush any trailing global lines
    if global_lines:
        blocks.append(
            ConfigBlock(
                kind="global",
                header=None,
                body=list(global_lines),
                context="global",
            )
        )

    return blocks
