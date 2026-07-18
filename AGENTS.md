# AGENTS.md

## Purpose

This workspace contains a preliminary implementation of an "Agentic Module" around ETSI TeraFlowSDN, plus a standalone TFS MCP server. The current codebase is best understood as:

- a single runnable agent runtime with LangChain, RAG, MCP client support, and A2A-style HTTP endpoints;
- a separate MCP server that exposes TeraFlowSDN northbound APIs as MCP tools;
- supporting documentation and sample topology/schema data.

It is not yet a complete implementation of multi-controller optical cross-domain negotiation. The repository already contains useful scaffolding for that target, but several core negotiation and orchestration pieces are still missing.

## Top-Level Inventory

### Main agent runtime

- `main.py`
  Process entry point. Loads `.env`, configures logging, creates `AutonomousManager`, starts the HTTP server, supports CLI mode, and supports one-shot query mode.

- `agent/`
  Core orchestration and LLM-driven reasoning.
  - `autonomous_manager.py`
    Wires together the vector store, RAG manager, MCP client, spectrum state, topology store, controller cache, LangChain agent, and A2A interface.
  - `langchain_agent.py`
    Hybrid reasoning agent. The LLM decides intent, ambiguity handling, and which
    high-level workflow tool to call; Python tools execute strict TFS lifecycles.
    Key tools: `tfs_capabilities`, `tfs_read`, `tfs_write`, `tfs_inventory`,
    `tfs_provision_service`, `tfs_provision_optical_connectivity_service`,
    `tfs_delete_service`, `tfs_service_status`, `rag_search`,
    `topology_lookup`, `update_working_memory`, and `ask_user`.
    Raw `mcp__*` tools are available only when
    `AGENT_EXPOSE_RAW_MCP_TOOLS=true`.

- `a2a/`
  Agent-to-agent and OpenWebUI-facing HTTP layer.
  - `a2a_interface.py`
    FastAPI app exposing:
    - `/.well-known/agent-card.json` and legacy `/.well-known/agent.json`
    - `/healthz`
    - `/a2a/info`
    - `/a2a/message`
    - `/a2a/state`
    - `/a2a/topology`
    - `/a2a/topology/path`
    - `/a2a/task`
    - `/a2a/peer/discover`
    - `/a2a/peer/send`
    - `/ingest`
    - `/upload`
    - `/v1/models`
    - `/v1/chat/completions`
  - `a2a_client.py`
    Outbound HTTP client for peer discovery, state retrieval, free-form messages, and structured A2A task dispatch.
  - `openwebui_auth.py`
    OpenWebUI authentication helper used when the agent is configured to use OpenWebUI as its LLM backend.

- `mcp_integration/`
  MCP client-side integration for the agent.
  - `mcp_client.py`
    Connects to one or more MCP servers over `stdio://`, SSE, or streamable HTTP. Discovers remote tools, wraps them as LangChain tools, validates required arguments from tool schemas, and mirrors certain spectrum-related tool results into local state.

- `network/`
  Local non-authoritative network state.
  - `controller_cache.py`
    Thread-safe TTL cache for controller-backed contexts, topologies, topology details, devices, links, and services. It exposes freshness metadata so UI and planner can reason about stale state.
  - `topology_store.py`
    In-memory topology parser and graph store. Accepts generic YAML/JSON topology shapes and also flattens TeraFlowSDN-style `devices` and `links` descriptors. Uses NetworkX when available and marks router-to-router links as border-link candidates.
  - `spectrum_state.py`
    Thread-safe in-memory cache for spectrum candidate ranges, reservations, and discovered MCP capabilities. This is a local cache only, not the source of truth.

- `rag/`
  Retrieval and knowledge ingestion.
  - `vector_store.py`
    FAISS-backed LangChain vector store using HuggingFace embeddings.
  - `rag_manager.py`
    Chunks ingested documents, writes them to FAISS, and formats retrieved context for the LLM.

- `cli/`
  Local terminal interface.
  - `cli_interface.py`
    Interactive prompt with `/help`, `/exit`, `/reset`, and `/ingest <path>`.

- `config/`
  Not present in the current repository tree. Earlier revisions used static
  keyword-routing hints; current behavior relies on LLM tool selection plus
  deterministic workflow tools.

- `static/`
  - `index.html`
    Bundled browser UI for chatting with the agent and uploading knowledge files.

