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
KIND_CLUSTER_NAME="${KIND_CLUSTER_NAME:-kind}"
K8S_IMAGE_REGISTRY="${K8S_IMAGE_REGISTRY:-localhost:32000}"

if [[ "${K8S_IMAGE_LOAD_MODE}" == "registry" ]]; then
    AGENTIC_IMAGE="${K8S_AGENTIC_IMAGE:-${K8S_IMAGE_REGISTRY}/tfs-agentic-agentic-module:dev}"
    TFS_MCP_IMAGE="${K8S_TFS_MCP_IMAGE:-${K8S_IMAGE_REGISTRY}/tfs-agentic-tfs-mcp-server:dev}"
fi

case "${K8S_IMAGE_LOAD_MODE}" in
    none)
        echo "[load-k8s-images.sh] Skipping image load. Ensure your cluster can pull or access:"
        echo "- ${AGENTIC_IMAGE}"
        echo "- ${TFS_MCP_IMAGE}"
        ;;
    kind)
        kind load docker-image "${AGENTIC_IMAGE}" "${TFS_MCP_IMAGE}" --name "${KIND_CLUSTER_NAME}"
        ;;
    minikube)
        minikube image load "${AGENTIC_IMAGE}" "${TFS_MCP_IMAGE}"
        ;;
    registry)
        docker push "${AGENTIC_IMAGE}"
        docker push "${TFS_MCP_IMAGE}"
        echo "[load-k8s-images.sh] Pushed images to registry ${K8S_IMAGE_REGISTRY}"
        ;;
    *)
        echo "[load-k8s-images.sh] Unsupported K8S_IMAGE_LOAD_MODE=${K8S_IMAGE_LOAD_MODE}" >&2
        exit 1
        ;;
esac
