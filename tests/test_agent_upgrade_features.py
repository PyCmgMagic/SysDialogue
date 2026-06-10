from __future__ import annotations

import json
import threading
import zipfile
from pathlib import Path

from sysdialogue.agent.command_registry import CommandRegistry
from sysdialogue.agent.controller import AgentController, LLMResponse
from sysdialogue.agent.hooks import HookEvent, HookManager
from sysdialogue.agent.memory import MemoryManager
from sysdialogue.agent.permission_policy import PermissionPolicy, PermissionRule
from sysdialogue.agent.playbook_catalog import PRODUCTION_PLAYBOOKS
from sysdialogue.agent.prompt import build_system_prompt
from sysdialogue.agent.role_agents import RoleRunner
from sysdialogue.agent.skills import SkillManager
from sysdialogue.agent.state_store import LockStore, SessionStore, TaskEventRecord, TaskStepRecord, TaskStore
from sysdialogue.agent.target_profile import TargetProfileStore
from sysdialogue.agent.trace_store import TraceStore
from sysdialogue.audit.serializers import export_replay_package
from sysdialogue.audit.trace_store import AuditLog
from sysdialogue.runtime.capability_probe import EnvProfileSanitizer
from sysdialogue.runtime.secure_runner import LocalExecutor
from sysdialogue.security.output_sanitizer import sanitize_command, sanitize_text, sanitize_value
from sysdialogue.tools.base import ToolResult
from sysdialogue.tools.registry import ToolDef, ToolRegistry


class NoLLM:
    def messages_create(self, *, system, messages, tools):
        return LLMResponse(content=[], stop_reason="stop")


class DiagnosticLLM:
    model = "diag-model"
    base_url = "https://api.example.test/v1"

    def messages_create(self, *, system, messages, tools):
        return LLMResponse(
            content=[
                {
                    "type": "tool_use",
                    "id": "call_diag",
                    "name": "diagnostic_ping",
                    "input": {"ok": True},
                }
            ],
            stop_reason="tool_use",
        )


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


