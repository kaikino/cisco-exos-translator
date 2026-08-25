# Cisco → EXOS L2 Config Translator

Translates Cisco Catalyst IOS/IOS-XE `running-config` files into Extreme EXOS
(Switch Engine) configuration scripts (`.xsf`). Scope is **Layer 2**: VLANs,
access/trunk port membership, link aggregation, and port state.

Each input config is translated **independently** ("Path A") — one Cisco switch
in, one EXOS `.xsf` out. There is no multi-switch topology correlation.

## Usage

```bash
python3 main.py <cisco_config.cfg> [<cisco_config2.cfg> ...]
```

Options:

| Flag | Effect |
|---|---|
| `-o DIR`, `--output-dir DIR` | write every artifact into `DIR` instead of next to each input (names are unchanged) |
| `--no-findings` | skip the `<name>.findings.json` artifact |
| `--ai-summary` | additionally generate `<name>.migration-report.md` from the findings (opt-in; see [AI migration report](#ai-migration-report-optional)) |
| `--ai-model MODEL` | model id for `--ai-summary` (default: `$EXOS_TRANSLATOR_AI_MODEL`, else `claude-opus-5`) |

For each input, writes alongside it:

- **`<name>.map.json`** — the translation mapping (VLAN names, Cisco→EXOS port
  numbers, uplink rule, LAG master/mode). Written with derived defaults on the
  first run and never overwritten after that. Edit it and re-run to customize
  the translation. Your entries override the defaults; entries for interfaces
  no longer in the config are ignored (warned), and new interfaces fall back
  to derived defaults (warned). For uplink-module ports, set
  `uplinks.start` to the target switch's first uplink port number (base
  ports + 1, e.g. `49` on a 48-port switch) and every `{uplink-mN-pP}`
  placeholder resolves to `start + P - 1` automatically; an explicit `ports`
  entry still overrides the rule per-port.
- **`<name>.xsf`** — the EXOS script, regenerated on every run from the config
  plus the mapping.
- **`<name>-acls/*.pol`** (only when the config has ACLs applied with
  `ip access-group ... in`) — one EXOS policy file per ACL. Upload them to the
  switch (`tftp put ...`) **before** loading the `.xsf`, which applies them by
  name.
- **`<name>.findings.json`** — the structured migration findings: what
  translated, what did not, and every assumption the translator made. Produced
  by the deterministic pipeline, with no AI involved, on every run unless
  `--no-findings` is given. See [Migration findings](#migration-findings).
- **`<name>.migration-report.md`** (only with `--ai-summary`) — a human-readable
  migration summary written by an LLM **from the findings file**. Explanatory
  only; never deployable configuration.
- **`<name>.stack-setup.txt`** (only when the source config has 2+ stack
  members) — the stack bring-up runbook. EXOS stacking is a mode change with
  per-node reboots and a fresh config context, so it cannot be part of the
  `.xsf`; this file lists the ordered steps (per-node `enable stacking-support`,
  Easy Setup from the member-1 switch, slot priorities carried over from
  `switch N priority`, plus a warning that stacking boots a fresh config
  context) to run **before** loading the `.xsf`. Environment-specific work
  such as re-establishing management access is noted but left to the operator.

A one-line warning count goes to stderr. All warnings are embedded as `#`
comments in a `WARNINGS` header at the top of the `.xsf`, grouped into
**Input** (problems in the Cisco config), **Not translated** (every source
line outside the tool's L2 scope — deduped by command with counts and line
numbers, so nothing is dropped silently), and **Translation** (decisions made
converting to EXOS, including unresolved mapping placeholders). A
`Translation reference` block below the header shows the active mapping.

Typical workflow:

```bash
python3 main.py sw1.cfg     # 1st run: writes sw1.map.json + sw1.xsf
vi sw1.map.json             # resolve placeholders, rename VLANs, adjust LAGs
python3 main.py sw1.cfg     # 2nd run: regenerates sw1.xsf with your edits
```

## Pipeline

```
running-config text
  → scanner     (text  → structured blocks)
  → parser      (blocks → ParsedConfig IR)
  → validation  (cross-reference checks → warnings + findings)
  → mapping     (derived defaults ⊕ user-edited <name>.map.json)
  → generator   (ParsedConfig + mapping → EXOS .xsf + warnings + findings)
  → findings    (collected findings + scope classification → <name>.findings.json)
  → ai summary  (findings → <name>.migration-report.md)          [opt-in]
```

Warnings and findings come from one pipeline, not two: each stage emits its
warning text through a `WarningSink`, which appends the message to the `.xsf`
banner and — when a `FindingsCollector` is attached — records the structured
finding for the same event. The collector is passed explicitly into each stage;
there is no global state, and the translation itself never depends on it.

The AI step is the last one, runs only with `--ai-summary`, and consumes the
findings document. It cannot influence the `.xsf`, the `.pol` files, or the
findings.

## Supported translations

| Cisco IOS/IOS-XE | EXOS output |
|---|---|
| `hostname X` | `configure snmp sysName "X"` |
| `vlan 10` / `name USERS` | `create vlan "USERS" tag 10` |
| `vlan 20,30` / `vlan 40-42` (lists & ranges) | one `create vlan` per ID |
| `switchport access vlan 10` | port removed from `Default`, then `add ports <p> untagged` |
| `switchport mode trunk` + `trunk allowed vlan ...` | tagged membership per allowed VLAN |
| `switchport mode trunk` (no allowed list) | expanded to all non-Default VLANs, each `tagged` (warned) |
| `switchport trunk native vlan 10` | native VLAN added `untagged` (excluded from tagged set) |
| `switchport trunk allowed vlan add/remove ...` | union/subtract applied to the allowed set |
| `channel-group N mode active\|passive` | `enable sharing <master> grouping <members> ... lacp` |
| `channel-group N mode on` | static sharing (no `lacp`) |
| `interface Port-channelN` (bundle L2 config) | applied to the LAG master port |
| `interface range Gi1/0/2-4` | expanded; body applied to each port |
| `shutdown` | `disable ports <p>` |
| `description X` | `configure ports <p> description-string "X"` |
| stack member/port (`Gi1/0/1`, `Gi2/0/24`) | `slot:port` when stacked, bare `port` when standalone |
| IPv4 ACLs (numbered & named, standard & extended) + `ip access-group <name> in` | one policy file (`<name>-acls/<ACL>.pol`) per applied ACL — entries in ACE order, `remark` kept as `#` comments, trailing deny-all (EXOS permits unmatched traffic by default) — applied via `configure access-list <ACL> ports <p> ingress`; upload the `.pol` files before loading the `.xsf` |
| `interface VlanN` + `ip address A MASK` (SVI) | `configure vlan "<name>" ipaddress A/len` + `enable ipforwarding vlan "<name>"` (SVI-only VLANs are auto-created) |
| `ip route P MASK GW` | `configure iproute add P/len GW` (`default` for 0.0.0.0/0); `ip routing` is consumed (realized per-VLAN) |

### Behavioral details

- **VLAN names** are sanitized to EXOS rules (start with a letter, alnum/`_`
  only, ≤32 chars). Unnamed VLANs become `VLAN_<tag>`; name collisions are
  de-duped by appending the tag.
- **Default VLAN**: tag 1 maps to the EXOS built-in `Default` VLAN — it is never
  recreated. Because EXOS ports start untagged in `Default`, an untagged add to
  any other VLAN is preceded by `configure vlan Default delete ports <p>`.
- **Trunk with no allowed list**: Cisco carries all VLANs (1–4094); the output
  is expanded to every non-Default VLAN defined on the switch (warned).
- **Referenced-but-undefined VLANs** are auto-created in the output (with a
  warning) so the `.xsf` is valid; tag 1 is exempt (maps to `Default`).
- **LAG master** is the lowest-numbered member; member ports are excluded from
  individual VLAN assignment (their L2 config comes from the bundle).
- **Stack detection**: more than one stack member ⇒ `slot:port` port naming.

## Warnings the generator emits

Embedded as `#` comments at the top of each `.xsf`:

- **Uplink/expansion module ports** (e.g. `Te1/1/1`, module ≠ 0) — no fixed
  EXOS port number, so a distinct placeholder like `1:{uplink-m1-p1}` is emitted
  in place of `slot:port`. It is deliberately invalid EXOS (can't be deployed by
  accident) and carries the original module/port; find-and-replace it with the
  real port from the target platform's port map.
- **EXOS port collisions** — defensive check: fires if two Cisco interfaces
  still resolve to the same `slot:port`.
- **Undefined VLANs** referenced by a port (tag 1 exempt).
- **Trunk with no allowed list** — expanded to all non-Default VLANs on the
  switch; flagged so the inference is explicit.
- **Renamed VLAN 1** — a non-default Cisco name on VLAN 1 is ignored (EXOS uses
  the built-in `Default`).
- **Stack provisioning** (`switch N provision <cisco-model>`) — Cisco SKU cannot
  be mapped to an EXOS slot type; emitted as review comments.
- **PAgP modes** (`auto`/`desirable`) — no EXOS equivalent; emitted as LACP.
  Mixed static (`on`) + LACP member modes are also flagged.
- **Routed (L3) interfaces** — skipped.

## Migration findings

`<name>.findings.json` is the machine-readable record of the translation. It is
produced by the deterministic pipeline — **no AI service is involved and none is
required** — and is written on every run unless `--no-findings` is given.

### Statuses and severities

Translation status and severity are separate fields, because they answer
different questions. Status is *what happened to this Cisco construct*;
severity is *how much it should worry you*. A VLAN auto-created because a port
referenced it is `assumption_made` **and** `warning`.

| `status` | Meaning |
|---|---|
| `translated` | emitted to the `.xsf` (or `.pol`) as intended |
| `partially_translated` | only part of the Cisco behaviour is represented — e.g. an ACL with rules the parser rejected, or a port left as an uplink placeholder |
| `unsupported` | recognised, but no EXOS translation is implemented — nothing was emitted |
| `assumption_made` | a default, inferred value or broad interpretation was used (trunk expanded to all VLANs, VLAN auto-created, PAgP defaulted to LACP) |
| `warning` | an input-validity or mapping-file problem that is not itself a translation outcome |
| `error` | invalid or self-contradictory source that produces wrong output (an ACL applied but never defined, two interfaces on one EXOS port) |

| `severity` | Meaning |
|---|---|
| `info` | for the record; no action expected |
| `warning` | review before deploying |
| `error` | the output is wrong or incomplete until you act |

`summary` counts the first four statuses, plus warnings and errors *by
severity*, so the six numbers deliberately do **not** sum to `total_findings`.

Successful low-level operations are not each turned into a finding: `translated`
findings are one per feature area (VLANs, ports, LAGs, ACLs, L3, hostname) with
counts and object lists in `metadata`. Untranslated source lines are grouped by
command, so a 48-port config with `spanning-tree portfast` everywhere produces
one finding with `metadata.occurrences: 48`, not 48 findings.

### Where classification lives

`cisco_exos_translator/feature_support.py` holds an ordered list of
`FeatureRule`s (regex → feature name, status, reason, suggested action). That is
the single place to extend feature coverage — from inside the module or via
`register_feature()` — instead of adding special cases to the parser or
generator. Anything unmatched falls through to `unsupported` with a generic
reason, so nothing is silently classified as fine.

### Sample findings JSON

Trimmed from `python3 main.py demo.cfg`:

```json
{
  "schema_version": "1.0",
  "translator_version": "0.1.0",
  "input": {
    "filename": "demo.cfg",
    "sha256": "4638a09a05115cd5a7507f8d002866652745b0b6083d9534b7cfa7992820c6dd",
    "line_count": 50,
    "hostname": "SW-DEMO-01"
  },
  "summary": {
    "translated": 4, "partially_translated": 1, "unsupported": 6,
    "assumptions": 0, "warnings": 6, "errors": 1, "total_findings": 11
  },
  "findings": [
    {
      "id": "port.unresolved_uplink#1",
      "code": "port.unresolved_uplink",
      "category": "translation",
      "feature": "port",
      "status": "partially_translated",
      "severity": "error",
      "message": "TenGigabitEthernet1/1/1: unresolved uplink placeholder '{uplink-m1-p1}'; set uplinks.start in the mapping file (first uplink port number) or replace this port entry individually",
      "object": "TenGigabitEthernet1/1/1",
      "reason": "uplink-module ports have no platform-independent EXOS port number",
      "source": { "cisco_object": "TenGigabitEthernet1/1/1" },
      "output_ref": "{uplink-m1-p1}",
      "action": "replace the placeholder with the real EXOS port before deploying"
    },
    {
      "id": "unsupported.storm-control#1",
      "code": "unsupported.storm-control",
      "category": "scope",
      "feature": "storm-control",
      "status": "unsupported",
      "severity": "warning",
      "message": "'storm-control broadcast level 5.00' (8 line(s)) has no EXOS output: storm control is not translated",
      "object": "storm-control broadcast level 5.00",
      "reason": "storm control is not translated",
      "source": { "lines": [21], "block": "interface range GigabitEthernet1/0/1-8" },
      "action": "review EXOS rate-limit / flood-control for the same ports",
      "metadata": { "occurrences": 8, "parser_reasons": ["unsupported or unhandled interface command"] }
    }
  ],
  "artifacts": {
    "exos_config": "demo.xsf",
    "mapping": "demo.map.json",
    "acl_policies": [],
    "stack_setup": null,
    "findings": "demo.findings.json",
    "migration_report": null
  },
  "generation": {
    "status": "completed_with_errors",
    "translation": "deterministic",
    "ai_report": { "requested": false, "status": "disabled" }
  }
}
```

Artifact references are **basenames only** — no local filesystem paths — and no
environment variables, API keys or credentials are ever written to the file.

## AI migration report (optional)

`--ai-summary` sends the findings document to an LLM and writes
`<name>.migration-report.md`. The AI is **explanatory only**: it is instructed to
use nothing but the supplied findings, never to write or correct EXOS
configuration, and never to claim an unsupported feature has an EXOS equivalent
unless the finding's own `action` field says so. Its output is not deployable
configuration, and the generated file says so in a comment at the top.

### Configuration

| Requirement | How |
|---|---|
| SDK | `pip install anthropic` — an **optional** dependency; the translator is stdlib-only without it |
| Credentials | resolved by the SDK from the environment (`ANTHROPIC_API_KEY`, or an `ant auth login` profile). The translator never reads, logs or serialises their value |
| Model | `--ai-model`, else `$EXOS_TRANSLATOR_AI_MODEL`, else `claude-opus-5` |

`cisco_exos_translator/ai.py` is the only module that talks to a provider:
`LLMClient` (interface) → `AnthropicLLMClient` (the one implementation) →
`MigrationSummaryGenerator.generate_summary(findings_document) -> str`. Swapping
providers means adding a class next to `AnthropicLLMClient`; nothing else in the
codebase imports the SDK, and `ai.py` is imported only when `--ai-summary` is
passed.

The prompt asks the model for, in order: **1.** executive summary,
**2.** successfully translated areas, **3.** unsupported items, **4.** partially
translated items, **5.** assumptions made, **6.** manual review requirements,
**7.** recommended validation steps.

### Privacy

- AI summary generation is **opt-in**; nothing leaves the machine without
  `--ai-summary`.
- What is sent: **the findings JSON only**, minus its `generation` block. The
  Cisco running-config file and the generated EXOS config are **not** sent.
  Every run prints a one-line disclosure to stderr before the request.
- **Findings still contain customer-specific data** — hostnames, interface
  descriptions, VLAN names, IP addresses, ACL source/destination prefixes, the
  resulting topology, and the **verbatim text of every Cisco command that could
  not be translated** (that quoting is the point of an `unsupported` finding).
  Treat sending them as you would treat sending the config itself.
- Lines the feature registry marks as credential-bearing (`username`,
  `enable secret`, `snmp-server community`, RADIUS/TACACS+ keys) are reduced to
  their command keyword before being recorded, so the secret never reaches the
  findings file. **This is not a general secret scanner** — it covers only the
  patterns listed in `feature_support.py`, and nothing else scans the config for
  secrets. Review the findings file before sending it anywhere.
- Raw configuration content is never printed to the console or to logs.

### Failure behaviour

The AI step runs *after* the `.xsf`, `.pol` and stack-setup files are on disk,
and its result is only recorded in the findings document. If the SDK is missing,
credentials are rejected, the model is unavailable, the request fails, or the
model declines:

- a clear `Warning: AI summary not generated: ...` goes to stderr,
- no `.migration-report.md` is written,
- the `.xsf`, `.pol`, mapping and `.findings.json` files are complete and
  unchanged,
- `generation.ai_report` in the findings records `"status": "failed"` plus the
  error message,
- the exit code stays `0` — the translation itself succeeded. A pipeline that
  must react to the failure should check `generation.ai_report.status`.

### Sample migration report

Illustrative of the shape (the headings are fixed by the prompt; the prose is
the model's):

```markdown
## Executive summary

SW-DEMO-01 translated to EXOS with 4 feature areas converted, 1 item partially
translated and 6 unsupported. One error-severity item must be resolved before
the script can be loaded.

## Successfully translated areas

- 3 VLANs created (10 → USERS, 20 → VOICE, 30 → MGMT).
- 12 access/trunk ports mapped with their VLAN membership.
- 1 link aggregation group converted to EXOS sharing.

## Unsupported items

- `storm-control broadcast level 5.00` (8 occurrences) — storm control is not
  translated. Recommended action from the findings: review EXOS rate-limit /
  flood-control for the same ports.
- `spanning-tree portfast` (9 occurrences) — spanning tree is not translated;
  an EXOS STP mode must be chosen explicitly. The findings supply no equivalent
  command.
```

## Testing

Stdlib `unittest`; no third-party test runner, no network access, no AI
credentials:

```bash
python3 -m unittest discover -s tests -t .
```

Covered: findings serialisation and schema shape, classification of each
status, deterministic ordering and de-duplication, summary-count accuracy,
byte-for-byte stability of the `.xsf`/`.pol` output against committed fixtures
(`tests/fixtures/`), translation with AI disabled, artifact integrity when the
AI provider fails, AI summary generation through a mocked client, that no
credential reaches an artifact or the console, and output naming for single and
multiple inputs with and without `--output-dir`.

## Not supported (out of scope)

- **Layer 3 beyond the basics**: SVI IPv4 addresses and static IP routes are
  translated (see above); still out of scope are routed *physical* ports (EXOS
  has no port IPs), `secondary` addresses, non-IP next-hops (`Null0`,
  interface next-hops), VRFs, and routing protocols. Router ACLs on SVIs are
  translated to VLAN-applied ACLs (`configure access-list <ACL> vlan "<name>"
  ingress`) with a warning: EXOS also filters intra-VLAN bridged traffic,
  which a Cisco router ACL does not.
- **Stack provisioning** — the Cisco SKU cannot be mapped to an EXOS slot type
  (comments only). Member count and priorities do translate: they drive the
  generated `.stack-setup.txt` runbook, but stack formation itself is a manual,
  reboot-bound procedure.
- **Uplink-module port renumbering** — requires the target platform's port map;
  emitted as a `{uplink-mN-pM}` placeholder for manual replacement.
- **ACL edge cases** — supported subset is permit/deny, `ip|tcp|udp|icmp`/
  numeric protocol, `any`/`host`/contiguous wildcard, numeric `eq`/`range`
  ports, `remark`, interface `in`. Everything else (`established`, `log`,
  `gt/lt/neq`, named ports, non-contiguous wildcards, object-groups, `out`
  direction, vty/SNMP/route-map contexts) is reported, never silently dropped;
  ACLs with untranslated rules get an "incomplete" warning. See
  [docs/acl-patterns.md](docs/acl-patterns.md).
- STP/spanning-tree, port speed/duplex, PoE, QoS, storm-control, voice
  VLANs, LACP timer tuning.
- **Multi-switch topology** — configs are translated independently; no VLAN
  consolidation, inter-switch link, or distributed-LAG correlation.

## Layout

```
main.py                         CLI entry point + pipeline orchestration
cisco_exos_translator/
  scanner.py                    running-config text → ConfigBlocks
  parser.py                     ConfigBlocks → ParsedConfig IR
  models.py                     dataclasses (Vlan, interfaces, ParsedConfig, ...)
  validation.py                 cross-reference checks (warnings + findings)
  mapping.py                    .map.json read/write/merge
  generator.py                  ParsedConfig + mapping → EXOS .xsf
  helpers.py                    VLAN list / interface name parsing
  findings.py                   Finding/status/severity model, collector, JSON doc
  feature_support.py            Cisco feature → support status registry
  findings_builder.py           collected findings → <name>.findings.json
  ai.py                         optional LLM migration report (opt-in)
tests/                          unittest suite + .xsf/.pol golden fixtures
```
