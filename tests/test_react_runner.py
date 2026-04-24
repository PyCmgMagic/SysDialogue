from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from sysdialogue.agent.controller import AgentController, LLMResponse, _direct_lock_scopes
from sysdialogue.agent.react_runner import (
    LLMVerificationJudge,
    TaskRun,
    VerificationJudge,
    _is_mutating_tool,
    _is_verification_tool,
    _iteration_budget,
    _plan_args_match,
    _requires_environment_feedback,
    _resolve_runtime_args_for_tool,
)
from sysdialogue.agent.state_store import LockStore, SessionStore, TaskStepRecord, TaskStore
from sysdialogue.audit.trace_store import AuditLog
from sysdialogue.runtime.secure_runner import LocalExecutor
from sysdialogue.tools.base import ToolResult
from sysdialogue.tools.dynamic_registry import DynamicToolRegistry
from sysdialogue.tools.registry import ToolDef, ToolRegistry
from tests.helpers import RecordingExecutor


class FakeLLM:
    def __init__(self, responses: list[list[dict]]):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def messages_create(self, *, system, messages, tools):
        self.calls.append({"system": system, "messages": deepcopy(messages), "tools": deepcopy(tools)})
        content = self.responses.pop(0)
        stop_reason = "tool_use" if any(block.get("type") == "tool_use" for block in content) else "stop"
        return LLMResponse(content=content, stop_reason=stop_reason)


def _tool_use(name: str, args: dict, tool_id: str = "call_1") -> dict:
    return {"type": "tool_use", "id": tool_id, "name": name, "input": args}


def _finish(args: dict, tool_id: str = "finish_1") -> dict:
    return _tool_use("finish_task", args, tool_id)


def _controller(tmp_path: Path, llm: FakeLLM, registry: ToolRegistry | None = None):
    events = []
    controller = AgentController(
        executor=LocalExecutor(),
        env_profile={"remote_mode": False, "current_user": "tester"},
        audit_log=AuditLog(log_dir=str(tmp_path / "audit")),
        registry=registry or ToolRegistry(),
        llm_client=llm,
        event_callback=events.append,
        session_store=SessionStore(str(tmp_path / "sessions")),
        task_store=TaskStore(str(tmp_path / "tasks")),
        lock_store=LockStore(str(tmp_path / "locks")),
    )
    return controller, events


class JudgeLLM:
    def __init__(self, content):
        self.content = content
        self.calls = 0

    def messages_create(self, *, system, messages, tools):
        self.calls += 1
        return LLMResponse(content=self.content, stop_reason="stop")


def _verified_mysql_task() -> TaskRun:
    task = TaskRun(
        task_id="task_judge",
        goal="create a Docker MySQL user and table",
        requires_environment_feedback=True,
    )
    task.changed_state = True
    task.last_action_step = 3
    task.tool_steps = 4
    task.verification_candidates.append(
        {
            "tool": "manage_container",
            "action_key": "manage_container:exec",
            "args": {"action": "exec", "command": ["mysql", "-e", "SELECT 1"]},
            "data": {"exit_code": 0, "stdout": "1"},
            "step": 4,
            "after_last_action": True,
        }
    )
    return task


def test_llm_verification_judge_can_accept_rule_sufficient_evidence() -> None:
    task = _verified_mysql_task()
    rule = VerificationJudge().judge(task)
    llm = JudgeLLM([
        {
            "type": "text",
            "text": json.dumps(
                {
                    "sufficient": True,
                    "covered_requirements": ["mysql SELECT confirms table is reachable"],
                    "missing_requirements": [],
                    "confidence": "high",
                    "recommended_next_verification": [],
                    "reason": "Targeted SELECT ran after the mutation.",
                }
            ),
        }
    ])

    judgement = LLMVerificationJudge(llm).judge(task, rule)

    assert judgement["sufficient"] is True
    assert judgement["judge"] == "llm"
    assert llm.calls == 1


def test_llm_verification_judge_cannot_override_rule_rejection() -> None:
    task = TaskRun(task_id="task_judge", goal="create user", requires_environment_feedback=True)
    rule = VerificationJudge().judge(task)
    llm = JudgeLLM([{"type": "text", "text": '{"sufficient": true}'}])

    judgement = LLMVerificationJudge(llm).judge(task, rule)

    assert judgement["sufficient"] is False
    assert llm.calls == 0


def test_llm_verification_judge_parse_failure_falls_back_to_rules() -> None:
    task = _verified_mysql_task()
    rule = VerificationJudge().judge(task)
    llm = JudgeLLM([{"type": "text", "text": "not json"}])

    judgement = LLMVerificationJudge(llm).judge(task, rule)

    assert judgement["sufficient"] is True
    assert judgement["judge"] == "rules"
    assert judgement["llm_judge_error"] == "invalid llm judgement"


