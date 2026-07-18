#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${ROOT_DIR}/.env"
MANIFEST_DIR="${ROOT_DIR}/devops/kubernetes"
NAMESPACE="${K8S_NAMESPACE:-tfs-agentic}"

if [[ ! -f "${ENV_FILE}" ]]; then
    echo "[deploy-k8s.sh] Missing ${ENV_FILE}" >&2
    echo "[deploy-k8s.sh] Copy .env.example to .env and fill in your local values." >&2
    exit 1
fi

set -a
source "${ENV_FILE}"
set +a

K8S_IMAGE_LOAD_MODE="${K8S_IMAGE_LOAD_MODE:-none}"
K8S_IMAGE_REGISTRY="${K8S_IMAGE_REGISTRY:-localhost:32000}"

if [[ "${K8S_IMAGE_LOAD_MODE}" == "registry" ]]; then
    AGENTIC_IMAGE="${AGENTIC_IMAGE:-${K8S_AGENTIC_IMAGE:-${K8S_IMAGE_REGISTRY}/tfs-agentic-agentic-module:dev}}"
    TFS_MCP_IMAGE="${TFS_MCP_IMAGE:-${K8S_TFS_MCP_IMAGE:-${K8S_IMAGE_REGISTRY}/tfs-agentic-tfs-mcp-server:dev}}"
else
    AGENTIC_IMAGE="${AGENTIC_IMAGE:-${K8S_AGENTIC_IMAGE:-tfs-agentic-agentic-module:dev}}"
    TFS_MCP_IMAGE="${TFS_MCP_IMAGE:-${K8S_TFS_MCP_IMAGE:-tfs-agentic-tfs-mcp-server:dev}}"
fi

AGENT_API_HOST="${AGENT_API_HOST:-0.0.0.0}"
AGENT_API_PORT="${AGENT_API_PORT:-8800}"
FAISS_INDEX_PATH="${FAISS_INDEX_PATH:-/app/faiss_index}"
LLM_PROVIDER="${LLM_PROVIDER:-ollama}"
LLM_MODEL="${LLM_MODEL:-qwen2.5:7b-instruct}"
OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://ollama.example.invalid:11434}"
OLLAMA_NUM_CTX="${OLLAMA_NUM_CTX:-8192}"
LLM_TEMPERATURE="${LLM_TEMPERATURE:-0.2}"
EMBEDDING_MODEL="${EMBEDDING_MODEL:-sentence-transformers/all-MiniLM-L6-v2}"
AGENT_VERBOSE="${AGENT_VERBOSE:-false}"
AGENT_MAX_ITERATIONS="${AGENT_MAX_ITERATIONS:-15}"
AGENT_EXPOSE_RAW_MCP_TOOLS="${AGENT_EXPOSE_RAW_MCP_TOOLS:-false}"
MCP_HARD_RESPONSE_CAP="${MCP_HARD_RESPONSE_CAP:-20000}"
A2A_PEERS="${A2A_PEERS:-}"
DEFAULT_CONTEXT_UUID="${DEFAULT_CONTEXT_UUID:-admin}"
DEFAULT_TOPOLOGY_UUID="${DEFAULT_TOPOLOGY_UUID:-admin}"
CONTROLLER_CACHE_TTL_SECONDS="${CONTROLLER_CACHE_TTL_SECONDS:-120}"
TFS_NBI_URL="${TFS_NBI_URL:-http://tfs-controller.example.invalid}"
TFS_NBI_PREFIX="${TFS_NBI_PREFIX:-/tfs-api}"
TFS_MCP_PORT="${TFS_MCP_PORT:-3002}"
MCP_SERVERS="${K8S_MCP_SERVERS:-${MCP_SERVERS:-http://tfs-mcp-server:3002/sse}}"
OPENAI_BASE_URL="${OPENAI_BASE_URL:-}"
OPENWEBUI_BASE_URL="${OPENWEBUI_BASE_URL:-}"
AGENT_PUBLIC_URL="${AGENT_PUBLIC_URL:-}"
OLLAMA_HOST_IP="${OLLAMA_HOST_IP:-$(kubectl get nodes -o jsonpath='{.items[0].status.addresses[?(@.type=="InternalIP")].address}' 2>/dev/null || echo "127.0.0.1")}"

DEFAULT_LOCAL_AGENTIC_IMAGE="tfs-agentic-agentic-module:dev"
DEFAULT_LOCAL_TFS_MCP_IMAGE="tfs-agentic-tfs-mcp-server:dev"

TFS_NBI_USER="${TFS_NBI_USER:-admin}"
TFS_NBI_PASS="${TFS_NBI_PASS:-admin}"
OPENAI_API_KEY="${OPENAI_API_KEY:-}"
ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-}"
OPENWEBUI_EMAIL="${OPENWEBUI_EMAIL:-}"
OPENWEBUI_PASSWORD="${OPENWEBUI_PASSWORD:-}"

TMP_DIR="$(mktemp -d)"
cleanup() {
    rm -rf "${TMP_DIR}"
}
trap cleanup EXIT

