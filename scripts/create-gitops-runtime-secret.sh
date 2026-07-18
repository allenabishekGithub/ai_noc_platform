#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${ROOT_DIR}/.env"
NAMESPACE="${K8S_NAMESPACE:-tfs-agentic}"
SECRET_NAME="${GITOPS_RUNTIME_SECRET:-tfs-agentic-runtime}"
KUBECTL_BIN="${KUBECTL_BIN:-microk8s kubectl}"

if [[ ! -f "${ENV_FILE}" ]]; then
    echo "[create-gitops-runtime-secret.sh] Missing ${ENV_FILE}" >&2
    exit 1
fi

set -a
# shellcheck disable=SC1090
source "${ENV_FILE}"
set +a

# The Secret is intentionally created outside Git and is only referenced by
# the Argo CD Application. Re-running this command updates the same Secret.
# shellcheck disable=SC2086
${KUBECTL_BIN} create namespace "${NAMESPACE}" --dry-run=client -o yaml | ${KUBECTL_BIN} apply -f -
# shellcheck disable=SC2086
${KUBECTL_BIN} -n "${NAMESPACE}" create secret generic "${SECRET_NAME}" \
    --from-literal="TFS_NBI_USER=${TFS_NBI_USER:-admin}" \
    --from-literal="TFS_NBI_PASS=${TFS_NBI_PASS:-admin}" \
    --from-literal="OPENAI_API_KEY=${OPENAI_API_KEY:-}" \
    --from-literal="ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY:-}" \
    --from-literal="OPENWEBUI_EMAIL=${OPENWEBUI_EMAIL:-}" \
    --from-literal="OPENWEBUI_PASSWORD=${OPENWEBUI_PASSWORD:-}" \
    --dry-run=client -o yaml | ${KUBECTL_BIN} apply -f -

echo "Secret '${SECRET_NAME}' is ready in namespace '${NAMESPACE}'."
