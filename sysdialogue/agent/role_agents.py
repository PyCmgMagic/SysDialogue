"""Built-in role profiles for structured handoff-style reasoning."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RoleAgentProfile:
    name: str
    purpose: str
    allowed_modes: tuple[str, ...]
    guidance: str


ROLE_AGENT_PROFILES: tuple[RoleAgentProfile, ...] = (
    RoleAgentProfile(
        name="planner",
        purpose="Turn ambiguous operational goals into auditable direct/plan/workflow steps.",
        allowed_modes=("direct", "plan", "workflow"),
        guidance="Prefer durable plan steps for multi-step work. Do not execute tools.",
    ),
    RoleAgentProfile(
        name="executor",
        purpose="Execute the next approved step through controlled tools.",
        allowed_modes=("direct", "workflow"),
        guidance="Never bypass risk gates, locks, or confirmations.",
    ),
    RoleAgentProfile(
        name="verifier",
        purpose="Verify post-change state using read-only tools or workflow validation.",
        allowed_modes=("direct", "workflow"),
        guidance="A mutation is not complete until verification happens after the action.",
    ),
    RoleAgentProfile(
        name="risk_reviewer",
        purpose="Review risk, rollback hints, lock scopes, and permission policy impact.",
        allowed_modes=("plan", "workflow", "direct"),
        guidance="BLOCK has no override path. Escalate unclear state to ask/need_info.",
    ),
    RoleAgentProfile(
        name="toolsmith",
        purpose="Design DynTool templates when static tools/workflows are insufficient.",
        allowed_modes=("direct",),
        guidance="Prefer inline one-shot execution; register only reusable command families.",
    ),
)


def render_role_profiles() -> str:
    lines = ["[Role Handoff Profiles]"]
    for profile in ROLE_AGENT_PROFILES:
        modes = ", ".join(profile.allowed_modes)
        lines.append(f"- {profile.name} ({modes}): {profile.purpose} {profile.guidance}")
    return "\n".join(lines)