def test_plan_args_match_tolerates_container_defaults_and_deferred_values() -> None:
    expected = {
        "action": "run",
        "backend": "docker",
        "command": ["sh", "-c", "ignored for run"],
        "name": "db",
        "image": "mysql:8",
        "ports": [{"host_port": 13306, "container_port": 3306, "protocol": "tcp"}],
        "env_vars": {"MYSQL_DATABASE": "app", "MYSQL_ROOT_PASSWORD": "secret"},
        "restart_policy": "no",
    }
    actual = {
        "action": "run",
        "name": "db",
        "image": "mysql:8",
        "ports": [{"host_port": 13306, "container_port": 3306}],
        "env_vars": {"MYSQL_DATABASE": "app", "MYSQL_ROOT_PASSWORD": "secret"},
    }

    assert _plan_args_match("manage_container", expected, actual)
    assert _plan_args_match(
        "manage_authorized_keys",
        {"action": "add", "username": "alice", "public_key_from_file": "/tmp/key.pub"},
        {"action": "add", "username": "alice", "public_key": "ssh-ed25519 AAAA test"},
    )


def test_frozen_plan_runtime_arg_binding_resolves_previous_step_data() -> None:
    task = TaskRun(
        task_id="task_bind",
        goal="create and delete cron",
        requires_environment_feedback=True,
        mode="plan",
        steps=[
            TaskStepRecord(
                step_id="create_cron",
                tool="manage_cron",
                args={"action": "create"},
                status="completed",
                result_data={"job_id": "job_1234abcd"},
            ),
            TaskStepRecord(
                step_id="delete_cron",
                tool="manage_cron",
                args={"action": "delete", "job_id": "{{create_cron.data.job_id}}"},
            ),
        ],
    )

    resolved, error = _resolve_runtime_args_for_tool(
        task,
        "manage_cron",
        {"action": "delete", "job_id": "{{create_cron.data.job_id}}"},
    )

    assert error is None
    assert resolved == {"action": "delete", "job_id": "job_1234abcd"}


def test_container_exec_verification_only_accepts_read_only_checks() -> None:
    select_args = {
        "action": "exec",
        "name": "mysql",
        "command": ["mysql", "-uroot", "-e", "SELECT COUNT(*) FROM app.users"],
    }
    create_args = {
        "action": "exec",
        "name": "mysql",
        "command": ["mysql", "-uroot", "-e", "CREATE TABLE app.users(id int)"],
    }

    assert _is_verification_tool("manage_container", select_args)
    assert not _is_mutating_tool("manage_container", select_args)
    assert not _is_verification_tool("manage_container", create_args)
    assert _is_mutating_tool("manage_container", create_args)


def test_verification_judge_rejects_generic_observation_after_mutation() -> None:
    task = TaskRun(
        task_id="task_verify",
        goal="create a user",
        requires_environment_feedback=True,
        changed_state=True,
        acted=True,
        last_action_step=2,
        verification_candidates=[
            {
                "tool": "get_system_info",
                "action_key": "get_system_info",
                "step": 3,
                "after_last_action": True,
                "data": {"hostname": "testbox"},
            }
        ],
    )

    judgement = VerificationJudge().judge(task)

    assert judgement["sufficient"] is False
    assert "too generic" in judgement["missing_requirements"][0]


def _controller_with_dynamic(tmp_path: Path, llm: FakeLLM, executor):
    events = []
    controller = AgentController(
        executor=executor,
        env_profile={"remote_mode": False, "current_user": "tester"},
        audit_log=AuditLog(log_dir=str(tmp_path / "audit")),
        registry=ToolRegistry(),
        llm_client=llm,
        confirm_callback=lambda req: True,
        event_callback=events.append,
        dynamic_registry=DynamicToolRegistry(
            storage_path=str(tmp_path / "dynamic_tools.json"),
        ),
        session_store=SessionStore(str(tmp_path / "sessions")),
        task_store=TaskStore(str(tmp_path / "tasks")),
        lock_store=LockStore(str(tmp_path / "locks")),
    )
    return controller, events


def _registry_with_system_info() -> ToolRegistry:
    registry = ToolRegistry()

    def get_system_info(executor):
        return ToolResult(
            success=True,
            data={"hostname": "testbox", "load": "0.01"},
            cmd_trace=["uname", "-a"],
        )

    registry.register(
        ToolDef(
            name="get_system_info",
            fn=get_system_info,
            schema={
                "name": "get_system_info",
                "description": "Get system info",
                "input_schema": {"type": "object", "properties": {}},
            },
        )
    )
    return registry


