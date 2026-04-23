from __future__ import annotations

from pathlib import Path

from sysdialogue.agent.controller import AgentController
from sysdialogue.agent.workflow_engine import WorkflowEngine
from sysdialogue.audit.trace_store import AuditLog
from sysdialogue.runtime.secure_runner import LocalExecutor
from sysdialogue.tools.registry import ToolRegistry


def _engine(tmp_path: Path) -> WorkflowEngine:
    controller = AgentController(
        executor=LocalExecutor(),
        env_profile={"remote_mode": False, "current_user": "tester"},
        audit_log=AuditLog(log_dir=str(tmp_path / "audit")),
        registry=ToolRegistry(),
        llm_client=None,
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
