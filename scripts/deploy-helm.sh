#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
CHART_DIR="${ROOT_DIR}/devops/helm/tfs-agentic"
ENV_FILE="${ROOT_DIR}/.env"
NAMESPACE="${K8S_NAMESPACE:-tfs-agentic}"
RELEASE="${HELM_RELEASE:-tfs-agentic}"
HELM_BIN="${HELM_BIN:-microk8s helm3}"

if [[ ! -d "${CHART_DIR}" ]]; then
    echo "[deploy-helm.sh] Helm chart not found: ${CHART_DIR}" >&2
    exit 1
fi

if [[ -f "${ENV_FILE}" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "${ENV_FILE}"
    set +a
fi

K8S_IMAGE_LOAD_MODE="${K8S_IMAGE_LOAD_MODE:-none}"
K8S_IMAGE_REGISTRY="${K8S_IMAGE_REGISTRY:-localhost:32000}"
if [[ "${K8S_IMAGE_LOAD_MODE}" == "registry" ]]; then
    AGENTIC_IMAGE="${AGENTIC_IMAGE:-${K8S_AGENTIC_IMAGE:-${K8S_IMAGE_REGISTRY}/tfs-agentic-agentic-module:dev}}"
    TFS_MCP_IMAGE="${TFS_MCP_IMAGE:-${K8S_TFS_MCP_IMAGE:-${K8S_IMAGE_REGISTRY}/tfs-agentic-tfs-mcp-server:dev}}"
else
    AGENTIC_IMAGE="${AGENTIC_IMAGE:-${K8S_AGENTIC_IMAGE:-tfs-agentic-agentic-module:dev}}"
    TFS_MCP_IMAGE="${TFS_MCP_IMAGE:-${K8S_TFS_MCP_IMAGE:-tfs-agentic-tfs-mcp-server:dev}}"
fi

image_repository() { printf '%s' "${1%:*}"; }
image_tag() { printf '%s' "${1##*:}"; }

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
OPENAI_BASE_URL="${OPENAI_BASE_URL:-}"
OPENWEBUI_BASE_URL="${OPENWEBUI_BASE_URL:-}"
AGENT_PUBLIC_URL="${AGENT_PUBLIC_URL:-}"

HELM_ARGS=(
    --namespace "${NAMESPACE}"
    --create-namespace
    --set-string "agent.image.repository=$(image_repository "${AGENTIC_IMAGE}")"
    --set-string "agent.image.tag=$(image_tag "${AGENTIC_IMAGE}")"
    --set-string "mcp.image.repository=$(image_repository "${TFS_MCP_IMAGE}")"
    --set-string "mcp.image.tag=$(image_tag "${TFS_MCP_IMAGE}")"
    --set-string "config.agentApiHost=${AGENT_API_HOST}"
    --set-string "config.agentApiPort=${AGENT_API_PORT}"
    --set-string "config.faissIndexPath=${FAISS_INDEX_PATH}"
    --set-string "config.llmProvider=${LLM_PROVIDER}"
    --set-string "config.llmModel=${LLM_MODEL}"
    --set-string "config.ollamaBaseUrl=${OLLAMA_BASE_URL}"
    --set-string "config.ollamaNumCtx=${OLLAMA_NUM_CTX}"
    --set-string "config.llmTemperature=${LLM_TEMPERATURE}"
    --set-string "config.embeddingModel=${EMBEDDING_MODEL}"
    --set-string "config.agentVerbose=${AGENT_VERBOSE}"
    --set-string "config.agentMaxIterations=${AGENT_MAX_ITERATIONS}"
    --set-string "config.agentExposeRawMcpTools=${AGENT_EXPOSE_RAW_MCP_TOOLS}"
    --set-string "config.mcpHardResponseCap=${MCP_HARD_RESPONSE_CAP}"
    --set-string "config.a2aPeers=${A2A_PEERS}"
    --set-string "config.defaultContextUuid=${DEFAULT_CONTEXT_UUID}"
    --set-string "config.defaultTopologyUuid=${DEFAULT_TOPOLOGY_UUID}"
    --set-string "config.controllerCacheTtlSeconds=${CONTROLLER_CACHE_TTL_SECONDS}"
    --set-string "config.tfsNbiUrl=${TFS_NBI_URL}"
    --set-string "config.tfsNbiPrefix=${TFS_NBI_PREFIX}"
    --set-string "config.tfsMcpPort=${TFS_MCP_PORT}"
    --set-string "config.openaiBaseUrl=${OPENAI_BASE_URL}"
    --set-string "config.openwebuiBaseUrl=${OPENWEBUI_BASE_URL}"
    --set-string "config.agentPublicUrl=${AGENT_PUBLIC_URL}"
    --wait
    --timeout "${HELM_TIMEOUT:-5m}"
)

# Supply an environment-specific values file for ingress, persistence, or
# resource settings. Do not commit files containing credentials.
if [[ -n "${HELM_VALUES_FILE:-}" ]]; then
    HELM_ARGS+=(--values "${HELM_VALUES_FILE}")
fi

# An externally managed Secret is preferred in production. If it is absent,
# preserve the existing .env workflow by creating the release Secret from its
# values. Helm stores release values, so use an external secret for real keys.
if [[ -n "${HELM_EXISTING_SECRET:-}" ]]; then
    HELM_ARGS+=(--set-string "secrets.existingSecret=${HELM_EXISTING_SECRET}")
else
    HELM_ARGS+=(
        --set-string "secrets.tfsNbiUser=${TFS_NBI_USER:-admin}"
        --set-string "secrets.tfsNbiPass=${TFS_NBI_PASS:-admin}"
        --set-string "secrets.openaiApiKey=${OPENAI_API_KEY:-}"
        --set-string "secrets.anthropicApiKey=${ANTHROPIC_API_KEY:-}"
        --set-string "secrets.openwebuiEmail=${OPENWEBUI_EMAIL:-}"
        --set-string "secrets.openwebuiPassword=${OPENWEBUI_PASSWORD:-}"
    )
fi

# Override the in-release MCP endpoint only for a deliberately external MCP
# server. K8S_MCP_SERVERS from the old manifest deployment is intentionally
# ignored because its fixed service name is no longer valid.
if [[ -n "${HELM_MCP_SERVERS:-}" ]]; then
    HELM_ARGS+=(--set-string "config.mcpServers=${HELM_MCP_SERVERS}")
fi

if [[ "${BUILD_IMAGES:-false}" == "true" ]]; then
    "${SCRIPT_DIR}/build-images.sh"
fi
if [[ "${LOAD_IMAGES:-true}" == "true" ]]; then
    "${SCRIPT_DIR}/load-k8s-images.sh"
fi

# shellcheck disable=SC2086
${HELM_BIN} lint "${CHART_DIR}"
# shellcheck disable=SC2086
${HELM_BIN} upgrade --install "${RELEASE}" "${CHART_DIR}" "${HELM_ARGS[@]}"

echo "Helm release '${RELEASE}' is ready in namespace '${NAMESPACE}'."
echo "Port-forward with: microk8s kubectl -n ${NAMESPACE} port-forward service/${RELEASE}-agent 8800:8800"
