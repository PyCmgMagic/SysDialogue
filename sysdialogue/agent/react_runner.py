"""Task-level ReAct runtime for SysDialogue."""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from sysdialogue.tools.meta_tools import (
    META_EXECUTE_DYNAMIC_TOOL,
    META_FINISH_TASK,
    META_PROPOSE_DYNAMIC_TOOL,
    META_SET_EXECUTION_MODE,
    META_TOOL_SCHEMAS,
)

if TYPE_CHECKING:
    from sysdialogue.agent.controller import AgentController


FINAL_STATUSES = {"completed", "partial", "failed", "blocked", "need_info", "cancelled"}

READ_ONLY_TOOLS = {
    "get_system_info",
    "get_disk_usage",
    "find_files",
    "list_processes",
    "get_port_status",
    "get_network_info",
    "read_log",
    "read_file",
    "manage_package:list",
    "manage_package:search",
    "get_resource_stats",
    "list_directory",
    "stat_path",
    "search_file_content",
    "backup_path:list",
    "validate_config",
    "resolve_dns",
    "check_endpoint",
    "manage_archive:list",
    "manage_mount:list",
    "manage_container:list",
    "manage_container:status",
    "manage_container:logs",
    "manage_container:inspect",
    "manage_authorized_keys:list",
    "manage_power:noop",
    "manage_hosts_entries:list",
    "manage_service:status",
    "manage_cron:list",
    "manage_sysctl:list",
    "manage_sysctl:get",
}

VERIFICATION_TOOLS = {
    "validate_config",
    "check_endpoint",
    "get_port_status",
    "read_log",
    "get_system_info",
    "get_resource_stats",
    "manage_service:status",
    "manage_container:status",
    "manage_container:logs",
    "manage_container:inspect",
}

READ_ONLY_WORKFLOWS = {"security_audit", "disk_cleanup"}
WORKFLOWS_WITH_INTERNAL_VERIFICATION = {
    "container_rollout",
    "file_edit",
    "rollback_config",
    "safe_config_patch",
    "service_restart",
}


@dataclass
class TaskEvent:
    stage: str
    message: str
    data: dict[str, Any] = field(default_factory=dict)
    ts: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts,
            "stage": self.stage,
            "message": self.message,
            "data": self.data,
        }


@dataclass
class TaskRun:
    task_id: str
    goal: str
    requires_environment_feedback: bool
    observed: bool = False
    acted: bool = False
    verified: bool = False
    changed_state: bool = False
    tool_steps: int = 0
    last_action_step: int = 0
    last_verification_step: int = 0
    failed_mutations: list[str] = field(default_factory=list)
    events: list[TaskEvent] = field(default_factory=list)
    final_status: str = ""
    final_reply: str = ""
    correction_count: int = 0
    iteration_budget: int = 0
    iteration_limit: int = 0