def _registry_with_mutation_and_validation() -> ToolRegistry:
    registry = _registry_with_system_info()

    def mutate_marker(executor):
        return ToolResult(success=True, data={"changed": True})

    def fail_mutation(executor):
        return ToolResult(success=False, error="mutation failed before changing state")

    def cancel_marker(executor):
        return ToolResult(success=True, data={"cancelled": True})

    def validate_config(executor, path: str, target_type: str = "auto"):
        return ToolResult(success=True, data={"path": path, "valid": True})

    registry.register(
        ToolDef(
            name="mutate_marker",
            fn=mutate_marker,
            schema={
                "name": "mutate_marker",
                "description": "Mutate test marker",
                "input_schema": {"type": "object", "properties": {}},
            },
        )
    )
    registry.register(
        ToolDef(
            name="fail_mutation",
            fn=fail_mutation,
            schema={
                "name": "fail_mutation",
                "description": "Failing mutation test marker",
                "input_schema": {"type": "object", "properties": {}},
            },
        )
    )
    registry.register(
        ToolDef(
            name="cancel_marker",
            fn=cancel_marker,
            schema={
                "name": "cancel_marker",
                "description": "Cancellation test marker",
                "input_schema": {"type": "object", "properties": {}},
            },
        )
    )
    registry.register(
        ToolDef(
            name="validate_config",
            fn=validate_config,
            schema={
                "name": "validate_config",
                "description": "Validate config",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "target_type": {"type": "string"},
                    },
                    "required": ["path"],
                },
            },
        )
    )
    return registry


def test_requires_environment_feedback_classifies_greeting_and_ops() -> None:
    assert _requires_environment_feedback("你好") is False
    assert _requires_environment_feedback("检查系统版本和负载") is True
    assert _requires_environment_feedback("解释 OpenAI API 怎么使用") is False
    assert _requires_environment_feedback("审计 API 密钥配置") is True


def test_iteration_budget_scales_by_task_complexity() -> None:
    assert _iteration_budget("你好", hard_limit=160, requires_environment_feedback=False) == 20
    assert _iteration_budget("检查系统版本和负载", hard_limit=160, requires_environment_feedback=True) == 80
    assert _iteration_budget("修改 nginx 配置并备份验证", hard_limit=160, requires_environment_feedback=True) == 140
    assert _iteration_budget("修改配置", hard_limit=60, requires_environment_feedback=True) == 60
    assert _iteration_budget("检查系统", hard_limit=10, requires_environment_feedback=True) == 20


def test_resume_command_persists_original_user_command(tmp_path: Path) -> None:
    llm = FakeLLM(
        [
            [
                _finish(
                    {
                        "status": "completed",
                        "summary": "Resumed task completed.",
                        "evidence": ["explicit resume"],
                        "verification": "",
                        "changed_state": False,
                        "remaining_risks": [],
                        "next_steps": [],
                        "no_action_reason": "No system action was required.",
                    }
                )
            ]
        ]
    )
    controller, _ = _controller(tmp_path, llm)
    task = controller.task_store.create(
        task_id="task_resume",
        session_id=controller.session_id,
        surface="tui",
        goal="hello",
        status="interrupted",
    )
    controller.session_store.ensure(controller.session_id, surface="tui")
    controller.session_store.set_status(
        controller.session_id,
        "interrupted",
        surface="tui",
        active_task_id=task.task_id,
    )

    controller.run_turn("/resume")
    record = controller.session_store.load(controller.session_id)

    assert record is not None
    assert record.entries[0] == {"role": "user", "text": "/resume"}
    assert all("continue task:" not in str(entry.get("text", "")) for entry in record.entries)


def test_greeting_can_finish_without_system_action(tmp_path: Path) -> None:
    llm = FakeLLM([
        [
            _finish({
                "status": "completed",
                "summary": "你好，我在。",
                "no_action_reason": "这是普通问候，不需要访问系统环境。",
            })
        ]
    ])
    controller, events = _controller(tmp_path, llm)

    reply = controller.run_turn("你好")

    assert "你好" in reply
    assert "未执行系统操作" in reply
    assert [event.stage for event in events] == ["task_started", "model_response", "task_finished"]


def test_operational_task_cannot_complete_without_observation(tmp_path: Path) -> None:
    llm = FakeLLM([
        [
            _finish({
                "status": "completed",
                "summary": "系统正常。",
                "evidence": ["未观察"],
            })
        ],
        [
            _finish({
                "status": "need_info",
                "summary": "还需要先观察目标环境。",
                "next_steps": ["调用只读工具检查系统状态"],
            }, tool_id="finish_2")
        ],
    ])
    controller, events = _controller(tmp_path, llm)

    reply = controller.run_turn("检查系统版本和负载")

    assert "还需要先观察" in reply
    assert llm.calls[1]["messages"][-1]["content"][0]["is_error"] is True
    assert "task_finished" in [event.stage for event in events]


