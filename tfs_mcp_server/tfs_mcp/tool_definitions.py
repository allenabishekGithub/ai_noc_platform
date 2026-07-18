from typing import Any, List

from .dependencies import Tool


# Reusable JSON-Schema fragments
_CTX = {"context_uuid": {"type": "string", "description": "UUID of the TFS Context (e.g., 'admin')"}}
_TOPO = {"topology_uuid": {"type": "string", "description": "UUID of the Topology"}}
_SVC = {"service_uuid": {"type": "string", "description": "UUID of the Service"}}
_SLC = {"slice_uuid": {"type": "string", "description": "UUID of the Slice"}}
_DEV = {"device_uuid": {"type": "string", "description": "UUID of the Device"}}
# =============================================================================
# JSON-Schema helpers derived from context.proto
# The TFS NBI REST layer serialises proto messages to/from JSON using the
# standard protobuf JSON mapping (snake_case field names, enum as integer).
# These schemas let the LLM know exactly what to send and what to expect.
# =============================================================================

# ---- primitive wrapper ----
_UUID_PROP = {"type": "object", "properties": {"uuid": {"type": "string"}}, "required": ["uuid"]}

# ---- ID messages ----
_CTX_ID_S = {"type": "object", "description": "ContextId",
             "properties": {"context_uuid": _UUID_PROP}, "required": ["context_uuid"]}
_TOPO_ID_S = {"type": "object", "description": "TopologyId",
              "properties": {"context_id": _CTX_ID_S, "topology_uuid": _UUID_PROP},
              "required": ["context_id", "topology_uuid"]}
_DEV_ID_S = {"type": "object", "description": "DeviceId",
             "properties": {"device_uuid": _UUID_PROP}, "required": ["device_uuid"]}
_SVC_ID_S = {"type": "object", "description": "ServiceId",
             "properties": {"context_id": _CTX_ID_S, "service_uuid": _UUID_PROP},
             "required": ["context_id", "service_uuid"]}
_SLC_ID_S = {"type": "object", "description": "SliceId",
             "properties": {"context_id": _CTX_ID_S, "slice_uuid": _UUID_PROP},
             "required": ["context_id", "slice_uuid"]}
_LNK_ID_S = {"type": "object", "description": "LinkId",
             "properties": {"link_uuid": _UUID_PROP}, "required": ["link_uuid"]}
_CON_ID_S = {"type": "object", "description": "ConnectionId",
             "properties": {"connection_uuid": _UUID_PROP}, "required": ["connection_uuid"]}
_POL_ID_S = {"type": "object", "description": "PolicyRuleId",
             "properties": {"policyrule_uuid": _UUID_PROP}, "required": ["policyrule_uuid"]}

# ---- EndPointId ----
_ENDPOINT_ID_S = {
    "type": "object",
    "description": "EndPointId: identifies a device port/interface within a topology",
    "properties": {
        "topology_id": _TOPO_ID_S,
        "device_id": _DEV_ID_S,
        "endpoint_uuid": _UUID_PROP,
    },
}

# ---- ConfigRule ----
_CONFIG_RULE_S = {
    "type": "object",
    "description": "ConfigRule - action: 1=SET, 2=DELETE",
    "properties": {
        "action": {
            "oneOf": [{"type": "integer"}, {"type": "string"}],
            "description": "ConfigActionEnum: 1=CONFIGACTION_SET, 2=CONFIGACTION_DELETE",
        },
        "custom": {
            "type": "object",
            "description": "Custom key/value pair config rule",
            "properties": {
                "resource_key": {"type": "string",
                                 "description": "Config path, e.g. '/endpoints/endpoint[uuid]/<uuid>/name'"},
                "resource_value": {"type": "string",
                                   "description": "JSON-serialised value string"},
            },
        },
    },
    "required": ["action"],
}

# ---- Constraint ----
_CONSTRAINT_S = {
    "type": "object",
    "description": "Constraint - action: 1=SET, 2=DELETE. Include exactly one constraint field.",
    "properties": {
        "action": {
            "oneOf": [{"type": "integer"}, {"type": "string"}],
            "description": "ConstraintActionEnum: 1=SET, 2=DELETE",
        },
        "custom": {"type": "object",
                   "properties": {"constraint_type": {"type": "string"},
                                  "constraint_value": {"type": "string"}}},
        "sla_capacity": {"type": "object",
                         "properties": {"capacity_gbps": {"type": "number"}}},
        "sla_latency": {"type": "object",
                        "properties": {"e2e_latency_ms": {"type": "number"}}},
        "sla_availability": {"type": "object",
                             "properties": {"num_disjoint_paths": {"type": "integer"},
                                            "all_active": {"type": "boolean"},
                                            "availability": {"type": "number",
                                                             "description": "0.0-100.0 %"}}},
        "endpoint_location": {"type": "object",
                              "properties": {"endpoint_id": _ENDPOINT_ID_S,
                                             "location": {"type": "object",
                                                          "properties": {
                                                              "region": {"type": "string"},
                                                              "gps_position": {"type": "object",
                                                                             "properties": {
                                                                                 "latitude": {"type": "number"},
                                                                                 "longitude": {"type": "number"}}}}}}},
    },
    "required": ["action"],
}

