# Diverse Network Topology Examples for TFS Service Provisioning

This document shows how to provision services across many different topology
shapes, device naming conventions, and endpoint styles. The goal is to
demonstrate that the agent's reasoning must adapt to what tfs_inventory
actually returns — not assume a fixed naming pattern.

---

## Topology 1: European Metro Ring (City Names, eth* Endpoints)

**Inventory:**
```json
{
  "devices": [
    {"name": "PARIS",     "type": "emu-datacenter",    "endpoints": [{"name": "eth0"}, {"name": "eth1"}]},
    {"name": "LONDON",    "type": "emu-datacenter",    "endpoints": [{"name": "eth0"}, {"name": "eth1"}]},
    {"name": "AMSTERDAM", "type": "emu-datacenter",    "endpoints": [{"name": "eth0"}, {"name": "eth1"}]},
    {"name": "PE-PARIS",  "type": "emu-packet-router", "endpoints": [{"name": "eth0"}, {"name": "eth1"}, {"name": "eth2"}]},
    {"name": "PE-LONDON", "type": "emu-packet-router", "endpoints": [{"name": "eth0"}, {"name": "eth1"}, {"name": "eth2"}]}
  ]
}
```

**User request:** "Create a 10 Gbps L3VPN between PARIS and LONDON."

**Reasoning:** PARIS and LONDON are type `emu-datacenter` — they are the CE devices.
They have no `int` endpoint; instead they use `eth0`. src/dst are the cities the user named.
PE-PARIS and PE-LONDON are transport devices — do not use them as src/dst.

```
tfs_provision_service(
  service_type="l3vpn",
  src_device="PARIS",
  src_endpoint="eth0",
  dst_device="LONDON",
  dst_endpoint="eth0",
  capacity_gbps=10.0
)
→ "Service 'l3-paris-london' provisioned. UUID: a1b2..., status: ACTIVE."
```

**Key lesson:** When no `int` endpoint exists, use the first available data-plane
endpoint (`eth0`). Endpoint selection is always based on what tfs_inventory returns
— never assumed from the name.

---

## Topology 2: Enterprise Branch Network (Descriptive Names, uplink/lan Endpoints)

**Inventory:**
```json
{
  "devices": [
    {"name": "HQ",           "type": "emu-datacenter",    "endpoints": [{"name": "lan"}, {"name": "uplink"}]},
    {"name": "Branch-North", "type": "emu-datacenter",    "endpoints": [{"name": "lan"}, {"name": "uplink"}]},
    {"name": "Branch-South", "type": "emu-datacenter",    "endpoints": [{"name": "lan"}, {"name": "uplink"}]},
    {"name": "Branch-East",  "type": "emu-datacenter",    "endpoints": [{"name": "lan"}, {"name": "uplink"}]},
    {"name": "Core-Router",  "type": "emu-packet-router", "endpoints": [{"name": "eth0"}, {"name": "eth1"}, {"name": "eth2"}, {"name": "eth3"}]}
  ]
}
```

**User request:** "Set up an L3VPN between HQ and Branch-East."

**Reasoning:** All branch and HQ devices are type `emu-datacenter`. They have `uplink`
as the WAN-facing port. Core-Router is the transport device. src/dst are HQ and Branch-East.

```
tfs_provision_service(
  service_type="l3vpn",
  src_device="HQ",
  src_endpoint="uplink",
  dst_device="Branch-East",
  dst_endpoint="uplink",
  capacity_gbps=1.0
)
→ "Service 'l3-hq-branch-east' provisioned. UUID: c3d4..., status: ACTIVE."
```

**Key lesson:** When a datacenter-type device has no `int` endpoint, look for
the WAN-facing port by name. `uplink`, `wan`, `eth0`, and `port-1` are all
common alternatives. Check what the inventory returns.

---

## Topology 3: Flat Topology — No Intermediate Routers

**Inventory:**
```json
{
  "devices": [
    {"name": "NodeA", "type": "emu-packet-router", "endpoints": [{"name": "eth0"}, {"name": "eth1"}]},
    {"name": "NodeB", "type": "emu-packet-router", "endpoints": [{"name": "eth0"}, {"name": "eth1"}]}
  ]
}
```

**User request:** "Create an L3VPN between NodeA and NodeB."

