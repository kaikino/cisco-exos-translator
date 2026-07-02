# Test Cisco L2 config parser.

from __future__ import annotations

import sys
from pathlib import Path

from cisco_exos_translator.models import ParsedConfig
from cisco_exos_translator.parser import (
    _infer_stack_members,
    _link_port_channel_members,
    _parse_global_block,
    _parse_interface_block,
    _parse_vlan_block,
)
from cisco_exos_translator.scanner import scan_config


def parse_cisco_config(text: str) -> ParsedConfig:
    # Parse Cisco IOS/IOS-XE running-config text into a ParsedConfig IR.
    # Orchestrates: scanner → parser passes → post-processing
    config = ParsedConfig()

    # Pass 1: scanner
    blocks = scan_config(text)

    # Pass 2: parser dispatch (explicit_allowed shared across blocks)
    explicit_allowed: set[str] = set()
    for block in blocks:
        if block.kind == "global":
            _parse_global_block(config, block)
        elif block.kind == "vlan":
            _parse_vlan_block(config, block)
        elif block.kind == "interface":
            _parse_interface_block(config, block, is_range=False, explicit_allowed=explicit_allowed)
        elif block.kind == "interface_range":
            _parse_interface_block(config, block, is_range=True, explicit_allowed=explicit_allowed)

    # Pass 3: post-processing
    _link_port_channel_members(config)
    _infer_stack_members(config)

    return config


def parse_cisco_config_file(path: str) -> ParsedConfig:
    # Read a config file and parse it.
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise OSError(f"failed to read {path!r}: {exc}") from exc
    return parse_cisco_config(text)


def parse_multiple_files(paths: list[str]) -> dict[str, ParsedConfig]:
    # Parse multiple config files; keys are file paths.
    return {path: parse_cisco_config_file(path) for path in paths}


def main(argv: list[str] | None = None) -> int:
    # Parse Cisco config(s) and print results.
    if argv is None:
        argv = sys.argv[1:]

    if not argv:
        print("Usage: main.py <cisco_config.cfg> [<cisco_config2.cfg> ...]")
        return 1

    try:
        configs = parse_multiple_files(argv)
    except OSError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    # Print parse results
    for path, config in sorted(configs.items()):
        print(f"\n{path}:")
        print(f"  Hostname: {config.hostname}")
        print(f"  VLANs: {sorted(config.vlans.keys())}")
        print(f"  Interfaces: {len(config.interfaces)}")
        print(f"  Port-channels: {list(config.port_channels.keys())}")
        print(f"  Stack members: {list(config.stack_members.keys())}")

        if config.warnings:
            print(f"  Warnings:")
            for w in config.warnings:
                print(f"    - {w}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