# ---- URL path-parameter helpers ----
_P_CTX = {"context_uuid": {"type": "string", "description": "Context UUID (default 'admin')"}}
_P_TOPO = {"topology_uuid": {"type": "string", "description": "Topology UUID"}}
_P_SVC = {"service_uuid": {"type": "string", "description": "Service UUID"}}
_P_SLC = {"slice_uuid": {"type": "string", "description": "Slice UUID"}}
_P_DEV = {"device_uuid": {"type": "string", "description": "Device UUID"}}
_P_LNK = {"link_uuid": {"type": "string", "description": "Link UUID"}}
_P_CON = {"connection_uuid": {"type": "string", "description": "Connection UUID"}}
_P_POL = {"policyrule_uuid": {"type": "string", "description": "PolicyRule UUID"}}

# ---- Full proto message body schemas for POST/PUT operations ----

_CONTEXT_BODY = {
    "type": "object",
    "description": (
        "Context proto message (JSON). "
        "Returns: {context_id:{context_uuid:{uuid}}, name, "
        "topology_ids:[{context_id,topology_uuid}], "
        "service_ids:[{context_id,service_uuid}], "
        "slice_ids:[{context_id,slice_uuid}]}"
    ),
    "properties": {
        "context_id": _CTX_ID_S,
        "name": {"type": "string"},
        "topology_ids": {"type": "array", "items": _TOPO_ID_S},
        "service_ids": {"type": "array", "items": _SVC_ID_S},
        "slice_ids": {"type": "array", "items": _SLC_ID_S},
    },
    "required": ["context_id"],
}

_TOPOLOGY_BODY = {
    "type": "object",
    "description": (
        "Topology proto message (JSON). "
        "Returns: {topology_id:{context_id,topology_uuid}, name, "
        "device_ids:[{device_uuid}], link_ids:[{link_uuid}]}"
    ),
    "properties": {
        "topology_id": _TOPO_ID_S,
        "name": {"type": "string"},
        "device_ids": {"type": "array", "items": _DEV_ID_S},
        "link_ids": {"type": "array", "items": _LNK_ID_S},
    },
    "required": ["topology_id"],
}

_DEVICE_BODY = {
    "type": "object",
    "description": (
        "Device proto message (JSON). "
        "device_operational_status: 0=UNDEFINED, 1=DISABLED, 2=ENABLED. "
        "device_drivers (DeviceDriverEnum ints): 0=UNDEFINED/emulated, 1=OPENCONFIG, "
        "2=TRANSPORT_API, 3=P4, 4=IETF_NETWORK_TOPOLOGY, 5=ONF_TR_532, 6=XR, "
        "8=GNMI_OPENCONFIG, 9=OPTICAL_TFS, 11=OC, 12=QKD, 13=IETF_L3VPN, "
        "14=IETF_SLICE, 18=RYU, 20=OPENROADM, 21=RESTCONF_OPENCONFIG. "
        "Returns: {device_id, name, device_type, device_config:{config_rules}, "
        "device_operational_status, device_drivers, device_endpoints:[EndPoint]}"
    ),
    "properties": {
        "device_id": _DEV_ID_S,
        "name": {"type": "string"},
        "device_type": {"type": "string",
                        "description": "Free-form type, e.g. 'emu-packet-router', 'emu-optical-roadm'"},
        "device_config": {
            "type": "object",
            "properties": {"config_rules": {"type": "array", "items": _CONFIG_RULE_S}},
        },
        "device_operational_status": {"type": "integer",
                                      "description": "0=UNDEFINED, 1=DISABLED, 2=ENABLED"},
        "device_drivers": {"type": "array", "items": {"type": "integer"},
                           "description": "List of DeviceDriverEnum integer values"},
        "device_endpoints": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "endpoint_id": _ENDPOINT_ID_S,
                    "name": {"type": "string"},
                    "endpoint_type": {"type": "string"},
                },
            },
        },
    },
    "required": ["device_id"],
}

