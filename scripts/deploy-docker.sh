#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${ROOT_DIR}/.env"
COMPOSE_FILE="${ROOT_DIR}/devops/docker/docker-compose.yml"

if [[ ! -f "${ENV_FILE}" ]]; then
    echo "[deploy-docker.sh] Missing ${ENV_FILE}" >&2
    echo "[deploy-docker.sh] Copy .env.example to .env and fill in your local values." >&2
    exit 1
fi

set -a
source "${ENV_FILE}"
set +a

IMAGE_BUILD_ENABLED="${IMAGE_BUILD_ENABLED:-true}"

if [[ "${IMAGE_BUILD_ENABLED}" == "true" ]]; then
    "${SCRIPT_DIR}/build-images.sh"
fi

cd "${ROOT_DIR}"
exec docker compose -f "${COMPOSE_FILE}" --env-file "${ENV_FILE}" up -d --no-build "$@"
