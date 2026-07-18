from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from .db import get_conn
from .models import (
    AgentInstanceRecord,
    AgentSpecDraft,
    AgentSpecRecord,
    FactRecord,
    KnowledgeItemRecord,
    MessageRecord,
    SessionStateRecord,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def create_spec(spec: AgentSpecDraft) -> AgentSpecRecord:
    spec_id = "spec-" + uuid.uuid4().hex[:12]
    created_at = _now()
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO agent_specs (
                id, name, purpose, specialization, system_prompt,
                model_name, temperature, memory_policy, tools_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                spec_id,
                spec.name,
                spec.purpose,
                spec.specialization,
                spec.system_prompt,
                spec.model_name,
                spec.temperature,
                spec.memory_policy,
                json.dumps(spec.tools),
                created_at,
            ),
        )
    return get_spec(spec_id)


def get_spec(spec_id: str) -> AgentSpecRecord:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM agent_specs WHERE id = ?", (spec_id,)).fetchone()
    if row is None:
        raise KeyError(f"Unknown spec_id: {spec_id}")
    return AgentSpecRecord(
        id=row["id"],
        name=row["name"],
        purpose=row["purpose"],
        specialization=row["specialization"],
        system_prompt=row["system_prompt"],
        model_name=row["model_name"],
        temperature=row["temperature"],
        memory_policy=row["memory_policy"],
        tools=json.loads(row["tools_json"]),
        created_at=_parse_dt(row["created_at"]),
    )


def list_specs() -> list[AgentSpecRecord]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM agent_specs ORDER BY created_at DESC").fetchall()
    return [
        AgentSpecRecord(
            id=row["id"],
            name=row["name"],
            purpose=row["purpose"],
            specialization=row["specialization"],
            system_prompt=row["system_prompt"],
            model_name=row["model_name"],
            temperature=row["temperature"],
            memory_policy=row["memory_policy"],
            tools=json.loads(row["tools_json"]),
            created_at=_parse_dt(row["created_at"]),
        )
        for row in rows
    ]


def create_instance(spec_id: str, display_name: str | None = None) -> AgentInstanceRecord:
    spec = get_spec(spec_id)
    instance_id = "agent-" + uuid.uuid4().hex[:12]
    created_at = _now()
    record_name = display_name or spec.name
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO agent_instances (
                id, spec_id, display_name, status, memory_summary, created_at, last_used_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                instance_id,
                spec_id,
                record_name,
                "ready",
                "",
                created_at,
                created_at,
            ),
        )
    return get_instance(instance_id)


def get_instance(instance_id: str) -> AgentInstanceRecord:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM agent_instances WHERE id = ?", (instance_id,)).fetchone()
    if row is None:
        raise KeyError(f"Unknown instance_id: {instance_id}")
    return AgentInstanceRecord(
        id=row["id"],
        spec_id=row["spec_id"],
        display_name=row["display_name"],
        status=row["status"],
        memory_summary=row["memory_summary"],
        created_at=_parse_dt(row["created_at"]),
        last_used_at=_parse_dt(row["last_used_at"]),
    )


def list_instances() -> list[AgentInstanceRecord]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM agent_instances ORDER BY last_used_at DESC, created_at DESC"
        ).fetchall()
    return [
        AgentInstanceRecord(
            id=row["id"],
            spec_id=row["spec_id"],
            display_name=row["display_name"],
            status=row["status"],
            memory_summary=row["memory_summary"],
            created_at=_parse_dt(row["created_at"]),
            last_used_at=_parse_dt(row["last_used_at"]),
        )
        for row in rows
    ]


def touch_instance(instance_id: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE agent_instances SET last_used_at = ? WHERE id = ?",
            (_now(), instance_id),
        )


def update_summary(instance_id: str, summary: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE agent_instances SET memory_summary = ?, last_used_at = ? WHERE id = ?",
            (summary, _now(), instance_id),
        )


def add_message(instance_id: str, role: str, content: str) -> MessageRecord:
    created_at = _now()
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO messages (instance_id, role, content, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (instance_id, role, content, created_at),
        )
    touch_instance(instance_id)
    return MessageRecord(
        id=cur.lastrowid,
        instance_id=instance_id,
        role=role,
        content=content,
        created_at=_parse_dt(created_at),
    )


