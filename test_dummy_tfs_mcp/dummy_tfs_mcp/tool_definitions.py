from typing import Any, List

from .dependencies import Tool


_P_CTX = {"context_uuid": {"type": "string", "description": "Context UUID, for example 'admin'"}}
_P_TOPO = {"topology_uuid": {"type": "string", "description": "Topology UUID, for example 'admin'"}}
_P_DEV = {"device_uuid": {"type": "string", "description": "Device UUID"}}
_P_LNK = {"link_uuid": {"type": "string", "description": "Link UUID"}}


if Tool is None:
    TOOLS: List[Any] = []
else:
    TOOLS = [
        Tool(
            name="tfs_health_check",
            description="Dummy server health check.",
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        Tool(
            name="tfs_list_context_ids",
            description="List all dummy context IDs.",
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        Tool(
            name="tfs_list_contexts",
            description="List all dummy contexts with attached topology IDs.",
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        Tool(
            name="tfs_get_dummy_contexts",
            description="Return one aggregated dummy snapshot with contexts, topologies, devices, and links.",
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        Tool(
            name="tfs_get_context",
            description="Get one dummy context by UUID.",
            inputSchema={"type": "object", "properties": _P_CTX, "required": ["context_uuid"]},
        ),
        Tool(
            name="tfs_list_topology_ids",
            description="List topology IDs within a dummy context.",
            inputSchema={"type": "object", "properties": _P_CTX, "required": ["context_uuid"]},
        ),
        Tool(
            name="tfs_list_topologies",
            description="List topologies within a dummy context.",
            inputSchema={"type": "object", "properties": _P_CTX, "required": ["context_uuid"]},
        ),
        Tool(
            name="tfs_get_topology",
            description="Get a dummy topology by context and topology UUID.",
            inputSchema={
                "type": "object",
                "properties": {**_P_CTX, **_P_TOPO},
                "required": ["context_uuid", "topology_uuid"],
            },
        ),
        Tool(
            name="tfs_get_topology_details",
            description="Get a dummy topology with fully populated devices and links.",
            inputSchema={
                "type": "object",
                "properties": {**_P_CTX, **_P_TOPO},
                "required": ["context_uuid", "topology_uuid"],
            },
        ),
        Tool(
            name="tfs_list_device_ids",
            description="List all dummy device IDs.",
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        Tool(
            name="tfs_list_devices",
            description="List all dummy devices.",
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        Tool(
            name="tfs_get_device",
            description="Get one dummy device by UUID.",
            inputSchema={"type": "object", "properties": _P_DEV, "required": ["device_uuid"]},
        ),
        Tool(
            name="tfs_list_link_ids",
            description="List all dummy link IDs.",
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        Tool(
            name="tfs_list_links",
            description="List all dummy links.",
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        Tool(
            name="tfs_get_link",
            description="Get one dummy link by UUID.",
            inputSchema={"type": "object", "properties": _P_LNK, "required": ["link_uuid"]},
        ),
    ]
