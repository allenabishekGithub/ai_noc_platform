# test_multi_smart

Small isolated prototype to validate a LangChain-based "many small agents"
architecture instead of one giant generalist agent.

What it demonstrates:

- dynamic agent-spec generation from natural-language prompts
- persistence of agent specs and agent instances in SQLite
- per-agent memory with message history, fact storage, and rolling summaries
- a persisted session blackboard with active-specialist continuity for follow-up turns
- a persisted knowledge layer for domain facts, dialogue patterns, and verifier patterns
- planner-driven reuse of an existing specialist or creation of a new one
- optional MCP-backed controller inventory access through the official Python `mcp` SDK
- a response-quality verifier that checks draft answers and can force one refinement pass before replying
- a single-input web UI that submits a user request and shows `Processing...` while the planner/executor runs
- per-request compact horizontal execution graphs rendered inline with the conversation
- explicit plan review before execution, with verification steps included in the plan
- parallel inventory discovery branches when the plan needs independent MCP fetches such as devices and links
- standalone containerization with `docker run`

Default model split:

- planner/spec generation: `qwen2.5:7b-instruct`
- regular specialist runtime: `qwen2.5:3b-instruct`
- light specialists or summaries: `qwen2.5:1.5b-instruct`

## Run locally

```bash
cd test_multi_smart
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

export OLLAMA_BASE_URL=http://10.1.1.89:11434
uvicorn app.main:app --host 0.0.0.0 --port 8787
```

Open:

```text
http://localhost:8787
```

UI behavior:

- type one request in the single input box and submit it
- press `Ctrl+Enter` to submit from the keyboard
- the UI shows a temporary `Processing...` assistant message until a reply is ready
- each conversation turn keeps its own compact execution graph by polling `/api/executions/<id>`
- the planner expands a full plan first, then reviews it before running any task steps
- the execution graph includes a verifier node that approves or rejects the draft answer
- nodes render only the short label; hover a node to inspect details in a tooltip
- independent discovery work can branch in parallel and then rejoin before answer composition
- the sidebar keeps showing persisted specs and instantiated specialists as the planner creates or reuses them
- follow-up cues such as `go ahead` and challenge cues such as `are you sure?` are resolved from the knowledge layer rather than fixed code literals

MCP behavior:

- if `MCP_SERVERS` is configured, specialists receive MCP-backed tools discovered dynamically from the MCP server schemas
- discovered tools are exposed to the agent as `mcp__<server_tool_name>`
- the deterministic fast path still uses a small subset of known TFS inventory tools for grounded list and topology answers
- default controller facts such as the default context and topology are stored in the knowledge layer
- MCP tool calls appear in the execution graph like any other tool step

## Run with Docker

Build:

```bash
docker build -t multi-smart:test .
```

Run:

```bash
docker run --rm \
  -p 8787:8787 \
  --add-host=host.docker.internal:host-gateway \
  -e OLLAMA_BASE_URL=http://10.1.1.89:11434 \
  -e PLANNER_MODEL=qwen2.5:7b-instruct \
  -e DEFAULT_AGENT_MODEL=qwen2.5:3b-instruct \
  -e SUMMARY_MODEL=qwen2.5:1.5b-instruct \
  -e MCP_SERVERS=http://host.docker.internal:3010/sse \
  -v multi-smart-data:/data \
  multi-smart:test
```

The SQLite database is stored at `/data/multi_smart.db` inside the container.

To inspect it directly from the host, bind-mount a local directory instead of a
named volume:

```bash
mkdir -p ./runtime
docker run --rm \
  -p 8787:8787 \
  -e OLLAMA_BASE_URL=http://10.1.1.89:11434 \
  -e PLANNER_MODEL=qwen2.5:7b-instruct \
  -e DEFAULT_AGENT_MODEL=qwen2.5:3b-instruct \
  -e SUMMARY_MODEL=qwen2.5:1.5b-instruct \
  -v "$PWD/runtime:/data" \
  multi-smart:test
```

Then the host will contain:

- `runtime/multi_smart.db`
- `runtime/multi_smart.db-wal`
- `runtime/multi_smart.db-shm`

You can inspect the database with `sqlite3`, DB Browser for SQLite, or other
SQLite tools while the container is running.

Read-only monitoring endpoints are also available:

- `GET /api/admin/sqlite/status`
- `GET /api/admin/sqlite/table/agent_specs`
- `GET /api/admin/sqlite/table/agent_instances`
- `GET /api/admin/sqlite/table/messages`
- `GET /api/admin/sqlite/table/facts`
- `GET /api/admin/mcp/status`
- `GET /api/admin/session/default`
- `GET /api/admin/knowledge`
