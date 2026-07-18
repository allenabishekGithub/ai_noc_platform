#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
GITOPS_DIR="${ROOT_DIR}/devops/gitops/argocd"
ARGOCD_NAMESPACE="${ARGOCD_NAMESPACE:-argocd}"
ARGOCD_VERSION="${ARGOCD_VERSION:-v3.4.2}"
KUBECTL_BIN="${KUBECTL_BIN:-microk8s kubectl}"

if [[ ! -f "${GITOPS_DIR}/application.yaml" ]]; then
    echo "[bootstrap-argocd.sh] GitOps definitions not found: ${GITOPS_DIR}" >&2
    exit 1
fi

# Install a pinned non-HA Argo CD release. The upstream documentation
# recommends server-side apply because some Argo CD CRDs exceed the client-side
# apply annotation-size limit.
# shellcheck disable=SC2086
${KUBECTL_BIN} create namespace "${ARGOCD_NAMESPACE}" --dry-run=client -o yaml | ${KUBECTL_BIN} apply -f -
# shellcheck disable=SC2086
${KUBECTL_BIN} apply --server-side --force-conflicts -n "${ARGOCD_NAMESPACE}" \
    -f "https://raw.githubusercontent.com/argoproj/argo-cd/${ARGOCD_VERSION}/manifests/install.yaml"
# shellcheck disable=SC2086
${KUBECTL_BIN} -n "${ARGOCD_NAMESPACE}" rollout status deployment/argocd-server --timeout="${ARGOCD_TIMEOUT:-5m}"
# shellcheck disable=SC2086
${KUBECTL_BIN} -n "${ARGOCD_NAMESPACE}" rollout status deployment/argocd-repo-server --timeout="${ARGOCD_TIMEOUT:-5m}"

# The repo must already contain and expose this commit on origin/main. Argo CD
# will use the Application to render the Helm chart and reconcile it from Git.
# shellcheck disable=SC2086
${KUBECTL_BIN} apply -f "${GITOPS_DIR}/project.yaml"
# shellcheck disable=SC2086
${KUBECTL_BIN} apply -f "${GITOPS_DIR}/application.yaml"

echo "Argo CD bootstrap complete."
echo "Get the initial password:"
echo "  ${KUBECTL_BIN} -n ${ARGOCD_NAMESPACE} get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d; echo"
echo "Open the UI with:"
echo "  ${KUBECTL_BIN} -n ${ARGOCD_NAMESPACE} port-forward svc/argocd-server 8080:443"
