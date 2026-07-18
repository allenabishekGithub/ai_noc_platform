# Agentic Module

> Start with the illustrated [Two SDN Domains, One Agentic Workflow](results/two-domain-l2vpn-story.md), which explains the two-controller architecture, local MCP operations, cross-domain A2A L2VPN provisioning, and Grafana observability.

A LangChain-powered AI agent that manages TeraFlowSDN through MCP, with the
current implementation centered on:

- a planner/executor agent for controller-backed discovery and service actions;
- a standalone MCP bridge for the TeraFlowSDN northbound API;
- explainable execution traces rendered in the bundled web UI;
- groundwork for future multi-agent optical cross-domain negotiation.

It already supports real single-controller service lifecycle operations through
TFS. Multi-controller optical negotiation remains a next-step architecture goal.

The runtime exposes:

- **MCP client** - connect to one or more Model Context Protocol servers and
  use their tools transparently from the agent.
- **A2A interface** - JSON HTTP endpoints (`/a2a/*`) so peer agents can send
  messages and discover capabilities.
- **FAISS vector DB** - local RAG store with HuggingFace embeddings; ingest
  documents at runtime via the `/ingest` endpoint or the CLI command
  `/ingest <path>`.
- **OpenWebUI integration** - exposes an OpenAI-compatible
  `/v1/chat/completions` endpoint, so OpenWebUI can use the agent as a model.
- **CLI** - interactive prompt or one-shot `--query` mode.

## Layout

```
.
+-- main.py                       <- Entry point; signal handling, CLI/server flags
+-- agent/                        <- Core orchestration & LLM agent
|   +-- autonomous_manager.py     <- AutonomousManager - wires all sub-components
|   +-- langchain_agent.py        <- LLM + tool-calling agent (LangChain)
+-- a2a/                          <- Agent-to-Agent communication
|   +-- a2a_interface.py          <- FastAPI server (A2A + OpenAI-compatible endpoints)
|   +-- a2a_client.py             <- Outbound HTTP client for peer agents
|   +-- openwebui_auth.py         <- OpenWebUI JWT authentication helper
+-- rag/                          <- RAG & vector store
|   +-- rag_manager.py            <- Chunking, ingestion, retrieval helpers
|   +-- vector_store.py           <- FAISS-backed vector store wrapper
+-- network/                      <- Network topology & spectrum state
|   +-- topology_store.py         <- Parses topology YAML/JSON into NetworkX graphs
|   +-- controller_cache.py       <- Expiring controller-backed cache for TFS inventory
|   +-- spectrum_state.py         <- Thread-safe in-memory spectrum cache
+-- cli/                          <- Interactive CLI
|   +-- cli_interface.py          <- Interactive CLI loop
+-- static/                       <- Web UI assets
|   +-- index.html
+-- mcp_integration/              <- MCP client integration
|   +-- mcp_client.py             <- Connects to MCP servers and exposes their tools
+-- tfs_mcp_server/               <- Standalone TFS MCP bridge
+-- devops/                       <- Docker Compose and Kubernetes deployment artifacts
+-- docs/                         <- Documentation and sample data
    +-- topology-emulated.json
    +-- md-naf-a2a-optical-spectrum-negotiation.md
    +-- planner_executor_analysis.md
    +-- tfs_context.proto
```

## Setup

```bash
# Ensure .env exists and is filled in (LLM provider/model + MCP_SERVERS)
pip install -r requirements.txt

# In case pip is not updated
python -m pip install --upgrade pip setuptools wheel
```

### Choose Your LLM Provider

The runtime supports three providers via environment variables:

- `ollama` for local Qwen (or other Ollama models)
- `openai` for ChatGPT models
- `anthropic` for Claude models

Set in `.env`:

```bash
# Ollama + Qwen
LLM_PROVIDER=ollama
LLM_MODEL=qwen2.5:7b-instruct
OLLAMA_BASE_URL=http://ollama:11434
```

```bash
# OpenAI + ChatGPT
LLM_PROVIDER=openai
LLM_MODEL=gpt-4o-mini
OPENAI_API_KEY=sk-...
# Optional for compatible gateways/proxies
OPENAI_BASE_URL=
```

```bash
# Anthropic + Claude
LLM_PROVIDER=anthropic
LLM_MODEL=claude-3-5-sonnet-latest
ANTHROPIC_API_KEY=sk-ant-...
```

Notes:

- `LLM_TEMPERATURE` applies to all providers.
- `OLLAMA_NUM_CTX` is used only when `LLM_PROVIDER=ollama`.
- In Docker Compose/Kubernetes, these variables are propagated from `.env`.

## Run

Start the HTTP server (A2A + OpenWebUI endpoint):

```bash
python main.py
```

Interactive CLI:

```bash
python main.py --cli
```

One-shot query:

```bash
python main.py --query "Summarize the latest ingested document"
```

Run with no HTTP server (CLI-only):

```bash
python main.py --cli --no-server
```

## Deployment

This repository includes both Docker Compose and Kubernetes deployment paths for the same two-container stack:

- `tfs-mcp-server`: MCP bridge to the TeraFlowSDN northbound API
- `agentic-module`: planner/executor A2A/RAG agent that consumes the MCP tools

Tracked files keep only generic endpoint placeholders. Local deployment values live in `.env`, and a generic template is provided in `.env.example`. For Kubernetes, runtime configuration is injected through a generated ConfigMap and Secret derived from `.env`.

Wrapper scripts are provided:

- `./scripts/build-images.sh`
- `./scripts/deploy-docker.sh`
- `./scripts/undeploy-docker.sh`
- `./scripts/deploy-k8s.sh`
- `./scripts/undeploy-k8s.sh`
- `./scripts/load-k8s-images.sh`

If you need to bootstrap a new local configuration:

```bash
cp .env.example .env
# then edit .env with your local TFS and LLM endpoints
```

```bash
./scripts/deploy-docker.sh
```

Key endpoints after startup:

- Agent health: `http://localhost:8800/healthz`
- Agent chat API: `http://localhost:8800/v1/chat/completions`
- Agent card: `http://localhost:8800/.well-known/agent-card.json`
- MCP SSE endpoint: `http://localhost:3002/sse`

To stop:

```bash
./scripts/undeploy-docker.sh
```

Docker deployment artifacts live under `devops/docker/`. The canonical files are:

- `devops/docker/docker-compose.yml`
- `devops/docker/agentic-module.Dockerfile`
- `devops/docker/tfs-mcp-server.Dockerfile`

The deploy pipeline builds named images first through `./scripts/build-images.sh`, then reuses those same tags for both Compose and Kubernetes. Root `requirements.txt` files remain in place because they are application dependency manifests, not deployment wrappers.

### Kubernetes

Kubernetes manifests live under `devops/kubernetes/`.

The deployment script:

- builds local images for `agentic-module` and `tfs-mcp-server`
- optionally loads them into a local cluster runtime through `./scripts/load-k8s-images.sh`
- creates or updates namespace `K8S_NAMESPACE`
- generates ConfigMap `tfs-agentic-config` from non-secret values in `.env`
- generates Secret `tfs-agentic-secret` from secret values in `.env`
- applies the Deployments, Services, and Ingress
- forces a rollout restart so config changes from ConfigMap and Secret updates are picked up even when the image tag string does not change

Deploy:

```bash
./scripts/deploy-k8s.sh
```

Undeploy:

```bash
./scripts/undeploy-k8s.sh
```

Access the agent after deployment with:

```bash
kubectl -n "${K8S_NAMESPACE:-tfs-agentic}" port-forward svc/agentic-module 8800:8800
```

Notes:

- The tracked manifests use `ClusterIP` services.
- The tracked ingress publishes the agent at `/agentic` through ingress class `public` and rewrites that path to `/` on the service.
- The FAISS index is mounted as `emptyDir`, so it is ephemeral in the current Kubernetes profile.
- The default in-cluster MCP URL comes from `K8S_MCP_SERVERS` and points at the `tfs-mcp-server` Kubernetes Service.
- Kubernetes also needs the container images. The deploy pipeline now builds them through `AGENTIC_IMAGE` and `TFS_MCP_IMAGE`, then optionally loads them into `kind`, `minikube`, or pushes them to a registry when `K8S_IMAGE_LOAD_MODE` is set.
- For MicroK8s with the built-in registry, use `K8S_IMAGE_LOAD_MODE=registry` and `K8S_IMAGE_REGISTRY=localhost:32000`.
- If your cluster uses a separate image runtime or remote nodes, push the images to a registry and set `AGENTIC_IMAGE` and `TFS_MCP_IMAGE` accordingly.
- Set `AGENT_PUBLIC_URL` to the externally reachable ingress URL, for example `http://gateway.example.invalid/agentic`, so the published A2A and chat URLs in the agent card are correct.

## Endpoints

| Method | Path                    | Purpose                                     |
| ------ | ----------------------- | ------------------------------------------- |
| GET    | `/healthz`              | Health probe                                |
| GET    | `/a2a/info`             | A2A capability discovery                    |
| GET    | `/a2a/state`            | Live spectrum state snapshot                |
| GET    | `/a2a/cache`            | Expiring controller cache snapshot          |
| GET    | `/a2a/execution/{id}`   | Last planner/executor trace for a session   |
| POST   | `/a2a/message`          | Receive a message from a peer agent         |
| POST   | `/a2a/task`             | Receive a structured skill-typed task       |
| GET    | `/a2a/topology`         | Parsed network topology                     |
| GET    | `/a2a/topology/path`    | Shortest path between two nodes             |
| POST   | `/a2a/peer/discover`    | Discover and fetch card from peer agent     |
| POST   | `/a2a/peer/send`        | Send message or task to peer agent          |
| POST   | `/ingest`               | Add documents to the FAISS RAG store        |
| POST   | `/upload`               | Upload files (PDF, DOCX, YAML, JSON, text)  |
| GET    | `/v1/models`            | OpenAI-compatible model listing (OpenWebUI) |
| POST   | `/v1/chat/completions`  | OpenAI-compatible chat (OpenWebUI)          |

The chat completion response also carries an `agent_trace` object with:

- the composed plan
- per-step status and summaries
- the execution blackboard
- cache freshness metadata

The bundled web UI renders this trace below each assistant response so the
execution remains inspectable.

## How the Agent Reasons

The LLM receives high-level TFS workflow tools plus `rag_search`,
`topology_lookup`, `update_working_memory`, and `ask_user`. It decides what
operation to perform and when to ask for clarification, while the high-level
tools execute strict TFS API lifecycles. `tfs_capabilities`, `tfs_read`, and
`tfs_write` provide a generic proto/NBI/MCP-aligned layer for context,
topology, service, slice, device, link, connection, policy, and optical
RESTCONF operations. Raw MCP tools can be exposed for debugging with
`AGENT_EXPOSE_RAW_MCP_TOOLS=true`, but they are disabled by default.

For a service creation request the LLM typically:
1. Calls `tfs_inventory` to discover contexts, topology, devices, endpoints, and services.
2. Resolves any ambiguity with the user.
3. Calls `tfs_provision_service` with service type and endpoint names.
4. Lets the workflow tool reserve, update, generate L3VPN config rules when needed, and verify.

For deletion, `tfs_delete_service` resolves the target by name/UUID, captures
pre-delete connection diagnostics, executes the delete in the same call, and
reports a normalized mutation result (`ok`, `phase`, `operation`,
`service_uuid`, `observed_status`, `retryable`, `error`, `message`, `details`).

For optical slice creation, provide an explicit `slice_id` and a valid optical
slice payload. Recommended operator prompt format:

```text
Create an optical slice with slice_id "opt-slice-dc2-dc4-001" from TP-DC2 line
to TP-DC4 line. Use this payload: { ...full optical slice config... }.
If any required payload fields are missing, ask me for them before calling the tool.
```

If the payload includes `tapi-common:context` but omits
`tapi-common:context.uuid`, the agent now auto-generates a random UUID and
uses it in the create request.
The MCP server also enforces this for direct `tfs_optical_slice_create` calls:
if `tapi-common:context.uuid` is missing (or context itself is missing), it
injects a generated UUID before sending to TFS.