_SERVICE_BODY = {
    "type": "object",
    "description": (
        "Service proto message (JSON). "
        "service_type (ServiceTypeEnum): 0=UNKNOWN, 1=L3NM, 2=L2NM, "
        "3=TAPI_CONNECTIVITY_SERVICE, 4=TE, 5=E2E, 6=OPTICAL_CONNECTIVITY, "
        "7=QKD, 8=L1NM, 9=INT, 10=ACL, 11=IP_LINK, 12=TAPI_LSP, 13=IPOWDM, 14=UPF. "
        "service_status.service_status (ServiceStatusEnum): 0=UNDEFINED, 1=PLANNED, "
        "2=ACTIVE, 3=UPDATING, 4=PENDING_REMOVAL, 5=SLA_VIOLATED. "
        "Returns: {service_id, name, service_type, service_endpoint_ids:[EndPointId], "
        "service_constraints:[Constraint], service_status, service_config:{config_rules}, timestamp}"
    ),
    "properties": {
        "service_id": _SVC_ID_S,
        "name": {"type": "string"},
        "service_type": {
            "oneOf": [{"type": "integer"}, {"type": "string"}],
            "description": "ServiceTypeEnum value",
        },
        "service_endpoint_ids": {"type": "array", "items": _ENDPOINT_ID_S},
        "service_constraints": {"type": "array", "items": _CONSTRAINT_S},
        "service_status": {
            "type": "object",
            "properties": {
                "service_status": {
                    "oneOf": [{"type": "integer"}, {"type": "string"}],
                    "description": "ServiceStatusEnum value",
                }
            },
        },
        "service_config": {
            "type": "object",
            "properties": {"config_rules": {"type": "array", "items": _CONFIG_RULE_S}},
        },
    },
    "required": ["service_id"],
}

_SLICE_BODY = {
    "type": "object",
    "description": (
        "Slice proto message (JSON). "
        "slice_status.slice_status (SliceStatusEnum): 0=UNDEFINED, 1=PLANNED, "
        "2=INIT, 3=ACTIVE, 4=DEINIT, 5=SLA_VIOLATED. "
        "Returns: {slice_id, name, slice_endpoint_ids:[EndPointId], "
        "slice_constraints:[Constraint], slice_service_ids:[ServiceId], "
        "slice_subslice_ids:[SliceId], slice_status, slice_config:{config_rules}, "
        "slice_owner:{owner_uuid,owner_string}, timestamp}"
    ),
    "properties": {
        "slice_id": _SLC_ID_S,
        "name": {"type": "string"},
        "slice_endpoint_ids": {"type": "array", "items": _ENDPOINT_ID_S},
        "slice_constraints": {"type": "array", "items": _CONSTRAINT_S},
        "slice_service_ids": {"type": "array", "items": _SVC_ID_S},
        "slice_subslice_ids": {"type": "array", "items": _SLC_ID_S},
        "slice_status": {
            "type": "object",
            "properties": {"slice_status": {"type": "integer",
                                              "description": "SliceStatusEnum value"}},
        },
        "slice_config": {
            "type": "object",
            "properties": {"config_rules": {"type": "array", "items": _CONFIG_RULE_S}},
        },
        "slice_owner": {
            "type": "object",
            "properties": {"owner_uuid": _UUID_PROP, "owner_string": {"type": "string"}},
        },
    },
    "required": ["slice_id"],
}

_LINK_BODY = {
    "type": "object",
    "description": (
        "Link proto message (JSON). "
        "link_type (LinkTypeEnum): 0=UNKNOWN, 1=COPPER, 2=FIBER, 3=RADIO, "
        "4=VIRTUAL, 5=MANAGEMENT, 6=REMOTE. "
        "Returns: {link_id:{link_uuid}, name, link_type, "
        "link_endpoint_ids:[EndPointId] (exactly 2), "
        "attributes:{is_bidirectional, total_capacity_gbps, used_capacity_gbps}}"
    ),
    "properties": {
        "link_id": _LNK_ID_S,
        "name": {"type": "string"},
        "link_type": {"type": "integer",
                      "description": "LinkTypeEnum: 0=UNKNOWN,1=COPPER,2=FIBER,3=RADIO,4=VIRTUAL,5=MANAGEMENT,6=REMOTE"},
        "link_endpoint_ids": {"type": "array", "items": _ENDPOINT_ID_S,
                              "description": "Exactly 2 EndPointIds defining the endpoints of the link"},
        "attributes": {
            "type": "object",
            "properties": {
                "is_bidirectional": {"type": "boolean"},
                "total_capacity_gbps": {"type": "number"},
                "used_capacity_gbps": {"type": "number"},
            },
        },
    },
    "required": ["link_id"],
}

# Connection is read-only from the NBI - no body schema needed.
# Returns: {connection_id:{connection_uuid}, service_id:{context_id,service_uuid},
#           path_hops_endpoint_ids:[EndPointId], sub_service_ids:[ServiceId],
#           settings:{l0:{lsp_symbolic_name}, l2:{src_mac,dst_mac,ether_type,vlan_id,...},
#                     l3:{src_ip,dst_ip,dscp,protocol,ttl},
#                     l4:{src_port,dst_port,tcp_flags,ttl}}}


