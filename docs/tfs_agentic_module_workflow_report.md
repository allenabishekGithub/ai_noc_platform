# TFS Agentic Module — Engineering Workflow Report

**Date:** 2026-05-12  
**Environment:** MicroK8s (single-node), Calico CNI, Ubuntu 22.04, NVIDIA A30 GPU (Ollama host)  
**Stack:** LangChain agent · FastAPI · FAISS RAG · MCP client · Kubernetes

---

## 1. Project Overview

The TFS Agentic Module is an AI agent that lets operators control a TFS (Transport and Flow-based SDN) controller using natural language. It sits between an operator (via a web UI or OpenWebUI) and the TFS NBI (North-Bound Interface), translating free-text requests into controller API calls.

### Architecture

```
Operator (Web UI / OpenWebUI)
        │  HTTP /v1/chat/completions
        ▼
  ┌─────────────────────────────┐
  │      agentic-module pod     │
  │                             │
  │  FastAPI (a2a_interface)    │
  │      │                      │
  │  LangChain Agent            │
  │   ├─ Workflow Tools (9)     │
  │   └─ FAISS RAG Store        │
  │      │                      │
  │  MCP Client                 │
  └─────┬───────────────────────┘
        │  SSE  http://tfs-mcp-server:3002/sse
        ▼
  ┌─────────────────────────────┐
  │     tfs-mcp-server pod      │
  │  FastMCP → TFS NBI HTTP     │
  └─────┬───────────────────────┘
        │  REST  http://nbiservice.tfs.svc.cluster.local:8080
        ▼
  TFS SDN Controller (NBI)
```

**Key design decisions:**

- The LLM (qwen2.5:7b-instruct on Ollama) only sees 9 high-level workflow tools — not the 52 raw MCP tools — to stay within a 7B model's reliable instruction-following capacity.
- A FAISS vector store holds RAG documents (5 built-in scenario guides) that the agent retrieves before answering, providing few-shot context without consuming fixed context-window tokens.
- All agent calls run in a `ThreadPoolExecutor` so uvicorn's async event loop is never blocked during LLM inference.

---

## 2. Issues Found and Fixed

### 2.1 Critical — Pod Crash Loop (Event Loop Blocking)

**Symptom:** The pod would start successfully, but crash-loop every ~60 seconds. Kubernetes liveness probe failures were the kill trigger.

**Root cause:** The `async def` route handlers in `a2a_interface.py` were calling blocking synchronous methods (`agent_core.ask()`, `agent_core.ask_detailed()`) directly. During LLM inference (~58 seconds for qwen2.5:7b), uvicorn's event loop was completely blocked. The `/healthz` endpoint could not respond, causing Kubernetes to declare the pod dead after 3 × 20-second probe failures.

**Evidence from logs:**
```
[11:12:09] Last successful /healthz response
[11:12:16] User query received → agent_core.ask_detailed() called (blocking)
[11:13:14] SIGTERM — pod killed after 3 × 20s probe failures
```

**Fix:** Wrapped all three route handlers in `run_in_executor` to offload blocking work to a dedicated `ThreadPoolExecutor`:

```python
self._executor = ThreadPoolExecutor(
    max_workers=int(os.environ.get("AGENT_WORKER_THREADS", "4")),
    thread_name_prefix="agent-worker",
)

# In each route handler:
loop = asyncio.get_event_loop()
answer = await loop.run_in_executor(
    self._executor,
    lambda: self.agent_core.ask(msg.message, session_id=sid),
)
```

This keeps uvicorn's event loop free to serve `/healthz` even during a 7-minute LLM inference call.

**Files changed:** `a2a/a2a_interface.py`

---

### 2.2 Critical — `UnboundLocalError` Crashing at Startup

**Symptom:** After the `run_in_executor` fix was merged, the pod still crashed immediately at startup with:

```
UnboundLocalError: cannot access local variable 'os' where it is not associated with a value
  File "/app/a2a/a2a_interface.py", line 204, in __init__
    max_workers=int(os.environ.get("AGENT_WORKER_THREADS", "4")),
```

**Root cause:** A stray `import os` statement had been placed inside `__init__()` at line 211, *after* `os.environ` was already used at line 204. Python's scoping rules see the local `import os` and treat the entire function scope's `os` as a local variable — meaning the reference at line 204 happens before the local binding is established.

**Fix:** Removed the duplicate `import os` from inside `__init__()`. The module-level `import os` at the top of the file is sufficient.

**Files changed:** `a2a/a2a_interface.py`

---

### 2.3 L3VPN IP Address Collision

**Symptom:** Every L3VPN service provisioned would assign the same IP addresses (`192.168.251.5` and `192.168.252.5`) to its endpoints. After two services, all would have colliding IPs, making routing impossible.

**Root cause:** `_build_l3vpn_config_rules()` used a hardcoded fixed subnet for every service:

```python
{"address_ip": "192.168.251.5", "address_prefix": 24}
```

**Fix:** Derived a unique `/24` subnet per service from a SHA-1 hash of the service UUID:

```python
@staticmethod
def _svc_subnet(svc_uuid: str) -> Tuple[int, int]:
    h = int(hashlib.sha1(svc_uuid.encode()).hexdigest()[:4], 16)
    return (h >> 8) & 0xFF, h & 0xFF

# Usage: produces 10.{third}.{fourth}.1 and 10.{third}.{fourth}.2
```

Collision probability is 1/65536 per service pair — negligible for typical deployments.

**Files changed:** `agent/tfs_workflow_tools.py`

---

### 2.4 Legacy Delete-Confirmation Logic (Historical)

This section documents an older design that used a two-step delete confirmation
flow. The current implementation no longer uses `confirmed` flags for
`tfs_delete_service`; deletion is executed in a single workflow-tool call and
reported as `deleted: true` or `deleted: "pending"`.

---

### 2.5 FAISS Index Lost on Pod Restart

**Symptom:** Every time the pod restarted (for any reason), all user-uploaded documents and RAG knowledge were wiped. Users had to re-upload files after every deploy.

**Root cause:** The FAISS volume was mounted as `emptyDir: {}`, which is ephemeral — wiped whenever the pod terminates.

**Fix:** Replaced with a `PersistentVolumeClaim`:

```yaml
# faiss-index-pvc.yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: faiss-index-pvc
spec:
  accessModes:
    - ReadWriteOnce
  resources:
    requests:
      storage: 1Gi
```

```yaml
# In deployment.yaml — volumes section
volumes:
  - name: faiss-index
    persistentVolumeClaim:
      claimName: faiss-index-pvc
```

**Files changed:** `devops/kubernetes/agentic-module-deployment.yaml`, new file `devops/kubernetes/faiss-index-pvc.yaml`

---

### 2.6 Missing Environment Variables in Deployment Scripts

**Symptom:** The agent was running with wrong defaults (`qwen2.5:1.5b-instruct` instead of `7b`, context window of 2048 instead of 8192), causing poor reasoning quality and context truncation.

**Root cause:** `OLLAMA_NUM_CTX`, `LLM_TEMPERATURE`, `AGENT_EXPOSE_RAW_MCP_TOOLS`, and `MCP_HARD_RESPONSE_CAP` were defined in `.env` but not forwarded in `docker-compose.yml` or written to the Kubernetes ConfigMap by `deploy-k8s.sh`. The default model in `deploy-k8s.sh` was also stale (`qwen2.5:1.5b-instruct`).

**Fix:** Added all four variables to both `docker-compose.yml` and `deploy-k8s.sh` (both the variable declarations and the ConfigMap heredoc). Updated default model to `qwen2.5:7b-instruct`.

**Files changed:** `devops/docker/docker-compose.yml`, `scripts/deploy-k8s.sh`

---

### 2.7 Redundant NBI Call in Delete Snapshot

**Symptom:** Minor — every dry-run delete made two identical calls to `tfs_get_service`, adding ~200ms of unnecessary latency.

**Root cause:** `_service_delete_snapshot()` called `tfs_get_service` internally even though `_find_service()` (the caller) had already fetched the service data.

**Fix:** Removed the redundant `tfs_get_service` call from `_service_delete_snapshot()`.

**Files changed:** `agent/tfs_workflow_tools.py`

---

### 2.8 Undiscoverable Context/Topology Cache Misses

**Symptom:** Every agent operation (list devices, provision service, etc.) triggered two fresh NBI API calls for context and topology discovery, adding 300–500ms per operation.

**Root cause:** `_discover_context()` and `_discover_topology()` had no caching — they hit the NBI every time.

**Fix:** Added 60-second `time.monotonic()` caches for both:

```python
def __init__(self, mcp_client):
    self.mcp = mcp_client
    self._ctx_cache: Tuple[Optional[str], float] = (None, 0.0)
    self._topo_cache: Dict[str, Tuple[str, float]] = {}
```

**Files changed:** `agent/tfs_workflow_tools.py`

---

## 3. Liveness Probe Tuning

After the `run_in_executor` fix, the liveness probe `timeoutSeconds` was increased from 5s to 30s in the Kubernetes deployment. This gives `/healthz` extra tolerance for brief blocking moments (e.g., FAISS model loading at startup) without triggering spurious pod kills.

```yaml
livenessProbe:
  httpGet:
    path: /healthz
    port: http
  initialDelaySeconds: 60
  periodSeconds: 20
  timeoutSeconds: 30   # was 5
```

---

## 4. RAG Knowledge Base

Five built-in markdown documents are automatically ingested into FAISS at startup. On subsequent restarts they are loaded from the PVC (not re-ingested) via content-hash deduplication.