def test_tool_success_then_finish_completes_operational_task(tmp_path: Path) -> None:
    llm = FakeLLM([
        [_tool_use("get_system_info", {})],
        [
            _finish({
                "status": "completed",
                "summary": "已检查系统信息。",
                "evidence": ["hostname=testbox", "load=0.01"],
                "verification": "只读系统信息已返回。",
            })
        ],
    ])
    controller, events = _controller(tmp_path, llm, _registry_with_system_info())

    reply = controller.run_turn("检查系统版本和负载")

    assert "已检查系统信息" in reply
    stages = [event.stage for event in events]
    assert stages[:2] == ["task_started", "model_response"]
    assert "tool_started" in stages
    assert "tool_finished" in stages
    assert "verification" in stages
    assert stages[-1] == "task_finished"


def test_session_store_persists_user_and_assistant_turn(tmp_path: Path) -> None:
    llm = FakeLLM([
        [
            _finish({
                "status": "completed",
                "summary": "你好，我在。",
                "no_action_reason": "这是普通问候，不需要访问系统环境。",
            })
        ]
    ])
    controller, _ = _controller(tmp_path, llm)

    controller.run_turn("你好")

    record = controller.session_store.load(controller.session_id)
    assert record is not None
    assert [entry["role"] for entry in record.entries] == ["user", "assistant"]
    assert record.user_messages[-1] == "你好"


def test_direct_mutating_tool_respects_existing_lock_lease(tmp_path: Path, monkeypatch) -> None:
    import sysdialogue.agent.controller as controller_module

    monkeypatch.setattr(controller_module, "_DIRECT_LOCK_TIMEOUT", 0.1)
    llm = FakeLLM([
        [_tool_use("mutate_marker", {}, tool_id="mutate_1")],
        [
            _finish({
                "status": "failed",
                "summary": "资源锁冲突，未执行变更。",
                "next_steps": ["等待当前任务结束后重试"],
            })
        ],
    ])
    controller, _ = _controller(tmp_path, llm, _registry_with_mutation_and_validation())
    controller.lock_store.acquire(
        "tool:mutate_marker",
        task_id="other_task",
        session_id="other_session",
        surface="web",
        timeout=0.1,
    )

    reply = controller.run_turn("修改配置")

    assert "资源锁冲突" in reply
    tool_result = llm.calls[1]["messages"][-1]["content"][0]
    assert tool_result["is_error"] is True
    assert "resource_locked: tool:mutate_marker" in tool_result["content"]


def test_system_config_empty_value_is_treated_as_mutation_for_direct_locking() -> None:
    assert _direct_lock_scopes("get_set_system_config", {"key": "hostname"}) == []
    assert _direct_lock_scopes("get_set_system_config", {"key": "hostname", "value": None}) == []
    assert _direct_lock_scopes("get_set_system_config", {"key": "hostname", "value": ""}) == [
        "system-config:hostname"
    ]


def test_plain_text_responses_trigger_react_correction_then_failure(tmp_path: Path) -> None:
    llm = FakeLLM([
        [{"type": "text", "text": "系统正常。"}],
        [{"type": "text", "text": "我已经回答了。"}],
        [{"type": "text", "text": "仍然没有工具。"}],
    ])
    controller, events = _controller(tmp_path, llm)

    reply = controller.run_turn("检查系统版本和负载")

    assert "未按 ReAct 协议" in reply
    assert len(llm.calls) == 3
    assert "ReAct protocol correction" in llm.calls[1]["messages"][-1]["content"]
    assert "ReAct protocol correction" in llm.calls[2]["messages"][-1]["content"]
    persisted_history = json.dumps(controller.conversation_manager.history, ensure_ascii=False)
    assert "ReAct protocol correction" not in persisted_history
    assert [event.stage for event in events].count("correction") == 2


def test_finish_task_requires_summary(tmp_path: Path) -> None:
    llm = FakeLLM([
        [_finish({"status": "completed", "no_action_reason": "普通聊天"})],
        [
            _finish({
                "status": "completed",
                "summary": "已按协议收口。",
                "no_action_reason": "普通聊天，不访问系统。",
            }, tool_id="finish_2")
        ],
    ])
    controller, _ = _controller(tmp_path, llm)

    reply = controller.run_turn("你好")

    assert "已按协议收口" in reply
    assert llm.calls[1]["messages"][-1]["content"][0]["is_error"] is True


def test_changed_state_requires_verification_after_mutation(tmp_path: Path) -> None:
    llm = FakeLLM([
        [_tool_use("get_system_info", {})],
        [_tool_use("mutate_marker", {}, tool_id="mutate_1")],
        [
            _finish({
                "status": "completed",
                "summary": "已经修改并验证。",
                "evidence": ["mutate_marker changed=true"],
                "verification": "模型声称已验证，但还没有验证工具结果。",
                "changed_state": True,
            })
        ],
        [_tool_use("validate_config", {"path": "/tmp/example.conf"}, tool_id="validate_1")],
        [
            _finish({
                "status": "completed",
                "summary": "已经修改并完成验证。",
                "evidence": ["mutate_marker changed=true", "validate_config valid=true"],
                "verification": "validate_config 在变更后返回 valid=true。",
                "changed_state": True,
            }, tool_id="finish_2")
        ],
    ])
    controller, events = _controller(tmp_path, llm, _registry_with_mutation_and_validation())

    reply = controller.run_turn("修改配置并验证")

    assert "完成验证" in reply
    rejected_result = llm.calls[3]["messages"][-1]["content"][0]
    assert rejected_result["is_error"] is True
    assert "verification tool/workflow after the mutation" in rejected_result["content"]
    stages = [event.stage for event in events]
    assert stages.count("correction") == 1