**Reasoning:** Both devices are routers directly connected. There is no hierarchy here —
both NodeA and NodeB are src/dst. TFS will still handle path computation even in a
two-node flat topology.

```
tfs_provision_service(
  service_type="l3vpn",
  src_device="NodeA",
  src_endpoint="eth0",
  dst_device="NodeB",
  dst_endpoint="eth0"
)
→ "Service 'l3-nodea-nodeb' provisioned. UUID: e5f6..., status: ACTIVE."
```

**Key lesson:** Not all topologies have a CE/PE split. When the user names two routers,
use those routers directly as src/dst. TFS handles whatever topology exists underneath.

---

## Topology 4: ISP Backbone (Airport Codes, Explicit CE/PE Naming)

**Inventory:**
```json
{
  "devices": [
    {"name": "LHR-CE",  "type": "emu-datacenter",    "endpoints": [{"name": "int"}, {"name": "eth0"}]},
    {"name": "CDG-CE",  "type": "emu-datacenter",    "endpoints": [{"name": "int"}, {"name": "eth0"}]},
    {"name": "AMS-CE",  "type": "emu-datacenter",    "endpoints": [{"name": "int"}, {"name": "eth0"}]},
    {"name": "LHR-PE",  "type": "emu-packet-router", "endpoints": [{"name": "eth0"}, {"name": "eth1"}, {"name": "eth2"}]},
    {"name": "CDG-PE",  "type": "emu-packet-router", "endpoints": [{"name": "eth0"}, {"name": "eth1"}, {"name": "eth2"}]},
    {"name": "AMS-PE",  "type": "emu-packet-router", "endpoints": [{"name": "eth0"}, {"name": "eth1"}, {"name": "eth2"}]}
  ]
}
```

**User request:** "Provision an L2VPN service from LHR to CDG."

**Reasoning:** LHR-CE and CDG-CE are type `emu-datacenter` — they are the CE attachment
points. "LHR" and "CDG" refer to the CE devices, not the PE routers. The `int` endpoint
is present, so use it. PE routers are not the src/dst.

```
tfs_provision_service(
  service_type="l2vpn",
  src_device="LHR-CE",
  src_endpoint="int",
  dst_device="CDG-CE",
  dst_endpoint="int"
)
→ "Service 'l2-lhr-ce-cdg-ce' provisioned. UUID: g7h8..., status: ACTIVE."
```

**Key lesson:** When device names have suffixes like -CE or -PE, the suffix encodes
the role. CE devices are always src/dst for customer services. If the user says
"from LHR to CDG", match to LHR-CE and CDG-CE from the inventory.

---

## Topology 5: Data Centre Interconnect — Optical Service

**Inventory:**
```json
{
  "devices": [
    {"name": "DC-Primary",   "type": "emu-datacenter",          "endpoints": [{"name": "int"}, {"name": "opt-1"}]},
    {"name": "DC-Secondary", "type": "emu-datacenter",          "endpoints": [{"name": "int"}, {"name": "opt-1"}]},
    {"name": "DC-DR",        "type": "emu-datacenter",          "endpoints": [{"name": "int"}, {"name": "opt-1"}]},
    {"name": "WDM-West",     "type": "emu-open-line-system",    "endpoints": [{"name": "line-1"}, {"name": "line-2"}, {"name": "add-drop-1"}]},
    {"name": "WDM-East",     "type": "emu-open-line-system",    "endpoints": [{"name": "line-1"}, {"name": "line-2"}, {"name": "add-drop-1"}]}
  ]
}
```

**User request:** "Create an optical connectivity service between DC-Primary and DC-DR."

**Reasoning:** DC-Primary and DC-DR are type `emu-datacenter` — they are the endpoint
sites. They have `opt-1` as the optical port. WDM devices are transport — not src/dst.
Service type is `optical`.

```
tfs_provision_optical_connectivity_service(
  src_device="DC-Primary",
  src_endpoint="opt-1",
  dst_device="DC-DR",
  dst_endpoint="opt-1"
)
→ "Service 'optical-dc-primary-dc-dr' provisioned. UUID: i9j0..., status: ACTIVE."
```

**Key lesson:** For optical services, choose optical endpoints (names containing
`opt`, `oc`, `line`, `wdm`, or of type `PHOTONIC`). If both electrical and optical
endpoints exist on a device, pick the one that matches the service type.