| Document | Purpose | Chunks |
|---|---|---|
| `tfs_agent_knowledge.md` | Core TFS concepts and API structure | 14 |
| `rag_scenarios_service_provisioning.md` | How to provision L3VPN/TAPI services | 9 |
| `rag_scenarios_service_management.md` | How to list, query, and manage services | 8 |
| `rag_scenarios_advanced_operations.md` | Topology inspection, link analysis | 12 |
| `rag_scenarios_error_recovery.md` | Error handling and recovery patterns | 10 |

**Total: 53 chunks** persisted to the PVC at `/app/faiss_index`.

Users can also upload additional documents (PDF, DOCX, TXT, MD, CSV, JSON) via the web UI drag-and-drop zone.

---

## 5. Web UI Enhancement — RAG Status Panel

A status box was added to the sidebar of the web UI (`static/index.html`) that shows the current state of the FAISS knowledge base on page load, and updates after every successful file upload.

**Backend:** A new `GET /rag/stats` endpoint in `a2a_interface.py` returns:
```json
{
  "chunks": 53,
  "sources": [
    "tfs-agent-knowledge-builtin",
    "tfs-scenarios-advanced",
    "tfs-scenarios-error-recovery",
    "tfs-scenarios-management",
    "tfs-scenarios-provisioning"
  ]
}
```

**Frontend:** JavaScript on page load calls `/rag/stats` and renders either:
- `53 chunks ingested` + source list (green, when documents are present)
- `No documents ingested yet.` (grey, on an empty index)

---

## 6. Networking Fix — Ollama Unreachable from Pods

### Problem

The Ollama LLM server runs at `10.1.1.89:11434` on an external GPU machine. From the Kubernetes node host, it is reachable via the default gateway (`10.0.2.2`). However, queries from within pods consistently timed out.

### Root Cause

The MicroK8s cluster uses Calico CNI with pod CIDR `10.1.0.0/16`. The Ollama server IP `10.1.1.89` falls **inside** this CIDR.

Calico's masquerade (SNAT) rule reads:

> Masquerade packets from pod source IPs when the destination is **outside** the pod CIDR.

Because `10.1.1.89` is inside the pod CIDR, Calico does **not** SNAT the outbound packets. They leave the pod with the raw pod source IP (`10.1.205.x`), travel to the VirtualBox gateway (`10.0.2.2`), reach the Ollama host — but the Ollama host has no route back to `10.1.205.x`, so the connection is silently dropped.

### What Did Not Work

| Approach | Why it failed |
|---|---|
| Direct pod → `10.1.1.89` | No SNAT applied; return packets lost |
| Kubernetes Service + Endpoints → `10.1.1.89` | After kube-proxy DNAT, packet still goes to `10.1.1.89`; same SNAT gap |

### Solution

The node IP `10.0.2.15` is **outside** the pod CIDR, so pod-to-node traffic is properly SNAT'd. The node itself can reach `10.1.1.89` via its default gateway. This two-hop proxy chain was used:

```
Pod → ollama ClusterIP → kube-proxy DNAT → 10.0.2.15:11435
                                                  │
                                            socat (hostNetwork)
                                                  │
                                            10.1.1.89:11434
```

**Step 1 — socat DaemonSet with `hostNetwork: true`**

