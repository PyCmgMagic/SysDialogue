"""AgentController — agentic loop 主控，串联安全门 / 工具执行 / 审计。

设计参考 claudeplan6.md §3.2。单会话单实例，非线程安全。
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from sysdialogue.agent.conversation import ConversationManager
from sysdialogue.agent.prompt import build_system_prompt
from sysdialogue.audit.trace_store import AuditLog
from sysdialogue.runtime.capability_probe import EnvProfileSanitizer
from sysdialogue.security.approval_rules import ConfirmationRequest
from sysdialogue.security.risk_classifier import RiskDecision, classify
from sysdialogue.tools.base import ToolResult
from sysdialogue.tools.meta_tools import (
    META_EXECUTE_DYNAMIC_TOOL,
    META_FINISH_TASK,
    META_PROPOSE_DYNAMIC_TOOL,
    META_SET_EXECUTION_MODE,
)

if TYPE_CHECKING:
    from sysdialogue.agent.planner import PlanningEngine
    from sysdialogue.agent.workflow_engine import WorkflowEngine
    from sysdialogue.runtime.capability_probe import EnvProfile
    from sysdialogue.runtime.secure_runner import SafeExecutor
    from sysdialogue.tools.registry import ToolRegistry


_DEFAULT_WORKFLOWS_DIR = Path(__file__).parent.parent / "workflows"


# --------------------------------------------------------------------------
# OpenAIChatClient — OpenAI-compatible Chat Completions 同步封装
# --------------------------------------------------------------------------

@dataclass
class LLMResponse:
    content: list[dict]
    stop_reason: str


class LLMClientError(RuntimeError):
    """Raised when the configured LLM endpoint does not return a usable response."""


class OpenAIChatClient:
    """OpenAI-compatible Chat Completions wrapper using SysDialogue tool blocks."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str = "",
        max_tokens: int | None = None,
    ):
        from openai import OpenAI
        kwargs = {"api_key": api_key or os.environ.get("OPENAI_API_KEY")}
        if base_url:
            kwargs["base_url"] = base_url
        self._client = OpenAI(**kwargs)
        self.model = model
        self.base_url = base_url or ""
        self.max_tokens = max_tokens

    def messages_create(
        self,
        *,
        system: str,
        messages: list,
        tools: list[dict],
    ):
        kwargs = {
            "model": self.model,
            "messages": _to_openai_messages(system, messages),
            "tools": _to_openai_tools(tools),
            "tool_choice": "auto",
        }
        if self.max_tokens is not None:
            kwargs["max_tokens"] = self.max_tokens
        try:
            response = self._client.chat.completions.create(**kwargs)
        except Exception as exc:
            raise LLMClientError(
                f"OpenAI-compatible API 调用失败（{type(exc).__name__}）：{exc}"
            ) from exc
        return _from_openai_response(response)


# --------------------------------------------------------------------------
# AgentController
# --------------------------------------------------------------------------

