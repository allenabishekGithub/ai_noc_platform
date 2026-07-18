# File: tfs_workflow_tools.py
"""
High-level TFS workflow tools designed for small LLMs.

Instead of exposing raw TFS API endpoints that require deeply nested JSON
construction, these tools accept simple human-readable arguments and handle
UUID discovery, JSON building, and multi-step service lifecycle internally.

The LLM only needs to pass device names and endpoint names — not nested dicts.
"""
import hashlib
import json
import logging
import os
import time
import uuid
from contextvars import ContextVar, Token
from copy import deepcopy
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.tools import StructuredTool

LOGGER = logging.getLogger(__name__)

_TURN_SESSION_ID: ContextVar[str] = ContextVar("workflow_turn_session_id", default="default")
_TURN_DECLARED_INTENT: ContextVar[str] = ContextVar("workflow_declared_intent", default="")
_TURN_DECLARED_TOOL: ContextVar[str] = ContextVar("workflow_declared_tool", default="")
_TURN_PROVISION_CALLED: ContextVar[bool] = ContextVar("workflow_provision_called", default=False)
_TURN_CALLER_TOOL: ContextVar[str] = ContextVar("workflow_caller_tool", default="")
_TURN_A2A_PEER_PACKET_PROVISIONED: ContextVar[bool] = ContextVar(
    "workflow_a2a_peer_packet_provisioned",
    default=False,
)

_SERVICE_TYPE_MAP = {
    "l3vpn":                    "SERVICETYPE_L3NM",
    "l3nm":                     "SERVICETYPE_L3NM",
    "l3":                       "SERVICETYPE_L3NM",
    "l2vpn":                    "SERVICETYPE_L2NM",
    "l2nm":                     "SERVICETYPE_L2NM",
    "l2":                       "SERVICETYPE_L2NM",
    "optical":                  "SERVICETYPE_OPTICAL_CONNECTIVITY",
    "optical_connectivity":     "SERVICETYPE_OPTICAL_CONNECTIVITY",
    "e2e":                      "SERVICETYPE_E2E",
}

_CAPABILITY_CATALOG: Dict[str, Dict[str, Any]] = {
    "health": {
        "proto": "health/NBI liveness",
        "read": {"check": {"tool": "tfs_health_check", "endpoint": "GET /healthz"}},
    },
    "context": {
        "proto": "ContextService: ListContextIds, ListContexts, GetContext, SetContext, RemoveContext",
        "read": {
            "ids": {"tool": "tfs_list_context_ids", "endpoint": "GET /tfs-api/context_ids"},
            "list": {"tool": "tfs_list_contexts", "endpoint": "GET /tfs-api/contexts"},
            "dummy": {"tool": "tfs_get_dummy_contexts", "endpoint": "GET /tfs-api/dummy_contexts"},
            "get": {"tool": "tfs_get_context", "endpoint": "GET /tfs-api/context/<context_uuid>"},
        },
        "write": {
            "set_many": {"tool": "tfs_set_contexts", "endpoint": "POST /tfs-api/contexts", "body": "contexts"},
            "set": {"tool": "tfs_set_context", "endpoint": "PUT /tfs-api/context/<context_uuid>", "body": "context"},
            "delete": {"tool": "tfs_delete_context", "endpoint": "DELETE /tfs-api/context/<context_uuid>"},
        },
    },
    "topology": {
        "proto": "ContextService: ListTopologyIds, ListTopologies, GetTopology, GetTopologyDetails, SetTopology, RemoveTopology",
        "read": {
            "ids": {"tool": "tfs_list_topology_ids", "endpoint": "GET /tfs-api/context/<context_uuid>/topology_ids"},
            "list": {"tool": "tfs_list_topologies", "endpoint": "GET /tfs-api/context/<context_uuid>/topologies"},
            "get": {"tool": "tfs_get_topology", "endpoint": "GET /tfs-api/context/<context_uuid>/topology/<topology_uuid>"},
            "details": {"tool": "tfs_get_topology_details", "endpoint": "GET /tfs-api/context/<context_uuid>/topology_details/<topology_uuid>"},
        },
        "write": {
            "set_many": {"tool": "tfs_set_topologies", "endpoint": "POST /tfs-api/context/<context_uuid>/topologies", "body": "topologies"},
            "set": {"tool": "tfs_set_topology", "endpoint": "PUT /tfs-api/context/<context_uuid>/topology/<topology_uuid>", "body": "topology"},
            "delete": {"tool": "tfs_delete_topology", "endpoint": "DELETE /tfs-api/context/<context_uuid>/topology/<topology_uuid>"},
        },
    },
    "service": {
        "proto": "ContextService: ListServiceIds, ListServices, GetService, SetService, UnsetService, RemoveService",
        "read": {
            "ids": {"tool": "tfs_list_service_ids", "endpoint": "GET /tfs-api/context/<context_uuid>/service_ids"},
            "list": {"tool": "tfs_list_services", "endpoint": "GET /tfs-api/context/<context_uuid>/services"},
            "get": {"tool": "tfs_get_service", "endpoint": "GET /tfs-api/context/<context_uuid>/service/<service_uuid>"},
        },
        "write": {
            "create_many": {"tool": "tfs_create_services", "endpoint": "POST /tfs-api/context/<context_uuid>/services", "body": "services"},
            "update": {"tool": "tfs_update_service", "endpoint": "PUT /tfs-api/context/<context_uuid>/service/<service_uuid>", "body": "service"},
            "delete": {"tool": "tfs_delete_service", "endpoint": "DELETE /tfs-api/context/<context_uuid>/service/<service_uuid>"},
        },
    },
    "slice": {
        "proto": "ContextService: ListSliceIds, ListSlices, GetSlice, SetSlice, UnsetSlice, RemoveSlice",
        "read": {
            "ids": {"tool": "tfs_list_slice_ids", "endpoint": "GET /tfs-api/context/<context_uuid>/slice_ids"},
            "list": {"tool": "tfs_list_slices", "endpoint": "GET /tfs-api/context/<context_uuid>/slices"},
            "get": {"tool": "tfs_get_slice", "endpoint": "GET /tfs-api/context/<context_uuid>/slice/<slice_uuid>"},
        },
        "write": {
            "create_many": {"tool": "tfs_create_slices", "endpoint": "POST /tfs-api/context/<context_uuid>/slices", "body": "slices"},
            "update": {"tool": "tfs_update_slice", "endpoint": "PUT /tfs-api/context/<context_uuid>/slice/<slice_uuid>", "body": "slice"},
            "delete": {"tool": "tfs_delete_slice", "endpoint": "DELETE /tfs-api/context/<context_uuid>/slice/<slice_uuid>"},
        },
    },
    "device": {
        "proto": "ContextService: ListDeviceIds, ListDevices, GetDevice, SetDevice, RemoveDevice",
        "read": {
            "ids": {"tool": "tfs_list_device_ids", "endpoint": "GET /tfs-api/device_ids"},
            "list": {"tool": "tfs_list_devices", "endpoint": "GET /tfs-api/devices"},
            "get": {"tool": "tfs_get_device", "endpoint": "GET /tfs-api/device/<device_uuid>"},
        },
        "write": {
            "add_many": {"tool": "tfs_add_devices", "endpoint": "POST /tfs-api/devices", "body": "devices"},
            "configure": {"tool": "tfs_configure_device", "endpoint": "PUT /tfs-api/device/<device_uuid>", "body": "device"},
            "delete": {"tool": "tfs_delete_device", "endpoint": "DELETE /tfs-api/device/<device_uuid>"},
        },
    },
    "link": {
        "proto": "ContextService: ListLinkIds, ListLinks, GetLink, SetLink, RemoveLink",
        "read": {
            "ids": {"tool": "tfs_list_link_ids", "endpoint": "GET /tfs-api/link_ids"},
            "list": {"tool": "tfs_list_links", "endpoint": "GET /tfs-api/links"},
            "get": {"tool": "tfs_get_link", "endpoint": "GET /tfs-api/link/<link_uuid>"},
        },
        "write": {
            "set_many": {"tool": "tfs_set_links", "endpoint": "POST /tfs-api/links", "body": "links"},
            "set": {"tool": "tfs_set_link", "endpoint": "PUT /tfs-api/link/<link_uuid>", "body": "link"},
            "delete": {"tool": "tfs_delete_link", "endpoint": "DELETE /tfs-api/link/<link_uuid>"},
        },
    },
    "connection": {
        "proto": "ContextService: ListConnectionIds, ListConnections, GetConnection, SetConnection, RemoveConnection",
        "read": {
            "ids": {"tool": "tfs_list_connection_ids", "endpoint": "GET /tfs-api/context/<context_uuid>/service/<service_uuid>/connection_ids"},
            "list": {"tool": "tfs_list_connections", "endpoint": "GET /tfs-api/context/<context_uuid>/service/<service_uuid>/connections"},
            "get": {"tool": "tfs_get_connection", "endpoint": "GET /tfs-api/connection/<connection_uuid>"},
        },
        "write": {},
        "note": "SetConnection and RemoveConnection exist in context.proto but are not exposed by the current TFS MCP server.",
    },
    "policyrule": {
        "proto": "Context policy resources exposed by NBI/MCP",
        "read": {
            "ids": {"tool": "tfs_list_policyrule_ids", "endpoint": "GET /tfs-api/policyrule_ids"},
            "list": {"tool": "tfs_list_policyrules", "endpoint": "GET /tfs-api/policyrules"},
            "get": {"tool": "tfs_get_policyrule", "endpoint": "GET /tfs-api/policyrule/<policyrule_uuid>"},
        },
        "write": {},
    },
    "optical_slice": {
        "proto": "NBI RESTCONF optical-slice endpoint; related experimental proto optical calls are not exposed one-for-one",
        "write": {
            "create": {"tool": "tfs_optical_slice_create", "endpoint": "POST /restconf/optical-slice/v1/service/<slice_id>", "body": "slice"},
            "delete": {"tool": "tfs_optical_slice_delete", "endpoint": "DELETE /restconf/optical-slice/v1/service/<slice_id>"},
        },
    },
    "media_channel": {
        "proto": "NBI RESTCONF media-channel endpoint",
        "write": {
            "allocate": {"tool": "tfs_media_channel_allocate", "endpoint": "POST /restconf/media-channel/v1/service/<allocation_id>", "body": "media_channel"},
            "release": {"tool": "tfs_media_channel_release", "endpoint": "DELETE /restconf/media-channel/v1/service/<allocation_id>"},
        },
    },
    "dscm_oc": {
        "proto": "NBI RESTCONF per-device OpenConfig path",
        "read": {
            "get": {"tool": "tfs_dscm_oc_get", "endpoint": "GET /restconf/data/device=<device_uuid>/<rc_path>"},
        },
        "write": {
            "post": {"tool": "tfs_dscm_oc_post", "endpoint": "POST /restconf/data/device=<device_uuid>/<rc_path>", "body": "config"},
            "delete": {"tool": "tfs_dscm_oc_delete", "endpoint": "DELETE /restconf/data/device=<device_uuid>/<rc_path>"},
        },
    },
}

_PROTO_NOT_EXPOSED_BY_MCP = [
    "GetContextEvents/GetTopologyEvents/GetDeviceEvents/GetLinkEvents/GetServiceEvents/GetSliceEvents/GetConnectionEvents/GetAllEvents streams",
    "SelectDevice/SelectService/SelectSlice filter RPCs",
    "UnsetService and UnsetSlice as distinct operations",
    "SetConnection and RemoveConnection write operations",
    "DeleteServiceConfigRule",
    "Experimental proto optical RPCs: GetOpticalConfig, SetOpticalConfig, UpdateOpticalConfig, SelectOpticalConfig, DeleteOpticalConfig, DeleteOpticalChannel, SetOpticalLink, GetOpticalLink, DeleteOpticalLink, GetOpticalLinkList, GetOpticalBand, SelectOpticalBand",
]