def test_session_always_grant_skips_later_warn_high_tool_confirmation(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    controller.registry.register(
        ToolDef(
            name="create_user",
            fn=lambda executor, username: ToolResult(success=True, data={"username": username}),
            schema={
                "name": "create_user",
                "description": "Create user",
                "input_schema": {
                    "type": "object",
                    "properties": {"username": {"type": "string"}},
                    "required": ["username"],
                },
            },
        )
    )
    confirmations: list[str] = []
    controller.confirm_callback = lambda req: confirmations.append(req.tool) or {
        "approved": True,
        "decision": "always_this_session",
    }
    controller.bind_task("task_session_grant")
    controller.task_store.create(
        task_id="task_session_grant",
        session_id=controller.session_id,
        surface="test",
        goal="create users",
    )

    try:
        first = controller._dispatch_tool("create_user", {"username": "alice"}, "tool_1")
        second = controller._dispatch_tool("create_user", {"username": "bob"}, "tool_2")
    finally:
        controller.unbind_task()

    assert first["is_error"] is False
    assert second["is_error"] is False
    assert confirmations == ["create_user"]


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


def test_unified_output_sanitizer_redacts_nested_values_and_commands() -> None:
    private_key = "-----BEGIN OPENSSH PRIVATE KEY-----\nsecret-body\n-----END OPENSSH PRIVATE KEY-----"
    text = (
        "OPENAI_API_KEY=sk-live-secret Authorization: Bearer abc.def "
        "password=pw123\n"
        f"{private_key}"
    )
    redacted = sanitize_text(text)
    command = sanitize_command(["curl", "-H", "Authorization: Bearer abc.def", "https://example.test"])
    value = sanitize_value({"nested": {"token": "plain-secret"}, "output": text})

    assert "sk-live-secret" not in redacted
    assert "abc.def" not in redacted
    assert "pw123" not in redacted
    assert "secret-body" not in redacted
    assert "abc.def" not in command[2]
    assert "<redacted>" in command[2]
    assert value["nested"]["token"] == "<redacted>"


def test_tool_result_audit_and_replay_exports_are_sanitized(tmp_path: Path) -> None:
    result = ToolResult(
        success=True,
        data={"api_key": "sk-live-secret", "message": "Bearer abc.def"},
        cmd_trace=["curl", "-H", "Authorization: Bearer abc.def"],
    )
    audit = AuditLog(session_id="sanitize_session", log_dir=str(tmp_path / "audit"))
    audit.log_command("demo", result.cmd_trace, 0, json.dumps(result.to_dict(sanitize=False)))
    replay = export_replay_package(audit, output_dir=str(tmp_path / "exports"))
    raw_audit = audit.path.read_text(encoding="utf-8")

    assert "sk-live-secret" not in json.dumps(result.to_dict(), ensure_ascii=False)
    assert "abc.def" not in raw_audit
    with zipfile.ZipFile(replay) as zf:
        names = set(zf.namelist())
        combined = "\n".join(zf.read(name).decode("utf-8") for name in zf.namelist())
    assert "SUMMARY.md" in names
    assert "sk-live-secret" not in combined
    assert "abc.def" not in combined


def test_replay_package_includes_human_readable_summary(tmp_path: Path) -> None:
    audit = AuditLog(session_id="summary_session", log_dir=str(tmp_path / "audit"))
    audit.log_env_profile({"os": "linux", "distro": "ubuntu", "container_backend": "docker"})
    audit.log_decision(
        tool="manage_service",
        args={"name": "nginx", "action": "restart"},
        risk_level="WARN-HIGH",
        rule_ids=["WH006"],
        reason="restart Authorization: Bearer abc.def",
        decision="WARN-HIGH",
    )
    audit.log_command(
        "manage_service",
        ["systemctl", "restart", "nginx"],
        1,
        "OPENAI_API_KEY=sk-live-secret failed",
    )
    audit.log_final(final_status="failed", detail="service restart failed")

    replay = export_replay_package(audit, output_dir=str(tmp_path / "exports"))

    with zipfile.ZipFile(replay) as zf:
        summary_md = zf.read("SUMMARY.md").decode("utf-8")
        summary_json = json.loads(zf.read("summary.json").decode("utf-8"))
        combined = "\n".join(zf.read(name).decode("utf-8") for name in zf.namelist())

    assert "# SysDialogue Replay Summary" in summary_md
    assert "WARN-HIGH" in summary_md
    assert "manage_service" in summary_md
    assert "FAILED" in summary_md
    assert "service restart failed" in summary_md
    assert summary_json["risk_counts"]["WARN-HIGH"] == 1
    assert summary_json["failed_command_count"] == 1
    assert "sk-live-secret" not in combined
    assert "abc.def" not in combined


def test_slash_commands_persist_and_can_compact_memory(tmp_path: Path) -> None:
    controller = _controller(tmp_path)

    status = controller.run_turn("/status")
    compact = controller.run_turn("/compact remember nginx service context")
    memory = controller.run_turn("/memory")
    memory_id = controller.conversation_manager.context["last_compaction_memory_id"]
    forget = controller.run_turn(f"/forget {memory_id}")

    record = controller.session_store.load(controller.session_id)
    assert "Session:" in status
    assert "Compacted" in compact
    assert memory_id in memory
    assert "remember nginx" in memory
    assert f"Forgot {memory_id}" in forget
    assert record is not None
    assert [entry["role"] for entry in record.entries[-2:]] == ["user", "assistant"]


def test_slash_doctor_reports_agent_readiness(tmp_path: Path) -> None:
    controller = _controller(tmp_path)

    reply = controller.run_turn("/doctor")

    assert "SysDialogue doctor:" in reply
    assert "Safety profile:" in reply
    assert "Tools: 1 static" in reply
    assert "Actionable notices:" in reply


def test_slash_examples_are_context_aware(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    controller.env_profile["container_backend"] = "docker"
    controller.conversation_manager.context["service_name"] = "postgresql"
    controller.conversation_manager.context["container_name"] = "db"

    reply = controller.run_turn("/examples")

    assert "Example tasks:" in reply
    assert "postgresql" in reply
    assert "db" in reply
    assert "Docker/Podman" not in reply
    assert "/playbooks" in reply


def test_slash_playbooks_lists_copy_ready_workflows(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    controller.env_profile.update(
        {
            "remote_mode": True,
            "host": "prod.example.test",
            "ssh_port": 2222,
            "ssh_proxy_command_configured": True,
        }
    )

    reply = controller.run_turn("/playbooks")
    alias_reply = controller.run_turn("/workflows")
    help_reply = controller.run_turn("/help")

    assert "Production playbooks:" in reply
    assert "Target: ssh://prod.example.test:2222 via ProxyCommand" in reply
    assert "security_audit" in reply
    assert "service_restart" in reply
    assert "safe_config_patch" in reply
    assert "container_rollout" in reply
    assert "set_execution_mode(mode=\"workflow\")" in reply
    assert "Safely restart nginx" in reply
    assert alias_reply == reply
    assert "/playbooks" in help_reply


def test_production_playbooks_doc_mentions_workflow_onboarding() -> None:
    guide = Path("docs/PRODUCTION_PLAYBOOKS.md").read_text(encoding="utf-8")

    assert "/playbooks" in guide
    assert "safe_config_patch" in guide
    assert "container_rollout" in guide
    assert "via ProxyCommand" in guide


def test_production_playbook_catalog_matches_builtin_workflow_files() -> None:
    workflow_names = {path.stem for path in Path("sysdialogue/workflows").glob("*.yaml")}
    catalog_names = {entry.workflow_name for entry in PRODUCTION_PLAYBOOKS}

    assert catalog_names <= workflow_names
    assert {"security_audit", "service_restart", "safe_config_patch"} <= catalog_names


def test_slash_examples_remote_includes_setup_and_recovery(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    controller.env_profile.update(
        {
            "remote_mode": True,
            "host": "example.test",
            "ssh_port": 2222,
            "current_user": "alice",
        }
    )

    reply = controller.run_turn("/examples")

    assert "Remote setup and recovery:" in reply
    assert "sysdialogue --doctor --remote alice@example.test:2222" in reply
    assert "ssh -p 2222 alice@example.test 'uname -a'" in reply
    assert "--ssh-proxy-command" in reply
    assert "/next" in reply
    assert "/resume" in reply
    assert "/abandon" in reply
    assert "known_hosts" in reply


def test_remote_operations_guide_documents_failure_and_recovery_commands() -> None:
    guide = Path("docs/REMOTE_OPERATIONS_GUIDE.md").read_text(encoding="utf-8")

    assert "sysdialogue --doctor --remote user@example.com:22" in guide
    assert "known_hosts" in guide
    assert "SYSDIALOGUE_SSH_PASSWORD" in guide
    assert "SYSDIALOGUE_SSH_PROXY_COMMAND" in guide
    assert "--ssh-proxy-command" in guide
    assert "/next" in guide
    assert "/resume" in guide
    assert "/abandon" in guide


def test_slash_check_model_runs_tool_call_diagnostic(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    controller.llm_client = DiagnosticLLM()

    reply = controller.run_turn("/check-model")

    assert "Model tool-call diagnostic:" in reply
    assert "Status: ok" in reply
    assert "diag-model" in reply


def test_slash_next_recommends_resume_and_abandon_for_interrupted_task(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    controller.task_store.create(
        task_id="task_interrupted",
        session_id=controller.session_id,
        surface="test",
        goal="restart nginx safely",
        status="interrupted",
        current_phase="resume",
    )
    controller.session_store.set_status(
        controller.session_id,
        "interrupted",
        surface="test",
        active_task_id="task_interrupted",
        technical_details="heartbeat expired",
    )
    controller.lock_store.acquire(
        "service:nginx",
        task_id="task_interrupted",
        session_id=controller.session_id,
        surface="test",
        timeout=0.1,
    )

    next_reply = controller.run_turn("/next")
    abandon_reply = controller.run_turn("/abandon")

    assert "/resume" in next_reply
    assert "/abandon" in next_reply
    assert "restart nginx safely" in next_reply
    assert "Abandoned task_interrupted" in abandon_reply
    assert "Released 1 lock" in abandon_reply
    assert controller.task_store.load("task_interrupted").status == "cancelled"
    assert controller.session_store.load(controller.session_id).active_task_id == ""
    assert controller.lock_store.list_leases() == []


def test_slash_next_summarizes_blocked_task_advice(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    task = controller.task_store.create(
        task_id="task_blocked",
        session_id=controller.session_id,
        surface="test",
        goal="patch nginx config",
        status="blocked",
        current_phase="verify",
        iteration_budget=80,
        iteration_limit=160,
    )
    task.steps = [
        TaskStepRecord(
            step_id="validate",
            status="failed",
            tool="validate_config",
            purpose="Validate nginx config",
            error="nginx binary not found",
        ),
        TaskStepRecord(
            step_id="verify",
            status="pending",
            tool="read_file",
            purpose="Read target config after validation is available",
            last_rejected_args={"path": ""},
        ),
    ]
    task.events = [
        TaskEventRecord(
            ts="2026-05-27T00:00:00+00:00",
            stage="task_failed",
            message="blocked",
            data={"next_steps": ["Install nginx or provide the absolute nginx binary path."]},
        )
    ]
    controller.task_store.save(task)

    reply = controller.run_turn("/next")

    assert "task_blocked" in reply
    assert "Install nginx" in reply
    assert "Next pending step" in reply
    assert "Last failed step" in reply
    assert "nginx binary not found" in reply


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


def test_skill_activation_redacts_secret_arguments(tmp_path: Path) -> None:
    project_skill = tmp_path / "project" / ".sysdialogue" / "skills" / "deploy" / "SKILL.md"
    project_skill.parent.mkdir(parents=True)
    project_skill.write_text("---\nname: deploy\ndescription: deploy\n---\nDeploy safely.\n", encoding="utf-8")
    manager = SkillManager(project_root=tmp_path / "project", user_root=tmp_path / "user")

    invocation = manager.activate("deploy", {"token": "sk-live-secret"}, source="user")

    assert "sk-live-secret" not in invocation.context
    assert "<redacted>" in invocation.context


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


def test_hook_template_redacts_secret_payload_values(tmp_path: Path) -> None:
    hooks_path = tmp_path / "hooks.json"
    hooks_path.write_text(
        json.dumps(
            {
                "hooks": [
                    {"id": "ctx", "event": "task_started", "action": "inject_context", "context": "token {token}"}
                ]
            }
        ),
        encoding="utf-8",
    )
    controller = _controller(tmp_path)
    manager = HookManager(project_root=tmp_path / "project", user_path=hooks_path)

    manager.run(
        HookEvent(event="task_started", session_id=controller.session_id, payload={"token": "sk-live-secret"}),
        controller=controller,
    )

    assert "sk-live-secret" not in controller.conversation_manager.context["hook:ctx"]
    assert "<redacted>" in controller.conversation_manager.context["hook:ctx"]


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


def test_env_profile_sanitizer_keeps_remote_target_identity_without_credentials() -> None:
    sanitized = EnvProfileSanitizer.sanitize(
        {
            "remote_mode": True,
            "host": "example.test token=secret-value",
            "hostname": "prod-box",
            "ssh_port": 2222,
            "ssh_proxy_command_configured": True,
            "service_manager": "service",
            "os_release": "Ubuntu 24.04",
            "distro_id": "ubuntu",
            "kernel_version": "6.8.0",
            "architecture": "x86_64",
            "current_user": "deploy",
        }
    )

    assert sanitized["host"] == "example.test <redacted>"
    assert sanitized["hostname"] == "prod-box"
    assert sanitized["ssh_port"] == 2222
    assert sanitized["ssh_proxy_command_configured"] is True
    assert sanitized["service_manager"] == "service"
    assert "secret-value" not in str(sanitized)


def test_system_prompt_uses_env_profile_for_remote_platform_limits() -> None:
    prompt = build_system_prompt(
        {
            "remote_mode": True,
            "host": "example.test",
            "ssh_port": 2222,
            "service_manager": "unknown",
            "supports_journalctl": False,
            "has_sudo": False,
            "is_root": False,
        },
        _registry(),
    )

    assert "[Environment Operating Guidance]" in prompt
    assert "Remote target is example.test:2222" in prompt
    assert "directly reachable SSH host:port" in prompt
    assert "do not assume systemd or journalctl" in prompt
    assert "finish need_info" in prompt
    assert "service_manager=systemd" in prompt
    assert "supports_journalctl=false" in prompt
    assert "[Built-in Production Workflows]" in prompt
    assert "safe_config_patch" in prompt
    assert "scheduled_health_check" in prompt


def test_target_profile_store_redacts_secret_facts(tmp_path: Path) -> None:
    store = TargetProfileStore(str(tmp_path / "targets"))
    target_id = store.target_id_from_env({"hostname": "box"})

    store.remember_fact(target_id, "api_key", "OPENAI_API_KEY=sk-live-secret")
    raw = "\n".join(path.read_text(encoding="utf-8") for path in (tmp_path / "targets").glob("*.json"))
    summary = store.render_prompt_summary(target_id)

    assert "sk-live-secret" not in raw
    assert "sk-live-secret" not in summary
    assert "<redacted>" in raw
    assert "<redacted>" in summary


def test_memory_forget_removes_record(tmp_path: Path) -> None:
    manager = MemoryManager(str(tmp_path / "memory"))
    record = manager.remember(scope="global", key="note", value="hello", source="test")

    assert manager.forget(record.memory_id) is True
    assert manager.forget(record.memory_id) is False
    assert not manager.list_records()