# =============================================================================
# Tool Definitions
# =============================================================================

if Tool is None:
    TOOLS: List[Any] = []
else:
    TOOLS: List[Tool] = [
        Tool(name="tfs_list_context_ids",
             description=(
                 "List all TFS Context IDs (GET /tfs-api/context_ids). "
                 "Returns: {context_ids:[{context_uuid:{uuid}}]}"
             ),
             inputSchema={"type": "object", "properties": {}, "required": []}),
        Tool(name="tfs_list_contexts",
             description=(
                 "List all TFS Contexts with full details (GET /tfs-api/contexts). "
                 "Returns: {contexts:[{context_id, name, topology_ids, service_ids, slice_ids}]}"
             ),
             inputSchema={"type": "object", "properties": {}, "required": []}),
        Tool(name="tfs_set_contexts",
             description=(
                 "Create / update one or more TFS Contexts (POST /tfs-api/contexts). "
                 "Returns: {context_ids:[ContextId]}"
             ),
             inputSchema={
                 "type": "object",
                 "properties": {
                     "contexts": {"type": "array", "items": _CONTEXT_BODY,
                                  "description": "List of Context proto messages"}
                 },
                 "required": ["contexts"],
             }),
        Tool(name="tfs_get_dummy_contexts",
             description=(
                 "Get a single aggregated snapshot with all contexts, topologies, devices, links, "
                 "slices, services, and connections (GET /tfs-api/dummy_contexts). "
                 "Returns: {contexts:[Context], topologies:[Topology], devices:[Device], "
                 "links:[Link], slices:[Slice], services:[Service], connections:[Connection]}"
             ),
             inputSchema={"type": "object", "properties": {}, "required": []}),
        Tool(name="tfs_get_context",
             description=(
                 "Get a specific TFS Context by UUID (GET /tfs-api/context/<context_uuid>). "
                 "Returns: Context {context_id, name, topology_ids, service_ids, slice_ids}"
             ),
             inputSchema={"type": "object", "properties": _P_CTX, "required": ["context_uuid"]}),
        Tool(name="tfs_set_context",
             description=(
                 "Create / update a TFS Context (PUT /tfs-api/context/<context_uuid>). "
                 "Returns: ContextId {context_uuid:{uuid}}"
             ),
             inputSchema={"type": "object",
                          "properties": {**_P_CTX, "context": _CONTEXT_BODY},
                          "required": ["context_uuid", "context"]}),
        Tool(name="tfs_delete_context",
             description="Delete a TFS Context (DELETE /tfs-api/context/<context_uuid>). Returns: {}",
             inputSchema={"type": "object", "properties": _P_CTX, "required": ["context_uuid"]}),
        Tool(name="tfs_list_topology_ids",
             description=(
                 "List Topology IDs within a Context (GET /tfs-api/context/<context_uuid>/topology_ids). "
                 "Returns: {topology_ids:[{context_id, topology_uuid:{uuid}}]}"
             ),
             inputSchema={"type": "object", "properties": _P_CTX, "required": ["context_uuid"]}),
        Tool(name="tfs_list_topologies",
             description=(
                 "List Topologies within a Context (GET /tfs-api/context/<context_uuid>/topologies). "
                 "Returns: {topologies:[{topology_id, name, device_ids, link_ids}]}"
             ),
             inputSchema={"type": "object", "properties": _P_CTX, "required": ["context_uuid"]}),
        Tool(name="tfs_set_topologies",
             description=(
                 "Create / update Topologies within a Context "
                 "(POST /tfs-api/context/<context_uuid>/topologies). "
                 "Returns: {topology_ids:[TopologyId]}"
             ),
             inputSchema={"type": "object",
                          "properties": {**_P_CTX,
                                         "topologies": {"type": "array", "items": _TOPOLOGY_BODY}},
                          "required": ["context_uuid", "topologies"]}),
        Tool(name="tfs_get_topology",
             description=(
                 "Get a Topology (GET /tfs-api/context/<context_uuid>/topology/<topology_uuid>). "
                 "Returns: Topology {topology_id, name, device_ids:[DeviceId], link_ids:[LinkId]}"
             ),
             inputSchema={"type": "object", "properties": {**_P_CTX, **_P_TOPO},
                          "required": ["context_uuid", "topology_uuid"]}),
        Tool(name="tfs_set_topology",
             description=(
                 "Create / update a specific Topology "
                 "(PUT /tfs-api/context/<context_uuid>/topology/<topology_uuid>). "
                 "Returns: TopologyId {context_id, topology_uuid:{uuid}}"
             ),
             inputSchema={"type": "object",
                          "properties": {**_P_CTX, **_P_TOPO, "topology": _TOPOLOGY_BODY},
                          "required": ["context_uuid", "topology_uuid", "topology"]}),
        Tool(name="tfs_delete_topology",
             description=(
                 "Delete a Topology "
                 "(DELETE /tfs-api/context/<context_uuid>/topology/<topology_uuid>). Returns: {}"
             ),
             inputSchema={"type": "object", "properties": {**_P_CTX, **_P_TOPO},
                          "required": ["context_uuid", "topology_uuid"]}),
        Tool(name="tfs_get_topology_details",
             description=(
                 "Get Topology with fully populated devices and links "
                 "(GET /tfs-api/context/<context_uuid>/topology_details/<topology_uuid>). "
                 "Returns: TopologyDetails {topology_id, name, "
                 "devices:[Device (full)], links:[Link (full)]}"
             ),
             inputSchema={"type": "object", "properties": {**_P_CTX, **_P_TOPO},
                          "required": ["context_uuid", "topology_uuid"]}),
        Tool(name="tfs_list_service_ids",
             description=(
                 "List Service IDs in a Context (GET /tfs-api/context/<context_uuid>/service_ids). "
                 "Returns: {service_ids:[{context_id, service_uuid:{uuid}}]}"
             ),
             inputSchema={"type": "object", "properties": _P_CTX, "required": ["context_uuid"]}),
        Tool(name="tfs_list_services",
             description=(
                 "List Services in a Context (GET /tfs-api/context/<context_uuid>/services). "
                 "Returns: {services:[Service {service_id, name, service_type, "
                 "service_endpoint_ids, service_constraints, service_status, service_config}]}"
             ),
             inputSchema={"type": "object", "properties": _P_CTX, "required": ["context_uuid"]}),
        Tool(name="tfs_create_services",
             description=(
                 "Create one or more Services in a Context "
                 "(POST /tfs-api/context/<context_uuid>/services). "
                 "Returns: {service_ids:[ServiceId]}"
             ),
             inputSchema={"type": "object",
                          "properties": {**_P_CTX,
                                         "services": {"type": "array", "items": _SERVICE_BODY}},
                          "required": ["context_uuid", "services"]}),
        Tool(name="tfs_get_service",
             description=(
                 "Get a Service (GET /tfs-api/context/<context_uuid>/service/<service_uuid>). "
                 "Returns: Service {service_id, name, service_type, service_endpoint_ids, "
                 "service_constraints, service_status, service_config:{config_rules}, timestamp}"
             ),
             inputSchema={"type": "object", "properties": {**_P_CTX, **_P_SVC},
                          "required": ["context_uuid", "service_uuid"]}),
        Tool(name="tfs_update_service",
             description=(
                 "Update a Service (PUT /tfs-api/context/<context_uuid>/service/<service_uuid>). "
                 "Returns: ServiceId {context_id, service_uuid:{uuid}}"
             ),
             inputSchema={"type": "object",
                          "properties": {**_P_CTX, **_P_SVC, "service": _SERVICE_BODY},
                          "required": ["context_uuid", "service_uuid", "service"]}),
        Tool(name="tfs_delete_service",
             description=(
                 "Delete a Service (DELETE /tfs-api/context/<context_uuid>/service/<service_uuid>). "
                 "Returns: {}"
             ),
             inputSchema={"type": "object", "properties": {**_P_CTX, **_P_SVC},
                          "required": ["context_uuid", "service_uuid"]}),
        Tool(name="tfs_list_slice_ids",
             description=(
                 "List Slice IDs in a Context (GET /tfs-api/context/<context_uuid>/slice_ids). "
                 "Returns: {slice_ids:[{context_id, slice_uuid:{uuid}}]}"
             ),
             inputSchema={"type": "object", "properties": _P_CTX, "required": ["context_uuid"]}),
        Tool(name="tfs_list_slices",
             description=(
                 "List Slices in a Context (GET /tfs-api/context/<context_uuid>/slices). "
                 "Returns: {slices:[Slice {slice_id, name, slice_endpoint_ids, "
                 "slice_constraints, slice_service_ids, slice_status, slice_config}]}"
             ),
             inputSchema={"type": "object", "properties": _P_CTX, "required": ["context_uuid"]}),
        Tool(name="tfs_create_slices",
             description=(
                 "Create one or more Slices in a Context "
                 "(POST /tfs-api/context/<context_uuid>/slices). "
                 "Returns: {slice_ids:[SliceId]}"
             ),
             inputSchema={"type": "object",
                          "properties": {**_P_CTX,
                                         "slices": {"type": "array", "items": _SLICE_BODY}},
                          "required": ["context_uuid", "slices"]}),
        Tool(name="tfs_get_slice",
             description=(
                 "Get a Slice (GET /tfs-api/context/<context_uuid>/slice/<slice_uuid>). "
                 "Returns: Slice {slice_id, name, slice_endpoint_ids:[EndPointId], "
                 "slice_constraints, slice_service_ids, slice_subslice_ids, "
                 "slice_status:{slice_status}, slice_config:{config_rules}, slice_owner, timestamp}"
             ),
             inputSchema={"type": "object", "properties": {**_P_CTX, **_P_SLC},
                          "required": ["context_uuid", "slice_uuid"]}),
        Tool(name="tfs_update_slice",
             description=(
                 "Update a Slice (PUT /tfs-api/context/<context_uuid>/slice/<slice_uuid>). "
                 "Returns: SliceId {context_id, slice_uuid:{uuid}}"
             ),
             inputSchema={"type": "object",
                          "properties": {**_P_CTX, **_P_SLC, "slice": _SLICE_BODY},
                          "required": ["context_uuid", "slice_uuid", "slice"]}),
        Tool(name="tfs_delete_slice",
             description=(
                 "Delete a Slice (DELETE /tfs-api/context/<context_uuid>/slice/<slice_uuid>). "
                 "Returns: {}"
             ),
             inputSchema={"type": "object", "properties": {**_P_CTX, **_P_SLC},
                          "required": ["context_uuid", "slice_uuid"]}),
        Tool(name="tfs_list_device_ids",
             description=(
                 "List all Device IDs (GET /tfs-api/device_ids). "
                 "Returns: {device_ids:[{device_uuid:{uuid}}]}"
             ),
             inputSchema={"type": "object", "properties": {}, "required": []}),
        Tool(name="tfs_list_devices",
             description=(
                 "List all Devices with full details (GET /tfs-api/devices). "
                 "Returns: {devices:[Device {device_id, name, device_type, device_config, "
                 "device_operational_status, device_drivers, device_endpoints:[EndPoint]}]}"
             ),
             inputSchema={"type": "object", "properties": {}, "required": []}),
        Tool(name="tfs_add_devices",
             description=(
                 "Add one or more Devices (POST /tfs-api/devices). "
                 "Returns: {device_ids:[DeviceId]}"
             ),
             inputSchema={"type": "object",
                          "properties": {"devices": {"type": "array", "items": _DEVICE_BODY}},
                          "required": ["devices"]}),
        Tool(name="tfs_get_device",
             description=(
                 "Get a Device by UUID (GET /tfs-api/device/<device_uuid>). "
                 "Returns: Device {device_id, name, device_type, "
                 "device_config:{config_rules:[ConfigRule]}, device_operational_status, "
                 "device_drivers:[int], device_endpoints:[EndPoint]}"
             ),
             inputSchema={"type": "object", "properties": _P_DEV, "required": ["device_uuid"]}),
        Tool(name="tfs_configure_device",
             description=(
                 "Configure / update a Device (PUT /tfs-api/device/<device_uuid>). "
                 "Typically used to push config_rules onto an existing device. "
                 "Returns: DeviceId {device_uuid:{uuid}}"
             ),
             inputSchema={"type": "object",
                          "properties": {**_P_DEV, "device": _DEVICE_BODY},
                          "required": ["device_uuid", "device"]}),
        Tool(name="tfs_delete_device",
             description="Delete a Device (DELETE /tfs-api/device/<device_uuid>). Returns: {}",
             inputSchema={"type": "object", "properties": _P_DEV, "required": ["device_uuid"]}),
        Tool(name="tfs_list_link_ids",
             description=(
                 "List all Link IDs (GET /tfs-api/link_ids). "
                 "Returns: {link_ids:[{link_uuid:{uuid}}]}"
             ),
             inputSchema={"type": "object", "properties": {}, "required": []}),
        Tool(name="tfs_list_links",
             description=(
                 "List all Links (GET /tfs-api/links). "
                 "Returns: {links:[Link {link_id, name, link_type, "
                 "link_endpoint_ids:[EndPointId], attributes:{is_bidirectional, "
                 "total_capacity_gbps, used_capacity_gbps}}]}"
             ),
             inputSchema={"type": "object", "properties": {}, "required": []}),
        Tool(name="tfs_set_links",
             description=(
                 "Create / update one or more Links (POST /tfs-api/links). "
                 "Virtual links (link_type=4) are routed via the VNT manager automatically. "
                 "Returns: {link_ids:[LinkId]}"
             ),
             inputSchema={"type": "object",
                          "properties": {"links": {"type": "array", "items": _LINK_BODY}},
                          "required": ["links"]}),
        Tool(name="tfs_get_link",
             description=(
                 "Get a Link by UUID (GET /tfs-api/link/<link_uuid>). "
                 "Returns: Link {link_id, name, link_type, link_endpoint_ids:[EndPointId], "
                 "attributes:{is_bidirectional, total_capacity_gbps, used_capacity_gbps}}"
             ),
             inputSchema={"type": "object", "properties": _P_LNK, "required": ["link_uuid"]}),
        Tool(name="tfs_set_link",
             description=(
                 "Create / update a specific Link (PUT /tfs-api/link/<link_uuid>). "
                 "Returns: LinkId {link_uuid:{uuid}}"
             ),
             inputSchema={"type": "object",
                          "properties": {**_P_LNK, "link": _LINK_BODY},
                          "required": ["link_uuid", "link"]}),
        Tool(name="tfs_delete_link",
             description="Delete a Link (DELETE /tfs-api/link/<link_uuid>). Returns: {}",
             inputSchema={"type": "object", "properties": _P_LNK, "required": ["link_uuid"]}),
        Tool(name="tfs_list_connection_ids",
             description=(
                 "List Connection IDs of a Service "
                 "(GET /tfs-api/context/<context_uuid>/service/<service_uuid>/connection_ids). "
                 "Returns: {connection_ids:[{connection_uuid:{uuid}}]}"
             ),
             inputSchema={"type": "object", "properties": {**_P_CTX, **_P_SVC},
                          "required": ["context_uuid", "service_uuid"]}),
        Tool(name="tfs_list_connections",
             description=(
                 "List Connections of a Service "
                 "(GET /tfs-api/context/<context_uuid>/service/<service_uuid>/connections). "
                 "Returns: {connections:[Connection {connection_id, service_id, "
                 "path_hops_endpoint_ids:[EndPointId], sub_service_ids, "
                 "settings:{l0:{lsp_symbolic_name}, l2:{src_mac,dst_mac,ether_type,vlan_id}, "
                 "l3:{src_ip,dst_ip,dscp,protocol,ttl}, l4:{src_port,dst_port,tcp_flags}}}]}"
             ),
             inputSchema={"type": "object", "properties": {**_P_CTX, **_P_SVC},
                          "required": ["context_uuid", "service_uuid"]}),
        Tool(name="tfs_get_connection",
             description=(
                 "Get a Connection by UUID (GET /tfs-api/connection/<connection_uuid>). "
                 "Returns: Connection {connection_id, service_id:{context_id,service_uuid}, "
                 "path_hops_endpoint_ids:[EndPointId], sub_service_ids:[ServiceId], "
                 "settings:{l0,l2,l3,l4}}"
             ),
             inputSchema={"type": "object", "properties": _P_CON, "required": ["connection_uuid"]}),
        Tool(name="tfs_list_policyrule_ids",
             description=(
                 "List all Policy Rule IDs (GET /tfs-api/policyrule_ids). "
                 "Returns: {policyrule_ids:[{policyrule_uuid:{uuid}}]}"
             ),
             inputSchema={"type": "object", "properties": {}, "required": []}),
        Tool(name="tfs_list_policyrules",
             description=(
                 "List all Policy Rules (GET /tfs-api/policyrules). "
                 "Returns: {policyrules:[PolicyRule]}"
             ),
             inputSchema={"type": "object", "properties": {}, "required": []}),
        Tool(name="tfs_get_policyrule",
             description=(
                 "Get a Policy Rule by UUID (GET /tfs-api/policyrule/<policyrule_uuid>). "
                 "Returns: PolicyRule {policyrule_id, name, ...}"
             ),
             inputSchema={"type": "object", "properties": _P_POL, "required": ["policyrule_uuid"]}),
        Tool(name="tfs_health_check",
             description=(
                 "Probe the TFS NBI service for liveness (GET /healthz). "
                 "Returns: HTTP 200 with {} when healthy."
             ),
             inputSchema={"type": "object", "properties": {}, "required": []}),
        Tool(name="tfs_optical_slice_create",
             description=(
                 "Create / request an end-to-end OPTICAL SLICE "
                 "(POST /restconf/optical-slice/v1/service/<slice_id>). "
                 "This is the RESTCONF optical-slice API and is distinct from "
                 "TFS OPTICAL_CONNECTIVITY service provisioning through /tfs-api. "
                 "Use this only for optical slice intents, not for optical "
                 "connectivity service intents. "
                 "The controller resolves the slice into one or more spectrum "
                 "allocations (media channels) along the optical path. "
                 "Input MUST be a full optical-slice payload (not just src/dst names). "
                 "If `tapi-common:context.uuid` is missing, server auto-generates it."
             ),
             inputSchema={
                 "type": "object",
                 "properties": {
                     "slice_id": {"type": "string", "description": "Identifier for the optical slice"},
                     "slice": {"type": "object",
                               "description": "Optical-slice request body (TFS optical-slice schema). Include `tapi-common:context` with service-interface-points/constraints as required by controller; uuid may be omitted and will be generated."}
                 },
                 "required": ["slice_id", "slice"]
             }),
        Tool(name="tfs_optical_slice_delete",
             description=(
                 "Delete / release an OPTICAL SLICE and its associated spectrum "
                 "allocations (DELETE /restconf/optical-slice/v1/service/<slice_id>)."
             ),
             inputSchema={
                 "type": "object",
                 "properties": {
                     "slice_id": {"type": "string", "description": "Identifier of the optical slice to delete"}
                 },
                 "required": ["slice_id"]
             }),
        Tool(name="tfs_media_channel_allocate",
             description=(
                 "Allocate an OPTICAL SPECTRUM SLOT (media channel) on a fiber/ROADM path "
                 "(POST /restconf/media-channel/v1/service/<allocation_id>). "
                 "A media channel = a contiguous spectrum allocation defined by "
                 "center frequency and slot width (e.g., flexgrid n / m). "
                 "This is the primary endpoint used to commit a spectrum negotiation result."
             ),
             inputSchema={
                 "type": "object",
                 "properties": {
                     "allocation_id": {"type": "string",
                                       "description": "Identifier for the media-channel allocation"},
                     "media_channel": {
                         "type": "object",
                         "description": (
                             "Media-channel request body. Typical fields include source/destination "
                             "endpoints, center frequency (THz / 0.0125 THz units), slot width, "
                             "lower/upper frequency bounds, optical-channel name, and target power."
                         )
                     }
                 },
                 "required": ["allocation_id", "media_channel"]
             }),
        Tool(name="tfs_media_channel_release",
             description=(
                 "Release / tear down an optical spectrum allocation "
                 "(DELETE /restconf/media-channel/v1/service/<allocation_id>)."
             ),
             inputSchema={
                 "type": "object",
                 "properties": {
                     "allocation_id": {"type": "string",
                                       "description": "Identifier of the media channel to release"}
                 },
                 "required": ["allocation_id"]
             }),
        Tool(name="tfs_dscm_oc_get",
             description=(
                 "RESTCONF GET on a per-device OpenConfig YANG path used for optical / "
                 "DSCM (Dynamic Spectrum & Capacity Management) configuration "
                 "(GET /restconf/data/device=<device_uuid>/<rc_path>). "
                 "Use this to read optical-channel state - e.g. frequency, "
                 "operational-mode, target-output-power - from a transponder or ROADM."
             ),
             inputSchema={
                 "type": "object",
                 "properties": {
                     "device_uuid": {"type": "string", "description": "Target device UUID"},
                     "rc_path": {
                         "type": "string",
                         "description": (
                             "OpenConfig RESTCONF path under the device, e.g. "
                             "'openconfig-platform:components/component=och-1/optical-channel/config'."
                         )
                     }
                 },
                 "required": ["device_uuid", "rc_path"]
             }),
        Tool(name="tfs_dscm_oc_post",
             description=(
                 "RESTCONF POST to push an OpenConfig optical-channel / spectrum "
                 "configuration onto a device "
                 "(POST /restconf/data/device=<device_uuid>/<rc_path>). "
                 "Use this to configure center frequency, operational-mode, target output "
                 "power and other DSCM-OC parameters on a transponder or ROADM."
             ),
             inputSchema={
                 "type": "object",
                 "properties": {
                     "device_uuid": {"type": "string", "description": "Target device UUID"},
                     "rc_path": {"type": "string",
                                 "description": "OpenConfig RESTCONF path under the device"},
                     "config": {
                         "type": "object",
                         "description": (
                             "YANG-data+json payload to push (OpenConfig optical-channel config, "
                             "e.g. {'openconfig-terminal-device:optical-channel': {'config': "
                             "{'frequency': 193100000, 'target-output-power': -1.0, "
                             "'operational-mode': 1}}})."
                         )
                     }
                 },
                 "required": ["device_uuid", "rc_path", "config"]
             }),
        Tool(name="tfs_dscm_oc_delete",
             description=(
                 "RESTCONF DELETE on a per-device OpenConfig path "
                 "(DELETE /restconf/data/device=<device_uuid>/<rc_path>). "
                 "Use this to remove an optical-channel / spectrum configuration "
                 "from a transponder or ROADM."
             ),
             inputSchema={
                 "type": "object",
                 "properties": {
                     "device_uuid": {"type": "string", "description": "Target device UUID"},
                     "rc_path": {"type": "string",
                                 "description": "OpenConfig RESTCONF path under the device"}
                 },
                 "required": ["device_uuid", "rc_path"]
             }),
    ]
