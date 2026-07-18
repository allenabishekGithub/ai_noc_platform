# Networking Fix: Ollama Unreachable from Kubernetes Pods

**Date:** 2026-05-12  
**Environment:** MicroK8s (single-node), Calico CNI, Ubuntu 22.04  
**Symptom:** Agent returned `[Errno 110] Connection timed out` on every query

---

## Symptom

Every query sent to the agentic module via the web UI resulted in:

```
The agent encountered an error: [Errno 110] Connection timed out
```

The pod logs showed the agent ran for ~131 seconds before failing:

```
Agent execution failed: [Errno 110] Connection timed out
EVENT_END elapsed_ms=131154.315 event='langchain_agent_run' status='ok'
```

---

## Root Cause Analysis

### Step 1 — Confirmed Ollama is running on the host

```bash
curl http://10.1.1.89:11434
# → "Ollama is running"
```

Ollama was running and reachable from the Kubernetes node host machine.

### Step 2 — Confirmed pods cannot reach Ollama directly

```bash
kubectl -n tfs-agentic exec deployment/agentic-module -- python -c \
  "import socket; s=socket.socket(); s.settimeout(5); s.connect(('10.1.1.89', 11434))"
# → TimeoutError: timed out
```

### Step 3 — Identified the conflict: Ollama IP is inside the pod CIDR

Checking the node and pod configuration:

| Resource | IP |
|---|---|
| Node (MicroK8s) | `10.0.2.15` |
| Pod CIDR (Calico) | `10.1.0.0/16` |
| Ollama server | `10.1.1.89` |
| agentic-module pod | `10.1.205.217` |

**The Ollama server IP `10.1.1.89` falls inside the pod CIDR `10.1.0.0/16`.**

Calico's masquerade (SNAT) rule is:

> Masquerade packets whose source is in the pod CIDR **and** whose destination is **not** in the pod CIDR.

Because `10.1.1.89` is inside the pod CIDR, Calico does not apply SNAT. Packets leave the pod with the pod source IP (`10.1.205.217`), reach the VirtualBox NAT gateway at `10.0.2.2`, but the return traffic has no route back to the pod — so the connection silently times out.

### Step 4 — Tried a Kubernetes Service + Endpoints pointing to `10.1.1.89`

```yaml
# Did NOT work
apiVersion: v1
kind: Endpoints
subsets:
  - addresses:
      - ip: 10.1.1.89   # inside pod CIDR — same masquerade problem
```

After kube-proxy's DNAT, the packet destination is still `10.1.1.89`. Calico's eBPF/iptables applied the same routing, and the packet was dropped for the same reason.

### Step 5 — Confirmed pods can reach the node IP

```bash
kubectl -n tfs-agentic exec deployment/agentic-module -- python -c \
  "import socket; s=socket.socket(); s.settimeout(5); s.connect(('10.0.2.15', 22)); print('reachable')"
# → reachable
```

Pods can always reach the node's primary IP. From the node, Ollama is reachable via the default gateway (`10.0.2.2 → 10.1.1.89`).

---

## Solution

The fix uses two Kubernetes resources to create a transparent proxy chain:

```
Pod → ollama Service (ClusterIP) → node IP:11435 → socat proxy → 10.1.1.89:11434
```

### 1. DaemonSet: socat TCP proxy with `hostNetwork: true`

Running a container with `hostNetwork: true` places it in the **host's network namespace**, giving it full access to the node's routing table and default gateway — which can reach `10.1.1.89`.

`devops/kubernetes/ollama-proxy-daemonset.yaml`:

```yaml
apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: ollama-proxy
spec:
  selector:
    matchLabels:
      app.kubernetes.io/name: ollama-proxy
  template:
    spec:
      hostNetwork: true          # <-- uses host network namespace
      tolerations:
        - operator: Exists
      containers:
        - name: socat
          image: alpine/socat:latest
          args:
            - TCP-LISTEN:11435,fork,reuseaddr
            - TCP:10.1.1.89:11434
          ports:
            - containerPort: 11435
              hostPort: 11435
```

socat listens on port `11435` of the host's IP (`10.0.2.15`) and forwards each connection to `10.1.1.89:11434`. Because it runs in the host network namespace, it uses the host's routing table and the gateway `10.0.2.2` can reach `10.1.1.89` normally.

### 2. Service + Endpoints: point to the node IP

`devops/kubernetes/ollama-service.yaml`:

```yaml
apiVersion: v1
kind: Service
metadata:
  name: ollama
spec:
  ports:
    - port: 11434       # what pods connect to
      targetPort: 11435 # what the proxy listens on
---
apiVersion: v1
kind: Endpoints
metadata:
  name: ollama
subsets:
  - addresses:
      - ip: 10.0.2.15   # node IP — reachable from pods, outside pod CIDR
    ports:
      - port: 11435
```

The endpoint IP `10.0.2.15` is the node's primary IP, which is **outside** the pod CIDR (`10.1.0.0/16`). Calico's masquerade rule applies correctly, so the return traffic reaches the pod.

### 3. Updated `OLLAMA_BASE_URL`

`.env` and the Kubernetes ConfigMap were updated:

```
OLLAMA_BASE_URL=http://ollama:11434
```

This resolves to the `ollama` ClusterIP via CoreDNS, which then routes through kube-proxy to the socat proxy on the node.

---

## Verification

```bash
kubectl -n tfs-agentic exec deployment/agentic-module -- python -c \
  "import urllib.request; print(urllib.request.urlopen('http://ollama:11434', timeout=10).read().decode())"
# → Ollama is running
```

---

## Why Other Approaches Were Ruled Out

| Approach | Why it failed |
|---|---|
| Direct pod → `10.1.1.89` | IP is in pod CIDR; Calico skips SNAT; return traffic lost |
| Service/Endpoints → `10.1.1.89` | After kube-proxy DNAT, packet still goes to `10.1.1.89`; same SNAT problem |
| `sudo iptables` custom MASQUERADE rule | No passwordless sudo available in this environment |
| Change cluster CIDR | Requires MicroK8s reinstall; destructive |
| `hostNetwork: true` on agentic-module | Security concern; not suitable for application pods |

---

## Files Changed

| File | Change |
|---|---|
| `devops/kubernetes/ollama-proxy-daemonset.yaml` | New — socat DaemonSet with `hostNetwork: true` |
| `devops/kubernetes/ollama-service.yaml` | New — Service + Endpoints pointing to node proxy |
| `.env` | `OLLAMA_BASE_URL` changed from `http://10.1.1.89:11434` to `http://ollama:11434` |
