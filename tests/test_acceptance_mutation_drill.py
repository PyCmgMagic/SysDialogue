from __future__ import annotations

from pathlib import Path

from sysdialogue.agent.acceptance_runner import render_guided_acceptance
from sysdialogue.agent.controller import AgentController
from sysdialogue.agent.hooks import HookManager
from sysdialogue.agent.memory import MemoryManager
from sysdialogue.agent.release_readiness import analyze_release_readiness_text
from sysdialogue.agent.skills import SkillManager
from sysdialogue.agent.state_store import LockStore, SessionStore, TaskStore
from sysdialogue.agent.target_profile import TargetProfileStore
from sysdialogue.agent.trace_store import TraceStore
from sysdialogue.app.mutation_drill import (
    A07_APPROVAL_PHRASE,
    collect_operator_approved_mutation_drill_evidence,
)
from sysdialogue.audit.trace_store import AuditLog
from sysdialogue.runtime.secure_runner import LocalExecutor
from sysdialogue.tools.base import ToolResult
from sysdialogue.tools.registry import ToolDef, ToolRegistry


def _controller(tmp_path: Path, calls: list[tuple[str, str]]) -> AgentController:
    registry = ToolRegistry()

    def manage_service(name: str, action: str, executor: LocalExecutor) -> ToolResult:
        calls.append((name, action))
        return ToolResult(success=True, data={"name": name, "action": action, "status": "ok"})

    registry.register(
        ToolDef(
            name="manage_service",
            fn=manage_service,
            schema={
                "name": "manage_service",
                "description": "Manage a service",
                "input_schema": {"type": "object", "properties": {}},
            },
        )
    )
    return AgentController(
        executor=LocalExecutor(),
        env_profile={"remote_mode": False, "current_user": "tester", "service_manager": "systemd"},
        audit_log=AuditLog(log_dir=str(tmp_path / "audit")),
        registry=registry,
        llm_client=None,
        session_store=SessionStore(str(tmp_path / "sessions")),
        task_store=TaskStore(str(tmp_path / "tasks")),
        lock_store=LockStore(str(tmp_path / "locks")),
        memory_manager=MemoryManager(str(tmp_path / "memory")),
        trace_store=TraceStore(str(tmp_path / "traces")),
        skill_manager=SkillManager(user_root=tmp_path / "skills"),
        hook_manager=HookManager(user_path=tmp_path / "hooks.json"),
        target_profile_store=TargetProfileStore(str(tmp_path / "targets")),
        workflows_dir=Path(__file__).resolve().parents[1] / "sysdialogue" / "workflows",
        surface="test",
    )


def _approved_plan() -> dict:
    return {
        "workflow_name": "service_restart",
        "args": {"service_name": "sysdialogue-a07-test"},
        "approval_phrase": A07_APPROVAL_PHRASE,
        "impact": "Restart only the disposable A07 test service.",
        "rollback": "Start the disposable test service again if restart fails.",
        "verification": "Post-change status check must report the service state.",
        "disposable_target": True,
    }


def test_operator_approved_mutation_drill_runs_constrained_workflow(tmp_path: Path) -> None:
    calls: list[tuple[str, str]] = []
    controller = _controller(tmp_path, calls)

    collected = collect_operator_approved_mutation_drill_evidence(controller, _approved_plan())
    artifact = render_guided_acceptance({"remote_mode": False}, mode="operator-approved-drill", collected=collected)
    readiness = analyze_release_readiness_text(artifact, source="a07")

    assert collected["A07"]["status"] == "pass"
    assert calls == [
        ("sysdialogue-a07-test", "status"),
        ("sysdialogue-a07-test", "restart"),
        ("sysdialogue-a07-test", "status"),
    ]
    assert "Operator-approved A07 mutation drill" in artifact
    assert "Runner mode: operator-approved" in artifact
    statuses = {check.step_id: check.status for check in readiness.checks}
    assert statuses["A07"] == "pass"


def test_operator_approved_mutation_drill_rejects_missing_phrase(tmp_path: Path) -> None:
    calls: list[tuple[str, str]] = []
    controller = _controller(tmp_path, calls)
    plan = {**_approved_plan(), "approval_phrase": "yes"}

    collected = collect_operator_approved_mutation_drill_evidence(controller, plan)

    assert collected["A07"]["status"] == "fail"
    assert "Approval phrase" in collected["A07"]["evidence"]
    assert calls == []