---

## Topology 6: Multi-Site SD-WAN (Generic Numbered Sites)

**Inventory:**
```json
{
  "devices": [
    {"name": "site-1",  "type": "emu-datacenter",    "endpoints": [{"name": "wan0"}, {"name": "lan0"}]},
    {"name": "site-2",  "type": "emu-datacenter",    "endpoints": [{"name": "wan0"}, {"name": "lan0"}]},
    {"name": "site-3",  "type": "emu-datacenter",    "endpoints": [{"name": "wan0"}, {"name": "lan0"}]},
    {"name": "site-4",  "type": "emu-datacenter",    "endpoints": [{"name": "wan0"}, {"name": "lan0"}]},
    {"name": "hub-pe",  "type": "emu-packet-router", "endpoints": [{"name": "eth0"}, {"name": "eth1"}, {"name": "eth2"}, {"name": "eth3"}]}
  ]
}
```

**User request:** "I need a 2 Gbps L3VPN between site-2 and site-4."

**Reasoning:** All site-* devices are type `emu-datacenter`. They have `wan0` as the
external-facing port. hub-pe is the transport device. src/dst = site-2 and site-4.

```
tfs_provision_service(
  service_type="l3vpn",
  src_device="site-2",
  src_endpoint="wan0",
  dst_device="site-4",
  dst_endpoint="wan0",
  capacity_gbps=2.0
)
→ "Service 'l3-site-2-site-4' provisioned. UUID: k1l2..., status: ACTIVE."
```

---

## Topology 7: Spine-Leaf Data Centre (High Port Count)

**Inventory:**
```json
{
  "devices": [
    {"name": "Leaf-1",  "type": "emu-datacenter",    "endpoints": [{"name": "port-1"}, {"name": "port-2"}, {"name": "port-3"}, {"name": "port-4"}]},
    {"name": "Leaf-2",  "type": "emu-datacenter",    "endpoints": [{"name": "port-1"}, {"name": "port-2"}, {"name": "port-3"}, {"name": "port-4"}]},
    {"name": "Leaf-3",  "type": "emu-datacenter",    "endpoints": [{"name": "port-1"}, {"name": "port-2"}, {"name": "port-3"}, {"name": "port-4"}]},
    {"name": "Spine-1", "type": "emu-packet-router", "endpoints": [{"name": "eth0"}, {"name": "eth1"}, {"name": "eth2"}]},
    {"name": "Spine-2", "type": "emu-packet-router", "endpoints": [{"name": "eth0"}, {"name": "eth1"}, {"name": "eth2"}]}
  ]
}
```

**User request:** "Connect Leaf-1 to Leaf-3 with an L2VPN."

**Reasoning:** Leaf-* devices are type `emu-datacenter`. They have numbered `port-*`
endpoints. Spine-* are transport routers. src/dst = Leaf-1 and Leaf-3. Use port-1
as the default when the user has not specified a port.

```
tfs_provision_service(
  service_type="l2vpn",
  src_device="Leaf-1",
  src_endpoint="port-1",
  dst_device="Leaf-3",
  dst_endpoint="port-1"
)
→ "Service 'l2-leaf-1-leaf-3' provisioned. UUID: m3n4..., status: ACTIVE."
```

---

## Topology 8: Carrier Backbone — Ambiguous Name Resolution

**Inventory:**
```json
{
  "devices": [
    {"name": "NY-DC",   "type": "emu-datacenter",    "endpoints": [{"name": "int"}, {"name": "eth0"}]},
    {"name": "LA-DC",   "type": "emu-datacenter",    "endpoints": [{"name": "int"}, {"name": "eth0"}]},
    {"name": "CHI-DC",  "type": "emu-datacenter",    "endpoints": [{"name": "int"}, {"name": "eth0"}]},
    {"name": "NY-PE",   "type": "emu-packet-router", "endpoints": [{"name": "eth0"}, {"name": "eth1"}]},
    {"name": "LA-PE",   "type": "emu-packet-router", "endpoints": [{"name": "eth0"}, {"name": "eth1"}]},
    {"name": "CHI-PE",  "type": "emu-packet-router", "endpoints": [{"name": "eth0"}, {"name": "eth1"}]}
  ]
}
```

