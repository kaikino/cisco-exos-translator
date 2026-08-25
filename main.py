# Cisco IOS/IOS-XE L2 config -> EXOS (.xsf) translator.

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from cisco_exos_translator.findings import (
    FindingsCollector,
    Severity,
    SourceRef,
    TranslationStatus,
)
from cisco_exos_translator.findings_builder import build_findings
from cisco_exos_translator.generator import (
    build_default_mapping,
    generate_exos_config,
    generate_stack_setup,
)
from cisco_exos_translator.mapping import load_mapping, merge_mapping, write_mapping
from cisco_exos_translator.models import ParsedConfig
from cisco_exos_translator.parser import (
    _infer_stack_members,
    _link_port_channel_members,
    _parse_acl_block,
    _parse_global_block,
    _parse_interface_block,
    _parse_vlan_block,
)
from cisco_exos_translator.scanner import scan_config
from cisco_exos_translator.validation import validate_parsed_config


def parse_cisco_config(
    text: str, collector: FindingsCollector | None = None
) -> ParsedConfig:
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
        elif block.kind == "acl":
            _parse_acl_block(config, block)
        elif block.kind == "interface":
            _parse_interface_block(config, block, is_range=False, explicit_allowed=explicit_allowed)
        elif block.kind == "interface_range":
            _parse_interface_block(config, block, is_range=True, explicit_allowed=explicit_allowed)

    # Pass 3: post-processing
    _link_port_channel_members(config)
    _infer_stack_members(config)

    # Everything on config.warnings at this point came from the parser; the
    # validation pass appends its own below.
    if collector is not None:
        for warning in config.warnings:
            collector.record(
                code="input.parser_warning",
                category="input",
                feature="general",
                status=TranslationStatus.WARNING,
                severity=Severity.WARNING,
                message=warning,
                reason="the parser could not apply a command as written",
                source=SourceRef(),
            )

    # Pass 4: validation
    validation_warnings = validate_parsed_config(config, collector)
    config.warnings.extend(validation_warnings)

    return config


def parse_cisco_config_file(
    path: str, collector: FindingsCollector | None = None
) -> ParsedConfig:
    # Read a config file and parse it.
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise OSError(f"failed to read {path!r}: {exc}") from exc
    return parse_cisco_config(text, collector)


def parse_multiple_files(paths: list[str]) -> dict[str, ParsedConfig]:
    # Parse multiple config files; keys are file paths.
    return {path: parse_cisco_config_file(path) for path in paths}


def _out_path(path: str, suffix: str, output_dir: str | None) -> Path:
    # Artifact paths keep the historical "<name><suffix>" naming; --output-dir
    # only changes the directory they land in.
    named = Path(path).with_suffix(suffix)
    return Path(output_dir) / named.name if output_dir else named


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="Translate Cisco IOS/IOS-XE configs into EXOS .xsf scripts "
                    "plus structured migration findings.",
    )
    parser.add_argument("configs", nargs="*", metavar="cisco_config.cfg",
                        help="one or more Cisco running-config files")
    parser.add_argument("-o", "--output-dir", metavar="DIR",
                        help="write artifacts here instead of next to each input")
    parser.add_argument("--no-findings", action="store_true",
                        help="skip the <name>.findings.json artifact")
    parser.add_argument("--ai-summary", action="store_true",
                        help="also generate <name>.migration-report.md from the findings "
                             "using the configured AI provider (opt-in; sends the findings "
                             "to that provider)")
    parser.add_argument("--ai-model", metavar="MODEL",
                        help="model id for --ai-summary (default: EXOS_TRANSLATOR_AI_MODEL "
                             "or the built-in default)")
    return parser


