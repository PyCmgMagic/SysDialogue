from __future__ import annotations

from sysdialogue.agent.planner import PlanningEngine


class _Controller:
    env_profile = {}
    _session_counters = {}
    _env_profile_id = "env"

    class _Registry:
        def has(self, name: str) -> bool:
            return False

    class _Audit:
        def log_decision(self, **kwargs) -> str:
            return "decision"

    registry = _Registry()
    audit_log = _Audit()


def test_planner_tolerates_non_object_steps_from_llm() -> None:
    plan = PlanningEngine(controller=_Controller()).freeze(["create user", {"tool": ""}])

    assert plan.steps[0].step_id == "step_1"
    assert plan.steps[0].purpose == "create user"
    assert "invalid plan step format" in plan.warnings[0]
