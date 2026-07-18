# A2A Details

This document summarizes the Agent-to-Agent (A2A) support implemented in the TFS
Agentic Module.

## Scope

The A2A layer is implemented as a pragmatic HTTP JSON interface for peer agents.
It supports:

- agent discovery through an Agent Card;
- free-form peer messaging;
- structured skill/task dispatch;
- outbound peer discovery and peer calls;
- live spectrum-state exposure;
- topology and path query exposure;
- execution trace inspection;
- structured packet/IP service creation for L2VPN and L3VPN services.

The implementation provides groundwork for multi-agent optical spectrum
negotiation and now also exposes deterministic packet-service creation skills.
Full distributed optical negotiation depends on deterministic spectrum MCP tools
being available to the agent.

## Main Files

- `a2a/a2a_interface.py` - FastAPI server exposing A2A, OpenAI-compatible chat,
  RAG ingestion, upload, topology, and trace endpoints.
- `a2a/a2a_client.py` - outbound HTTP client used to discover and contact peer
  agents.
- `agent/autonomous_manager.py` - wires `A2AInterface`, `A2AClient`, and
  `SpectrumState`; dispatches structured A2A skills to MCP tools.
- `network/spectrum_state.py` - in-memory spectrum candidate/reservation state
  published to peers.
- `docs/md-naf-a2a-optical-spectrum-negotiation.md` - design note for future
  inter-domain flex-grid spectrum negotiation.

## Runtime Wiring

`main.py` creates an `AutonomousManager`. During initialization,
`AutonomousManager` creates:

- `SpectrumState`
- `A2AClient`
- `A2AInterface`
- `LangChainAgent`
- `MCPClient`
- RAG/vector store
- topology store
- controller cache
- session memory

When the HTTP server is enabled, `AutonomousManager.start()` runs
`A2AInterface.run()` in a background thread. The default bind address is
`AGENT_API_HOST=0.0.0.0` and the default port is `AGENT_API_PORT=8800`.

## Inbound A2A Endpoints

### `GET /.well-known/agent-card.json`

Returns the current A2A Agent Card.

The card is generated dynamically, so its metadata reflects current model
configuration, current spectrum state, and discovered MCP capabilities.

### `GET /.well-known/agent.json`

Legacy discovery path. It returns the same Agent Card as
`/.well-known/agent-card.json`.

### `GET /a2a/info`

Returns lightweight metadata:

```json
{
  "agent_id": "...",
  "capabilities": ["chat", "rag", "mcp"],
  "model": "...",
  "provider": "..."
}
```

### `POST /a2a/message`

Accepts a free-form peer message and runs it through the LangChain agent.

Request model:

```json
{
  "sender": "peer-agent",
  "message": "Free-form request",
  "session_id": "optional-session",
  "metadata": {}
}
```

Response model:

```json
{
  "success": true,
  "response": "Agent answer",
  "agent_id": "agentic-module-...",
  "session_id": "optional-session"
}
```

If `session_id` is omitted, the sender name is used as the session identifier.
The handler runs the blocking agent call in a `ThreadPoolExecutor` to avoid
blocking the FastAPI event loop.

### `POST /a2a/task`

Accepts a structured, skill-typed task and dispatches it through
`AutonomousManager.dispatch_skill()`.

Request model:

```json
{
  "sender": "peer-agent",
  "skill_id": "compute-local-spectrum-candidates",
  "input": {},
  "session_id": "optional-session"
}
```

Response model:

```json
{
  "success": true,
  "skill_id": "compute-local-spectrum-candidates",
  "artifact": {},
  "state_version": 1,
  "agent_id": "agentic-module-...",
  "error": null
}
```

Known structured skills are:

Optical spectrum skills:

- `compute-local-spectrum-candidates`
- `negotiate-interdomain-spectrum`
- `reserve-or-commit-spectrum`

Packet/IP service skills:

- `create-l2vpn-service`
- `create-l3vpn-service`
- `provision-packet-service`

The optical skill IDs are mapped to MCP tool-name fragments. The first
discovered MCP tool whose name contains the matching fragment is called.

Current optical skill-to-tool mapping:

```python
{
    "compute-local-spectrum-candidates": "compute_local_spectrum_candidates",
    "negotiate-interdomain-spectrum": "negotiate_interdomain_spectrum",
    "reserve-or-commit-spectrum": "reserve_or_commit_spectrum",
}
```

The packet/IP service skills are handled directly by the existing TFS workflow
provisioning layer and call `tfs_provision_service` semantics internally.
They do not require separate packet-service MCP tools beyond the normal TFS MCP
bridge used by the workflow.

