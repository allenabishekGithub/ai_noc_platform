# TFS Service Provisioning Scenarios

## Scenario: Provision an L3VPN service between two devices

**Trigger phrases:** "create a service", "provision L3VPN", "connect R1 to R2", "set up a VPN"

**Step-by-step tool call sequence:**

1. Call `tfs_inventory()` to discover devices and their endpoint names.
2. Identify src_device and dst_device as exactly the devices the user named.
   - CE/datacenter devices (DC1–DC4) → use endpoint `int`
   - PE/router devices (R1–R4) → use endpoint `eth0` unless user specifies otherwise
3. Call `tfs_provision_service()` immediately. Do not ask for confirmation first —
   provisioning is additive and reversible. The user's request is the confirmation.

**Example exchange:**

User: "Create an L3VPN service between DC1 and DC4 at 4 Gbps."

Tool calls:
```
tfs_inventory()
→ {
    "devices": [
      {"name": "DC1", "type": "emu-datacenter", "endpoints": [{"name": "int"}, {"name": "eth0"}]},
      {"name": "DC4", "type": "emu-datacenter", "endpoints": [{"name": "int"}, {"name": "eth0"}]},
      ...
    ],
    "services": []
  }

tfs_provision_service(
  service_type="l3vpn",
  src_device="DC1",
  src_endpoint="int",
  dst_device="DC4",
  dst_endpoint="int",
  capacity_gbps=4.0
)
→ "Service 'l3-dc1-dc4' provisioned successfully. UUID: f5e6..., status: ACTIVE."
```

**Response to user:** "L3VPN service 'l3-dc1-dc4' is ACTIVE between DC1 and DC4 with 4 Gbps capacity (UUID: f5e6...)."

---

## Scenario: Provision an L2VPN service

**Trigger phrases:** "create L2VPN", "layer 2 service", "L2 connectivity"

**Same sequence as L3VPN, but with service_type="l2vpn".**

**Example:**
```
tfs_provision_service(
  name="l2-svc-r1-r3",
  service_type="l2vpn",
  src_device="R1",
  src_endpoint="1/2",
  dst_device="R3",
  dst_endpoint="1/1"
)
→ "Service 'l2-svc-r1-r3' provisioned successfully. UUID: a9b0..., status: ACTIVE."
```

Note: L2VPN services do not generate per-endpoint IP config rules — only L3VPN does.

---

## Scenario: Provision an optical connectivity service

**Trigger phrases:** "optical service", "optical connectivity", "wavelength service", "WDM service"

**Do not confuse this with optical slice.**

| User intent | Correct tool | Controller API | TFS service type |
| --- | --- | --- | --- |
| "create optical connectivity service" | `tfs_service_create_optical_connectivity` or `tfs_provision_optical_connectivity_service` | TFS service create/update API | `OPTICAL_CONNECTIVITY` |
| "create optical slice" | `tfs_restconf_create_optical_slice` or `tfs_create_optical_slice` | RESTCONF optical-slice API | `TAPI_CONNECTIVITY_SERVICE` |

Use `tfs_service_create_optical_connectivity` or
`tfs_provision_optical_connectivity_service` (preferred).  
`tfs_provision_service(service_type="optical", ...)` is supported for backward
compatibility and internally routes to the same optical workflow.

Use optical endpoints from `tfs_inventory`. In the optical emulated topology,
transponders expose:

- `client`: copper/DC-facing endpoint
- `line`: optical/ROADM-facing endpoint

For optical connectivity between transponders, use `line`, not `client`.

**Example:**
```
declare_tfs_intent(
  intent="optical_connectivity_service",
  selected_tool="tfs_service_create_optical_connectivity",
  reason="User asked for optical connectivity service, not optical slice."
)

tfs_service_create_optical_connectivity(
  name="optical-tp-dc1-tp-dc3",
  src_device="TP-DC1",
  src_endpoint="line",
  dst_device="TP-DC3",
  dst_endpoint="line",
  capacity_gbps=100.0,
  bidirectional=True,
  optical_band_width_ghz=200,
  optical_type="multi_granular"
)
→ "Service 'optical-svc-r1-r2' provisioned successfully. UUID: b2c3..., status: ACTIVE."
```

For optical services, check whether `tfs_inventory` shows optical endpoint
types. If the endpoint type contains "optical" or "PHOTONIC", those are the
correct endpoints to use. Do not use copper/client endpoints for optical
connectivity services.

---

## Scenario: Provision a service when endpoint names are unknown

**Situation:** User says "connect R1 to R2" without specifying endpoint names.

**Correct behavior:** Call `tfs_inventory()` first, show the user the available endpoints, then call `ask_user()` to let them choose.

```
tfs_inventory()
→ R1 has endpoints: 1/1, 1/2, opt/1
  R2 has endpoints: 1/1, 1/2, opt/1

ask_user(
  "Which endpoints should I use for the service?",
  candidates=["R1:1/1 → R2:1/1", "R1:1/2 → R2:1/2", "R1:opt/1 → R2:opt/1"]
)
```

Never guess endpoint names. Always confirm with the user if not specified.

---

## Scenario: Service provisioning fails — device not found

**Situation:** `tfs_provision_service` returns an error like: "device 'Router1' endpoint '1/1' not found."

**Correct behavior:**
1. Call `tfs_inventory()` again to get the exact device names from the controller.
2. Show the user the actual device names.
3. Call `ask_user()` to confirm which device they meant.
4. Retry `tfs_provision_service` with the corrected name.

```
tfs_inventory()
→ actual device names: ["R1", "R2", "R3"] (not "Router1")

ask_user("I could not find 'Router1'. Did you mean one of: R1, R2, R3?",
         candidates=["R1", "R2", "R3"])
```

---

## Scenario: Service provisioning returns PLANNED instead of ACTIVE

**Situation:** `tfs_provision_service` returns that the service is still `SERVICESTATUS_PLANNED` after polling.

**Meaning:** The TFS controller received the request and is computing the path. This is normal for controllers with slower path computation.

**Correct behavior:** Wait and check again with `tfs_service_status`.

```
tfs_service_status(name_or_uuid="svc-r1-r2")
→ {"name": "svc-r1-r2", "uuid": "f5e6...", "status": "SERVICESTATUS_ACTIVE", ...}
```

**Response to user:** "The service was still computing when I checked. I just verified again and it is now ACTIVE."

If the service remains `SERVICESTATUS_UNDEFINED` after polling, do not describe
that as normal path computation. It usually means the service did not advance
into provisioning. Check that the endpoints are valid for the requested service
type, especially that optical connectivity uses optical endpoints such as
`TP-DC1:line` and `TP-DC3:line`.

If an optical connectivity service has `SERVICESTATUS_UNDEFINED` and its
`service_endpoint_ids` list is empty, it is only a reserved service shell. The
create step ran but the update/provisioning intent did not persist. Tell the
operator to delete that stale service before retrying with optical endpoints.

---

## Scenario: Check whether a service already exists before provisioning

**Situation:** User asks to create a service but one with the same name might already exist.

**Correct behavior:** Call `tfs_inventory()` first and check the services list. If a service with that name already exists, tell the user before proceeding.

```
tfs_inventory()
→ services: [{"name": "svc-r1-r2", "uuid": "f5e6...", "status": "SERVICESTATUS_ACTIVE"}]
```

**Response:** "A service named 'svc-r1-r2' already exists (UUID: f5e6..., status: ACTIVE). Do you want to create a new one with a different name, or would you like to check the existing service?"