**User request:** "Create an L3VPN from New York to Chicago."

**Reasoning:** The user said "New York" — match to `NY-DC` in inventory (CE device).
"Chicago" maps to `CHI-DC`. Both have `int` endpoint. Do not use NY-PE or CHI-PE.

```
tfs_provision_service(
  service_type="l3vpn",
  src_device="NY-DC",
  src_endpoint="int",
  dst_device="CHI-DC",
  dst_endpoint="int"
)
→ "Service 'l3-ny-dc-chi-dc' provisioned. UUID: o5p6..., status: ACTIVE."
```

**Key lesson:** Match user-provided site descriptions to the closest device name
in the inventory. "New York" → NY-DC is a reasonable inference when no exact match
exists. If multiple plausible matches exist (e.g., NY-DC and NY-PE both match "NY"),
prefer the CE device (datacenter type) for customer services.

---

## Topology 9: Mixed Technology — When to Use ask_user

**Inventory:**
```json
{
  "devices": [
    {"name": "Router-A", "type": "emu-packet-router", "endpoints": [{"name": "eth0"}, {"name": "eth1"}, {"name": "opt-1"}]},
    {"name": "Router-B", "type": "emu-packet-router", "endpoints": [{"name": "eth0"}, {"name": "eth1"}, {"name": "opt-1"}]}
  ]
}
```

**User request:** "Create a service between Router-A and Router-B."

**Reasoning:** The user has not specified a service type or which endpoint to use.
Router-A and Router-B each have both electrical (`eth0`) and optical (`opt-1`) ports.
This ambiguity cannot be resolved from the inventory alone — ask.

```
ask_user(
  "Router-A and Router-B each have both electrical and optical ports. "
  "What type of service do you want?",
  candidates=["L3VPN (eth0)", "L2VPN (eth0)", "Optical (opt-1)"]
)
```

**Key lesson:** Use ask_user only when a genuine ambiguity exists that tools cannot
resolve. Ambiguous endpoint type (electrical vs optical) is a valid case. Ambiguous
device name that matches multiple candidates is also valid. Provisioning capacity or
MTU are not — use defaults and report what was chosen.

---

## Topology 10: Three-Site L3VPN — User Mentions Intermediate City

**Inventory:**
```json
{
  "devices": [
    {"name": "Rome",   "type": "emu-datacenter",    "endpoints": [{"name": "int"}]},
    {"name": "Milan",  "type": "emu-datacenter",    "endpoints": [{"name": "int"}]},
    {"name": "Naples", "type": "emu-datacenter",    "endpoints": [{"name": "int"}]},
    {"name": "PE-1",   "type": "emu-packet-router", "endpoints": [{"name": "eth0"}, {"name": "eth1"}, {"name": "eth2"}]}
  ]
}
```

**User request:** "I want an L3VPN from Rome to Milan going via Naples."

**Reasoning:** TFS computes paths automatically — the agent does not control routing.
The correct src/dst are Rome and Milan. Naples is not a src or dst device. Inform the
user that path selection is handled by the TFS controller and the service will be
created between Rome and Milan.

```
tfs_provision_service(
  service_type="l3vpn",
  src_device="Rome",
  src_endpoint="int",
  dst_device="Milan",
  dst_endpoint="int"
)
→ "Service 'l3-rome-milan' provisioned. UUID: q7r8..., status: ACTIVE."
```

**Response:** "L3VPN service provisioned between Rome and Milan. The TFS controller
handles path computation internally — the actual routing path may traverse Naples
depending on topology and constraints."

---

## General Endpoint Selection Decision Tree

When deciding which endpoint to use on a device, follow this logic using
information from tfs_inventory:

1. Does the device have an endpoint named `int`? → Use `int` (standard CE port).
2. Does the request mention an optical service? → Use an endpoint whose name contains
   `opt`, `oc`, `line`, or `wdm`.
3. Does the device have `wan`, `wan0`, or `uplink`? → Use it for WAN-facing services.
4. Does the device have `port-N` endpoints? → Use `port-1` as default.
5. Otherwise use `eth0` as the default data-plane port.
6. If multiple options exist and the service type is ambiguous → use ask_user.

Never assume an endpoint exists. Always verify from the inventory result before
calling tfs_provision_service.
