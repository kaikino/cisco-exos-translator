# .pol ACL hardware test — steps and commands

Hardware validation of the ACL translation (`.pol` policy files) on the
standalone X440G2-12p-10G4 loaner. Executed 2026-08-12 via mgmt telnet
(10.170.11.122); switch running `pre-dem.cfg`, nothing saved at any point.

## 1. Generate the outputs (laptop)

```bash
python3 main.py docs/cisco-acl-list.cfg
# -> cisco-acl-list.xsf
# -> cisco-acl-list-acls/SERVERS_IN.pol  (named extended ACL, remark, seq numbers)
# -> cisco-acl-list-acls/V_100.pol       (extended numbered ACL)
```

## 2. Put the .pol files on the switch

Normal path (needs a TFTP server reachable from the switch):

```
tftp get <server-ip> vr VR-Mgmt SERVERS_IN.pol
tftp get <server-ip> vr VR-Mgmt V_100.pol
```

Used here instead (no TFTP server available): wrote the file content through
the switch's own `edit policy <name>.pol` (vi) driven by an expect script —
functionally identical result, file lands in `/usr/local/cfg`.

## 3. Validate the policy syntax (on the switch)

```
check policy SERVERS_IN        -> "Policy file check successful."
check policy V_100             -> "Policy file check successful."
```

## 4. Apply (the lines from the generated .xsf)

```
configure access-list SERVERS_IN ports 1 ingress    -> done!
configure access-list V_100 ports 2 ingress         -> done!
```

## 5. Verify

```
show access-list
    Port 1  SERVERS_IN  ingress  Rules: 5
    Port 2  V_100       ingress  Rules: 5
    (5 = 4 translated entries + implicit_deny; matches the Cisco source)

show access-list port 1 detail
    entry r10: protocol tcp; destination-address 10.2.0.5/32; destination-port 22; permit
    entry r20: protocol tcp; source-address 10.1.0.0/24;
               destination-address 10.2.0.5/32; destination-port 8000 - 8080; permit
    entry r30: protocol 47; permit
    entry r40: deny (ip any any)
    entry implicit_deny: source-address 0.0.0.0/0; deny
    (rule-for-rule match with the Cisco ACL, including the port range,
     protocol number, and the IPv4-only implicit deny that leaves ARP alone)
```

Traffic-level testing was not possible (no cables on the data ports); this
validates syntax acceptance, TCAM compilation, and rule fidelity.

## 6. Cleanup (nothing saved)

```
unconfigure access-list SERVERS_IN     -> done!
unconfigure access-list V_100          -> done!
show access-list                       -> No entry found!
rm SERVERS_IN.pol                      (confirm y)
rm V_100.pol                           (confirm y)
exit                                   (answer N to "save configuration?")
```

## Result

Every generated construct was accepted by `check policy`, compiled into TCAM
on apply, and the switch's parsed view matched the Cisco source rule-for-rule.
The .pol ACL path is hardware-validated at the configuration level.

---

# Follow-up: RACL (VLAN-applied ACL) hardware test — 2026-08-12

Same switch, same generated outputs, after the RACL-to-VLAN feature landed.
Full integrated flow from the .xsf: VLAN + port memberships + L3 (SVI address,
ipforwarding) + PACLs + RACL together.

```
(upload SERVERS_IN.pol / V_100.pol as above; check policy -> successful)

create vlan "USERS" tag 10
configure vlan Default delete ports 1     (and 2, 3)
configure vlan "USERS" add ports 1 untagged   (and 2, 3)
configure vlan "USERS" ipaddress 10.10.10.1/24
enable ipforwarding vlan "USERS"
configure access-list V_100 ports 2 ingress          -> done!
configure access-list SERVERS_IN ports 1 ingress     -> done!
configure access-list SERVERS_IN vlan "USERS" ingress -> done!

show access-list
    Port 1        SERVERS_IN  ingress  5     (PACL)
    Port 2        V_100       ingress  5     (PACL)
    USERS  *      SERVERS_IN  ingress  5     (RACL -> VLAN-applied)
show vlan | include USERS
    USERS 10 10.10.10.1/24  -f-  0/3         (addressed, forwarding on)
```

Notable: the same .pol (SERVERS_IN) bound to a port and a VLAN simultaneously
with no conflict — matching Cisco's reuse of one ACL on multiple targets.

Cleanup (nothing saved): unconfigure both access-lists, delete vlan USERS,
re-add ports 1-3 to Default, restore sysName, rm both .pol files.
show access-list -> "No entry found!"; Default back to 0/16.
