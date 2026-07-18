from __future__ import annotations

from .config import SETTINGS
from .llm import build_ollama_chat
from .repository import count_messages, get_instance, list_facts, list_messages, update_summary


SUMMARY_PROMPT = """You compress specialist memory for future turns.

Create a compact summary with:
- current specialization context
- user preferences and stable facts
- unfinished tasks or follow-ups
- any facts that should be retained

Use plain text, no markdown bullets longer than one sentence each.

Current summary:
{current_summary}

Recent messages:
{messages}

Stored facts:
{facts}
"""


def refresh_summary_if_needed(instance_id: str) -> str:
    instance = get_instance(instance_id)
    if count_messages(instance_id) < SETTINGS.summary_trigger_messages:
        return instance.memory_summary

    model = build_ollama_chat(SETTINGS.summary_model, 0.1)
    messages = "\n".join(
        f"{m.role}: {m.content}" for m in list_messages(instance_id, limit=12)
    )
    facts = "\n".join(f"- {f.content}" for f in list_facts(instance_id, limit=12))
    summary = model.invoke(
        SUMMARY_PROMPT.format(
            current_summary=instance.memory_summary or "None yet.",
            messages=messages or "No messages.",
            facts=facts or "No facts.",
        )
    ).content
    text = str(summary).strip()
    update_summary(instance_id, text)
    return text