For cross-domain L2VPN/L3VPN intents, the local reasoning agent should prefer
`a2a_provision_cross_domain_packet_service`. That tool computes the local and
peer `NET-EXT` handoff segments, provisions the peer segment over A2A first,
and only then provisions the local `NET-EXT` segment. If the model uses the
lower-level tools, `a2a_provision_peer_packet_service` must succeed before
`tfs_provision_service` is allowed to create the local handoff segment. This
prevents local-only orphan services when the peer controller has not accepted
the remote segment.

Packet service payload fields:

```json
{
  "src_device": "DC1",
  "src_endpoint": "eth0",
  "dst_device": "DC2",
  "dst_endpoint": "eth0",
  "name": "optional-service-name",
  "capacity_gbps": 10,
  "mtu": 1512,
  "context_uuid": "admin"
}
```

For `provision-packet-service`, include `service_type`:

```json
{
  "service_type": "l3vpn",
  "src_device": "DC1",
  "src_endpoint": "eth0",
  "dst_device": "DC3",
  "dst_endpoint": "eth0"
}
```

Supported `service_type` values are `l2vpn` and `l3vpn`, including local aliases
`l2`, `l2nm`, `l3`, and `l3nm`.

If a skill is unknown, dispatch falls back to the LangChain agent using a
free-text prompt containing the skill ID and payload.

If a skill is known but no matching MCP tool is discovered, the endpoint returns
a `404` for unknown skill/tool availability.

### `GET /a2a/state`

Returns the live spectrum-state snapshot:

```json
{
  "state_version": 0,
  "updated_at": 0,
  "candidates": {},
  "reservations": {},
  "mcp_capabilities": []
}
```

This state is local and non-authoritative. The SDN controller and MCP tools
remain the source of truth.

### `GET /a2a/cache`

Returns the controller cache snapshot. This is useful for inspecting recently
read controller resources such as contexts, topologies, devices, links, and
services.

### `GET /a2a/execution/{session_id}`

Returns the last execution trace for a session:

```json
{
  "session_id": "session",
  "trace": {}
}
```

The trace is generated by `LangChainAgent.run_detailed()` and includes tool
calls, parameters, output summaries, cache metadata, working memory, and a
simple execution graph.

### `GET /a2a/topology`

Returns the current uploaded topology from the local topology store:

```json
{
  "sources": ["topology.json"],
  "topology": {
    "source": "topology.json",
    "nodes": [],
    "links": [],
    "border_links": []
  }
}
```

If no topology is loaded, it returns:

```json
{
  "sources": [],
  "topology": null
}
```

### `GET /a2a/topology/path`

Computes a shortest path from the local topology graph.

Example:

```text
/a2a/topology/path?src=DC1&dst=DC4
```

Response:

```json
{
  "src": "DC1",
  "dst": "DC4",
  "path": ["DC1", "R1", "R4", "DC4"]
}
```

This only works when a topology has been uploaded and parsed into the local
topology store.

## Outbound A2A Endpoints

These endpoints instruct this agent to contact another peer.

### `POST /a2a/peer/discover`

Request:

```json
{
  "peer_url": "http://peer-agent:8800"
}
```

Behavior:

1. Fetches `/.well-known/agent-card.json`.
2. Falls back to `/.well-known/agent.json`.
3. Attempts to fetch `/a2a/state`.
4. Returns the peer Agent Card and optional state.

### `POST /a2a/peer/send`

Sends either a free-form message or a structured task to a peer.

Free-form message request:

```json
{
  "peer_url": "http://peer-agent:8800",
  "message": "Hello peer",
  "session_id": "optional-session",
  "metadata": {}
}
```

Structured task request:

```json
{
  "peer_url": "http://peer-agent:8800",
  "skill_id": "negotiate-interdomain-spectrum",
  "input": {
    "border_link": "A-B-1",
    "required_slots": 4,
    "guard_band_slots": 1,
    "candidate_ranges": [
      {"n_start": -20, "n_end": -17}
    ]
  },
  "session_id": "optional-session"
}
```

Packet L3VPN task request:

```json
{
  "peer_url": "http://peer-agent:8800",
  "skill_id": "create-l3vpn-service",
  "input": {
    "src_device": "DC1",
    "src_endpoint": "eth0",
    "dst_device": "DC3",
    "dst_endpoint": "eth0",
    "name": "l3vpn-dc1-dc3",
    "capacity_gbps": 10,
    "mtu": 1512,
    "context_uuid": "admin"
  },
  "session_id": "optional-session"
}
```