def test_frozen_plan_allows_only_dependency_ready_steps(tmp_path: Path) -> None:
    llm = FakeLLM([
        [
            _tool_use(
                "set_execution_mode",
                {
                    "mode": "plan",
                    "plan_steps": [
                        {
                            "step_id": "observe",
                            "tool": "get_system_info",
                            "args": {},
                            "purpose": "observe host",
                        },
                        {
                            "step_id": "verify",
                            "tool": "validate_config",
                            "args": {"path": "/tmp/example.conf"},
                            "purpose": "verify config",
                            "depends_on": ["observe"],
                            "finding_id": "finding-1",
                            "severity": "P2",
                            "blocking": True,
                        },
                    ],
                },
                tool_id="plan_1",
            )
        ],
        [_tool_use("validate_config", {"path": "/tmp/example.conf"}, tool_id="verify_too_early")],
        [_tool_use("get_system_info", {}, tool_id="observe_1")],
        [_tool_use("validate_config", {"path": "/tmp/example.conf"}, tool_id="verify_1")],
        [
            _finish({
                "status": "completed",
                "summary": "Plan completed.",
                "evidence": ["observed host", "validated config"],
                "verification": "validate_config completed after observe.",
            })
        ],
    ])
    controller, _ = _controller(tmp_path, llm, _registry_with_mutation_and_validation())

    reply = controller.run_turn("run a frozen plan")

    assert "Plan completed" in reply
    rejected = llm.calls[2]["messages"][-1]["content"][0]
    assert rejected["is_error"] is True
    assert "Frozen plan deviation rejected" in rejected["content"]
    record = next(controller.task_store.storage_dir.glob("*.json"))
    payload = json.loads(record.read_text(encoding="utf-8"))
    verify_step = [step for step in payload["steps"] if step["step_id"] == "verify"][0]
    assert verify_step["depends_on"] == ["observe"]
    assert verify_step["finding_id"] == "finding-1"


def test_frozen_plan_cron_create_delete_uses_bound_job_id(tmp_path: Path) -> None:
    registry = ToolRegistry()
    calls = []

    def manage_cron(executor, action: str, scope: str = "user", schedule=None, job_target=None, job_id=None):
        calls.append({"action": action, "scope": scope, "job_id": job_id})
        if action == "create":
            return ToolResult(
                success=True,
                data={
                    "job_id": "job_bound123",
                    "scope": scope,
                    "schedule": schedule,
                    "job_target": job_target,
                    "enabled": True,
                },
            )
        if action == "list":
            return ToolResult(success=True, data={"managed": {}})
        if action == "delete":
            return ToolResult(success=True, data={"job_id": job_id, "action": "delete"})
        return ToolResult(success=False, error="bad action")

    registry.register(
        ToolDef(
            name="manage_cron",
            fn=manage_cron,
            schema={
                "name": "manage_cron",
                "description": "Cron test tool",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string"},
                        "scope": {"type": "string"},
                        "schedule": {"type": "string"},
                        "job_id": {"type": "string"},
                        "job_target": {"type": "object"},
                    },
                    "required": ["action"],
                },
            },
        )
    )
    llm = FakeLLM([
        [
            _tool_use(
                "set_execution_mode",
                {
                    "mode": "plan",
                    "plan_steps": [
                        {
                            "step_id": "create_cron",
                            "tool": "manage_cron",
                            "args": {
                                "action": "create",
                                "scope": "system",
                                "schedule": "*/5 * * * *",
                                "job_target": {"kind": "tool", "name": "get_system_info", "args": {}},
                            },
                        },
                        {
                            "step_id": "list_after_create",
                            "tool": "manage_cron",
                            "args": {"action": "list", "scope": "system"},
                            "depends_on": ["create_cron"],
                        },
                        {
                            "step_id": "delete_cron",
                            "tool": "manage_cron",
                            "args": {
                                "action": "delete",
                                "scope": "system",
                                "job_id": "{{create_cron.data.job_id}}",
                            },
                            "depends_on": ["list_after_create"],
                        },
                        {
                            "step_id": "list_after_delete",
                            "tool": "manage_cron",
                            "args": {"action": "list", "scope": "system"},
                            "depends_on": ["delete_cron"],
                        },
                    ],
                },
                tool_id="plan_1",
            )
        ],
        [
            _tool_use(
                "manage_cron",
                {
                    "action": "create",
                    "scope": "system",
                    "schedule": "*/5 * * * *",
                    "job_target": {"kind": "tool", "name": "get_system_info", "args": {}},
                },
                tool_id="create_1",
            )
        ],
        [_tool_use("manage_cron", {"action": "list", "scope": "system"}, tool_id="list_1")],
        [
            _tool_use(
                "manage_cron",
                {"action": "delete", "scope": "system", "job_id": "{{create_cron.data.job_id}}"},
                tool_id="delete_1",
            )
        ],
        [_tool_use("manage_cron", {"action": "list", "scope": "system"}, tool_id="list_2")],
        [
            _finish({
                "status": "completed",
                "summary": "Cron lifecycle completed.",
                "evidence": ["created job_bound123", "deleted job_bound123", "list verified"],
                "verification": "manage_cron:list ran after delete.",
                "changed_state": True,
            })
        ],
    ])
    controller, _ = _controller(tmp_path, llm, registry)
    controller.confirm_callback = lambda req: True

    reply = controller.run_turn("create then delete cron")

    assert "Cron lifecycle completed" in reply
    assert calls[2] == {"action": "delete", "scope": "system", "job_id": "job_bound123"}
    payload = json.loads(next(controller.task_store.storage_dir.glob("*.json")).read_text(encoding="utf-8"))
    create_step = [step for step in payload["steps"] if step["step_id"] == "create_cron"][0]
    assert create_step["result_data"]["job_id"] == "job_bound123"


