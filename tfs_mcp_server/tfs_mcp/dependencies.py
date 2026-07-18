import json
import logging
from pathlib import Path
from typing import Any

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
LOGGER = logging.getLogger(__name__)

try:
    from mcp.server import Server
    from mcp.server.sse import SseServerTransport
    from mcp.server.stdio import stdio_server
    from mcp.types import TextContent, Tool
    MCP_AVAILABLE = True
except ImportError:
    Server = None  # type: ignore[assignment]
    SseServerTransport = None  # type: ignore[assignment]
    stdio_server = None  # type: ignore[assignment]
    TextContent = None  # type: ignore[assignment]
    Tool = None  # type: ignore[assignment]
    MCP_AVAILABLE = False
    LOGGER.warning("MCP SDK not installed. Run: pip install mcp")

try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    httpx = None  # type: ignore[assignment]
    HTTPX_AVAILABLE = False
    LOGGER.warning("httpx not installed. Run: pip install httpx")

try:
    from dotenv import load_dotenv
    DOTENV_AVAILABLE = True
except ImportError:
    load_dotenv = None  # type: ignore[assignment]
    DOTENV_AVAILABLE = False
    LOGGER.warning("python-dotenv not installed. Run: pip install python-dotenv")

if DOTENV_AVAILABLE:
    load_dotenv(dotenv_path=Path(__file__).resolve().parent.parent / ".env")


def format_response(data: Any, indent: int = 2) -> str:
    """Format response data as a JSON string."""
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            return data
    return json.dumps(data, indent=indent, default=str)
