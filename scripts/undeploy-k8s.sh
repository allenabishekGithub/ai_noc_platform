#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
MANIFEST_DIR="${ROOT_DIR}/devops/kubernetes"
NAMESPACE="${K8S_NAMESPACE:-tfs-agentic}"
FAISS_PVC_NAME="${FAISS_PVC_NAME:-faiss-index-pvc}"

kubectl -n "${NAMESPACE}" delete -f "${MANIFEST_DIR}" --ignore-not-found=true
kubectl -n "${NAMESPACE}" delete pvc "${FAISS_PVC_NAME}" --ignore-not-found=true
kubectl -n "${NAMESPACE}" delete configmap tfs-agentic-config --ignore-not-found=true
kubectl -n "${NAMESPACE}" delete secret tfs-agentic-secret --ignore-not-found=true
kubectl delete namespace "${NAMESPACE}" --ignore-not-found=true
