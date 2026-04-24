from __future__ import annotations

import json
import threading
from pathlib import Path

from sysdialogue.agent.command_registry import CommandRegistry
from sysdialogue.agent.controller import AgentController, LLMResponse
from sysdialogue.agent.hooks import HookEvent, HookManager
from sysdialogue.agent.memory import MemoryManager
from sysdialogue.agent.permission_policy import PermissionPolicy, PermissionRule
from sysdialogue.agent.role_agents import RoleRunner
from sysdialogue.agent.skills import SkillManager
from sysdialogue.agent.state_store import LockStore, SessionStore, TaskStore
from sysdialogue.agent.target_profile import TargetProfileStore
from sysdialogue.agent.trace_store import TraceStore
from sysdialogue.audit.trace_store import AuditLog
from sysdialogue.runtime.secure_runner import LocalExecutor
from sysdialogue.tools.base import ToolResult
from sysdialogue.tools.registry import ToolDef, ToolRegistry
from sysdialogue.web.app import create_web_app


class NoLLM:
    def messages_create(self, *, system, messages, tools):
        return LLMResponse(content=[], stop_reason="stop")


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolDef(
            name="get_system_info",
            fn=lambda executor: ToolResult(success=True, data={"hostname": "box"}),
            schema={
                "name": "get_system_info",
                "description": "Get system info",
                "input_schema": {"type": "object", "properties": {}},
            },
        )
    )
    return registry


def _controller(tmp_path: Path) -> AgentController:
    return AgentController(
        executor=LocalExecutor(),
        env_profile={"remote_mode": False, "hostname": "box"},
        audit_log=AuditLog(log_dir=str(tmp_path / "audit")),
        registry=_registry(),
        llm_client=NoLLM(),
        session_store=SessionStore(str(tmp_path / "sessions")),
        task_store=TaskStore(str(tmp_path / "tasks")),
        lock_store=LockStore(str(tmp_path / "locks")),
        permission_policy=PermissionPolicy(str(tmp_path / "policy.json")),
        memory_manager=MemoryManager(str(tmp_path / "memory")),
        trace_store=TraceStore(str(tmp_path / "traces")),
        command_registry=CommandRegistry(),
        target_profile_store=TargetProfileStore(str(tmp_path / "targets")),
    )


def test_permission_policy_can_deny_tool_without_lowering_block(tmp_path: Path) -> None:
    policy = PermissionPolicy(str(tmp_path / "policy.json"))
    policy.rules = [
        PermissionRule(
            rule_id="deny-info",
            action="deny",
            kind="tool",
            pattern="get_system_info",
            description="blocked by local policy",
        )
    ]

    decision = policy.evaluate_tool(tool="get_system_info", args={}, risk_level="SAFE")
    block_decision = policy.evaluate_tool(tool="anything", args={}, risk_level="BLOCK")

    assert decision.action == "deny"
    assert decision.rule_id == "deny-info"
    assert block_decision.action == "deny"
    assert block_decision.rule_id == "risk:block"


def test_permission_policy_specific_path_rule_overrides_broad_tool_allow(tmp_path: Path) -> None:
    policy = PermissionPolicy(str(tmp_path / "policy.json"))
    policy.rules = [
        PermissionRule(rule_id="allow-delete", action="allow", kind="tool", pattern="delete_path"),
        PermissionRule(rule_id="deny-etc", action="deny", kind="path", pattern="/etc/*"),
    ]

    decision = policy.evaluate_tool(
        tool="delete_path",
        args={"path": "/etc/hosts"},
        risk_level="SAFE",
    )

    assert decision.action == "deny"
    assert decision.rule_id == "deny-etc"


