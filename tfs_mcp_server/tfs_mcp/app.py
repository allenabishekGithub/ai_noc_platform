import argparse
import asyncio
import os

from .server import TFSMCPServer
from .tool_definitions import TOOLS


def parse_args():
    parser = argparse.ArgumentParser(description="TFS MCP Server")
    parser.add_argument("--transport", choices=["stdio", "sse"], default="sse",
                        help="Transport type (default: sse)")
    parser.add_argument("--port", type=int, default=int(os.environ.get("TFS_MCP_PORT", 3002)),
                        help="Port for SSE transport (default: 3002)")
    parser.add_argument("--api-url",
                        default=os.environ.get("TFS_NBI_URL", "http://127.0.0.1:80"),
                        help="TFS NBI base URL (default: http://127.0.0.1:80 or $TFS_NBI_URL)")
    parser.add_argument("--api-prefix",
                        default=os.environ.get("TFS_NBI_PREFIX", "/tfs-api"),
                        help="TFS NBI API URL prefix (default: /tfs-api)")
    parser.add_argument("--username", default=os.environ.get("TFS_NBI_USER"),
                        help="Optional HTTP basic-auth username")
    parser.add_argument("--password", default=os.environ.get("TFS_NBI_PASS"),
                        help="Optional HTTP basic-auth password")
    parser.add_argument("--insecure", action="store_true",
                        help="Disable TLS verification (default: disabled)")
    return parser.parse_args()


def main():
    args = parse_args()

    server = TFSMCPServer(
        transport=args.transport,
        port=args.port,
        api_url=args.api_url,
        api_prefix=args.api_prefix,
        username=args.username,
        password=args.password,
        verify_tls=not args.insecure,
    )

    if args.transport == "stdio":
        asyncio.run(server._run_stdio_server())
    else:
        print(f"\nStarting TFS MCP Server (SSE) on port {args.port}")
        print(f"SSE endpoint: http://localhost:{args.port}/sse")
        print(f"TFS NBI URL: {args.api_url}{server.api_prefix}")
        print(f"\nAvailable Tools ({len(TOOLS)}):")
        for tool in TOOLS:
            print(f"  - {tool.name}")
        print("\nPress Ctrl+C to stop\n")
        try:
            asyncio.run(server._run_sse_server())
        except KeyboardInterrupt:
            print("\nShutting down...")


if __name__ == "__main__":
    main()
