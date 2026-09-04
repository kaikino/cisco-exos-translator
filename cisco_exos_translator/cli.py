# command-line entry point: one Cisco config in -> .map.json / .xsf / -acls/ /
# .stack-setup.txt next to it

from __future__ import annotations

import sys
from pathlib import Path

from .generator import build_default_mapping, generate_exos_config, generate_stack_setup
from .mapping import load_mapping, merge_mapping, write_mapping
from .models import ParsedConfig
from .parser import parse_cisco_config

USAGE = "Usage: main.py <cisco_config.cfg> [<cisco_config2.cfg> ...]"


# read and parse one config file; OSError carries the path for the CLI message
def parse_cisco_config_file(path: str) -> ParsedConfig:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise OSError(f"failed to read {path!r}: {exc}") from exc
    return parse_cisco_config(text)


# translate one parsed config, writing the outputs next to `path`;
# returns the number of warnings (also embedded in the .xsf header)
def translate_file(path: str, config: ParsedConfig) -> int:
    src = Path(path)

    # mapping: derived defaults overlaid with user edits from <name>.map.json;
    # the first run writes the defaults for the user to edit
    map_path = src.with_suffix(".map.json")
    defaults = build_default_mapping(config)
    notes: list[str] = []
    if map_path.exists():
        mapping, notes = merge_mapping(defaults, load_mapping(map_path))
    else:
        write_mapping(map_path, defaults)
        print(f"{map_path} written — edit it to customize, then re-run")
        mapping = defaults

    # the EXOS script goes next to the input as <name>.xsf
    exos_text, gen_warnings, pol_files = generate_exos_config(config, mapping, notes)
    out_path = src.with_suffix(".xsf")
    out_path.write_text(exos_text, encoding="utf-8")
    print(f"{path} -> {out_path}")

    # policy files go in <name>-acls/; the basename must stay equal to the
    # policy name the .xsf references, so they get their own directory
    if pol_files:
        acl_dir = Path(f"{src.with_suffix('')}-acls")
        acl_dir.mkdir(exist_ok=True)
        for pol_name, text in sorted(pol_files.items()):
            (acl_dir / f"{pol_name}.pol").write_text(text, encoding="utf-8")
        print(
            f"{path} -> {acl_dir}/ ({len(pol_files)} .pol file(s); "
            f"upload to the switch before loading the .xsf)"
        )

    # stacked source: also emit the stack bring-up runbook (the .xsf must be
    # loaded only after the stack exists)
    setup_text = generate_stack_setup(config, out_path.name)
    if setup_text:
        setup_path = src.with_suffix(".stack-setup.txt")
        setup_path.write_text(setup_text, encoding="utf-8")
        print(f"{path} -> {setup_path} (run before loading the .xsf)")

    # all warnings are embedded in the .xsf header; just summarize here
    total = len(config.warnings) + len(gen_warnings)
    untranslated = len(config.unsupported_lines) + sum(
        len(iface.unsupported_lines) for iface in config.interfaces.values()
    )
    if total or untranslated:
        parts = []
        if total:
            parts.append(f"{total} warning(s)")
        if untranslated:
            parts.append(f"{untranslated} untranslated line(s)")
        print(
            f"  {' + '.join(parts)} — see the WARNINGS header in {out_path}",
            file=sys.stderr,
        )
    return total


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        print(USAGE)
        return 1

    # every input is read and parsed first so a bad path fails the whole run
    # before anything is written
    try:
        configs = [(path, parse_cisco_config_file(path)) for path in argv]
    except OSError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    for path, config in configs:
        try:
            translate_file(path, config)
        except (OSError, ValueError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
    return 0
