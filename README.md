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

For each input, writes two files alongside it:

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
  → validation  (cross-reference checks → warnings)
  → mapping     (derived defaults ⊕ user-edited <name>.map.json)
  → generator   (ParsedConfig + mapping → EXOS .xsf)
```

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

## Not supported (out of scope)

- **Layer 3**: `ip address` is only used to flag a port as routed (the address
  is not captured); `ip route`, SVIs, and `ip forwarding` are not translated.
- **Stack provisioning / priority** — informational comments only; EXOS
  stacking is configured on-hardware.
- **Uplink-module port renumbering** — requires the target platform's port map;
  emitted as a `{uplink-mN-pM}` placeholder for manual replacement.
- STP/spanning-tree, port speed/duplex, PoE, ACLs, QoS, storm-control, voice
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
  validation.py                 cross-reference checks (warnings)
  mapping.py                    .map.json read/write/merge
  generator.py                  ParsedConfig + mapping → EXOS .xsf
  helpers.py                    VLAN list / interface name parsing
```