@dataclass
class AgentController:
    """主控。持有 executor / env_profile / audit / registry / llm_client / confirm_callback。"""

    executor: "SafeExecutor"
    env_profile: "EnvProfile"
    audit_log: AuditLog
    registry: "ToolRegistry"
    llm_client: Any
    confirm_callback: Callable[[ConfirmationRequest], bool] = field(
        default=lambda req: False
    )
    input_callback: Callable[[str, bool], str] = field(
        default=lambda prompt, multiline: ""
    )
    event_callback: Callable[[Any], None] = field(default=lambda event: None)
    workflows_dir: Path | None = None
    dynamic_registry: Any = None  # DynamicToolRegistry instance.
    max_iterations: int = 160
    conversation_manager: ConversationManager | None = None

    # 运行时状态
    _session_counters: dict = field(default_factory=dict)
    _system_prompt: str | None = None
    _env_profile_id: str | None = None
    _workflow_engine: "WorkflowEngine | None" = None
    _planning_engine: "PlanningEngine | None" = None
    _cancel_event: threading.Event = field(default_factory=threading.Event)
    _env_sanitized: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._env_sanitized = EnvProfileSanitizer.sanitize(self.env_profile)
        self._system_prompt = build_system_prompt(
            self._env_sanitized, self.registry
        )
        self._env_profile_id = self.audit_log.log_env_profile(self._env_sanitized)
        if self.conversation_manager is None:
            self.conversation_manager = ConversationManager()

    def _get_workflow_engine(self) -> "WorkflowEngine":
        if self._workflow_engine is None:
            from sysdialogue.agent.workflow_engine import WorkflowEngine
            self._workflow_engine = WorkflowEngine(
                controller=self,
                workflows_dir=self.workflows_dir or _DEFAULT_WORKFLOWS_DIR,
                input_callback=self.input_callback,
            )
        return self._workflow_engine

    def _get_planning_engine(self) -> "PlanningEngine":
        if self._planning_engine is None:
            from sysdialogue.agent.planner import PlanningEngine
            self._planning_engine = PlanningEngine(controller=self)
        return self._planning_engine

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------

    def run_turn(self, user_message: str) -> str:
        """Run one user turn through the task-level ReAct runtime."""
        from sysdialogue.agent.react_runner import ReActRunner

        return ReActRunner(self).run(user_message)

    # ------------------------------------------------------------------
    # 工具分派
    # ------------------------------------------------------------------

    def _dispatch_tool(self, name: str, args: dict, tool_use_id: str) -> dict:
        """按名字拦截元工具；常规工具走安全门 → 执行 → 审计。"""
        # 元工具拦截
        if name == META_SET_EXECUTION_MODE:
            return self._handle_set_execution_mode(args, tool_use_id)
        if name == META_PROPOSE_DYNAMIC_TOOL:
            return self._handle_propose_dynamic_tool(args, tool_use_id)
        if name == META_EXECUTE_DYNAMIC_TOOL:
            return self._handle_execute_dynamic_tool(args, tool_use_id)
        if name == META_FINISH_TASK:
            return _tool_result_block(
                tool_use_id,
                "finish_task is handled by ReActRunner and cannot be dispatched as an OS tool.",
                is_error=True,
            )

        # 注册表校验
        if not self.registry.has(name):
            self.audit_log.log_decision(
                tool=name, args=args, risk_level="SAFE", rule_ids=[],
                reason=f"未注册工具：{name}", decision="unknown_tool",
                env_profile_id=self._env_profile_id,
            )
            return _tool_result_block(tool_use_id, f"未注册工具：{name}", is_error=True)

        # 安全门判定
        decision: RiskDecision = classify(
            name,
            args,
            self.env_profile,
            session_counters=self._session_counters,
        )
        self.audit_log.log_decision(
            tool=name, args=args, risk_level=decision.level,
            rule_ids=decision.rule_ids, reason=decision.reason,
            decision=decision.level, env_profile_id=self._env_profile_id,
        )

        if decision.level == "BLOCK":
            return _tool_result_block(
                tool_use_id,
                f"BLOCK（{', '.join(decision.rule_ids)}）：{decision.reason}",
                is_error=True,
            )

        if decision.requires_confirmation:
            self._emit_task_event(
                "confirmation_requested",
                f"{name} requires confirmation ({decision.level})",
                {
                    "tool": name,
                    "risk_level": decision.level,
                    "rule_ids": decision.rule_ids,
                    "reason": decision.reason,
                },
            )
            req = ConfirmationRequest(
                tool=name, args=args, risk=decision,
                rollback_hint=decision.rollback_hint,
            )
            try:
                ok = self.confirm_callback(req)
            except Exception as e:
                ok = False
                self.audit_log.log_decision(
                    tool=name, args=args, risk_level=decision.level,
                    rule_ids=decision.rule_ids,
                    reason=f"confirm_callback 异常：{e}",
                    decision="confirm_error",
                    env_profile_id=self._env_profile_id,
                )
            if not ok:
                self.audit_log.log_decision(
                    tool=name, args=args, risk_level=decision.level,
                    rule_ids=decision.rule_ids, reason=decision.reason,
                    decision="user_cancelled",
                    env_profile_id=self._env_profile_id,
                )
                return _tool_result_block(
                    tool_use_id, "用户已取消该操作", is_error=True,
                )

        # 执行
        result: ToolResult = self.registry.call(
            name, args,
            executor=self.executor,
            session_counters=self._session_counters,
            env_profile=self.env_profile,
        )
        if result.success:
            self.conversation_manager.observe_tool_success(name, args, result)
        self.audit_log.log_command(
            tool=name,
            cmd=result.cmd_trace,
            exit_code=result.exit_code,
            output_preview=_preview(result),
        )
        return _tool_result_block(
            tool_use_id,
            json.dumps(result.to_dict(), ensure_ascii=False),
            is_error=(not result.success),
        )

    # ------------------------------------------------------------------
    # 元工具处理（Task 10 阶段仅打桩 + 审计）
    # ------------------------------------------------------------------

    def _handle_set_execution_mode(self, args: dict, tool_use_id: str) -> dict:
        mode = args.get("mode", "direct")

        if mode == "workflow":
            wf_name = args.get("workflow_name", "") or ""
            wf_params = args.get("workflow_params") or {}
            self.audit_log.log_decision(
                tool=META_SET_EXECUTION_MODE, args=args,
                risk_level="SAFE", rule_ids=[],
                reason=f"workflow 路由：{wf_name}", decision="workflow_start",
                env_profile_id=self._env_profile_id,
            )
            try:
                engine = self._get_workflow_engine()
                execution = engine.run(wf_name, wf_params)
            except FileNotFoundError as e:
                return _tool_result_block(tool_use_id, str(e), is_error=True)
            except ValueError as e:
                return _tool_result_block(tool_use_id, f"参数错误：{e}", is_error=True)
            self.conversation_manager.observe_workflow(wf_name, wf_params, execution)
            summary = execution.summary()
            summary["message"] = execution.final_message
            return _tool_result_block(
                tool_use_id,
                json.dumps(summary, ensure_ascii=False),
                is_error=(execution.final_status in ("failed", "rollback_failed")),
            )

        if mode == "plan":
            plan_steps = args.get("plan_steps") or []
            planner = self._get_planning_engine()
            frozen = planner.freeze(plan_steps)
            self.audit_log.log_decision(
                tool=META_SET_EXECUTION_MODE, args=args,
                risk_level="SAFE", rule_ids=[],
                reason=f"plan 冻结 {frozen.plan_id}", decision="plan_frozen",
                plan_id=frozen.plan_id,
                env_profile_id=self._env_profile_id,
            )
            return _tool_result_block(
                tool_use_id,
                json.dumps(frozen.summary(), ensure_ascii=False) + "\n\n" + frozen.display_text(),
                is_error=False,
            )

        # direct 或未指定
        self.audit_log.log_decision(
            tool=META_SET_EXECUTION_MODE, args=args,
            risk_level="SAFE", rule_ids=[],
            reason=f"set_execution_mode={mode}", decision="set_execution_mode_ack",
            env_profile_id=self._env_profile_id,
        )
        return _tool_result_block(
            tool_use_id,
            "已声明 direct 模式，可直接调用工具。",
            is_error=False,
        )

    def request_cancel(self) -> None:
        self._cancel_event.set()

    def clear_cancel(self) -> None:
        self._cancel_event.clear()

    def is_cancel_requested(self) -> bool:
        return self._cancel_event.is_set()

    def _emit_task_event(self, stage: str, message: str, data: dict | None = None) -> None:
        from sysdialogue.agent.react_runner import TaskEvent

        self._emit_task_event_obj(TaskEvent(stage=stage, message=message, data=data or {}))

    def _emit_task_event_obj(self, event: Any) -> None:
        try:
            self.event_callback(event)
        except Exception:
            pass

    def _current_system_prompt(self) -> str:
        return build_system_prompt(
            self._env_sanitized,
            self.registry,
            context_summary=self.conversation_manager.render_context(),
        )

    def _handle_propose_dynamic_tool(self, args: dict, tool_use_id: str) -> dict:
        if self.dynamic_registry is None:
            self.audit_log.log_decision(
                tool=META_PROPOSE_DYNAMIC_TOOL, args=args,
                risk_level="WARN-HIGH", rule_ids=[],
                reason="DynamicToolRegistry 未注入",
                decision="propose_dynamic_tool_pending",
                env_profile_id=self._env_profile_id,
            )
            return _tool_result_block(
                tool_use_id,
                "DynTool registry is unavailable; runtime did not inject DynamicToolRegistry.",
                is_error=True,
            )
        try:
            dt = self.dynamic_registry.register(
                name=args.get("proposed_tool_name", ""),
                description=args.get("intent_summary", ""),
                cmd_template=args.get("cmd_template") or [],
                params=args.get("params") or {},
                consequences=args.get("consequences", ""),
                risk_assessment=args.get("risk_assessment", ""),
                estimated_risk=args.get("estimated_risk", "UNKNOWN"),
                changes_state=bool(args.get("changes_state", True)),
                reversible=args.get("reversible", False),
            )
        except (ValueError, RuntimeError) as e:
            self.audit_log.log_decision(
                tool=META_PROPOSE_DYNAMIC_TOOL, args=args,
                risk_level="WARN-HIGH", rule_ids=[],
                reason=f"DynTool 注册失败：{e}",
                decision="propose_dynamic_tool_rejected",
                env_profile_id=self._env_profile_id,
            )
            return _tool_result_block(tool_use_id, f"DynTool 注册失败：{e}", is_error=True)

        self.audit_log.log_decision(
            tool=META_PROPOSE_DYNAMIC_TOOL, args=args,
            risk_level="WARN-HIGH", rule_ids=[],
            reason=f"DynTool 已注册：{dt['tool_id']}",
            decision="propose_dynamic_tool_registered",
            env_profile_id=self._env_profile_id,
        )
        return _tool_result_block(
            tool_use_id,
            json.dumps({
                "tool_id": dt["tool_id"], "name": dt["name"],
                "estimated_risk": dt["estimated_risk"],
                "changes_state": dt.get("changes_state", True),
                "note": "已注册为 UNKNOWN 级；如需执行，请继续调用 execute_dynamic_tool；执行前须经 CommandSafetyChecker + 用户确认。",
            }, ensure_ascii=False),
            is_error=False,
        )

    def _handle_execute_dynamic_tool(self, args: dict, tool_use_id: str) -> dict:
        if not isinstance(args, dict):
            return _tool_result_block(
                tool_use_id,
                "execute_dynamic_tool 参数必须是对象。",
                is_error=True,
            )
        if self.dynamic_registry is None:
            self.audit_log.log_decision(
                tool=META_EXECUTE_DYNAMIC_TOOL, args=args,
                risk_level="BLOCK", rule_ids=["DYN000"],
                reason="DynamicToolRegistry 未注入",
                decision="dynamic_tool_unavailable",
                env_profile_id=self._env_profile_id,
            )
            return _tool_result_block(
                tool_use_id,
                "DynTool executor is unavailable; runtime did not inject DynamicToolRegistry.",
                is_error=True,
            )

        tool_id = args.get("tool_id", "")
        if not isinstance(tool_id, str) or not tool_id.strip():
            return _tool_result_block(
                tool_use_id,
                "execute_dynamic_tool 需要非空字符串 tool_id。",
                is_error=True,
            )
        dyn_args = args.get("args") or {}
        if not isinstance(dyn_args, dict):
            return _tool_result_block(
                tool_use_id,
                "execute_dynamic_tool.args 必须是对象。",
                is_error=True,
            )
        raw_timeout = args.get("timeout", 30)
        try:
            timeout = int(raw_timeout)
        except (TypeError, ValueError):
            return _tool_result_block(
                tool_use_id,
                "execute_dynamic_tool.timeout 必须是 1-300 之间的整数。",
                is_error=True,
            )
        if timeout < 1 or timeout > 300:
            return _tool_result_block(
                tool_use_id,
                "execute_dynamic_tool.timeout 必须是 1-300 之间的整数。",
                is_error=True,
            )

        def confirm_dynamic(payload: dict) -> bool:
            reason = (
                f"DynTool 将执行：{payload.get('tool_name')}；"
                f"命令 argv={payload.get('cmd')}；"
                f"影响：{payload.get('consequences') or '未说明'}"
            )
            decision = RiskDecision(
                level="WARN-HIGH",
                rule_ids=["DYN001"],
                reason=reason,
                requires_confirmation=True,
                rollback_hint="DynTool 无通用自动回滚；请仅批准你理解影响面的命令。",
            )
            self._emit_task_event(
                "confirmation_requested",
                f"{META_EXECUTE_DYNAMIC_TOOL} requires confirmation (WARN-HIGH)",
                {
                    "tool": META_EXECUTE_DYNAMIC_TOOL,
                    "risk_level": decision.level,
                    "rule_ids": decision.rule_ids,
                    "reason": decision.reason,
                    "dynamic_payload": payload,
                },
            )
            self.audit_log.log_decision(
                tool=META_EXECUTE_DYNAMIC_TOOL,
                args=payload,
                risk_level=decision.level,
                rule_ids=decision.rule_ids,
                reason=decision.reason,
                decision="dynamic_tool_confirmation",
                env_profile_id=self._env_profile_id,
            )
            try:
                ok = self.confirm_callback(
                    ConfirmationRequest(
                        tool=META_EXECUTE_DYNAMIC_TOOL,
                        args=payload,
                        risk=decision,
                        rollback_hint=decision.rollback_hint,
                    )
                )
            except Exception as exc:
                self.audit_log.log_decision(
                    tool=META_EXECUTE_DYNAMIC_TOOL,
                    args=payload,
                    risk_level=decision.level,
                    rule_ids=decision.rule_ids,
                    reason=f"confirm_callback 异常：{exc}",
                    decision="confirm_error",
                    env_profile_id=self._env_profile_id,
                )
                return False
            self.audit_log.log_decision(
                tool=META_EXECUTE_DYNAMIC_TOOL,
                args=payload,
                risk_level=decision.level,
                rule_ids=decision.rule_ids,
                reason=decision.reason,
                decision="user_confirmed" if ok else "user_cancelled",
                env_profile_id=self._env_profile_id,
            )
            return bool(ok)

        try:
            result = self.dynamic_registry.execute(
                tool_id,
                dyn_args,
                executor=self.executor,
                env_profile=self.env_profile,
                confirm_fn=confirm_dynamic,
                timeout=timeout,
            )
        except Exception as exc:
            self.audit_log.log_decision(
                tool=META_EXECUTE_DYNAMIC_TOOL,
                args=args,
                risk_level="BLOCK",
                rule_ids=["DYN002"],
                reason=f"DynTool 执行异常：{exc}",
                decision="dynamic_tool_error",
                env_profile_id=self._env_profile_id,
            )
            return _tool_result_block(
                tool_use_id,
                f"DynTool 执行异常：{exc}",
                is_error=True,
            )
        content = {
            "success": result.success,
            "blocked": result.blocked,
            "cancelled": result.cancelled,
            "output": result.output[:12000],
            "exit_code": result.exit_code,
            "reason": result.reason,
            "cmd": result.cmd,
            "final_risk": result.final_risk,
            "declared_changes_state": result.declared_changes_state,
            "changes_state": result.changes_state,
        }
        self.audit_log.log_command(
            tool=META_EXECUTE_DYNAMIC_TOOL,
            cmd=result.cmd,
            exit_code=result.exit_code,
            output_preview=(result.output or result.reason)[:1024],
        )
        return _tool_result_block(
            tool_use_id,
            json.dumps(content, ensure_ascii=False),
            is_error=(not result.success),
        )


# --------------------------------------------------------------------------
# 辅助函数 — 兼容内部 content block、OpenAI SDK 对象与 mock 字典结构
# --------------------------------------------------------------------------

def _object_attr(obj, key: str, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _to_openai_tools(tools: list[dict]) -> list[dict]:
    converted = []
    for tool in tools:
        converted.append(
            {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": tool.get("input_schema") or {"type": "object", "properties": {}},
                },
            }
        )
    return converted


def _to_openai_messages(system: str, messages: list[dict]) -> list[dict]:
    converted = [{"role": "system", "content": system}]
    for message in messages:
        role = message.get("role")
        content = message.get("content")
        if role == "assistant":
            converted.append(_assistant_message_to_openai(content))
            continue
        if role == "user" and _contains_tool_result_block(content):
            converted.extend(_tool_result_messages_to_openai(content))
            continue
        converted.append({"role": role, "content": _content_to_text(content)})
    return converted


def _assistant_message_to_openai(content) -> dict:
    blocks = _content_as_list(content)
    text_parts: list[str] = []
    tool_calls: list[dict] = []
    for block in blocks:
        block_type = _block_type(block)
        if block_type == "text":
            text = _block_attr(block, "text") or ""
            if text:
                text_parts.append(text)
        elif block_type == "tool_use":
            tool_calls.append(
                {
                    "id": _block_attr(block, "id"),
                    "type": "function",
                    "function": {
                        "name": _block_attr(block, "name"),
                        "arguments": json.dumps(_block_attr(block, "input") or {}, ensure_ascii=False),
                    },
                }
            )
    message: dict = {
        "role": "assistant",
        "content": "\n".join(text_parts) if text_parts else None,
    }
    if tool_calls:
        message["tool_calls"] = tool_calls
    return message


