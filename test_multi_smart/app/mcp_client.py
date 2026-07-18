from __future__ import annotations

import asyncio
import inspect
import json
import logging
import shlex
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlparse

from langchain_core.tools import StructuredTool

LOGGER = logging.getLogger(__name__)

try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    try:
        from mcp.client.sse import sse_client
    except ImportError:
        sse_client = None
    try:
        from mcp.client.streamable_http import streamablehttp_client
    except ImportError:
        streamablehttp_client = None
    MCP_AVAILABLE = True
except ImportError:
    MCP_AVAILABLE = False


def _schema_required_fields(input_schema: Any) -> list[str]:
    if not isinstance(input_schema, dict):
        return []
    required = input_schema.get("required")
    if not isinstance(required, list):
        return []
    return [str(item) for item in required if isinstance(item, (str, int, float))]


def _default_inventory_arguments(tool_name: str) -> dict[str, Any]:
    try:
        from .repository import get_domain_fact
    except Exception:
        return {}

    defaults = {
        "context_uuid": get_domain_fact("default_context_uuid", "admin") or "admin",
        "topology_uuid": get_domain_fact("default_topology_uuid", "admin") or "admin",
    }
    if tool_name in {"tfs_get_topology", "tfs_get_topology_details"}:
        return defaults
    if tool_name in {"tfs_get_context"}:
        return {"context_uuid": defaults["context_uuid"]}
    return {}