class ReActRunner:
    """Drive one user turn through explicit observe/act/verify/finish steps."""

    def __init__(self, controller: "AgentController"):
        self.controller = controller

    def run(self, user_message: str) -> str:
        from sysdialogue.agent.controller import _content_as_list, _extract_text

        self.controller.clear_cancel()
        task = TaskRun(
            task_id=f"task_{uuid.uuid4().hex[:8]}",
            goal=user_message,
            requires_environment_feedback=_requires_environment_feedback(user_message),
        )
        task.iteration_limit = _clamp_iteration_limit(self.controller.max_iterations)
        task.iteration_budget = _iteration_budget(
            user_message,
            hard_limit=task.iteration_limit,
            requires_environment_feedback=task.requires_environment_feedback,
        )
        self._emit(task, "task_started", "ReAct task started", {
            "task_id": task.task_id,
            "requires_environment_feedback": task.requires_environment_feedback,
            "iteration_budget": task.iteration_budget,
            "iteration_limit": task.iteration_limit,
        })
        messages = self.controller.conversation_manager.prepare_turn(user_message)
        all_tools = self.controller.registry.all_schemas() + META_TOOL_SCHEMAS
        empty_action_turns = 0

        for iteration in range(task.iteration_budget):
            if self.controller.is_cancel_requested():
                task.final_status = "cancelled"
                task.final_reply = "当前执行已取消。"
                self._commit_final(messages, task)
                return task.final_reply

            try:
                response = self.controller.llm_client.messages_create(
                    system=self.controller._current_system_prompt(),
                    messages=messages,
                    tools=all_tools,
                )
            except Exception as exc:
                task.final_status = "failed"
                task.final_reply = (
                    f"LLM 调用失败：{exc}\n"
                    "请检查 OPENAI_API_KEY、OPENAI_BASE_URL、OPENAI_MODEL 是否正确，"
                    "以及该 OpenAI-compatible 服务是否支持 Chat Completions / tool_calls。"
                )
                self._emit(task, "task_failed", "LLM call failed", {
                    "status": "failed",
                    "error_type": type(exc).__name__,
                    "error_summary": "模型服务调用失败，任务已停止。",
                    "error_detail": str(exc),
                    "next_steps": [
                        "检查 OPENAI_API_KEY、OPENAI_BASE_URL、OPENAI_MODEL",
                        "确认 OpenAI-compatible 服务支持 Chat Completions tool_calls",
                    ],
                })
                self._commit_final(messages, task)
                return task.final_reply

            content = _content_as_list(response.content)
            messages.append({"role": "assistant", "content": content})
            tool_blocks = [b for b in content if _block_type(b) == "tool_use"]
            self._emit(task, "model_response", _model_response_summary(content), {
                "iteration": iteration + 1,
                "tool_count": len(tool_blocks),
                "tool_names": [str(_block_attr(block, "name") or "") for block in tool_blocks],
                "analysis_summary": _analysis_summary(content),
                "visible_text_preview": _visible_text_preview(content),
            })

            if not tool_blocks:
                empty_action_turns += 1
                if empty_action_turns <= 2:
                    correction = _react_correction_message(user_message, _extract_text(content))
                    messages.append({
                        "role": "user",
                        "content": correction,
                        "sysdialogue_internal": True,
                    })
                    self._emit_correction(
                        task,
                        "Model did not use ReAct tools; correction injected.",
                        {"attempt": empty_action_turns},
                    )
                    continue
                task.final_status = "failed"
                task.final_reply = (
                    "模型连续返回普通文本，未按 ReAct 协议调用工具或 finish_task。"
                    "请确认当前模型支持 Chat Completions tool_calls。"
                )
                self._emit(task, "task_failed", "ReAct protocol was not followed.", {
                    "status": "failed",
                    "error_summary": "模型未按工具协议完成任务收口。",
                    "error_detail": task.final_reply,
                    "next_steps": ["确认当前模型支持 tool_calls", "将任务拆小后重试"],
                    "correction_count": task.correction_count,
                })
                self._commit_final(messages, task)
                return task.final_reply

            empty_action_turns = 0
            result_blocks: list[dict] = []
            final_ready = False

            if any(_block_attr(block, "name") == META_FINISH_TASK for block in tool_blocks) and len(tool_blocks) > 1:
                for block in tool_blocks:
                    result_blocks.append(_tool_result(
                        _block_attr(block, "id"),
                        "finish_task must be the only tool call in the final ReAct turn.",
                        is_error=True,
                    ))
                messages.append({"role": "user", "content": result_blocks})
                self._emit_correction(task, "finish_task was mixed with other tool calls.")
                continue

            for block in tool_blocks:
                name = _block_attr(block, "name")
                args = _block_attr(block, "input") or {}
                tool_use_id = _block_attr(block, "id")
                if self.controller.is_cancel_requested():
                    result_blocks.extend(_cancelled_results_for_pending(tool_blocks, block, include_current=True))
                    break
                if name == META_FINISH_TASK:
                    result_block = self._handle_finish_task(task, args, tool_use_id)
                    result_blocks.append(result_block)
                    final_ready = not result_block.get("is_error")
                    continue

                self._emit_tool_started(task, name, args)
                result_block = self.controller._dispatch_tool(name, args, tool_use_id)
                result_blocks.append(result_block)
                self._observe_tool_result(task, name, args, result_block)
                self._emit_tool_finished(task, name, args, result_block)

                if self.controller.is_cancel_requested():
                    result_blocks.extend(_cancelled_results_for_pending(tool_blocks, block))
                    break

            messages.append({"role": "user", "content": result_blocks})
            if self.controller.is_cancel_requested():
                task.final_status = "cancelled"
                task.final_reply = "当前执行已取消。"
                self._emit(task, "task_failed", "ReAct task cancelled.", {"status": "cancelled"})
                self._commit_final(messages, task)
                return task.final_reply
            if final_ready:
                self._commit_final(messages, task)
                return task.final_reply

        task.final_status = "failed"
        task.final_reply = (
            f"已达到本任务动态 ReAct 预算（{task.iteration_budget} 轮），"
            "请缩小任务范围或分步重试。"
        )
        self._emit(task, "task_failed", "Maximum ReAct iterations reached.", {
            "status": "failed",
            "error_summary": "已达到本任务动态 ReAct 预算，任务未完成。",
            "error_detail": task.final_reply,
            "iteration_budget": task.iteration_budget,
            "iteration_limit": task.iteration_limit,
            "next_steps": ["缩小任务范围", "把目标拆成更明确的单步请求"],
        })
        self._commit_final(messages, task)
        return task.final_reply

    def _handle_finish_task(self, task: TaskRun, args: dict, tool_use_id: str) -> dict:
        errors = _validate_finish_args(task, args)
        if errors:
            self._emit_correction(task, "finish_task rejected by completion gate.", {
                "errors": errors,
            })
            return _tool_result(tool_use_id, "\n".join(errors), is_error=True)

        status = args["status"]
        task.final_status = status
        task.changed_state = task.changed_state or bool(args.get("changed_state"))
        if args.get("verification"):
            task.verified = True
            self._emit(task, "verification", args.get("verification", "Verification recorded."))
        task.final_reply = _format_final_reply(args)
        self._emit(
            task,
            "task_finished" if status in ("completed", "partial", "need_info", "blocked") else "task_failed",
            args.get("summary", ""),
            {
                "status": status,
                "summary": args.get("summary", ""),
                "evidence": args.get("evidence") or [],
                "verification": args.get("verification") or "",
                "changed_state": bool(args.get("changed_state")),
                "next_steps": args.get("next_steps") or [],
                "remaining_risks": args.get("remaining_risks") or [],
                "no_action_reason": args.get("no_action_reason") or "",
            },
        )
        return _tool_result(
            tool_use_id,
            json.dumps({"success": True, "status": status}, ensure_ascii=False),
            is_error=False,
        )

    def _observe_tool_result(self, task: TaskRun, name: str, args: dict, result_block: dict) -> None:
        task.tool_steps += 1
        success = not result_block.get("is_error")

        if name == META_SET_EXECUTION_MODE:
            mode = args.get("mode")
            if mode == "workflow":
                workflow_name = args.get("workflow_name", "")
                task.observed = True
                if success and workflow_name not in READ_ONLY_WORKFLOWS:
                    task.acted = True
                    task.changed_state = True
                    task.last_action_step = task.tool_steps
                    if workflow_name in WORKFLOWS_WITH_INTERNAL_VERIFICATION:
                        task.verified = True
                        # Synthetic sub-step: the workflow already ran its own post-change checks.
                        task.last_verification_step = task.tool_steps + 1
                if success and workflow_name in READ_ONLY_WORKFLOWS:
                    task.verified = True
                    task.last_verification_step = task.tool_steps
            return

        if name == META_PROPOSE_DYNAMIC_TOOL:
            task.observed = True
            return

        if name == META_EXECUTE_DYNAMIC_TOOL:
            payload = _parse_tool_result_json(result_block)
            changes_state = bool(payload.get("changes_state", True))
            task.observed = True
            if not success:
                if changes_state:
                    task.failed_mutations.append(_tool_action_key(name, args))
                return
            if changes_state:
                task.acted = True
                task.changed_state = True
                task.last_action_step = task.tool_steps
            return

        if not success:
            task.observed = True
            if _is_mutating_tool(name, args):
                task.failed_mutations.append(_tool_action_key(name, args))
            return

        if _is_mutating_tool(name, args):
            task.acted = True
            task.changed_state = True
            task.last_action_step = task.tool_steps
        else:
            task.observed = True

        if _is_verification_tool(name, args):
            task.verified = True
            task.last_verification_step = task.tool_steps

        if not result_block.get("is_error"):
            task.observed = True

    def _emit_tool_started(self, task: TaskRun, name: str, args: dict) -> None:
        if name == META_SET_EXECUTION_MODE and args.get("mode") == "workflow":
            self._emit(task, "workflow_started", f"Workflow started: {args.get('workflow_name', '')}", {
                "workflow_name": args.get("workflow_name", ""),
                "args_preview": _preview_json(args),
            })
            return
        self._emit(task, "tool_started", f"Tool started: {name}", {
            "tool": name,
            "args_preview": _preview_json(args),
        })

    def _emit_tool_finished(self, task: TaskRun, name: str, args: dict, result_block: dict) -> None:
        success = not result_block.get("is_error")
        if name == META_SET_EXECUTION_MODE and args.get("mode") == "workflow":
            self._emit(task, "workflow_finished", f"Workflow finished: {args.get('workflow_name', '')}", {
                "workflow_name": args.get("workflow_name", ""),
                "success": success,
                **_tool_result_display_data(result_block),
            })
            return
        self._emit(task, "tool_finished", f"Tool finished: {name}", {
            "tool": name,
            "success": success,
            **_tool_result_display_data(result_block),
        })

    def _commit_final(self, messages: list[dict], task: TaskRun) -> None:
        if task.final_reply:
            messages.append({"role": "assistant", "content": [{"type": "text", "text": task.final_reply}]})
        persisted_messages = [
            message for message in messages
            if not message.get("sysdialogue_internal")
        ]
        self.controller.conversation_manager.commit_turn(persisted_messages)

    def _emit(self, task: TaskRun, stage: str, message: str, data: dict[str, Any] | None = None) -> None:
        event = TaskEvent(stage=stage, message=message, data=data or {})
        task.events.append(event)
        self.controller._emit_task_event_obj(event)

    def _emit_correction(self, task: TaskRun, message: str, data: dict[str, Any] | None = None) -> None:
        task.correction_count += 1
        payload = {
            "display_level": "debug",
            "correction_count": task.correction_count,
            **(data or {}),
        }
        self._emit(task, "correction", message, payload)


