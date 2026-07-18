# File: mcp_client.py
"""
Lightweight MCP (Model Context Protocol) client.

Connects to one or more MCP servers and exposes their tools as LangChain
`StructuredTool`s so the agent can call them transparently.

This module supports two transport flavours, picked per-URL:
  * `http://...` / `https://...`  - Streamable HTTP / SSE transport
  * `stdio://<command>`           - Spawn an MCP server as a subprocess

It gracefully degrades when the optional `mcp` dependency is not installed
(useful for development without external MCP servers).
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import logging
import os
import shlex
from urllib.parse import urlparse
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.tools import StructuredTool
from utils.event_timing import timed_event

LOGGER = logging.getLogger(__name__)

# TFS API returns verbose payloads with many fields the LLM never needs.
# These are dropped from every raw MCP response before it reaches the model.
_NOISY_TFS_FIELDS = frozenset({
    "device_config",
    "device_drivers",
    "device_sbi_drivers",
    "device_constraints",
    "device_operational_status_reason",
    "monitoring_params",
    "controller_id",
})


def _compact_tfs_json(obj: Any) -> Any:
    """Recursively compact a TFS JSON payload.

    Two transformations applied together:
    1. Drop known verbose fields (device_config, device_drivers, …).
    2. Flatten single-key {"uuid": "..."} dicts to the bare UUID string.
       e.g. {"endpoint_uuid": {"uuid": "abc"}} → {"endpoint_uuid": "abc"}

    All data the LLM actually needs is preserved; only noise is removed.
    """
    if isinstance(obj, dict):
        # Collapse {"uuid": "<value>"} leaf nodes.
        if set(obj.keys()) == {"uuid"} and isinstance(obj.get("uuid"), str):
            return obj["uuid"]
        return {
            k: _compact_tfs_json(v)
            for k, v in obj.items()
            if k not in _NOISY_TFS_FIELDS
        }
    if isinstance(obj, list):
        return [_compact_tfs_json(item) for item in obj]
    return obj


def _compact_response(tool_name: str, text: str) -> str:
    """Compact a TFS MCP tool response before sending it to the LLM.

    Parses JSON, strips noisy fields, flattens UUID leaves, then re-serialises
    with compact separators.  Non-JSON responses are returned unchanged.
    Falls back to a hard character cap only if the compacted form is still
    unreasonably large (protects against pathological payloads).
    """
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return text  # not JSON — leave as-is

    compacted = _compact_tfs_json(data)
    result = json.dumps(compacted, separators=(",", ":"))

    original_len = len(text)
    compacted_len = len(result)
    if compacted_len < original_len:
        LOGGER.debug(
            "Compacted %s response: %d → %d chars (%.0f%% reduction)",
            tool_name, original_len, compacted_len,
            100 * (1 - compacted_len / original_len),
        )

    # Hard safety cap — only fires on truly pathological responses.
    hard_limit = int(os.environ.get("MCP_HARD_RESPONSE_CAP", "20000"))
    if len(result) > hard_limit:
        LOGGER.warning(
            "%s response still %d chars after compaction; hard-capping at %d",
            tool_name, len(result), hard_limit,
        )
        result = result[:hard_limit] + " ...[response capped]"

    return result


# MCP tool names whose structured results should auto-populate SpectrumState.
# Tools are matched on a substring basis (server-side names vary slightly).
_SPECTRUM_TOOL_HINTS = {
    "compute_local_spectrum_candidates": "candidates",
    "local_spectrum_candidates": "candidates",
    "reserve_or_commit_spectrum": "reservation",
    "reserve_spectrum": "reservation",
    "commit_spectrum": "reservation",
    "rollback_spectrum": "reservation",
}


def _schema_required_fields(input_schema: Any) -> List[str]:
    """Return required argument names from a JSON schema-like payload."""
    if not isinstance(input_schema, dict):
        return []
    required = input_schema.get("required")
    if not isinstance(required, list):
        return []
    return [str(x) for x in required if isinstance(x, (str, int, float))]


def _schema_hint(input_schema: Any) -> str:
    """Build a compact argument hint string for tool descriptions."""
    if not isinstance(input_schema, dict):
        return ""

    properties = input_schema.get("properties") or {}
    if not isinstance(properties, dict):
        properties = {}

    required = set(_schema_required_fields(input_schema))
    all_fields = [str(k) for k in properties.keys()]
    optional = [k for k in all_fields if k not in required]

    parts: List[str] = []
    if required:
        parts.append("required: " + ", ".join(sorted(required)))
    if optional:
        parts.append("optional: " + ", ".join(sorted(optional)))
    if not parts:
        return ""
    return " Args(" + " | ".join(parts) + ")"

try:
    # Official MCP Python SDK
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    try:
        from mcp.client.sse import sse_client
    except ImportError:  # older/newer SDK variants may omit SSE transport
        sse_client = None  # type: ignore
    try:
        from mcp.client.streamable_http import streamablehttp_client
    except ImportError:  # older SDK
        streamablehttp_client = None  # type: ignore
    MCP_AVAILABLE = True
except ImportError:
    LOGGER.warning(
        "`mcp` package not installed - MCP client will run in no-op mode. "
        "Install with: pip install mcp"
    )
    MCP_AVAILABLE = False


class MCPClient:
    def __init__(
        self,
        server_urls: Optional[List[str]] = None,
        spectrum_state: Optional[Any] = None,
    ):
        self.server_urls = server_urls or []
        self.spectrum_state = spectrum_state  # optional SpectrumState
        self._tools_cache: List[StructuredTool] = []
        self._capabilities: Dict[str, Dict[str, Any]] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._sync_executor = ThreadPoolExecutor(max_workers=1)

        if not MCP_AVAILABLE or not self.server_urls:
            LOGGER.info("MCP client running with no active connections")
            return

        try:
            with timed_event(
                LOGGER,
                "mcp_discovery_init",
                servers=len(self.server_urls),
            ):
                self._loop = asyncio.new_event_loop()
                self._tools_cache = self._loop.run_until_complete(
                    self._discover_all_tools()
                )
            LOGGER.info(
                f"Discovered {len(self._tools_cache)} MCP tool(s) across "
                f"{len(self.server_urls)} server(s)"
            )
            if self.spectrum_state is not None:
                self.spectrum_state.set_capabilities(self._capabilities)
        except Exception as e:
            LOGGER.error(f"Failed to initialize MCP client: {e}")

    # ------------------------------------------------------------------ #
    async def _discover_all_tools(self) -> List[StructuredTool]:
        all_tools: List[StructuredTool] = []
        for url in self.server_urls:
            try:
                with timed_event(LOGGER, "discover_server_tools", server_url=url):
                    tools = await self._discover_server_tools(url)
                all_tools.extend(tools)
            except Exception as e:
                LOGGER.error(f"Failed to discover tools from {url}: {e}")
        return all_tools

    async def _discover_server_tools(self, url: str) -> List[StructuredTool]:
        """Connect briefly to list tools, then build LangChain wrappers."""
        async with self._open_session(url) as session:
            await session.initialize()
            listed = await session.list_tools()

        tools: List[StructuredTool] = []
        for t in listed.tools:
            desc = t.description or ""
            input_schema = getattr(t, "inputSchema", None)
            self._capabilities[t.name] = {
                "server": url,
                "description": desc,
                "input_schema": input_schema,
            }
            tools.append(self._make_tool(url, t.name, desc, input_schema))
        return tools

    def _open_session(self, url: str):
        """Return an async context manager yielding an MCP ClientSession."""
        from contextlib import asynccontextmanager

        def _use_sse_transport(target: str) -> bool:
            parsed = urlparse(target)
            path = (parsed.path or "").rstrip("/").lower()
            # Legacy MCP SSE endpoints are commonly exposed as /sse.
            return path.endswith("/sse") or path == "sse"

        @asynccontextmanager
        async def _ctx():
            if url.startswith("stdio://"):
                cmd = shlex.split(url[len("stdio://"):])
                params = StdioServerParameters(command=cmd[0], args=cmd[1:])
                async with stdio_client(params) as (read, write):
                    async with ClientSession(read, write) as session:
                        yield session
            else:
                if _use_sse_transport(url):
                    if sse_client is None:
                        raise RuntimeError(
                            "SSE transport not available in installed mcp package."
                        )
                    async with sse_client(url) as (read, write):
                        async with ClientSession(read, write) as session:
                            yield session
                else:
                    if streamablehttp_client is None:
                        raise RuntimeError(
                            "Streamable HTTP transport not available in installed "
                            "mcp package."
                        )
                    async with streamablehttp_client(url) as (read, write, _):
                        async with ClientSession(read, write) as session:
                            yield session

        return _ctx()

    def _make_tool(
        self,
        url: str,
        name: str,
        description: str,
        input_schema: Any,
    ) -> StructuredTool:
        """Wrap a remote MCP tool as a synchronous LangChain tool."""

        async def _async_call(**kwargs):
            return await self._call_remote_tool_text(url, name, kwargs)

        def _sync_call(**kwargs):
            return self._run_coro_blocking(_async_call, **kwargs)

        return StructuredTool.from_function(
            func=_sync_call,
            coroutine=_async_call,
            name=f"mcp__{name}",
            description=f"[MCP@{url}] {description}{_schema_hint(input_schema)}",
        )

    def _validate_required_arguments(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
    ) -> Optional[str]:
        """Check that all required tool arguments are present and non-empty."""
        cap = self._capabilities.get(tool_name, {})
        required = _schema_required_fields(cap.get("input_schema"))
        if not required:
            return None

        missing = []
        for key in required:
            if key not in arguments:
                missing.append(key)
                continue
            value = arguments.get(key)
            if value is None:
                missing.append(key)
            elif isinstance(value, str) and not value.strip():
                missing.append(key)

        if not missing:
            return None

        return (
            f"Missing required argument(s) for {tool_name}: "
            f"{', '.join(sorted(missing))}. "
            "Ask the user for these values and retry."
        )

    # ------------------------------------------------------------------ #
    def call_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Synchronously invoke a discovered MCP tool by its bare name.

        Returns ``{"text": str, "structured": Any | None}`` so callers can
        consume either form. Also auto-updates SpectrumState when the tool
        is one of the known spectrum tools.
        """
        if not MCP_AVAILABLE:
            raise RuntimeError("mcp package not installed")
        if name not in self._capabilities:
            raise KeyError(f"Unknown MCP tool: {name}")
        validation_error = self._validate_required_arguments(name, arguments)
        if validation_error:
            return {"text": validation_error, "structured": {"error": validation_error}}
        url = self._capabilities[name]["server"]

        async def _run():
            async with self._open_session(url) as session:
                await session.initialize()
                return await session.call_tool(name, arguments)

        with timed_event(LOGGER, "mcp_call_tool", tool_name=name):
            result = self._run_coro_blocking(_run)
            text, structured = self._extract_result(result)
            self._maybe_update_state(name, structured, arguments)
            return {"text": text, "structured": structured}

    def has_tool(self, name: str) -> bool:
        return name in self._capabilities

    def capability_names(self) -> List[str]:
        return sorted(self._capabilities.keys())

    def capability_details(self) -> Dict[str, Dict[str, Any]]:
        return dict(self._capabilities)

    async def _call_remote_tool_text(
        self,
        url: str,
        name: str,
        arguments: Dict[str, Any],
    ) -> str:
        """Invoke a remote MCP tool and return text suitable for LangChain."""
        validation_error = self._validate_required_arguments(name, arguments)
        if validation_error:
            return validation_error
        with timed_event(LOGGER, "mcp_call_remote_tool_text", tool_name=name, server=url):
            async with self._open_session(url) as session:
                await session.initialize()
                result = await session.call_tool(name, arguments)
                text, structured = self._extract_result(result)
                self._maybe_update_state(name, structured, arguments)
                # Prefer structured JSON so the agent can reason over tool output.
                output = json.dumps(structured) if structured is not None else (text or str(result))
                return _compact_response(name, output)

    def _run_coro_blocking(self, coro_factory, *args, **kwargs):
        """Run a coroutine from sync code, even if another event loop is active."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro_factory(*args, **kwargs))

        future = self._sync_executor.submit(
            lambda: asyncio.run(coro_factory(*args, **kwargs))
        )
        return future.result()

    # ------------------------------------------------------------------ #
    @staticmethod
    def _extract_result(result: Any) -> Tuple[str, Optional[Any]]:
        """Pull text + best-effort structured payload out of an MCP result."""
        parts: List[str] = []
        structured: Optional[Any] = None
        is_error = bool(getattr(result, "isError", False))
        for block in getattr(result, "content", []) or []:
            text = getattr(block, "text", None)
            if text:
                parts.append(text)
        text_joined = "\n".join(parts)

        # Newer MCP SDKs expose `structuredContent` directly.
        sc = getattr(result, "structuredContent", None)
        if sc is not None:
            structured = sc
        elif text_joined:
            try:
                structured = json.loads(text_joined)
            except (ValueError, TypeError):
                structured = None
        if is_error:
            if isinstance(structured, dict):
                structured.setdefault("status", "error")
                structured.setdefault("error", text_joined or "MCP tool returned an error")
            else:
                structured = {
                    "status": "error",
                    "error": text_joined or "MCP tool returned an error",
                }
        return text_joined, structured

    def _maybe_update_state(
        self,
        tool_name: str,
        structured: Optional[Any],
        arguments: Dict[str, Any],
    ):
        """Mirror spectrum-tool results into SpectrumState (if attached)."""
        if self.spectrum_state is None or not isinstance(structured, dict):
            return
        kind = None
        for hint, k in _SPECTRUM_TOOL_HINTS.items():
            if hint in tool_name:
                kind = k
                break
        if kind is None:
            return

        try:
            if kind == "candidates":
                border_link = (
                    structured.get("border_link")
                    or arguments.get("border_link")
                )
                ranges = structured.get("candidate_ranges") or []
                self.spectrum_state.update_candidates(
                    border_link=border_link,
                    candidate_ranges=ranges,
                    required_slots=structured.get("required_slots")
                    or arguments.get("required_slots"),
                    guard_band_slots=structured.get("guard_band_slots")
                    or arguments.get("guard_band_slots"),
                    source_tool=tool_name,
                )
            elif kind == "reservation":
                self.spectrum_state.add_reservation(
                    border_link=structured.get("border_link")
                    or arguments.get("border_link", ""),
                    n_start=int(structured.get("n_start", 0)),
                    n_end=int(structured.get("n_end", 0)),
                    service_id=str(
                        structured.get("service_id")
                        or arguments.get("service_id", "")
                    ),
                    status=str(structured.get("status", "soft")),
                    reservation_id=structured.get("reservation_id"),
                )
        except Exception as e:
            LOGGER.warning(f"SpectrumState update from {tool_name} failed: {e}")

    # ------------------------------------------------------------------ #
    def get_tools(self) -> List[StructuredTool]:
        return list(self._tools_cache)

    def close(self):
        if self._loop and not self._loop.is_closed():
            try:
                self._loop.close()
            except Exception:
                pass
        self._sync_executor.shutdown(wait=False)
