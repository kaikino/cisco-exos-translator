# Cisco IOS/IOS-XE L2 config -> EXOS (.xsf) translator.

from __future__ import annotations

import sys
from pathlib import Path

from cisco_exos_translator.generator import generate_exos_config
from cisco_exos_translator.models import ParsedConfig
from cisco_exos_translator.parser import (
    _infer_stack_members,
    _link_port_channel_members,
    _parse_global_block,
    _parse_interface_block,
    _parse_vlan_block,
)
from cisco_exos_translator.scanner import scan_config
from cisco_exos_translator.validation import validate_parsed_config


def parse_cisco_config(text: str) -> ParsedConfig:
    # Parse Cisco IOS/IOS-XE running-config text into a ParsedConfig IR.
    # Orchestrates: scanner → parser passes → post-processing → validation.
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

    # Pass 4: validation
    validation_warnings = validate_parsed_config(config)
    config.warnings.extend(validation_warnings)

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
    # Translate Cisco config(s) to EXOS .xsf files (one per input).
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

    for path, config in sorted(configs.items()):
        # Generate the EXOS script and write it next to the input as <name>.xsf.
        exos_text, gen_warnings = generate_exos_config(config)
        out_path = Path(path).with_suffix(".xsf")
        out_path.write_text(exos_text, encoding="utf-8")
        print(f"{path} -> {out_path}")

        # All warnings are embedded in the .xsf header; just summarize here.
        total = len(config.warnings) + len(gen_warnings)
        if total:
            print(
                f"  {total} warning(s) — see the WARNINGS header in {out_path}",
                file=sys.stderr,
            )

    return 0


if __name__ == "__main__":
    sys.exit(main())
