from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from .agent_runtime import invoke_agent
from .config import SETTINGS
from .execution import ExecutionStore
from .memory import refresh_summary_if_needed
from .mcp_client import MCPInventoryClient
from .models import (
    AgentInstanceRecord,
    AgentSpecDraft,
    AgentSpecRecord,
    ChatResult,
    ExecutionPlan,
    ExecutionTrace,
    GraphNode,
    PlanStep,
    RouteDecision,
    RouteResult,
    SessionStateRecord,
)
from .planner import Planner
from .repository import (
    add_message,
    create_instance,
    create_spec,
    get_domain_fact,
    get_instance,
    get_session_state,
    get_spec,
    get_verifier_patterns,
    list_instances,
    list_specs,
    resolve_dialogue_action,
    upsert_session_state,
)
from .verifier import evaluate_reply


DEFAULT_SESSION_ID = "default"


class MultiSmartService:
    def __init__(self) -> None:
        self.planner = Planner()
        self.executions = ExecutionStore()
        self.mcp_client = MCPInventoryClient(SETTINGS.mcp_servers)

    def list_specs(self) -> list[AgentSpecRecord]:
        return list_specs()

    def list_instances(self) -> list[AgentInstanceRecord]:
        return list_instances()

    def generate_spec(self, prompt: str) -> AgentSpecDraft:
        return self.planner.generate_spec(prompt)

    def create_spec(self, prompt: str) -> AgentSpecRecord:
        return create_spec(self.generate_spec(prompt))

    def instantiate(self, spec_id: str | None = None, create_from_prompt: str | None = None, display_name: str | None = None) -> AgentInstanceRecord:
        if spec_id is None:
            if not create_from_prompt:
                raise ValueError("spec_id or create_from_prompt is required")
            spec = create_spec(self.generate_spec(create_from_prompt))
            spec_id = spec.id
        return create_instance(spec_id, display_name=display_name)

    def chat(
        self,
        instance_id: str,
        message: str,
        execution_id: str | None = None,
        agent_node_id: str | None = None,
        verifier_node_id: str | None = None,
        *,
        session_id: str = DEFAULT_SESSION_ID,
        session_state: SessionStateRecord | None = None,
    ) -> ChatResult:
        session_state = session_state or get_session_state(session_id)
        blackboard = dict(session_state.blackboard or {})
        add_message(instance_id, "user", message)

        reply = ""
        trace: list[dict[str, Any]] = []
        pending_task = blackboard.get("pending_task", "")
        verifier_issues: list[str] = []
        outcome_instance = get_instance(instance_id)
        outcome_spec = get_spec(outcome_instance.spec_id)
        previous = agent_node_id

        fast_path = self._try_mcp_inventory_fastpath(message, blackboard, session_state)
        if fast_path is not None:
            reply = fast_path["reply"]
            trace = fast_path["trace"]
            blackboard = self._merge_blackboard(blackboard, fast_path.get("blackboard", {}))
            pending_task = fast_path.get("pending_task", pending_task)
        else:
            quality_hint = "Provide a direct, grounded answer using the available controller tools when relevant."
            outcome = invoke_agent(
                instance_id,
                message,
                external_tools=self.mcp_client.build_inventory_tools(),
                quality_hint=quality_hint,
            )
            reply = str(outcome["output"]).strip()
            trace = self._normalize_trace(outcome["intermediate_steps"])
            blackboard = self._update_blackboard_from_trace(blackboard, trace)
            outcome_instance = outcome["instance"]
            outcome_spec = get_spec(outcome["spec"].id)

        if execution_id and previous:
            previous = self._add_trace_nodes(execution_id, previous, trace)

        dialogue_action = self._dialogue_action(message)
        verifier_result = evaluate_reply(
            message,
            reply,
            blackboard=blackboard,
            tool_trace=trace,
            pending_task=pending_task,
            generic_bad_patterns=get_verifier_patterns(),
            dialogue_action=dialogue_action,
        )

        if execution_id and verifier_node_id:
            self.executions.set_node_status(execution_id, verifier_node_id, "processing")
        elif execution_id:
            verifier_node_id = f"{execution_id}-verifier"
            self.executions.add_node(
                execution_id,
                GraphNode(
                    id=verifier_node_id,
                    label="Verifier",
                    kind="verifier",
                    status="processing",
                    model=SETTINGS.planner_model,
                    details="Check whether the draft reply is grounded and complete.",
                ),
            )
            if previous:
                self.executions.add_edge(execution_id, previous, verifier_node_id)

        if not verifier_result.passed:
            verifier_issues = verifier_result.issues
            pending_task = verifier_result.pending_task or pending_task
            refined = self._refine_response(
                message,
                blackboard,
                session_state,
                instance_id,
                verifier_result.refinement_hint,
            )
            if refined is not None:
                reply = refined["reply"]
                trace.extend(refined["trace"])
                blackboard = self._merge_blackboard(blackboard, refined.get("blackboard", {}))
                if execution_id and (agent_node_id or verifier_node_id):
                    parent = verifier_node_id or agent_node_id
                    self._add_trace_nodes(execution_id, parent, refined["trace"], start_index=len(trace) - len(refined["trace"]) + 1)
                verifier_result = evaluate_reply(
                    message,
                    reply,
                    blackboard=blackboard,
                    tool_trace=trace,
                    pending_task=pending_task,
                    generic_bad_patterns=get_verifier_patterns(),
                    dialogue_action=dialogue_action,
                )
                verifier_issues = verifier_result.issues
                pending_task = verifier_result.pending_task or pending_task

        if verifier_node_id:
            status = "completed" if verifier_result.passed else "failed"
            details = "Reply accepted." if verifier_result.passed else "\n".join(verifier_issues)
            self.executions.set_node_status(execution_id, verifier_node_id, status, details)

        add_message(instance_id, "assistant", reply)
        blackboard["last_user_message"] = message
        blackboard["last_reply"] = reply
        blackboard["pending_task"] = pending_task if not verifier_result.passed else ""
        summary = refresh_summary_if_needed(instance_id)
        upsert_session_state(
            session_id=session_id,
            active_instance_id=instance_id,
            active_spec_id=outcome_spec.id,
            last_user_message=message,
            last_reply=reply,
            pending_task=blackboard.get("pending_task", ""),
            blackboard=blackboard,
        )

        return ChatResult(
            instance=get_instance(instance_id),
            spec=outcome_spec,
            reply=reply,
            tool_trace=trace,
            used_summary=summary,
        )

    def _normalize_trace(self, intermediate_steps: list[tuple[Any, Any]]) -> list[dict[str, Any]]:
        trace = []
        for action, observation in intermediate_steps:
            trace.append(
                {
                    "tool": getattr(action, "tool", "unknown"),
                    "tool_input": getattr(action, "tool_input", {}),
                    "observation": observation,
                }
            )
        return trace

    def _add_trace_nodes(
        self,
        execution_id: str,
        previous_node_id: str,
        trace: list[dict[str, Any]],
        *,
        start_index: int = 1,
    ) -> str:
        previous = previous_node_id
        for offset, item in enumerate(trace, start=start_index):
            node_id = f"{execution_id}-tool-{offset}"
            self.executions.add_node(
                execution_id,
                GraphNode(
                    id=node_id,
                    label=item["tool"],
                    kind="tool",
                    status="completed",
                    details=f"input={item['tool_input']}\nobservation={item['observation']}",
                ),
            )
            self.executions.add_edge(execution_id, previous, node_id)
            previous = node_id
        return previous

    def _merge_blackboard(self, blackboard: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
        merged = dict(blackboard or {})
        discovered = dict(merged.get("discovered") or {})
        patch_discovered = dict(patch.get("discovered") or {})
        for key, value in patch_discovered.items():
            discovered[key] = value
        merged["discovered"] = discovered
        for key, value in patch.items():
            if key != "discovered":
                merged[key] = value
        return merged

    def _update_blackboard_from_trace(self, blackboard: dict[str, Any], trace: list[dict[str, Any]]) -> dict[str, Any]:
        merged = dict(blackboard or {})
        discovered = dict(merged.get("discovered") or {})
        for item in trace:
            observation = item.get("observation")
            if isinstance(observation, str):
                try:
                    payload = json.loads(observation)
                except Exception:
                    continue
            else:
                payload = observation
            if not isinstance(payload, dict):
                continue
            if "devices" in payload:
                devices = payload.get("devices") or []
                discovered["devices"] = [
                    entry.get("device_id", {}).get("device_uuid", {}).get("uuid")
                    for entry in devices
                    if entry.get("device_id", {}).get("device_uuid", {}).get("uuid")
                ]
                discovered["routers"] = [
                    entry.get("device_id", {}).get("device_uuid", {}).get("uuid")
                    for entry in devices
                    if "router" in str(entry.get("device_type", "")).lower()
                ]
            if "links" in payload:
                discovered["links"] = [
                    entry.get("link_id", {}).get("link_uuid", {}).get("uuid")
                    for entry in payload.get("links") or []
                    if entry.get("link_id", {}).get("link_uuid", {}).get("uuid")
                ]
        merged["discovered"] = discovered
        return merged

    def _is_follow_up(self, message: str) -> bool:
        return self._dialogue_action(message) in {"continue_pending", "proceed_pending"}

    def _dialogue_action(self, message: str) -> str | None:
        return resolve_dialogue_action(message)

    def _route_with_session_context(self, message: str, session_state: SessionStateRecord) -> RouteDecision:
        dialogue_action = self._dialogue_action(message)
        if session_state.active_instance_id and (
            dialogue_action in {"continue_pending", "proceed_pending", "double_check"}
            or session_state.pending_task
        ):
            instance = get_instance(session_state.active_instance_id)
            return RouteDecision(
                action="reuse",
                reason="Continue the active specialist based on session context and pending work.",
                spec_id=instance.spec_id,
                instance_id=instance.id,
                new_spec=None,
            )
        return self.planner.decide(message)

    def _refine_response(
        self,
        message: str,
        blackboard: dict[str, Any],
        session_state: SessionStateRecord,
        instance_id: str,
        hint: str,
    ) -> dict[str, Any] | None:
        dialogue_action = self._dialogue_action(message)
        if dialogue_action == "double_check":
            target_message = session_state.pending_task or session_state.last_user_message or message
            return self._try_mcp_inventory_fastpath(
                target_message,
                blackboard,
                session_state,
                force_router_details="router" in target_message.lower() and "connect" in target_message.lower(),
                force_recheck=True,
            )
        should_force_router = (
            ("router" in message.lower() and "connect" in message.lower())
            or ("router" in session_state.pending_task.lower() and "connect" in session_state.pending_task.lower())
            or bool((blackboard.get("discovered") or {}).get("router_connections"))
        )
        refined = self._try_mcp_inventory_fastpath(
            message,
            blackboard,
            session_state,
            force_router_details=should_force_router,
        )
        if refined is not None:
            return refined
        if self._is_follow_up(message) and session_state.pending_task:
            return self._try_mcp_inventory_fastpath(
                session_state.pending_task,
                blackboard,
                session_state,
                force_router_details=should_force_router,
            )
        outcome = invoke_agent(
            instance_id,
            message,
            external_tools=self.mcp_client.build_inventory_tools(),
            quality_hint=f"Refine the answer and fix these issues: {hint}",
        )
        reply = str(outcome["output"]).strip()
        trace = self._normalize_trace(outcome["intermediate_steps"])
        patch = self._update_blackboard_from_trace({}, trace)
        return {
            "reply": reply,
            "trace": trace,
            "blackboard": patch,
            "pending_task": "",
        }

    def _router_connection_map(self, topology: dict[str, Any]) -> dict[str, list[str]]:
        devices = topology.get("devices") or []
        links = topology.get("links") or []
        router_ids = {
            entry.get("device_id", {}).get("device_uuid", {}).get("uuid")
            for entry in devices
            if "router" in str(entry.get("device_type", "")).lower()
        }
        router_ids = {item for item in router_ids if item}
        mapping: dict[str, set[str]] = {router: set() for router in router_ids}
        for link in links:
            endpoints = link.get("link_endpoint_ids") or []
            device_ids = [
                endpoint.get("device_id", {}).get("device_uuid", {}).get("uuid")
                for endpoint in endpoints
                if endpoint.get("device_id", {}).get("device_uuid", {}).get("uuid")
            ]
            for device_id in device_ids:
                if device_id in router_ids:
                    for neighbor in device_ids:
                        if neighbor != device_id:
                            mapping.setdefault(device_id, set()).add(neighbor)
        return {router: sorted(neighbors) for router, neighbors in mapping.items()}

    def _try_mcp_inventory_fastpath(
        self,
        message: str,
        blackboard: dict[str, Any] | None = None,
        session_state: SessionStateRecord | None = None,
        *,
        force_router_details: bool = False,
        force_recheck: bool = False,
    ) -> dict[str, Any] | None:
        if not self.mcp_client.has_mcp():
            return None
        text = message.lower()
        blackboard = dict(blackboard or {})
        discovered = dict(blackboard.get("discovered") or {})
        pending = blackboard.get("pending_task", "")
        dialogue_action = self._dialogue_action(message)
        default_context = get_domain_fact("default_context_uuid", "admin") or "admin"
        default_topology = get_domain_fact("default_topology_uuid", "admin") or "admin"

        if session_state and dialogue_action in {"continue_pending", "proceed_pending"} and session_state.pending_task:
            text = session_state.pending_task.lower()
            pending = session_state.pending_task
        elif session_state and dialogue_action in {"continue_pending", "proceed_pending"}:
            connections = discovered.get("router_connections") or {}
            if connections:
                lines = [
                    f"{router} is connected to {', '.join(neighbors)}."
                    for router, neighbors in sorted(connections.items())
                ]
                return {
                    "reply": "Continuing the previous result:\n" + "\n".join(lines),
                    "trace": [],
                    "blackboard": {"discovered": discovered, "pending_task": ""},
                    "pending_task": "",
                }
        elif session_state and dialogue_action == "double_check":
            text = (session_state.pending_task or session_state.last_user_message or message).lower()
            pending = session_state.pending_task or pending
            connections = discovered.get("router_connections") or {}
            if connections:
                result = self.mcp_client.call_tool(
                    "tfs_get_topology_details",
                    {"context_uuid": default_context, "topology_uuid": default_topology},
                )
                structured = result.get("structured") or {}
                connections = self._router_connection_map(structured)
                lines = [
                    f"{router} is connected to {', '.join(neighbors)}."
                    for router, neighbors in sorted(connections.items())
                ]
                discovered["router_connections"] = connections
                return {
                    "reply": "Double-check confirmed:\n" + "\n".join(lines),
                    "trace": [
                        {
                            "tool": "mcp_get_topology_details",
                            "tool_input": {"context_uuid": default_context, "topology_uuid": default_topology},
                            "observation": json.dumps(structured, ensure_ascii=True, indent=2),
                        }
                    ],
                    "blackboard": {"discovered": discovered, "pending_task": ""},
                    "pending_task": "",
                }

        if ("router" in text and "connect" in text) or force_router_details:
            result = self.mcp_client.call_tool(
                "tfs_get_topology_details",
                {"context_uuid": default_context, "topology_uuid": default_topology},
            )
            structured = result.get("structured") or {}
            connections = self._router_connection_map(structured)
            if not connections:
                return None
            lines = []
            for router in sorted(connections.keys()):
                neighbors = connections.get(router) or []
                if neighbors:
                    lines.append(f"{router} is connected to {', '.join(neighbors)}.")
                else:
                    lines.append(f"{router} has no discovered connected device.")
            discovered["router_connections"] = connections
            discovered["routers"] = sorted(connections.keys())
            prefix = "Double-check confirmed:\n" if force_recheck or dialogue_action == "double_check" else ""
            return {
                "reply": prefix + "\n".join(lines),
                "trace": [
                    {
                        "tool": "mcp_get_topology_details",
                        "tool_input": {"context_uuid": default_context, "topology_uuid": default_topology},
                        "observation": json.dumps(structured, ensure_ascii=True, indent=2),
                    }
                ],
                "blackboard": {"discovered": discovered, "pending_task": ""},
                "pending_task": "",
            }

        if "device" in text and "link" in text and any(term in text for term in ("list", "show", "existing", "inventory", "use mcp", "which", "plot", "draw", "map")):
            device_result = self.mcp_client.call_tool("tfs_list_devices", {})
            link_result = self.mcp_client.call_tool("tfs_list_links", {})
            device_payload = device_result.get("structured") or {}
            link_payload = link_result.get("structured") or {}
            names, routers = self._extract_devices(device_payload.get("devices") or [])
            link_ids = self._extract_link_ids(link_payload.get("links") or [])
            discovered["devices"] = names
            discovered["routers"] = routers
            discovered["links"] = link_ids
            reply = (
                f"Devices ({len(names)}): {', '.join(names)}.\n"
                f"Links ({len(link_ids)}): {', '.join(link_ids)}."
            )
            return {
                "reply": reply,
                "trace": [
                    {
                        "tool": "mcp_list_devices",
                        "tool_input": {},
                        "observation": json.dumps(device_payload, ensure_ascii=True, indent=2),
                    },
                    {
                        "tool": "mcp_list_links",
                        "tool_input": {},
                        "observation": json.dumps(link_payload, ensure_ascii=True, indent=2),
                    },
                ],
                "blackboard": {"discovered": discovered},
                "pending_task": "",
            }

        if not any(term in text for term in ("list", "show", "existing", "inventory", "use mcp", "which", "plot", "draw", "map")):
            return None

        if "router" in text:
            result = self.mcp_client.call_tool("tfs_list_devices", {})
            structured = result.get("structured") or {}
            devices = structured.get("devices") or []
            routers = [
                entry.get("device_id", {}).get("device_uuid", {}).get("uuid")
                for entry in devices
                if "router" in str(entry.get("device_type", "")).lower()
            ]
            routers = [router for router in routers if router]
            discovered["routers"] = routers
            reply = (
                f"The TFS controller reports the following routers: {', '.join(routers)}."
                if routers
                else "The TFS controller returned no routers."
            )
            return {
                "reply": reply,
                "trace": [
                    {
                        "tool": "mcp_list_devices",
                        "tool_input": {},
                        "observation": json.dumps(structured, ensure_ascii=True, indent=2),
                    }
                ],
                "blackboard": {"discovered": discovered},
                "pending_task": pending,
            }

        if "device" in text:
            result = self.mcp_client.call_tool("tfs_list_devices", {})
            return self._format_device_inventory(result, tool_label="mcp_list_devices", noun="devices")
        if "node" in text:
            result = self.mcp_client.call_tool("tfs_list_devices", {})
            return self._format_device_inventory(result, tool_label="mcp_list_nodes", noun="nodes")
        if "link" in text:
            result = self.mcp_client.call_tool("tfs_list_links", {})
            structured = result.get("structured") or {}
            links = structured.get("links") or []
            link_ids = []
            for item in links:
                uuid = (
                    item.get("link_id", {})
                    .get("link_uuid", {})
                    .get("uuid")
                )
                if uuid:
                    link_ids.append(str(uuid))
            reply = (
                f"TFS controller reports {len(link_ids)} links: {', '.join(link_ids)}."
                if link_ids
                else "TFS controller returned no links."
            )
            return {
                "reply": reply,
                "trace": [
                    {
                        "tool": "mcp_list_links",
                        "tool_input": {},
                        "observation": json.dumps(structured or result.get("text", {}), ensure_ascii=True, indent=2),
                    }
                ],
                "blackboard": {"discovered": {"links": link_ids}},
                "pending_task": pending,
            }
        return None

    def _format_device_inventory(
        self,
        result: dict[str, Any],
        tool_label: str,
        noun: str,
    ) -> dict[str, Any]:
        structured = result.get("structured") or {}
        devices = structured.get("devices") or []
        names = []
        routers = []
        for item in devices:
            uuid = (
                item.get("device_id", {})
                .get("device_uuid", {})
                .get("uuid")
            )
            if uuid:
                names.append(str(uuid))
                if "router" in str(item.get("device_type", "")).lower():
                    routers.append(str(uuid))
        reply = (
            f"TFS controller reports {len(names)} {noun}: {', '.join(names)}."
            if names
            else f"TFS controller returned no {noun}."
        )
        return {
            "reply": reply,
            "trace": [
                {
                    "tool": tool_label,
                    "tool_input": {},
                    "observation": json.dumps(structured or result.get("text", {}), ensure_ascii=True, indent=2),
                }
            ],
            "blackboard": {"discovered": {"devices": names, "routers": routers}},
            "pending_task": "",
        }

    def route(self, message: str, auto_create: bool = True) -> RouteResult:
        session_state = get_session_state(DEFAULT_SESSION_ID)
        decision = self._route_with_session_context(message, session_state)
        if decision.action == "reuse":
            if not decision.instance_id:
                instance = create_instance(decision.spec_id or list_specs()[0].id)
                decision.instance_id = instance.id
            return RouteResult(
                decision=decision,
                chat=self.chat(
                    decision.instance_id,
                    message,
                    session_id=DEFAULT_SESSION_ID,
                    session_state=session_state,
                ),
            )

        if not auto_create:
            return RouteResult(decision=decision, chat=None)

        if decision.new_spec is None:
            raise ValueError("Planner requested create but did not provide new_spec")
        spec = create_spec(decision.new_spec)
        instance = create_instance(spec.id)
        decision.spec_id = spec.id
        decision.instance_id = instance.id
        return RouteResult(
            decision=decision,
            chat=self.chat(
                instance.id,
                message,
                session_id=DEFAULT_SESSION_ID,
                session_state=session_state,
            ),
        )

    def submit(self, message: str, auto_create: bool = True) -> ExecutionTrace:
        execution = self.executions.create(message)
        self.executions.add_node(
            execution.id,
            GraphNode(
                id=f"{execution.id}-request",
                label="Request",
                kind="request",
                status="completed",
                details=message,
            ),
        )
        self.executions.add_node(
            execution.id,
            GraphNode(
                id=f"{execution.id}-planner",
                label="Planner",
                kind="planner",
                status="planned",
                model=SETTINGS.planner_model,
                details="Decide how to solve the request and expand the execution DAG.",
            ),
        )
        self.executions.add_edge(execution.id, f"{execution.id}-request", f"{execution.id}-planner")
        thread = threading.Thread(
            target=self._run_execution,
            args=(execution.id, message, auto_create),
            daemon=True,
        )
        thread.start()
        return self.executions.get(execution.id)

    def get_execution(self, execution_id: str) -> ExecutionTrace:
        return self.executions.get(execution_id)

    def _run_execution(self, execution_id: str, message: str, auto_create: bool) -> None:
        planner_node = f"{execution_id}-planner"
        self.executions.update(execution_id, status="processing")
        self.executions.set_node_status(execution_id, planner_node, "processing")
        try:
            session_state = get_session_state(DEFAULT_SESSION_ID)
            decision = self._route_with_session_context(message, session_state)
            self.executions.update(execution_id, decision=decision)
            self.executions.set_node_status(execution_id, planner_node, "completed", decision.reason)
            blueprint = self._build_execution_plan(execution_id, message, decision, session_state, auto_create)
            plan = blueprint["plan"]
            self.executions.set_plan(execution_id, plan)

            review_node = blueprint["node_ids"]["review"]
            self.executions.set_node_status(execution_id, review_node, "processing")
            reviewed_plan = self._review_execution_plan(plan, blueprint["mode"])
            self.executions.set_plan(execution_id, reviewed_plan)
            self.executions.set_node_status(
                execution_id,
                review_node,
                "completed",
                "\n".join(reviewed_plan.review_notes) or "Plan verified without changes.",
            )

            if blueprint["mode"].startswith("inventory_"):
                outcome = self._execute_inventory_plan(
                    execution_id,
                    message,
                    session_state,
                    blueprint,
                )
                self.executions.update(
                    execution_id,
                    status="completed",
                    reply=outcome["reply"],
                    decision=decision,
                )
                return

            if decision.action == "reuse":
                if not decision.instance_id:
                    instance = create_instance(decision.spec_id or list_specs()[0].id)
                    decision.instance_id = instance.id
                instance = get_instance(decision.instance_id)
                spec = get_spec(instance.spec_id)
            else:
                if not auto_create:
                    self.executions.update(execution_id, status="completed", reply="", decision=decision)
                    return
                if decision.new_spec is None:
                    raise ValueError("Planner requested create but did not provide new_spec")

                spec_node = blueprint["node_ids"]["spec"]
                self.executions.set_node_status(execution_id, spec_node, "processing")
                spec = create_spec(decision.new_spec)
                self.executions.set_node_status(
                    execution_id,
                    spec_node,
                    "completed",
                    f"{spec.specialization}\nmodel={spec.model_name}",
                )

                instance_node = blueprint["node_ids"]["instance"]
                self.executions.set_node_status(execution_id, instance_node, "processing")
                instance = create_instance(spec.id)
                self.executions.set_node_status(
                    execution_id,
                    instance_node,
                    "completed",
                    f"instance_id={instance.id}",
                )
                decision.spec_id = spec.id
                decision.instance_id = instance.id
                self.executions.update(execution_id, decision=decision)

            agent_node = blueprint["node_ids"]["agent"]
            self.executions.set_node_status(execution_id, agent_node, "processing")
            chat = self.chat(
                instance.id,
                message,
                execution_id=execution_id,
                agent_node_id=agent_node,
                verifier_node_id=blueprint["node_ids"]["verifier"],
                session_id=DEFAULT_SESSION_ID,
                session_state=session_state,
            )
            self.executions.set_node_status(
                execution_id,
                agent_node,
                "completed",
                f"{spec.specialization}\nmodel={spec.model_name}\nsummary={chat.used_summary or 'none'}",
            )
            self.executions.update(
                execution_id,
                status="completed",
                reply=chat.reply,
                instance_id=chat.instance.id,
                spec_id=chat.spec.id,
                decision=decision,
            )
        except Exception as exc:
            self.executions.set_node_status(execution_id, planner_node, "failed", str(exc))
            self.executions.update(execution_id, status="failed", error=str(exc))

    def _build_execution_plan(
        self,
        execution_id: str,
        message: str,
        decision: RouteDecision,
        session_state: SessionStateRecord,
        auto_create: bool,
    ) -> dict[str, Any]:
        mode = self._inventory_mode_for_message(message, session_state)
        review_id = f"{execution_id}-review"
        steps: list[PlanStep] = [
            PlanStep(
                id=review_id,
                label="Review Plan",
                kind="review",
                status="planned",
                model=SETTINGS.planner_model,
                details="Validate dependencies, add verification, and prepare branching.",
                depends_on=[f"{execution_id}-planner"],
            )
        ]
        node_ids: dict[str, str] = {"review": review_id}

        if mode == "inventory_router_dc_links":
            device_step = f"{execution_id}-devices"
            link_step = f"{execution_id}-links"
            analyze_step = f"{execution_id}-match-links"
            compose_step = f"{execution_id}-compose"
            verify_step = f"{execution_id}-verifier"
            steps.extend(
                [
                    PlanStep(
                        id=device_step,
                        label="tfs_list_devices",
                        kind="tool",
                        status="planned",
                        details="Fetch device inventory for role classification.",
                        depends_on=[review_id],
                        parallel_group="inventory-discovery",
                    ),
                    PlanStep(
                        id=link_step,
                        label="tfs_list_links",
                        kind="tool",
                        status="planned",
                        details="Fetch link inventory to inspect router-to-DC edges.",
                        depends_on=[review_id],
                        parallel_group="inventory-discovery",
                    ),
                    PlanStep(
                        id=analyze_step,
                        label="Match Router-DC Links",
                        kind="reasoning",
                        status="planned",
                        model=SETTINGS.planner_model,
                        details="Combine devices and links to isolate the router-to-DC links.",
                        depends_on=[device_step, link_step],
                    ),
                    PlanStep(
                        id=compose_step,
                        label="Compose Reply",
                        kind="reasoning",
                        status="planned",
                        details="Draft a direct answer using the derived links.",
                        depends_on=[analyze_step],
                    ),
                    PlanStep(
                        id=verify_step,
                        label="Verifier",
                        kind="verifier",
                        status="planned",
                        model=SETTINGS.planner_model,
                        details="Check that the answer names the requested links.",
                        depends_on=[compose_step],
                    ),
                ]
            )
            node_ids.update(
                {
                    "devices": device_step,
                    "links": link_step,
                    "analyze": analyze_step,
                    "compose": compose_step,
                    "verifier": verify_step,
                }
            )
            summary = "Review the plan, fetch devices and links in parallel, isolate router-to-DC links, compose the answer, and verify it."
        elif mode == "inventory_router_connections":
            device_step = f"{execution_id}-devices"
            link_step = f"{execution_id}-links"
            analyze_step = f"{execution_id}-map-router-neighbors"
            compose_step = f"{execution_id}-compose"
            verify_step = f"{execution_id}-verifier"
            steps.extend(
                [
                    PlanStep(
                        id=device_step,
                        label="tfs_list_devices",
                        kind="tool",
                        status="planned",
                        details="Fetch devices to identify routers.",
                        depends_on=[review_id],
                        parallel_group="inventory-discovery",
                    ),
                    PlanStep(
                        id=link_step,
                        label="tfs_list_links",
                        kind="tool",
                        status="planned",
                        details="Fetch links to derive adjacency for each router.",
                        depends_on=[review_id],
                        parallel_group="inventory-discovery",
                    ),
                    PlanStep(
                        id=analyze_step,
                        label="Map Router Connectivity",
                        kind="reasoning",
                        status="planned",
                        model=SETTINGS.planner_model,
                        details="Build per-router adjacency from the link endpoints.",
                        depends_on=[device_step, link_step],
                    ),
                    PlanStep(
                        id=compose_step,
                        label="Compose Reply",
                        kind="reasoning",
                        status="planned",
                        details="Draft a complete per-router answer.",
                        depends_on=[analyze_step],
                    ),
                    PlanStep(
                        id=verify_step,
                        label="Verifier",
                        kind="verifier",
                        status="planned",
                        model=SETTINGS.planner_model,
                        details="Check that every router is covered in the answer.",
                        depends_on=[compose_step],
                    ),
                ]
            )
            node_ids.update(
                {
                    "devices": device_step,
                    "links": link_step,
                    "analyze": analyze_step,
                    "compose": compose_step,
                    "verifier": verify_step,
                }
            )
            summary = "Review the plan, fetch devices and links in parallel, derive router adjacency, compose the answer, and verify it."
        elif mode == "inventory_devices_and_links":
            device_step = f"{execution_id}-devices"
            link_step = f"{execution_id}-links"
            compose_step = f"{execution_id}-compose"
            verify_step = f"{execution_id}-verifier"
            steps.extend(
                [
                    PlanStep(
                        id=device_step,
                        label="tfs_list_devices",
                        kind="tool",
                        status="planned",
                        details="Fetch controller devices.",
                        depends_on=[review_id],
                        parallel_group="inventory-discovery",
                    ),
                    PlanStep(
                        id=link_step,
                        label="tfs_list_links",
                        kind="tool",
                        status="planned",
                        details="Fetch controller links.",
                        depends_on=[review_id],
                        parallel_group="inventory-discovery",
                    ),
                    PlanStep(
                        id=compose_step,
                        label="Compose Reply",
                        kind="reasoning",
                        status="planned",
                        details="Draft a combined devices-and-links answer.",
                        depends_on=[device_step, link_step],
                    ),
                    PlanStep(
                        id=verify_step,
                        label="Verifier",
                        kind="verifier",
                        status="planned",
                        model=SETTINGS.planner_model,
                        details="Check that the reply covers both devices and links.",
                        depends_on=[compose_step],
                    ),
                ]
            )
            node_ids.update(
                {
                    "devices": device_step,
                    "links": link_step,
                    "compose": compose_step,
                    "verifier": verify_step,
                }
            )
            summary = "Review the plan, fetch devices and links in parallel, compose a combined view, and verify the answer."
        elif mode in {"inventory_devices", "inventory_nodes", "inventory_routers", "inventory_links"}:
            tool_name = {
                "inventory_devices": "tfs_list_devices",
                "inventory_nodes": "tfs_list_devices",
                "inventory_routers": "tfs_list_devices",
                "inventory_links": "tfs_list_links",
            }[mode]
            tool_step = f"{execution_id}-inventory"
            compose_step = f"{execution_id}-compose"
            verify_step = f"{execution_id}-verifier"
            compose_detail = {
                "inventory_devices": "Draft the device inventory answer.",
                "inventory_nodes": "Draft the node inventory answer.",
                "inventory_routers": "Draft the router inventory answer.",
                "inventory_links": "Draft the link inventory answer.",
            }[mode]
            steps.extend(
                [
                    PlanStep(
                        id=tool_step,
                        label=tool_name,
                        kind="tool",
                        status="planned",
                        details="Fetch controller inventory from MCP.",
                        depends_on=[review_id],
                    ),
                    PlanStep(
                        id=compose_step,
                        label="Compose Reply",
                        kind="reasoning",
                        status="planned",
                        details=compose_detail,
                        depends_on=[tool_step],
                    ),
                    PlanStep(
                        id=verify_step,
                        label="Verifier",
                        kind="verifier",
                        status="planned",
                        model=SETTINGS.planner_model,
                        details="Check that the reply matches the requested inventory.",
                        depends_on=[compose_step],
                    ),
                ]
            )
            node_ids.update(
                {
                    "tool": tool_step,
                    "compose": compose_step,
                    "verifier": verify_step,
                }
            )
            summary = "Review the plan, fetch the requested inventory, compose the answer, and verify it."
        else:
            verify_step = f"{execution_id}-verifier"
            if decision.action == "create":
                if not auto_create:
                    raise ValueError("Planner requested specialist creation but auto_create is disabled")
                if decision.new_spec is None:
                    raise ValueError("Planner requested create but did not provide new_spec")
                spec_step = f"{execution_id}-spec"
                instance_step = f"{execution_id}-instance"
                agent_step = f"{execution_id}-agent"
                steps.extend(
                    [
                        PlanStep(
                            id=spec_step,
                            label=decision.new_spec.name,
                            kind="spec",
                            status="planned",
                            model=decision.new_spec.model_name,
                            details=decision.new_spec.specialization,
                            depends_on=[review_id],
                        ),
                        PlanStep(
                            id=instance_step,
                            label=decision.new_spec.name,
                            kind="instance",
                            status="planned",
                            model=decision.new_spec.model_name,
                            details="Instantiate the specialist agent.",
                            depends_on=[spec_step],
                        ),
                        PlanStep(
                            id=agent_step,
                            label=decision.new_spec.name,
                            kind="agent",
                            status="planned",
                            model=decision.new_spec.model_name,
                            details="Execute the specialist against the request.",
                            depends_on=[instance_step],
                        ),
                        PlanStep(
                            id=verify_step,
                            label="Verifier",
                            kind="verifier",
                            status="planned",
                            model=SETTINGS.planner_model,
                            details="Check that the specialist reply is grounded and complete.",
                            depends_on=[agent_step],
                        ),
                    ]
                )
                node_ids.update(
                    {
                        "spec": spec_step,
                        "instance": instance_step,
                        "agent": agent_step,
                        "verifier": verify_step,
                    }
                )
            else:
                agent_label = "Active Specialist"
                agent_model = None
                if decision.instance_id:
                    instance = get_instance(decision.instance_id)
                    spec = get_spec(instance.spec_id)
                    agent_label = instance.display_name
                    agent_model = spec.model_name
                elif decision.spec_id:
                    spec = get_spec(decision.spec_id)
                    agent_label = spec.name
                    agent_model = spec.model_name
                agent_step = f"{execution_id}-agent"
                steps.extend(
                    [
                        PlanStep(
                            id=agent_step,
                            label=agent_label,
                            kind="agent",
                            status="planned",
                            model=agent_model,
                            details="Execute the selected specialist.",
                            depends_on=[review_id],
                        ),
                        PlanStep(
                            id=verify_step,
                            label="Verifier",
                            kind="verifier",
                            status="planned",
                            model=SETTINGS.planner_model,
                            details="Check that the specialist reply is grounded and complete.",
                            depends_on=[agent_step],
                        ),
                    ]
                )
                node_ids.update({"agent": agent_step, "verifier": verify_step})
            summary = "Review the plan, run the specialist path, and verify the final answer."

        plan = ExecutionPlan(
            revision=1,
            summary=summary,
            verified=False,
            review_notes=[],
            steps=steps,
        )
        return {"mode": mode or "specialist", "plan": plan, "node_ids": node_ids}

    def _inventory_mode_for_message(self, message: str, session_state: SessionStateRecord) -> str | None:
        text = message.lower()
        dialogue_action = self._dialogue_action(message)
        inventory_verbs = ("which", "list", "show", "have", "existing", "plot", "draw", "map")
        if dialogue_action in {"continue_pending", "proceed_pending", "double_check"} and session_state.pending_task:
            text = session_state.pending_task.lower()
        if "device" in text and "link" in text and any(term in text for term in inventory_verbs):
            return "inventory_devices_and_links"
        if "link" in text and "router" in text and "dc" in text:
            return "inventory_router_dc_links"
        if "router" in text and "connect" in text:
            return "inventory_router_connections"
        if "router" in text and any(term in text for term in inventory_verbs):
            return "inventory_routers"
        if "link" in text and any(term in text for term in inventory_verbs):
            return "inventory_links"
        if "device" in text and any(term in text for term in inventory_verbs):
            return "inventory_devices"
        if "node" in text and any(term in text for term in inventory_verbs):
            return "inventory_nodes"
        return None

    def _review_execution_plan(self, plan: ExecutionPlan, mode: str) -> ExecutionPlan:
        reviewed = plan.model_copy(deep=True)
        notes: list[str] = []
        step_ids = {step.id for step in reviewed.steps}
        last_id = reviewed.steps[-1].id if reviewed.steps else ""
        if not any(step.kind == "verifier" for step in reviewed.steps) and last_id:
            verifier_id = f"{last_id}-verifier"
            reviewed.steps.append(
                PlanStep(
                    id=verifier_id,
                    label="Verifier",
                    kind="verifier",
                    status="planned",
                    model=SETTINGS.planner_model,
                    details="Added by plan review to verify the final answer.",
                    depends_on=[last_id],
                )
            )
            notes.append("Added a final verifier step.")
            step_ids.add(verifier_id)
        if mode in {"inventory_router_connections", "inventory_router_dc_links"}:
            if not any(step.label == "tfs_list_devices" for step in reviewed.steps):
                reviewed.steps.append(
                    PlanStep(
                        id=f"{reviewed.steps[0].id}-devices",
                        label="tfs_list_devices",
                        kind="tool",
                        status="planned",
                        details="Added by plan review for router classification.",
                        depends_on=[reviewed.steps[0].id],
                        parallel_group="inventory-discovery",
                    )
                )
                notes.append("Added missing device discovery for topology reasoning.")
            if not any(step.label == "tfs_list_links" for step in reviewed.steps):
                reviewed.steps.append(
                    PlanStep(
                        id=f"{reviewed.steps[0].id}-links",
                        label="tfs_list_links",
                        kind="tool",
                        status="planned",
                        details="Added by plan review for link reasoning.",
                        depends_on=[reviewed.steps[0].id],
                        parallel_group="inventory-discovery",
                    )
                )
                notes.append("Added missing link discovery for topology reasoning.")
        self._assert_plan_is_dag(reviewed.steps)
        if not notes:
            notes.append("Plan verified. Dependencies are acyclic and verification is included.")
        reviewed.verified = True
        reviewed.review_notes = notes
        reviewed.revision += 1
        return reviewed

    def _assert_plan_is_dag(self, steps: list[PlanStep]) -> None:
        indegree: dict[str, int] = {step.id: 0 for step in steps}
        outgoing: dict[str, list[str]] = {step.id: [] for step in steps}
        for step in steps:
            for dependency in step.depends_on:
                if dependency not in indegree:
                    continue
                indegree[step.id] += 1
                outgoing[dependency].append(step.id)
        queue = [step_id for step_id, count in indegree.items() if count == 0]
        visited = 0
        while queue:
            current = queue.pop(0)
            visited += 1
            for target in outgoing.get(current, []):
                indegree[target] -= 1
                if indegree[target] == 0:
                    queue.append(target)
        if visited != len(steps):
            raise ValueError("Execution plan contains a cycle.")

    def _execute_inventory_plan(
        self,
        execution_id: str,
        message: str,
        session_state: SessionStateRecord,
        blueprint: dict[str, Any],
    ) -> dict[str, Any]:
        mode = blueprint["mode"]
        node_ids = blueprint["node_ids"]
        blackboard = dict(session_state.blackboard or {})
        discovered = dict(blackboard.get("discovered") or {})
        trace: list[dict[str, Any]] = []

        if mode == "inventory_devices_and_links":
            results = self._run_parallel_tool_steps(
                execution_id,
                [
                    (node_ids["devices"], "tfs_list_devices", {}),
                    (node_ids["links"], "tfs_list_links", {}),
                ],
            )
            device_result = results[node_ids["devices"]]
            link_result = results[node_ids["links"]]
            trace.extend([device_result["trace"], link_result["trace"]])
            names, routers = self._extract_devices((device_result["result"].get("structured") or {}).get("devices") or [])
            link_ids = self._extract_link_ids((link_result["result"].get("structured") or {}).get("links") or [])
            discovered["devices"] = names
            discovered["routers"] = routers
            discovered["links"] = link_ids
            reply = (
                f"Devices ({len(names)}): {', '.join(names)}.\n"
                f"Links ({len(link_ids)}): {', '.join(link_ids)}."
            )
        elif mode in {"inventory_router_connections", "inventory_router_dc_links"}:
            results = self._run_parallel_tool_steps(
                execution_id,
                [
                    (node_ids["devices"], "tfs_list_devices", {}),
                    (node_ids["links"], "tfs_list_links", {}),
                ],
            )
            device_result = results[node_ids["devices"]]
            link_result = results[node_ids["links"]]
            trace.extend([device_result["trace"], link_result["trace"]])
            devices = (device_result["result"].get("structured") or {}).get("devices") or []
            links = (link_result["result"].get("structured") or {}).get("links") or []

            self.executions.set_node_status(execution_id, node_ids["analyze"], "processing")
            if mode == "inventory_router_connections":
                connections = self._router_connection_map_from_inventory(devices, links)
                discovered["router_connections"] = connections
                discovered["routers"] = sorted(connections.keys())
                analysis_details = f"routers={len(connections)}"
                lines = [
                    f"{router} is connected to {', '.join(neighbors)}."
                    if neighbors
                    else f"{router} has no discovered connected device."
                    for router, neighbors in sorted(connections.items())
                ]
                reply = "\n".join(lines) if lines else "No router connectivity could be derived from the controller inventory."
            else:
                link_ids = self._router_dc_link_ids_from_inventory(devices, links)
                discovered["router_dc_links"] = link_ids
                analysis_details = f"router_dc_links={len(link_ids)}"
                reply = (
                    "Links connecting routers with DCs: " + ", ".join(link_ids) + "."
                    if link_ids
                    else "No router-to-DC links were found in the controller inventory."
                )
            self.executions.set_node_status(execution_id, node_ids["analyze"], "completed", analysis_details)
        else:
            tool_name = {
                "inventory_devices": "tfs_list_devices",
                "inventory_nodes": "tfs_list_devices",
                "inventory_routers": "tfs_list_devices",
                "inventory_links": "tfs_list_links",
            }[mode]
            tool_outcome = self._run_tool_step(execution_id, node_ids["tool"], tool_name, {})
            trace.append(tool_outcome["trace"])
            structured = tool_outcome["result"].get("structured") or {}
            if mode == "inventory_links":
                link_ids = self._extract_link_ids(structured.get("links") or [])
                discovered["links"] = link_ids
                reply = (
                    f"TFS controller reports {len(link_ids)} links: {', '.join(link_ids)}."
                    if link_ids
                    else "TFS controller returned no links."
                )
            else:
                names, routers = self._extract_devices(structured.get("devices") or [])
                discovered["devices"] = names
                discovered["routers"] = routers
                noun = {
                    "inventory_devices": "devices",
                    "inventory_nodes": "nodes",
                    "inventory_routers": "routers",
                }[mode]
                if mode == "inventory_routers":
                    reply = (
                        f"The TFS controller reports the following routers: {', '.join(routers)}."
                        if routers
                        else "The TFS controller returned no routers."
                    )
                else:
                    shown = routers if mode == "inventory_routers" else names
                    reply = (
                        f"TFS controller reports {len(shown)} {noun}: {', '.join(shown)}."
                        if shown
                        else f"TFS controller returned no {noun}."
                    )

        self.executions.set_node_status(execution_id, node_ids["compose"], "processing")
        self.executions.set_node_status(
            execution_id,
            node_ids["compose"],
            "completed",
            f"reply_preview={reply[:180]}",
        )

        blackboard["discovered"] = discovered
        blackboard["last_user_message"] = message
        blackboard["last_reply"] = reply
        blackboard["pending_task"] = ""

        dialogue_action = self._dialogue_action(message)
        self.executions.set_node_status(execution_id, node_ids["verifier"], "processing")
        verifier_result = evaluate_reply(
            message,
            reply,
            blackboard=blackboard,
            tool_trace=trace,
            pending_task="",
            generic_bad_patterns=get_verifier_patterns(),
            dialogue_action=dialogue_action,
        )
        verifier_details = "Reply accepted." if verifier_result.passed else "\n".join(verifier_result.issues)
        self.executions.set_node_status(
            execution_id,
            node_ids["verifier"],
            "completed" if verifier_result.passed else "failed",
            verifier_details,
        )
        if not verifier_result.passed:
            raise ValueError(verifier_details)

        upsert_session_state(
            session_id=DEFAULT_SESSION_ID,
            active_instance_id=session_state.active_instance_id,
            active_spec_id=session_state.active_spec_id,
            last_user_message=message,
            last_reply=reply,
            pending_task="",
            blackboard=blackboard,
        )
        return {"reply": reply, "trace": trace}

    def _run_parallel_tool_steps(
        self,
        execution_id: str,
        calls: list[tuple[str, str, dict[str, Any]]],
    ) -> dict[str, dict[str, Any]]:
        for step_id, _, _ in calls:
            self.executions.set_node_status(execution_id, step_id, "processing")
        results: dict[str, dict[str, Any]] = {}
        with ThreadPoolExecutor(max_workers=max(1, len(calls))) as pool:
            future_map = {
                pool.submit(self.mcp_client.call_tool, tool_name, arguments): (step_id, tool_name, arguments)
                for step_id, tool_name, arguments in calls
            }
            for future in as_completed(future_map):
                step_id, tool_name, arguments = future_map[future]
                try:
                    result = future.result()
                except Exception as exc:
                    self.executions.set_node_status(execution_id, step_id, "failed", str(exc))
                    raise
                self.executions.set_node_status(
                    execution_id,
                    step_id,
                    "completed",
                    f"tool={tool_name}\nparams={arguments}",
                )
                observation = result.get("structured")
                if observation is None:
                    observation = result.get("text", "")
                results[step_id] = {
                    "result": result,
                    "trace": {
                        "tool": f"mcp__{tool_name}",
                        "tool_input": arguments,
                        "observation": json.dumps(observation, ensure_ascii=True, indent=2)
                        if not isinstance(observation, str)
                        else observation,
                    },
                }
        return results

    def _run_tool_step(
        self,
        execution_id: str,
        step_id: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        self.executions.set_node_status(execution_id, step_id, "processing")
        try:
            result = self.mcp_client.call_tool(tool_name, arguments)
        except Exception as exc:
            self.executions.set_node_status(execution_id, step_id, "failed", str(exc))
            raise
        self.executions.set_node_status(
            execution_id,
            step_id,
            "completed",
            f"tool={tool_name}\nparams={arguments}",
        )
        observation = result.get("structured")
        if observation is None:
            observation = result.get("text", "")
        return {
            "result": result,
            "trace": {
                "tool": f"mcp__{tool_name}",
                "tool_input": arguments,
                "observation": json.dumps(observation, ensure_ascii=True, indent=2)
                if not isinstance(observation, str)
                else observation,
            },
        }

    def _extract_devices(self, devices: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
        names: list[str] = []
        routers: list[str] = []
        for item in devices:
            uuid = (
                item.get("device_id", {})
                .get("device_uuid", {})
                .get("uuid")
            )
            if not uuid:
                continue
            name = str(uuid)
            names.append(name)
            if "router" in str(item.get("device_type", "")).lower():
                routers.append(name)
        return names, routers

    def _extract_link_ids(self, links: list[dict[str, Any]]) -> list[str]:
        link_ids: list[str] = []
        for item in links:
            uuid = (
                item.get("link_id", {})
                .get("link_uuid", {})
                .get("uuid")
            )
            if uuid:
                link_ids.append(str(uuid))
        return link_ids

    def _router_connection_map_from_inventory(
        self,
        devices: list[dict[str, Any]],
        links: list[dict[str, Any]],
    ) -> dict[str, list[str]]:
        _, routers = self._extract_devices(devices)
        router_ids = set(routers)
        mapping: dict[str, set[str]] = {router: set() for router in routers}
        for link in links:
            endpoint_ids = [
                endpoint.get("device_id", {}).get("device_uuid", {}).get("uuid")
                for endpoint in (link.get("link_endpoint_ids") or [])
                if endpoint.get("device_id", {}).get("device_uuid", {}).get("uuid")
            ]
            if len(endpoint_ids) < 2:
                continue
            for endpoint_id in endpoint_ids:
                if endpoint_id in router_ids:
                    for neighbor in endpoint_ids:
                        if neighbor != endpoint_id:
                            mapping.setdefault(str(endpoint_id), set()).add(str(neighbor))
        return {router: sorted(neighbors) for router, neighbors in sorted(mapping.items())}

    def _router_dc_link_ids_from_inventory(
        self,
        devices: list[dict[str, Any]],
        links: list[dict[str, Any]],
    ) -> list[str]:
        _, routers = self._extract_devices(devices)
        router_ids = set(routers)
        link_ids: list[str] = []
        for link in links:
            endpoint_ids = [
                endpoint.get("device_id", {}).get("device_uuid", {}).get("uuid")
                for endpoint in (link.get("link_endpoint_ids") or [])
                if endpoint.get("device_id", {}).get("device_uuid", {}).get("uuid")
            ]
            if len(endpoint_ids) < 2:
                continue
            first, second = str(endpoint_ids[0]), str(endpoint_ids[1])
            one_router = first in router_ids or second in router_ids
            one_dc = first.upper().startswith("DC") or second.upper().startswith("DC")
            if one_router and one_dc:
                link_uuid = (
                    link.get("link_id", {})
                    .get("link_uuid", {})
                    .get("uuid")
                )
                if link_uuid:
                    link_ids.append(str(link_uuid))
        return sorted(link_ids)