def _tool_result_messages_to_openai(content) -> list[dict]:
    messages = []
    for block in _content_as_list(content):
        if _block_type(block) != "tool_result":
            continue
        messages.append(
            {
                "role": "tool",
                "tool_call_id": _block_attr(block, "tool_use_id"),
                "content": _block_attr(block, "content") or "",
            }
        )
    return messages


def _from_openai_response(response) -> LLMResponse:
    if isinstance(response, str):
        if _looks_like_html(response):
            raise LLMClientError(
                "OpenAI-compatible API 返回了 HTML 页面，而不是 Chat Completions JSON。"
                "请确认 OPENAI_BASE_URL 指向 API 根路径，通常应类似 "
                "https://newapi.sduonline.cn/v1，而不是控制台网页首页。"
            )
        parsed = _json_loads_or_none(response)
        if parsed is not None:
            return _from_openai_response(parsed)
        return LLMResponse(
            content=[{"type": "text", "text": response}],
            stop_reason="stop",
        )

    if isinstance(response, dict) and "data" in response and "choices" not in response:
        return _from_openai_response(response["data"])

    if isinstance(response, dict) and "error" in response and "choices" not in response:
        raise LLMClientError(
            "OpenAI-compatible API 返回错误："
            f"{_response_preview(response['error'])}"
        )

    choices = _object_attr(response, "choices", None) or []
    if choices:
        choice = choices[0]
        message = _object_attr(choice, "message")
        if message is None:
            message = _object_attr(choice, "delta")
        return _from_openai_message(
            message or {},
            _object_attr(choice, "finish_reason", "stop"),
        )

    message = _object_attr(response, "message", None)
    if message is not None:
        return _from_openai_message(message, _object_attr(response, "finish_reason", "stop"))

    text = (
        _object_attr(response, "content", None)
        or _object_attr(response, "text", None)
        or _object_attr(response, "output_text", None)
    )
    if isinstance(text, str) and text:
        return LLMResponse(content=[{"type": "text", "text": text}], stop_reason="stop")

    raise LLMClientError(
        "OpenAI-compatible API 返回了无法识别的响应结构："
        f"{type(response).__name__}。响应预览：{_response_preview(response)}"
    )


