from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from sysdialogue.agent.controller import AgentController, LLMResponse
from sysdialogue.agent.hooks import HookManager
from sysdialogue.agent.memory import MemoryManager
from sysdialogue.agent.skills import SkillManager
from sysdialogue.agent.state_store import LockStore, SessionStore, TaskStore
from sysdialogue.agent.target_profile import TargetProfileStore
from sysdialogue.agent.trace_store import TraceStore
from sysdialogue.audit.trace_store import AuditLog
from sysdialogue.runtime.secure_runner import LocalExecutor
from sysdialogue.security.approval_rules import ConfirmationRequest
from sysdialogue.tools.base import ToolResult
from sysdialogue.tools.dynamic_registry import DynamicToolRegistry
from sysdialogue.tools.registry import ToolDef, ToolRegistry
from tests.helpers import RecordingExecutor


class ScriptedLLM:
    def __init__(self, responses: list[list[dict]]):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def messages_create(self, *, system, messages, tools):
        self.calls.append({"system": system, "messages": deepcopy(messages), "tools": deepcopy(tools)})
        if any(tool.get("name") == "submit_verification_judgement" for tool in tools):
            return LLMResponse(
                content=[
                    {
                        "type": "text",
                        "text": json.dumps(
                            {
                                "sufficient": True,
                                "covered_requirements": ["golden scenario evidence accepted"],
                                "missing_requirements": [],
                                "confidence": "high",
                                "recommended_next_verification": [],
                                "reason": "The verification happened after the mutation.",
                            }
                        ),
                    }
                ],
                stop_reason="stop",
            )
        content = self.responses.pop(0)
        stop_reason = "tool_use" if any(block.get("type") == "tool_use" for block in content) else "stop"
        return LLMResponse(content=content, stop_reason=stop_reason)


def _tool_use(name: str, args: dict, tool_id: str = "call_1") -> dict:
    return {"type": "tool_use", "id": tool_id, "name": name, "input": args}


def _finish(args: dict, tool_id: str = "finish_1") -> dict:
    return _tool_use("finish_task", args, tool_id)


def _schema(name: str, properties: dict | None = None, required: list[str] | None = None) -> dict:
    input_schema = {"type": "object", "properties": properties or {}}
    if required:
        input_schema["required"] = required
    return {"name": name, "description": f"Golden scenario fake {name}", "input_schema": input_schema}


def _register(
    registry: ToolRegistry,
    name: str,
    fn,
    properties: dict | None = None,
    required: list[str] | None = None,
) -> None:
    registry.register(
        ToolDef(
            name=name,
            fn=fn,
            schema=_schema(name, properties, required),
            requires_executor=False,
        )
    )


def _controller(tmp_path: Path, llm: ScriptedLLM, registry: ToolRegistry):
    events = []
    confirmations: list[ConfirmationRequest] = []
    controller = AgentController(
        executor=LocalExecutor(),
        env_profile={
            "remote_mode": False,
            "hostname": "golden-box",
            "current_user": "tester",
            "container_backend": "docker",
            "package_manager": "apt",
            "firewall_backend": "ufw",
        },
        audit_log=AuditLog(log_dir=str(tmp_path / "audit")),
        registry=registry,
        llm_client=llm,
        event_callback=events.append,
        session_store=SessionStore(str(tmp_path / "sessions")),
        task_store=TaskStore(str(tmp_path / "tasks")),
        lock_store=LockStore(str(tmp_path / "locks")),
        memory_manager=MemoryManager(str(tmp_path / "memory")),
        trace_store=TraceStore(str(tmp_path / "traces")),
        skill_manager=SkillManager(user_root=tmp_path / "skills"),
        hook_manager=HookManager(user_path=tmp_path / "hooks.json"),
        target_profile_store=TargetProfileStore(str(tmp_path / "targets")),
    )
    controller.confirm_callback = lambda req: confirmations.append(req) or True
    return controller, events, confirmations


