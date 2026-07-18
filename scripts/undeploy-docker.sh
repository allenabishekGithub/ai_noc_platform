#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${ROOT_DIR}/.env"
COMPOSE_FILE="${ROOT_DIR}/devops/docker/docker-compose.yml"

cd "${ROOT_DIR}"
exec docker compose -f "${COMPOSE_FILE}" --env-file "${ENV_FILE}" down "$@"