def _requires_environment_feedback(user_message: str) -> bool:
    text = (user_message or "").strip().lower()
    if not text:
        return False

    greetings = {"你好", "您好", "hi", "hello", "hey", "在吗", "谢谢", "thanks"}
    if text in greetings:
        return False

    non_ops_keywords = (
        "怎么运行", "如何运行", "文档", "设计", "解释", "说明", "框架", "react",
        "codex", "claude", "openai", "api", "提示词", "计划", "review",
    )
    ops_keywords = (
        "检查", "查看", "状态", "启动", "停止", "重启", "reload", "restart",
        "安装", "删除", "修改", "写入", "备份", "恢复", "回滚", "验证",
        "服务", "端口", "日志", "磁盘", "内存", "cpu", "防火墙", "cron",
        "nginx", "docker", "podman", "ssh", "服务器", "/etc", "远程",
        "配置", "密钥", "安全", "审计", "权限", "环境变量", "连接",
        "key", "token", "secret", ".env",
    )
    if any(k in text for k in ops_keywords):
        return True
    if any(k in text for k in non_ops_keywords):
        return False
    return True


def _clamp_iteration_limit(limit: int) -> int:
    try:
        value = int(limit)
    except (TypeError, ValueError):
        value = 160
    return max(20, min(300, value))