def _controller_with_executor(
    tmp_path: Path,
    llm: ScriptedLLM,
    registry: ToolRegistry,
    executor,
):
    events = []
    confirmations: list[ConfirmationRequest] = []
    controller = AgentController(
        executor=executor,
        env_profile={
            "remote_mode": False,
            "hostname": "golden-box",
            "current_user": "tester",
            "container_backend": "docker",
            "package_manager": "apt",
            "firewall_backend": "ufw",
        },
        audit_log=AuditLog(log_dir=str(tmp_path / "audit")),
        registry=registry,
        llm_client=llm,
        event_callback=events.append,
        dynamic_registry=DynamicToolRegistry(storage_path=str(tmp_path / "dynamic_tools.json")),
        session_store=SessionStore(str(tmp_path / "sessions")),
        task_store=TaskStore(str(tmp_path / "tasks")),
        lock_store=LockStore(str(tmp_path / "locks")),
        memory_manager=MemoryManager(str(tmp_path / "memory")),
        trace_store=TraceStore(str(tmp_path / "traces")),
        skill_manager=SkillManager(user_root=tmp_path / "skills"),
        hook_manager=HookManager(user_path=tmp_path / "hooks.json"),
        target_profile_store=TargetProfileStore(str(tmp_path / "targets")),
    )
    controller.confirm_callback = lambda req: confirmations.append(req) or True
    return controller, events, confirmations


def test_golden_read_only_audit_observes_and_finishes_without_confirmation(tmp_path: Path) -> None:
    registry = ToolRegistry()
    _register(registry, "get_system_info", lambda: ToolResult(success=True, data={"hostname": "golden-box"}))
    _register(registry, "get_resource_stats", lambda: ToolResult(success=True, data={"load": "0.10"}))
    llm = ScriptedLLM(
        [
            [_tool_use("get_system_info", {}, "info_1")],
            [_tool_use("get_resource_stats", {}, "stats_1")],
            [
                _finish(
                    {
                        "status": "completed",
                        "summary": "Read-only audit completed.",
                        "evidence": ["hostname=golden-box", "load=0.10"],
                        "verification": "Both observations were read-only.",
                    }
                )
            ],
        ]
    )
    controller, events, confirmations = _controller(tmp_path, llm, registry)

    reply = controller.run_turn("检查系统主机名和资源状态")

    assert "Read-only audit completed" in reply
    assert confirmations == []
    assert [event.stage for event in events].count("tool_finished") == 2
    assert events[-1].stage == "task_finished"


def test_golden_service_restart_requires_approval_then_status_verification(tmp_path: Path) -> None:
    registry = ToolRegistry()
    service_calls: list[tuple[str, str]] = []

    def manage_service(name: str, action: str) -> ToolResult:
        service_calls.append((name, action))
        return ToolResult(success=True, data={"name": name, "action": action, "active": True})

    _register(
        registry,
        "manage_service",
        manage_service,
        {"name": {"type": "string"}, "action": {"type": "string"}},
        ["name", "action"],
    )
    llm = ScriptedLLM(
        [
            [_tool_use("manage_service", {"name": "nginx", "action": "restart"}, "restart_1")],
            [_tool_use("manage_service", {"name": "nginx", "action": "status"}, "status_1")],
            [
                _finish(
                    {
                        "status": "completed",
                        "summary": "nginx restarted and verified.",
                        "evidence": ["restart succeeded", "status active"],
                        "verification": "manage_service(status) ran after restart.",
                        "changed_state": True,
                    }
                )
            ],
        ]
    )
    controller, events, confirmations = _controller(tmp_path, llm, registry)

    reply = controller.run_turn("重启 nginx 并确认状态")

    assert "nginx restarted and verified" in reply
    assert service_calls == [("nginx", "restart"), ("nginx", "status")]
    assert [(req.tool, req.risk.level) for req in confirmations] == [("manage_service", "WARN-HIGH")]
    assert events[-1].data["verification_judgement"]["sufficient"] is True


