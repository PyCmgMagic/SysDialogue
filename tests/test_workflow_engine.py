from __future__ import annotations

from pathlib import Path

from sysdialogue.agent.controller import AgentController
from sysdialogue.agent.hooks import HookManager
from sysdialogue.agent.memory import MemoryManager
from sysdialogue.agent.skills import SkillManager
from sysdialogue.agent.state_store import LockStore, SessionStore, TaskStore
from sysdialogue.agent.target_profile import TargetProfileStore
from sysdialogue.agent.trace_store import TraceStore
from sysdialogue.agent.workflow_engine import WorkflowEngine
from sysdialogue.audit.trace_store import AuditLog
from sysdialogue.runtime.secure_runner import LocalExecutor
from sysdialogue.tools.base import ToolResult
from sysdialogue.tools.registry import ToolDef, ToolRegistry


def _engine(tmp_path: Path) -> WorkflowEngine:
    controller = AgentController(
        executor=LocalExecutor(),
        env_profile={"remote_mode": False, "current_user": "tester"},
        audit_log=AuditLog(log_dir=str(tmp_path / "audit")),
        registry=ToolRegistry(),
        llm_client=None,
        session_store=SessionStore(str(tmp_path / "sessions")),
        task_store=TaskStore(str(tmp_path / "tasks")),
        lock_store=LockStore(str(tmp_path / "locks")),
        memory_manager=MemoryManager(str(tmp_path / "memory")),
        trace_store=TraceStore(str(tmp_path / "traces")),
        skill_manager=SkillManager(user_root=tmp_path / "skills"),
        hook_manager=HookManager(user_path=tmp_path / "hooks.json"),
        target_profile_store=TargetProfileStore(str(tmp_path / "targets")),
    )
    return WorkflowEngine(controller=controller, workflows_dir=tmp_path / "workflows")


def test_workflow_template_error_fails_step_before_tool_execution(tmp_path: Path) -> None:
    workflows_dir = tmp_path / "workflows"
    workflows_dir.mkdir()
    (workflows_dir / "bad_args.yaml").write_text(
        """
name: bad_args
parameters: []
steps:
  - id: bad_step
    type: tool_call
    tool: get_system_info
    args:
      path: "{{ missing.path }}"
""".lstrip(),
        encoding="utf-8",
    )

    execution = _engine(tmp_path).run("bad_args", {})

    assert execution.final_status == "failed"
    assert execution.steps_state["bad_step"].status == "failed"
    assert "模板插值失败" in execution.steps_state["bad_step"].error


def test_workflow_template_error_in_condition_fails_instead_of_truthy_skip(
    tmp_path: Path,
) -> None:
    workflows_dir = tmp_path / "workflows"
    workflows_dir.mkdir()
    (workflows_dir / "bad_condition.yaml").write_text(
        """
name: bad_condition
parameters: []
steps:
  - id: guarded_step
    type: display
    condition: "{{ missing.flag }}"
    template: "should not render"
""".lstrip(),
        encoding="utf-8",
    )

    execution = _engine(tmp_path).run("bad_condition", {})

    assert execution.final_status == "failed"
    assert execution.steps_state["guarded_step"].status == "failed"
    assert "模板插值失败" in execution.steps_state["guarded_step"].error


def test_workflow_tool_call_uses_direct_resource_lease_when_no_lock_scope(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import sysdialogue.agent.controller as controller_module

    monkeypatch.setattr(controller_module, "_DIRECT_LOCK_TIMEOUT", 0.1)
    workflows_dir = tmp_path / "workflows"
    workflows_dir.mkdir()
    (workflows_dir / "mutate_without_scope.yaml").write_text(
        """
name: mutate_without_scope
parameters: []
steps:
  - id: mutate
    type: tool_call
    tool: mutate_marker
    args: {}
""".lstrip(),
        encoding="utf-8",
    )
    registry = ToolRegistry()
    registry.register(
        ToolDef(
            name="mutate_marker",
            fn=lambda executor: ToolResult(success=True, data={"changed": True}),
            schema={
                "name": "mutate_marker",
                "description": "Mutate marker",
                "input_schema": {"type": "object", "properties": {}},
            },
        )
    )
    session_store = SessionStore(str(tmp_path / "sessions"))
    task_store = TaskStore(str(tmp_path / "tasks"))
    lock_store = LockStore(str(tmp_path / "locks"))
    controller = AgentController(
        executor=LocalExecutor(),
        env_profile={"remote_mode": False, "current_user": "tester"},
        audit_log=AuditLog(log_dir=str(tmp_path / "audit")),
        registry=registry,
        llm_client=None,
        session_store=session_store,
        task_store=task_store,
        lock_store=lock_store,
    )
    task_store.create(
        task_id="task_wf",
        session_id=controller.session_id,
        surface="test",
        goal="mutate",
        status="running",
    )
    lock_store.acquire(
        "tool:mutate_marker",
        task_id="other_task",
        session_id="other_session",
        surface="web",
        timeout=0.1,
    )
    controller.bind_task("task_wf")
    try:
        execution = WorkflowEngine(controller=controller, workflows_dir=workflows_dir).run(
            "mutate_without_scope",
            {},
        )
    finally:
        controller.unbind_task()

    assert execution.final_status == "failed"
    assert execution.steps_state["mutate"].status == "failed"
    assert "resource_locked: tool:mutate_marker" in execution.steps_state["mutate"].error


def test_workflow_lock_scope_requires_durable_task_owner(tmp_path: Path) -> None:
    workflows_dir = tmp_path / "workflows"
    workflows_dir.mkdir()
    (workflows_dir / "locked_mutation.yaml").write_text(
        """
name: locked_mutation
parameters: []
steps:
  - id: mutate
    type: tool_call
    tool: mutate_marker
    lock_scope: "file:/etc/example.conf"
    args: {}
""".lstrip(),
        encoding="utf-8",
    )
    called: list[str] = []
    registry = ToolRegistry()
    registry.register(
        ToolDef(
            name="mutate_marker",
            fn=lambda executor: called.append("ran") or ToolResult(success=True),
            schema={
                "name": "mutate_marker",
                "description": "Mutate marker",
                "input_schema": {"type": "object", "properties": {}},
            },
        )
    )
    controller = AgentController(
        executor=LocalExecutor(),
        env_profile={"remote_mode": False, "current_user": "tester"},
        audit_log=AuditLog(log_dir=str(tmp_path / "audit")),
        registry=registry,
        llm_client=None,
        session_store=SessionStore(str(tmp_path / "sessions")),
        task_store=TaskStore(str(tmp_path / "tasks")),
        lock_store=LockStore(str(tmp_path / "locks")),
    )

    execution = WorkflowEngine(controller=controller, workflows_dir=workflows_dir).run(
        "locked_mutation",
        {},
    )

    assert execution.final_status == "failed"
    assert execution.steps_state["mutate"].status == "failed"
    assert "missing_task_context: file:/etc/example.conf" in execution.steps_state["mutate"].error
    assert called == []
