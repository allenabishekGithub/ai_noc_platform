from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone

from .models import ExecutionPlan, ExecutionTrace, GraphEdge, GraphNode, PlanStep


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ExecutionStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[str, ExecutionTrace] = {}

    def create(self, message: str) -> ExecutionTrace:
        execution = ExecutionTrace(
            id="exec-" + uuid.uuid4().hex[:12],
            message=message,
            status="planned",
            created_at=_now(),
            updated_at=_now(),
            nodes=[],
            edges=[],
        )
        with self._lock:
            self._items[execution.id] = execution
        return execution.model_copy(deep=True)

    def get(self, execution_id: str) -> ExecutionTrace:
        with self._lock:
            execution = self._items.get(execution_id)
            if execution is None:
                raise KeyError(f"Unknown execution_id: {execution_id}")
            return execution.model_copy(deep=True)

    def update(self, execution_id: str, **changes) -> ExecutionTrace:
        with self._lock:
            execution = self._items[execution_id]
            for key, value in changes.items():
                setattr(execution, key, value)
            execution.updated_at = _now()
            self._items[execution_id] = execution
            return execution.model_copy(deep=True)

    def set_plan(self, execution_id: str, plan: ExecutionPlan) -> ExecutionTrace:
        with self._lock:
            execution = self._items[execution_id]
            execution.plan = plan
            for step in plan.steps:
                node = self._plan_step_to_node(step)
                execution.nodes = [n for n in execution.nodes if n.id != node.id] + [node]
            for step in plan.steps:
                for dependency in step.depends_on:
                    edge = GraphEdge(source=dependency, target=step.id)
                    if not any(existing.source == edge.source and existing.target == edge.target for existing in execution.edges):
                        execution.edges.append(edge)
            execution.updated_at = _now()
            return execution.model_copy(deep=True)

    def add_node(self, execution_id: str, node: GraphNode) -> GraphNode:
        with self._lock:
            execution = self._items[execution_id]
            execution.nodes = [n for n in execution.nodes if n.id != node.id] + [node]
            execution.updated_at = _now()
        return node

    def set_node_status(self, execution_id: str, node_id: str, status: str, details: str | None = None) -> None:
        with self._lock:
            execution = self._items[execution_id]
            new_nodes = []
            for node in execution.nodes:
                if node.id == node_id:
                    node = node.model_copy(
                        update={
                            "status": status,
                            "details": details if details is not None else node.details,
                        }
                    )
                new_nodes.append(node)
            execution.nodes = new_nodes
            execution.updated_at = _now()

    def add_edge(self, execution_id: str, source: str, target: str) -> None:
        with self._lock:
            execution = self._items[execution_id]
            if any(edge.source == source and edge.target == target for edge in execution.edges):
                return
            execution.edges.append(GraphEdge(source=source, target=target))
            execution.updated_at = _now()

    def _plan_step_to_node(self, step: PlanStep) -> GraphNode:
        details = step.details
        if step.parameters:
            details = (details + "\n" if details else "") + f"params={step.parameters}"
        if step.parallel_group:
            details = (details + "\n" if details else "") + f"parallel_group={step.parallel_group}"
        return GraphNode(
            id=step.id,
            label=step.label,
            kind=step.kind,
            status=step.status,
            model=step.model,
            details=details,
        )
