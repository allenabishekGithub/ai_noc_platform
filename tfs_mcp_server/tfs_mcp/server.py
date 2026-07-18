import asyncio
import copy
import json
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

from .dependencies import (
    HTTPX_AVAILABLE,
    LOGGER,
    MCP_AVAILABLE,
    Server,
    SseServerTransport,
    TextContent,
    format_response,
    httpx,
    stdio_server,
)
from .tool_definitions import TOOLS
from .observability import instrument_starlette, metrics_response


class TFSMCPServer:
    """MCP Server bridging an MCP client to the TFS NBI REST API."""

    def __init__(
        self,
        transport: str = "sse",
        port: int = 3002,
        api_url: str = "http://127.0.0.1:80",
        api_prefix: str = "/tfs-api",
        username: Optional[str] = None,
        password: Optional[str] = None,
        verify_tls: bool = False,
    ):
        if not MCP_AVAILABLE:
            raise ImportError("MCP SDK is required. Install with: pip install mcp")
        if not HTTPX_AVAILABLE:
            raise ImportError("httpx is required. Install with: pip install httpx")

        self.transport = transport
        self.port = port
        self.api_url = api_url.rstrip('/')
        self.api_prefix = '/' + api_prefix.strip('/')
        self.auth = (username, password) if username and password else None
        self.verify_tls = verify_tls

        self._server_thread: Optional[threading.Thread] = None
        self._should_stop = threading.Event()

        self.mcp = Server("tfs-mcp-server")
        self._register_handlers()

        LOGGER.info(
            "TFSMCPServer initialized (transport=%s, port=%s, api_url=%s, prefix=%s)",
            transport, port, self.api_url, self.api_prefix,
        )

    async def api_request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict] = None,
        json_data: Optional[Any] = None,
        timeout: float = 60.0,
        use_prefix: bool = True,
    ) -> Any:
        """Issue an HTTP request to the TFS NBI."""
        prefix = self.api_prefix if use_prefix else ''
        url = f"{self.api_url}{prefix}{endpoint}"

        async with httpx.AsyncClient(timeout=timeout, verify=self.verify_tls, auth=self.auth) as client:
            try:
                response = await client.request(
                    method=method, url=url, params=params, json=json_data,
                )
                response.raise_for_status()
                if not response.content:
                    return {"status": "ok", "http_status": response.status_code}
                ctype = response.headers.get("content-type", "")
                if "application/json" in ctype:
                    return response.json()
                return {"status": "ok", "body": response.text}
            except httpx.TimeoutException:
                return {"error": f"Request timeout after {timeout}s", "status": "error", "url": url}
            except httpx.HTTPStatusError as error:
                return {
                    "error": f"HTTP {error.response.status_code}: {error.response.text}",
                    "status": "error",
                    "url": url,
                }
            except Exception as error:  # pylint: disable=broad-except
                return {"error": str(error), "status": "error", "url": url}

    @staticmethod
    def _normalize_optical_slice_payload(slice_payload: Any) -> Any:
        """Ensure optical-slice payload has tapi-common:context.uuid.

        - If `tapi-common:context` exists but `uuid` is missing/empty, inject UUID.
        - If it does not exist, create it with a generated UUID.
        Supports payloads where the optical content is directly at root or under `data`.
        """
        if not isinstance(slice_payload, dict):
            return slice_payload

        normalized = copy.deepcopy(slice_payload)
        container = normalized.get("data") if isinstance(normalized.get("data"), dict) else normalized

        context_obj = container.get("tapi-common:context")
        if not isinstance(context_obj, dict):
            container["tapi-common:context"] = {"uuid": str(uuid.uuid4())}
            return normalized

        context_uuid = context_obj.get("uuid")
        if not isinstance(context_uuid, str) or not context_uuid.strip():
            context_obj["uuid"] = str(uuid.uuid4())
        return normalized

    def _register_handlers(self):
        @self.mcp.list_tools()
        async def list_tools() -> List[Any]:
            return TOOLS

        @self.mcp.call_tool()
        async def call_tool(name: str, arguments: Dict[str, Any]) -> List[Any]:
            try:
                result = await self._handle_tool_call(name, arguments or {})
            except Exception as error:  # pylint: disable=broad-except
                LOGGER.exception("Tool call failed: %s", name)
                result = format_response({"error": str(error), "tool": name})
            return [TextContent(type="text", text=result)]

    async def _handle_tool_call(self, name: str, args: Dict[str, Any]) -> str:
        if name == "tfs_list_context_ids":
            return format_response(await self.api_request("GET", "/context_ids"))
        if name == "tfs_list_contexts":
            return format_response(await self.api_request("GET", "/contexts"))
        if name == "tfs_set_contexts":
            return format_response(await self.api_request(
                "POST", "/contexts", json_data={"contexts": args["contexts"]}))
        if name == "tfs_get_dummy_contexts":
            return format_response(await self.api_request("GET", "/dummy_contexts"))
        if name == "tfs_get_context":
            return format_response(await self.api_request("GET", f"/context/{args['context_uuid']}"))
        if name == "tfs_set_context":
            return format_response(await self.api_request(
                "PUT", f"/context/{args['context_uuid']}", json_data=args["context"]))
        if name == "tfs_delete_context":
            return format_response(await self.api_request("DELETE", f"/context/{args['context_uuid']}"))

        if name == "tfs_list_topology_ids":
            return format_response(await self.api_request(
                "GET", f"/context/{args['context_uuid']}/topology_ids"))
        if name == "tfs_list_topologies":
            return format_response(await self.api_request(
                "GET", f"/context/{args['context_uuid']}/topologies"))
        if name == "tfs_set_topologies":
            return format_response(await self.api_request(
                "POST", f"/context/{args['context_uuid']}/topologies",
                json_data={"topologies": args["topologies"]}))
        if name == "tfs_get_topology":
            return format_response(await self.api_request(
                "GET", f"/context/{args['context_uuid']}/topology/{args['topology_uuid']}"))
        if name == "tfs_set_topology":
            return format_response(await self.api_request(
                "PUT", f"/context/{args['context_uuid']}/topology/{args['topology_uuid']}",
                json_data=args["topology"]))
        if name == "tfs_delete_topology":
            return format_response(await self.api_request(
                "DELETE", f"/context/{args['context_uuid']}/topology/{args['topology_uuid']}"))
        if name == "tfs_get_topology_details":
            return format_response(await self.api_request(
                "GET", f"/context/{args['context_uuid']}/topology_details/{args['topology_uuid']}"))

        if name == "tfs_list_service_ids":
            return format_response(await self.api_request(
                "GET", f"/context/{args['context_uuid']}/service_ids"))
        if name == "tfs_list_services":
            return format_response(await self.api_request(
                "GET", f"/context/{args['context_uuid']}/services"))
        if name == "tfs_create_services":
            return format_response(await self.api_request(
                "POST", f"/context/{args['context_uuid']}/services",
                json_data={"services": args["services"]}))
        if name == "tfs_get_service":
            return format_response(await self.api_request(
                "GET", f"/context/{args['context_uuid']}/service/{args['service_uuid']}"))
        if name == "tfs_update_service":
            return format_response(await self.api_request(
                "PUT", f"/context/{args['context_uuid']}/service/{args['service_uuid']}",
                json_data=args["service"]))
        if name == "tfs_delete_service":
            return format_response(await self.api_request(
                "DELETE", f"/context/{args['context_uuid']}/service/{args['service_uuid']}"))

        if name == "tfs_list_slice_ids":
            return format_response(await self.api_request(
                "GET", f"/context/{args['context_uuid']}/slice_ids"))
        if name == "tfs_list_slices":
            return format_response(await self.api_request(
                "GET", f"/context/{args['context_uuid']}/slices"))
        if name == "tfs_create_slices":
            return format_response(await self.api_request(
                "POST", f"/context/{args['context_uuid']}/slices",
                json_data={"slices": args["slices"]}))
        if name == "tfs_get_slice":
            return format_response(await self.api_request(
                "GET", f"/context/{args['context_uuid']}/slice/{args['slice_uuid']}"))
        if name == "tfs_update_slice":
            return format_response(await self.api_request(
                "PUT", f"/context/{args['context_uuid']}/slice/{args['slice_uuid']}",
                json_data=args["slice"]))
        if name == "tfs_delete_slice":
            return format_response(await self.api_request(
                "DELETE", f"/context/{args['context_uuid']}/slice/{args['slice_uuid']}"))

        if name == "tfs_list_device_ids":
            return format_response(await self.api_request("GET", "/device_ids"))
        if name == "tfs_list_devices":
            return format_response(await self.api_request("GET", "/devices"))
        if name == "tfs_add_devices":
            return format_response(await self.api_request(
                "POST", "/devices", json_data={"devices": args["devices"]}))
        if name == "tfs_get_device":
            return format_response(await self.api_request("GET", f"/device/{args['device_uuid']}"))
        if name == "tfs_configure_device":
            return format_response(await self.api_request(
                "PUT", f"/device/{args['device_uuid']}", json_data=args["device"]))
        if name == "tfs_delete_device":
            return format_response(await self.api_request(
                "DELETE", f"/device/{args['device_uuid']}"))

        if name == "tfs_list_link_ids":
            return format_response(await self.api_request("GET", "/link_ids"))
        if name == "tfs_list_links":
            return format_response(await self.api_request("GET", "/links"))
        if name == "tfs_set_links":
            return format_response(await self.api_request(
                "POST", "/links", json_data={"links": args["links"]}))
        if name == "tfs_get_link":
            return format_response(await self.api_request("GET", f"/link/{args['link_uuid']}"))
        if name == "tfs_set_link":
            return format_response(await self.api_request(
                "PUT", f"/link/{args['link_uuid']}", json_data=args["link"]))
        if name == "tfs_delete_link":
            return format_response(await self.api_request(
                "DELETE", f"/link/{args['link_uuid']}"))

        if name == "tfs_list_connection_ids":
            return format_response(await self.api_request(
                "GET", f"/context/{args['context_uuid']}/service/{args['service_uuid']}/connection_ids"))
        if name == "tfs_list_connections":
            return format_response(await self.api_request(
                "GET", f"/context/{args['context_uuid']}/service/{args['service_uuid']}/connections"))
        if name == "tfs_get_connection":
            return format_response(await self.api_request(
                "GET", f"/connection/{args['connection_uuid']}"))

        if name == "tfs_list_policyrule_ids":
            return format_response(await self.api_request("GET", "/policyrule_ids"))
        if name == "tfs_list_policyrules":
            return format_response(await self.api_request("GET", "/policyrules"))
        if name == "tfs_get_policyrule":
            return format_response(await self.api_request(
                "GET", f"/policyrule/{args['policyrule_uuid']}"))

        if name == "tfs_health_check":
            return format_response(await self.api_request("GET", "/healthz", use_prefix=False))

        if name == "tfs_optical_slice_create":
            normalized_slice = self._normalize_optical_slice_payload(args["slice"])
            return format_response(await self.api_request(
                "POST", f"/restconf/optical-slice/v1/service/{args['slice_id']}",
                json_data=normalized_slice, use_prefix=False))
        if name == "tfs_optical_slice_delete":
            return format_response(await self.api_request(
                "DELETE", f"/restconf/optical-slice/v1/service/{args['slice_id']}",
                use_prefix=False))
        if name == "tfs_media_channel_allocate":
            return format_response(await self.api_request(
                "POST", f"/restconf/media-channel/v1/service/{args['allocation_id']}",
                json_data=args["media_channel"], use_prefix=False))
        if name == "tfs_media_channel_release":
            return format_response(await self.api_request(
                "DELETE", f"/restconf/media-channel/v1/service/{args['allocation_id']}",
                use_prefix=False))
        if name == "tfs_dscm_oc_get":
            return format_response(await self.api_request(
                "GET",
                f"/restconf/data/device={args['device_uuid']}/{args['rc_path'].lstrip('/')}",
                use_prefix=False))
        if name == "tfs_dscm_oc_post":
            return format_response(await self.api_request(
                "POST",
                f"/restconf/data/device={args['device_uuid']}/{args['rc_path'].lstrip('/')}",
                json_data=args["config"], use_prefix=False))
        if name == "tfs_dscm_oc_delete":
            return format_response(await self.api_request(
                "DELETE",
                f"/restconf/data/device={args['device_uuid']}/{args['rc_path'].lstrip('/')}",
                use_prefix=False))

        return json.dumps({"error": f"Unknown tool: {name}"})

    async def _run_sse_server(self):
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
            return JSONResponse({"status": "healthy", "server": "tfs-mcp"})

        async def metrics(request):
            return metrics_response()

        app = Starlette(routes=[
            Route("/sse", endpoint=handle_sse),
            Mount("/messages/", app=sse_transport.handle_post_message),
            Route("/health", endpoint=health),
            Route("/metrics", endpoint=metrics),
        ])

        instrument_starlette(app, "tfs-mcp-server")
        config = uvicorn.Config(app, host="0.0.0.0", port=self.port, log_level="info")
        server = uvicorn.Server(config)
        await server.serve()

    async def _run_stdio_server(self):
        async with stdio_server() as (read_stream, write_stream):
            await self.mcp.run(
                read_stream, write_stream, self.mcp.create_initialization_options(),
            )

    def _run_server_thread(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            if self.transport == "stdio":
                loop.run_until_complete(self._run_stdio_server())
            else:
                loop.run_until_complete(self._run_sse_server())
        except Exception as error:  # pylint: disable=broad-except
            LOGGER.error("MCP server error: %s", error)
        finally:
            loop.close()

    def start(self) -> bool:
        if self._server_thread and self._server_thread.is_alive():
            LOGGER.warning("MCP server is already running")
            return False

        LOGGER.info("Starting TFS MCP server (%s transport)", self.transport)
        self._should_stop.clear()
        self._server_thread = threading.Thread(
            target=self._run_server_thread, daemon=True, name="TFSMCPServerThread",
        )
        self._server_thread.start()

        time.sleep(1)

        if self._server_thread.is_alive():
            LOGGER.info("TFS MCP server started successfully")
            if self.transport == "sse":
                LOGGER.info("  SSE endpoint: http://localhost:%s/sse", self.port)
            return True
        LOGGER.error("TFS MCP server failed to start")
        return False

    def stop(self):
        LOGGER.info("Stopping TFS MCP server...")
        self._should_stop.set()
        if self._server_thread and self._server_thread.is_alive():
            self._server_thread.join(timeout=5)
        LOGGER.info("TFS MCP server stopped")

    def is_running(self) -> bool:
        return self._server_thread is not None and self._server_thread.is_alive()