def _from_openai_message(message, finish_reason: str | None = None) -> LLMResponse:
    content: list[dict] = []
    text = _object_attr(message, "content", "") or ""
    if text:
        content.append({"type": "text", "text": text})

    tool_calls = _object_attr(message, "tool_calls", None) or []
    for call in tool_calls:
        function = _object_attr(call, "function", {}) or {}
        raw_args = _object_attr(function, "arguments", "") or "{}"
        try:
            parsed_args = json.loads(raw_args)
        except json.JSONDecodeError:
            parsed_args = {}
        content.append(
            {
                "type": "tool_use",
                "id": _object_attr(call, "id"),
                "name": _object_attr(function, "name", ""),
                "input": parsed_args,
            }
        )
    stop_reason = "tool_use" if tool_calls else (finish_reason or "stop")
    return LLMResponse(content=content, stop_reason=stop_reason)


def _content_to_text(content) -> str:
    if isinstance(content, str):
        return content
    parts = []
    for block in _content_as_list(content):
        if _block_type(block) == "text":
            text = _block_attr(block, "text") or ""
            if text:
                parts.append(text)
    return "\n".join(parts)


def _contains_tool_result_block(content) -> bool:
    return any(_block_type(block) == "tool_result" for block in _content_as_list(content))


def _json_loads_or_none(value: str):
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


