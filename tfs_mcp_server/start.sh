#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# start.sh - Launch the TFS MCP Server
# -----------------------------------------------------------------------------
# Starts the MCP server defined in main.py. The server bridges MCP
# clients (e.g. the TFS NAF agent) to the TFS controller's NBI REST API.
#
# Environment variables (override defaults):
#   TFS_MCP_TRANSPORT   "sse" | "stdio"           (default: sse)
#   TFS_MCP_PORT        SSE listen port           (default: 3002)
#   TFS_NBI_URL         TFS NBI base URL          (default: http://127.0.0.1:80)
#   TFS_NBI_PREFIX      TFS NBI URL prefix        (default: /tfs-api)
#   TFS_NBI_USER        Optional HTTP basic user
#   TFS_NBI_PASS        Optional HTTP basic pass
#   TFS_MCP_INSECURE    "1" to skip TLS verify    (default: unset)
#   VENV_DIR            Path to virtualenv        (default: ./mcp_server/.venv)
#   SKIP_INSTALL        "1" to skip pip install   (default: unset)
#
# This component is fully standalone: it creates and manages its own
# virtualenv inside the mcp_server/ directory and installs the dependencies
# listed in mcp_server/requirements.txt - independent of the NAF agent.
#
# Usage:
#   ./start.sh                          # SSE on :3002
#   TFS_MCP_PORT=4000 ./start.sh        # SSE on :4000
#   TFS_MCP_TRANSPORT=stdio ./start.sh  # stdio mode
# -----------------------------------------------------------------------------
set -euo pipefail

# Resolve script & project directories
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
REQUIREMENTS_FILE="${SCRIPT_DIR}/requirements.txt"

# Load environment variables from local .env if present.
if [[ -f "${SCRIPT_DIR}/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "${SCRIPT_DIR}/.env"
    set +a
fi

# Defaults
TRANSPORT="${TFS_MCP_TRANSPORT:-sse}"
PORT="${TFS_MCP_PORT:-3002}"
API_URL="${TFS_NBI_URL:-http://127.0.0.1:80}"
API_PREFIX="${TFS_NBI_PREFIX:-/tfs-api}"
VENV_DIR="${VENV_DIR:-${SCRIPT_DIR}/.venv}"

# Locate base python (used to bootstrap the venv if needed)
BASE_PYTHON="${PYTHON_BIN:-python3}"
if ! command -v "${BASE_PYTHON}" >/dev/null 2>&1; then
    echo "[start.sh] ERROR: python interpreter '${BASE_PYTHON}' not found" >&2
    exit 1
fi

# Create dedicated virtualenv if missing
if [[ ! -f "${VENV_DIR}/bin/activate" ]]; then
    echo "[start.sh] Creating virtualenv at ${VENV_DIR}"
    "${BASE_PYTHON}" -m venv "${VENV_DIR}"
fi

# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate"
echo "[start.sh] Using venv: ${VENV_DIR}"

PYTHON_BIN="${VENV_DIR}/bin/python"

# Install dependencies from this component's requirements.txt
if [[ "${SKIP_INSTALL:-0}" != "1" ]]; then
    if ! "${PYTHON_BIN}" -c "import mcp, httpx, uvicorn, starlette, dotenv" >/dev/null 2>&1; then
        echo "[start.sh] Installing dependencies from ${REQUIREMENTS_FILE}"
        "${PYTHON_BIN}" -m pip install --quiet --upgrade pip
        "${PYTHON_BIN}" -m pip install --quiet -r "${REQUIREMENTS_FILE}"
    fi
fi

# Build CLI args
ARGS=(
    --transport "${TRANSPORT}"
    --port "${PORT}"
    --api-url "${API_URL}"
    --api-prefix "${API_PREFIX}"
)
[[ -n "${TFS_NBI_USER:-}" ]] && ARGS+=(--username "${TFS_NBI_USER}")
[[ -n "${TFS_NBI_PASS:-}" ]] && ARGS+=(--password "${TFS_NBI_PASS}")
[[ "${TFS_MCP_INSECURE:-0}" == "1" ]] && ARGS+=(--insecure)

cd "${SCRIPT_DIR}"

echo "[start.sh] Launching TFS MCP Server"
echo "           transport : ${TRANSPORT}"
echo "           port      : ${PORT}"
echo "           api url   : ${API_URL}${API_PREFIX}"
[[ "${TRANSPORT}" == "sse" ]] && echo "           sse url   : http://localhost:${PORT}/sse"
echo

exec "${PYTHON_BIN}" "${SCRIPT_DIR}/main.py" "${ARGS[@]}"
