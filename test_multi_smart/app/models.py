from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


AllowedModel = Literal[
    "qwen2.5:1.5b-instruct",
    "qwen2.5:3b-instruct",
    "qwen2.5:7b-instruct",
]


class AgentSpecDraft(BaseModel):
    name: str
    purpose: str
    specialization: str
    system_prompt: str
    model_name: AllowedModel = "qwen2.5:3b-instruct"
    temperature: float = 0.2
    memory_policy: str = "Retain short conversation history, explicit facts, and a rolling summary."
    tools: list[str] = Field(default_factory=list)


class AgentSpecRecord(AgentSpecDraft):
    id: str
    created_at: datetime


class AgentInstanceRecord(BaseModel):
    id: str
    spec_id: str
    display_name: str
    status: str
    memory_summary: str
    created_at: datetime
    last_used_at: datetime


class MessageRecord(BaseModel):
    id: int
    instance_id: str
    role: str
    content: str
    created_at: datetime


class FactRecord(BaseModel):
    id: int
    instance_id: str
    content: str
    created_at: datetime


class CreateSpecRequest(BaseModel):
    prompt: str


class CreateAgentRequest(BaseModel):
    spec_id: Optional[str] = None
    display_name: Optional[str] = None
    create_from_prompt: Optional[str] = None


class ChatRequest(BaseModel):
    message: str


class RouteRequest(BaseModel):
    message: str
    auto_create: bool = True


class RouteDecision(BaseModel):
    action: Literal["reuse", "create"]
    reason: str
    spec_id: Optional[str] = None
    instance_id: Optional[str] = None
    new_spec: Optional[AgentSpecDraft] = None


class ChatResult(BaseModel):
    instance: AgentInstanceRecord
    spec: AgentSpecRecord
    reply: str
    tool_trace: list[dict[str, Any]] = Field(default_factory=list)
    used_summary: str = ""


class RouteResult(BaseModel):
    decision: RouteDecision
    chat: Optional[ChatResult] = None


class PlanStep(BaseModel):
    id: str
    label: str
    kind: str
    status: Literal["planned", "processing", "completed", "failed"]
    model: Optional[str] = None
    details: str = ""
    parameters: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    parallel_group: Optional[str] = None


class ExecutionPlan(BaseModel):
    revision: int = 1
    summary: str = ""
    verified: bool = False
    review_notes: list[str] = Field(default_factory=list)
    steps: list[PlanStep] = Field(default_factory=list)


class GraphNode(BaseModel):
    id: str
    label: str
    kind: str
    status: Literal["planned", "processing", "completed", "failed"]
    model: Optional[str] = None
    details: str = ""


class GraphEdge(BaseModel):
    source: str
    target: str


class ExecutionTrace(BaseModel):
    id: str
    message: str
    status: Literal["planned", "processing", "completed", "failed"]
    created_at: datetime
    updated_at: datetime
    reply: str = ""
    error: str = ""
    decision: Optional[RouteDecision] = None
    instance_id: Optional[str] = None
    spec_id: Optional[str] = None
    plan: Optional[ExecutionPlan] = None
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)


class SessionStateRecord(BaseModel):
    session_id: str
    active_instance_id: Optional[str] = None
    active_spec_id: Optional[str] = None
    last_user_message: str = ""
    last_reply: str = ""
    pending_task: str = ""
    blackboard: dict[str, Any] = Field(default_factory=dict)
    updated_at: datetime


class KnowledgeItemRecord(BaseModel):
    id: str
    category: str
    key: str
    value: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime
