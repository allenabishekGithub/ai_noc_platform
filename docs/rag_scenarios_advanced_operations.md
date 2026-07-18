# TFS Advanced Operations Scenarios

## Scenario: Discover what TFS operations are available

**Trigger phrases:** "what can you do", "what operations are supported", "list capabilities", "what TFS operations exist"

```
tfs_capabilities(detail="summary")
→ {
    "context":    {"read": ["get", "ids", "list", "dummy"], "write": ["delete", "set", "set_many"]},
    "topology":   {"read": ["details", "get", "ids", "list"], "write": ["delete", "set", "set_many"]},
    "service":    {"read": ["get", "ids", "list"], "write": ["create_many", "delete", "update"]},
    "device":     {"read": ["get", "ids", "list"], "write": ["add_many", "configure", "delete"]},
    "link":       {"read": ["get", "ids", "list"], "write": ["delete", "set", "set_many"]},
    "connection": {"read": ["get", "ids", "list"], "write": {}},
    "policyrule": {"read": ["get", "ids", "list"], "write": {}},
    "optical_slice": {"read": {}, "write": ["create", "delete"]},
    "media_channel": {"read": {}, "write": ["allocate", "release"]}
  }
```

For the full endpoint and proto mapping use `tfs_capabilities(detail="full")`.

---

## Scenario: Read topology details

**Trigger phrases:** "topology details", "what is in the topology", "show topology", "get topology"

```
tfs_read(resource="topology", operation="details", context_uuid="admin", topology_uuid="admin")
→ {
    "result": {
      "devices": [...],
      "links": [
        {"link_id": {"link_uuid": {"uuid": "link-001"}},
         "link_endpoint_ids": [
           {"device_id": {"device_uuid": {"uuid": "a1b2"}}, "endpoint_uuid": {"uuid": "ep-001"}},
           {"device_id": {"device_uuid": {"uuid": "c3d4"}}, "endpoint_uuid": {"uuid": "ep-010"}}
         ]}
      ]
    }
  }
```

---

## Scenario: Read a specific device

**Trigger phrases:** "get device R1", "device details", "show device info", "device configuration"

First get the device UUID from `tfs_inventory()`, then:

```
tfs_read(resource="device", operation="get", device_uuid="a1b2...")
→ {
    "result": {
      "device_id": {"device_uuid": {"uuid": "a1b2..."}},
      "name": "R1",
      "device_type": "emu-packet-router",
      "device_operational_status": 2
    }
  }
```

`device_operational_status` values: 0=UNDEFINED, 1=DISABLED, 2=ENABLED.

---

## Scenario: Read a specific link

**Trigger phrases:** "show links", "get link", "what links exist", "link between R1 and R2"

```
tfs_read(resource="link", operation="list")
→ {
    "result": {
      "links": [
        {"link_id": {"link_uuid": {"uuid": "link-001"}},
         "link_endpoint_ids": [...]}
      ]
    }
  }
```

---

## Scenario: Read all contexts

**Trigger phrases:** "list contexts", "what contexts exist", "show contexts"

```
tfs_read(resource="context", operation="list")
→ {
    "result": {
      "contexts": [
        {"context_id": {"context_uuid": {"uuid": "admin"}}, "name": "admin"}
      ]
    }
  }
```

---

## Scenario: Read policy rules

**Trigger phrases:** "policy rules", "show policies", "list policy rules"

```
tfs_read(resource="policyrule", operation="list")
→ {
    "result": {
      "policyrules": [...]
    }
  }
```

Note: policy rule writes are not exposed through the current MCP server.

---

## Scenario: Create an optical slice

**Trigger phrases:** "create optical slice", "provision optical slice", "optical slice"

**Do not confuse this with optical connectivity service.**

| User intent | Correct tool | Controller API | TFS service type |
| --- | --- | --- | --- |
| "create optical slice" | `tfs_restconf_create_optical_slice` or `tfs_create_optical_slice` | `/restconf/optical-slice/v1/service/<slice_id>` | `TAPI_CONNECTIVITY_SERVICE` created by optical-slice handler |
| "create optical connectivity service" | `tfs_service_create_optical_connectivity` or `tfs_provision_optical_connectivity_service` | `/tfs-api/context/<context_uuid>/services` then `/service/<uuid>` update | `OPTICAL_CONNECTIVITY` |

**Requires the full slice payload — ask user for the slice_id and slice configuration.**

Use the optical-slice API path through `tfs_restconf_create_optical_slice`
or `tfs_create_optical_slice`. Do **not** map this intent to
`tfs_provision_service` or `tfs_service_create_optical_connectivity` because
those create connectivity services, not optical-slice RESTCONF requests.

```
declare_tfs_intent(
  intent="optical_slice_restconf",
  selected_tool="tfs_restconf_create_optical_slice",
  reason="User explicitly asked for optical slice, not optical connectivity service."
)

ask_user("Please provide: (1) the slice ID, and (2) the optical slice configuration payload.")

tfs_restconf_create_optical_slice(
  slice_id="slice-001",
  slice={"<optical slice config from user>"}
)
```

Note: the operation executes immediately. Ensure required IDs and payload are
grounded before invoking it.

After create, verify the resulting slice state:

```
tfs_read(resource="slice", operation="list", context_uuid="admin")
```

If the matching slice is `PLANNED`, report it as pending and re-check after a
short delay. Only report ACTIVE when a tool result shows ACTIVE.

---

## Scenario: Allocate a media channel

**Trigger phrases:** "allocate media channel", "flex-grid allocation", "spectrum allocation"

```
ask_user("Please provide: (1) the allocation ID, and (2) the media channel configuration.")

tfs_write(
  resource="media_channel",
  operation="allocate",
  allocation_id="alloc-001",
  payload={"<media channel config from user>"}
)
```

---

## Scenario: Release a media channel

**Trigger phrases:** "release media channel", "deallocate spectrum", "free media channel"

Single call:

```
tfs_write(resource="media_channel", operation="release", allocation_id="alloc-001")
```

---

## Scenario: Read DSCM OpenConfig state for a device

**Trigger phrases:** "OpenConfig", "DSCM OC", "device OC state", "read OpenConfig path"

```
tfs_read(
  resource="dscm_oc",
  operation="get",
  device_uuid="a1b2...",
  rc_path="openconfig-interfaces:interfaces"
)
→ {"result": {"<OpenConfig response>"}}
```

If the user doesn't know the device UUID, call `tfs_inventory()` first to find it by device name.

---

## Scenario: Check TFS controller health

**Trigger phrases:** "is TFS up", "health check", "controller status", "is the controller reachable"

```
tfs_read(resource="health", operation="check")
→ {"result": {"status": "ok", "http_status": 200}}
```

If the result contains `"status": "error"`, the controller is not reachable. Tell the user.

---

## Scenario: List slices

**Trigger phrases:** "list slices", "what slices exist", "show slices"

```
tfs_read(resource="slice", operation="list", context_uuid="admin")
→ {"result": {"slices": [...]}}
```

---

## Scenario: Get a specific service by UUID

**Trigger phrases:** "get service by UUID", "service details", "show service f5e6..."

```
tfs_read(resource="service", operation="get", service_uuid="f5e6...", context_uuid="admin")
→ {
    "result": {
      "service_id": {"service_uuid": {"uuid": "f5e6..."}},
      "name": "svc-r1-r2",
      "service_type": "SERVICETYPE_L3NM",
      "service_status": {"service_status": "SERVICESTATUS_ACTIVE"},
      "service_endpoint_ids": [...]
    }
  }
```