def _iteration_budget(
    user_message: str,
    *,
    hard_limit: int,
    requires_environment_feedback: bool | None = None,
) -> int:
    text = (user_message or "").lower()
    limit = _clamp_iteration_limit(hard_limit)
    if requires_environment_feedback is None:
        requires_environment_feedback = _requires_environment_feedback(user_message)
    if not requires_environment_feedback:
        return min(20, limit)
    complex_markers = (
        "修改", "写入", "备份", "恢复", "回滚", "发布", "部署", "rollout",
        "workflow", "工作流", "动态工具", "dyntool", "container", "docker",
        "podman", "迁移", "升级", "多步", "验证", "修复",
    )
    if any(marker in text for marker in complex_markers):
        return min(140, limit)
    return min(80, limit)


def _validate_finish_args(task: TaskRun, args: dict) -> list[str]:
    errors: list[str] = []
    status = args.get("status")
    summary = (args.get("summary") or "").strip()
    evidence = args.get("evidence") or []
    next_steps = args.get("next_steps") or []
    no_action_reason = (args.get("no_action_reason") or "").strip()

    if status not in FINAL_STATUSES:
        errors.append(f"finish_task.status must be one of {sorted(FINAL_STATUSES)}.")
    if not summary:
        errors.append("finish_task.summary is required.")
    if status == "completed":
        if task.requires_environment_feedback and not task.observed:
            errors.append(
                "Operational tasks cannot be completed before observing the target environment. "
                "Call a read-only tool/workflow first, or finish with need_info/blocked."
            )
        if task.requires_environment_feedback and not evidence:
            errors.append("finish_task.evidence is required for completed operational tasks.")
        if task.changed_state or args.get("changed_state"):
            if not task.acted:
                errors.append("Changed-state completions require an executed mutation before completed.")
            if not task.verified or task.last_verification_step <= task.last_action_step:
                errors.append("Changed-state tasks require a verification tool/workflow after the mutation before completed.")
        if task.failed_mutations and not task.acted:
            errors.append(
                "Failed mutation attempts cannot be reported as completed before a later successful mutation and verification."
            )
    if task.requires_environment_feedback and not task.observed and status not in {"need_info", "blocked", "failed", "cancelled"}:
        errors.append("Operational tasks without observation must finish as need_info, blocked, failed, or cancelled.")
    if status in {"need_info", "blocked", "failed"} and not (next_steps or no_action_reason):
        errors.append("finish_task.next_steps or no_action_reason is required for need_info/blocked/failed.")
    if not task.requires_environment_feedback and status == "completed" and not no_action_reason and not evidence:
        errors.append("Non-operational completions must include no_action_reason or evidence.")
    return errors