- `devops/`
  Deployment artifacts.
  - `docker/`
    Canonical Docker deployment definitions used by the wrapper scripts, including Compose and both Dockerfiles.
  - `kubernetes/`
    Kubernetes Deployments, Services, and Ingress for `agentic-module` and `tfs-mcp-server`. Runtime settings are injected through a ConfigMap and Secret generated from local `.env` values. The agent ingress publishes the UI and APIs under `/agentic` and relies on ingress-nginx path rewriting.

- `scripts/`
  Deployment helpers.
  - `build-images.sh`
    Builds the `agentic-module` and `tfs-mcp-server` images from the canonical Dockerfiles under `devops/docker/`.
  - `load-k8s-images.sh`
    Optional local-cluster image loader for `kind` and `minikube`.
  - `deploy-docker.sh`, `undeploy-docker.sh`
    Docker Compose deployment wrappers.
  - `deploy-k8s.sh`, `undeploy-k8s.sh`
    Kubernetes deployment wrappers. `deploy-k8s.sh` generates a ConfigMap and Secret from `.env`, builds images, optionally loads them into the cluster runtime, applies the manifests, and forces a rollout restart so config changes take effect even when image tags stay the same.

- `utils/`
  - `event_timing.py`
    Shared structured event logging and elapsed-time helpers.

### Standalone TFS MCP server

- `tfs_mcp_server/`
  Separate MCP server implementation that bridges MCP clients to TeraFlowSDN northbound REST APIs.
  - `main.py`
    Thin entry point into `tfs_mcp.app`.
  - `tfs_mcp_server.py`
    Compatibility shim for older imports/scripts.
  - `start.sh`
    Self-contained launcher that creates or reuses its own virtualenv and starts the server.
  - `requirements.txt`
    Independent dependency set for the MCP server.
  - `README.md`
    Server overview and tool catalogue.
  - `TFS_MCP_ServerPrerequisites.md`
    Additional prerequisite notes.
  - `tfs_mcp/`
    - `app.py`
      CLI parsing and process startup.
    - `dependencies.py`
      Optional dependency loading, dotenv loading, logging, and response formatting.
    - `server.py`
      MCP server implementation. Registers tools and maps each tool call to the appropriate TFS HTTP request.
    - `tool_definitions.py`
      MCP tool catalogue and JSON schemas for TFS resources.

The MCP server exposes both general TFS resource tools and optical-specific tools. The optical ones include:

- `tfs_optical_slice_create`
- `tfs_optical_slice_delete`
- `tfs_media_channel_allocate`
- `tfs_media_channel_release`
- `tfs_dscm_oc_get`
- `tfs_dscm_oc_post`
- `tfs_dscm_oc_delete`

This subtree is the strongest existing building block for the "manage TFS through MCP" goal.

### Documentation and sample artifacts

- `README.md`
  High-level repo description and runtime guide, including the controller-backed
  TFS service lifecycle and deployment paths.

- `docs/`
  - `topology-emulated.json`
    Sample TFS-style topology/device/link payload suitable for upload and local topology parsing.
  - `md-naf-a2a-optical-spectrum-negotiation.md`
    Design note describing the intended architecture: distributed A2A negotiation between domain agents with deterministic spectrum reasoning.
  - `planner_executor_analysis.md`
    Refactor note describing why static tool routing was insufficient and why the planner, executor, blackboard, and expiring controller cache were introduced.
  - `tfs_context.proto`
    Proto schema reference for TFS context, service, endpoint, constraint, and configuration message shapes.

### Workspace metadata

- `.agents/`
  Not present in this clone.

- `.codex/`
  Not present in this clone.

- `.git/`, `.gitignore`
  Standard repository metadata.

## Runtime Shape

The current runtime flow is:

1. `main.py` creates `AutonomousManager`.
2. `AutonomousManager` loads config, then initializes:
   - FAISS vector store
   - RAG manager
   - spectrum state
   - topology store
   - MCP client
   - LangChain agent
   - A2A/OpenWebUI FastAPI interface
3. The FastAPI server exposes human-facing and agent-facing endpoints.
4. Uploaded files can feed both:
   - the FAISS RAG store as text
   - the topology graph if the payload looks like topology JSON/YAML