def _structured(result: Dict[str, Any]) -> Any:
    """Extract structured payload from an mcp_client.call_tool() result."""
    s = result.get("structured")
    if s is not None:
        return s
    try:
        return json.loads(result.get("text", ""))
    except Exception:
        return {}


def _is_error(data: Any) -> bool:
    return isinstance(data, dict) and (
        data.get("status") == "error"
        or "error" in data
        or data.get("isError") is True
    )


class TFSWorkflowTools:
    """High-level TFS workflow tools that hide API complexity from the LLM."""

    def __init__(self, mcp_client):
        self.mcp = mcp_client
        # Cache discovered context/topology UUIDs for 60 s — they are static
        # in almost all deployments and don't need a round-trip per operation.
        self._ctx_cache: Tuple[Optional[str], float] = (None, 0.0)
        self._topo_cache: Dict[str, Tuple[str, float]] = {}
        self._provision_called_sessions: set[str] = set()
        self._a2a_peer_packet_provisioned_sessions: set[str] = set()
        self._cross_domain_packet_plans: Dict[str, Dict[str, Any]] = {}

    def begin_turn(self, session_id: str) -> Dict[str, Token]:
        """Initialize per-turn workflow context."""
        sid = session_id or "default"
        self._provision_called_sessions.discard(sid)
        self._a2a_peer_packet_provisioned_sessions.discard(sid)
        self._cross_domain_packet_plans.pop(sid, None)
        return {
            "session_id": _TURN_SESSION_ID.set(sid),
            "intent": _TURN_DECLARED_INTENT.set(""),
            "selected_tool": _TURN_DECLARED_TOOL.set(""),
            "provision_called": _TURN_PROVISION_CALLED.set(False),
            "caller_tool": _TURN_CALLER_TOOL.set(""),
            "a2a_peer_packet_provisioned": _TURN_A2A_PEER_PACKET_PROVISIONED.set(False),
        }

    def end_turn(self, tokens: Dict[str, Token]) -> None:
        """Reset per-turn workflow context."""
        for var, key in (
            (_TURN_A2A_PEER_PACKET_PROVISIONED, "a2a_peer_packet_provisioned"),
            (_TURN_CALLER_TOOL, "caller_tool"),
            (_TURN_PROVISION_CALLED, "provision_called"),
            (_TURN_DECLARED_TOOL, "selected_tool"),
            (_TURN_DECLARED_INTENT, "intent"),
            (_TURN_SESSION_ID, "session_id"),
        ):
            token = tokens.get(key)
            if token is not None:
                var.reset(token)

    # Backward-compatible alias used by existing caller paths.
    def reset_turn_guards(self, session_id: str) -> None:
        self._provision_called_sessions.discard(session_id or "default")
        self._a2a_peer_packet_provisioned_sessions.discard(session_id or "default")
        self._cross_domain_packet_plans.pop(session_id or "default", None)
        _TURN_SESSION_ID.set(session_id or "default")
        _TURN_DECLARED_INTENT.set("")
        _TURN_DECLARED_TOOL.set("")
        _TURN_PROVISION_CALLED.set(False)
        _TURN_CALLER_TOOL.set("")
        _TURN_A2A_PEER_PACKET_PROVISIONED.set(False)

    @staticmethod
    def _tool_family(tool_name: str) -> str:
        name = (tool_name or "").strip()
        if name in {
            "tfs_create_optical_slice",
            "tfs_restconf_create_optical_slice",
            "tfs_create_optical_slice_from_endpoints",
            "tfs_restconf_create_optical_slice_from_endpoints",
            "tfs_delete_optical_slice",
        }:
            return "optical_slice_restconf"
        if name in {
            "tfs_provision_optical_connectivity_service",
            "tfs_service_create_optical_connectivity",
        }:
            return "optical_connectivity_service"
        if name in {"tfs_provision_service"}:
            return "packet_service"
        if name in {"tfs_delete_service"}:
            return "delete"
        if name in {
            "tfs_service_delete_optical_connectivity",
            "tfs_delete_optical_connectivity_service",
        }:
            return "delete"
        if name in {"tfs_write"}:
            return "generic_write"
        return name

    @staticmethod
    def _default_intent_for_tool(tool_name: str) -> Optional[str]:
        family = TFSWorkflowTools._tool_family(tool_name)
        allowed = {
            "optical_slice_restconf",
            "optical_connectivity_service",
            "packet_service",
            "delete",
            "generic_write",
        }
        if family in allowed:
            return family
        return None

    def declare_intent(self, intent: str, selected_tool: str, reason: str = "") -> str:
        """Persist model-declared intent for mutation guard validation."""
        allowed = {
            "optical_slice_restconf",
            "optical_connectivity_service",
            "packet_service",
            "delete",
            "generic_write",
        }
        normalized_intent = (intent or "").strip().lower()
        normalized_tool = (selected_tool or "").strip()
        if normalized_intent not in allowed:
            return json.dumps({
                "ok": False,
                "phase": "intent_guard",
                "operation": "declare_tfs_intent",
                "service_uuid": None,
                "observed_status": None,
                "retryable": True,
                "error": "unsupported_intent",
                "message": "Unsupported intent declaration.",
                "details": {
                    "allowed_intents": sorted(allowed),
                    "received_intent": intent,
                },
            }, indent=2)
        _TURN_DECLARED_INTENT.set(normalized_intent)
        _TURN_DECLARED_TOOL.set(normalized_tool)
        return json.dumps({
            "ok": True,
            "phase": "intent_declared",
            "operation": "declare_tfs_intent",
            "service_uuid": None,
            "observed_status": None,
            "retryable": False,
            "error": None,
            "message": "Intent declaration recorded.",
            "details": {
                "intent": normalized_intent,
                "selected_tool": normalized_tool,
                "reason": reason,
            },
        }, indent=2)

    @staticmethod
    def _mutation_result(
        *,
        ok: bool,
        phase: str,
        operation: str,
        message: str,
        retryable: bool,
        error: Optional[str] = None,
        service_uuid: Optional[str] = None,
        observed_status: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> str:
        return json.dumps({
            "ok": ok,
            "phase": phase,
            "operation": operation,
            "service_uuid": service_uuid,
            "observed_status": observed_status,
            "retryable": retryable,
            "error": error,
            "message": message,
            "details": details or {},
        }, indent=2)

    @staticmethod
    def _with_caller_tool(tool_name: str) -> Token:
        return _TURN_CALLER_TOOL.set(tool_name)

    @staticmethod
    def _reset_caller_tool(token: Token) -> None:
        _TURN_CALLER_TOOL.reset(token)

    def mark_a2a_peer_packet_provisioned(self, session_id: Optional[str] = None) -> None:
        """Record that the remote packet segment succeeded in this agent turn."""
        sid = session_id or _TURN_SESSION_ID.get() or "default"
        self._a2a_peer_packet_provisioned_sessions.add(sid)
        _TURN_A2A_PEER_PACKET_PROVISIONED.set(True)

    def _a2a_peer_packet_provisioned_this_turn(self) -> bool:
        sid = _TURN_SESSION_ID.get() or "default"
        return _TURN_A2A_PEER_PACKET_PROVISIONED.get() or sid in self._a2a_peer_packet_provisioned_sessions

    def _require_mutation_intent(
        self,
        *,
        actual_tool: str,
        allowed_intents: set[str],
        operation: str,
    ) -> Optional[str]:
        declared_intent = (_TURN_DECLARED_INTENT.get() or "").strip().lower()
        if not declared_intent:
            inferred = self._default_intent_for_tool(actual_tool)
            if inferred in allowed_intents:
                _TURN_DECLARED_INTENT.set(inferred or "")
                _TURN_DECLARED_TOOL.set(actual_tool)
                LOGGER.info(
                    "Auto-inferred mutation intent '%s' for operation '%s' (tool=%s)",
                    inferred,
                    operation,
                    actual_tool,
                )
                declared_intent = inferred or ""
            elif len(allowed_intents) == 1:
                # Deterministic fallback for strict operations (for example delete-only paths).
                only_intent = next(iter(allowed_intents))
                _TURN_DECLARED_INTENT.set(only_intent)
                _TURN_DECLARED_TOOL.set(actual_tool)
                LOGGER.info(
                    "Auto-filled required intent '%s' for operation '%s' (tool=%s)",
                    only_intent,
                    operation,
                    actual_tool,
                )
                declared_intent = only_intent
            else:
                return self._mutation_result(
                    ok=False,
                    phase="intent_guard",
                    operation=operation,
                    message=(
                        "Missing intent declaration. Call declare_tfs_intent before controller mutations."
                    ),
                    retryable=True,
                    error="missing_intent_declaration",
                    details={
                        "required_intents": sorted(allowed_intents),
                        "required_tool": actual_tool,
                    },
                )

        if declared_intent not in allowed_intents:
            return self._mutation_result(
                ok=False,
                phase="intent_guard",
                operation=operation,
                message="Declared intent does not match this mutation operation.",
                retryable=True,
                error="intent_mismatch",
                details={
                    "declared_intent": declared_intent,
                    "allowed_intents": sorted(allowed_intents),
                    "required_tool": actual_tool,
                },
            )

        declared_tool = (_TURN_DECLARED_TOOL.get() or "").strip()
        if declared_tool:
            declared_family = self._tool_family(declared_tool)
            actual_family = self._tool_family(actual_tool)
            if declared_family != actual_family and declared_tool != actual_tool:
                return self._mutation_result(
                    ok=False,
                    phase="intent_guard",
                    operation=operation,
                    message="Declared tool does not match the selected mutation tool.",
                    retryable=True,
                    error="declared_tool_mismatch",
                    details={
                        "declared_tool": declared_tool,
                        "actual_tool": actual_tool,
                        "declared_intent": declared_intent,
                    },
                )
        return None

    # ------------------------------------------------------------------ #
    # Internal helpers                                                     #
    # ------------------------------------------------------------------ #

    def _call(self, tool: str, args: Dict[str, Any]) -> Dict[str, Any]:
        try:
            return self.mcp.call_tool(tool, args)
        except KeyError:
            msg = f"MCP tool {tool!r} not available (server may be offline)."
            return {"text": msg, "structured": {"error": msg, "status": "error"}}
        except Exception as exc:
            return {"text": str(exc), "structured": {"error": str(exc), "status": "error"}}

    @staticmethod
    def _ensure_optical_slice_context_uuid(
        payload: Any,
    ) -> Tuple[Any, Optional[str]]:
        """Ensure optical-slice payload has tapi-common:context.uuid.

        If payload contains `tapi-common:context` but omits/empties `uuid`,
        generate a random UUID and inject it.
        """
        if not isinstance(payload, dict):
            return payload, None

        normalized = deepcopy(payload)
        container = normalized.get("data")
        if not isinstance(container, dict):
            container = normalized

        context = container.get("tapi-common:context")
        if not isinstance(context, dict):
            return normalized, None

        context_uuid = context.get("uuid")
        if isinstance(context_uuid, str) and context_uuid.strip():
            return normalized, None

        generated = str(uuid.uuid4())
        context["uuid"] = generated
        return normalized, generated

    def _discover_context(self, context_uuid: Optional[str] = None) -> str:
        cached_uuid, expiry = self._ctx_cache
        if cached_uuid is None or time.monotonic() >= expiry:
            result = self._call("tfs_list_contexts", {})
            data = _structured(result)
            contexts = data.get("contexts", []) if isinstance(data, dict) else []
            first_uuid = "admin"
            valid_uuids: set = set()
            for c in contexts:
                u = (c.get("context_id", {})
                      .get("context_uuid", {})
                      .get("uuid", ""))
                if u:
                    valid_uuids.add(u)
                    if first_uuid == "admin":
                        first_uuid = u
            self._ctx_cache = (first_uuid, time.monotonic() + 60.0)
            self._valid_ctx_uuids: set = valid_uuids
        cached_uuid = self._ctx_cache[0]
        # Only honour a caller-supplied UUID if it matches a real context.
        # This prevents hallucinated UUIDs (e.g. device UUIDs passed as context
        # UUIDs by small models) from bypassing NBI discovery.
        if context_uuid and context_uuid != "admin":
            if context_uuid in getattr(self, "_valid_ctx_uuids", set()):
                return context_uuid
        return cached_uuid

    def _discover_topology(self, context_uuid: str) -> str:
        cached_uuid, expiry = self._topo_cache.get(context_uuid, (None, 0.0))
        if cached_uuid is not None and time.monotonic() < expiry:
            return cached_uuid
        result = self._call("tfs_list_topologies", {"context_uuid": context_uuid})
        data = _structured(result)
        topos = data.get("topologies", []) if isinstance(data, dict) else []
        resolved = "admin"
        if topos:
            resolved = (topos[0].get("topology_id", {})
                                .get("topology_uuid", {})
                                .get("uuid", "admin"))
        self._topo_cache[context_uuid] = (resolved, time.monotonic() + 60.0)
        return resolved

    def _get_devices(self, context_uuid: str, topology_uuid: str) -> List[Dict]:
        result = self._call("tfs_get_topology_details", {
            "context_uuid": context_uuid,
            "topology_uuid": topology_uuid,
        })
        data = _structured(result)
        if isinstance(data, dict):
            return data.get("devices", [])
        return []

    def _resolve_endpoint(
        self,
        devices: List[Dict],
        context_uuid: str,
        topology_uuid: str,
        device_name: str,
        endpoint_name: str,
    ) -> Optional[Dict]:
        """Find device+endpoint by name (exact then prefix match) and build endpoint_id."""
        device_name_lower = device_name.lower()
        endpoint_name_lower = endpoint_name.lower()

        matched_device = None
        for device in devices:
            dev_name = device.get("name", "")
            dev_uuid = (device.get("device_id", {})
                              .get("device_uuid", {})
                              .get("uuid", ""))
            if (dev_name.lower() == device_name_lower
                    or dev_uuid.lower() == device_name_lower
                    or device_name_lower in dev_name.lower()):
                matched_device = (device, dev_uuid)
                break

        if matched_device is None:
            return None

        device, dev_uuid = matched_device
        for ep in device.get("device_endpoints", []):
            ep_name = ep.get("name", "")
            ep_uuid = (ep.get("endpoint_id", {})
                         .get("endpoint_uuid", {})
                         .get("uuid", ""))
            if (ep_name.lower() == endpoint_name_lower
                    or ep_uuid.lower() == endpoint_name_lower
                    or endpoint_name_lower in ep_name.lower()):
                return {
                    "topology_id": {
                        "context_id": {"context_uuid": {"uuid": context_uuid}},
                        "topology_uuid": {"uuid": topology_uuid},
                    },
                    "device_id": {"device_uuid": {"uuid": dev_uuid}},
                    "endpoint_uuid": {"uuid": ep_uuid},
                }
        return None

    @staticmethod
    def _endpoint_names(device: Dict, endpoint_id: Dict) -> Tuple[str, str]:
        device_name = device.get("name") or (
            endpoint_id.get("device_id", {})
            .get("device_uuid", {})
            .get("uuid", "")
        )
        endpoint_uuid = endpoint_id.get("endpoint_uuid", {}).get("uuid", "")
        endpoint_name = endpoint_uuid
        for ep in device.get("device_endpoints", []):
            ep_uuid = (
                ep.get("endpoint_id", {})
                .get("endpoint_uuid", {})
                .get("uuid", "")
            )
            if ep_uuid == endpoint_uuid:
                endpoint_name = ep.get("name") or endpoint_uuid
                break
        return device_name, endpoint_name

    @staticmethod
    def _config_rule(resource_key: str, resource_value: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "action": "CONFIGACTION_SET",
            "custom": {
                "resource_key": resource_key,
                "resource_value": json.dumps(resource_value, sort_keys=True),
            },
        }

    @staticmethod
    def _svc_subnet(svc_uuid: str) -> Tuple[int, int]:
        """Derive a stable, unique 10.A.B.0/24 subnet from a service UUID.

        Uses the first 16 bits of SHA-1(uuid) → third and fourth octets.
        Collision probability is 1/65536 across all active services.
        """
        h = int(hashlib.sha1(svc_uuid.encode()).hexdigest()[:4], 16)
        return (h >> 8) & 0xFF, h & 0xFF

    def _build_l3vpn_config_rules(
        self,
        src_device: Dict,
        src_ep_id: Dict,
        dst_device: Dict,
        dst_ep_id: Dict,
        mtu: int,
        svc_uuid: str = "",
    ) -> List[Dict[str, Any]]:
        """Build the minimal L3NM config rules the TFS service handler expects.

        Each service gets a unique 10.A.B.0/24 subnet derived from svc_uuid so
        multiple L3VPN services never share endpoint IPs.
        """
        rules = [self._config_rule("/settings", {"mtu": mtu})]
        third, fourth = self._svc_subnet(svc_uuid) if svc_uuid else (0, 0)
        for index, (device, endpoint_id) in enumerate(
            ((src_device, src_ep_id), (dst_device, dst_ep_id))
        ):
            device_name, endpoint_name = self._endpoint_names(device, endpoint_id)
            rules.append(
                self._config_rule(
                    f"/device[{device_name}]/endpoint[{endpoint_name}]/settings",
                    {
                        "address_ip": f"10.{third}.{fourth}.{1 + index}",
                        "address_prefix": 24,
                        "sub_interface_index": 0,
                    },
                )
            )
        return rules

    @staticmethod
    def _find_device_for_endpoint(devices: List[Dict], endpoint_id: Dict) -> Optional[Dict]:
        target_uuid = (
            endpoint_id.get("device_id", {})
            .get("device_uuid", {})
            .get("uuid", "")
        )
        for device in devices:
            device_uuid = (
                device.get("device_id", {})
                .get("device_uuid", {})
                .get("uuid", "")
            )
            if device_uuid == target_uuid:
                return device
        return None

    @staticmethod
    def _find_endpoint_info(device: Dict, endpoint_id: Dict) -> Optional[Dict]:
        target_uuid = endpoint_id.get("endpoint_uuid", {}).get("uuid", "")
        for endpoint in device.get("device_endpoints", []):
            endpoint_uuid = (
                endpoint.get("endpoint_id", {})
                .get("endpoint_uuid", {})
                .get("uuid", "")
            )
            if endpoint_uuid == target_uuid:
                return endpoint
        return None

    @staticmethod
    def _optical_endpoint_names(device: Dict) -> List[str]:
        names: List[str] = []
        for endpoint in device.get("device_endpoints", []):
            endpoint_type = str(endpoint.get("endpoint_type", "")).lower()
            if "optical" in endpoint_type:
                name = endpoint.get("name") or (
                    endpoint.get("endpoint_id", {})
                    .get("endpoint_uuid", {})
                    .get("uuid", "")
                )
                if name:
                    names.append(name)
        return names

    def _validate_optical_endpoint(
        self,
        devices: List[Dict],
        endpoint_id: Dict,
        role: str,
    ) -> Optional[str]:
        device = self._find_device_for_endpoint(devices, endpoint_id)
        if device is None:
            return f"Error: resolved {role} endpoint but could not find its parent device."

        endpoint = self._find_endpoint_info(device, endpoint_id)
        if endpoint is None:
            return f"Error: resolved {role} endpoint but could not find endpoint metadata."

        endpoint_type = str(endpoint.get("endpoint_type", "")).lower()
        if endpoint_type and "optical" not in endpoint_type:
            device_name = device.get("name") or (
                endpoint_id.get("device_id", {})
                .get("device_uuid", {})
                .get("uuid", "")
            )
            endpoint_name = endpoint.get("name") or (
                endpoint_id.get("endpoint_uuid", {}).get("uuid", "")
            )
            optical_names = self._optical_endpoint_names(device)
            return (
                f"Error: {role} endpoint '{device_name}:{endpoint_name}' has type "
                f"'{endpoint.get('endpoint_type')}', but optical connectivity services "
                "must use optical endpoints. "
                f"Available optical endpoints on {device_name}: {optical_names}."
            )
        return None

    def _find_service(self, context_uuid: str, name_or_uuid: str) -> Optional[Dict]:
        result = self._call("tfs_list_services", {"context_uuid": context_uuid})
        data = _structured(result)
        services = data.get("services", []) if isinstance(data, dict) else []
        target = name_or_uuid.lower()
        for svc in services:
            svc_uuid = (svc.get("service_id", {})
                           .get("service_uuid", {})
                           .get("uuid", ""))
            svc_name = svc.get("name", "")
            if svc_uuid.lower() == target or svc_name.lower() == target:
                return svc
        return None

    # ------------------------------------------------------------------ #
    # Public workflow tools                                                #
    # ------------------------------------------------------------------ #

    def capabilities(self, detail: str = "summary") -> str:
        """Return the proto/NBI/MCP-aligned operation catalog available here."""
        if (detail or "summary").lower() == "full":
            return json.dumps({
                "source_alignment": {
                    "proto": "context.proto (ContextService definitions for all TFS resources)",
                    "nbi": "TFS NBI REST API — /tfs-api/* routes",
                    "mcp": "tfs_mcp_server — tool_definitions.py and server.py",
                },
                "available_capabilities": _CAPABILITY_CATALOG,
                "proto_capabilities_not_exposed_by_current_mcp": _PROTO_NOT_EXPOSED_BY_MCP,
            }, indent=2)
        return json.dumps({
            resource: {
                "read": sorted(spec.get("read", {}).keys()),
                "write": sorted(spec.get("write", {}).keys()),
                "note": spec.get("note"),
            }
            for resource, spec in _CAPABILITY_CATALOG.items()
        }, indent=2)

    @staticmethod
    def _catalog_entry(resource: str, mode: str, operation: str) -> Optional[Dict[str, Any]]:
        resource_key = (resource or "").lower().strip()
        operation_key = (operation or "").lower().strip()
        spec = _CAPABILITY_CATALOG.get(resource_key, {})
        return spec.get(mode, {}).get(operation_key)

    @staticmethod
    def _read_needs_context(resource: str, operation: str) -> bool:
        if resource in {"topology", "service", "slice"}:
            return operation in {"ids", "list", "get", "details"}
        if resource == "context":
            return operation == "get"
        if resource == "connection":
            return operation in {"ids", "list"}
        return False

    def _resolve_service_uuid_for_generic(self, context_uuid: str, identifier: Optional[str]) -> str:
        if not identifier:
            return ""
        svc = self._find_service(context_uuid, identifier)
        if svc is not None:
            return svc.get("service_id", {}).get("service_uuid", {}).get("uuid", identifier)
        return identifier

    def _build_read_args(
        self,
        resource: str,
        operation: str,
        context_uuid: str,
        identifier: Optional[str],
        topology_uuid: Optional[str],
        service_uuid: Optional[str],
        device_uuid: Optional[str],
        link_uuid: Optional[str],
        slice_uuid: Optional[str],
        connection_uuid: Optional[str],
        policyrule_uuid: Optional[str],
        rc_path: Optional[str],
    ) -> Dict[str, Any]:
        resource = resource.lower()
        operation = operation.lower()
        args: Dict[str, Any] = {}
        if resource == "context" and operation == "get":
            args["context_uuid"] = identifier or context_uuid
        if resource in {"topology", "service", "slice"} and operation in {"ids", "list", "get", "details"}:
            args["context_uuid"] = context_uuid
        if resource == "topology" and operation in {"get", "details"}:
            args["topology_uuid"] = topology_uuid or identifier or "admin"
        elif resource == "service" and operation == "get":
            args["service_uuid"] = self._resolve_service_uuid_for_generic(context_uuid, service_uuid or identifier)
        elif resource == "slice" and operation == "get":
            args["slice_uuid"] = slice_uuid or identifier or ""
        elif resource == "device" and operation == "get":
            args["device_uuid"] = device_uuid or identifier or ""
        elif resource == "link" and operation == "get":
            args["link_uuid"] = link_uuid or identifier or ""
        elif resource == "connection":
            if operation in {"ids", "list"}:
                args["context_uuid"] = context_uuid
                args["service_uuid"] = self._resolve_service_uuid_for_generic(context_uuid, service_uuid or identifier)
            elif operation == "get":
                args["connection_uuid"] = connection_uuid or identifier or ""
        elif resource == "policyrule" and operation == "get":
            args["policyrule_uuid"] = policyrule_uuid or identifier or ""
        elif resource == "dscm_oc":
            args["device_uuid"] = device_uuid or identifier or ""
            args["rc_path"] = rc_path or ""
        return args

    def read_resource(
        self,
        resource: str,
        operation: str = "list",
        identifier: Optional[str] = None,
        context_uuid: Optional[str] = None,
        topology_uuid: Optional[str] = None,
        service_uuid: Optional[str] = None,
        device_uuid: Optional[str] = None,
        link_uuid: Optional[str] = None,
        slice_uuid: Optional[str] = None,
        connection_uuid: Optional[str] = None,
        policyrule_uuid: Optional[str] = None,
        rc_path: Optional[str] = None,
    ) -> str:
        """Generic read-only TFS operation aligned to proto/NBI/MCP mappings."""
        context_uuid = context_uuid or "admin"
        resource_key = (resource or "").lower().strip()
        operation_key = (operation or "").lower().strip()
        entry = self._catalog_entry(resource_key, "read", operation_key)
        if entry is None:
            return json.dumps({
                "error": f"Unsupported read operation: {resource}.{operation}",
                "available": json.loads(self.capabilities()),
            }, indent=2)
        ctx_uuid = (
            self._discover_context(context_uuid)
            if self._read_needs_context(resource_key, operation_key)
            else context_uuid
        )
        args = self._build_read_args(
            resource_key, operation_key, ctx_uuid, identifier, topology_uuid, service_uuid,
            device_uuid, link_uuid, slice_uuid, connection_uuid, policyrule_uuid, rc_path,
        )
        missing = [key for key, value in args.items() if value in (None, "")]
        if missing:
            return json.dumps({"error": "missing required argument(s)", "missing": missing, "args": args}, indent=2)
        result = self._call(entry["tool"], args)
        return json.dumps({
            "resource": resource_key,
            "operation": operation_key,
            "tool": entry["tool"],
            "endpoint": entry.get("endpoint"),
            "arguments": args,
            "result": _structured(result) or result.get("text", ""),
        }, indent=2)

    def _build_write_args(
        self,
        resource: str,
        operation: str,
        payload: Optional[Any],
        context_uuid: str,
        identifier: Optional[str],
        topology_uuid: Optional[str],
        service_uuid: Optional[str],
        device_uuid: Optional[str],
        link_uuid: Optional[str],
        slice_uuid: Optional[str],
        allocation_id: Optional[str],
        rc_path: Optional[str],
    ) -> Dict[str, Any]:
        resource = resource.lower()
        args: Dict[str, Any] = {}
        if resource in {"context", "topology", "service", "slice"}:
            if operation not in {"set_many"} or resource != "context":
                args["context_uuid"] = context_uuid
        if resource == "context":
            if operation == "set_many":
                args["contexts"] = payload if isinstance(payload, list) else [payload]
            elif operation == "set":
                args["context"] = payload
            elif operation == "delete":
                args["context_uuid"] = identifier or context_uuid
        elif resource == "topology":
            if operation == "set_many":
                args["topologies"] = payload if isinstance(payload, list) else [payload]
            else:
                args["topology_uuid"] = topology_uuid or identifier or "admin"
                if operation == "set":
                    args["topology"] = payload
        elif resource == "service":
            if operation == "create_many":
                args["services"] = payload if isinstance(payload, list) else [payload]
            else:
                args["service_uuid"] = self._resolve_service_uuid_for_generic(context_uuid, service_uuid or identifier)
                if operation == "update":
                    args["service"] = payload
        elif resource == "slice":
            if operation == "create_many":
                args["slices"] = payload if isinstance(payload, list) else [payload]
            else:
                args["slice_uuid"] = slice_uuid or identifier or ""
                if operation == "update":
                    args["slice"] = payload
        elif resource == "device":
            if operation == "add_many":
                args["devices"] = payload if isinstance(payload, list) else [payload]
            else:
                args["device_uuid"] = device_uuid or identifier or ""
                if operation == "configure":
                    args["device"] = payload
        elif resource == "link":
            if operation == "set_many":
                args["links"] = payload if isinstance(payload, list) else [payload]
            else:
                args["link_uuid"] = link_uuid or identifier or ""
                if operation == "set":
                    args["link"] = payload
        elif resource == "optical_slice":
            slice_id = slice_uuid or identifier or ""
            # Fallback for model calls that provide slice identity inside payload
            # but forget identifier/slice_uuid.
            if not slice_id and isinstance(payload, dict):
                for key in ("slice_id", "slice_name", "name"):
                    val = payload.get(key)
                    if isinstance(val, str) and val.strip():
                        slice_id = val.strip()
                        break
            args["slice_id"] = slice_id
            if operation == "create":
                args["slice"] = payload
        elif resource == "media_channel":
            args["allocation_id"] = allocation_id or identifier or ""
            if operation == "allocate":
                args["media_channel"] = payload
        elif resource == "dscm_oc":
            args["device_uuid"] = device_uuid or identifier or ""
            args["rc_path"] = rc_path or ""
            if operation == "post":
                args["config"] = payload
        return args

    def write_resource(
        self,
        resource: str,
        operation: str,
        payload: Optional[Any] = None,
        identifier: Optional[str] = None,
        context_uuid: Optional[str] = None,
        topology_uuid: Optional[str] = None,
        service_uuid: Optional[str] = None,
        device_uuid: Optional[str] = None,
        link_uuid: Optional[str] = None,
        slice_uuid: Optional[str] = None,
        allocation_id: Optional[str] = None,
        rc_path: Optional[str] = None,
    ) -> str:
        """Generic write/delete TFS operation aligned to proto/NBI/MCP mappings."""
        context_uuid = context_uuid or "admin"
        resource_key = (resource or "").lower().strip()
        operation_key = (operation or "").lower().strip()
        operation_name = f"{resource_key}.{operation_key}"
        actual_tool = (_TURN_CALLER_TOOL.get() or "").strip() or "tfs_write"
        if operation_key == "delete" and resource_key == "optical_slice":
            allowed_intents = {"delete", "optical_slice_restconf", "generic_write"}
        elif operation_key == "delete":
            allowed_intents = {"delete", "generic_write"}
        elif resource_key == "optical_slice":
            allowed_intents = {"optical_slice_restconf"}
        elif resource_key == "service":
            allowed_intents = {"packet_service", "optical_connectivity_service", "generic_write"}
        else:
            allowed_intents = {"generic_write"}

        intent_guard = self._require_mutation_intent(
            actual_tool=actual_tool,
            allowed_intents=allowed_intents,
            operation=operation_name,
        )
        if intent_guard:
            return intent_guard
        auto_context_uuid: Optional[str] = None
        normalized_payload = payload
        if resource_key == "optical_slice" and operation_key == "create":
            normalized_payload, auto_context_uuid = self._ensure_optical_slice_context_uuid(payload)
        entry = self._catalog_entry(resource_key, "write", operation_key)
        if entry is None:
            supported_ops = sorted(_CAPABILITY_CATALOG.get(resource_key, {}).get("write", {}).keys())
            return self._mutation_result(
                ok=False,
                phase="validate_args",
                operation=operation_name,
                message=f"Unsupported write operation: {resource}.{operation}",
                retryable=True,
                error="unsupported_write_operation",
                details={
                    "resource": resource_key,
                    "attempted_operation": operation_key,
                    "supported_write_operations": supported_ops,
                    "hint": (
                        "Call tfs_capabilities(detail='summary') to see valid operations."
                        if supported_ops
                        else "This resource has no write operations in the current MCP exposure."
                    ),
                },
            )
        ctx_uuid = self._discover_context(context_uuid)
        args = self._build_write_args(
            resource_key, operation_key, normalized_payload, ctx_uuid, identifier, topology_uuid,
            service_uuid, device_uuid, link_uuid, slice_uuid, allocation_id, rc_path,
        )
        missing = [key for key, value in args.items() if value in (None, "")]
        if missing:
            hint = "Provide the missing argument(s) and retry once."
            if resource_key == "optical_slice" and "slice_id" in missing:
                hint = (
                    "For optical_slice create/delete, pass identifier='<slice-id>' "
                    "(mapped to slice_id)."
                )
            elif resource_key == "media_channel" and "allocation_id" in missing:
                hint = (
                    "For media_channel allocate/release, pass allocation_id "
                    "or identifier='<allocation-id>'."
                )
            return self._mutation_result(
                ok=False,
                phase="validate_args",
                operation=operation_name,
                message="Missing required argument(s).",
                retryable=True,
                error="missing_required_arguments",
                details={
                    "missing": missing,
                    "args": args,
                    "hint": hint,
                    "do_not_retry_same_arguments": True,
                },
            )
        result = self._call(entry["tool"], args)
        result_data = _structured(result)
        if _is_error(result_data):
            return self._mutation_result(
                ok=False,
                phase="apply",
                operation=operation_name,
                message="Controller mutation failed.",
                retryable=True,
                error=result_data.get("error", result.get("text", "unknown")),
                details={
                    "tool": entry["tool"],
                    "endpoint": entry.get("endpoint"),
                    "arguments": args,
                    "result": result_data or result.get("text", ""),
                },
            )
        response_obj: Dict[str, Any] = {
            "tool": entry["tool"],
            "endpoint": entry.get("endpoint"),
            "arguments": args,
            "result": result_data or result.get("text", ""),
        }
        if auto_context_uuid:
            response_obj["auto_generated"] = {
                "tapi-common:context.uuid": auto_context_uuid
            }
        return self._mutation_result(
            ok=True,
            phase="applied",
            operation=operation_name,
            message="Controller mutation applied.",
            retryable=False,
            observed_status="APPLIED",
            details=response_obj,
        )

    def inventory(self, context_uuid: Optional[str] = None) -> str:
        """Return a compact TFS inventory: context, topology, devices with endpoints,
        and existing services. Always call this first before any service operation."""
        ctx_uuid = self._discover_context(context_uuid or "admin")
        topo_uuid = self._discover_topology(ctx_uuid)
        devices = self._get_devices(ctx_uuid, topo_uuid)

        device_summary = []
        for d in devices:
            dev_name = d.get("name", "?")
            dev_uuid = (d.get("device_id", {}).get("device_uuid", {}).get("uuid", "?"))
            status_code = d.get("device_operational_status", 0)
            status = {0: "UNDEFINED", 1: "DISABLED", 2: "ENABLED"}.get(status_code, str(status_code))
            endpoints = [
                {
                    "name": ep.get("name", "?"),
                    "uuid": (ep.get("endpoint_id", {})
                               .get("endpoint_uuid", {})
                               .get("uuid", "?")),
                    "type": ep.get("endpoint_type", ""),
                }
                for ep in d.get("device_endpoints", [])
            ]
            device_summary.append({
                "name": dev_name,
                "uuid": dev_uuid,
                "status": status,
                "endpoints": endpoints,
            })

        svc_data = _structured(self._call("tfs_list_services", {"context_uuid": ctx_uuid}))
        services = svc_data.get("services", []) if isinstance(svc_data, dict) else []
        service_summary = [
            {
                "name": svc.get("name", "?"),
                "uuid": (svc.get("service_id", {})
                            .get("service_uuid", {})
                            .get("uuid", "?")),
                "type": svc.get("service_type", "?"),
                "status": (svc.get("service_status", {})
                              .get("service_status", "?")),
            }
            for svc in services
        ]

        # Detect duplicate names so the model knows each entry is a distinct service.
        seen_names: dict[str, int] = {}
        for s in service_summary:
            seen_names[s["name"]] = seen_names.get(s["name"], 0) + 1
        duplicate_names = [n for n, cnt in seen_names.items() if cnt > 1]

        result: dict = {
            "context_uuid": ctx_uuid,
            "topology_uuid": topo_uuid,
            "device_count": len(device_summary),
            "devices": device_summary,
            "service_count": len(service_summary),
            "services": service_summary,
        }
        if duplicate_names:
            result["warning"] = (
                f"Multiple services share the same name: {duplicate_names}. "
                "They are distinct services — each has a unique UUID. "
                "Report all of them to the user."
            )
        return json.dumps(result, indent=2)

    def provision_service(
        self,
        service_type: str,
        src_device: str,
        src_endpoint: str,
        dst_device: str,
        dst_endpoint: str,
        name: Optional[str] = None,
        capacity_gbps: Optional[float] = None,
        mtu: int = 1512,
        context_uuid: Optional[str] = None,
    ) -> str:
        """Provision a TFS service end-to-end.
        Discovers UUIDs by name, creates the service, applies endpoints and
        constraints, and polls until ACTIVE (up to 5 retries).

        service_type: l3vpn | l2vpn | optical | e2e
        src_device / dst_device: device names exactly as shown by tfs_inventory
        src_endpoint / dst_endpoint: endpoint names on those devices
        name: optional — auto-generated from device names if omitted
        capacity_gbps: optional bandwidth constraint
        """
        context_uuid = context_uuid or "admin"
        operation_name = "service.provision"
        normalized_type = service_type.lower().replace("-", "_")
        resolved_type = _SERVICE_TYPE_MAP.get(normalized_type, "")
        if not resolved_type:
            resolved_type = (
                service_type
                if service_type.startswith("SERVICETYPE_")
                else f"SERVICETYPE_{service_type.upper()}"
            )

        # Keep packet workflows untouched while routing optical connectivity to
        # its dedicated, controller-aligned provisioning path.
        if resolved_type == "SERVICETYPE_OPTICAL_CONNECTIVITY":
            return self.provision_optical_connectivity_service(
                src_device=src_device,
                src_endpoint=src_endpoint,
                dst_device=dst_device,
                dst_endpoint=dst_endpoint,
                name=name,
                capacity_gbps=capacity_gbps if capacity_gbps is not None else 100.0,
                context_uuid=context_uuid,
            )

        if not name:
            stype = service_type.lower().replace("vpn", "").replace("nm", "")
            name = f"{stype}-{src_device}-{dst_device}".lower()
        ctx_uuid = self._discover_context(context_uuid)
        topo_uuid = self._discover_topology(ctx_uuid)
        devices = self._get_devices(ctx_uuid, topo_uuid)

        if not devices:
            return self._mutation_result(
                ok=False,
                phase="resolve_target",
                operation=operation_name,
                message=(
                    f"No devices found in topology '{topo_uuid}' of context '{ctx_uuid}'."
                ),
                retryable=True,
                error="inventory_empty",
                details={"hint": "Check TFS controller reachability and topology content."},
            )

        src_ep_id = self._resolve_endpoint(devices, ctx_uuid, topo_uuid, src_device, src_endpoint)
        if src_ep_id is None:
            names = [d.get("name") for d in devices]
            return self._mutation_result(
                ok=False,
                phase="resolve_target",
                operation=operation_name,
                message=f"Source endpoint '{src_device}:{src_endpoint}' not found.",
                retryable=True,
                error="source_endpoint_not_found",
                details={"known_devices": names, "hint": "Call tfs_inventory and retry with exact endpoint names."},
            )
        src_device_obj = self._find_device_for_endpoint(devices, src_ep_id)
        if src_device_obj is None:
            return self._mutation_result(
                ok=False,
                phase="resolve_target",
                operation=operation_name,
                message="Resolved source endpoint but parent device lookup failed.",
                retryable=True,
                error="source_parent_device_missing",
            )

        dst_ep_id = self._resolve_endpoint(devices, ctx_uuid, topo_uuid, dst_device, dst_endpoint)
        if dst_ep_id is None:
            names = [d.get("name") for d in devices]
            return self._mutation_result(
                ok=False,
                phase="resolve_target",
                operation=operation_name,
                message=f"Destination endpoint '{dst_device}:{dst_endpoint}' not found.",
                retryable=True,
                error="destination_endpoint_not_found",
                details={"known_devices": names, "hint": "Call tfs_inventory and retry with exact endpoint names."},
            )
        dst_device_obj = self._find_device_for_endpoint(devices, dst_ep_id)
        if dst_device_obj is None:
            return self._mutation_result(
                ok=False,
                phase="resolve_target",
                operation=operation_name,
                message="Resolved destination endpoint but parent device lookup failed.",
                retryable=True,
                error="destination_parent_device_missing",
            )

        svc_type = resolved_type
        svc_uuid = str(uuid.uuid4())

        # Step 2: Apply full intent
        constraints = []
        if capacity_gbps is not None:
            constraints.append({
                "action": "CONSTRAINTACTION_SET",
                "sla_capacity": {"capacity_gbps": float(capacity_gbps)},
            })

        config_rules: List[Dict[str, Any]] = []
        if svc_type == "SERVICETYPE_L3NM":
            config_rules = self._build_l3vpn_config_rules(
                src_device_obj,
                src_ep_id,
                dst_device_obj,
                dst_ep_id,
                int(mtu or 1512),
                svc_uuid=svc_uuid,
            )

        return self._create_update_and_wait_service(
            context_uuid=ctx_uuid,
            name=name,
            svc_type=svc_type,
            svc_uuid=svc_uuid,
            src_ep_id=src_ep_id,
            dst_ep_id=dst_ep_id,
            constraints=constraints,
            config_rules=config_rules,
            optical_poll=False,
        )

    def provision_optical_connectivity_service(
        self,
        src_device: str,
        dst_device: str,
        src_endpoint: str = "line",
        dst_endpoint: str = "line",
        name: Optional[str] = None,
        capacity_gbps: float = 100.0,
        bidirectional: bool = True,
        optical_band_width_ghz: int = 200,
        optical_type: str = "multi_granular",
        context_uuid: Optional[str] = None,
    ) -> str:
        """Provision an optical connectivity service with TFS optical custom constraints.

        This path is separate from packet L2/L3 provisioning so those workflows
        remain unchanged.
        """
        context_uuid = context_uuid or "admin"
        operation_name = "service.provision_optical_connectivity"
        if not name:
            name = f"optical-{src_device}-{dst_device}".lower()
        ctx_uuid = self._discover_context(context_uuid)
        topo_uuid = self._discover_topology(ctx_uuid)
        devices = self._get_devices(ctx_uuid, topo_uuid)

        if not devices:
            return self._mutation_result(
                ok=False,
                phase="resolve_target",
                operation=operation_name,
                message=(
                    f"No devices found in topology '{topo_uuid}' of context '{ctx_uuid}'."
                ),
                retryable=True,
                error="inventory_empty",
                details={"hint": "Check TFS controller reachability and topology content."},
            )

        src_ep_id = self._resolve_endpoint(devices, ctx_uuid, topo_uuid, src_device, src_endpoint)
        if src_ep_id is None:
            names = [d.get("name") for d in devices]
            return self._mutation_result(
                ok=False,
                phase="resolve_target",
                operation=operation_name,
                message=f"Source endpoint '{src_device}:{src_endpoint}' not found.",
                retryable=True,
                error="source_endpoint_not_found",
                details={"known_devices": names, "hint": "Call tfs_inventory and retry with exact endpoint names."},
            )

        dst_ep_id = self._resolve_endpoint(devices, ctx_uuid, topo_uuid, dst_device, dst_endpoint)
        if dst_ep_id is None:
            names = [d.get("name") for d in devices]
            return self._mutation_result(
                ok=False,
                phase="resolve_target",
                operation=operation_name,
                message=f"Destination endpoint '{dst_device}:{dst_endpoint}' not found.",
                retryable=True,
                error="destination_endpoint_not_found",
                details={"known_devices": names, "hint": "Call tfs_inventory and retry with exact endpoint names."},
            )

        src_validation_error = self._validate_optical_endpoint(devices, src_ep_id, "source")
        if src_validation_error:
            return self._mutation_result(
                ok=False,
                phase="validate_args",
                operation=operation_name,
                message=src_validation_error,
                retryable=True,
                error="invalid_source_endpoint_type",
            )

        dst_validation_error = self._validate_optical_endpoint(devices, dst_ep_id, "destination")
        if dst_validation_error:
            return self._mutation_result(
                ok=False,
                phase="validate_args",
                operation=operation_name,
                message=dst_validation_error,
                retryable=True,
                error="invalid_destination_endpoint_type",
            )

        constraints: List[Dict[str, Any]] = [
            {
                "action": "CONSTRAINTACTION_SET",
                "custom": {
                    "constraint_type": "bandwidth[gbps]",
                    "constraint_value": str(float(capacity_gbps)),
                }
            },
            {
                "action": "CONSTRAINTACTION_SET",
                "custom": {
                    "constraint_type": "bidirectionality",
                    "constraint_value": "1" if bidirectional else "0",
                }
            },
            {
                "action": "CONSTRAINTACTION_SET",
                "custom": {
                    "constraint_type": "optical-band-width[GHz]",
                    "constraint_value": str(int(optical_band_width_ghz)),
                }
            },
            {
                "action": "CONSTRAINTACTION_SET",
                "custom": {
                    "constraint_type": "type",
                    "constraint_value": str(optical_type),
                }
            },
        ]

        return self._create_update_and_wait_service(
            context_uuid=ctx_uuid,
            name=name,
            svc_type="SERVICETYPE_OPTICAL_CONNECTIVITY",
            svc_uuid=None,
            src_ep_id=src_ep_id,
            dst_ep_id=dst_ep_id,
            constraints=constraints,
            config_rules=[],
            optical_poll=True,
        )

    def _create_update_and_wait_service(
        self,
        *,
        context_uuid: str,
        name: str,
        svc_type: str,
        svc_uuid: Optional[str],
        src_ep_id: Dict[str, Any],
        dst_ep_id: Dict[str, Any],
        constraints: List[Dict[str, Any]],
        config_rules: List[Dict[str, Any]],
        optical_poll: bool,
    ) -> str:
        svc_uuid = svc_uuid or str(uuid.uuid4())
        operation_name = "service.provision"
        service_id = {
            "context_id": {"context_uuid": {"uuid": context_uuid}},
            "service_uuid": {"uuid": svc_uuid},
        }

        # Step 1: Reserve UUID
        create_res = self._call("tfs_create_services", {
            "context_uuid": context_uuid,
            "services": [{
                "service_id": service_id,
                "name": name,
                "service_type": svc_type,
            }],
        })
        create_data = _structured(create_res)
        if _is_error(create_data):
            return self._mutation_result(
                ok=False,
                phase="reserve",
                operation=operation_name,
                service_uuid=svc_uuid,
                message="Failed to reserve service UUID.",
                retryable=True,
                error=create_data.get("error", create_res.get("text", "unknown")),
                details={"create_result": create_data or create_res.get("text", "")},
            )

        # Step 2: Apply full intent
        update_res = self._call("tfs_update_service", {
            "context_uuid": context_uuid,
            "service_uuid": svc_uuid,
            "service": {
                "service_id": service_id,
                "name": name,
                "service_type": svc_type,
                "service_endpoint_ids": [src_ep_id, dst_ep_id],
                "service_constraints": constraints,
                "service_config": {"config_rules": config_rules},
            },
        })
        update_data = _structured(update_res)
        if _is_error(update_data):
            return self._mutation_result(
                ok=False,
                phase="apply",
                operation=operation_name,
                service_uuid=svc_uuid,
                message="Failed to apply service intent.",
                retryable=True,
                error=update_data.get("error", update_res.get("text", "unknown")),
                details={"update_result": update_data or update_res.get("text", "")},
            )

        verify_res = self._call("tfs_get_service", {
            "context_uuid": context_uuid,
            "service_uuid": svc_uuid,
        })
        verify_data = _structured(verify_res)
        if _is_error(verify_data):
            return self._mutation_result(
                ok=False,
                phase="verify",
                operation=operation_name,
                service_uuid=svc_uuid,
                message="Failed to verify persisted service intent.",
                retryable=True,
                error=verify_data.get("error", verify_res.get("text", "unknown")),
                details={"verify_result": verify_data or verify_res.get("text", "")},
            )

        persisted_endpoints = (
            verify_data.get("service_endpoint_ids", [])
            if isinstance(verify_data, dict) else []
        )
        if len(persisted_endpoints) < 2:
            return self._mutation_result(
                ok=False,
                phase="verify",
                operation=operation_name,
                service_uuid=svc_uuid,
                observed_status="SERVICESTATUS_UNDEFINED",
                message=(
                    f"Service '{name}' was reserved but requested endpoints were not persisted."
                ),
                retryable=True,
                error="reserved_shell_service",
                details={
                    "hint": (
                        "Service remains a reserved shell. Delete it before retrying provisioning."
                    ),
                    "update_result": update_data or update_res.get("text", ""),
                    "persisted_endpoint_count": len(persisted_endpoints),
                },
            )

        # Step 3: Poll for ACTIVE
        # Optical connectivity commonly needs longer path computation and
        # spectrum assignment time than packet services.
        is_optical = optical_poll or svc_type == "SERVICETYPE_OPTICAL_CONNECTIVITY"
        max_checks = 30 if is_optical else 5
        poll_interval_s = 2 if is_optical else 1

        last_status = "UNKNOWN"
        for attempt in range(max_checks):
            time.sleep(poll_interval_s)
            status_res = self._call("tfs_get_service", {
                "context_uuid": context_uuid,
                "service_uuid": svc_uuid,
            })
            svc_data = _structured(status_res)
            raw_status = (
                svc_data.get("service_status", {}).get("service_status", "UNKNOWN")
                if isinstance(svc_data, dict) else "UNKNOWN"
            )
            if isinstance(raw_status, int):
                last_status = {
                    0: "SERVICESTATUS_UNDEFINED",
                    1: "SERVICESTATUS_PLANNED",
                    2: "SERVICESTATUS_ACTIVE",
                    3: "SERVICESTATUS_UPDATING",
                    4: "SERVICESTATUS_PENDING_REMOVAL",
                    5: "SERVICESTATUS_SLA_VIOLATED",
                }.get(raw_status, str(raw_status))
            else:
                last_status = str(raw_status)
            LOGGER.info(
                "Service %s status (attempt %d/%d): %s",
                svc_uuid,
                attempt + 1,
                max_checks,
                last_status,
            )
            if last_status == "SERVICESTATUS_ACTIVE":
                return self._mutation_result(
                    ok=True,
                    phase="active",
                    operation=operation_name,
                    service_uuid=svc_uuid,
                    observed_status=last_status,
                    message=f"Service '{name}' is ACTIVE.",
                    retryable=False,
                    details={
                        "service_name": name,
                        "service_type": svc_type,
                        "max_checks": max_checks,
                        "poll_interval_s": poll_interval_s,
                    },
                )

        return self._mutation_result(
            ok=False,
            phase="poll_timeout",
            operation=operation_name,
            service_uuid=svc_uuid,
            observed_status=last_status,
            message=(
                f"Service '{name}' was created but did not reach ACTIVE within polling window."
            ),
            retryable=True,
            error="service_not_active_yet",
            details={
                "service_name": name,
                "service_type": svc_type,
                "max_checks": max_checks,
                "poll_interval_s": poll_interval_s,
                "total_wait_s": max_checks * poll_interval_s,
                "hint": (
                    "If status remains SERVICESTATUS_UNDEFINED, check endpoint types, "
                    "optical constraints, and controller service logs."
                ),
            },
        )


    def _cross_domain_a2a_preflight(
        self,
        service_type: str,
        src_device: str,
        src_endpoint: str,
        dst_device: str,
        dst_endpoint: str,
        context_uuid: str,
    ) -> Optional[str]:
        """Detect packet requests that should be delegated through A2A.

        This is intentionally read-only. It prevents a missing remote endpoint
        from consuming the one local provisioning attempt for the current turn.
        """
        normalized_type = (service_type or "").lower().replace("-", "_")
        peers_env = os.environ.get("A2A_PEERS", "").strip()
        if not peers_env:
            return None

        try:
            ctx_uuid = self._discover_context(context_uuid)
            topo_uuid = self._discover_topology(ctx_uuid)
            devices = self._get_devices(ctx_uuid, topo_uuid)
        except Exception:
            return None
        if not devices:
            return None

        def has_local_device(device_name: str) -> bool:
            wanted = str(device_name or "").lower()
            for device in devices:
                dev_name = str(device.get("name", ""))
                dev_uuid = (device.get("device_id", {})
                                  .get("device_uuid", {})
                                  .get("uuid", ""))
                if (dev_name.lower() == wanted
                        or dev_uuid.lower() == wanted
                        or wanted in dev_name.lower()):
                    return True
            return False

        src_device_local = has_local_device(src_device)
        dst_device_local = has_local_device(dst_device)
        if src_device_local == dst_device_local:
            return None

        src_ep_id = self._resolve_endpoint(devices, ctx_uuid, topo_uuid, src_device, src_endpoint)
        dst_ep_id = self._resolve_endpoint(devices, ctx_uuid, topo_uuid, dst_device, dst_endpoint)
        if src_device_local and src_ep_id is None:
            return None
        if dst_device_local and dst_ep_id is None:
            return None

        net_ext_ep_id = self._resolve_endpoint(devices, ctx_uuid, topo_uuid, "NET-EXT", "access0")
        if net_ext_ep_id is None:
            return None

        known_devices = [d.get("name") for d in devices]

        def service_endpoint(device: str, requested_endpoint: str, is_local: bool) -> str:
            # Datacenter access services should use eth0 when it is available.
            # The internal endpoint named "int" is not the packet service handoff.
            if str(device).upper().startswith("DC"):
                if not is_local:
                    return "eth0"
                if self._resolve_endpoint(devices, ctx_uuid, topo_uuid, device, "eth0") is not None:
                    return "eth0"
            return requested_endpoint

        if src_ep_id is not None:
            local_src_endpoint = service_endpoint(src_device, src_endpoint, is_local=True)
            remote_dst_endpoint = service_endpoint(dst_device, dst_endpoint, is_local=False)
            local_segment = {
                "src_device": src_device,
                "src_endpoint": local_src_endpoint,
                "dst_device": "NET-EXT",
                "dst_endpoint": "access0",
            }
            peer_segment = {
                "src_device": "NET-EXT",
                "src_endpoint": "access0",
                "dst_device": dst_device,
                "dst_endpoint": remote_dst_endpoint,
            }
            missing = {"device": dst_device, "endpoint": remote_dst_endpoint, "side": "destination"}
        else:
            local_dst_endpoint = service_endpoint(dst_device, dst_endpoint, is_local=True)
            remote_src_endpoint = service_endpoint(src_device, src_endpoint, is_local=False)
            local_segment = {
                "src_device": "NET-EXT",
                "src_endpoint": "access0",
                "dst_device": dst_device,
                "dst_endpoint": local_dst_endpoint,
            }
            peer_segment = {
                "src_device": "NET-EXT",
                "src_endpoint": "access0",
                "dst_device": src_device,
                "dst_endpoint": remote_src_endpoint,
            }
            missing = {"device": src_device, "endpoint": remote_src_endpoint, "side": "source"}

        packet_structured = normalized_type in {"l2vpn", "l2nm", "l2", "l3vpn", "l3nm", "l3"}
        plan = {
            "service_type": service_type,
            "original_request": {
                "src_device": src_device,
                "src_endpoint": src_endpoint,
                "dst_device": dst_device,
                "dst_endpoint": dst_endpoint,
            },
            "peer_segment_to_request_via_a2a": peer_segment,
            "local_segment_to_provision_after_peer_success": local_segment,
        }
        self._cross_domain_packet_plans[_TURN_SESSION_ID.get() or "default"] = plan

        next_tools = ["a2a_list_peers", "a2a_discover_peer"]
        if packet_structured:
            next_tools.append("a2a_provision_peer_packet_service")
        else:
            next_tools.append("a2a_send_peer_message")
        next_tools.append("tfs_provision_service")

        return self._mutation_result(
            ok=False,
            phase="cross_domain_a2a_required",
            operation="service.provision",
            message=(
                "One requested endpoint or node is not in the local controller topology, "
                "and A2A peers are configured. Treat the missing node as belonging "
                "potentially to another SDN controller domain. Contact the peer agent via A2A "
                "and let that peer decide whether it can handle the remote segment. Do not "
                "report the node as missing until the peer rejects the A2A request."
            ),
            retryable=True,
            error="cross_domain_a2a_required",
            details={
                "missing_local_endpoint": missing,
                "known_local_devices": known_devices,
                "handoff": {"device": "NET-EXT", "endpoint": "access0"},
                "peer_segment_to_request_via_a2a": peer_segment,
                "local_segment_to_provision_after_peer_success": local_segment,
                "next_tools": next_tools,
                "structured_packet_a2a_available": packet_structured,
                "generic_peer_message_available": True,
                "peer_internal_topology_required": False,
            },
        )

    def _guarded_provision_service(
        self,
        service_type: str,
        src_device: str,
        src_endpoint: str,
        dst_device: str,
        dst_endpoint: str,
        name: Optional[str] = None,
        capacity_gbps: Optional[float] = None,
        mtu: int = 1512,
        context_uuid: Optional[str] = None,
    ) -> str:
        """Wrapper enforcing that tfs_provision_service is called at most once per turn."""
        context_uuid = context_uuid or "admin"
        sid = _TURN_SESSION_ID.get()
        intent_guard = self._require_mutation_intent(
            actual_tool="tfs_provision_service",
            allowed_intents={"packet_service"},
            operation="service.provision",
        )
        if intent_guard:
            return intent_guard
        cross_domain_hint = self._cross_domain_a2a_preflight(
            service_type=service_type,
            src_device=src_device,
            src_endpoint=src_endpoint,
            dst_device=dst_device,
            dst_endpoint=dst_endpoint,
            context_uuid=context_uuid,
        )
        if cross_domain_hint:
            return cross_domain_hint

        normalized_type = (service_type or "").lower().replace("-", "_")
        packet_service = normalized_type in {"l2vpn", "l2nm", "l2", "l3vpn", "l3nm", "l3"}
        uses_net_ext_handoff = (
            str(src_device).upper() == "NET-EXT"
            or str(dst_device).upper() == "NET-EXT"
        )
        def packet_family(value: str) -> str:
            family = (value or "").lower().replace("-", "_")
            if family in {"l2vpn", "l2nm", "l2"}:
                return "l2vpn"
            if family in {"l3vpn", "l3nm", "l3"}:
                return "l3vpn"
            return family

        pending_plan = self._cross_domain_packet_plans.get(sid)
        pending_type = packet_family(str((pending_plan or {}).get("service_type", "")))
        requested_type = packet_family(service_type)
        if (
            packet_service
            and uses_net_ext_handoff
            and pending_type
            and requested_type != pending_type
        ):
            return self._mutation_result(
                ok=False,
                phase="cross_domain_service_type_mismatch",
                operation="service.provision",
                message=(
                    "The local NET-EXT handoff segment must use the same packet "
                    "service type as the original cross-domain request. Retry the "
                    f"local handoff with service_type='{pending_type}'."
                ),
                retryable=True,
                error="cross_domain_service_type_mismatch",
                details={
                    "original_cross_domain_service_type": pending_type,
                    "requested_local_service_type": requested_type,
                    "pending_cross_domain_plan": pending_plan,
                    "local_segment_attempt": {
                        "src_device": src_device,
                        "src_endpoint": src_endpoint,
                        "dst_device": dst_device,
                        "dst_endpoint": dst_endpoint,
                    },
                },
            )
        if (
            packet_service
            and uses_net_ext_handoff
            and os.environ.get("A2A_PEERS", "").strip()
            and not self._a2a_peer_packet_provisioned_this_turn()
        ):
            return self._mutation_result(
                ok=False,
                phase="a2a_peer_segment_required",
                operation="service.provision",
                message=(
                    "This looks like a cross-domain NET-EXT handoff segment. "
                    "Provision the peer packet segment via A2A first, then retry "
                    "the local NET-EXT segment only after the peer reports success."
                ),
                retryable=True,
                error="a2a_peer_segment_required",
                details={
                    "required_before_local_segment": "a2a_provision_peer_packet_service",
                    "recommended_tool": "a2a_provision_cross_domain_packet_service",
                    "pending_cross_domain_plan": self._cross_domain_packet_plans.get(sid),
                    "local_segment": {
                        "src_device": src_device,
                        "src_endpoint": src_endpoint,
                        "dst_device": dst_device,
                        "dst_endpoint": dst_endpoint,
                    },
                },
            )
        if _TURN_PROVISION_CALLED.get() or sid in self._provision_called_sessions:
            return self._mutation_result(
                ok=False,
                phase="intent_guard",
                operation="service.provision",
                message=(
                    "A provisioning tool was already called in this turn. "
                    "Do not call provisioning tools again in the same response."
                ),
                retryable=True,
                error="duplicate_provision_call",
            )
        _TURN_PROVISION_CALLED.set(True)
        self._provision_called_sessions.add(sid)
        return self.provision_service(
            service_type=service_type,
            src_device=src_device,
            src_endpoint=src_endpoint,
            dst_device=dst_device,
            dst_endpoint=dst_endpoint,
            name=name,
            capacity_gbps=capacity_gbps,
            mtu=mtu,
            context_uuid=context_uuid,
        )

    def _guarded_provision_optical_connectivity_service(
        self,
        src_device: str,
        dst_device: str,
        src_endpoint: str = "line",
        dst_endpoint: str = "line",
        name: Optional[str] = None,
        capacity_gbps: float = 100.0,
        bidirectional: bool = True,
        optical_band_width_ghz: int = 200,
        optical_type: str = "multi_granular",
        context_uuid: Optional[str] = None,
    ) -> str:
        """Wrapper enforcing that optical provisioning is called at most once per turn."""
        context_uuid = context_uuid or "admin"
        sid = _TURN_SESSION_ID.get()
        intent_guard = self._require_mutation_intent(
            actual_tool="tfs_provision_optical_connectivity_service",
            allowed_intents={"optical_connectivity_service"},
            operation="service.provision_optical_connectivity",
        )
        if intent_guard:
            return intent_guard
        if _TURN_PROVISION_CALLED.get() or sid in self._provision_called_sessions:
            return self._mutation_result(
                ok=False,
                phase="intent_guard",
                operation="service.provision_optical_connectivity",
                message=(
                    "A provisioning tool was already called in this turn. "
                    "Do not call provisioning tools again in the same response."
                ),
                retryable=True,
                error="duplicate_provision_call",
            )
        _TURN_PROVISION_CALLED.set(True)
        self._provision_called_sessions.add(sid)
        return self.provision_optical_connectivity_service(
            src_device=src_device,
            src_endpoint=src_endpoint,
            dst_device=dst_device,
            dst_endpoint=dst_endpoint,
            name=name,
            capacity_gbps=capacity_gbps,
            bidirectional=bidirectional,
            optical_band_width_ghz=optical_band_width_ghz,
            optical_type=optical_type,
            context_uuid=context_uuid,
        )

    def _service_delete_snapshot(self, context_uuid: str, service_uuid: str) -> Dict[str, Any]:
        """Fetch connection diagnostics for a service pre-delete.

        Service detail is NOT fetched here — the caller already has it from
        _find_service(), so fetching it again would be a duplicate API call.
        """
        snapshot: Dict[str, Any] = {}
        for key, tool in (
            ("connection_ids", "tfs_list_connection_ids"),
            ("connections", "tfs_list_connections"),
        ):
            result = self._call(tool, {
                "context_uuid": context_uuid,
                "service_uuid": service_uuid,
            })
            data = _structured(result)
            snapshot[key] = data if data else result.get("text", "")
        return snapshot

    def delete_service(
        self,
        name_or_uuid: str,
        context_uuid: Optional[str] = None,
    ) -> str:
        """Delete a non-slice TFS service by name or UUID (L2VPN/L3VPN/E2E/other)."""
        context_uuid = context_uuid or "admin"
        intent_guard = self._require_mutation_intent(
            actual_tool="tfs_delete_service",
            allowed_intents={"delete"},
            operation="service.delete",
        )
        if intent_guard:
            return intent_guard

        ctx_uuid = self._discover_context(context_uuid)
        svc = self._find_service(ctx_uuid, name_or_uuid)

        if svc is None:
            return self._mutation_result(
                ok=False,
                phase="resolve_target",
                operation="service.delete",
                message=f"Service '{name_or_uuid}' not found in context '{ctx_uuid}'.",
                retryable=False,
                error="service_not_found",
            )

        svc_uuid = svc.get("service_id", {}).get("service_uuid", {}).get("uuid", "")
        svc_name = svc.get("name", name_or_uuid)
        svc_type = svc.get("service_type", "?")
        svc_status = svc.get("service_status", {}).get("service_status", "?")
        snapshot = self._service_delete_snapshot(ctx_uuid, svc_uuid)

        del_res = self._call("tfs_delete_service", {
            "context_uuid": ctx_uuid,
            "service_uuid": svc_uuid,
        })
        del_data = _structured(del_res)
        if _is_error(del_data):
            # Some controller paths can return an error while still moving the
            # service to PENDING_REMOVAL asynchronously. Re-check state before
            # concluding hard failure.
            post_delete = self._find_service(ctx_uuid, svc_uuid)
            if post_delete is not None:
                post_status = str(
                    post_delete.get("service_status", {}).get("service_status", "UNKNOWN")
                )
                if post_status == "SERVICESTATUS_PENDING_REMOVAL":
                    return self._mutation_result(
                        ok=False,
                        phase="verify",
                        operation="service.delete",
                        service_uuid=svc_uuid,
                        observed_status="PENDING_REMOVAL",
                        message=(
                            "Delete request was accepted by controller but service is still pending removal."
                        ),
                        retryable=True,
                        error="delete_pending",
                        details={
                            "context_uuid": ctx_uuid,
                            "service": {"name": svc_name, "uuid": svc_uuid, "type": svc_type},
                            "controller_delete_error": del_data.get("error", del_res.get("text", "unknown")),
                            "pre_delete_diagnostics": {
                                "connection_ids": snapshot.get("connection_ids"),
                                "connections": snapshot.get("connections"),
                            },
                        },
                    )
            return self._mutation_result(
                ok=False,
                phase="apply",
                operation="service.delete",
                service_uuid=svc_uuid,
                observed_status=str(svc_status),
                message="Delete request failed at controller.",
                retryable=True,
                error=del_data.get("error", del_res.get("text", "unknown")),
                details={
                    "context_uuid": ctx_uuid,
                    "service": {
                        "name": svc_name,
                        "uuid": svc_uuid,
                        "type": svc_type,
                        "status": svc_status,
                    },
                    "pre_delete_diagnostics": {
                        "connection_ids": snapshot.get("connection_ids"),
                        "connections": snapshot.get("connections"),
                    },
                },
            )

        # Confirm
        if self._find_service(ctx_uuid, svc_uuid) is None:
            return self._mutation_result(
                ok=True,
                phase="verified",
                operation="service.delete",
                service_uuid=svc_uuid,
                message=f"Service '{svc_name}' deleted.",
                retryable=False,
                observed_status="DELETED",
                details={
                    "context_uuid": ctx_uuid,
                    "service": {"name": svc_name, "uuid": svc_uuid},
                    "delete_response": del_data,
                    "pre_delete_diagnostics": {
                        "connection_ids": snapshot.get("connection_ids"),
                        "connections": snapshot.get("connections"),
                    },
                },
            )
        return self._mutation_result(
            ok=False,
            phase="verify",
            operation="service.delete",
            service_uuid=svc_uuid,
            observed_status="PENDING_REMOVAL",
            message="Delete was issued, but the service still appears in the service list.",
            retryable=True,
            error="delete_pending",
            details={
                "context_uuid": ctx_uuid,
                "service": {"name": svc_name, "uuid": svc_uuid},
                "delete_response": del_data,
                "pre_delete_diagnostics": {
                    "connection_ids": snapshot.get("connection_ids"),
                    "connections": snapshot.get("connections"),
                },
            },
        )

    def delete_optical_connectivity_service(
        self,
        name_or_uuid: str,
        context_uuid: Optional[str] = None,
    ) -> str:
        """Delete a TFS optical connectivity service by name or UUID.

        This is a specialized delete tool for OPTICAL_CONNECTIVITY services.
        Generic packet-service deletions should use tfs_delete_service.
        """
        context_uuid = context_uuid or "admin"
        intent_guard = self._require_mutation_intent(
            actual_tool="tfs_service_delete_optical_connectivity",
            allowed_intents={"delete"},
            operation="service.delete_optical_connectivity",
        )
        if intent_guard:
            return intent_guard

        ctx_uuid = self._discover_context(context_uuid)
        svc = self._find_service(ctx_uuid, name_or_uuid)
        if svc is None:
            return self._mutation_result(
                ok=False,
                phase="resolve_target",
                operation="service.delete_optical_connectivity",
                message=f"Service '{name_or_uuid}' not found in context '{ctx_uuid}'.",
                retryable=False,
                error="service_not_found",
            )

        svc_uuid = svc.get("service_id", {}).get("service_uuid", {}).get("uuid", "")
        svc_name = svc.get("name", name_or_uuid)
        svc_type = str(svc.get("service_type", ""))
        if svc_type != "SERVICETYPE_OPTICAL_CONNECTIVITY":
            return self._mutation_result(
                ok=False,
                phase="validate_args",
                operation="service.delete_optical_connectivity",
                service_uuid=svc_uuid or None,
                message=(
                    f"Service '{svc_name}' is type '{svc_type}', not OPTICAL_CONNECTIVITY."
                ),
                retryable=False,
                error="wrong_service_type",
                details={
                    "hint": (
                        "Use tfs_delete_service for L2VPN/L3VPN/E2E or other non-optical service types."
                    ),
                },
            )

        # Reuse the generic delete lifecycle while preserving intent/tool-family coherence.
        token = self._with_caller_tool("tfs_delete_service")
        try:
            return self.delete_service(name_or_uuid=svc_uuid or svc_name, context_uuid=ctx_uuid)
        finally:
            self._reset_caller_tool(token)

    def service_status(self, name_or_uuid: str, context_uuid: Optional[str] = None) -> str:
        """Get current status of a TFS service by name or UUID."""
        context_uuid = context_uuid or "admin"
        ctx_uuid = self._discover_context(context_uuid)
        svc = self._find_service(ctx_uuid, name_or_uuid)

        if svc is None:
            return f"Service '{name_or_uuid}' not found in context '{ctx_uuid}'."

        svc_uuid = svc.get("service_id", {}).get("service_uuid", {}).get("uuid", "")
        svc_name = svc.get("name", name_or_uuid)
        svc_type = svc.get("service_type", "?")
        status = svc.get("service_status", {}).get("service_status", "?")

        ep_summaries = []
        for ep in svc.get("service_endpoint_ids", []):
            dev_uuid = ep.get("device_id", {}).get("device_uuid", {}).get("uuid", "?")
            ep_uuid = ep.get("endpoint_uuid", {}).get("uuid", "?")
            ep_summaries.append(f"device={dev_uuid} endpoint={ep_uuid}")

        return json.dumps({
            "name": svc_name,
            "uuid": svc_uuid,
            "type": svc_type,
            "status": status,
            "endpoints": ep_summaries,
        })

    def create_optical_slice(
        self,
        slice_id: str,
        slice: Dict[str, Any],
    ) -> str:
        """Create an optical-slice RESTCONF request.

        This is a direct workflow wrapper around:
          tfs_write(resource="optical_slice", operation="create", ...)
        """
        token = self._with_caller_tool("tfs_create_optical_slice")
        try:
            return self.write_resource(
                resource="optical_slice",
                operation="create",
                payload=slice,
                identifier=slice_id,
            )
        finally:
            self._reset_caller_tool(token)

    def create_optical_slice_from_endpoints(
        self,
        src_device: str,
        dst_device: str,
        src_endpoint: str = "line",
        dst_endpoint: str = "line",
        capacity_gbps: float = 10.0,
        slice_id: Optional[str] = None,
        context_uuid: Optional[str] = None,
    ) -> str:
        """Create optical-slice request from endpoint names.

        This helper builds a minimal TFS optical-slice payload so operators can
        use endpoint-based prompts without handcrafting RESTCONF JSON.
        """
        context_uuid = context_uuid or "admin"
        operation_name = "optical_slice.create_from_endpoints"
        ctx_uuid = self._discover_context(context_uuid)
        topo_uuid = self._discover_topology(ctx_uuid)
        devices = self._get_devices(ctx_uuid, topo_uuid)
        if not devices:
            return self._mutation_result(
                ok=False,
                phase="resolve_target",
                operation=operation_name,
                message=(
                    f"No devices found in topology '{topo_uuid}' of context '{ctx_uuid}'."
                ),
                retryable=True,
                error="inventory_empty",
                details={"hint": "Check TFS controller reachability and topology content."},
            )

        src_ep_id = self._resolve_endpoint(devices, ctx_uuid, topo_uuid, src_device, src_endpoint)
        if src_ep_id is None:
            names = [d.get("name") for d in devices]
            return self._mutation_result(
                ok=False,
                phase="resolve_target",
                operation=operation_name,
                message=f"Source endpoint '{src_device}:{src_endpoint}' not found.",
                retryable=True,
                error="source_endpoint_not_found",
                details={"known_devices": names, "hint": "Call tfs_inventory and retry with exact endpoint names."},
            )

        dst_ep_id = self._resolve_endpoint(devices, ctx_uuid, topo_uuid, dst_device, dst_endpoint)
        if dst_ep_id is None:
            names = [d.get("name") for d in devices]
            return self._mutation_result(
                ok=False,
                phase="resolve_target",
                operation=operation_name,
                message=f"Destination endpoint '{dst_device}:{dst_endpoint}' not found.",
                retryable=True,
                error="destination_endpoint_not_found",
                details={"known_devices": names, "hint": "Call tfs_inventory and retry with exact endpoint names."},
            )

        src_ep_uuid = src_ep_id.get("endpoint_uuid", {}).get("uuid", "")
        dst_ep_uuid = dst_ep_id.get("endpoint_uuid", {}).get("uuid", "")
        if not src_ep_uuid or not dst_ep_uuid:
            return self._mutation_result(
                ok=False,
                phase="resolve_target",
                operation=operation_name,
                message="Resolved endpoint UUID is empty.",
                retryable=True,
                error="empty_endpoint_uuid",
            )

        chosen_slice_id = slice_id or f"optical-slice-{src_device}-{dst_device}".lower()
        payload = {
            "tapi-common:context": {
                "service-interface-point": [
                    {
                        "uuid": src_ep_uuid,
                        "name": [{"value-name": "node-edge-point", "value": f"{src_device}:{src_endpoint}"}],
                    },
                    {
                        "uuid": dst_ep_uuid,
                        "name": [{"value-name": "node-edge-point", "value": f"{dst_device}:{dst_endpoint}"}],
                    },
                ],
                "service-level-specification": {
                    "capacity": {"total-size": {"value": float(capacity_gbps), "unit": "Gbps"}}
                },
            }
        }

        return self.create_optical_slice(slice_id=chosen_slice_id, slice=payload)

    def delete_optical_slice(self, slice_id: str) -> str:
        """Delete an optical-slice RESTCONF request by slice_id."""
        token = self._with_caller_tool("tfs_delete_optical_slice")
        try:
            return self.write_resource(
                resource="optical_slice",
                operation="delete",
                identifier=slice_id,
            )
        finally:
            self._reset_caller_tool(token)

    # ------------------------------------------------------------------ #
    # LangChain tool registration                                          #
    # ------------------------------------------------------------------ #

    def build_langchain_tools(self) -> List[StructuredTool]:
        return [
            StructuredTool.from_function(
                func=self.capabilities,
                name="tfs_capabilities",
                description=(
                    "Show all TFS operations available through workflow_tools, aligned with "
                    "tfs_context.proto, TFS NBI endpoints, and MCP tool definitions. Use "
                    "detail='full' for endpoint/tool/proto mapping and proto gaps not exposed by MCP."
                ),
            ),
            StructuredTool.from_function(
                func=self.read_resource,
                name="tfs_read",
                description=(
                    "Generic read-only TFS operation. Supports resources: health, context, topology, "
                    "service, slice, device, link, connection, policyrule, dscm_oc. Operations include "
                    "ids, list, get, details, dummy, check depending on resource. Call tfs_capabilities "
                    "when unsure."
                ),
            ),
            StructuredTool.from_function(
                func=self.write_resource,
                name="tfs_write",
                description=(
                    "Generic TFS write/delete for optical_slice, media_channel, dscm_oc, and "
                    "low-level context/topology/link/device edits. Requires exact proto-shaped "
                    "payloads for set/update/create — only use when no specific workflow tool "
                    "exists. For services use tfs_provision_service or tfs_delete_service instead. "
                    "Executes immediately — no confirmation required."
                ),
            ),
            StructuredTool.from_function(
                func=self.create_optical_slice,
                name="tfs_create_optical_slice",
                description=(
                    "Create an optical slice using the RESTCONF optical-slice API. "
                    "This creates/requests a TAPI connectivity service through "
                    "/restconf/optical-slice/v1/service/<slice_id>; it is not a TFS "
                    "OPTICAL_CONNECTIVITY service. Required: slice_id and slice payload. "
                    "Use only for requests that explicitly ask for 'optical slice'. "
                    "Do not use service provisioning tools for optical-slice intents."
                ),
            ),
            StructuredTool.from_function(
                func=self.create_optical_slice,
                name="tfs_restconf_create_optical_slice",
                description=(
                    "Explicit RESTCONF optical-slice creator. Use for optical slice requests, "
                    "not for optical connectivity service requests. Calls the TFS RESTCONF "
                    "optical-slice endpoint and requires slice_id plus optical-slice payload."
                ),
            ),
            StructuredTool.from_function(
                func=self.create_optical_slice_from_endpoints,
                name="tfs_create_optical_slice_from_endpoints",
                description=(
                    "Create a RESTCONF optical slice by endpoint names (src_device, "
                    "src_endpoint, dst_device, dst_endpoint). The tool discovers endpoint "
                    "UUIDs and builds a minimal optical-slice payload for TFS RESTCONF. "
                    "Use when the user says 'create optical slice from A to B'. "
                    "Do not use for optical connectivity service requests."
                ),
            ),
            StructuredTool.from_function(
                func=self.create_optical_slice_from_endpoints,
                name="tfs_restconf_create_optical_slice_from_endpoints",
                description=(
                    "Explicit endpoint-based RESTCONF optical-slice creator. Use for optical "
                    "slice intents expressed as endpoint names. Do not use for TFS "
                    "OPTICAL_CONNECTIVITY service provisioning."
                ),
            ),
            StructuredTool.from_function(
                func=self.delete_optical_slice,
                name="tfs_delete_optical_slice",
                description=(
                    "Delete an optical slice by slice_id (RESTCONF optical-slice API). "
                    "Use this tool for user requests that explicitly ask to delete/remove an optical slice."
                ),
            ),
            StructuredTool.from_function(
                func=self.delete_optical_connectivity_service,
                name="tfs_service_delete_optical_connectivity",
                description=(
                    "Delete a TFS service of type SERVICETYPE_OPTICAL_CONNECTIVITY by name or UUID. "
                    "Use this for optical connectivity service teardown. "
                    "Do not use this for optical-slice RESTCONF deletion; use tfs_delete_optical_slice."
                ),
            ),
            StructuredTool.from_function(
                func=self.delete_optical_connectivity_service,
                name="tfs_delete_optical_connectivity_service",
                description=(
                    "Alias for deleting OPTICAL_CONNECTIVITY services by name/UUID. "
                    "Preferred for requests such as 'delete optical connectivity service ...'."
                ),
            ),
            StructuredTool.from_function(
                func=self.inventory,
                name="tfs_inventory",
                description=(
                    "Fetch the current TFS inventory: context UUID, topology UUID, "
                    "all devices with their names, types, endpoint names and UUIDs, "
                    "and all existing services with their status."
                ),
            ),
            StructuredTool.from_function(
                func=self._guarded_provision_optical_connectivity_service,
                name="tfs_provision_optical_connectivity_service",
                description=(
                    "Provision a TFS service of type SERVICETYPE_OPTICAL_CONNECTIVITY between "
                    "two optical endpoints. This uses /tfs-api service create/update, not the "
                    "RESTCONF optical-slice API. Required: src_device, dst_device "
                    "(exact names from tfs_inventory). Optional endpoints: src_endpoint and "
                    "dst_endpoint default to 'line' when omitted. Endpoints must be optical "
                    "endpoint types; for transponders this is typically 'line', not 'client'. "
                    "Optional: name, capacity_gbps (default 100), bidirectional "
                    "(default true), optical_band_width_ghz (default 200), optical_type "
                    "(default 'multi_granular'). Do not use for optical slice requests."
                ),
            ),
            StructuredTool.from_function(
                func=self._guarded_provision_optical_connectivity_service,
                name="tfs_service_create_optical_connectivity",
                description=(
                    "Explicit TFS OPTICAL_CONNECTIVITY service creator. Use for user requests "
                    "that say optical connectivity service, wavelength service, or WDM service. "
                    "Do not use for RESTCONF optical slice requests."
                ),
            ),
            StructuredTool.from_function(
                func=self._guarded_provision_service,
                name="tfs_provision_service",
                description=(
                    "Provision a new TFS service end-to-end between two devices. "
                    "Handles UUID discovery, proto construction, and polls until ACTIVE. "
                    "Required: service_type (l3vpn|l2vpn|e2e), "
                    "src_device, src_endpoint, dst_device, dst_endpoint "
                    "(use exact names from tfs_inventory). "
                    "name is optional — auto-generated from device names if omitted. "
                    "capacity_gbps is optional. For l3vpn, mtu defaults to 1512. "
                    "For optical connectivity, prefer tfs_provision_optical_connectivity_service."
                ),
            ),
            StructuredTool.from_function(
                func=self.delete_service,
                name="tfs_delete_service",
                description=(
                    "Delete a TFS service by name or UUID. Use this for packet and generic "
                    "service deletions including L2VPN, L3VPN, and E2E services. "
                    "For OPTICAL_CONNECTIVITY service deletions, prefer "
                    "tfs_service_delete_optical_connectivity. For optical-slice RESTCONF "
                    "deletions, use tfs_delete_optical_slice."
                ),
            ),
            StructuredTool.from_function(
                func=self.service_status,
                name="tfs_service_status",
                description=(
                    "Check the current status of a TFS service by name or UUID. "
                    "Returns name, UUID, type, provisioning status, and endpoints."
                ),
            ),
        ]
