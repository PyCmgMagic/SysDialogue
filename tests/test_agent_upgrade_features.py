from __future__ import annotations

import json
import threading
from pathlib import Path

from sysdialogue.agent.command_registry import CommandRegistry
from sysdialogue.agent.controller import AgentController, LLMResponse
from sysdialogue.agent.memory import MemoryManager
from sysdialogue.agent.permission_policy import PermissionPolicy, PermissionRule
from sysdialogue.agent.state_store import LockStore, SessionStore, TaskStore
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


def test_web_app_exposes_command_trace_and_memory_routes() -> None:
    app = create_web_app(type("Config", (), {})())
    paths = {route.path for route in app.routes}

    assert "/api/session/{session_id}/command" in paths
    assert "/api/session/{session_id}/traces" in paths
    assert "/api/session/{session_id}/memory" in paths