def test_golden_safe_config_patch_workflow_runs_approval_backup_patch_and_validate(tmp_path: Path) -> None:
    registry = ToolRegistry()
    tool_calls: list[tuple[str, dict]] = []

    def record(name: str, data: dict):
        def inner(**kwargs):
            tool_calls.append((name, dict(kwargs)))
            return ToolResult(success=True, data=data)

        return inner

    _register(registry, "stat_path", record("stat_path", {"exists": True}))
    _register(registry, "read_file", record("read_file", {"content": "worker_processes 1;"}))
    def replace_in_file(**kwargs) -> ToolResult:
        tool_calls.append(("replace_in_file", dict(kwargs)))
        return ToolResult(
            success=True,
            data=(
                {"diff_preview": "-worker_processes 1;\n+worker_processes auto;", "actual_matches": 1}
                if kwargs.get("dry_run")
                else {"changed": True, "actual_matches": 1}
            ),
        )

    _register(registry, "replace_in_file", replace_in_file)
    _register(registry, "backup_path", record("backup_path", {"backup_id": "backup-1"}))
    _register(registry, "validate_config", record("validate_config", {"valid": True}))
    llm = ScriptedLLM(
        [
            [
                _tool_use(
                    "set_execution_mode",
                    {
                        "mode": "workflow",
                        "workflow_name": "safe_config_patch",
                        "workflow_params": {
                            "file_path": "/etc/nginx/nginx.conf",
                            "search_text": "worker_processes 1;",
                            "replace_text": "worker_processes auto;",
                            "validator": "nginx",
                        },
                    },
                    "workflow_1",
                )
            ],
            [
                _finish(
                    {
                        "status": "completed",
                        "summary": "Config patch workflow completed.",
                        "evidence": ["backup-1 created", "validate_config valid=true"],
                        "verification": "safe_config_patch includes internal validation.",
                        "changed_state": True,
                    }
                )
            ],
        ]
    )
    controller, events, confirmations = _controller(tmp_path, llm, registry)

    reply = controller.run_turn("安全修改 nginx 配置并校验")

    assert "Config patch workflow completed" in reply
    assert [name for name, _ in tool_calls] == [
        "stat_path",
        "read_file",
        "replace_in_file",
        "backup_path",
        "replace_in_file",
        "validate_config",
    ]
    assert any(req.tool == "workflow:approval:s4" for req in confirmations)
    assert [event.stage for event in events].count("workflow_finished") == 1
    judgement = events[-1].data["verification_judgement"]
    assert judgement["sufficient"] is True
    assert any("safe_config_patch" in item for item in judgement["covered_requirements"])


def test_golden_failed_tool_repair_can_complete_after_successful_retry_and_verification(tmp_path: Path) -> None:
    registry = ToolRegistry()
    attempts = {"patch": 0}

    _register(registry, "get_system_info", lambda: ToolResult(success=True, data={"hostname": "golden-box"}))

    def patch_config(path: str, value: str) -> ToolResult:
        attempts["patch"] += 1
        if attempts["patch"] == 1:
            return ToolResult(success=False, error="temporary file lock")
        return ToolResult(success=True, data={"path": path, "changed": True, "value": value})

    _register(
        registry,
        "patch_config",
        patch_config,
        {"path": {"type": "string"}, "value": {"type": "string"}},
        ["path", "value"],
    )
    _register(
        registry,
        "validate_config",
        lambda path: ToolResult(success=True, data={"path": path, "valid": True}),
        {"path": {"type": "string"}},
        ["path"],
    )
    llm = ScriptedLLM(
        [
            [_tool_use("get_system_info", {}, "observe_1")],
            [_tool_use("patch_config", {"path": "/tmp/app.conf", "value": "enabled=true"}, "patch_1")],
            [_tool_use("patch_config", {"path": "/tmp/app.conf", "value": "enabled=true"}, "patch_2")],
            [_tool_use("validate_config", {"path": "/tmp/app.conf"}, "validate_1")],
            [
                _finish(
                    {
                        "status": "completed",
                        "summary": "Patch retried, succeeded, and was verified.",
                        "evidence": ["first patch failed", "second patch changed file", "validate_config valid=true"],
                        "verification": "validate_config ran after the successful retry.",
                        "changed_state": True,
                    }
                )
            ],
        ]
    )
    controller, events, _ = _controller(tmp_path, llm, registry)

    reply = controller.run_turn("修复配置，如果第一次失败就重试并验证")

    assert "Patch retried, succeeded, and was verified" in reply
    assert attempts["patch"] == 2
    tool_results = [event for event in events if event.stage == "tool_finished"]
    assert tool_results[1].data["success"] is False
    assert tool_results[2].data["success"] is True
    assert events[-1].data["verification_judgement"]["sufficient"] is True


