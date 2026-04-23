"""AgentController — agentic loop 主控，串联安全门 / 工具执行 / 审计。

设计参考 claudeplan6.md §3.2。单会话单实例，非线程安全。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from sysdialogue.agent.prompt import build_system_prompt
from sysdialogue.audit.trace_store import AuditLog
from sysdialogue.runtime.capability_probe import EnvProfileSanitizer
from sysdialogue.security.approval_rules import ConfirmationRequest
from sysdialogue.security.risk_classifier import RiskDecision, classify
from sysdialogue.tools.base import ToolResult
from sysdialogue.tools.meta_tools import (
    META_PROPOSE_DYNAMIC_TOOL,
    META_SET_EXECUTION_MODE,
    META_TOOL_SCHEMAS,
)

if TYPE_CHECKING:
    from sysdialogue.agent.planner import PlanningEngine
    from sysdialogue.agent.workflow_engine import WorkflowEngine
    from sysdialogue.runtime.capability_probe import EnvProfile
    from sysdialogue.runtime.secure_runner import SafeExecutor
    from sysdialogue.tools.registry import ToolRegistry


_DEFAULT_WORKFLOWS_DIR = Path(__file__).parent.parent / "workflows"


# --------------------------------------------------------------------------
# ClaudeClient — Anthropic SDK 同步封装
# --------------------------------------------------------------------------

class ClaudeClient:
    """同步 agentic loop 封装。Task 13 TUI 再加流式。"""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "claude-sonnet-4-6",
        max_tokens: int = 4096,
    ):
        from anthropic import Anthropic
        self._client = Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))
        self.model = model
        self.max_tokens = max_tokens

    def messages_create(
        self,
        *,
        system: str,
        messages: list,
        tools: list[dict],
    ):
        return self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=messages,
            tools=tools,
        )


# --------------------------------------------------------------------------
# AgentController
# --------------------------------------------------------------------------

@dataclass
class AgentController:
    """主控。持有 executor / env_profile / audit / registry / claude_client / confirm_callback。"""

    executor: "SafeExecutor"
    env_profile: "EnvProfile"
    audit_log: AuditLog
    registry: "ToolRegistry"
    claude_client: Any
    confirm_callback: Callable[[ConfirmationRequest], bool] = field(
        default=lambda req: False
    )
    input_callback: Callable[[str, bool], str] = field(
        default=lambda prompt, multiline: ""
    )
    workflows_dir: Path | None = None
    dynamic_registry: Any = None  # 可选 DynamicToolRegistry 实例（dev 模式）
    competition_mode: bool = True
    max_iterations: int = 25

    # 运行时状态
    _session_counters: dict = field(default_factory=dict)
    _system_prompt: str | None = None
    _env_profile_id: str | None = None
    _workflow_engine: "WorkflowEngine | None" = None
    _planning_engine: "PlanningEngine | None" = None

    def __post_init__(self) -> None:
        env_sanitized = EnvProfileSanitizer.sanitize(self.env_profile)
        self._system_prompt = build_system_prompt(
            env_sanitized, self.registry, self.competition_mode
        )
        self._env_profile_id = self.audit_log.log_env_profile(env_sanitized)

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
        """单轮对话：返回 assistant 最终自然语言回复。"""
        messages: list = [{"role": "user", "content": user_message}]
        all_tools = self.registry.all_schemas() + META_TOOL_SCHEMAS

        for _ in range(self.max_iterations):
            response = self.claude_client.messages_create(
                system=self._system_prompt,
                messages=messages,
                tools=all_tools,
            )
            content = _content_as_list(response.content)
            messages.append({"role": "assistant", "content": content})

            stop_reason = getattr(response, "stop_reason", None)
            if stop_reason != "tool_use":
                return _extract_text(content)

            tool_result_blocks = []
            for block in content:
                if _block_type(block) != "tool_use":
                    continue
                name = _block_attr(block, "name")
                args = _block_attr(block, "input") or {}
                tool_use_id = _block_attr(block, "id")
                result_block = self._dispatch_tool(name, args, tool_use_id)
                tool_result_blocks.append(result_block)

            messages.append({"role": "user", "content": tool_result_blocks})

        return "（达到最大迭代次数，请分步重试或精简需求）"

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

        # 注册表校验
        if not self.registry.has(name):
            self.audit_log.log_decision(
                tool=name, args=args, risk_level="SAFE", rule_ids=[],
                reason=f"未注册工具：{name}", decision="unknown_tool",
                env_profile_id=self._env_profile_id,
            )
            return _tool_result_block(tool_use_id, f"未注册工具：{name}", is_error=True)

        # 安全门判定
        decision: RiskDecision = classify(name, args, self.env_profile)
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

    def _handle_propose_dynamic_tool(self, args: dict, tool_use_id: str) -> dict:
        if self.competition_mode:
            self.audit_log.log_decision(
                tool=META_PROPOSE_DYNAMIC_TOOL, args=args,
                risk_level="BLOCK", rule_ids=[],
                reason="竞赛模式关闭 propose_dynamic_tool",
                decision="BLOCK", env_profile_id=self._env_profile_id,
            )
            return _tool_result_block(
                tool_use_id,
                "竞赛模式下 propose_dynamic_tool 已关闭；请使用 37 个静态工具或内置 workflow 完成任务。",
                is_error=True,
            )

        # 开发模式：有 dynamic_registry 则注册提案，否则只记录不执行
        if self.dynamic_registry is None:
            self.audit_log.log_decision(
                tool=META_PROPOSE_DYNAMIC_TOOL, args=args,
                risk_level="WARN-HIGH", rule_ids=[],
                reason="dev 模式但未注入 dynamic_registry",
                decision="propose_dynamic_tool_pending",
                env_profile_id=self._env_profile_id,
            )
            return _tool_result_block(
                tool_use_id,
                "DynTool 提案已记录但 DynamicToolRegistry 未注入，无法执行。",
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
                "note": "已注册为 UNKNOWN 级；执行前须经 CommandSafetyChecker + 用户确认。",
            }, ensure_ascii=False),
            is_error=False,
        )


# --------------------------------------------------------------------------
# 辅助函数 — 兼容 Anthropic SDK content block 与 mock 字典结构
# --------------------------------------------------------------------------

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
