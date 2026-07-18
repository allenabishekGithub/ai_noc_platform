from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class VerificationResult:
    passed: bool
    issues: list[str] = field(default_factory=list)
    pending_task: str = ""
    refinement_hint: str = ""


def _normalize(text: str) -> str:
    return " ".join(str(text or "").strip().lower().split())


def evaluate_reply(
    user_message: str,
    reply: str,
    *,
    blackboard: dict | None = None,
    tool_trace: list[dict] | None = None,
    pending_task: str = "",
    generic_bad_patterns: list[str] | None = None,
    dialogue_action: str | None = None,
) -> VerificationResult:
    blackboard = blackboard or {}
    tool_trace = tool_trace or []
    user_text = _normalize(user_message)
    reply_text = _normalize(reply)
    issues: list[str] = []
    next_pending = pending_task

    if not reply_text:
        issues.append("reply is empty")

    generic_bad_patterns = [str(item).strip().lower() for item in (generic_bad_patterns or []) if str(item).strip()]

    if any(pattern in reply_text for pattern in generic_bad_patterns):
        if user_text not in {"hi", "hello", "hey"}:
            issues.append("reply is generic or admits incompleteness")

    if "router" in user_text and "connect" in user_text:
        router_connections = (blackboard.get("discovered") or {}).get("router_connections") or {}
        if router_connections:
            missing = [router for router in sorted(router_connections.keys()) if router.lower() not in reply_text]
            if missing:
                issues.append("reply does not cover all routers: " + ", ".join(missing))
            next_pending = ""
        else:
            issues.append("router connectivity answer not grounded in topology data")
            next_pending = "Determine to which device each router is connected using topology details."

    if "link" in user_text and "router" in user_text and "dc" in user_text:
        router_dc_links = (blackboard.get("discovered") or {}).get("router_dc_links") or []
        if router_dc_links:
            missing = [link_id for link_id in router_dc_links if link_id.lower() not in reply_text]
            if missing:
                issues.append("reply does not list all router-to-DC links: " + ", ".join(missing))
            next_pending = ""
        else:
            issues.append("router-to-DC link answer not grounded in link inventory")
            next_pending = "Determine which links connect routers with DCs using the controller link inventory."

    if "which routers" in user_text or ("router" in user_text and "have" in user_text):
        routers = (blackboard.get("discovered") or {}).get("routers") or []
        missing = [router for router in routers if router.lower() not in reply_text]
        if routers and missing:
            issues.append("reply does not list all known routers")

    if ("which links" in user_text or "list links" in user_text) and "router" not in user_text:
        link_ids = (blackboard.get("discovered") or {}).get("links") or []
        if link_ids:
            missing = [link_id for link_id in link_ids if link_id.lower() not in reply_text]
            if missing:
                issues.append("reply does not list all known links")

    if dialogue_action in {"continue_pending", "proceed_pending"}:
        if pending_task and pending_task.lower() not in reply_text:
            issues.append("follow-up did not continue the pending task")
            next_pending = pending_task

    if issues:
        hint = "; ".join(issues)
        return VerificationResult(
            passed=False,
            issues=issues,
            pending_task=next_pending,
            refinement_hint=hint,
        )

    return VerificationResult(
        passed=True,
        issues=[],
        pending_task=next_pending,
        refinement_hint="",
    )
