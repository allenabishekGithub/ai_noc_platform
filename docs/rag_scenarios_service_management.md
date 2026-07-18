# TFS Service Management Scenarios

## Scenario: Create an L3VPN service between two devices

**Trigger phrases:** "create a service", "provision l3vpn", "set up a connection between", "create 2gbps l3vpn"

**IMPORTANT: Never ask the user for device names or endpoint names. Always call `tfs_inventory` first to discover them.**

**Step-by-step:**

1. Call `tfs_inventory()` to get all device names and their endpoints.
2. Match the user's device references (e.g. "DC1", "dc1", "data center 1") to the actual device names returned.
3. Pick the correct endpoint on each device — for datacenters use `int`, for routers use the appropriate `eth` port.
4. `src_device` and `dst_device` are ALWAYS the exact devices the user named (e.g. DC2 and DC4). NEVER substitute intermediate routers (R1, R2, etc.) — those handle routing internally.
5. Call `tfs_provision_service()` ONCE with the discovered names. Do NOT ask the user to approve before calling. Do NOT call it twice.
   The `name` field is optional and will be auto-generated if omitted.

**Example:**

User: "Create a 2gbps L3VPN service between DC1 and DC4."

```
tfs_inventory()
→ {
    "device_count": 8,
    "devices": [
      {"name": "DC1", "type": "emu-datacenter", "endpoints": [{"name": "int"}, {"name": "eth0"}]},
      {"name": "DC2", "type": "emu-datacenter", "endpoints": [{"name": "int"}, {"name": "eth0"}]},
      {"name": "DC3", "type": "emu-datacenter", "endpoints": [{"name": "int"}, {"name": "eth0"}]},
      {"name": "DC4", "type": "emu-datacenter", "endpoints": [{"name": "int"}, {"name": "eth0"}]},
      {"name": "R1",  "type": "emu-packet-router", "endpoints": [{"name": "eth0"}, {"name": "eth1"}, ...]},
      ...
    ]
  }
```

Now call provision directly — do NOT ask the user for device names, you already have them:

```
tfs_provision_service(
    service_type="l3vpn",
    src_device="DC1",
    src_endpoint="int",
    dst_device="DC4",
    dst_endpoint="int",
    capacity_gbps=2.0
)
→ {"status": "ok", "service_name": "l3-dc1-dc4", "service_uuid": "...", "service_status": "SERVICESTATUS_ACTIVE"}
```

**Response:** "L3VPN service 'l3-dc1-dc4' has been provisioned successfully between DC1 and DC4 with 2 Gbps capacity. Status: ACTIVE."

**Rule:** If the user says "between DC1 and DC4", use `DC1` and `DC4` exactly as they appear in `tfs_inventory`. Do not ask the user to confirm device names you already retrieved.

---

## Scenario: Delete a service by name

**Trigger phrases:** "delete service", "remove service", "tear down", "decommission"

**Tool selection rule:**
- Use `tfs_service_delete_optical_connectivity` when the target is an optical connectivity service.
- Use `tfs_delete_service` for packet and generic service deletion (L2VPN/L3VPN/E2E and others).

Both flows resolve by name/UUID, capture pre-delete diagnostics, issue the delete,
and report normalized mutation results.

**Step-by-step:**

1. Pick the right delete tool (optical-specific vs generic).
2. Call delete once with `name_or_uuid`.
3. If `ok: true`, report successful delete.
4. If `error: "delete_pending"` or `observed_status: "PENDING_REMOVAL"`, report that deletion is in progress and recommend status follow-up.

**Example:**

User: "Delete optical connectivity service optical-tp-dc2-tp-dc4."

```
tfs_service_delete_optical_connectivity(name_or_uuid="optical-tp-dc2-tp-dc4")
→ {
    "ok": true,
    "phase": "verified",
    "operation": "service.delete",
    "service_uuid": "f5e6...",
    "observed_status": "DELETED",
    "error": null
  }
```

Response: "Service 'optical-tp-dc2-tp-dc4' has been deleted successfully."

---

## Scenario: Delete a service — service not found

**Situation:** `tfs_delete_service` returns "Service 'xyz' not found."