def _format_final_reply(args: dict) -> str:
    parts = [args.get("summary", "").strip()]
    if args.get("verification"):
        parts.append(f"验证：{args['verification']}")
    if args.get("evidence"):
        parts.append("证据：" + "；".join(str(x) for x in args["evidence"]))
    if args.get("changed_state"):
        parts.append("状态变更：已发生受控变更。")
    if args.get("remaining_risks"):
        parts.append("剩余风险：" + "；".join(str(x) for x in args["remaining_risks"]))
    if args.get("next_steps"):
        parts.append("下一步：" + "；".join(str(x) for x in args["next_steps"]))
    if args.get("no_action_reason"):
        parts.append(f"未执行系统操作：{args['no_action_reason']}")
    return "\n".join(p for p in parts if p)


def _react_correction_message(user_message: str, assistant_text: str) -> str:
    return (
        "ReAct protocol correction: do not finish with natural language. "
        "You must either call an appropriate tool to observe/act, or call finish_task. "
        "For operational tasks, completed requires evidence from tool results. "
        f"User goal: {user_message}. Previous text: {assistant_text[:400]}"
    )


def _cancelled_results_for_pending(tool_blocks: list, current_block, *, include_current: bool = False) -> list[dict]:
    pending: list[dict] = []
    current_seen = False
    for block in tool_blocks:
        if block is current_block:
            current_seen = True
            if not include_current:
                continue
        if not current_seen:
            continue
        pending.append(
            _tool_result(
                _block_attr(block, "id"),
                "当前执行已取消，该工具未执行。",
                is_error=True,
            )
        )
    return pending