5. MCP-discovered tools become LangChain tools named `mcp__<tool_name>`.
6. A2A skill dispatch can bypass free-form LLM reasoning and invoke selected MCP tools directly.
7. All chat requests still enter the planner/executor path first, even when the final answer is generic rather than a controller action.

For TFS service provisioning, the important runtime behavior is:

1. resolve user-facing endpoint labels into valid controller endpoints;
2. reserve a service UUID with `CreateService`;
3. update the reserved service with endpoints, constraints, and config rules;
4. read the service back until the controller reports `SERVICESTATUS_ACTIVE`.

Path computation is controller-driven. The local topology graph is used for
context and endpoint grounding, not as the authoritative provisioning decision.

## What Is Already Implemented

- TFS controller access through MCP, via the standalone `tfs_mcp_server/` subtree.
- Hybrid LangChain agent: the LLM decides what operation to perform without keyword
  routing or intent classification, while high-level Python tools execute TFS
  lifecycle sequences deterministically.
- Working memory (`update_working_memory` tool): LLM persists discovered UUIDs and progress
  across turns within a session.
- Human escalation (`ask_user` tool): LLM uses this when required inputs are
  missing or retries are exhausted.
- Built-in TFS knowledge base (`docs/tfs_agent_knowledge.md`) auto-ingested into RAG at startup.
- A2A-style discovery and messaging endpoints.
- Structured A2A task entry points with three spectrum-negotiation-oriented skill IDs:
  - `compute-local-spectrum-candidates`
  - `negotiate-interdomain-spectrum`
  - `reserve-or-commit-spectrum`
- Local topology ingestion and deterministic path/topology queries.
- Local RAG ingestion and retrieval.
- OpenAI-compatible `/v1/chat/completions` endpoint for OpenWebUI integration.
- Execution traces (tool call sequence, working memory, cache) surfaced through the web UI
  and HTTP response metadata.
- Real controller-backed TFS service lifecycle through high-level workflow tools:
  - generic proto/NBI/MCP-aligned reads and writes through `tfs_read` and `tfs_write`
  - inventory discovers real UUIDs
  - provisioning reserves, updates, generates L3VPN config rules, and verifies
  - deletion snapshots service/connection diagnostics, executes delete, and
    reports confirmed or pending removal state

## What Is Not Yet Implemented End-to-End

The repository does not yet contain a full multi-agent optical cross-domain negotiation engine. In particular, the following are still absent or incomplete:

- explicit multi-controller lifecycle management for several domain agents in one deployment;
- a deterministic flex-grid RSA/SA engine that computes feasible slot blocks, intersects proposals, and applies selection policy;
- durable negotiation state shared across multiple agents or controller instances;
- commit/rollback coordination across domains beyond local scaffolding;
- a persistent skill registry and skill crystallization from successful executions;
- explicit pause/resume human-feedback checkpoints during planning and execution;
- automated tests;
- a complete optical-domain data model tied to real controller state beyond generic caches and tool wrappers.

## Important Notes About Maturity

- The code is real and runnable, but some docs are ahead of implementation.
- Some subproject documents still show minor naming drift or aspirational behavior when compared with the current implementation.
- The MCP server README is also slightly stale in places when compared with the actual file list.
- Live validation has been performed manually against a reachable TFS controller, but the repository still lacks an automated test suite.

## Practical Reading Order

For anyone extending this repository toward MCP-managed multi-controller optical negotiation, the most useful order is:

1. `README.md`
2. `agent/autonomous_manager.py`
3. `agent/langchain_agent.py`
4. `a2a/a2a_interface.py`
5. `mcp_integration/mcp_client.py`
6. `network/topology_store.py`
7. `network/spectrum_state.py`
8. `tfs_mcp_server/tfs_mcp/server.py`
9. `tfs_mcp_server/tfs_mcp/tool_definitions.py`
10. `docs/tfs_context.proto`
11. `docs/md-naf-a2a-optical-spectrum-negotiation.md`

## Bottom Line

This repository already demonstrates the three key primitives needed for the target architecture:

- controller access through MCP,
- agent-to-agent communication through HTTP/A2A-style endpoints,
- local knowledge and topology context through RAG and graph parsing.

What it does not yet provide is the actual cross-domain optical negotiation brain and orchestration layer that would turn those primitives into a full multi-agent flex-grid service establishment system.
