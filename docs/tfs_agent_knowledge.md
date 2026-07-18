# TFS Agent Knowledge Base

## TeraFlowSDN Overview

TeraFlowSDN (TFS) is an SDN controller that exposes a northbound REST API (NBI).
Resources are organised hierarchically:

  Context > Topology > Device, Link, Service, Slice, Connection, PolicyRule

The MCP server wraps these REST endpoints as callable tools named `mcp__tfs_*`.
The agent normally uses higher-level workflow tools:
  - `tfs_capabilities` lists the proto/NBI/MCP-aligned operation catalog.
  - `tfs_read` performs generic read-only operations.
  - `tfs_write` performs generic create/update/delete operations with proto-shaped payloads in a single call.
  - `tfs_create_optical_slice_from_endpoints` builds optical-slice payloads from endpoint names.
  - `tfs_inventory`, `tfs_provision_service`, `tfs_provision_optical_connectivity_service`,
    `tfs_delete_service`, and `tfs_service_status` cover common service workflows.

Raw MCP tools remain useful for debugging when enabled, but normal reasoning
should prefer the workflow tools.

`docs/tfs_context.proto` mirrors the upstream TFS `context.proto` schema in this
repository snapshot.
The TFS NBI/MCP layer does not expose every proto RPC: event streams, select
filters, `UnsetService`, `UnsetSlice`, `SetConnection`, `RemoveConnection`,
`DeleteServiceConfigRule`, and several experimental optical proto RPCs are not
present in the current MCP tool definitions.

---

## Resource Hierarchy and Default Values

- Default context_uuid: "admin"
- Default topology_uuid: "admin"
- Always verify by calling list tools — never assume UUIDs.

---

## Service Lifecycle — CREATE

Step 1: Discover inventory
  Prefer tfs_inventory.
  If raw MCP tools are enabled:
  Call mcp__tfs_list_contexts → note context_uuid (usually "admin")
  Call mcp__tfs_list_topologies with context_uuid → note topology_uuid
  Call mcp__tfs_list_devices → get all device UUIDs and their endpoints
  Call mcp__tfs_list_services → check for existing services to avoid duplicates

Step 2: Resolve endpoint IDs
  From the device list, find the device UUIDs and endpoint UUIDs for the requested endpoints.
  Endpoint IDs have this shape:
    {
      "topology_id": {
        "context_id": { "context_uuid": { "uuid": "<context_uuid>" } },
        "topology_uuid": { "uuid": "<topology_uuid>" }
      },
      "device_id": { "device_uuid": { "uuid": "<device_uuid>" } },
      "endpoint_uuid": { "uuid": "<endpoint_uuid>" }
    }

Step 3: Reserve the service UUID
  Call mcp__tfs_create_services with:
    {
      "context_uuid": "<context_uuid>",
      "services": [{
        "service_id": {
          "context_id": { "context_uuid": { "uuid": "<context_uuid>" } },
          "service_uuid": { "uuid": "<new-uuid>" }
        },
        "name": "<service-name>",
        "service_type": "SERVICETYPE_L3NM"
      }]
    }
  This reserves the UUID. TFS does not provision yet.

Step 4: Update the service with full intent
  Call mcp__tfs_update_service with:
    {
      "context_uuid": "<context_uuid>",
      "service_uuid": "<reserved-uuid>",
      "service": {
        "service_id": { ... },
        "name": "<service-name>",
        "service_type": "SERVICETYPE_L3NM",
        "service_endpoint_ids": [ <endpoint_id_1>, <endpoint_id_2> ],
        "service_constraints": [
          { "action": "CONSTRAINTACTION_SET",
            "sla_capacity": { "capacity_gbps": 10.0 } }
        ],
        "service_config": {
          "config_rules": [
            { "action": "CONFIGACTION_SET",
              "custom": { "resource_key": "/settings",
                          "resource_value": "{\"mtu\": 1500}" } }
          ]
        }
      }
    }
  TFS computes the path and provisions the service.

  Note for optical connectivity:
  prefer `tfs_provision_optical_connectivity_service` so constraints match what
  the optical service handler expects (custom constraints such as
  `bandwidth[gbps]`, `bidirectionality`, and `optical-band-width[GHz]`).