def test_permission_policy_denial_is_returned_as_tool_error(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    controller.permission_policy.rules = [
        PermissionRule(rule_id="deny-info", action="deny", kind="tool", pattern="get_system_info")
    ]
    controller.bind_task("task_policy")
    controller.task_store.create(
        task_id="task_policy",
        session_id=controller.session_id,
        surface="test",
        goal="policy",
    )
    try:
        result = controller._dispatch_tool("get_system_info", {}, "tool_1")
    finally:
        controller.unbind_task()

    assert result["is_error"] is True
    assert "PermissionPolicy denied" in result["content"]
    spans = controller.trace_store.list_spans(controller.session_id)
    assert any(span.span_type == "guardrail" and span.status == "denied" for span in spans)


def test_memory_manager_redacts_secret_and_renders_summary(tmp_path: Path) -> None:
    memory = MemoryManager(str(tmp_path / "memory"))

    record = memory.remember(
        scope="global",
        key="api",
        value="OPENAI_API_KEY=sk-secret",
        source="test",
    )

    assert "sk-secret" not in record.value
    assert "<redacted>" in record.value
    assert "api" in memory.render_prompt_summary()
    assert (tmp_path / "memory" / "MEMORY.md").exists()


def test_memory_manager_preserves_concurrent_writes(tmp_path: Path) -> None:
    memory_dir = tmp_path / "memory"

    def worker(index: int) -> None:
        MemoryManager(str(memory_dir)).remember(
            scope="global",
            key=f"key_{index}",
            value=f"value_{index}",
            source="test",
        )

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    keys = {record.key for record in MemoryManager(str(memory_dir)).list_records(limit=20)}
    assert {f"key_{index}" for index in range(12)} <= keys


def test_trace_store_writes_jsonl_spans(tmp_path: Path) -> None:
    store = TraceStore(str(tmp_path / "traces"))
    span = store.start_span(
        session_id="session_a",
        task_id="task_a",
        span_type="tool_call",
        name="get_system_info",
        data={"api_key": "secret", "safe": "value"},
    )
    store.end_span(span, summary="done")

    loaded = store.list_spans("session_a")
    raw = (tmp_path / "traces" / "session_a.jsonl").read_text(encoding="utf-8")

    assert loaded[-1].span_type == "tool_call"
    assert loaded[-1].data["api_key"] == "<redacted>"
    assert "secret" not in raw


def test_trace_store_redacts_secret_values_in_generic_fields(tmp_path: Path) -> None:
    store = TraceStore(str(tmp_path / "traces"))
    span = store.start_span(
        session_id="session_secret",
        task_id="task_secret",
        span_type="tool_call",
        name="bad_tool",
        data={
            "output_preview": "OPENAI_API_KEY=sk-live Bearer abc.def",
            "nested": {"message": "token=plain-secret"},
        },
    )
    store.end_span(span, summary="stderr leaked password=pw123")

    raw = (tmp_path / "traces" / "session_secret.jsonl").read_text(encoding="utf-8")

    assert "sk-live" not in raw
    assert "abc.def" not in raw
    assert "plain-secret" not in raw
    assert "pw123" not in raw


def test_slash_commands_persist_and_can_compact_memory(tmp_path: Path) -> None:
    controller = _controller(tmp_path)

    status = controller.run_turn("/status")
    compact = controller.run_turn("/compact remember nginx service context")
    memory = controller.run_turn("/memory")

    record = controller.session_store.load(controller.session_id)
    assert "Session:" in status
    assert "Compacted" in compact
    assert "remember nginx" in memory
    assert record is not None
    assert [entry["role"] for entry in record.entries[-2:]] == ["user", "assistant"]


def test_tui_facing_slash_commands_render_readable_summaries(tmp_path: Path) -> None:
    project_skill = tmp_path / ".sysdialogue" / "skills" / "deploy" / "SKILL.md"
    project_skill.parent.mkdir(parents=True)
    project_skill.write_text(
        "---\nname: deploy\ndescription: deploy playbook\nallowed_tools: [manage_service]\n---\nDeploy safely.\n",
        encoding="utf-8",
    )
    controller = _controller(tmp_path)
    controller.skill_manager = SkillManager(project_root=tmp_path, user_root=tmp_path / "user-skills")
    controller.permission_policy.rules = [
        PermissionRule(rule_id="ask-service", action="ask", kind="tool", pattern="manage_service")
    ]

    skills = controller.run_turn("/skills")
    why = controller.run_turn("/why manage_service")
    target = controller.run_turn("/target set service=nginx")

    assert "tools=manage_service" in skills
    assert "## Permission decision for `manage_service`" in why
    assert "Matched rule: `ask-service`" in why
    assert "Updated target" in target
    assert controller.conversation_manager.context["target:service"] == "nginx"


def test_web_app_exposes_command_trace_and_memory_routes() -> None:
    app = create_web_app(type("Config", (), {})())
    paths = {route.path for route in app.routes}

    assert "/api/session/{session_id}/command" in paths
    assert "/api/session/{session_id}/traces" in paths
    assert "/api/session/{session_id}/memory" in paths
    assert "/api/session/{session_id}/skills" in paths
    assert "/api/session/{session_id}/skill" in paths
    assert "/api/session/{session_id}/hooks" in paths
    assert "/api/session/{session_id}/permissions/explain" in paths


def test_skill_manager_project_skill_overrides_user_skill(tmp_path: Path) -> None:
    user_skill = tmp_path / "user" / "deploy" / "SKILL.md"
    project_skill = tmp_path / "project" / ".sysdialogue" / "skills" / "deploy" / "SKILL.md"
    user_skill.parent.mkdir(parents=True)
    project_skill.parent.mkdir(parents=True)
    user_skill.write_text("---\nname: deploy\ndescription: user deploy\n---\nuser body\n", encoding="utf-8")
    project_skill.write_text(
        "---\nname: deploy\ndescription: project deploy\nallowed_tools: [manage_service]\n---\nproject body\n",
        encoding="utf-8",
    )

    manager = SkillManager(project_root=tmp_path / "project", user_root=tmp_path / "user")
    invocation = manager.activate("deploy", {"service": "nginx"}, source="user")

    assert "project deploy" in manager.render_prompt_summary()
    assert "project body" in invocation.context
    assert "user body" not in invocation.context
    assert '"service": "nginx"' in invocation.context


def test_slash_skill_activates_context_without_os_execution(tmp_path: Path) -> None:
    project_skill = tmp_path / ".sysdialogue" / "skills" / "audit" / "SKILL.md"
    project_skill.parent.mkdir(parents=True)
    project_skill.write_text(
        "---\nname: audit\ndescription: audit playbook\n---\nRead-only audit guidance.\n",
        encoding="utf-8",
    )
    controller = _controller(tmp_path)
    controller.skill_manager = SkillManager(project_root=tmp_path, user_root=tmp_path / "user-skills")

    reply = controller.run_turn('/skill audit {"scope":"ssh"}')

    assert "Activated skill audit" in reply
    assert "skill:audit" in controller.conversation_manager.context
    assert "Read-only audit guidance" in controller.conversation_manager.context["skill:audit"]


def test_activate_skill_meta_tool_injects_context(tmp_path: Path) -> None:
    project_skill = tmp_path / ".sysdialogue" / "skills" / "triage" / "SKILL.md"
    project_skill.parent.mkdir(parents=True)
    project_skill.write_text(
        "---\nname: triage\ndescription: triage playbook\nmodel_invocable: true\n---\nTriage steps.\n",
        encoding="utf-8",
    )
    controller = _controller(tmp_path)
    controller.skill_manager = SkillManager(project_root=tmp_path, user_root=tmp_path / "user-skills")

    result = controller._dispatch_tool("activate_skill", {"name": "triage", "args": {"x": 1}}, "skill_1")

    assert result["is_error"] is False
    assert "skill:triage" in controller.conversation_manager.context


def test_hook_manager_notify_and_inject_context(tmp_path: Path) -> None:
    hooks_path = tmp_path / "hooks.json"
    hooks_path.write_text(
        json.dumps(
            {
                "hooks": [
                    {"id": "notify", "event": "task_started", "action": "notify", "message": "task {goal}"},
                    {"id": "ctx", "event": "task_started", "action": "inject_context", "context": "ctx {goal}"},
                ]
            }
        ),
        encoding="utf-8",
    )
    controller = _controller(tmp_path)
    manager = HookManager(project_root=tmp_path / "project", user_path=hooks_path)

    results = manager.run(
        HookEvent(event="task_started", session_id=controller.session_id, payload={"goal": "audit"}),
        controller=controller,
    )

    assert [result.hook_id for result in results] == ["notify", "ctx"]
    assert controller.conversation_manager.context["hook:ctx"] == "ctx audit"


def test_role_runner_handoff_is_structured_and_advisory() -> None:
    record = RoleRunner().handoff(
        role="verifier",
        objective="verify nginx restart",
        constraints={"read_only": True},
    )

    assert record.role == "verifier"
    assert "verify" in record.recommendation.lower()
    assert "manage_service" in record.allowed_tools


def test_handoff_meta_tool_returns_record(tmp_path: Path) -> None:
    controller = _controller(tmp_path)

    result = controller._dispatch_tool(
        "handoff_to_role",
        {"role": "risk_reviewer", "objective": "review restart risk", "constraints": {"service": "nginx"}},
        "handoff_1",
    )
    payload = json.loads(result["content"])

    assert result["is_error"] is False
    assert payload["role"] == "risk_reviewer"
    assert payload["handoff_id"].startswith("handoff_")


def test_permission_explain_includes_candidates_and_suggestion(tmp_path: Path) -> None:
    policy = PermissionPolicy(str(tmp_path / "policy.json"))
    policy.rules = [
        PermissionRule(rule_id="allow-all", action="allow", kind="tool", pattern="*"),
        PermissionRule(rule_id="ask-service", action="ask", kind="tool", pattern="manage_service"),
    ]

    explanation = policy.explain_tool(tool="manage_service", args={}, risk_level="SAFE")

    assert explanation["action"] == "ask"
    assert explanation["matched_rule"]["rule_id"] == "ask-service"
    assert explanation["candidate_rules"]
    assert explanation["suggested_always_grant"] is True


def test_target_profile_store_persists_facts(tmp_path: Path) -> None:
    store = TargetProfileStore(str(tmp_path / "targets"))
    target_id = store.target_id_from_env({"remote_mode": True, "host": "example.com", "ssh_port": 22})

    store.remember_fact(target_id, "service", "nginx")
    summary = store.render_prompt_summary(target_id)

    assert target_id == "ssh-example.com-22"
    assert "service" in summary
    assert "nginx" in summary


def test_memory_forget_removes_record(tmp_path: Path) -> None:
    manager = MemoryManager(str(tmp_path / "memory"))
    record = manager.remember(scope="global", key="note", value="hello", source="test")

    assert manager.forget(record.memory_id) is True
    assert manager.forget(record.memory_id) is False
    assert not manager.list_records()