class MCPInventoryClient:
    def __init__(self, server_urls: list[str] | None = None) -> None:
        self.server_urls = server_urls or []
        self._capabilities: dict[str, dict[str, Any]] = {}
        self._tools_cache: list[StructuredTool] = []
        self._sync_executor = ThreadPoolExecutor(max_workers=1)
        if not MCP_AVAILABLE or not self.server_urls:
            return
        try:
            self._run_coro_blocking(self._discover_all)
            self._tools_cache = self._build_discovered_tools()
        except Exception as exc:
            LOGGER.warning("MCP discovery failed: %s", exc)

    def has_mcp(self) -> bool:
        return MCP_AVAILABLE and bool(self._capabilities)

    def capability_names(self) -> list[str]:
        return sorted(self._capabilities.keys())

    def capability_details(self) -> dict[str, dict[str, Any]]:
        return dict(self._capabilities)

    async def _discover_all(self) -> None:
        for url in self.server_urls:
            async with self._open_session(url) as session:
                await session.initialize()
                listed = await session.list_tools()
            for tool in listed.tools:
                self._capabilities[tool.name] = {
                    "server": url,
                    "description": tool.description or "",
                    "input_schema": getattr(tool, "inputSchema", None),
                }

    @asynccontextmanager
    async def _open_session(self, url: str):
        if url.startswith("stdio://"):
            cmd = shlex.split(url[len("stdio://"):])
            params = StdioServerParameters(command=cmd[0], args=cmd[1:])
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    yield session
            return

        parsed = urlparse(url)
        path = (parsed.path or "").rstrip("/").lower()
        is_sse = path.endswith("/sse") or path == "sse"
        if is_sse:
            if sse_client is None:
                raise RuntimeError("Installed mcp package lacks SSE transport support")
            async with sse_client(url) as (read, write):
                async with ClientSession(read, write) as session:
                    yield session
            return

        if streamablehttp_client is None:
            raise RuntimeError("Installed mcp package lacks streamable HTTP transport support")
        async with streamablehttp_client(url) as (read, write, _):
            async with ClientSession(read, write) as session:
                yield session

    def _run_coro_blocking(self, coro_factory, *args, **kwargs):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro_factory(*args, **kwargs))
        future = self._sync_executor.submit(
            lambda: asyncio.run(coro_factory(*args, **kwargs))
        )
        return future.result()

    @staticmethod
    def _extract_result(result: Any) -> tuple[str, Any | None]:
        parts: list[str] = []
        structured = getattr(result, "structuredContent", None)
        for block in getattr(result, "content", []) or []:
            text = getattr(block, "text", None)
            if text:
                parts.append(text)
        text_joined = "\n".join(parts)
        if structured is None and text_joined:
            try:
                structured = json.loads(text_joined)
            except Exception:
                structured = None
        return text_joined, structured

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.has_mcp():
            raise RuntimeError("No MCP servers configured")
        if name not in self._capabilities:
            raise KeyError(f"Unknown MCP tool: {name}")
        arguments = dict(arguments or {})
        for key, value in _default_inventory_arguments(name).items():
            if arguments.get(key) in (None, ""):
                arguments[key] = value
        required = _schema_required_fields(self._capabilities[name].get("input_schema"))
        missing = [field for field in required if field not in arguments or arguments[field] in (None, "")]
        if missing:
            raise ValueError(f"Missing required MCP arguments for {name}: {', '.join(missing)}")
        url = self._capabilities[name]["server"]

        async def _run():
            async with self._open_session(url) as session:
                await session.initialize()
                return await session.call_tool(name, arguments)

        result = self._run_coro_blocking(_run)
        text, structured = self._extract_result(result)
        return {"text": text, "structured": structured}

    def _json_text(self, payload: Any) -> str:
        return json.dumps(payload, indent=2, ensure_ascii=True)

    def _call_json(self, tool_name: str, arguments: dict[str, Any] | None = None) -> str:
        result = self.call_tool(tool_name, arguments or {})
        if result["structured"] is not None:
            return self._json_text(result["structured"])
        return result["text"] or "{}"

    def build_inventory_tools(self) -> list[StructuredTool]:
        return list(self._tools_cache)

    def _build_discovered_tools(self) -> list[StructuredTool]:
        tools: list[StructuredTool] = []
        for tool_name in self.capability_names():
            tools.append(self._make_dynamic_tool(tool_name))
        return tools

    def _make_dynamic_tool(self, tool_name: str) -> StructuredTool:
        capability = self._capabilities[tool_name]
        input_schema = capability.get("input_schema") or {}
        properties = input_schema.get("properties") if isinstance(input_schema, dict) else {}
        if not isinstance(properties, dict):
            properties = {}
        required = set(_schema_required_fields(input_schema))

        def _call(**kwargs) -> str:
            sanitized = {
                key: value
                for key, value in kwargs.items()
                if value is not None and value != ""
            }
            return self._call_json(tool_name, sanitized)

        params: list[inspect.Parameter] = []
        annotations: dict[str, Any] = {"return": str}
        for key, schema in properties.items():
            param_name = str(key)
            json_type = schema.get("type") if isinstance(schema, dict) else "string"
            annotation: Any = str
            if json_type == "integer":
                annotation = int
            elif json_type == "number":
                annotation = float
            elif json_type == "boolean":
                annotation = bool
            elif json_type == "object":
                annotation = dict
            elif json_type == "array":
                annotation = list
            annotations[param_name] = annotation
            default = inspect.Parameter.empty if param_name in required else None
            params.append(
                inspect.Parameter(
                    param_name,
                    kind=inspect.Parameter.KEYWORD_ONLY,
                    default=default,
                    annotation=annotation,
                )
            )
        _call.__name__ = f"mcp__{tool_name}"
        _call.__doc__ = capability.get("description") or f"MCP tool {tool_name}"
        _call.__annotations__ = annotations
        _call.__signature__ = inspect.Signature(parameters=params, return_annotation=str)

        description = capability.get("description") or f"MCP tool {tool_name}"
        if properties:
            hints = []
            for key, schema in properties.items():
                label = f"{key}{'*' if key in required else ''}"
                if isinstance(schema, dict) and schema.get("description"):
                    label += f": {schema['description']}"
                hints.append(label)
            description += " Args(" + " | ".join(hints) + ")"

        return StructuredTool.from_function(
            _call,
            name=f"mcp__{tool_name}",
            description=description,
        )

    def close(self) -> None:
        self._sync_executor.shutdown(wait=False)
