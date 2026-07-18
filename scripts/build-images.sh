#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${ROOT_DIR}/.env"

if [[ -f "${ENV_FILE}" ]]; then
    set -a
    source "${ENV_FILE}"
    set +a
fi

AGENTIC_IMAGE="${AGENTIC_IMAGE:-tfs-agentic-agentic-module:dev}"
TFS_MCP_IMAGE="${TFS_MCP_IMAGE:-tfs-agentic-tfs-mcp-server:dev}"
K8S_IMAGE_LOAD_MODE="${K8S_IMAGE_LOAD_MODE:-none}"
K8S_IMAGE_REGISTRY="${K8S_IMAGE_REGISTRY:-localhost:32000}"
PRUNE_DANGLING_IMAGES="${PRUNE_DANGLING_IMAGES:-true}"

# Always build local images first; deploy-time loaders can retag/push as needed.
LOCAL_AGENTIC_IMAGE="tfs-agentic-agentic-module:dev"
LOCAL_TFS_MCP_IMAGE="tfs-agentic-tfs-mcp-server:dev"

if [[ "${K8S_IMAGE_LOAD_MODE}" == "registry" ]]; then
    AGENTIC_IMAGE="${K8S_AGENTIC_IMAGE:-${K8S_IMAGE_REGISTRY}/tfs-agentic-agentic-module:dev}"
    TFS_MCP_IMAGE="${K8S_TFS_MCP_IMAGE:-${K8S_IMAGE_REGISTRY}/tfs-agentic-tfs-mcp-server:dev}"
fi

cd "${ROOT_DIR}"
docker build -f devops/docker/agentic-module.Dockerfile -t "${LOCAL_AGENTIC_IMAGE}" .
docker build -f devops/docker/tfs-mcp-server.Dockerfile -t "${LOCAL_TFS_MCP_IMAGE}" .

if [[ "${AGENTIC_IMAGE}" != "${LOCAL_AGENTIC_IMAGE}" ]]; then
    docker tag "${LOCAL_AGENTIC_IMAGE}" "${AGENTIC_IMAGE}"
fi

if [[ "${TFS_MCP_IMAGE}" != "${LOCAL_TFS_MCP_IMAGE}" ]]; then
    docker tag "${LOCAL_TFS_MCP_IMAGE}" "${TFS_MCP_IMAGE}"
fi

echo "Built images:"
echo "- ${LOCAL_AGENTIC_IMAGE}"
echo "- ${LOCAL_TFS_MCP_IMAGE}"
echo "Deploy target tags:"
echo "- ${AGENTIC_IMAGE}"
echo "- ${TFS_MCP_IMAGE}"

if [[ "${PRUNE_DANGLING_IMAGES}" == "true" ]]; then
    # Keep the current tags, remove old untagged image leftovers from repeated builds.
    docker image prune -f >/dev/null
    echo "Pruned dangling images."
fi