def _translate_one(path: str, text: str, args) -> int:
    # Translate one Cisco config into its artifacts. Returns an exit code.
    collector = FindingsCollector()
    config = parse_cisco_config(text, collector)

    # mapping: derived defaults overlaid with user edits from <name>.map.json
    # first run writes the defaults for the user to edit
    map_path = _out_path(path, ".map.json", args.output_dir)
    defaults = build_default_mapping(config)
    notes: list[str] = []
    if map_path.exists():
        try:
            mapping, notes = merge_mapping(defaults, load_mapping(map_path))
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
    else:
        write_mapping(map_path, defaults)
        print(f"{map_path} written — edit it to customize, then re-run")
        mapping = defaults

    # Generate the EXOS script and write it next to the input as <name>.xsf.
    exos_text, gen_warnings, pol_files = generate_exos_config(
        config, mapping, notes, collector
    )
    out_path = _out_path(path, ".xsf", args.output_dir)
    out_path.write_text(exos_text, encoding="utf-8")
    print(f"{path} -> {out_path}")

    artifacts = {
        "exos_config": out_path.name,
        "mapping": map_path.name,
        "acl_policies": [],
        "stack_setup": None,
        "findings": None,
        "migration_report": None,
    }

    # ACL policy files go in <name>-acls/; the basename must stay equal to
    # the policy name the .xsf references, so they get their own directory.
    if pol_files:
        acl_dir = _out_path(path, "", args.output_dir)
        acl_dir = acl_dir.with_name(f"{acl_dir.name}-acls")
        acl_dir.mkdir(exist_ok=True)
        for pol_name, pol_text in sorted(pol_files.items()):
            (acl_dir / f"{pol_name}.pol").write_text(pol_text, encoding="utf-8")
        artifacts["acl_policies"] = [f"{acl_dir.name}/{n}.pol" for n in sorted(pol_files)]
        print(
            f"{path} -> {acl_dir}/ ({len(pol_files)} .pol file(s); "
            f"upload to the switch before loading the .xsf)"
        )

    # Stacked source: also emit the stack bring-up runbook (the .xsf must be
    # loaded only after the stack exists).
    setup_text = generate_stack_setup(config, out_path.name)
    if setup_text:
        setup_path = _out_path(path, ".stack-setup.txt", args.output_dir)
        setup_path.write_text(setup_text, encoding="utf-8")
        artifacts["stack_setup"] = setup_path.name
        print(f"{path} -> {setup_path} (run before loading the .xsf)")

    # Findings are always built (the AI report is derived from them); --no-findings
    # only suppresses the JSON artifact.
    findings_path = _out_path(path, ".findings.json", args.output_dir)
    if not args.no_findings:
        artifacts["findings"] = findings_path.name
    document = build_findings(
        config, mapping, pol_files, collector,
        input_name=Path(path).name,
        input_text=text,
        artifacts=artifacts,
        ai_report={"requested": bool(args.ai_summary), "status": "disabled"},
    )

    # The AI report runs after the deterministic artifacts are on disk, so a
    # failure here can never remove or corrupt them.
    if args.ai_summary:
        report_path = _out_path(path, ".migration-report.md", args.output_dir)
        ok, ai_report = _write_ai_report(document.to_dict(), report_path, args)
        if ok:
            document.artifacts["migration_report"] = report_path.name
            print(f"{path} -> {report_path}")
        document.generation["ai_report"] = ai_report

    if not args.no_findings:
        findings_path.write_text(document.to_json(), encoding="utf-8")
        print(f"{path} -> {findings_path}")

    # All warnings are embedded in the .xsf header; just summarize here.
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
    # A failed AI report is reported on stderr and recorded in the findings, but
    # it does not fail the translation.
    return 0


def _write_ai_report(document: dict, report_path: Path, args) -> tuple[bool, dict]:
    # Returns (wrote_report, ai_report_metadata). Never raises: an AI failure
    # is reported and the deterministic artifacts are left untouched.
    from cisco_exos_translator import ai  # imported only when explicitly enabled

    client = ai.AnthropicLLMClient(model=args.ai_model)
    meta = dict(client.describe())
    meta["requested"] = True
    print(ai.disclosure(document, client), file=sys.stderr)
    try:
        markdown = ai.MigrationSummaryGenerator(client).generate_summary(document)
    except (ai.AIConfigurationError, ai.AIRequestError) as exc:
        print(f"Warning: AI summary not generated: {exc}", file=sys.stderr)
        meta["status"] = "failed"
        meta["error"] = str(exc)
        return False, meta

    header = (
        "<!-- AI-generated explanation of the migration findings. "
        "Not deployable configuration; verify against the .xsf and .findings.json. -->\n\n"
    )
    report_path.write_text(header + markdown.rstrip() + "\n", encoding="utf-8")
    meta["status"] = "generated"
    return True, meta


def main(argv: list[str] | None = None) -> int:
    # Translate Cisco config(s) to EXOS .xsf files (one per input).
    args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)

    if not args.configs:
        print("Usage: main.py <cisco_config.cfg> [<cisco_config2.cfg> ...]")
        return 1

    # Read every input up front so an unreadable file fails before any artifact
    # is written (the pre-argparse CLI behaved the same way).
    sources: dict[str, str] = {}
    for path in sorted(args.configs):
        try:
            sources[path] = Path(path).read_text(encoding="utf-8")
        except OSError as exc:
            print(f"Error: failed to read {path!r}: {exc}", file=sys.stderr)
            return 1

    if args.output_dir:
        Path(args.output_dir).mkdir(parents=True, exist_ok=True)

    for path, text in sources.items():
        result = _translate_one(path, text, args)
        if result:
            return result
    return 0


if __name__ == "__main__":
    sys.exit(main())
