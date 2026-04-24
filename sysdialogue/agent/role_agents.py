"""Built-in role profiles for structured handoff-style reasoning."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class RoleAgentProfile:
    name: str
    purpose: str
    allowed_modes: tuple[str, ...]
    guidance: str
    allowed_tools: tuple[str, ...] = ()


@dataclass(frozen=True)
class RoleHandoffRecord:
    handoff_id: str
    role: str
    objective: str
    constraints: dict[str, Any] = field(default_factory=dict)
    recommendation: str = ""
    allowed_tools: tuple[str, ...] = ()
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


ROLE_AGENT_PROFILES: tuple[RoleAgentProfile, ...] = (
    RoleAgentProfile(
        name="planner",
        purpose="Turn ambiguous operational goals into auditable direct/plan/workflow steps.",
        allowed_modes=("direct", "plan", "workflow"),
        guidance="Prefer durable plan steps for multi-step work. Do not execute tools.",
        allowed_tools=(),
    ),
    RoleAgentProfile(
        name="executor",
        purpose="Execute the next approved step through controlled tools.",
        allowed_modes=("direct", "workflow"),
        guidance="Never bypass risk gates, locks, or confirmations.",
        allowed_tools=("*",),
    ),
    RoleAgentProfile(
        name="verifier",
        purpose="Verify post-change state using read-only tools or workflow validation.",
        allowed_modes=("direct", "workflow"),
        guidance="A mutation is not complete until verification happens after the action.",
        allowed_tools=("get_system_info", "read_log", "validate_config", "check_endpoint", "manage_service"),
    ),
    RoleAgentProfile(
        name="risk_reviewer",
        purpose="Review risk, rollback hints, lock scopes, and permission policy impact.",
        allowed_modes=("plan", "workflow", "direct"),
        guidance="BLOCK has no override path. Escalate unclear state to ask/need_info.",
        allowed_tools=(),
    ),
    RoleAgentProfile(
        name="toolsmith",
        purpose="Design DynTool templates when static tools/workflows are insufficient.",
        allowed_modes=("direct",),
        guidance="Prefer inline one-shot execution; register only reusable command families.",
        allowed_tools=("propose_dynamic_tool", "execute_dynamic_tool"),
    ),
)


class RoleRunner:
    """Serial, constrained role handoff helper.

    This is intentionally not a parallel sub-agent framework. It gives the main
    ReAct loop a structured second opinion while keeping execution ownership in
    AgentController.
    """

    def __init__(self, profiles: tuple[RoleAgentProfile, ...] = ROLE_AGENT_PROFILES):
        self.profiles = {profile.name: profile for profile in profiles}

    def handoff(
        self,
        *,
        role: str,
        objective: str,
        constraints: dict[str, Any] | None = None,
    ) -> RoleHandoffRecord:
        profile = self.profiles.get(str(role or "").strip())
        if profile is None:
            raise KeyError(f"Unknown role: {role}")
        safe_constraints = dict(constraints or {})
        recommendation = _recommendation(profile, objective, safe_constraints)
        return RoleHandoffRecord(
            handoff_id=f"handoff_{uuid.uuid4().hex[:8]}",
            role=profile.name,
            objective=str(objective or ""),
            constraints=safe_constraints,
            recommendation=recommendation,
            allowed_tools=profile.allowed_tools,
        )

    def render_summary(self) -> str:
        return render_role_profiles()


def render_role_profiles() -> str:
    lines = ["[Role Handoff Profiles]"]
    for profile in ROLE_AGENT_PROFILES:
        modes = ", ".join(profile.allowed_modes)
        tools = ", ".join(profile.allowed_tools) or "no execution tools"
        lines.append(f"- {profile.name} ({modes}; tools={tools}): {profile.purpose} {profile.guidance}")
    return "\n".join(lines)


def _recommendation(profile: RoleAgentProfile, objective: str, constraints: dict[str, Any]) -> str:
    objective_text = str(objective or "").strip() or "No objective provided."
    if profile.name == "planner":
        return (
            "Create or revise a durable plan before executing. "
            f"Objective: {objective_text}. Respect constraints: {constraints or 'none'}."
        )
    if profile.name == "executor":
        return (
            "Execute only the next approved step through AgentController tools. "
            f"Objective: {objective_text}."
        )
    if profile.name == "verifier":
        return (
            "Use read-only evidence to verify the post-change state before finish_task(completed). "
            f"Objective: {objective_text}."
        )
    if profile.name == "risk_reviewer":
        return (
            "Review risk level, rollback path, permission reason, and lock scope. "
            "Escalate unclear or irreversible work."
        )
    if profile.name == "toolsmith":
        return (
            "Prefer inline execute_dynamic_tool for one-off commands; propose a reusable DynTool only for reusable command families."
        )
    return profile.guidance
