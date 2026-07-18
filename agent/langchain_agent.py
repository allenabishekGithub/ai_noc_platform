# File: langchain_agent.py
"""
LangChain reasoning agent.

The LLM decides intent, ambiguity handling, and which high-level tool to call.
The high-level tools execute strict TFS lifecycles so raw controller JSON is
not left to model improvisation. There is no keyword routing or intent
classification in Python.

Tool categories:
  - TFS workflows    : inventory, provision, delete, status
  - Local knowledge  : rag_search, topology_lookup
  - Agent state      : update_working_memory, ask_user
  - Raw MCP fallback : optional, enabled with AGENT_EXPOSE_RAW_MCP_TOOLS=true
"""
import json
import logging
import os
from contextvars import ContextVar
from typing import Any, Dict, List, Optional

from langchain.agents import AgentExecutor, create_tool_calling_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.tools import StructuredTool

from rag.rag_manager import RAGManager
from mcp_integration.mcp_client import MCPClient
from agent.tfs_workflow_tools import TFSWorkflowTools
from memory.session_memory_manager import SessionMemoryManager
from utils.event_timing import timed_event

LOGGER = logging.getLogger(__name__)

# Sentinel returned by ask_user so we can detect it in post-processing.
_ASK_USER_SENTINEL = "__ASK_USER__:"
_ACTIVE_SESSION_ID: ContextVar[str] = ContextVar("active_session_id", default="default")
# Guard: ask_user must never be called more than once per agent turn.
_ASK_USER_CALLED: ContextVar[bool] = ContextVar("ask_user_called", default=False)

SYSTEM_PROMPT = """You are an AI agent that manages a TeraFlowSDN (TFS) network controller.

## Tools
- tfs_inventory         — discover all devices, endpoints, and services in one call.
- tfs_provision_service — create a network service (handles all UUID resolution internally).
- tfs_provision_optical_connectivity_service — create an optical connectivity service using TFS optical custom constraints.
- tfs_delete_service    — delete packet/generic services by name or UUID (L2VPN/L3VPN/E2E and other non-optical service types).
- tfs_service_delete_optical_connectivity — delete an OPTICAL_CONNECTIVITY service by name or UUID.
- tfs_service_status    — check a service's current status by name or UUID.
- tfs_capabilities      — list all available TFS controller operations.
- tfs_read              — read any TFS resource (context, topology, device, service, link, connection).
- tfs_write             — write or delete any TFS resource.
- tfs_create_optical_slice — create an optical-slice RESTCONF request (requires slice_id + slice payload).
- tfs_create_optical_slice_from_endpoints — create optical slice intent from endpoint names (agent builds payload).
- tfs_restconf_create_optical_slice — explicit alias for RESTCONF optical-slice creation.
- tfs_service_create_optical_connectivity — explicit alias for TFS OPTICAL_CONNECTIVITY service provisioning.
- tfs_delete_optical_slice — delete an optical-slice RESTCONF request by slice_id.
- a2a_list_peers        — list configured peer agent base URLs from A2A_PEERS.
- a2a_discover_peer     — fetch a peer Agent Card and optional live state.
- a2a_provision_peer_packet_service — ask a peer agent to create an L2VPN/L3VPN service in its controller.
- a2a_provision_cross_domain_packet_service — orchestrate cross-domain L2VPN/L3VPN as peer segment first, then local NET-EXT segment.
- a2a_send_peer_message — send a generic A2A message to a peer agent for non-packet or unsupported cross-domain requests.
- rag_search            — search TFS documentation and network knowledge.
- topology_lookup       — query the local network topology graph.
- declare_tfs_intent    — state the selected TFS intent before mutating the controller.
- update_working_memory — save key facts for use in later turns.
- ask_user              — ask the operator when genuinely missing required information.

## Principles
- Discover before acting: call tfs_inventory to learn device names and current state before making changes.
- Use exact names: pass device and endpoint names exactly as returned by tfs_inventory — never guess.
- Additive operations (service provisioning) are safe to perform as soon as you have the necessary facts.
  Do not ask the user for prior approval — their request is the intent.
- Execute destructive operations (delete, write) immediately when the user requests them — do not ask for confirmation.
- For delete/remove requests, never ask confirmation questions like "are you sure?" — execute deletion tools directly and report the outcome.
- Tool evidence is mandatory for controller mutations. For any request to create, update, delete, allocate, release, or configure controller resources,
  you must call at least one relevant tfs_* tool in the current turn before finalizing.
- Tool calls must be executed through the tool interface. Do not print JSON-looking tool-call text as a substitute for an actual tool call.
- Never claim that something was created, deleted, updated, allocated, or released unless the tool observation in this turn indicates success.
- If a tool call fails or is skipped, report that explicitly and ask the user whether to retry.
- Tool error recovery: if a tool reports missing required argument(s), ask the user for those exact missing fields.
  Do not repeat the same tool call with the same invalid arguments.
- Tool error recovery: if a tool reports unsupported operation, switch to one of the supported operations from
  tfs_capabilities/tfs_write feedback. Do not retry the same unsupported operation.
- Interpret "optical slice" literally: use tfs_create_optical_slice / tfs_delete_optical_slice for slice intents.
  For "create optical slice from <A> to <B>" user prompts, prefer tfs_create_optical_slice_from_endpoints.
  If those tools are unavailable, use tfs_write with resource="optical_slice".
  Use tfs_provision_optical_connectivity_service for optical connectivity service create intents,
  and tfs_service_delete_optical_connectivity for optical connectivity service delete intents.
  Use tfs_delete_service for L2VPN/L3VPN/E2E and other non-optical service deletions.
  Do not use optical connectivity service tools for optical slice requests, and do not use optical-slice RESTCONF tools for optical connectivity service requests.
- Before controller mutations, call declare_tfs_intent once to state whether the request is optical_slice_restconf,
  optical_connectivity_service, packet_service, delete, or generic_write and name the tool you plan to use.
- Device names are case-insensitive: if the user says "dc1" and the inventory shows "DC1", use "DC1" exactly as returned by tfs_inventory. Never ask the user to confirm names that already appear in the inventory.
- For packet L2VPN/L3VPN services involving datacenter devices, prefer the datacenter access endpoint `eth0` when inventory exposes both `eth0` and `int`; do not choose `int` for the service attachment unless `eth0` is unavailable.
- For packet service requests whose endpoints span two controller domains, prefer a2a_provision_cross_domain_packet_service. It provisions the peer segment first and then the local NET-EXT segment. If using individual tools, call a2a_provision_peer_packet_service before tfs_provision_service. The peer segment and local segment must use the same packet service type as the user's request: L2VPN stays L2VPN on both sides, and L3VPN stays L3VPN on both sides. If either local or peer provisioning fails, report that the end-to-end service is not possible and include the failing side.
- Cross-domain missing-node rule: if any requested service endpoint or node is absent from local tfs_inventory, but NET-EXT exists locally and A2A_PEERS is configured, do not conclude the node is invalid and do not ask the user to add/rename it. Treat the absent node as potentially belonging to another SDN controller domain. Discover/contact the peer via A2A and let that peer agent decide whether the node exists in its own controller. For L2VPN/L3VPN use a2a_provision_peer_packet_service; for other service types use a2a_send_peer_message unless a more specific A2A skill exists.
- A2A privacy rule: do not require or expose the peer controller's internal topology in local reasoning. The local agent only needs the peer Agent Card, peer reachability, the requested remote endpoint name, and whether the peer accepted or rejected the structured A2A task.
- A2A failure rule: if a cross-domain request requires a peer and a2a_discover_peer or peer provisioning times out/fails, do not provision the local segment by itself. Report that the end-to-end service was not established because the peer agent is unreachable or rejected the task.
- Cross-domain packet segmentation rule: never ask a peer agent to create the full end-to-end service path such as DC1 to DC6. Prefer a2a_provision_cross_domain_packet_service for the whole operation. If using separate tools, ask the peer only for its local handoff segment. For a local DC1 to remote DC6 request, call a2a_provision_peer_packet_service with src_device=NET-EXT, src_endpoint=access0, dst_device=DC6, dst_endpoint=eth0. After that succeeds, provision the local segment DC1/eth0 to NET-EXT/access0. Never provision DC1/eth0 to NET-EXT/access0 before the peer segment succeeds.
- Use ask_user only when a required fact genuinely cannot be found by calling a tool. Never call ask_user if the answer is already in the tfs_inventory result, and never use ask_user as a confirmation gate for requested create/update/delete operations.
- Always report what you did and what the outcome was.
"""