`devops/kubernetes/ollama-proxy-daemonset.yaml` deploys `alpine/socat` in the host network namespace (so it uses the node's routing table and can reach `10.1.1.89`), listening on port 11435:

```yaml
spec:
  hostNetwork: true
  containers:
    - name: socat
      image: alpine/socat:latest
      args:
        - TCP-LISTEN:11435,fork,reuseaddr
        - TCP:10.1.1.89:11434
```

**Step 2 — Kubernetes Service + Endpoints pointing to node IP**

`devops/kubernetes/ollama-service.yaml` creates a ClusterIP Service with an Endpoint pointing to the node IP at the proxy port:

```yaml
apiVersion: v1
kind: Service
metadata:
  name: ollama
spec:
  ports:
    - port: 11434
      targetPort: 11435
---
apiVersion: v1
kind: Endpoints
metadata:
  name: ollama
subsets:
  - addresses:
      - ip: 10.0.2.15    # node IP — outside pod CIDR, SNAT applies
    ports:
      - port: 11435
```

**Step 3 — Update `OLLAMA_BASE_URL`**

```
OLLAMA_BASE_URL=http://ollama:11434
```

Pods resolve `ollama` via CoreDNS to the ClusterIP, kube-proxy DNATs to `10.0.2.15:11435`, socat forwards to `10.1.1.89:11434`.

### Verification

```bash
kubectl -n tfs-agentic exec deployment/agentic-module -- python -c \
  "import urllib.request; print(urllib.request.urlopen('http://ollama:11434', timeout=10).read().decode())"
# → Ollama is running
```

---

## 7. Deployment Workflow

### Prerequisites

Ensure `.env` exists in the project root with at minimum:

```bash
TFS_NBI_URL=http://nbiservice.tfs.svc.cluster.local:8080
OLLAMA_BASE_URL=http://ollama:11434
LLM_MODEL=qwen2.5:7b-instruct
OLLAMA_NUM_CTX=8192
LLM_TEMPERATURE=0.2
AGENT_EXPOSE_RAW_MCP_TOOLS=false
MCP_HARD_RESPONSE_CAP=20000
K8S_IMAGE_LOAD_MODE=registry
K8S_IMAGE_REGISTRY=localhost:32000
```

### Deploy Everything

```bash
cd /home/allen/tfs-agentic-module
./scripts/deploy-k8s.sh
```

This single command:
1. Builds Docker images for `agentic-module` and `tfs-mcp-server`
2. Pushes them to the local registry (`localhost:32000`)
3. Creates the `tfs-agentic` namespace if it doesn't exist
4. Applies the Kubernetes ConfigMap and Secret from `.env`
5. Applies all manifests in `devops/kubernetes/` (including the PVC, Ollama proxy, and Ollama service)
6. Rolls out both deployments and waits for them to become ready

### Monitor a Running Deployment

```bash
# Pod health
kubectl -n tfs-agentic get pods

# Live agent logs (filter out health checks)
kubectl -n tfs-agentic logs -f deployment/agentic-module | grep -v healthz

# MCP server logs
kubectl -n tfs-agentic logs -f deployment/tfs-mcp-server

# RAG index status
kubectl -n tfs-agentic exec deployment/agentic-module -- \
  python -c "import urllib.request, json; \
  print(json.dumps(json.loads(urllib.request.urlopen('http://127.0.0.1:8800/rag/stats').read()), indent=2))"
```

### Port-forward for local access

```bash
kubectl -n tfs-agentic port-forward svc/agentic-module 8800:8800
# Web UI: http://localhost:8800
# API docs: http://localhost:8800/docs
```

---

## 8. End-to-End Query Flow

When the user sends "get all connected devices" via the web UI:

```
1. Browser  POST /v1/chat/completions
2. FastAPI   → run_in_executor (non-blocking)
3. LangChain → RAG retrieval (FAISS similarity search)
4. LangChain → LLM call (Ollama qwen2.5:7b) for tool selection
5. Agent     → tfs_list_contexts    (MCP → NBI GET /contexts)
6. Agent     → tfs_list_topologies  (MCP → NBI GET /context/{uuid}/topology_ids)
7. Agent     → tfs_get_topology_details (MCP → NBI GET /context/{uuid}/topology_details/{uuid})
8. Agent     → tfs_list_services    (MCP → NBI GET /context/{uuid}/services)
9. LangChain → LLM call (Ollama) for final answer synthesis
10. FastAPI  → 200 OK with answer JSON
11. Browser  renders agent response
```

All NBI calls go through the MCP server pod at `10.1.205.235`. Ollama calls travel `pod → ollama ClusterIP → socat (hostNetwork) → 10.1.1.89:11434`.

---

## 9. Files Created or Modified

| File | Type | Description |
|---|---|---|
| `a2a/a2a_interface.py` | Modified | `run_in_executor` fix; removed duplicate `import os`; added `/rag/stats` endpoint |
| `agent/langchain_agent.py` | Modified | Agent execution/tracing updates (historically also included delete-confirmation parsing logic) |
| `agent/tfs_workflow_tools.py` | Modified | SHA-1 subnet derivation; discovery caches; removed redundant NBI call |
| `rag/vector_store.py` | Modified | Added `get_stats()` method |
| `static/index.html` | Modified | RAG status panel in sidebar |
| `devops/docker/docker-compose.yml` | Modified | Added missing env vars; fixed default model |
| `devops/kubernetes/agentic-module-deployment.yaml` | Modified | EmptyDir → PVC; liveness probe timeout 5s → 30s |
| `devops/kubernetes/faiss-index-pvc.yaml` | New | PersistentVolumeClaim for FAISS index |
| `devops/kubernetes/ollama-proxy-daemonset.yaml` | New | socat proxy DaemonSet with `hostNetwork: true` |
| `devops/kubernetes/ollama-service.yaml` | New | ClusterIP Service + Endpoints pointing to node proxy |
| `scripts/deploy-k8s.sh` | Modified | Added missing env vars; fixed default model |
| `.env` | Modified | `OLLAMA_BASE_URL` changed to `http://ollama:11434` |
| `docs/rag_scenarios_*.md` (×4) | New | Built-in RAG scenario guides |
| `docs/networking_ollama_k8s_fix.md` | New | Detailed networking fix write-up |
