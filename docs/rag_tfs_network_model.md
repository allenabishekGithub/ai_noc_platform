# TFS Network Model and Agent Decision-Making

## Device Roles in TeraFlowSDN

TeraFlowSDN manages a network composed of two distinct types of devices, each
with a different role in service provisioning:

**Customer Edge (CE) devices** are the endpoints that the operator cares about.
They represent the sites or data centres being connected. In the emulated topology
these are named DC1, DC2, DC3, DC4 and have device type `emu-datacenter`.
CE devices have an `int` endpoint — the internal/customer-facing port used as
the attachment point for services.

**Provider Edge (PE) devices** form the transport core. They carry traffic
between CE sites. In the emulated topology these are named R1, R2, R3, R4 and
have device type `emu-packet-router`. PE devices have `eth0`, `eth1`, `eth2`, …
endpoints that interconnect the routers to each other and to the DC devices.

## Identifying Device Roles from Inventory

When `tfs_inventory` returns a device list, determine each device's role from:
- `device_type` containing "datacenter" → CE device, use endpoint `int`
- `device_type` containing "router" → PE device, use `eth*` endpoints
- Name prefix DC, dc → CE device
- Name prefix R, r followed by a number → PE device (router)

## Service Endpoints — Always CE-to-CE

For L3VPN and L2VPN services, `src_device` and `dst_device` are always the
two CE (customer) devices the operator named in their request.

Example: "create an L3VPN between DC2 and DC4"
→ src_device=DC2, src_endpoint=int, dst_device=DC4, dst_endpoint=int

The TFS controller computes the path through the PE routers automatically.
The agent does not specify intermediate routers. Never use a PE router (R1, R2,
R3, R4) as src_device or dst_device unless the user explicitly requests a
router-to-router service.

## Topology Layout (Emulated)

The emulated network is a hub-and-spoke with a router ring:

  DC1 — R1 — R4 — DC4
              |
  DC2 — R2 — R3 — DC3

DC devices attach to one router each. Routers are connected in a ring.
TFS path computation handles multi-hop traversal automatically.

## When to Act vs When to Ask

Understanding the risk profile of each operation guides when the agent needs
human input and when it should proceed immediately.

**Provisioning a service — act immediately after inventory.**
Service creation is additive: it creates something new without modifying
anything that exists. If an error is made, the service can be deleted.
The operator has already expressed intent by making the request.
Asking for a second confirmation adds no safety value and slows the workflow.
Correct flow: tfs_inventory → tfs_provision_service → report result.

**Deleting a service — execute when explicitly requested.**
The current runtime policy executes destructive operations when the user asks.
Use the exact service name/UUID the user provided (or resolve it from
inventory), call `tfs_delete_service` once, and report whether deletion is
confirmed or pending.

**Generic tfs_write mutations — execute with explicit user intent.**
tfs_write can modify contexts, topologies, slices, and optical resources.
Ensure the request is unambiguous, then execute in one call and report the
result.

**Asking the user — only when genuinely blocked.**
Use ask_user only when: (1) a required piece of information cannot be
discovered by calling a tool, or (2) the agent has failed twice on the same
step, or (3) multiple valid interpretations exist and the wrong choice would
cause a meaningful problem (e.g. ambiguous device name matching multiple
real devices). Do not ask for confirmation of things the agent can determine
from tools.

## Endpoint Selection Quick Reference

| Device type    | Example names     | Use endpoint |
|----------------|-------------------|--------------|
| Datacenter (CE)| DC1, DC2, DC3, DC4| int          |
| Router (PE)    | R1, R2, R3, R4    | eth0–eth4    |
| Unknown        | —                 | call tfs_inventory, inspect endpoint names |

If a device has no `int` endpoint, use the first available endpoint from the
inventory. If a device has multiple `eth` ports and the user has not specified
one, use eth0 as the default and note this in the response.

## Service Naming

`tfs_provision_service` auto-generates a name if none is provided, using the
pattern `{type}-{src_device}-{dst_device}` in lowercase (e.g. `l3-dc2-dc4`).
If the user provides a preferred name, pass it explicitly. Never invent a name
based on intermediate routers — always derive it from the user-named endpoints.

## Duplicate Service Detection

Before provisioning, check the services list returned by tfs_inventory. If a
service with the same or similar name already exists between the same endpoints,
tell the user before creating another one. Creating duplicate services between
the same CE devices can waste resources and cause routing issues.