For endpoint-based optical slice intents (for example: "create optical slice
from TP-DC2 to TP-DC4"), the agent now has
`tfs_create_optical_slice_from_endpoints`, which resolves endpoint UUIDs from
inventory and builds a minimal optical-slice payload automatically.
The explicit alias `tfs_restconf_create_optical_slice` exists to make clear
that this is the RESTCONF optical-slice API, not TFS optical connectivity
service provisioning.

For optical connectivity services (not optical-slice RESTCONF objects), use a
prompt that includes service intent plus exact endpoint names from inventory.
Recommended operator prompt:

```text
Create an optical connectivity service named "optical-dc2-dc4-1" from TP-DC2 endpoint line
to TP-DC4 endpoint line with 10 Gbps capacity.
```

For deletion of optical connectivity services, prefer the dedicated delete tool
path by using an explicit optical-connectivity delete intent:

```text
Delete optical connectivity service "optical-dc2-dc4-1".
```

This routes to `tfs_service_delete_optical_connectivity`, while generic
`tfs_delete_service` remains available for packet services (L2VPN/L3VPN/E2E)
and other non-optical service types.

For packet-service deletions, use explicit intent text such as:

```text
Delete L3VPN service "l3-dc3-dc4".
```

or

```text
Delete L2VPN service "l2-dc2-dc4".
```

These should route to `tfs_delete_service`.

The agent uses a dedicated optical-connectivity provisioning tool internally
(`tfs_provision_optical_connectivity_service`) so optical custom constraints
match TFS controller expectations, while packet L2/L3 provisioning remains on
the existing `tfs_provision_service` path.
The explicit alias `tfs_service_create_optical_connectivity` exists to make
clear that this creates a TFS `OPTICAL_CONNECTIVITY` service, not a RESTCONF
optical slice.

Before controller mutations, the agent is instructed to call
`declare_tfs_intent` as a reasoning checkpoint. This records whether the model
selected `optical_slice_restconf`, `optical_connectivity_service`,
`packet_service`, `delete`, or `generic_write` before it calls the mutating
TFS tool.

### Workflow Tool -> MCP Translation (by intent)

This is the current workflow-layer mapping in `tfs-agentic-module`.

**Optical slice (RESTCONF / TAPI slice):**

- `tfs_create_optical_slice` -> `tfs_optical_slice_create`
- `tfs_restconf_create_optical_slice` -> alias of `tfs_create_optical_slice`
- `tfs_create_optical_slice_from_endpoints` ->
  `tfs_list_contexts` -> `tfs_list_topologies` -> `tfs_get_topology_details` ->
  `tfs_optical_slice_create`
- `tfs_restconf_create_optical_slice_from_endpoints` -> alias of
  `tfs_create_optical_slice_from_endpoints`
- `tfs_delete_optical_slice` -> `tfs_optical_slice_delete`

**Optical connectivity service (`SERVICETYPE_OPTICAL_CONNECTIVITY`):**

- `tfs_provision_optical_connectivity_service` ->
  `tfs_list_contexts` -> `tfs_list_topologies` -> `tfs_get_topology_details` ->
  `tfs_create_services` -> `tfs_update_service` -> `tfs_get_service`
  (verify/poll)
- `tfs_service_create_optical_connectivity` -> alias of
  `tfs_provision_optical_connectivity_service`
- `tfs_service_delete_optical_connectivity` ->
  `tfs_list_services` (type check) -> `tfs_list_connection_ids` +
  `tfs_list_connections` -> `tfs_delete_service` -> `tfs_list_services`
  (verify)
- `tfs_delete_optical_connectivity_service` -> alias of
  `tfs_service_delete_optical_connectivity`

**Packet services (`l2vpn`, `l3vpn`, `e2e`):**

- `tfs_provision_service` ->
  `tfs_list_contexts` -> `tfs_list_topologies` -> `tfs_get_topology_details` ->
  `tfs_create_services` -> `tfs_update_service` -> `tfs_get_service`
  (verify/poll)
- `tfs_delete_service` ->
  `tfs_list_services` -> `tfs_list_connection_ids` + `tfs_list_connections` ->
  `tfs_delete_service` -> `tfs_list_services` (verify)
- `tfs_service_status` -> `tfs_list_services` (after context discovery)

**Generic read/write layer:**

- `tfs_read` -> resource/operation mapped to one MCP read tool
  (`tfs_list_*`, `tfs_get_*`, etc.)
- `tfs_write` -> resource/operation mapped to one MCP write/delete tool
  (`tfs_create_*`, `tfs_update_*`, `tfs_delete_*`, including RESTCONF tools)

**Support/meta tools:**

- `tfs_inventory` ->
  `tfs_list_contexts` -> `tfs_list_topologies` -> `tfs_get_topology_details` ->
  `tfs_list_services`
- `tfs_capabilities` -> no MCP call (internal capability catalog)
- `declare_tfs_intent` -> no MCP call (internal intent state/guard)

Optical service status interpretation:

- `SERVICESTATUS_ACTIVE`: provisioning completed. For optical slice, the TFS
  controller creates a `TAPI_CONNECTIVITY_SERVICE` and marks it active after the
  optical-slice RESTCONF handler succeeds.
- `SERVICESTATUS_PLANNED`: TFS accepted the service intent and persisted the
  endpoints, but provisioning/path computation has not completed yet. For
  optical connectivity, verify that endpoint names are optical endpoints such as
  `TP-DC1:line` and `TP-DC4:line`, then check TFS optical controller/path
  computation logs if it remains planned.
- `SERVICESTATUS_UNDEFINED`: not a normal completed state. If the service has
  empty `service_endpoint_ids` and empty constraints, it is only a reserved
  service shell: the create/reserve step ran, but the update/provisioning intent
  did not persist. Delete that stale service before retrying.

The agent uses `update_working_memory` to persist discovered UUIDs and progress
across tool calls so it does not repeat discovery steps.

TFS computes the service path and provisions. The agent grounds endpoint labels into
real controller UUIDs by querying the controller directly, not by regex-matching the
user's message.

### Example: A2A message

```bash
curl -X POST http://localhost:8800/a2a/message \
  -H 'content-type: application/json' \
  -d '{"sender":"peer-agent-1","message":"What does the SDT manager do?"}'
```

### Example: ingest

```bash
curl -X POST http://localhost:8800/ingest \
  -H 'content-type: application/json' \
  -d '{"documents":[{"text":"Hello world","metadata":{"source":"demo"}}]}'
```

## OpenWebUI

Use the Docker deployment wrapper:

```bash
./scripts/deploy-docker.sh
```

Then open http://localhost:3000 - OpenWebUI is preconfigured to point at the
agent's `/v1` endpoint, so the model picker will list the agent.

## MCP servers

Set `MCP_SERVERS` in `.env` as a comma-separated list. Each entry is either:

- `http(s)://host:port/mcp` - Streamable HTTP transport
- `stdio://<command and args>` - spawned subprocess transport

Discovered tools become available to the agent as `mcp__<tool_name>`.

## Current Status

The current codebase has been validated against a live TFS controller for a
controller-driven L3VPN workflow:

- reserve service UUID through `CreateService`
- push full service intent through `UpdateService`
- verify `SERVICESTATUS_ACTIVE` through `GetService`

That is the strongest end-to-end implemented path today. The future target of
multiple SDN-controller agents negotiating optical cross-domain flex-grid
services is still architectural rather than complete.

---

# Complete Functionality Reference

## Architecture Overview

The ai_noc_platform combines 6 core architectural layers:

1. **Core Orchestrator** (`agent/autonomous_manager.py`) - Wires all subsystems
2. **LLM + Agent Layer** (`agent/langchain_agent.py`) - Intelligent tool-calling reasoning
3. **Data & Knowledge** (`rag/`, `network/`) - Knowledge base and state tracking
4. **Integration Layer** (`mcp_integration/`, `a2a/`) - Remote tool and peer communication
5. **HTTP Interface** (`a2a/a2a_interface.py`) - FastAPI server for all endpoints
6. **User Interface** (`cli/`, OpenWebUI) - Interactive and web-based interaction

---

## Component Functionalities

### 1. Core Orchestrator (`agent/autonomous_manager.py`)

**Primary Role:** Central hub that wires all subsystems together and manages the agent lifecycle.

| Functionality | Description |
|---|---|
| **Component Initialization** | Instantiates all sub-components: FAISS vector store, RAG manager, MCP client, LangChain agent, A2A interface, topology store, spectrum state |
| **Configuration Loading** | Reads environment variables for API host/port, LLM provider, FAISS paths, MCP server URLs |
| **High-Level API** | Exposes `ask(query, session_id)` and `ingest_documents(docs)` methods for CLI/HTTP handlers |
| **Lifecycle Management** | `start()` launches HTTP server in background thread; `stop()` gracefully closes MCP client and persists FAISS index |
| **Signal Handling** | Catches SIGINT/SIGTERM for clean shutdown |

**Example Usage:**
```python
manager = AutonomousManager()
manager.start()  # Start HTTP server
answer = manager.ask("What spectrum ranges are available?")
manager.ingest_documents([{"text": "...", "metadata": {...}}])
manager.stop()
```

---

### 2. LLM + Agent Layer (`agent/langchain_agent.py`)

**Primary Role:** The intelligent agent that orchestrates LLM tool-calling for spectrum negotiation.

| Functionality | Description |
|---|---|
| **Multi-Provider LLM Support** | Seamlessly switches between OpenAI, OpenWebUI (self-hosted), Ollama, Anthropic via env vars |
| **Tool-Calling Agent** | Uses LangChain's `AgentExecutor` with `create_tool_calling_agent` for agentic reasoning loop |
| **Tool Integration** | Merges 3 tool sources: RAG search, MCP remote tools, topology lookup into single tool set |
| **Spectrum-Tuned Prompt** | System prompt designed for flex-grid negotiation (slot allocation, guard-bands, contiguity constraints) |
| **Session History** | Maintains per-session chat history for multi-turn conversations |
| **Temperature Control** | Configurable LLM temperature (default 0.2 for deterministic spectrum decisions) |

**Tool Categories Exposed to Agent:**
- **rag_search**: Query ingested documents (topology specs, policies, standards)
- **topology_lookup**: Query parsed network topology (nodes, links, borders, paths)
- **mcp__***: All MCP server tools (compute candidates, negotiate, reserve, commit, rollback)

**Example Agent Flow:**
```
User Query: "Negotiate spectrum for service SVC-123 on border-link A-B-1"
  - Agent reads prompt (spectrum constraints, RFC 7792)
  - Agent calls: rag_search("flex-grid continuity constraints")
  - Agent calls: topology_lookup("Get border-link A-B-1 details")
  - Agent calls: mcp__compute_local_spectrum_candidates(...)
  - Agent calls: mcp__negotiate_interdomain_spectrum(...)
  - Returns negotiated slot range to user
```

---

### 3. Vector Store & RAG (`rag/vector_store.py`, `rag/rag_manager.py`)

**Primary Role:** Semantic search over ingested documents using FAISS and HuggingFace embeddings.

#### **Vector Store (`rag/vector_store.py`):**
| Functionality | Description |
|---|---|
| **Index Persistence** | Loads/saves FAISS index from disk; survives restarts |
| **Embedding Model** | Uses HuggingFace sentence-transformers (default: `all-MiniLM-L6-v2` for fast embeddings) |
| **Similarity Search** | `similarity_search(query, k=4)` returns top-k semantically similar documents |
| **Add Documents** | `add_documents(docs)` or `add_texts(texts, metadatas)` ingest new content |
| **Retriever Mode** | Exposes as LangChain retriever for tool-calling agent |

#### **RAG Manager (`rag/rag_manager.py`):**
| Functionality | Description |
|---|---|
| **Document Chunking** | Splits large documents into semantic chunks (default 800 chars, 100 overlap) |
| **Batch Ingestion** | Processes multiple docs with metadata, handles chunking automatically |
| **Context Formatting** | Formats retrieved docs with source attribution for LLM prompt injection |
| **Persistence** | Auto-persists FAISS index after each ingestion |

**Use Cases:**
- Retrieve ITU-T G.694.1 specs when agent needs flex-grid rules
- Look up topology or SLA policies
- Search historical negotiation cases ("counterproposals that succeeded")
- Find domain-specific configurations

---

### 4. Topology Store (`network/topology_store.py`)

**Primary Role:** Parse network topology from YAML/JSON files into queryable NetworkX graphs.

| Functionality | Description |
|---|---|
| **Multi-Format Parsing** | Accepts YAML/JSON with explicit nodes+links or adjacency-list format |
| **Graph Construction** | Builds NetworkX `MultiGraph` for multi-link topologies |
| **Topology Queries** | Neighbors, paths, border links, link details via graph API |
| **Metadata Extraction** | Tracks node types (dc, router, switch), link properties (capacity, border flag, slot width) |
| **Per-Source Storage** | Maintains separate topologies per source file; can merge multiple topologies |
| **Border Link Detection** | Automatically identifies inter-domain links for spectrum negotiation |

**Supported Formats:**

**Format 1 - Explicit Nodes & Links (YAML/JSON):**
```yaml
nodes:
  - {id: DC1, type: dc, domain: A}
  - {id: R1,  type: router, domain: A}
links:
  - {id: A-B-1, src: R1, dst: R2, border: true, 
     slot_width_ghz: 12.5, n_min: -32, n_max: 32}
```

**Format 2 - Adjacency List:**
```yaml
topology:
  DC1: [R1, R2]
  R1: [DC1, R2, R3]
```

**Agent Topology Queries:**
- "What nodes are in domain A?"
- "Which links border domain A and B?"
- "What's the slot width on link R1-R2?"

---

### 5. Spectrum State (`network/spectrum_state.py`)

**Primary Role:** Thread-safe in-memory cache for live spectrum data, enabling fast peer queries.

| Functionality | Description |
|---|---|
| **Candidate Ranges Cache** | Stores per-border-link feasible slot ranges from MCP tools |
| **Reservations Tracking** | Maintains soft/committed/rolled-back spectrum reservations with IDs |
| **MCP Capabilities Registry** | Tracks which tools are available on connected MCP servers |
| **Version Numbering** | Monotonic version counter; peers detect state changes without diffing |
| **Thread-Safe Access** | `RLock` protects all read/write operations for concurrent access |
| **Timestamps** | Records when each entry was updated for freshness checks |
| **Auto-Update from MCP** | Spectrum-related MCP tool results automatically populate this cache |

**Data Structure Examples:**
```python
# Candidates for a border link
candidates["A-B-1"] = {
  "candidate_ranges": [
    {"n_start": -20, "n_end": -17},
    {"n_start": -8, "n_end": -5}
  ],
  "required_slots": 4,
  "guard_band_slots": 1,
  "updated_at": 1714987234.5,
  "source_tool": "compute_local_spectrum_candidates"
}

# Soft reservation tracking
reservations["res-12345"] = {
  "border_link": "A-B-1",
  "n_start": -20,
  "n_end": -17,
  "service_id": "svc-123",
  "status": "soft",
  "updated_at": 1714987240.2
}
```

---

### 6. MCP Client (`mcp_integration/mcp_client.py`)

**Primary Role:** Connect to Model Context Protocol servers and expose their tools as LangChain tools.

| Functionality | Description |
|---|---|
| **Multi-Transport Support** | HTTP/SSE (for remote servers) or stdio (for local CLI spawns) |
| **Auto-Discovery** | Connects to MCP servers at startup, lists all available tools |
| **Tool Wrapping** | Converts MCP tools into LangChain `StructuredTool` wrappers |
| **Structured I/O** | Enforces typed input/output schemas for safe tool calling |
| **Spectrum Auto-Sync** | Detects spectrum-related tools; auto-updates SpectrumState on execution |
| **Error Resilience** | Graceful degradation if MCP servers unreachable; can work with zero servers |
| **Async/Sync Bridge** | Internal async loop for MCP, sync interface for FastAPI handlers |

**Tool Name Matching (Auto-Sync Triggers):**
```python
"compute_local_spectrum_candidates" -> state.update_candidates()
"negotiate_interdomain_spectrum"    -> state.update_reservations()
"reserve_spectrum"                  -> state.update_reservations()
"commit_spectrum"                   -> state.update_reservations()
"rollback_spectrum"                 -> state.update_reservations()
```

**Configuration:**
```bash
# Environment variable (comma-separated)
MCP_SERVERS=http://localhost:9000/mcp,stdio://npx server-filesystem /tmp
```

**Example Tool Invocation:**
```python
mcp__compute_local_spectrum_candidates(
  border_link="A-B-1",
  required_slots=4,
  guard_band_slots=1
)
# Returns: {"candidate_ranges": [...]}
```

---

### 7. A2A (Agent-to-Agent) Communication

#### **A2A Client (`a2a/a2a_client.py`) - Outbound Communication:**

| Functionality | Description |
|---|---|
| **Peer Discovery** | Fetch peer's Agent Card (`/.well-known/agent-card.json`) to learn capabilities |
| **State Fetching** | Query peer's live spectrum state (`/a2a/state`) without re-negotiating |
| **Free-Form Messaging** | Send text messages to peer's `/a2a/message` endpoint |
| **Structured Tasks** | Send skill-typed JSON tasks to peer's `/a2a/task` endpoint |
| **Session Tracking** | Correlate multi-turn conversations via `session_id` |
| **Error Handling** | Graceful timeout and HTTP error handling |

**Usage Example:**
```python
client = A2AClient(sender_id="agent-a")

# Discover peer
card = client.discover("http://agent-b:8801")

# Get peer's spectrum state
state = client.fetch_state("http://agent-b:8801")

# Send proposal
response = client.send_message(
  peer_base_url="http://agent-b:8801",
  message="Propose slot range [-20, -17] on A-B-1"
)
```

#### **A2A Interface (`a2a/a2a_interface.py`) - Inbound Communication + HTTP API:**

**Inbound A2A Endpoints:**
| Endpoint | Method | Purpose |
|---|---|---|
| `/a2a/message` | POST | Receive free-form message from peer agent |
| `/a2a/task` | POST | Receive structured skill-typed task request |
| `/a2a/info` | GET | Capability discovery (what this agent can do) |
| `/a2a/state` | GET | Live spectrum state snapshot |

**Agent Cards (Discovery):**
- Advertises name, skills, MCP capabilities, spectrum state
- Served at `/.well-known/agent-card.json` (current spec) and `/.well-known/agent.json` (legacy)
- Includes skill definitions (compute-candidates, negotiate, reserve, commit, rollback)

---

### 8. File Upload & Ingestion

**Primary Role:** Accept uploaded documents and integrate them into RAG store and topology.

| Functionality | Description |
|---|---|
| **Multi-Format Support** | PDF, DOCX, YAML, JSON, plain text (txt, md, csv, code) |
| **PDF Extraction** | Uses pypdf to extract text from all pages |
| **DOCX Parsing** | Uses python-docx to extract paragraphs |
| **JSON Parsing** | Parses JSON into Python dict; detects topology keys |
| **YAML Parsing** | Parses into Python dict; detects topology/domain/spectrum keys |
| **Topology Auto-Load** | If parsed JSON/YAML has topology keys, auto-loads into TopologyStore |
| **RAG Ingestion** | All extracted text chunked and added to FAISS vector store |
| **Batch Upload** | Accepts multiple files in single POST request |
| **Metadata Tracking** | Records source filename, content-type, size, parsed keys |

**Upload Endpoint:**
```bash
POST /upload
Content-Type: multipart/form-data

files: [topology.json, policies.yaml, network-spec.pdf]
```

**Response:**
```json
{
  "success": true,
  "files": [
    {
      "filename": "topology.json",
      "status": "queued",
      "chars": 5432,
      "topology": {
        "loaded": true,
        "nodes": 12,
        "links": 18,
        "border_links": 4
      }
    }
  ],
  "chunks_added": 45
}
```

---

### 9. OpenWebUI Integration

**Primary Role:** Expose agent as a chat model in OpenWebUI for UI-based interaction.

| Functionality | Description |
|---|---|
| **OpenAI-Compatible API** | Implements `/v1/models` and `/v1/chat/completions` (OpenAI spec subset) |
| **Model Listing** | `/v1/models` returns agent as available model for OpenWebUI discovery |
| **Chat Completions** | `/v1/chat/completions` accepts messages, returns agent response |
| **Session Isolation** | Each OpenWebUI user gets separate session_id (username-based) |
| **Authentication** | Supports OpenWebUI login; JWT token in auth header |
| **Docker Compose Integration** | Includes OpenWebUI sidecar with pre-configured agent URL |

**Integration Example:**
```yaml
# devops/docker/docker-compose.yml
services:
  agentic-module:
    build: .
    ports:
      - "8800:8800"
  
  openwebui:
    image: ghcr.io/open-webui/open-webui:main
    environment:
      OPENAI_API_BASE_URL: http://agentic-module:8800/v1
      OPENAI_API_KEY: not-needed
```

Then in OpenWebUI UI: Select "Optical Spectrum Negotiation Agent" from model dropdown -> chat with spectrum negotiation agent.

---

### 10. Interactive CLI (`cli/cli_interface.py`)

**Primary Role:** Command-line interface for local interactive use.

| Functionality | Description |
|---|---|
| **Interactive Prompt Loop** | `you> ` prompt; replies with `agent> ` responses |
| **Special Commands** | `/help`, `/exit`, `/quit`, `/reset`, `/ingest <path>` |
| **Session Management** | Maintains chat history for multi-turn conversations |
| **File Ingestion** | `/ingest /path/to/topology.yaml` loads file into RAG store |
| **Session Reset** | `/reset` clears conversation history |
| **Error Recovery** | Catches exceptions; displays errors without crashing |
| **EOF Handling** | Graceful exit on Ctrl+D or pipe completion |

**Usage:**
```bash
# Interactive mode
python main.py --cli

you> What spectrum ranges are available on A-B-1?
agent> Based on the topology, border-link A-B-1 supports...

you> /ingest ./network-spec.yaml
(ingested 12 chunks from ./network-spec.yaml)

you> /reset
(session reset)

you> /exit
```

---

### 11. Execution Modes (`main.py`)

**Primary Role:** Entry point offering flexible execution modes.

| Mode | Command | Behavior |
|---|---|---|
| **HTTP Server** | `python main.py` | Starts FastAPI server on 0.0.0.0:8800; blocks until SIGINT |
| **Interactive CLI** | `python main.py --cli` | Interactive prompt loop; no HTTP server |
| **One-Shot Query** | `python main.py --query "..."` | Single query, print answer, exit |
| **CLI + Server** | `python main.py --cli` (default) | Both CLI and HTTP server running |
| **CLI Only** | `python main.py --cli --no-server` | CLI without HTTP background thread |

**Signal Handling:**
- SIGINT (Ctrl+C): Calls `manager.stop()`, closes MCP, persists FAISS, exits cleanly
- SIGTERM: Same as SIGINT

---

### 12. Spectrum Negotiation Workflow (Domain-Specific Logic)

**End-to-End Flow:**

```
Step 1: Agent A Proposes
  +- Calls: mcp__compute_local_spectrum_candidates(...)
  +- Gets: [{"n_start": -20, "n_end": -17}, ...]

Step 2: Agent A Sends Proposal to Agent B
  +- Calls: a2a_client.send_message(peer_url, proposal_json)
  +- Encodes: service_id, required_slots, guard_band, candidate_ranges

Step 3: Agent B Receives on /a2a/message
  +- Runs agent.ask() with peer's proposal
  +- Calls: mcp__compute_local_spectrum_candidates(...)
  +- Intersects: own ranges ? peer ranges
  +- Decides: ACCEPT | COUNTER_PROPOSAL | REJECT

Step 4: Agent B Responds
  +- If ACCEPT: calls mcp__reserve_spectrum(...)
  +- Returns: selected slots, reservation_id to Agent A

Step 5: Agent A Reserves
  +- Calls: mcp__reserve_spectrum(...) with same slots
  +- Soft-reserves on both sides

Step 6: Commit Phase
  +- Both agents call: mcp__commit_spectrum(...)
  +- Controllers apply config; service gets allocated slots

Step 7: Rollback (on failure)
  +- Either agent calls: mcp__rollback_spectrum(...)
  +- Cleans up soft reservations
```

---

## Configuration Reference

**Environment Variables:**

```bash
# --- HTTP server ---
AGENT_API_HOST=0.0.0.0
AGENT_API_PORT=8800
AGENT_PUBLIC_URL=https://agent.example.com          # Optional; for reverse proxy/NAT

# --- LLM provider: openai | openwebui | ollama | anthropic ---
LLM_PROVIDER=openwebui
LLM_MODEL=qwen2.5:7b
LLM_TEMPERATURE=0.2

# --- OpenAI ---
OPENAI_API_KEY=sk-...
OPENAI_BASE_URL=https://api.openai.com/v1

# --- OpenWebUI (self-hosted, login-based) ---
OPENWEBUI_BASE_URL=http://localhost:3000
OPENWEBUI_EMAIL=user@example.com
OPENWEBUI_PASSWORD=password

# --- Ollama (local) ---
OLLAMA_BASE_URL=http://localhost:11434

# --- Anthropic ---
ANTHROPIC_API_KEY=...

# --- FAISS / embeddings ---
FAISS_INDEX_PATH=./faiss_index
EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2

# --- MCP servers (comma-separated) ---
MCP_SERVERS=http://localhost:9000/mcp,stdio://npx server-filesystem /tmp

# --- Agent runtime ---
AGENT_VERBOSE=false
AGENT_MAX_ITERATIONS=8
| **JSON Parsing** | Parses JSON into Python dict; detects topology keys |
| **YAML Parsing** | Parses into Python dict; detects topology/domain/spectrum keys |
| **Topology Auto-Load** | If parsed JSON/YAML has topology keys, auto-loads into TopologyStore |
| **RAG Ingestion** | All extracted text chunked and added to FAISS vector store |
| **Batch Upload** | Accepts multiple files in single POST request |
| **Metadata Tracking** | Records source filename, content-type, size, parsed keys |

**Upload Endpoint:**
```bash
POST /upload
Content-Type: multipart/form-data

files: [topology.json, policies.yaml, network-spec.pdf]
```

**Response:**
```json
{
  "success": true,
  "files": [
    {
      "filename": "topology.json",
      "status": "queued",
      "chars": 5432,
      "topology": {
        "loaded": true,
        "nodes": 12,
        "links": 18,
        "border_links": 4
      }
    }
  ],
  "chunks_added": 45
}
```

---

### 9. OpenWebUI Integration

**Primary Role:** Expose agent as a chat model in OpenWebUI for UI-based interaction.

| Functionality | Description |
|---|---|
| **OpenAI-Compatible API** | Implements `/v1/models` and `/v1/chat/completions` (OpenAI spec subset) |
| **Model Listing** | `/v1/models` returns agent as available model for OpenWebUI discovery |
| **Chat Completions** | `/v1/chat/completions` accepts messages, returns agent response |
| **Session Isolation** | Each OpenWebUI user gets separate session_id (username-based) |
| **Authentication** | Supports OpenWebUI login; JWT token in auth header |
| **Docker Compose Integration** | Includes OpenWebUI sidecar with pre-configured agent URL |

**Integration Example:**
```yaml
# devops/docker/docker-compose.yml
services:
  agentic-module:
    build: .
    ports:
      - "8800:8800"
  
  openwebui:
    image: ghcr.io/open-webui/open-webui:main
    environment:
      OPENAI_API_BASE_URL: http://agentic-module:8800/v1
      OPENAI_API_KEY: not-needed
```

Then in OpenWebUI UI: Select "Optical Spectrum Negotiation Agent" from model dropdown -> chat with spectrum negotiation agent.

---

### 10. Interactive CLI (`cli_interface.py`)

**Primary Role:** Command-line interface for local interactive use.

| Functionality | Description |
|---|---|
| **Interactive Prompt Loop** | `you> ` prompt; replies with `agent> ` responses |
| **Special Commands** | `/help`, `/exit`, `/quit`, `/reset`, `/ingest <path>` |
| **Session Management** | Maintains chat history for multi-turn conversations |
| **File Ingestion** | `/ingest /path/to/topology.yaml` loads file into RAG store |
| **Session Reset** | `/reset` clears conversation history |
| **Error Recovery** | Catches exceptions; displays errors without crashing |
| **EOF Handling** | Graceful exit on Ctrl+D or pipe completion |

**Usage:**
```bash
# Interactive mode
python main.py --cli

you> What spectrum ranges are available on A-B-1?
agent> Based on the topology, border-link A-B-1 supports...

you> /ingest ./network-spec.yaml
(ingested 12 chunks from ./network-spec.yaml)

you> /reset
(session reset)

you> /exit
```

---

### 11. Execution Modes (`main.py`)

**Primary Role:** Entry point offering flexible execution modes.

| Mode | Command | Behavior |
|---|---|---|
| **HTTP Server** | `python main.py` | Starts FastAPI server on 0.0.0.0:8800; blocks until SIGINT |
| **Interactive CLI** | `python main.py --cli` | Interactive prompt loop; no HTTP server |
| **One-Shot Query** | `python main.py --query "..."` | Single query, print answer, exit |
| **CLI + Server** | `python main.py --cli` (default) | Both CLI and HTTP server running |
| **CLI Only** | `python main.py --cli --no-server` | CLI without HTTP background thread |

**Signal Handling:**
- SIGINT (Ctrl+C): Calls `manager.stop()`, closes MCP, persists FAISS, exits cleanly
- SIGTERM: Same as SIGINT

---

### 12. Spectrum Negotiation Workflow (Domain-Specific Logic)

**End-to-End Flow:**

```
Step 1: Agent A Proposes
  +- Calls: mcp__compute_local_spectrum_candidates(...)
  +- Gets: [{"n_start": -20, "n_end": -17}, ...]

Step 2: Agent A Sends Proposal to Agent B
  +- Calls: a2a_client.send_message(peer_url, proposal_json)
  +- Encodes: service_id, required_slots, guard_band, candidate_ranges

Step 3: Agent B Receives on /a2a/message
  +- Runs agent.ask() with peer's proposal
  +- Calls: mcp__compute_local_spectrum_candidates(...)
  +- Intersects: own ranges ? peer ranges
  +- Decides: ACCEPT | COUNTER_PROPOSAL | REJECT

Step 4: Agent B Responds
  +- If ACCEPT: calls mcp__reserve_spectrum(...)
  +- Returns: selected slots, reservation_id to Agent A

Step 5: Agent A Reserves
  +- Calls: mcp__reserve_spectrum(...) with same slots
  +- Soft-reserves on both sides

Step 6: Commit Phase
  +- Both agents call: mcp__commit_spectrum(...)
  +- Controllers apply config; service gets allocated slots

Step 7: Rollback (on failure)
  +- Either agent calls: mcp__rollback_spectrum(...)
  +- Cleans up soft reservations
```

---

## Configuration Reference

**Environment Variables:**

```bash
# --- HTTP server ---
AGENT_API_HOST=0.0.0.0
AGENT_API_PORT=8800
AGENT_PUBLIC_URL=https://agent.example.com          # Optional; for reverse proxy/NAT

# --- LLM provider: openai | openwebui | ollama | anthropic ---
LLM_PROVIDER=openwebui
LLM_MODEL=qwen2.5:7b
LLM_TEMPERATURE=0.2

# --- OpenAI ---
OPENAI_API_KEY=sk-...
OPENAI_BASE_URL=https://api.openai.com/v1

# --- OpenWebUI (self-hosted, login-based) ---
OPENWEBUI_BASE_URL=http://localhost:3000
OPENWEBUI_EMAIL=user@example.com
OPENWEBUI_PASSWORD=password

# --- Ollama (local) ---
OLLAMA_BASE_URL=http://localhost:11434

# --- Anthropic ---
ANTHROPIC_API_KEY=...

# --- FAISS / embeddings ---
FAISS_INDEX_PATH=./faiss_index
EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2

# --- MCP servers (comma-separated) ---
MCP_SERVERS=http://localhost:9000/mcp,stdio://npx server-filesystem /tmp

# --- Agent runtime ---
AGENT_VERBOSE=false
AGENT_MAX_ITERATIONS=8
```

---

## All HTTP Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/healthz` | Health probe |
| GET | `/.well-known/agent-card.json` | A2A Agent Card discovery (current spec) |
| GET | `/.well-known/agent.json` | A2A Agent Card discovery (legacy) |
| GET | `/a2a/info` | Capability discovery |
| POST | `/a2a/message` | Receive message from peer agent |
| POST | `/a2a/task` | Receive structured skill task |
| GET | `/a2a/state` | Live spectrum state snapshot |
| POST | `/a2a/peer/discover` | Discover peer agent capabilities |
| POST | `/a2a/peer/send` | Send message/task to peer agent |
| POST | `/ingest` | Add documents to RAG (JSON payload) |
| POST | `/upload` | Upload files (multipart) for RAG + topology |
| GET | `/v1/models` | OpenAI-compatible model listing |
| POST | `/v1/chat/completions` | OpenAI-compatible chat endpoint |
| GET | `/` | Serve web UI (if static/index.html exists) |
| GET | `/docs` | FastAPI interactive API documentation |

---

## Key Design Patterns

### Pattern: Component Composition
Each major subsystem is independently instantiated and composed:
- Vector Store -> RAG Manager
- MCP servers -> MCP Client -> LangChain Agent
- Topology YAML/JSON -> TopologyStore -> Agent tooling
- All wired by AutonomousManager

### Pattern: Graceful Degradation
- MCP optional (logs warning if unavailable)
- NetworkX optional (TopologyStore works in dict-only mode)
- Document parsers optional (graceful import errors)

### Pattern: Thread-Safe State
- SpectrumState, TopologyStore use RLock
- Version numbers for change detection
- Timestamps for cache freshness

### Pattern: Tool Discovery & Auto-Wiring
- MCP tools discovered at startup -> automatic LangChain integration
- Spectrum-related tools auto-populate SpectrumState
- Topology loaded from YAML/JSON -> queryable graph tool

---

## Dependencies

**Core:**
- FastAPI, uvicorn, pydantic (HTTP framework)
- langchain 0.3.x (agent framework)

**LLM Providers:**
- langchain-openai, langchain-ollama, langchain-huggingface, langchain-anthropic

**RAG:**
- FAISS (vector store)
- sentence-transformers (embeddings)

**MCP:**
- mcp>=1.13 (Model Context Protocol SDK)

**Parsers:**
- pypdf (PDF text extraction)
- python-docx (DOCX parsing)
- PyYAML (YAML parsing)

**Graph:**
- networkx (topology graph queries)

---

## Strengths

- **Modular Design** - Each component independently testable  
- **Flexible LLM** - Plug-and-play provider support  
- **Domain-Aware** - Spectrum-specific state tracking and constraints  
- **Scalable Tooling** - MCP auto-discovery + wrapping  
- **Multi-Modal UI** - CLI + HTTP + OpenWebUI  
- **Thread-Safe** - Proper locking on shared state  
- **Well-Documented** - Inline comments, markdown spec


---

## Observability and Monitoring

The MicroK8s observability stack is installed in the `observability` namespace:

- Prometheus scrapes the Agentic Module, the TFS MCP server, and the OpenTelemetry Collector through `ServiceMonitor` resources.
- Grafana provides the UI and the bundled dashboards.
- Tempo stores OpenTelemetry traces.
- Loki is the log backend.
- The OpenTelemetry Collector receives OTLP telemetry at `opentelemetry-collector.observability.svc.cluster.local:4317`.

The platform configuration is in `platform/observability/`; deployment values and operational instructions are in `platform/observability/README.md`.

### Application telemetry

The Agentic Module and MCP server expose Prometheus metrics:

| Service | Metrics endpoint | Key metrics |
|---|---|---|
| Agentic Module | `GET /metrics` on port `8800` | `tfs_agent_http_requests_total`, `tfs_agent_http_request_duration_seconds` |
| TFS MCP Server | `GET /metrics` on port `3002` | `tfs_mcp_http_requests_total`, `tfs_mcp_http_request_duration_seconds` |

Tracing is controlled by `OTEL_EXPORTER_OTLP_ENDPOINT`. The GitOps values set it to the in-cluster collector. An empty value leaves tracing disabled while keeping the Prometheus metrics endpoint available.

### Access Grafana and Prometheus

```bash
# Grafana
microk8s kubectl -n observability \
  port-forward service/kube-prometheus-stack-grafana 3000:80

# Prometheus
microk8s kubectl -n observability \
  port-forward service/kube-prometheus-stack-prometheus 9090:9090
```

Open `http://127.0.0.1:3000` for Grafana and log in as `admin`. Retrieve its generated password with:

```bash
microk8s kubectl -n observability get secret kube-prometheus-stack-grafana \
  -o jsonpath="{.data.admin-password}" | base64 -d; echo
```

Open `http://127.0.0.1:9090` for Prometheus. Confirm scrape status at `/targets` and try queries such as:

```promql
tfs_agent_http_requests_total
tfs_mcp_http_requests_total
```

Grafana includes these dashboards:

- **TFS Agentic Overview**
- **Cross-domain Workflow**
- **TFS MCP Server**
- **TFS Agentic Gateway**

In Grafana **Explore**, select the Tempo data source for traces and the Loki data source for logs.

### GitOps image rollout

Argo CD deploys the application chart. Build and push new images before pushing the matching GitOps chart values. Use immutable tags so an image change also produces a declarative Deployment change that Argo CD can roll out.

```bash
./scripts/build-images.sh
./scripts/load-k8s-images.sh
git add .
git commit -m "Describe change"
git push origin main
microk8s kubectl -n argocd get application tfs-agentic -w
```

The application should reach `Synced` and `Healthy`. Do not use `deploy-helm.sh` to modify this release after Argo CD takes ownership.

### Two-VM A2A and cross-domain L2VPN deployment

For an illustrated end-to-end walkthrough of the two independent TFS domains, local MCP operations, cross-domain A2A orchestration, and Grafana observability, see [Two SDN Domains, One Agentic Workflow](results/two-domain-l2vpn-story.md).

The current lab contains two Agentic instances, each associated with a different TeraFlowSDN domain. They communicate over the Kubernetes ingress using their EC2 private addresses and the `/agentic` path:

| VM | Deployment owner | A2A peer setting | Workload to inspect |
|---|---|---|---|
| `172.31.20.188` | Argo CD / GitOps | `http://172.31.44.44/agentic` | `deployment/tfs-agentic-agent` |
| `172.31.44.44` | Manual Kubernetes deployment | `http://172.31.20.188/agentic` | `deployment/agentic-module` |

Use the private addresses only for this lab. Replace them with the reachable private DNS names or private addresses appropriate for another environment. The security groups for both VMs must allow TCP port `80` from the peer VM, or from a shared security group.

The configuration source depends on the deployment owner:

| Deployment method | Where `A2A_PEERS` is configured | Apply the change |
|---|---|---|
| Manual `deploy-k8s.sh` | Root `.env` | Run `./scripts/deploy-k8s.sh`; it updates `tfs-agentic-config` and restarts the workloads. |
| Argo CD / GitOps | `devops/helm/tfs-agentic/values.gitops.yaml` as `config.a2aPeers` | Commit and push to `main`; Argo CD renders the Helm chart and restarts the workload. |

`create-gitops-runtime-secret.sh` deliberately creates only runtime credentials. Therefore, changing `A2A_PEERS` in `.env` does **not** change an Argo-managed Agentic pod. For example, on the GitOps VM the required values entry is:

```yaml
config:
  a2aPeers: "http://172.31.44.44/agentic"
```

Validate the running configuration and peer discovery from each VM:

```bash
# Argo-managed VM
microk8s kubectl -n tfs-agentic exec deployment/tfs-agentic-agent -- \
  printenv A2A_PEERS

# Manual-deployment VM
microk8s kubectl -n tfs-agentic exec deployment/agentic-module -- \
  printenv A2A_PEERS

# Run from either VM, replacing the address with its peer
curl -fsS --connect-timeout 5 http://PEER_PRIVATE_ADDRESS/agentic/a2a/info
```

The final command must return the peer Agent Card JSON before cross-domain requests can succeed. With both peers configured, an intent such as `setup l2vpn service between dc2 and dc5` can provision the remote segment through A2A and the local segment through the local TFS MCP bridge.

#### Ownership and migration rule

Installations of Helm, Argo CD, and Grafana can exist on both VMs. The important rule is that a single `tfs-agentic` namespace must have only one active Agentic deployment owner. Do not run the manual `agentic-module` stack and the Argo-managed `tfs-agentic-agent` stack simultaneously: both publish `/agentic` through ingress, making traffic routing ambiguous, and they draw configuration from different sources.

On the peer VM in this lab, the Argo CD controllers and observability stack remain installed, but the `Application/tfs-agentic` resource was removed; the manual deployment is the active application. Do not run `deploy-helm.sh` for an Argo-managed application. It attempts to create a Helm release and can conflict with resources that Argo CD already manages.

Grafana, Prometheus, Loki, Tempo, and the OpenTelemetry Collector do not modify A2A peer routing. An error stating `A2A_PEERS is empty` identifies a configuration-ownership or rollout issue, not an observability issue.

### Log collection note

Loki and OTLP log ingestion are ready. To ingest regular Kubernetes container stdout/stderr logs from every node, add a node-level log collector such as Grafana Alloy. Until then, application logs are still available through `microk8s kubectl logs`, but they are not automatically searchable in Loki.


### Kubernetes service and port reference

The following table reflects the MicroK8s deployment. `ClusterIP` services are reachable only inside the cluster; use `kubectl port-forward` for local access. `None` means a headless service intended for in-cluster discovery. Regenerate the live inventory with `microk8s kubectl get services -A`.

| Namespace | Service | Ports | Purpose / access |
|---|---|---|---|
| `tfs-agentic` | `tfs-agentic-agent` | `8800/TCP` | Agent API, UI, A2A, OpenAI-compatible API, `/metrics` |
| `tfs-agentic` | `tfs-agentic-mcp` | `3002/TCP` | TFS MCP SSE endpoint (`/sse`), health, `/metrics` |
| `tfs-agentic` | Ingress `tfs-agentic-agent` | `80/TCP` | External path `/agentic/...` through ingress class `public` |
| `tfs` | `contextservice` | `1010`, `9192/TCP` | TeraFlowSDN context service |
| `tfs` | `deviceservice` | `2020`, `9192/TCP` | TeraFlowSDN device service |
| `tfs` | `nbiservice` | `8080/TCP` | TeraFlowSDN northbound API used by the MCP server |
| `tfs` | `opticalcontrollerservice` | `10060`, `9192/TCP` | Optical controller service |
| `tfs` | `pathcompservice` | `10020`, `8081`, `9192/TCP` | Path computation service |
| `tfs` | `serviceservice` | `3030`, `9192/TCP` | TeraFlowSDN service lifecycle API |
| `tfs` | `webuiservice` | `8004`, `3000/TCP` | TeraFlowSDN web UI |
| `argocd` | `argocd-server` | `80`, `443/TCP` | Argo CD UI and API; port-forward to local `8080:443` |
| `argocd` | `argocd-repo-server` | `8081`, `8084/TCP` | Git/Helm manifest rendering, internal only |
| `argocd` | `argocd-applicationset-controller` | `7000`, `8080/TCP` | ApplicationSet controller, internal only |
| `argocd` | `argocd-dex-server` | `5556`, `5557`, `5558/TCP` | Argo CD identity service, internal only |
| `argocd` | `argocd-redis` | `6379/TCP` | Argo CD cache, internal only |
| `argocd` | Metrics services | `8082`, `8083`, `9001/TCP` | Application controller, server, and notification metrics |
| `observability` | `kube-prometheus-stack-grafana` | `80/TCP` | Grafana; port-forward to local `3000:80` |
| `observability` | `kube-prometheus-stack-prometheus` | `9090`, `8080/TCP` | Prometheus UI/API and metrics; port-forward `9090:9090` |
| `observability` | `kube-prometheus-stack-alertmanager` | `9093`, `8080/TCP` | Alertmanager UI/API |
| `observability` | `kube-prometheus-stack-operator` | `443/TCP` | Prometheus Operator webhook/API, internal only |
| `observability` | `kube-prometheus-stack-kube-state-metrics` | `8080/TCP` | Kubernetes state metrics |
| `observability` | `kube-prometheus-stack-prometheus-node-exporter` | `9100/TCP` | Node host metrics |
| `observability` | `opentelemetry-collector` | `4317`, `4318`, `8889`, `8888/TCP` | OTLP gRPC, OTLP HTTP, exported Prometheus metrics, collector internal metrics |
| `observability` | `tempo` | `3200`, `4317`, `4318`, `14250`, `14268`, `9411`, `55678`, `55680`, `55681/TCP`; `6831`, `6832/UDP` | Tempo trace query and ingest protocols |
| `observability` | `loki` | `3100`, `9095/TCP` | Loki query/API and gRPC |
| `observability` | `loki-gateway` | `80/TCP` | Loki HTTP gateway; used by clients inside the cluster |
| `observability` | `loki-canary` | `3500/TCP` | Loki canary metrics |
| `observability` | Loki internal services | `11211`, `9150`, `7946`, `3100/TCP` | Chunk/result caches, memberlist, and headless discovery; internal only |
| `container-registry` | `registry` | `5000/TCP` in cluster; `32000/TCP` NodePort | MicroK8s local registry at `localhost:32000` |
| `crdb` | `cockroachdb-public` | `8080`, `26257/TCP` | CockroachDB UI and SQL |
| `kafka` | `kafka-service` | `9092`, `9093`, `9094/TCP` | Kafka client and internal listener ports |
| `nats` | `nats` | `4222/TCP` | NATS client endpoint |
| `nats` | `nats-headless` | `4222`, `8222/TCP` | NATS cluster discovery and monitoring, internal only |
| `kube-system` | `kube-dns` | `53/UDP`, `53`, `9153/TCP` | Cluster DNS and CoreDNS metrics |
| `kube-system` | `metrics-server` | `443/TCP` | Kubernetes resource metrics API |
| `default` | `kubernetes` | `443/TCP` | Kubernetes API server |
| `kube-system` | Prometheus headless targets | `9153`, `2381`, `10249`, `10250`, `10255`, `10257`, `10259`, `4194/TCP` | CoreDNS, etcd, kube-proxy, kubelet, controller-manager, and scheduler metrics; internal only |