def _build_llm(provider: str, model: str):
    """Instantiate the chat LLM for the requested provider."""
    provider = (provider or "openai").lower()
    temperature = float(os.environ.get("LLM_TEMPERATURE", "0.2"))

    if provider == "openai":
        from langchain_openai import ChatOpenAI
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY must be set for the openai provider.")
        LOGGER.info("LLM provider: openai | model: %s", model)
        return ChatOpenAI(
            model=model, temperature=temperature, api_key=api_key,
            base_url=os.environ.get("OPENAI_BASE_URL"),
        )

    if provider == "openwebui":
        from langchain_openai import ChatOpenAI
        from a2a.openwebui_auth import get_token_from_env
        base_url = os.environ.get("OPENWEBUI_BASE_URL", "").rstrip("/")
        email = os.environ.get("OPENWEBUI_EMAIL", "")
        password = os.environ.get("OPENWEBUI_PASSWORD", "")
        missing = [k for k, v in {
            "OPENWEBUI_BASE_URL": base_url,
            "OPENWEBUI_EMAIL": email,
            "OPENWEBUI_PASSWORD": password,
        }.items() if not v]
        if missing:
            raise RuntimeError(
                f"Missing required env vars for openwebui provider: {', '.join(missing)}"
            )
        LOGGER.info("LLM provider: openwebui | base: %s | user: %s | model: %s",
                    base_url, email, model)
        token = get_token_from_env()
        return ChatOpenAI(
            model=model, temperature=temperature, api_key=token,
            base_url=f"{base_url}/api",
        )

    if provider == "ollama":
        from langchain_ollama import ChatOllama
        ollama_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
        num_ctx = int(os.environ.get("OLLAMA_NUM_CTX", "8192"))
        LOGGER.info("LLM provider: ollama | base: %s | model: %s | num_ctx: %d",
                    ollama_url, model, num_ctx)
        return ChatOllama(
            model=model, base_url=ollama_url,
            temperature=temperature, num_ctx=num_ctx,
        )

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY must be set for the anthropic provider.")
        LOGGER.info("LLM provider: anthropic | model: %s", model)
        return ChatAnthropic(model=model, temperature=temperature, api_key=api_key)

    raise ValueError(f"Unsupported LLM_PROVIDER: {provider!r}")