**Correct behavior:**
1. Call `tfs_inventory()` to list all services.
2. Show the user what services actually exist.
3. Ask whether they meant a different service name.

```
tfs_inventory()
→ services: [{"name": "svc-r1-r2", ...}, {"name": "svc-r2-r3", ...}]

ask_user("I could not find a service named 'xyz'. Existing services are: svc-r1-r2, svc-r2-r3. Which did you mean?",
         candidates=["svc-r1-r2", "svc-r2-r3"])
```

---

## Scenario: Check the status of a service

**Trigger phrases:** "what is the status", "is the service up", "check service", "is svc-r1-r2 active"

**Single tool call:**

```
tfs_service_status(name_or_uuid="svc-r1-r2")
→ {
    "name": "svc-r1-r2",
    "uuid": "f5e6...",
    "type": "SERVICETYPE_L3NM",
    "status": "SERVICESTATUS_ACTIVE",
    "endpoints": ["device=a1b2... endpoint=ep-001", "device=c3d4... endpoint=ep-010"]
  }
```

**Response:** "Service 'svc-r1-r2' is currently ACTIVE. It is an L3VPN service connecting endpoint ep-001 on device a1b2 to endpoint ep-010 on device c3d4."

---

## Scenario: List all services

**Trigger phrases:** "list services", "what services exist", "show all services", "what is provisioned"

**Use `tfs_inventory()` — it returns both devices and services in one call.**

```
tfs_inventory()
→ {
    "service_count": 2,
    "services": [
      {"name": "svc-r1-r2", "uuid": "f5e6...", "type": "SERVICETYPE_L3NM", "status": "SERVICESTATUS_ACTIVE"},
      {"name": "svc-r2-r3", "uuid": "a9b0...", "type": "SERVICETYPE_L3NM", "status": "SERVICESTATUS_PLANNED"}
    ]
  }
```

**Response:** "There are 2 services provisioned:
1. svc-r1-r2 — L3VPN — ACTIVE (UUID: f5e6...)
2. svc-r2-r3 — L3VPN — PLANNED (UUID: a9b0...)"

---

## Scenario: List all devices and their endpoints

**Trigger phrases:** "what devices are there", "show devices", "what endpoints does R1 have", "list inventory"

```
tfs_inventory()
→ {
    "device_count": 3,
    "devices": [
      {"name": "R1", "uuid": "a1b2...", "status": "ENABLED",
       "endpoints": [{"name": "1/1", "uuid": "ep-001"}, {"name": "1/2", "uuid": "ep-002"}]},
      {"name": "R2", "uuid": "c3d4...", "status": "ENABLED",
       "endpoints": [{"name": "1/1", "uuid": "ep-010"}]},
      {"name": "R3", "uuid": "e5f6...", "status": "DISABLED",
       "endpoints": [{"name": "1/1", "uuid": "ep-020"}]}
    ]
  }
```

**Response:** "There are 3 devices:
- R1 (ENABLED): endpoints 1/1, 1/2
- R2 (ENABLED): endpoints 1/1
- R3 (DISABLED): endpoints 1/1 — note this device is currently disabled."

---

## Scenario: Get connections for a service

**Trigger phrases:** "show connections", "what connections does this service have", "connection details"

**Use `tfs_read` with the connection resource.**

```
tfs_read(resource="connection", operation="list", service_uuid="f5e6...", context_uuid="admin")
→ {
    "result": {
      "connections": [
        {"connection_id": {"connection_uuid": {"uuid": "conn-001"}}, ...}
      ]
    }
  }
```

If you only have the service name, call `tfs_inventory()` first to get the service UUID, then call `tfs_read`.

---

## Scenario: Delete all services (cleanup)

**Situation:** User wants to remove all provisioned services.

**Correct behavior:** Never delete all services silently. List them first, ask
which services should be removed, then delete one at a time.

```
tfs_inventory()
→ services: [svc-r1-r2, svc-r2-r3]

ask_user("I found 2 services: svc-r1-r2 and svc-r2-r3. Do you want to delete all of them, or specific ones?",
         candidates=["Delete all", "svc-r1-r2 only", "svc-r2-r3 only"])
```

Then for each selected service, call `tfs_delete_service(name_or_uuid=...)` once.