Step 5: Verify
  Call mcp__tfs_get_service with context_uuid and service_uuid.
  Check: service_status.service_status == "SERVICESTATUS_ACTIVE"
  Retry if not yet active. Optical connectivity can take longer (for example
  up to ~60 seconds) while path/spectrum computation completes.

---

## Service Lifecycle — DELETE

Step 1: List services to get exact service UUID
  Prefer tfs_delete_service with name or UUID. It resolves the target service,
  captures pre-delete connection diagnostics, and issues the delete.
  If raw MCP tools are enabled:
  Call mcp__tfs_list_services with context_uuid
  Find the service by name or UUID from the user's request.

Step 2: Delete
  Prefer tfs_delete_service.
  If raw MCP tools are enabled:
  Call mcp__tfs_delete_service with context_uuid and service_uuid

Step 3: Verify deletion
  Call mcp__tfs_list_services again and confirm the service is no longer listed.

---

## Service Types

  SERVICETYPE_L3NM              - L3VPN (Layer 3 Virtual Private Network)
  SERVICETYPE_L2NM              - L2VPN (Layer 2 Virtual Private Network)
  SERVICETYPE_OPTICAL_CONNECTIVITY - Optical connectivity service
  SERVICETYPE_E2E               - End-to-end generic service

---

## Config Rule Shape

  {
    "action": "CONFIGACTION_SET",      // or "CONFIGACTION_DELETE"
    "custom": {
      "resource_key": "/settings",     // config path
      "resource_value": "{\"mtu\": 1500}"  // JSON-serialised value string
    }
  }

Common resource keys:
  /settings                          - Service-level settings (MTU etc.)
  /device[<name>]/endpoint[<ep>]/settings  - Per-endpoint settings (IP, prefix, sub_interface_index)

---

## Constraint Shape

  { "action": "CONSTRAINTACTION_SET",
    "sla_capacity": { "capacity_gbps": 10.0 } }

  { "action": "CONSTRAINTACTION_SET",
    "sla_latency": { "e2e_latency_ms": 5.0 } }

  { "action": "CONSTRAINTACTION_SET",
    "endpoint_location": {
      "endpoint_id": { ... },
      "location": { "region": "A" }
    } }

---

## Device Structure

Devices returned by mcp__tfs_list_devices have this shape:
  {
    "device_id": { "device_uuid": { "uuid": "<uuid>" } },
    "name": "<name>",
    "device_type": "emu-packet-router",
    "device_operational_status": 2,   // 0=UNDEFINED, 1=DISABLED, 2=ENABLED
    "device_endpoints": [
      {
        "endpoint_id": {
          "device_id": { "device_uuid": { "uuid": "<device_uuid>" } },
          "endpoint_uuid": { "uuid": "<ep_uuid>" }
        },
        "name": "<ep_name>",
        "endpoint_type": "..."
      }
    ]
  }

---

## Optical Tools

For optical services the MCP server exposes additional tools:
  mcp__tfs_optical_slice_create   - Create an optical slice
  mcp__tfs_optical_slice_delete   - Delete an optical slice
  mcp__tfs_media_channel_allocate - Allocate a media channel (flex-grid)
  mcp__tfs_media_channel_release  - Release a media channel
  mcp__tfs_dscm_oc_get            - Read O-Cloud resource
  mcp__tfs_dscm_oc_post           - Create/update O-Cloud resource
  mcp__tfs_dscm_oc_delete         - Delete O-Cloud resource

---

## Error Handling

TFS NBI returns HTTP 4xx/5xx on validation errors.
The MCP server wraps these as: { "error": "HTTP 400: ...", "status": "error" }

Common issues:
  - Missing required fields in service_id or endpoint_id → add the missing nested field
  - service_uuid already exists → use a different UUID
  - Device UUID not found → call list_devices and re-check
  - SERVICESTATUS_PLANNED instead of ACTIVE → TFS is still computing; retry status checks.

---

## Topology Details

Call mcp__tfs_get_topology_details with context_uuid and topology_uuid to get
full device and link lists for a specific topology. This is the best source for
resolving endpoint IDs when creating services.