class LangChainAgent:
    def __init__(
        self,
        llm_provider: str,
        llm_model: str,
        rag_manager: RAGManager,
        mcp_client: MCPClient,
        topology_store=None,
        controller_cache=None,
        session_memory: Optional[SessionMemoryManager] = None,
    ):
        self.rag = rag_manager
        self.mcp = mcp_client
        self.topology = topology_store
        self.controller_cache = controller_cache
        self.session_memory = session_memory or SessionMemoryManager()

        self.llm = _build_llm(llm_provider, llm_model)
        self.workflow = TFSWorkflowTools(self.mcp)
        self.tools = self._build_tools()

        self._prompt = ChatPromptTemplate.from_messages([
            ("system", SYSTEM_PROMPT),
            MessagesPlaceholder("chat_history", optional=True),
            ("human", "{input}"),
            MessagesPlaceholder("agent_scratchpad"),
        ])

        self._max_iterations = int(os.environ.get("AGENT_MAX_ITERATIONS", "20"))
        self._verbose = os.environ.get("AGENT_VERBOSE", "false").lower() == "true"

    # ------------------------------------------------------------------ #
    # Tool construction                                                    #
    # ------------------------------------------------------------------ #
    def _build_tools(self) -> List[StructuredTool]:
        tools: List[StructuredTool] = []

        # --- RAG search --------------------------------------------------
        def rag_search(query: str, k: int = 4) -> str:
            """Search ingested documents for TFS docs, topology specs, and policies."""
            docs = self.rag.retrieve(query, k=k)
            return self.rag.format_context(docs) or "No relevant documents found."

        tools.append(StructuredTool.from_function(
            func=rag_search,
            name="rag_search",
            description=(
                "Search the local knowledge base (ingested TFS documentation, topology specs, "
                "policies) for relevant context."
            ),
        ))

        # --- Local topology graph ----------------------------------------
        if self.topology is not None:
            def topology_lookup(
                query: str = "summary",
                node: Optional[str] = None,
                src: Optional[str] = None,
                dst: Optional[str] = None,
            ) -> str:
                """Query the locally parsed network topology graph.
                query values: summary | nodes | links | border_links | neighbors | path
                node: required for 'neighbors'
                src, dst: required for 'path'
                If the local graph is empty, this tool falls back to controller-backed
                topology via tfs_inventory + tfs_read(topology.details).
                """
                def controller_fallback() -> Dict[str, Any]:
                    inv_raw = self.workflow.inventory()
                    try:
                        inv = json.loads(inv_raw)
                    except Exception:
                        return {
                            "local_topology_empty": True,
                            "controller_topology_unavailable": True,
                            "hint": "Controller inventory fetch failed.",
                        }
                    if not isinstance(inv, dict) or inv.get("error"):
                        return {
                            "local_topology_empty": True,
                            "controller_topology_unavailable": True,
                            "hint": "Controller inventory fetch returned no usable topology context.",
                        }

                    ctx_uuid = inv.get("context_uuid", "admin")
                    topo_uuid = inv.get("topology_uuid", "admin")
                    details_raw = self.workflow.read_resource(
                        resource="topology",
                        operation="details",
                        context_uuid=ctx_uuid,
                        topology_uuid=topo_uuid,
                    )
                    try:
                        details_env = json.loads(details_raw)
                    except Exception:
                        details_env = {}
                    details = details_env.get("result", {}) if isinstance(details_env, dict) else {}
                    devices = details.get("devices", []) if isinstance(details, dict) else []
                    links = details.get("links", []) if isinstance(details, dict) else []
                    return {
                        "source": "controller",
                        "context_uuid": ctx_uuid,
                        "topology_uuid": topo_uuid,
                        "device_count": len(devices),
                        "link_count": len(links),
                        "devices": devices,
                        "links": links,
                    }

                local = self.topology.get() or {}
                q = (query or "").lower()
                if q == "summary":
                    summary = self.topology.summary()
                    if not summary:
                        return json.dumps(controller_fallback())
                    return json.dumps(summary)
                if q == "nodes":
                    if local:
                        return json.dumps({"source": "local", "nodes": local.get("nodes", [])})
                    fb = controller_fallback()
                    return json.dumps({"source": "controller", "nodes": fb.get("devices", [])})
                if q == "links":
                    if local:
                        return json.dumps({"source": "local", "links": local.get("links", [])})
                    fb = controller_fallback()
                    return json.dumps({"source": "controller", "links": fb.get("links", [])})
                if q == "border_links":
                    if local:
                        return json.dumps({"source": "local", "border_links": self.topology.border_links()})
                    fb = controller_fallback()
                    links = fb.get("links", [])
                    border = []
                    for link in links if isinstance(links, list) else []:
                        link_id = (link.get("link_id", {}).get("link_uuid", {}).get("uuid", "")
                                   if isinstance(link, dict) else "")
                        if link_id:
                            border.append(link_id)
                    return json.dumps({"source": "controller", "border_links": border})
                if q == "neighbors" and node:
                    if local:
                        return json.dumps({"source": "local", "neighbors": self.topology.neighbors(node)})
                    fb = controller_fallback()
                    links = fb.get("links", [])
                    neighbors: set[str] = set()
                    for link in links if isinstance(links, list) else []:
                        eps = link.get("link_endpoint_ids", []) if isinstance(link, dict) else []
                        if len(eps) < 2:
                            continue
                        a = eps[0].get("device_id", {}).get("device_uuid", {}).get("uuid", "")
                        b = eps[1].get("device_id", {}).get("device_uuid", {}).get("uuid", "")
                        if node == a and b:
                            neighbors.add(b)
                        if node == b and a:
                            neighbors.add(a)
                    return json.dumps({"source": "controller", "neighbors": sorted(neighbors)})
                if q == "path" and src and dst:
                    if local:
                        return json.dumps({"source": "local", "path": self.topology.shortest_path(src, dst)})
                    return json.dumps({
                        "source": "controller",
                        "path": [],
                        "hint": (
                            "Controller fallback does not compute shortest path locally. "
                            "Use local topology upload for path queries."
                        ),
                    })
                return json.dumps(local) if local else json.dumps(controller_fallback())

            tools.append(StructuredTool.from_function(
                func=topology_lookup,
                name="topology_lookup",
                description=(
                    "Query the local topology graph (nodes, links, paths, border links). "
                    "If local topology is empty, this tool automatically falls back to "
                    "controller-backed topology discovery."
                ),
            ))


        # --- A2A peer tools ---------------------------------------------
        def _configured_peers() -> List[str]:
            peers_env = os.environ.get("A2A_PEERS", "")
            return [peer.strip().rstrip("/") for peer in peers_env.split(",") if peer.strip()]

        def _resolve_peer_url(peer_url: Optional[str] = None) -> str:
            if peer_url:
                return peer_url.rstrip("/")
            peers = _configured_peers()
            if len(peers) == 1:
                return peers[0]
            if not peers:
                raise RuntimeError(
                    "No peer URL provided and A2A_PEERS is empty. Configure A2A_PEERS or pass peer_url."
                )
            raise RuntimeError(
                "Multiple peers are configured. Pass peer_url explicitly. Configured peers: "
                + ", ".join(peers)
            )

        def a2a_list_peers() -> str:
            """List configured peer agent base URLs from A2A_PEERS."""
            return json.dumps({"peers": _configured_peers()}, indent=2)

        tools.append(StructuredTool.from_function(
            func=a2a_list_peers,
            name="a2a_list_peers",
            description=(
                "List configured peer agent base URLs from A2A_PEERS. Use before "
                "cross-domain packet service negotiation when the peer URL is unknown."
            ),
        ))

        def a2a_discover_peer(peer_url: Optional[str] = None) -> str:
            """Fetch a peer Agent Card and optional live A2A state."""
            import httpx
            base = _resolve_peer_url(peer_url)
            card = None
            errors: List[str] = []
            for suffix in ("/.well-known/agent-card.json", "/.well-known/agent.json"):
                try:
                    with httpx.Client(timeout=15.0) as client:
                        response = client.get(f"{base}{suffix}")
                    if response.status_code == 200:
                        card = response.json()
                        break
                    errors.append(f"{suffix}: HTTP {response.status_code}")
                except Exception as exc:
                    errors.append(f"{suffix}: {exc}")
            if card is None:
                return json.dumps({"ok": False, "peer_url": base, "errors": errors}, indent=2)
            state = None
            try:
                with httpx.Client(timeout=15.0) as client:
                    response = client.get(f"{base}/a2a/state")
                if response.status_code == 200:
                    state = response.json()
            except Exception:
                state = None
            return json.dumps({"ok": True, "peer_url": base, "agent_card": card, "state": state}, indent=2)

        tools.append(StructuredTool.from_function(
            func=a2a_discover_peer,
            name="a2a_discover_peer",
            description=(
                "Discover a peer agent by fetching its Agent Card and optional /a2a/state. "
                "Pass peer_url, or omit it only when A2A_PEERS contains exactly one peer."
            ),
        ))

        def a2a_send_peer_message(message: str, peer_url: Optional[str] = None) -> str:
            """Send a generic A2A message to a peer agent."""
            import httpx
            base = _resolve_peer_url(peer_url)
            payload = {
                "sender": f"agentic-module-session-{_ACTIVE_SESSION_ID.get()}",
                "message": message,
                "session_id": _ACTIVE_SESSION_ID.get(),
                "metadata": {"source": "cross-domain-a2a"},
            }
            try:
                with httpx.Client(timeout=120.0) as client:
                    response = client.post(f"{base}/a2a/message", json=payload)
                body = response.json() if response.content else {}
                return json.dumps({
                    "ok": 200 <= response.status_code < 300 and bool(body.get("success", True)),
                    "peer_url": base,
                    "http_status": response.status_code,
                    "response": body,
                }, indent=2)
            except Exception as exc:
                return json.dumps({"ok": False, "peer_url": base, "error": str(exc)}, indent=2)

        tools.append(StructuredTool.from_function(
            func=a2a_send_peer_message,
            name="a2a_send_peer_message",
            description=(
                "Send a generic A2A message to a peer agent. Use when a requested node is not "
                "in local inventory and the service type is not covered by a structured A2A tool. "
                "Do not include or require peer internal topology; ask the peer to check/provision "
                "within its own controller domain."
            ),
        ))

        def _a2a_provision_peer_packet_service_impl(
            service_type: str,
            src_device: str,
            src_endpoint: str,
            dst_device: str,
            dst_endpoint: str,
            peer_url: Optional[str] = None,
            name: Optional[str] = None,
            capacity_gbps: Optional[float] = None,
            mtu: int = 1512,
            context_uuid: Optional[str] = None,
        ) -> Dict[str, Any]:
            """Ask a peer agent to create an L2VPN/L3VPN service in its local controller."""
            import httpx
            context_uuid = context_uuid or "admin"
            base = _resolve_peer_url(peer_url)
            stype = (service_type or "").lower().replace("-", "_")
            if stype in {"l2", "l2nm", "l2vpn"}:
                skill_id = "create-l2vpn-service"
                stype = "l2vpn"
            elif stype in {"l3", "l3nm", "l3vpn"}:
                skill_id = "create-l3vpn-service"
                stype = "l3vpn"
            else:
                return {
                    "ok": False,
                    "error": "unsupported_service_type",
                    "service_type": service_type,
                    "supported_service_types": ["l2vpn", "l3vpn"],
                }

            payload: Dict[str, Any] = {
                "sender": f"agentic-module-session-{_ACTIVE_SESSION_ID.get()}",
                "skill_id": skill_id,
                "input": {
                    "src_device": src_device,
                    "src_endpoint": src_endpoint,
                    "dst_device": dst_device,
                    "dst_endpoint": dst_endpoint,
                    "name": name,
                    "capacity_gbps": capacity_gbps,
                    "mtu": mtu,
                    "context_uuid": context_uuid,
                },
                "session_id": _ACTIVE_SESSION_ID.get(),
            }
            try:
                with httpx.Client(timeout=120.0) as client:
                    response = client.post(f"{base}/a2a/task", json=payload)
                body = response.json() if response.content else {}
                artifact = body.get("artifact") if isinstance(body, dict) else None
                structured = artifact.get("structured") if isinstance(artifact, dict) else None
                peer_ok = 200 <= response.status_code < 300 and bool(body.get("success", True))
                if isinstance(structured, dict) and structured.get("ok") is False:
                    peer_ok = False
                result = {
                    "ok": peer_ok,
                    "peer_url": base,
                    "http_status": response.status_code,
                    "skill_id": skill_id,
                    "service_type": stype,
                    "response": body,
                }
                if peer_ok:
                    self.workflow.mark_a2a_peer_packet_provisioned(_ACTIVE_SESSION_ID.get())
                return result
            except Exception as exc:
                return {
                    "ok": False,
                    "peer_url": base,
                    "error": str(exc),
                    "skill_id": skill_id,
                    "service_type": stype,
                }

        def a2a_provision_peer_packet_service(
            service_type: str,
            src_device: str,
            src_endpoint: str,
            dst_device: str,
            dst_endpoint: str,
            peer_url: Optional[str] = None,
            name: Optional[str] = None,
            capacity_gbps: Optional[float] = None,
            mtu: int = 1512,
            context_uuid: Optional[str] = None,
        ) -> str:
            result = _a2a_provision_peer_packet_service_impl(
                service_type=service_type,
                src_device=src_device,
                src_endpoint=src_endpoint,
                dst_device=dst_device,
                dst_endpoint=dst_endpoint,
                peer_url=peer_url,
                name=name,
                capacity_gbps=capacity_gbps,
                mtu=mtu,
                context_uuid=context_uuid,
            )
            return json.dumps(result, indent=2)

        tools.append(StructuredTool.from_function(
            func=a2a_provision_peer_packet_service,
            name="a2a_provision_peer_packet_service",
            description=(
                "Ask a peer TFS agentic module to create an L2VPN or L3VPN service in "
                "its local controller via /a2a/task. Use for cross-domain packet service "
                "requests where one endpoint is in the peer domain. Required: service_type "
                "(l2vpn|l3vpn), src_device, src_endpoint, dst_device, dst_endpoint. "
                "Pass peer_url, or configure A2A_PEERS with exactly one peer. If the peer "
                "reports success, this marks the current turn as safe to provision the local "
                "NET-EXT segment."
            ),
        ))

        def a2a_provision_cross_domain_packet_service(
            service_type: str,
            src_device: str,
            dst_device: str,
            src_endpoint: str = "eth0",
            dst_endpoint: str = "eth0",
            peer_url: Optional[str] = None,
            name: Optional[str] = None,
            capacity_gbps: Optional[float] = None,
            mtu: int = 1512,
            context_uuid: Optional[str] = None,
        ) -> str:
            """Provision a cross-domain packet service through A2A and local NET-EXT handoff."""
            context_uuid = context_uuid or "admin"
            hint_raw = self.workflow._cross_domain_a2a_preflight(
                service_type=service_type,
                src_device=src_device,
                src_endpoint=src_endpoint,
                dst_device=dst_device,
                dst_endpoint=dst_endpoint,
                context_uuid=context_uuid,
            )
            if not hint_raw:
                pending = self.workflow._cross_domain_packet_plans.get(_ACTIVE_SESSION_ID.get() or "default", {})
                if pending:
                    details = pending
                else:
                    return json.dumps({
                        "ok": False,
                        "phase": "not_cross_domain",
                        "error": "cross_domain_preflight_not_required",
                        "message": (
                            "Both endpoints appear local, both appear missing, NET-EXT is absent, "
                            "or A2A_PEERS is not configured. Use tfs_provision_service for local "
                            "services, or check inventory/A2A configuration."
                        ),
                    }, indent=2)
            else:
                try:
                    hint = json.loads(hint_raw)
                except Exception:
                    return json.dumps({
                        "ok": False,
                        "phase": "preflight_parse_error",
                        "error": "invalid_cross_domain_preflight",
                        "raw": hint_raw,
                    }, indent=2)
                details = hint.get("details", {}) if isinstance(hint, dict) else {}

            peer_segment = details.get("peer_segment_to_request_via_a2a", {})
            local_segment = details.get("local_segment_to_provision_after_peer_success", {})
            if not peer_segment or not local_segment:
                return json.dumps({
                    "ok": False,
                    "phase": "preflight_missing_segments",
                    "error": "cross_domain_segments_unavailable",
                    "preflight": hint,
                }, indent=2)

            service_suffix = f"{peer_segment.get('src_device')}-{peer_segment.get('dst_device')}".lower()
            peer_name = f"{(service_type or 'packet').lower()}-{service_suffix}"
            peer_result = _a2a_provision_peer_packet_service_impl(
                service_type=service_type,
                src_device=peer_segment.get("src_device"),
                src_endpoint=peer_segment.get("src_endpoint"),
                dst_device=peer_segment.get("dst_device"),
                dst_endpoint=peer_segment.get("dst_endpoint"),
                peer_url=peer_url,
                name=peer_name,
                capacity_gbps=capacity_gbps,
                mtu=mtu,
                context_uuid=context_uuid,
            )
            if not peer_result.get("ok"):
                return json.dumps({
                    "ok": False,
                    "phase": "peer_segment_failed",
                    "error": "a2a_peer_provision_failed",
                    "message": "The peer segment was not provisioned; local NET-EXT segment was not created.",
                    "peer_segment": peer_segment,
                    "peer_result": peer_result,
                }, indent=2)

            local_name = name
            if not local_name:
                local_name = f"{(service_type or 'packet').lower()}-{local_segment.get('src_device')}-{local_segment.get('dst_device')}".lower()
            local_raw = self.workflow.provision_service(
                service_type=service_type,
                src_device=local_segment.get("src_device"),
                src_endpoint=local_segment.get("src_endpoint"),
                dst_device=local_segment.get("dst_device"),
                dst_endpoint=local_segment.get("dst_endpoint"),
                name=local_name,
                capacity_gbps=capacity_gbps,
                mtu=mtu,
                context_uuid=context_uuid,
            )
            try:
                local_result = json.loads(local_raw)
            except Exception:
                local_result = {"ok": False, "raw": local_raw}

            ok = bool(local_result.get("ok")) and local_result.get("phase") == "active"
            return json.dumps({
                "ok": ok,
                "phase": "cross_domain_packet_complete" if ok else "local_segment_failed",
                "service_type": service_type,
                "peer_segment": peer_segment,
                "local_segment": local_segment,
                "peer_result": peer_result,
                "local_result": local_result,
                "message": (
                    "Cross-domain packet service established across peer and local NET-EXT segments."
                    if ok else
                    "Peer segment succeeded, but the local NET-EXT segment did not become ACTIVE."
                ),
            }, indent=2)

        tools.append(StructuredTool.from_function(
            func=a2a_provision_cross_domain_packet_service,
            name="a2a_provision_cross_domain_packet_service",
            description=(
                "Preferred tool for cross-domain L2VPN/L3VPN requests such as DC1 to DC6/DC7 "
                "when one endpoint is not in local inventory. It computes local/peer NET-EXT "
                "handoff segments, provisions the peer segment through A2A first, and only then "
                "creates the local segment. Required: service_type, src_device, dst_device. "
                "Optional endpoints default to eth0 for datacenters."
            ),
        ))

        # --- Working memory ----------------------------------------------
        def update_working_memory(notes: str) -> str:
            """Persist a scratchpad across turns.
            Write discovered UUIDs, device names, endpoint IDs, progress, and next steps.
            This text is prepended to your next prompt. Keep it under 400 words.
            Replace stale entries; do not accumulate redundant information.
            """
            sid = _ACTIVE_SESSION_ID.get()
            self.session_memory.set_working_memory(sid, notes)
            return f"Working memory updated ({len(notes)} chars)."

        tools.append(StructuredTool.from_function(
            func=update_working_memory,
            name="update_working_memory",
            description=(
                "Write to your working memory scratchpad. Record discovered UUIDs, "
                "topology details, progress, and next steps. Content is injected into "
                "your next prompt. Keep it concise and current."
            ),
        ))

        # --- Intent declaration ------------------------------------------
        def declare_tfs_intent(intent: str, selected_tool: str, reason: str = "") -> str:
            """Record the model's selected controller intent before mutation."""
            return self.workflow.declare_intent(
                intent=intent,
                selected_tool=selected_tool,
                reason=reason,
            )

        tools.append(StructuredTool.from_function(
            func=declare_tfs_intent,
            name="declare_tfs_intent",
            description=(
                "Before any controller mutation, state the selected intent and the TFS tool "
                "you will use. Allowed intents: optical_slice_restconf, "
                "optical_connectivity_service, packet_service, delete, generic_write. "
                "This is a reasoning checkpoint, not a controller API call."
            ),
        ))

        # --- Human escalation --------------------------------------------
        def ask_user(question: str, candidates: Optional[List[str]] = None) -> str:
            """Interrupt execution and ask the human a question.
            Use when: (1) required IDs cannot be found automatically,
            (2) stuck after two failed attempts on the same step.
            After calling this tool, return the question as your final response.
            """
            qnorm = (question or "").strip().lower()
            if any(token in qnorm for token in (
                "are you sure",
                "do you confirm",
                "confirm deletion",
                "confirm delete",
                "do you want me to delete",
                "should i delete",
            )):
                return (
                    _ASK_USER_SENTINEL
                    + "Confirmation prompts are not allowed for requested delete/remove operations. "
                    "Execute the delete tool directly and report the result."
                )
            if _ASK_USER_CALLED.get():
                return (
                    _ASK_USER_SENTINEL
                    + "ask_user was already called this turn — do not call it again."
                )
            _ASK_USER_CALLED.set(True)
            parts = [question]
            if candidates:
                parts.append("Options: " + ", ".join(str(c) for c in candidates))
            return _ASK_USER_SENTINEL + " ".join(parts)

        tools.append(StructuredTool.from_function(
            func=ask_user,
            name="ask_user",
            description=(
                "Ask the user a question and return their input. Use only when required "
                "facts are missing and cannot be discovered from tools. Do not use it to "
                "ask permission for controller mutations explicitly requested by the user. "
                "Provide candidates when there are clear options to choose from."
            ),
        ))

        # --- High-level TFS workflow tools (preferred by small LLMs) -----
        workflow_tools = self.workflow.build_langchain_tools()
        tools.extend(workflow_tools)

        # --- Raw MCP tools (optional low-level fallback) -----------------
        expose_raw_mcp = os.environ.get("AGENT_EXPOSE_RAW_MCP_TOOLS", "false").lower() == "true"
        mcp_tools = self.mcp.get_tools() if expose_raw_mcp else []
        tools.extend(mcp_tools)
        LOGGER.info(
            "Agent assembled: %d total tools (%d workflow, %d raw MCP, raw_mcp=%s)",
            len(tools), len(workflow_tools), len(mcp_tools), expose_raw_mcp,
        )
        return tools

    # ------------------------------------------------------------------ #
    # Execution                                                           #
    # ------------------------------------------------------------------ #
    def _build_executor(self) -> AgentExecutor:
        agent = create_tool_calling_agent(self.llm, self.tools, self._prompt)
        return AgentExecutor(
            agent=agent,
            tools=self.tools,
            verbose=self._verbose,
            max_iterations=self._max_iterations,
            handle_parsing_errors=True,
            return_intermediate_steps=True,
        )

    def _build_augmented_input(self, query: str, session_id: str) -> str:
        return self.session_memory.build_augmented_input(session_id, query)

    @staticmethod
    def _build_trace_steps(intermediate_steps: list) -> List[Dict[str, Any]]:
        steps = []
        for i, (action, observation) in enumerate(intermediate_steps, start=1):
            tool_name = getattr(action, "tool", "unknown")
            tool_input = getattr(action, "tool_input", {})
            steps.append({
                "id": f"step-{i}",
                "title": tool_name,
                "kind": "tool_call",
                "status": "completed",
                "parameters": tool_input if isinstance(tool_input, dict) else {"input": str(tool_input)},
                "output_summary": str(observation)[:600],
                "error": None,
            })
        return steps

    @staticmethod
    def _is_mutation_request(query: str) -> bool:
        q = (query or "").lower()
        return any(token in q for token in (
            "create", "provision", "delete", "remove", "update",
            "configure", "allocate", "release", "set ",
        ))

    @staticmethod
    def _is_mutating_tool(tool_name: str) -> bool:
        t = (tool_name or "").lower()
        return any(token in t for token in (
            "provision", "delete", "create", "update", "write",
            "allocate", "release", "configure", "set_", "add_",
        ))

    @staticmethod
    def get_last_trace(self, session_id: Optional[str] = None) -> Dict[str, Any]:
        return self.session_memory.get_trace(session_id or "default")

    def run(self, query: str, session_id: Optional[str] = None) -> str:
        return self.run_detailed(query, session_id=session_id)["answer"]

    def run_detailed(self, query: str, session_id: Optional[str] = None) -> Dict[str, Any]:
        sid = session_id or "default"
        session_token = _ACTIVE_SESSION_ID.set(sid)
        ask_user_token = _ASK_USER_CALLED.set(False)
        workflow_tokens = self.workflow.begin_turn(sid)

        try:
            with timed_event(LOGGER, "langchain_agent_run",
                             session_id=sid, query_len=len(query or "")):
                history = self.session_memory.get_history(sid)
                augmented_input = self._build_augmented_input(query, sid)

                executor = self._build_executor()
                try:
                    result = executor.invoke({
                        "input": augmented_input,
                        "chat_history": history,
                    })
                except Exception as exc:
                    LOGGER.error("Agent execution failed: %s", exc)
                    error_answer = f"The agent encountered an error: {exc}"
                    trace = {
                        "session_id": sid, "query": query,
                        "answer": error_answer, "steps": [], "error": str(exc),
                    }
                    self.session_memory.set_trace(sid, trace)
                    return {"answer": error_answer, "trace": trace}

                raw_output: str = (
                    result.get("output", "") if isinstance(result, dict) else str(result)
                )
                intermediate: list = (
                    result.get("intermediate_steps", []) if isinstance(result, dict) else []
                )

                # For mutation intents, enforce at least one mutating tool call.
                # If the first pass produced none, retry once with an explicit
                # execution requirement before finalizing.
                def _count_mutating_calls(steps: list) -> int:
                    count = 0
                    for action, _observation in steps:
                        tool = getattr(action, "tool", "")
                        if self._is_mutating_tool(tool):
                            count += 1
                    return count

                if self._is_mutation_request(query) and _count_mutating_calls(intermediate) == 0:
                    retry_inputs = [
                        (
                            augmented_input
                            + "\n\nExecution requirement: this request is a controller mutation. "
                              "You must call at least one relevant mutating tfs_* tool in this turn "
                              "(create/update/delete/write/provision) before answering."
                        ),
                        (
                            augmented_input
                            + "\n\nTool-call-only requirement: do not return a natural-language answer yet. "
                              "First execute at least one mutating tfs_* tool call in this turn, then summarize."
                        ),
                    ]
                    for retry_idx, retry_input in enumerate(retry_inputs, start=1):
                        try:
                            retry_result = executor.invoke({
                                "input": retry_input,
                                "chat_history": history,
                            })
                            retry_raw_output = (
                                retry_result.get("output", "")
                                if isinstance(retry_result, dict) else str(retry_result)
                            )
                            retry_intermediate = (
                                retry_result.get("intermediate_steps", [])
                                if isinstance(retry_result, dict) else []
                            )
                            retry_mutating_calls = _count_mutating_calls(retry_intermediate)
                            LOGGER.info(
                                "Mutation retry #%d mutating_tool_calls=%d query=%r",
                                retry_idx, retry_mutating_calls, query,
                            )
                            # Keep the retry path only when it actually executed
                            # mutation tooling; otherwise continue to stricter retry.
                            if retry_mutating_calls > 0:
                                raw_output = retry_raw_output
                                intermediate = retry_intermediate
                                break
                        except Exception as retry_exc:
                            LOGGER.warning(
                                "Mutation no-tool retry #%d failed: %s",
                                retry_idx, retry_exc
                            )

                # If the agent called ask_user, surface the question.
                answer = raw_output.strip()
                for action, observation in intermediate:
                    if getattr(action, "tool", "") == "ask_user":
                        question = str(observation).replace(_ASK_USER_SENTINEL, "").strip()
                        if question:
                            answer = question
                        break

                # Remove sentinel if it leaked into the final output.
                answer = answer.replace(_ASK_USER_SENTINEL, "").strip()

                # Enforce tool evidence for mutation requests to prevent
                # false-positive success replies with zero controller actions.
                if self._is_mutation_request(query):
                    mutating_calls = _count_mutating_calls(intermediate)
                    if mutating_calls == 0:
                        answer = (
                            "No controller mutation was executed in this turn, so I did not "
                            "create/delete/update any resource. Please retry the request."
                        )

                if not answer:
                    if intermediate:
                        parts = []
                        for action, observation in intermediate:
                            tool = getattr(action, "tool", "unknown")
                            parts.append(f"**{tool}**:\n{str(observation)[:600]}")
                        answer = (
                            "The agent completed its tool calls but did not produce a summary. "
                            "Please verify the outcome directly on the TFS controller. "
                            "Raw tool results:\n\n" + "\n\n".join(parts)
                        )
                    else:
                        answer = "The agent completed but produced no output."

                steps = self._build_trace_steps(intermediate)
                trace = {
                    "session_id": sid,
                    "query": query,
                    "answer": answer,
                    "planner_mode": "reasoning",
                    "title": f"agent run ({len(steps)} tool call(s))",
                    "steps": steps,
                    "working_memory": self.session_memory.get_working_memory(sid),
                    "cache": (
                        self.controller_cache.snapshot()
                        if self.controller_cache is not None else {}
                    ),
                    "graph": {
                        "nodes": [
                            {
                                "id": s["id"],
                                "task": s["title"],
                                "parameters": s["parameters"],
                                "status": s["status"],
                            }
                            for s in steps
                        ],
                        "edges": [
                            {"from": steps[i]["id"], "to": steps[i + 1]["id"]}
                            for i in range(len(steps) - 1)
                        ],
                    },
                }
                self.session_memory.set_trace(sid, trace)
                self.session_memory.add_turn(sid, query, answer)
                return {"answer": answer, "trace": trace}
        finally:
            self.workflow.end_turn(workflow_tokens)
            _ASK_USER_CALLED.reset(ask_user_token)
            _ACTIVE_SESSION_ID.reset(session_token)