def test_react_runner_stops_repeated_no_progress_plan_deviations(tmp_path: Path) -> None:
    responses = [
        [
            _tool_use(
                "set_execution_mode",
                {
                    "mode": "plan",
                    "plan_steps": [
                        {
                            "step_id": "mutate",
                            "tool": "mutate_marker",
                            "args": {},
                        }
                    ],
                },
                tool_id="plan_1",
            )
        ],
    ]
    responses.extend([[_tool_use("get_system_info", {}, tool_id=f"wrong_{index}")] for index in range(10)])
    llm = FakeLLM(responses)
    controller, events = _controller(tmp_path, llm, _registry_with_mutation_and_validation())

    reply = controller.run_turn("run a frozen mutation plan")

    assert "Task blocked after repeated no-progress" in reply
    assert len(llm.calls) < len(responses)
    assert [event.stage for event in events].count("correction") >= 8


def test_failed_mutation_does_not_satisfy_completion_gate(tmp_path: Path) -> None:
    llm = FakeLLM([
        [_tool_use("fail_mutation", {}, tool_id="mutate_1")],
        [_tool_use("validate_config", {"path": "/tmp/example.conf"}, tool_id="validate_1")],
        [
            _finish({
                "status": "completed",
                "summary": "错误地声称完成。",
                "evidence": ["fail_mutation failed", "validate_config valid=true"],
                "verification": "validate_config 在失败变更后返回 valid=true。",
                "changed_state": True,
            })
        ],
        [
            _finish({
                "status": "failed",
                "summary": "变更工具失败，未完成修改。",
                "next_steps": ["检查失败原因后重试变更"],
            }, tool_id="finish_2")
        ],
    ])
    controller, events = _controller(tmp_path, llm, _registry_with_mutation_and_validation())

    reply = controller.run_turn("修改配置并验证")

    assert "未完成修改" in reply
    rejected_result = llm.calls[3]["messages"][-1]["content"][0]
    assert rejected_result["is_error"] is True
    assert "Failed mutation attempts cannot be reported as completed" in rejected_result["content"]
    assert "task_failed" in [event.stage for event in events]


def test_cancelled_multi_tool_turn_writes_results_for_all_tool_calls(tmp_path: Path) -> None:
    llm = FakeLLM([
        [
            _tool_use("cancel_marker", {}, tool_id="cancel_1"),
            _tool_use("get_system_info", {}, tool_id="info_1"),
        ],
    ])
    controller, _ = _controller(tmp_path, llm, _registry_with_mutation_and_validation())

    original_dispatch = controller._dispatch_tool

    def dispatch_and_cancel(name, args, tool_use_id):
        result = original_dispatch(name, args, tool_use_id)
        if name == "cancel_marker":
            controller.request_cancel()
        return result

    controller._dispatch_tool = dispatch_and_cancel

    reply = controller.run_turn("检查系统并取消")

    assert "已取消" in reply
    tool_result_message = controller.conversation_manager.history[-2]
    result_ids = [block["tool_use_id"] for block in tool_result_message["content"]]
    assert result_ids == ["cancel_1", "info_1"]
    assert tool_result_message["content"][1]["is_error"] is True
    assert "未执行" in tool_result_message["content"][1]["content"]


