from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .config import SETTINGS


def _ensure_parent(path: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)


def init_db() -> None:
    _ensure_parent(SETTINGS.sqlite_path)
    with sqlite3.connect(SETTINGS.sqlite_path) as conn:
        conn.executescript(
            """
            PRAGMA journal_mode=WAL;

            CREATE TABLE IF NOT EXISTS agent_specs (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                purpose TEXT NOT NULL,
                specialization TEXT NOT NULL,
                system_prompt TEXT NOT NULL,
                model_name TEXT NOT NULL,
                temperature REAL NOT NULL,
                memory_policy TEXT NOT NULL,
                tools_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS agent_instances (
                id TEXT PRIMARY KEY,
                spec_id TEXT NOT NULL,
                display_name TEXT NOT NULL,
                status TEXT NOT NULL,
                memory_summary TEXT NOT NULL,
                created_at TEXT NOT NULL,
                last_used_at TEXT NOT NULL,
                FOREIGN KEY(spec_id) REFERENCES agent_specs(id)
            );

            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                instance_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(instance_id) REFERENCES agent_instances(id)
            );

            CREATE TABLE IF NOT EXISTS facts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                instance_id TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(instance_id) REFERENCES agent_instances(id)
            );

            CREATE TABLE IF NOT EXISTS session_state (
                session_id TEXT PRIMARY KEY,
                active_instance_id TEXT,
                active_spec_id TEXT,
                last_user_message TEXT NOT NULL,
                last_reply TEXT NOT NULL,
                pending_task TEXT NOT NULL,
                blackboard_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS knowledge_items (
                id TEXT PRIMARY KEY,
                category TEXT NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_knowledge_items_category_key
            ON knowledge_items(category, key);
            """
        )


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(SETTINGS.sqlite_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def get_db_status() -> dict[str, Any]:
    path = Path(SETTINGS.sqlite_path)
    companions = []
    for suffix in ("-wal", "-shm"):
        companion = Path(str(path) + suffix)
        if companion.exists():
            companions.append(
                {
                    "path": str(companion),
                    "size_bytes": companion.stat().st_size,
                }
            )
    if not path.exists():
        return {
            "path": str(path),
            "exists": False,
            "size_bytes": 0,
            "tables": [],
            "companions": companions,
        }
    with sqlite3.connect(SETTINGS.sqlite_path) as conn:
        conn.row_factory = sqlite3.Row
        tables = [
            row["name"]
            for row in conn.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
                ORDER BY name
                """
            ).fetchall()
        ]
    return {
        "path": str(path),
        "exists": path.exists(),
        "size_bytes": path.stat().st_size if path.exists() else 0,
        "tables": tables,
        "companions": companions,
    }


def list_table_rows(table_name: str, limit: int = 100) -> list[dict[str, Any]]:
    status = get_db_status()
    if table_name not in status["tables"]:
        raise KeyError(f"Unknown table: {table_name}")
    safe_limit = max(1, min(int(limit), 500))
    with sqlite3.connect(SETTINGS.sqlite_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f'SELECT * FROM "{table_name}" ORDER BY ROWID DESC LIMIT ?',
            (safe_limit,),
        ).fetchall()
    return [dict(row) for row in rows]