def _looks_like_html(value: str) -> bool:
    text = value.lstrip().lower()
    return text.startswith("<!doctype html") or text.startswith("<html")


def _response_preview(response) -> str:
    try:
        if isinstance(response, str):
            raw = response
        elif hasattr(response, "model_dump_json"):
            raw = response.model_dump_json()
        else:
            raw = json.dumps(response, ensure_ascii=False, default=str)
    except Exception:
        raw = str(response)
    return raw[:500]


def _content_as_list(content) -> list:
    if isinstance(content, list):
        return content
    return [content]


def _block_type(block) -> str:
    if isinstance(block, dict):
        return block.get("type", "")
    return getattr(block, "type", "")


def _block_attr(block, key: str):
    if isinstance(block, dict):
        return block.get(key)
    return getattr(block, key, None)


def _extract_text(content: list) -> str:
    parts: list[str] = []
    for block in content:
        if _block_type(block) == "text":
            t = _block_attr(block, "text") or ""
            if t:
                parts.append(t)
    return "\n".join(parts).strip()


def _tool_result_block(tool_use_id: str, content: str, *, is_error: bool) -> dict:
    return {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": content,
        "is_error": is_error,
    }


def _preview(result: ToolResult) -> str:
    if result.success:
        data = result.data
        if isinstance(data, (dict, list)):
            try:
                return json.dumps(data, ensure_ascii=False)[:1024]
            except Exception:
                return str(data)[:1024]
        return str(data or "")[:1024]
    return (result.error or "")[:1024]
