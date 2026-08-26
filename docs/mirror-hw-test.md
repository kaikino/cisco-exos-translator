# Mirroring (SPAN) hardware test — steps and results

Hardware validation of the SPAN → EXOS mirror translation on the standalone
X440G2-12p-10G4 loaner (EXOS 33.4.1.15). Executed 2026-08-26 via mgmt telnet
(10.170.254.73:11245, out-of-band mgmt on VR-Mgmt, so mirror config on data
ports cannot cut off access); nothing saved at any point.

## 1. Baseline

```
show mirror
    DefaultMirror (Disabled) — "Default Mirror Instance, created automatically"
    Mirrors enabled: 0 (Maximum 4)
    HW mirror instances used: 0 ingress, 0 egress (Maximum 4 total, 1 egress)
```

Confirms the assumptions baked into the generator: `DefaultMirror` always
exists (so the mapping's `DefaultMirror` option skips `create mirror`), and
the enabled-mirror ceiling is a per-platform HW limit.

## 2. Apply (the exact construct the generator emits, one session)

```
create vlan "VLAN_20" tag 20
configure vlan Default delete ports 12
create mirror monitor_1
configure mirror monitor_1 to port 12
configure mirror monitor_1 add port 1 ingress
configure mirror monitor_1 add port 2 egress
configure mirror monitor_1 add port 3 ingress-and-egress
configure mirror monitor_1 add vlan "VLAN_20"
enable mirror monitor_1
```

Every line accepted with no error and **no interactive prompt**.

## 3. Verify

```
show mirror
    monitor_1 (Enabled)
        Mirror to port: 12
        Port 1, all vlans, ingress only
        Port 2, all vlans, egress only
        Port 3, all vlans, ingress and egress
        All ports, vlan VLAN_20, ingress only
    HW mirror instances used: 1 ingress, 1 egress
```

Filter-for-filter match with the Cisco source semantics (rx / tx / both /
source vlan).

## 4. Limits and behaviors probed

- **`enable mirror` prompts if the monitor port still has VLAN membership**:
  enabling a second mirror whose monitor port was still in `Default` raised
  `Warning: This command will remove VLAN membership from the monitor port.
  Do you want to continue? (y/N)`. `monitor_1` did **not** prompt because the
  generated `configure vlan Default delete ports 12` ran first — this is why
  the generator emits that line (an interactive prompt would stall
  `load script`).
- **Concurrent mirrors**: 3 mirrors enabled simultaneously (monitor_1, a
  second named instance, and DefaultMirror) — accepted.
- **Egress ceiling**: adding an egress filter to a second enabled mirror
  fails with `Error: Maximum number of egress mirrors (1) already enabled!`.
  On X440-G2 only **one** enabled mirror may carry egress/both filters (the
  generator's multi-session warning reflects this).
- **DefaultMirror reuse** (mapping `"mirrors": {"1": "DefaultMirror"}` path):
  `configure mirror DefaultMirror to port ...` + `add port ... ingress` +
  `enable mirror DefaultMirror` all work without a `create`.

Traffic-level testing was not possible (no cables on the data ports); this
validates syntax acceptance, HW mirror-instance allocation, and filter
fidelity.

## 5. Stacked pair (SummitStack `slot:port` form)

Repeated 2026-08-26 on the 2-node X440G2 SummitStack ring (telnet
10.170.11.87, slot 1 master; running the earlier SW-STACK-DEMO translated
config). Sources 1:1–1:3 (untagged in USERS), monitor ports 1:11 / 2:12
(Default-only, matching the translator's monitor-port state). Nothing saved;
everything rolled back and re-verified against a pre-test
`show ports ... vlan` snapshot.

- **Cross-slot mirroring works both ways**: monitor on slot 2 with sources on
  slot 1 (`configure mirror monitor_1 to port 2:12` + `add port 1:1 ingress`
  / `1:2 egress` / `1:3 ingress-and-egress` / `add vlan "USERS"`), and the
  reverse (monitor 1:11, source 2:1 ingress). Every generated line accepted
  verbatim; `show mirror` matched filter-for-filter.
- **No interactive prompts**, because the generated
  `configure vlan Default delete ports <monitor-port>` ran first — same
  behavior as standalone.
- **Limits are per-stack, not per-slot**: the same `Maximum 4` enabled
  mirrors and `Error: Maximum number of egress mirrors (1) already enabled!`
  on a second egress filter, even with the two mirrors on different slots.
- A mirror source VLAN can be an existing VLAN with members (USERS) with no
  side effects on that VLAN's config.

## 6. Cleanup (nothing saved)

```
disable mirror monitor_1 / m2 / DefaultMirror
delete mirror monitor_1 / m2          (DefaultMirror cannot be deleted)
configure mirror DefaultMirror delete port 6
configure mirror DefaultMirror to port none
configure vlan Default add ports 10,11,12 untagged
delete vlan VLAN_20
show mirror / show vlan               -> baseline restored
```

## Result

Every construct the generator emits for mirroring was accepted verbatim on
hardware — standalone (bare port numbers) and 2-node SummitStack
(`slot:port`, including cross-slot monitor/source placement) — the switch's
parsed view matched the Cisco SPAN source filter-for-filter, and the
pre-delete of the monitor port from `Default` proved necessary to keep the
script non-interactive. Mirror-count and egress limits are enforced
per-switch/per-stack (4 enabled, 1 with egress filters on X440-G2). The
mirroring path is hardware-validated at the configuration level.