def test_verified_workflow_can_complete_without_extra_validation_tool(tmp_path: Path) -> None:
    llm = FakeLLM([
        [
            _tool_use(
                "set_execution_mode",
                {
                    "mode": "workflow",
                    "workflow_name": "safe_config_patch",
                    "workflow_params": {"file_path": "/tmp/example.conf"},
                },
                tool_id="workflow_1",
            )
        ],
        [
            _finish({
                "status": "completed",
                "summary": "配置修改 workflow 已完成。",
                "evidence": ["safe_config_patch final_status=completed"],
                "verification": "safe_config_patch 内部已执行 validate_config。",
                "changed_state": True,
            })
        ],
    ])
    controller, _ = _controller(tmp_path, llm, _registry_with_mutation_and_validation())

    def fake_workflow(args, tool_use_id):
        return {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": '{"final_status":"completed"}',
            "is_error": False,
        }

    controller._handle_set_execution_mode = fake_workflow

    reply = controller.run_turn("修改配置并验证")

    assert "workflow 已完成" in reply


def test_dynamic_tool_can_be_proposed_then_executed_by_default(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import sysdialogue.tools.dynamic_registry as dynamic_registry_module

    class FixedUUID:
        hex = "abc12345deadbeef"

        def __str__(self) -> str:
            return "abc12345-dead-beef-0000-000000000000"

    monkeypatch.setattr(dynamic_registry_module.uuid, "uuid4", lambda: FixedUUID())

    llm = FakeLLM([
        [
            _tool_use(
                "propose_dynamic_tool",
                {
                    "intent_summary": "Echo a diagnostic token that no static tool exposes directly.",
                    "proposed_tool_name": "echo_token",
                    "cmd_template": ["echo", "{message}"],
                    "params": {
                        "message": {
                            "type": "string",
                            "description": "message to echo",
                            "required": True,
                        }
                    },
                    "consequences": "只读输出，不修改系统。",
                    "risk_assessment": "SAFE shape, still requires dynamic confirmation.",
                    "estimated_risk": "WARN-LOW",
                    "changes_state": False,
                    "reversible": True,
                },
                tool_id="propose_1",
            )
        ],
        [
            _tool_use(
                "execute_dynamic_tool",
                {
                    "tool_id": "dyn_abc12345",
                    "args": {"message": "hello"},
                },
                tool_id="execute_1",
            )
        ],
        [
            _finish({
                "status": "completed",
                "summary": "动态工具已执行。",
                "evidence": ["execute_dynamic_tool output=hello"],
                "verification": "命令返回 exit_code=0，且工具声明为只读。",
            })
        ],
    ])

    executor = RecordingExecutor(
        handler=lambda cmd, timeout: ("hello\n", 0) if cmd == ["echo", "hello"] else ("", 1)
    )
    controller, events = _controller_with_dynamic(tmp_path, llm, executor)

    reply = controller.run_turn("执行一个当前静态工具没有覆盖的 echo 诊断动作")

    assert "动态工具已执行" in reply
    assert executor.calls == [["echo", "hello"]]
    execute_result = json.loads(llm.calls[2]["messages"][-1]["content"][0]["content"])
    assert execute_result["declared_changes_state"] is False
    assert execute_result["changes_state"] is False
    assert "dynamic_tools.json" in str(controller.dynamic_registry.storage_path)
    assert "confirmation_requested" in [event.stage for event in events]


def test_execute_dynamic_tool_inline_mode_supports_one_off_command(tmp_path: Path) -> None:
    llm = FakeLLM([
        [
            _tool_use(
                "execute_dynamic_tool",
                {
                    "tool_name": "echo_once",
                    "cmd_template": ["echo", "{message}"],
                    "args": {"message": "hello"},
                    "params": {
                        "message": {
                            "type": "string",
                            "description": "message to echo",
                            "required": True,
                        }
                    },
                    "intent_summary": "Run a one-off echo diagnostic.",
                    "consequences": "只读输出，不修改系统。",
                    "risk_assessment": "SAFE shape, still requires dynamic confirmation.",
                    "estimated_risk": "WARN-LOW",
                    "changes_state": False,
                    "reversible": True,
                },
                tool_id="execute_inline_1",
            )
        ],
        [
            _finish({
                "status": "completed",
                "summary": "一次性动态命令已执行。",
                "evidence": ["execute_dynamic_tool output=hello"],
                "verification": "命令返回 exit_code=0，且 inline 动态命令声明为只读。",
            })
        ],
    ])

    executor = RecordingExecutor(
        handler=lambda cmd, timeout: ("hello\n", 0) if cmd == ["echo", "hello"] else ("", 1)
    )
    controller, _ = _controller_with_dynamic(tmp_path, llm, executor)

    reply = controller.run_turn("执行一个一次性的 echo 诊断动作")

    assert "一次性动态命令已执行" in reply
    assert executor.calls == [["echo", "hello"]]
    execute_result = json.loads(llm.calls[1]["messages"][-1]["content"][0]["content"])
    assert execute_result["dynamic_mode"] == "inline"
    assert execute_result["changes_state"] is False
    assert not (tmp_path / "dynamic_tools.json").exists()


def test_registered_dynamic_tool_is_reused_by_signature(tmp_path: Path) -> None:
    registry = DynamicToolRegistry(storage_path=str(tmp_path / "dynamic_tools.json"))

    first = registry.register(
        name="echo_token",
        description="Echo a token",
        cmd_template=["echo", "{message}"],
        params={"message": {"type": "string", "required": True}},
        consequences="只读输出，不修改系统。",
        risk_assessment="SAFE shape.",
        estimated_risk="WARN-LOW",
        changes_state=False,
        reversible=True,
    )
    second = registry.register(
        name="echo_token_again",
        description="Echo the same token family",
        cmd_template=["echo", "{message}"],
        params={"message": {"type": "string", "required": True}},
        consequences="只读输出，不修改系统。",
        risk_assessment="SAFE shape.",
        estimated_risk="WARN-LOW",
        changes_state=False,
        reversible=True,
    )

    assert second["tool_id"] == first["tool_id"]
    assert second["reused_existing"] is True
    assert len(registry.list_tools()) == 1


def test_dynamic_tool_declared_read_only_must_be_proven_before_completion(
    tmp_path: Path,
) -> None:
    llm = FakeLLM([
        [
            _tool_use(
                "execute_dynamic_tool",
                {
                    "tool_id": "placeholder",
                    "args": {"path": "/tmp/marker"},
                },
                tool_id="execute_1",
            )
        ],
        [
            _finish({
                "status": "completed",
                "summary": "错误地声称动态变更已完成。",
                "evidence": ["execute_dynamic_tool exit_code=0"],
            })
        ],
        [
            _finish({
                "status": "failed",
                "summary": "动态工具执行后还没有完成验证。",
                "next_steps": ["执行后置验证工具"],
            }, tool_id="finish_2")
        ],
    ])
    executor = RecordingExecutor(
        handler=lambda cmd, timeout: ("", 0) if cmd == ["touch", "/tmp/marker"] else ("", 1)
    )
    controller, _ = _controller_with_dynamic(tmp_path, llm, executor)
    tool = controller.dynamic_registry.register(
        name="touch_marker",
        description="Mutates a marker file",
        cmd_template=["touch", "{path}"],
        params={"path": {"type": "string", "required": True}},
        consequences="创建或更新文件时间戳。",
        risk_assessment="实际会修改文件系统。",
        estimated_risk="WARN-LOW",
        changes_state=False,
    )
    llm.responses[0][0]["input"]["tool_id"] = tool["tool_id"]

    reply = controller.run_turn("执行一个动态变更")

    assert "还没有完成验证" in reply
    execute_result = json.loads(llm.calls[1]["messages"][-1]["content"][0]["content"])
    assert execute_result["declared_changes_state"] is False
    assert execute_result["changes_state"] is True
    rejected_result = llm.calls[2]["messages"][-1]["content"][0]
    assert rejected_result["is_error"] is True
    assert "verification tool/workflow after the mutation" in rejected_result["content"]


def test_execute_dynamic_tool_invalid_args_return_tool_errors(tmp_path: Path) -> None:
    cases = [
        {"tool_id": "", "args": {}},
        {"tool_id": "dyn_missing", "args": [], "timeout": 30},
        {"tool_id": "dyn_missing", "args": {}, "timeout": "abc"},
    ]
    for index, bad_args in enumerate(cases):
        llm = FakeLLM([
            [_tool_use("execute_dynamic_tool", bad_args, tool_id=f"execute_{index}")],
            [
                _finish({
                    "status": "failed",
                    "summary": "动态工具参数无效。",
                    "next_steps": ["修正 execute_dynamic_tool 参数"],
                }, tool_id=f"finish_{index}")
            ],
        ])
        controller, _ = _controller_with_dynamic(tmp_path / f"case_{index}", llm, RecordingExecutor())

        reply = controller.run_turn("执行动态工具")

        assert "动态工具参数无效" in reply
        tool_result = llm.calls[1]["messages"][-1]["content"][0]
        assert tool_result["is_error"] is True


def test_system_prompt_lists_reusable_dynamic_tools(tmp_path: Path) -> None:
    llm = FakeLLM([])
    controller, _ = _controller_with_dynamic(tmp_path, llm, RecordingExecutor())
    controller.dynamic_registry.register(
        name="echo_token",
        description="Echo a token",
        cmd_template=["echo", "{message}"],
        params={"message": {"type": "string", "required": True}},
        consequences="只读输出，不修改系统。",
        risk_assessment="SAFE shape.",
        estimated_risk="WARN-LOW",
        changes_state=False,
        reversible=True,
    )

    prompt = controller._current_system_prompt()

    assert "[Reusable DynTools]" in prompt
    assert "dyn_" in prompt
    assert "echo_token" in prompt