cat > "${TMP_DIR}/config.env" <<EOF
AGENT_API_HOST=${AGENT_API_HOST}
AGENT_API_PORT=${AGENT_API_PORT}
FAISS_INDEX_PATH=${FAISS_INDEX_PATH}
LLM_PROVIDER=${LLM_PROVIDER}
LLM_MODEL=${LLM_MODEL}
OLLAMA_BASE_URL=${OLLAMA_BASE_URL}
OLLAMA_NUM_CTX=${OLLAMA_NUM_CTX}
LLM_TEMPERATURE=${LLM_TEMPERATURE}
EMBEDDING_MODEL=${EMBEDDING_MODEL}
AGENT_VERBOSE=${AGENT_VERBOSE}
AGENT_EXPOSE_RAW_MCP_TOOLS=${AGENT_EXPOSE_RAW_MCP_TOOLS}
MCP_HARD_RESPONSE_CAP=${MCP_HARD_RESPONSE_CAP}
A2A_PEERS=${A2A_PEERS}
AGENT_MAX_ITERATIONS=${AGENT_MAX_ITERATIONS}
DEFAULT_CONTEXT_UUID=${DEFAULT_CONTEXT_UUID}
DEFAULT_TOPOLOGY_UUID=${DEFAULT_TOPOLOGY_UUID}
CONTROLLER_CACHE_TTL_SECONDS=${CONTROLLER_CACHE_TTL_SECONDS}
TFS_NBI_URL=${TFS_NBI_URL}
TFS_NBI_PREFIX=${TFS_NBI_PREFIX}
TFS_MCP_PORT=${TFS_MCP_PORT}
MCP_SERVERS=${MCP_SERVERS}
OPENAI_BASE_URL=${OPENAI_BASE_URL}
OPENWEBUI_BASE_URL=${OPENWEBUI_BASE_URL}
AGENT_PUBLIC_URL=${AGENT_PUBLIC_URL}
EOF

cat > "${TMP_DIR}/secret.env" <<EOF
TFS_NBI_USER=${TFS_NBI_USER}
TFS_NBI_PASS=${TFS_NBI_PASS}
OPENAI_API_KEY=${OPENAI_API_KEY}
ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
OPENWEBUI_EMAIL=${OPENWEBUI_EMAIL}
OPENWEBUI_PASSWORD=${OPENWEBUI_PASSWORD}
EOF

cd "${ROOT_DIR}"

if [[ "${K8S_IMAGE_LOAD_MODE}" == "registry" ]]; then
    if ! docker image inspect "${AGENTIC_IMAGE}" >/dev/null 2>&1; then
        if docker image inspect "${DEFAULT_LOCAL_AGENTIC_IMAGE}" >/dev/null 2>&1; then
            echo "[deploy-k8s.sh] Tagging ${DEFAULT_LOCAL_AGENTIC_IMAGE} as ${AGENTIC_IMAGE}"
            docker tag "${DEFAULT_LOCAL_AGENTIC_IMAGE}" "${AGENTIC_IMAGE}"
        else
            echo "[deploy-k8s.sh] Missing image ${AGENTIC_IMAGE}" >&2
            echo "[deploy-k8s.sh] Run ./scripts/build-images.sh first." >&2
            exit 1
        fi
    fi

    if ! docker image inspect "${TFS_MCP_IMAGE}" >/dev/null 2>&1; then
        if docker image inspect "${DEFAULT_LOCAL_TFS_MCP_IMAGE}" >/dev/null 2>&1; then
            echo "[deploy-k8s.sh] Tagging ${DEFAULT_LOCAL_TFS_MCP_IMAGE} as ${TFS_MCP_IMAGE}"
            docker tag "${DEFAULT_LOCAL_TFS_MCP_IMAGE}" "${TFS_MCP_IMAGE}"
        else
            echo "[deploy-k8s.sh] Missing image ${TFS_MCP_IMAGE}" >&2
            echo "[deploy-k8s.sh] Run ./scripts/build-images.sh first." >&2
            exit 1
        fi
    fi
fi

bash "${SCRIPT_DIR}/load-k8s-images.sh"

kubectl create namespace "${NAMESPACE}" --dry-run=client -o yaml | kubectl apply -f -
kubectl -n "${NAMESPACE}" create configmap tfs-agentic-config --from-env-file="${TMP_DIR}/config.env" --dry-run=client -o yaml | kubectl apply -f -
kubectl -n "${NAMESPACE}" create secret generic tfs-agentic-secret --from-env-file="${TMP_DIR}/secret.env" --dry-run=client -o yaml | kubectl apply -f -
# Apply all manifests except ollama-service.yaml (which needs IP substitution)
find "${MANIFEST_DIR}" -name "*.yaml" ! -name "ollama-service.yaml" -exec kubectl -n "${NAMESPACE}" apply -f {} \;
# Apply ollama-service.yaml with OLLAMA_HOST_IP substituted
OLLAMA_HOST_IP="${OLLAMA_HOST_IP}" envsubst '${OLLAMA_HOST_IP}' < "${MANIFEST_DIR}/ollama-service.yaml" | kubectl -n "${NAMESPACE}" apply -f -
kubectl -n "${NAMESPACE}" set image deployment/agentic-module agentic-module="${AGENTIC_IMAGE}"
kubectl -n "${NAMESPACE}" set image deployment/tfs-mcp-server tfs-mcp-server="${TFS_MCP_IMAGE}"
if [[ "${K8S_IMAGE_LOAD_MODE}" == "registry" ]]; then
    kubectl -n "${NAMESPACE}" patch deployment/agentic-module \
        -p '{"spec":{"template":{"spec":{"containers":[{"name":"agentic-module","imagePullPolicy":"Always"}]}}}}'
    kubectl -n "${NAMESPACE}" patch deployment/tfs-mcp-server \
        -p '{"spec":{"template":{"spec":{"containers":[{"name":"tfs-mcp-server","imagePullPolicy":"Always"}]}}}}'
fi
kubectl -n "${NAMESPACE}" rollout restart deployment/agentic-module deployment/tfs-mcp-server
kubectl -n "${NAMESPACE}" rollout status deployment/tfs-mcp-server --timeout=180s
kubectl -n "${NAMESPACE}" rollout status deployment/agentic-module --timeout=300s

echo "Kubernetes deployment ready in namespace ${NAMESPACE}."
echo "Port-forward the agent with: kubectl -n ${NAMESPACE} port-forward svc/agentic-module 8800:8800"
