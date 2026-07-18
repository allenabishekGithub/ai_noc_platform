from __future__ import annotations

import json

from .config import SETTINGS
from .llm import build_ollama_chat
from .models import AgentSpecDraft, RouteDecision
from .repository import list_instances, list_specs
from .utils import extract_json_object, normalize_agent_name

ALLOWED_TOOLS = {"store_fact", "recall_memory", "list_facts", "calculate"}
ALLOWED_MODELS = {
    "qwen2.5:1.5b-instruct",
    "qwen2.5:3b-instruct",
    "qwen2.5:7b-instruct",
}


SPEC_PROMPT = """You define a small specialist AI agent.

Return only one JSON object with these fields:
- name
- purpose
- specialization
- system_prompt
- model_name
- temperature
- memory_policy
- tools

Rules:
- Keep the agent narrow and specialized.
- Prefer qwen2.5:1.5b-instruct for simple focused tasks.
- Prefer qwen2.5:3b-instruct for moderate reasoning.
- Prefer qwen2.5:7b-instruct only for planning-heavy or broad coordination roles.
- tools may contain only: ["store_fact", "recall_memory", "list_facts", "calculate"]
- system_prompt must explicitly say what the agent should do, what it should refuse, and how it should use memory.
- Return valid JSON only.

User request for the new specialist:
{prompt}
"""


ROUTER_PROMPT = """You are a small-agent planner.

Your job is to decide whether an existing specialist should handle the task or
whether a new specialist should be created first.

Return only one JSON object with these fields:
- action: "reuse" or "create"
- reason
- spec_id
- instance_id
- new_spec

Rules:
- Reuse an existing agent only if its specialization is clearly aligned.
- Prefer small specialists over broad generalists.
- If no clear match exists, choose "create" and provide a complete new_spec object.
- For new_spec, use fields:
  name, purpose, specialization, system_prompt, model_name, temperature, memory_policy, tools
- tools may contain only: ["store_fact", "recall_memory", "list_facts", "calculate"]
- spec_id and instance_id should be null when action is "create".
- new_spec should be null when action is "reuse".
- Return valid JSON only.

Existing specs:
{specs}

Existing instances:
{instances}

Task:
{message}
"""


class Planner:
    def __init__(self) -> None:
        self.model = build_ollama_chat(SETTINGS.planner_model, SETTINGS.planner_temperature)

    def _normalize_spec_payload(self, payload: dict) -> dict:
        fallback_name = str(payload.get("specialization") or payload.get("purpose") or "Specialist")
        payload["name"] = normalize_agent_name(payload.get("name", ""), fallback=fallback_name)
        if not isinstance(payload.get("memory_policy"), str):
            payload["memory_policy"] = json.dumps(
                payload.get("memory_policy", "Retain useful facts and a rolling summary."),
                ensure_ascii=True,
            )
        tools = payload.get("tools") or []
        if not isinstance(tools, list):
            tools = [str(tools)]
        payload["tools"] = [str(tool) for tool in tools if str(tool) in ALLOWED_TOOLS]
        model_name = str(payload.get("model_name") or SETTINGS.default_agent_model)
        if model_name not in ALLOWED_MODELS:
            model_name = SETTINGS.default_agent_model
        payload["model_name"] = model_name
        try:
            temperature = float(payload.get("temperature", 0.2))
        except Exception:
            temperature = 0.2
        payload["temperature"] = max(0.0, min(1.0, temperature))
        return payload

    def generate_spec(self, prompt: str) -> AgentSpecDraft:
        raw = self.model.invoke(SPEC_PROMPT.format(prompt=prompt)).content
        payload = extract_json_object(str(raw))
        payload = self._normalize_spec_payload(payload)
        return AgentSpecDraft.model_validate(payload)

    def decide(self, message: str) -> RouteDecision:
        specs = [
            {
                "id": spec.id,
                "name": spec.name,
                "purpose": spec.purpose,
                "specialization": spec.specialization,
                "model_name": spec.model_name,
                "tools": spec.tools,
            }
            for spec in list_specs()
        ]
        instances = [
            {
                "id": inst.id,
                "spec_id": inst.spec_id,
                "display_name": inst.display_name,
                "memory_summary": inst.memory_summary[:300],
            }
            for inst in list_instances()
        ]
        raw = self.model.invoke(
            ROUTER_PROMPT.format(
                specs=json.dumps(specs, indent=2),
                instances=json.dumps(instances, indent=2),
                message=message,
            )
        ).content
        payload = extract_json_object(str(raw))
        if payload.get("new_spec"):
            payload["new_spec"] = AgentSpecDraft.model_validate(
                self._normalize_spec_payload(payload["new_spec"])
            )
        return RouteDecision.model_validate(payload)
