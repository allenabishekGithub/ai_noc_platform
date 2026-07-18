from __future__ import annotations

from typing import Any

from langchain.agents import AgentExecutor, create_tool_calling_agent
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.tools import BaseTool, StructuredTool

from .config import SETTINGS
from .llm import build_ollama_chat
from .models import AgentInstanceRecord, AgentSpecRecord
from .repository import (
    add_fact,
    get_instance,
    get_spec,
    list_facts,
    list_messages,
    search_facts,
)
from .utils import safe_eval


PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            """You are a small specialist agent.

Identity:
- Name: {agent_name}
- Purpose: {purpose}
- Specialization: {specialization}
- Memory policy: {memory_policy}

Core behavior:
{system_prompt}

Rolling memory summary:
{memory_summary}

Available tools:
{available_tools}

Quality guidance:
{quality_hint}

Use only the tools that are actually available in this run.
If the request needs external data or actions that you do not have tools for, say that clearly instead of improvising.
If MCP-backed controller inventory tools are available, use them for controller and topology questions.
For requests about TFS or controller devices, nodes, links, contexts, or topologies, prefer the discovered `mcp__...` tools before answering when those tools are available.
Do not claim that controller access is unavailable until you have checked the available MCP tools in this run.
Stay specialized. If the request is clearly outside your scope, say so.
""",
        ),
        MessagesPlaceholder("chat_history", optional=True),
        ("human", "{input}"),
        MessagesPlaceholder("agent_scratchpad"),
    ]
)


def _to_history(instance_id: str) -> list[Any]:
    history = []
    for msg in list_messages(instance_id, limit=SETTINGS.memory_window):
        if msg.role == "user":
            history.append(HumanMessage(content=msg.content))
        elif msg.role == "assistant":
            history.append(AIMessage(content=msg.content))
    return history


def _coerce_text(value: Any, default: str = "") -> str:
    if isinstance(value, dict):
        if "value" in value:
            return str(value["value"])
        return default
    if value is None:
        return default
    return str(value)


def _coerce_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    candidate = value
    if isinstance(value, dict):
        candidate = value.get("value", default)
    try:
        parsed = int(candidate)
    except Exception:
        parsed = default
    return max(minimum, min(maximum, parsed))


def build_executor(
    instance: AgentInstanceRecord,
    spec: AgentSpecRecord,
    external_tools: list[BaseTool] | None = None,
) -> AgentExecutor:
    llm = build_ollama_chat(spec.model_name, spec.temperature)

    def store_fact(text: str) -> str:
        add_fact(instance.id, text)
        return f"Stored fact for {instance.display_name}: {text}"

    def recall_memory(query: Any = "", limit: Any = 5) -> str:
        query_text = _coerce_text(query, "").strip()
        fact_limit = _coerce_int(limit, default=5, minimum=1, maximum=25)
        if query_text:
            facts = search_facts(instance.id, query_text, limit=fact_limit)
        else:
            facts = list_facts(instance.id, limit=fact_limit)
        if not facts:
            return "No matching facts in memory."
        return "\n".join(f"- {fact.content}" for fact in facts)

    def list_agent_facts(limit: Any = 10) -> str:
        fact_limit = _coerce_int(limit, default=10, minimum=1, maximum=25)
        facts = list_facts(instance.id, limit=fact_limit)
        if not facts:
            return "No facts stored. I do not have a live device inventory tool in this prototype."
        return "\n".join(f"- {fact.content}" for fact in facts)

    def calculate(expression: Any) -> str:
        try:
            return str(safe_eval(_coerce_text(expression)))
        except Exception as exc:
            return f"Calculation unavailable: {exc}"

    tools = [
        StructuredTool.from_function(store_fact, name="store_fact", description="Store a durable factual memory item relevant to this specialist."),
        StructuredTool.from_function(recall_memory, name="recall_memory", description="Recall memory facts for this specialist. Optionally filter by a text query."),
        StructuredTool.from_function(list_agent_facts, name="list_facts", description="List the most recent memory facts for this specialist."),
        StructuredTool.from_function(calculate, name="calculate", description="Evaluate a short arithmetic expression."),
    ]
    active_tools = [tool for tool in tools if tool.name in (spec.tools or [])]
    for tool in external_tools or []:
        if all(existing.name != tool.name for existing in active_tools):
            active_tools.append(tool)

    agent = create_tool_calling_agent(llm, active_tools, PROMPT)
    return AgentExecutor(
        agent=agent,
        tools=active_tools,
        verbose=False,
        return_intermediate_steps=True,
        max_iterations=8,
    )


def invoke_agent(
    instance_id: str,
    user_input: str,
    external_tools: list[BaseTool] | None = None,
    quality_hint: str = "",
) -> dict[str, Any]:
    instance = get_instance(instance_id)
    spec = get_spec(instance.spec_id)
    executor = build_executor(instance, spec, external_tools=external_tools)
    available_tools = "\n".join(
        f"- {tool.name}: {getattr(tool, 'description', '')}" for tool in (external_tools or [])
    ) or "No external tools."
    response = executor.invoke(
        {
            "input": user_input,
            "chat_history": _to_history(instance_id),
            "agent_name": instance.display_name,
            "purpose": spec.purpose,
            "specialization": spec.specialization,
            "memory_policy": spec.memory_policy,
            "system_prompt": spec.system_prompt,
            "memory_summary": instance.memory_summary or "No summary yet.",
            "available_tools": available_tools,
            "quality_hint": quality_hint or "Provide a direct, grounded answer. Do not stop at 'I will retrieve' or similar incomplete statements.",
        }
    )
    output = str(response.get("output", "")).strip()
    if not output:
        fallback = build_ollama_chat(spec.model_name, spec.temperature).invoke(
            f"""You are {instance.display_name}.
Purpose: {spec.purpose}
Specialization: {spec.specialization}
Memory summary: {instance.memory_summary or 'No summary yet.'}

Answer the user directly and concisely. Do not return an empty response.

User request:
{user_input}
"""
        ).content
        output = str(fallback).strip() or "No answer generated."
    return {
        "output": output,
        "intermediate_steps": response.get("intermediate_steps", []),
        "instance": instance,
        "spec": spec,
    }
