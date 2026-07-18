import os
from dataclasses import dataclass, field


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


@dataclass(frozen=True)
class Settings:
    host: str = os.environ.get("HOST", "0.0.0.0")
    port: int = int(os.environ.get("PORT", "8787"))
    sqlite_path: str = os.environ.get("SQLITE_PATH", "/data/multi_smart.db")
    ollama_base_url: str = os.environ.get("OLLAMA_BASE_URL", "http://10.1.1.89:11434")
    planner_model: str = os.environ.get("PLANNER_MODEL", "qwen2.5:7b-instruct")
    default_agent_model: str = os.environ.get("DEFAULT_AGENT_MODEL", "qwen2.5:3b-instruct")
    summary_model: str = os.environ.get("SUMMARY_MODEL", "qwen2.5:1.5b-instruct")
    planner_temperature: float = float(os.environ.get("PLANNER_TEMPERATURE", "0.1"))
    agent_temperature: float = float(os.environ.get("AGENT_TEMPERATURE", "0.2"))
    memory_window: int = int(os.environ.get("MEMORY_WINDOW", "8"))
    summary_trigger_messages: int = int(os.environ.get("SUMMARY_TRIGGER_MESSAGES", "6"))
    mcp_servers: list[str] = field(
        default_factory=lambda: _split_csv(os.environ.get("MCP_SERVERS", ""))
    )


SETTINGS = Settings()