def list_messages(instance_id: str, limit: int | None = None) -> list[MessageRecord]:
    sql = "SELECT * FROM messages WHERE instance_id = ? ORDER BY id ASC"
    params: tuple[object, ...] = (instance_id,)
    if limit is not None:
        sql = """
            SELECT * FROM (
                SELECT * FROM messages WHERE instance_id = ? ORDER BY id DESC LIMIT ?
            ) ORDER BY id ASC
        """
        params = (instance_id, limit)
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [
        MessageRecord(
            id=row["id"],
            instance_id=row["instance_id"],
            role=row["role"],
            content=row["content"],
            created_at=_parse_dt(row["created_at"]),
        )
        for row in rows
    ]


def count_messages(instance_id: str) -> int:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM messages WHERE instance_id = ?",
            (instance_id,),
        ).fetchone()
    return int(row["c"])


def add_fact(instance_id: str, content: str) -> FactRecord:
    created_at = _now()
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO facts (instance_id, content, created_at) VALUES (?, ?, ?)",
            (instance_id, content, created_at),
        )
    return FactRecord(
        id=cur.lastrowid,
        instance_id=instance_id,
        content=content,
        created_at=_parse_dt(created_at),
    )


def list_facts(instance_id: str, limit: int = 20) -> list[FactRecord]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM facts WHERE instance_id = ? ORDER BY id DESC LIMIT ?",
            (instance_id, limit),
        ).fetchall()
    return [
        FactRecord(
            id=row["id"],
            instance_id=row["instance_id"],
            content=row["content"],
            created_at=_parse_dt(row["created_at"]),
        )
        for row in rows
    ]


def search_facts(instance_id: str, query: str, limit: int = 10) -> list[FactRecord]:
    like = f"%{query}%"
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT * FROM facts
            WHERE instance_id = ? AND content LIKE ?
            ORDER BY id DESC LIMIT ?
            """,
            (instance_id, like, limit),
        ).fetchall()
    return [
        FactRecord(
            id=row["id"],
            instance_id=row["instance_id"],
            content=row["content"],
            created_at=_parse_dt(row["created_at"]),
        )
        for row in rows
    ]


def get_session_state(session_id: str = "default") -> SessionStateRecord:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM session_state WHERE session_id = ?",
            (session_id,),
        ).fetchone()
    if row is None:
        return SessionStateRecord(
            session_id=session_id,
            updated_at=_parse_dt(_now()),
        )
    blackboard = row["blackboard_json"] or "{}"
    try:
        blackboard_obj = json.loads(blackboard)
    except Exception:
        blackboard_obj = {}
    return SessionStateRecord(
        session_id=row["session_id"],
        active_instance_id=row["active_instance_id"],
        active_spec_id=row["active_spec_id"],
        last_user_message=row["last_user_message"],
        last_reply=row["last_reply"],
        pending_task=row["pending_task"],
        blackboard=blackboard_obj,
        updated_at=_parse_dt(row["updated_at"]),
    )


def upsert_session_state(
    session_id: str = "default",
    *,
    active_instance_id: str | None = None,
    active_spec_id: str | None = None,
    last_user_message: str = "",
    last_reply: str = "",
    pending_task: str = "",
    blackboard: dict | None = None,
) -> SessionStateRecord:
    existing = get_session_state(session_id)
    payload = {
        "session_id": session_id,
        "active_instance_id": active_instance_id if active_instance_id is not None else existing.active_instance_id,
        "active_spec_id": active_spec_id if active_spec_id is not None else existing.active_spec_id,
        "last_user_message": last_user_message or existing.last_user_message,
        "last_reply": last_reply or existing.last_reply,
        "pending_task": pending_task,
        "blackboard_json": json.dumps(blackboard if blackboard is not None else existing.blackboard, ensure_ascii=True),
        "updated_at": _now(),
    }
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO session_state (
                session_id, active_instance_id, active_spec_id,
                last_user_message, last_reply, pending_task, blackboard_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                active_instance_id=excluded.active_instance_id,
                active_spec_id=excluded.active_spec_id,
                last_user_message=excluded.last_user_message,
                last_reply=excluded.last_reply,
                pending_task=excluded.pending_task,
                blackboard_json=excluded.blackboard_json,
                updated_at=excluded.updated_at
            """,
            (
                payload["session_id"],
                payload["active_instance_id"],
                payload["active_spec_id"],
                payload["last_user_message"],
                payload["last_reply"],
                payload["pending_task"],
                payload["blackboard_json"],
                payload["updated_at"],
            ),
        )
    return get_session_state(session_id)


