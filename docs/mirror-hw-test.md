# Mirroring (SPAN) hardware test — steps and commands

Hardware validation plan for the SPAN → EXOS mirror translation on the
standalone X440G2-12p-10G4 loaner. **Prepared 2026-08-26, not yet executed —
the loaner (telnet 10.170.254.73:11245) was unreachable.** Syntax was instead
verified against the Switch Engine 33.5.1 Command Reference
(`create mirror` / `configure mirror ... to port` / `add port ... ingress |
egress | ingress-and-egress` / `add vlan` / `enable mirror`; VLAN mirroring is
ingress-only; DefaultMirror exists automatically; enabled-mirror limits are
platform HW limits, e.g. 4 total / 2 egress). Nothing is to be saved at any
point.

## 1. Generate the outputs (laptop)

```bash
python3 main.py sample.cfg
# sample.cfg ends with:
#   monitor session 1 source interface Gi1/0/1 - 2 rx
#   monitor session 1 source vlan 20
#   monitor session 1 destination interface Gi1/0/47 encapsulation replicate
# -> sample.xsf "# Mirroring (SPAN)" section (stacked source, so 1:N ports)
```

For the standalone loaner, renumber to free ports first (e.g. sources 1,2 →
monitor 12) via the `ports` section of `sample.map.json`, or hand-edit the
five generated lines.

## 2. Apply (the lines from the generated .xsf, standalone numbering)

```
configure vlan Default delete ports 12
create mirror monitor_1
configure mirror monitor_1 to port 12
configure mirror monitor_1 add port 1 ingress
configure mirror monitor_1 add port 2 ingress
configure mirror monitor_1 add vlan "VLAN_20"     (create vlan first)
enable mirror monitor_1
```

Expected: every line accepted; `enable mirror` may warn about monitor-port
behavior but must not error.

## 3. Verify

```
show mirror
    monitor_1  (Enabled)
      Mirror to port: 12
      Port 1, ingress only
      Port 2, ingress only
      All ports, vlan VLAN_20, ingress only
show port 12 information    -> no VLAN membership
```

Traffic-level check if cables are available: ping across port 1 and confirm
copies egress port 12 (e.g. laptop with tcpdump on the monitor port);
confirm mirrored frames keep their VLAN tag (Cisco `encapsulation replicate`
equivalence).

## 4. Limits worth probing while on the box

```
create mirror m2 / m3 / m4 ... + enable   -> find the enabled-mirror ceiling
configure mirror m2 add port 3 egress     -> egress-filter HW instance count
```

Record what the X440G2 actually allows next to the generic "commonly 4 total,
2 egress" warning the generator emits for multi-session configs.

## 5. Cleanup (nothing saved)

```
disable mirror monitor_1
delete mirror monitor_1          (DefaultMirror cannot be deleted)
configure vlan Default add ports 12 untagged
delete vlan VLAN_20
exit                             (answer N to "save configuration?")
```
