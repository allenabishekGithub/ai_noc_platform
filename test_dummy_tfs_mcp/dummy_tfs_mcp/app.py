import argparse
import asyncio
import os

from .server import DummyTFSMCPServer
from .tool_definitions import TOOLS


def parse_args():
    parser = argparse.ArgumentParser(description="Dummy TFS MCP Server")
    parser.add_argument("--transport", choices=["stdio", "sse"], default="sse")
    parser.add_argument("--port", type=int, default=int(os.environ.get("DUMMY_TFS_MCP_PORT", "3010")))
    parser.add_argument(
        "--topology-path",
        default=os.environ.get("DUMMY_TFS_TOPOLOGY_PATH"),
        help="Optional path to preload inventory JSON. Defaults to docs/dummy_contexts.json",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    server = DummyTFSMCPServer(
        transport=args.transport,
        port=args.port,
        topology_path=args.topology_path,
    )
    if args.transport == "stdio":
        asyncio.run(server._run_stdio_server())
    else:
        print(f"\nStarting Dummy TFS MCP Server (SSE) on port {args.port}")
        print(f"SSE endpoint: http://localhost:{args.port}/sse")
        print(f"Inventory source: {server.store.topology_path}")
        print(f"\nAvailable Tools ({len(TOOLS)}):")
        for tool in TOOLS:
            print(f"  - {tool.name}")
        print("\nPress Ctrl+C to stop\n")
        try:
            asyncio.run(server._run_sse_server())
        except KeyboardInterrupt:
            print("\nShutting down...")
