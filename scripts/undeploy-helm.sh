#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="${K8S_NAMESPACE:-tfs-agentic}"
RELEASE="${HELM_RELEASE:-tfs-agentic}"
HELM_BIN="${HELM_BIN:-microk8s helm3}"

# shellcheck disable=SC2086
${HELM_BIN} uninstall "${RELEASE}" --namespace "${NAMESPACE}" --wait || true

if [[ "${HELM_DELETE_NAMESPACE:-false}" == "true" ]]; then
    microk8s kubectl delete namespace "${NAMESPACE}" --ignore-not-found=true
fi

echo "Helm release '${RELEASE}' was removed from namespace '${NAMESPACE}'."
echo "The chart-managed FAISS PVC is also removed by Helm. Set persistence.faiss.existingClaim to preserve an external claim."
