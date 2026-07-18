from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional

from .dependencies import (
    LOGGER,
    MCP_AVAILABLE,
    Server,
    SseServerTransport,
    TextContent,
    format_response,
    stdio_server,
)
from .store import DummyInventoryStore
from .tool_definitions import TOOLS


class DummyTFSMCPServer:
    def __init__(
        self,
        transport: str = "sse",
        port: int = 3010,
        topology_path: Optional[str] = None,
    ):
        if not MCP_AVAILABLE:
            raise ImportError("MCP SDK is required. Install with: pip install mcp")
        self.transport = transport
        self.port = port
        self.store = DummyInventoryStore(topology_path=topology_path)
        self.mcp = Server("dummy-tfs-mcp-server")
        self._register_handlers()
        LOGGER.info(
            "DummyTFSMCPServer initialized (transport=%s, port=%s, topology=%s)",
            self.transport,
            self.port,
            self.store.topology_path,
        )

    def _register_handlers(self) -> None:
        @self.mcp.list_tools()
        async def list_tools() -> List[Any]:
            return TOOLS

        @self.mcp.call_tool()
        async def call_tool(name: str, arguments: Dict[str, Any]) -> List[Any]:
            try:
                result = self._handle_tool_call(name, arguments or {})
            except Exception as error:  # pylint: disable=broad-except
                LOGGER.exception("Dummy tool call failed: %s", name)
                result = format_response({"status": "error", "error": str(error), "tool": name})
            return [TextContent(type="text", text=result)]

    def _handle_tool_call(self, name: str, args: Dict[str, Any]) -> str:
        if name == "tfs_health_check":
            return format_response({"status": "healthy", "server": "dummy-tfs-mcp"})
        if name == "tfs_list_context_ids":
            return format_response(self.store.list_context_ids())
        if name == "tfs_list_contexts":
            return format_response(self.store.list_contexts())
        if name == "tfs_get_dummy_contexts":
            return format_response(self.store.get_dummy_contexts())
        if name == "tfs_get_context":
            return format_response(self.store.get_context(args["context_uuid"]))
        if name == "tfs_list_topology_ids":
            return format_response(self.store.list_topology_ids(args["context_uuid"]))
        if name == "tfs_list_topologies":
            return format_response(self.store.list_topologies(args["context_uuid"]))
        if name == "tfs_get_topology":
            return format_response(self.store.get_topology(args["context_uuid"], args["topology_uuid"]))
        if name == "tfs_get_topology_details":
            return format_response(self.store.get_topology_details(args["context_uuid"], args["topology_uuid"]))
        if name == "tfs_list_device_ids":
            return format_response(self.store.list_device_ids())
        if name == "tfs_list_devices":
            return format_response(self.store.list_devices())
        if name == "tfs_get_device":
            return format_response(self.store.get_device(args["device_uuid"]))
        if name == "tfs_list_link_ids":
            return format_response(self.store.list_link_ids())
        if name == "tfs_list_links":
            return format_response(self.store.list_links())
        if name == "tfs_get_link":
            return format_response(self.store.get_link(args["link_uuid"]))
        return json.dumps({"status": "error", "error": f"Unknown tool: {name}"})

    async def _run_sse_server(self) -> None:
        from starlette.applications import Starlette
        from starlette.responses import JSONResponse, Response
        from starlette.routing import Mount, Route
        import uvicorn

        sse_transport = SseServerTransport("/messages/")

        async def handle_sse(request):
            async with sse_transport.connect_sse(
                request.scope, request.receive, request._send,
            ) as streams:
                await self.mcp.run(
                    streams[0], streams[1], self.mcp.create_initialization_options(),
                )
            return Response()

        async def health(request):
            return JSONResponse(
                {
                    "status": "healthy",
                    "server": "dummy-tfs-mcp",
                    "contexts": len(self.store.contexts),
                    "topologies": len(self.store.topologies),
                    "devices": len(self.store.devices),
                    "links": len(self.store.links),
                }
            )

        app = Starlette(
            routes=[
                Route("/sse", endpoint=handle_sse),
                Mount("/messages/", app=sse_transport.handle_post_message),
                Route("/health", endpoint=health),
            ]
        )
        config = uvicorn.Config(app, host="0.0.0.0", port=self.port, log_level="info")
        server = uvicorn.Server(config)
        await server.serve()

    async def _run_stdio_server(self) -> None:
        async with stdio_server() as (read_stream, write_stream):
            await self.mcp.run(
                read_stream, write_stream, self.mcp.create_initialization_options(),
            )
