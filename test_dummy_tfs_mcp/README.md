# test_dummy_tfs_mcp

Small standalone dummy MCP server that emulates a minimal TFS inventory.

Scope:

- contexts
- topologies
- devices
- links
- health

It preloads the inventory from [docs/dummy_contexts.json](/home/tfs/tfs-agentic/docs/dummy_contexts.json) on startup and serves it through an MCP SSE endpoint.

## Run

```bash
cd test_dummy_tfs_mcp
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py --port 3010
```

Endpoints:

- SSE: `http://localhost:3010/sse`
- health: `http://localhost:3010/health`

## Run with Docker

```bash
docker build -t dummy-tfs-mcp:test -f test_dummy_tfs_mcp/Dockerfile .
docker run --rm -p 3010:3010 dummy-tfs-mcp:test
```

## Tools

- `tfs_health_check`
- `tfs_list_context_ids`
- `tfs_list_contexts`
- `tfs_get_dummy_contexts`
- `tfs_get_context`
- `tfs_list_topology_ids`
- `tfs_list_topologies`
- `tfs_get_topology`
- `tfs_get_topology_details`
- `tfs_list_device_ids`
- `tfs_list_devices`
- `tfs_get_device`
- `tfs_list_link_ids`
- `tfs_list_links`
- `tfs_get_link`

## Use with the main agent

Point `MCP_SERVERS` at:

```text
http://localhost:3010/sse
```