_DEFAULT_KNOWLEDGE = [
    {
        "category": "domain_fact",
        "key": "default_context_uuid",
        "value": "admin",
        "payload": {"description": "Default TFS context UUID or name for inventory questions."},
    },
    {
        "category": "domain_fact",
        "key": "default_topology_uuid",
        "value": "admin",
        "payload": {"description": "Default TFS topology UUID or name for inventory questions."},
    },
    {
        "category": "dialogue_pattern",
        "key": "continue_pending",
        "value": "continue_pending",
        "payload": {"triggers": ["so?", "so", "continue", "then?", "and?", "and then?", "what next?"]},
    },
    {
        "category": "dialogue_pattern",
        "key": "proceed_pending",
        "value": "proceed_pending",
        "payload": {"triggers": ["go ahead", "proceed", "carry on", "do it"]},
    },
    {
        "category": "dialogue_pattern",
        "key": "double_check",
        "value": "double_check",
        "payload": {"triggers": ["are you sure?", "double check", "verify that", "check again"]},
    },
    {
        "category": "verifier_pattern",
        "key": "generic_bad_reply",
        "value": "how can i assist you today",
        "payload": {},
    },
    {
        "category": "verifier_pattern",
        "key": "generic_bad_reply_2",
        "value": "i'm ready to help",
        "payload": {},
    },
    {
        "category": "verifier_pattern",
        "key": "generic_bad_reply_3",
        "value": "let's retrieve this information",
        "payload": {},
    },
    {
        "category": "verifier_pattern",
        "key": "generic_bad_reply_4",
        "value": "i need to identify",
        "payload": {},
    },
    {
        "category": "verifier_pattern",
        "key": "generic_bad_reply_5",
        "value": "i don't have direct access",
        "payload": {},
    },
    {
        "category": "verifier_pattern",
        "key": "generic_bad_reply_6",
        "value": "within the constraints",
        "payload": {},
    },
]


def ensure_default_knowledge() -> None:
    created_at = _now()
    with get_conn() as conn:
        for item in _DEFAULT_KNOWLEDGE:
            knowledge_id = "know-" + uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"{item['category']}::{item['key']}",
            ).hex[:20]
            conn.execute(
                """
                INSERT INTO knowledge_items (
                    id, category, key, value, payload_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(category, key) DO NOTHING
                """,
                (
                    knowledge_id,
                    item["category"],
                    item["key"],
                    item["value"],
                    json.dumps(item["payload"], ensure_ascii=True),
                    created_at,
                    created_at,
                ),
            )


def list_knowledge_items(category: str | None = None) -> list[KnowledgeItemRecord]:
    sql = "SELECT * FROM knowledge_items"
    params: tuple[object, ...] = ()
    if category:
        sql += " WHERE category = ?"
        params = (category,)
    sql += " ORDER BY category, key"
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    items = []
    for row in rows:
        payload = row["payload_json"] or "{}"
        try:
            payload_obj = json.loads(payload)
        except Exception:
            payload_obj = {}
        items.append(
            KnowledgeItemRecord(
                id=row["id"],
                category=row["category"],
                key=row["key"],
                value=row["value"],
                payload=payload_obj,
                created_at=_parse_dt(row["created_at"]),
                updated_at=_parse_dt(row["updated_at"]),
            )
        )
    return items


def get_domain_fact(key: str, default: str = "") -> str:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT value FROM knowledge_items WHERE category = 'domain_fact' AND key = ?",
            (key,),
        ).fetchone()
    if row is None:
        return default
    return str(row["value"] or default)


def resolve_dialogue_action(message: str) -> str | None:
    normalized = " ".join(str(message or "").strip().lower().split())
    if not normalized:
        return None
    for item in list_knowledge_items("dialogue_pattern"):
        triggers = item.payload.get("triggers") or []
        if not isinstance(triggers, list):
            continue
        if normalized in {" ".join(str(trigger).strip().lower().split()) for trigger in triggers}:
            return item.value or item.key
    return None


def get_verifier_patterns(key_prefix: str = "generic_bad_reply") -> list[str]:
    values = []
    for item in list_knowledge_items("verifier_pattern"):
        if item.key.startswith(key_prefix) and item.value:
            values.append(item.value.strip().lower())
    return values