def _model_response_summary(content: list) -> str:
    tool_names = [_block_attr(block, "name") for block in content if _block_type(block) == "tool_use"]
    if tool_names:
        return "Model requested tools: " + ", ".join(str(name) for name in tool_names)
    text = " ".join(str(_block_attr(block, "text") or "") for block in content if _block_type(block) == "text")
    return (text[:160] if text else "Model returned no tool calls.")


def _analysis_summary(content: list) -> str:
    tool_names = [str(_block_attr(block, "name") or "") for block in content if _block_type(block) == "tool_use"]
    visible_text = _visible_text_preview(content)
    if tool_names:
        names = ", ".join(name for name in tool_names if name)
        if visible_text:
            return f"模型给出可见分析摘要，并选择下一步调用：{names}。"
        return f"模型选择下一步调用：{names}。"
    if visible_text:
        return "模型返回了可见文本，但还没有调用工具或 finish_task。"
    return "模型没有返回可执行动作。"


def _visible_text_preview(content: list, limit: int = 800) -> str:
    parts = [
        str(_block_attr(block, "text") or "").strip()
        for block in content
        if _block_type(block) == "text"
    ]
    text = "\n".join(part for part in parts if part)
    return _truncate(text, limit)


def _tool_result_display_data(result_block: dict) -> dict:
    content = result_block.get("content") or ""
    raw_preview = _truncate(str(content), 1800)
    parsed = _parse_tool_result_json(result_block)
    success = not result_block.get("is_error")
    output = (
        parsed.get("output")
        or parsed.get("error")
        or parsed.get("reason")
        or parsed.get("message")
        or parsed.get("data")
        or ""
    )
    if isinstance(output, (dict, list)):
        output_preview = _preview_json(output)
    else:
        output_preview = _truncate(str(output), 1000)
    error_summary = "" if success else _friendly_tool_error(parsed, raw_preview)
    return {
        "output_preview": output_preview,
        "error_summary": error_summary,
        "raw_result_preview": raw_preview,
    }


def _friendly_tool_error(parsed: dict, raw_preview: str) -> str:
    for key in ("error", "reason", "message"):
        value = parsed.get(key)
        if isinstance(value, str) and value.strip():
            return _truncate(value.strip(), 240)
    if raw_preview:
        return _truncate(raw_preview.strip(), 240)
    return "工具返回失败，但没有提供详细错误。"


def _preview_json(value, limit: int = 1000) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, indent=2, default=str)
    except Exception:
        text = str(value)
    return _truncate(text, limit)


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


def _is_mutating_tool(name: str, args: dict) -> bool:
    key = _tool_action_key(name, args)
    if key in READ_ONLY_TOOLS:
        return False
    if name in {item.split(":", 1)[0] for item in READ_ONLY_TOOLS} and key not in READ_ONLY_TOOLS:
        return True
    read_only_names = {item for item in READ_ONLY_TOOLS if ":" not in item}
    return name not in read_only_names


def _is_verification_tool(name: str, args: dict) -> bool:
    return name in VERIFICATION_TOOLS or _tool_action_key(name, args) in VERIFICATION_TOOLS


def _tool_action_key(name: str, args: dict) -> str:
    action = (args.get("action") or "").lower()
    if action:
        return f"{name}:{action}"
    if name == "get_set_system_config" and not args.get("value"):
        return "get_set_system_config:get"
    return name


def _tool_result(tool_use_id: str, content: str, *, is_error: bool) -> dict:
    return {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": content,
        "is_error": is_error,
    }


def _parse_tool_result_json(result_block: dict) -> dict:
    content = result_block.get("content") or "{}"
    if not isinstance(content, str):
        return {}
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _block_type(block) -> str:
    if isinstance(block, dict):
        return block.get("type", "")
    return getattr(block, "type", "")


def _block_attr(block, key: str):
    if isinstance(block, dict):
        return block.get(key)
    return getattr(block, key, None)