def test_golden_resume_interrupted_task_keeps_resume_turn_and_completes(tmp_path: Path) -> None:
    registry = ToolRegistry()
    _register(registry, "get_system_info", lambda: ToolResult(success=True, data={"hostname": "golden-box"}))
    llm = ScriptedLLM(
        [
            [_tool_use("get_system_info", {}, "observe_resume")],
            [
                _finish(
                    {
                        "status": "completed",
                        "summary": "Resumed audit completed.",
                        "evidence": ["hostname=golden-box"],
                        "verification": "The resumed task observed the target before completion.",
                    }
                )
            ],
        ]
    )
    controller, events, _ = _controller(tmp_path, llm, registry)
    task = controller.task_store.create(
        task_id="task_resume_golden",
        session_id=controller.session_id,
        surface="tui",
        goal="检查系统状态",
        status="interrupted",
        current_phase="resume",
        iteration_budget=80,
        iteration_limit=160,
        resume_message="Recovered interrupted task after restart.",
    )
    controller.session_store.set_status(
        controller.session_id,
        "interrupted",
        surface="tui",
        active_task_id=task.task_id,
    )

    reply = controller.run_turn("/resume")

    record = controller.session_store.load(controller.session_id)
    task_record = controller.task_store.load(task.task_id)
    assert "Resumed audit completed" in reply
    assert events[0].data["resumed"] is True
    assert record is not None
    assert record.entries[0]["role"] == "user"
    assert record.entries[0]["text"] == "/resume"
    assert record.entries[0]["task_id"] == task.task_id
    assert task_record is not None
    assert task_record.status == "completed"