Packet L2VPN task request:

```json
{
  "peer_url": "http://peer-agent:8800",
  "skill_id": "create-l2vpn-service",
  "input": {
    "src_device": "DC1",
    "src_endpoint": "eth0",
    "dst_device": "DC2",
    "dst_endpoint": "eth0",
    "name": "l2vpn-dc1-dc2"
  }
}
```

## NET-EXT in Cross-Domain Packet Services

`NET-EXT` is not the A2A communication channel. A2A communication happens over
HTTP between the two `tfs-agentic-module` instances. `NET-EXT` is the packet
topology handoff point used when a service crosses from one TFS controller
domain into another.

In the packet topology, each controller domain has its own local `NET-EXT`
device. That device represents the external edge of the local TFS-controlled
network:

- traffic leaving the local controller domain should be carried to
  `NET-EXT/access0`;
- traffic entering the local controller domain from the peer should start at
  `NET-EXT/access0`;
- the agent uses `NET-EXT` as the stitch point when it splits a cross-domain
  L2VPN or L3VPN into local and remote service segments.

For example, if a user asks one agent to create an L2VPN between `DC1` and
`DC6`, and `DC1` is local while `DC6` belongs to the peer controller domain,
the service is coordinated like this:

```text
User
  -> Agentic Module A WebUI
  -> Agent A reasoning and tools
  -> A2A HTTP request to Agent B
  -> Agent B provisions NET-EXT/access0 to DC6/eth0 in Controller B
  -> Agent A provisions DC1/eth0 to NET-EXT/access0 in Controller A
  -> User receives the final result
```

The logical packet-service split is:

```text
Domain A:
DC1/eth0 -> ... -> NET-EXT/access0

Domain B:
NET-EXT/access0 -> ... -> DC6/eth0
```

The same model applies to L3VPN. The service type changes, but the inter-domain
boundary remains `NET-EXT`.

The A2A task sent by Agent A to Agent B should target the peer domain's local
handoff and destination endpoint, for example:

```json
{
  "skill_id": "create-l2vpn-service",
  "input": {
    "src_device": "NET-EXT",
    "src_endpoint": "access0",
    "dst_device": "DC6",
    "dst_endpoint": "eth0",
    "name": "l2vpn-net-ext-dc6"
  }
}
```

Agent A then creates its own local service segment:

```json
{
  "service_type": "l2vpn",
  "src_device": "DC1",
  "src_endpoint": "eth0",
  "dst_device": "NET-EXT",
  "dst_endpoint": "access0",
  "name": "l2vpn-dc1-net-ext"
}
```

A cross-domain service should only be reported as established when both the
local segment and the peer segment succeed. If peer discovery fails, the peer
does not advertise the required packet skill, the remote endpoint is missing,
or either controller fails to provision its segment, the agent should report
that the requested end-to-end L2VPN/L3VPN is not possible.

## Agent Card

The Agent Card identifies this module as:

```text
Optical Spectrum Negotiation Agent
```

It advertises:

- `protocolVersion: "0.3.0"`
- `preferredTransport: "JSONRPC"`
- streaming disabled
- push notifications disabled
- state transition history disabled
- text and JSON input/output modes

Advertised skills:

- `compute-local-spectrum-candidates`
- `negotiate-interdomain-spectrum`
- `reserve-or-commit-spectrum`
- `create-l2vpn-service`
- `create-l3vpn-service`
- `provision-packet-service`

Additional interfaces:

- `/v1/chat/completions`
- `/a2a/task`
- `/a2a/state`

Metadata includes:

- `agent_id`
- LLM model
- LLM provider
- current spectrum state
- discovered MCP capability names

Note: although the Agent Card says `preferredTransport: "JSONRPC"`, the current
implementation is simple HTTP JSON endpoints rather than a full A2A task
lifecycle implementation.

## Outbound Client

`A2AClient` is a synchronous `httpx` client with a default timeout of 15
seconds.

It supports:

- `discover(peer_base_url)`
- `fetch_state(peer_base_url)`
- `send_message(peer_base_url, message, session_id, metadata)`
- `send_task(peer_base_url, skill_id, input_payload, session_id)`

Discovery first tries:

```text
/.well-known/agent-card.json
```

Then falls back to:

```text
/.well-known/agent.json
```

## Spectrum State

`SpectrumState` is an in-memory, thread-safe local cache. It stores:

- candidate slot ranges per border link;
- soft, committed, or rolled-back reservations;
- discovered MCP capabilities;
- a monotonic `state_version`;
- an `updated_at` timestamp.

Candidate entry shape:

```json
{
  "border_link": {
    "candidate_ranges": [
      {"n_start": -20, "n_end": -17}
    ],
    "required_slots": 4,
    "guard_band_slots": 1,
    "updated_at": 0,
    "source_tool": "compute_local_spectrum_candidates"
  }
}
```

Reservation entry shape:

```json
{
  "res-12345678": {
    "border_link": "A-B-1",
    "n_start": -20,
    "n_end": -17,
    "service_id": "svc-123",
    "status": "soft",
    "updated_at": 0
  }
}
```

The MCP client auto-updates `SpectrumState` when called MCP tool names contain
one of these hints:

- `compute_local_spectrum_candidates`
- `local_spectrum_candidates`
- `reserve_or_commit_spectrum`
- `reserve_spectrum`
- `commit_spectrum`
- `rollback_spectrum`

## Intended Multi-Agent Spectrum Flow

The design document proposes distributed RSA/SA negotiation:

1. Agent A computes local feasible spectrum slot ranges.
2. Agent A sends a spectrum proposal to Agent B.
3. Agent B intersects the proposal with its own feasible ranges.
4. Agent B returns accept, counter-proposal, or reject.
5. Both agents soft-reserve the selected block.
6. Both controllers commit configuration.
7. If commit fails on either side, both rollback reservations.

The expected negotiation payload is based on discrete flex-grid slots:

```json
{
  "service_id": "svc-123",
  "src_domain": "A",
  "dst_domain": "B",
  "border_link": "A-B-1",
  "required_slots": 4,
  "guard_band_slots": 1,
  "candidate_ranges": [
    {"n_start": -20, "n_end": -17},
    {"n_start": -8, "n_end": -5}
  ],
  "constraints": {
    "continuity": true,
    "contiguity": true,
    "same_slot_on_all_segments": true
  }
}
```

The LLM should not directly assign spectrum. The intended split is:

- LLM: interpret intent, use RAG for standards/policies/context, explain
  decisions, initiate negotiation, call tools.
- deterministic spectrum engine: compute feasible ranges, enforce continuity
  and contiguity, intersect candidates, reserve, commit, and rollback.

## Current Limitations

- The bundled `tfs_mcp_server` exposes TFS controller REST operations, but does
  not appear to expose MCP tools named `compute_local_spectrum_candidates`,
  `negotiate_interdomain_spectrum`, or `reserve_or_commit_spectrum`.
- Therefore the advertised structured A2A spectrum skills require an additional
  MCP server/toolset before they can execute successfully.
- Packet/IP service skills use the existing TFS workflow layer and can create
  L2VPN/L3VPN services when the normal TFS MCP bridge and controller are
  reachable.
- A2A endpoints are not authenticated.
- Spectrum state is in memory only and is lost on restart.
- Skill payload validation is generic: `input` is a free-form dictionary.
- The implementation does not provide a full A2A task lifecycle with task state,
  push notifications, streaming, or durable task history.
- The Agent Card declares JSONRPC as preferred transport, but the implemented
  endpoints are HTTP JSON.

## Practical Integration Checklist

To make multi-agent optical spectrum negotiation operational:

1. Provide an MCP server exposing deterministic spectrum tools:
   - `compute_local_spectrum_candidates`
   - `negotiate_interdomain_spectrum`
   - `reserve_or_commit_spectrum`
2. Ensure those tools return structured JSON compatible with `SpectrumState`.
3. Upload or provide topology data with border links and spectrum attributes.
4. Configure each agent with reachable `AGENT_PUBLIC_URL`.
5. Expose each agent's `/a2a/*` endpoints between domains.
6. Add authentication before exposing A2A endpoints outside a trusted lab.
7. Consider durable storage for reservations if negotiation state must survive
   restarts.

To make cross-domain packet L2VPN/L3VPN provisioning operational:

1. Load a topology containing `NET-EXT` on each TFS controller domain.
2. Configure each agent with a reachable peer URL through `A2A_PEERS`.
3. Verify each peer Agent Card advertises `create-l2vpn-service`,
   `create-l3vpn-service`, or `provision-packet-service`.
4. Ensure the local endpoint, remote endpoint, and `NET-EXT/access0` endpoint
   names match the controller inventory exactly.
5. Provision the remote segment through A2A and the local segment through the
   local TFS workflow.
6. Report success only when both controller domains accept their respective
   service segments.