@pytest.mark.parametrize(
    "label,mutating_tool,mutating_args,verification_tool,verification_args,expected_rule",
    [
        (
            "cron create",
            "manage_cron",
            {
                "action": "create",
                "scope": "user",
                "schedule": "*/5 * * * *",
                "job_target": {"kind": "tool", "name": "get_system_info", "args": {}},
            },
            "manage_cron",
            {"action": "list", "scope": "user"},
            "WH015",
        ),
        (
            "package install",
            "manage_package",
            {"action": "install", "name": "htop", "manager": "apt"},
            "manage_package",
            {"action": "list", "name": "htop", "manager": "apt"},
            "WH009",
        ),
        (
            "firewall allow",
            "manage_firewall",
            {"action": "allow", "target": {"port": 8080, "protocol": "tcp"}},
            "manage_firewall",
            {"action": "list"},
            "WH010",
        ),
        (
            "user create",
            "create_user",
            {"username": "deploy", "groups": ["www-data"], "create_home": True},
            "read_file",
            {"path": "/etc/passwd", "mode": "tail", "tail_lines": 20},
            "WH003",
        ),
        (
            "ssh key add",
            "manage_authorized_keys",
            {"action": "add", "username": "deploy", "public_key": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITest deploy"},
            "manage_authorized_keys",
            {"action": "list", "username": "deploy"},
            "WH020",
        ),
        (
            "sysctl set",
            "manage_sysctl",
            {"action": "set", "key": "net.ipv4.ip_forward", "value": "1", "persist": True},
            "manage_sysctl",
            {"action": "get", "key": "net.ipv4.ip_forward"},
            "WH016",
        ),
        (
            "container restart",
            "manage_container",
            {"action": "restart", "name": "web"},
            "manage_container",
            {"action": "status", "name": "web"},
            "WH019",
        ),
    ],
)
def test_golden_static_mutation_matrix_requires_approval_and_post_verification(
    tmp_path: Path,
    label: str,
    mutating_tool: str,
    mutating_args: dict,
    verification_tool: str,
    verification_args: dict,
    expected_rule: str,
) -> None:
    registry = ToolRegistry()
    calls: list[tuple[str, dict]] = []

    def make_tool(name: str):
        def inner(**kwargs) -> ToolResult:
            calls.append((name, dict(kwargs)))
            return ToolResult(success=True, data={"tool": name, **dict(kwargs)})

        return inner

    _register(registry, "get_system_info", make_tool("get_system_info"))
    for name in {mutating_tool, verification_tool, "read_file"}:
        _register(registry, name, make_tool(name))

    llm = ScriptedLLM(
        [
            [_tool_use("get_system_info", {}, "observe_1")],
            [_tool_use(mutating_tool, mutating_args, "mutate_1")],
            [_tool_use(verification_tool, verification_args, "verify_1")],
            [
                _finish(
                    {
                        "status": "completed",
                        "summary": f"{label} mutation completed with approval and verification.",
                        "evidence": [f"{mutating_tool} succeeded", f"{verification_tool} verified target"],
                        "verification": f"{verification_tool} ran after {mutating_tool}.",
                        "changed_state": True,
                    }
                )
            ],
        ]
    )
    controller, events, confirmations = _controller(tmp_path, llm, registry)

    reply = controller.run_turn(f"golden matrix: {label}")

    assert f"{label} mutation completed" in reply
    assert calls == [
        ("get_system_info", {}),
        (mutating_tool, mutating_args),
        (verification_tool, verification_args),
    ]
    assert len(confirmations) == 1
    assert confirmations[0].tool == mutating_tool
    assert confirmations[0].risk.level == "WARN-HIGH"
    assert expected_rule in confirmations[0].risk.rule_ids
    assert events[-1].stage == "task_finished"
    assert events[-1].data["verification_judgement"]["sufficient"] is True


def test_golden_dyntool_mutation_requires_approval_and_static_verification(tmp_path: Path) -> None:
    registry = ToolRegistry()
    _register(registry, "get_system_info", lambda: ToolResult(success=True, data={"hostname": "golden-box"}))
    _register(
        registry,
        "stat_path",
        lambda path: ToolResult(success=True, data={"path": path, "exists": True, "size": 2}),
        {"path": {"type": "string"}},
        ["path"],
    )
    executor = RecordingExecutor(handler=lambda cmd, timeout: ("ok\n", 0))
    llm = ScriptedLLM(
        [
            [_tool_use("get_system_info", {}, "observe_1")],
            [
                _tool_use(
                    "execute_dynamic_tool",
                    {
                        "cmd_template": ["touch", "/tmp/sysdialogue-golden.flag"],
                        "args": {},
                        "intent_summary": "Create a marker file as a controlled dynamic mutation.",
                        "consequences": "Creates or updates /tmp/sysdialogue-golden.flag.",
                        "risk_assessment": "Writes a marker file under /tmp; reversible by deleting it.",
                        "estimated_risk": "WARN-HIGH",
                        "changes_state": True,
                    },
                    "dyntool_1",
                )
            ],
            [_tool_use("stat_path", {"path": "/tmp/sysdialogue-golden.flag"}, "verify_1")],
            [
                _finish(
                    {
                        "status": "completed",
                        "summary": "DynTool mutation completed with approval and verification.",
                        "evidence": ["execute_dynamic_tool exit_code=0", "stat_path exists=true"],
                        "verification": "stat_path verified the marker after execute_dynamic_tool.",
                        "changed_state": True,
                    }
                )
            ],
        ]
    )
    controller, events, confirmations = _controller_with_executor(tmp_path, llm, registry, executor)

    reply = controller.run_turn("用动态工具创建一个标记文件并验证")

    assert "DynTool mutation completed" in reply
    assert executor.calls == [["touch", "/tmp/sysdialogue-golden.flag"]]
    assert len(confirmations) == 1
    assert confirmations[0].tool == "execute_dynamic_tool"
    assert confirmations[0].risk.level == "WARN-HIGH"
    assert events[-1].data["verification_judgement"]["sufficient"] is True


def test_golden_user_cancelled_confirmation_does_not_execute_mutation(tmp_path: Path) -> None:
    registry = ToolRegistry()
    service_calls: list[dict] = []
    _register(
        registry,
        "manage_service",
        lambda **kwargs: service_calls.append(dict(kwargs)) or ToolResult(success=True, data=kwargs),
    )
    llm = ScriptedLLM(
        [
            [_tool_use("manage_service", {"name": "nginx", "action": "restart"}, "restart_1")],
            [
                _finish(
                    {
                        "status": "cancelled",
                        "summary": "User cancelled the nginx restart before execution.",
                    }
                )
            ],
        ]
    )
    controller, events, confirmations = _controller(tmp_path, llm, registry)
    controller.confirm_callback = lambda req: confirmations.append(req) or False

    reply = controller.run_turn("重启 nginx，但如果审批拒绝就停止")

    assert "User cancelled the nginx restart" in reply
    assert service_calls == []
    assert len(confirmations) == 1
    assert confirmations[0].tool == "manage_service"
    assert any(event.stage == "confirmation_requested" for event in events)
    assert events[-1].stage == "task_failed"
    assert events[-1].data["status"] == "cancelled"
    audit_records = controller.audit_log.read_all()
    assert any(record.get("decision") == "user_cancelled" for record in audit_records)
    assert not any(record.get("type") == "command_trace" and record.get("tool") == "manage_service" for record in audit_records)


def test_golden_hard_blocked_tool_is_not_confirmed_or_executed(tmp_path: Path) -> None:
    registry = ToolRegistry()
    key_calls: list[dict] = []
    _register(
        registry,
        "manage_authorized_keys",
        lambda **kwargs: key_calls.append(dict(kwargs)) or ToolResult(success=True, data=kwargs),
    )
    llm = ScriptedLLM(
        [
            [
                _tool_use(
                    "manage_authorized_keys",
                    {
                        "action": "add",
                        "username": "root",
                        "public_key": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITest root",
                    },
                    "blocked_key_1",
                )
            ],
            [
                _finish(
                    {
                        "status": "blocked",
                        "summary": "Root authorized_keys modification was hard-blocked.",
                        "next_steps": ["Use a non-root account or perform root key rotation manually."],
                    }
                )
            ],
        ]
    )
    controller, events, confirmations = _controller(tmp_path, llm, registry)

    reply = controller.run_turn("给 root 添加 SSH 公钥")

    assert "hard-blocked" in reply
    assert key_calls == []
    assert confirmations == []
    tool_finished = [event for event in events if event.stage == "tool_finished"][0]
    assert tool_finished.data["success"] is False
    assert "BLOCK" in tool_finished.data["error_summary"]
    assert events[-1].stage == "task_finished"
    assert events[-1].data["status"] == "blocked"
    audit_records = controller.audit_log.read_all()
    assert any(
        record.get("type") == "decision"
        and record.get("tool") == "manage_authorized_keys"
        and record.get("decision") == "BLOCK"
        for record in audit_records
    )
    assert not any(record.get("type") == "command_trace" for record in audit_records)


def test_golden_workflow_rollback_runs_after_failed_verification(tmp_path: Path) -> None:
    workflows_dir = tmp_path / "workflows"
    workflows_dir.mkdir()
    (workflows_dir / "rollback_demo.yaml").write_text(
        """
name: rollback_demo
parameters: []
steps:
  - id: mutate
    type: tool_call
    tool: mutate_marker
    args: {}
  - id: verify
    type: tool_call
    tool: fail_validation
    args: {}
    depends_on: [mutate]
    on_fail: rollback
rollback:
  - id: restore
    type: tool_call
    tool: restore_marker
    args: {}
final:
  rollback_template: "Mutation failed verification and was rolled back."
""".lstrip(),
        encoding="utf-8",
    )
    registry = ToolRegistry()
    calls: list[str] = []
    _register(registry, "mutate_marker", lambda: calls.append("mutate") or ToolResult(success=True, data={"changed": True}))
    _register(registry, "fail_validation", lambda: calls.append("verify") or ToolResult(success=False, error="invalid"))
    _register(registry, "restore_marker", lambda: calls.append("restore") or ToolResult(success=True, data={"restored": True}))
    llm = ScriptedLLM(
        [
            [
                _tool_use(
                    "set_execution_mode",
                    {"mode": "workflow", "workflow_name": "rollback_demo", "workflow_params": {}},
                    "workflow_rollback_1",
                )
            ],
            [
                _finish(
                    {
                        "status": "failed",
                        "summary": "Workflow mutation failed verification and rollback ran.",
                        "next_steps": ["Inspect the validation error before retrying."],
                    }
                )
            ],
        ]
    )
    controller, events, _ = _controller(tmp_path, llm, registry)
    controller.workflows_dir = workflows_dir

    reply = controller.run_turn("运行一个会在校验失败时回滚的工作流")

    assert "rollback ran" in reply
    assert calls == ["mutate", "verify", "restore"]
    workflow_finished = [event for event in events if event.stage == "workflow_finished"][0]
    assert workflow_finished.data["success"] is True
    assert "rolled_back" in workflow_finished.data["raw_result_preview"]
    assert events[-1].stage == "task_failed"
    assert events[-1].data["status"] == "failed"
    audit_records = controller.audit_log.read_all()
    assert any(record.get("type") == "workflow_step" and record.get("step_id") == "restore" and record.get("status") == "rolled_back" for record in audit_records)
    assert any(record.get("type") == "final" and record.get("final_status") == "rolled_back" for record in audit_records)
