"""Task-level ReAct runtime for SysDialogue."""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from sysdialogue.tools.meta_tools import (
    META_FINISH_TASK,
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
    events: list[TaskEvent] = field(default_factory=list)
    final_status: str = ""
    final_reply: str = ""


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
        self._emit(task, "task_started", "ReAct task started", {
            "task_id": task.task_id,
            "requires_environment_feedback": task.requires_environment_feedback,
        })
        messages = self.controller.conversation_manager.prepare_turn(user_message)
        all_tools = self.controller.registry.all_schemas() + META_TOOL_SCHEMAS
        empty_action_turns = 0

        for iteration in range(self.controller.max_iterations):
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
                self._emit(task, "task_failed", "LLM call failed", {"error": str(exc)})
                self._commit_final(messages, task)
                return task.final_reply

            content = _content_as_list(response.content)
            messages.append({"role": "assistant", "content": content})
            tool_blocks = [b for b in content if _block_type(b) == "tool_use"]
            self._emit(task, "model_response", _model_response_summary(content), {
                "iteration": iteration + 1,
                "tool_count": len(tool_blocks),
            })

            if not tool_blocks:
                empty_action_turns += 1
                if empty_action_turns <= 2:
                    correction = _react_correction_message(user_message, _extract_text(content))
                    messages.append({"role": "user", "content": correction})
                    self._emit(task, "correction", "Model did not use ReAct tools; correction injected.", {
                        "attempt": empty_action_turns,
                    })
                    continue
                task.final_status = "failed"
                task.final_reply = (
                    "模型连续返回普通文本，未按 ReAct 协议调用工具或 finish_task。"
                    "请确认当前模型支持 Chat Completions tool_calls。"
                )
                self._emit(task, "task_failed", "ReAct protocol was not followed.")
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
                self._emit(task, "correction", "finish_task was mixed with other tool calls.")
                continue

            for block in tool_blocks:
                name = _block_attr(block, "name")
                args = _block_attr(block, "input") or {}
                tool_use_id = _block_attr(block, "id")
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
                    break

            messages.append({"role": "user", "content": result_blocks})
            if final_ready:
                self._commit_final(messages, task)
                return task.final_reply

        task.final_status = "failed"
        task.final_reply = "达到最大 ReAct 迭代次数，请缩小任务范围或分步重试。"
        self._emit(task, "task_failed", "Maximum ReAct iterations reached.")
        self._commit_final(messages, task)
        return task.final_reply

    def _handle_finish_task(self, task: TaskRun, args: dict, tool_use_id: str) -> dict:
        errors = _validate_finish_args(task, args)
        if errors:
            self._emit(task, "correction", "finish_task rejected by completion gate.", {
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
            {"status": status},
        )
        return _tool_result(
            tool_use_id,
            json.dumps({"success": True, "status": status}, ensure_ascii=False),
            is_error=False,
        )

    def _observe_tool_result(self, task: TaskRun, name: str, args: dict, result_block: dict) -> None:
        task.tool_steps += 1
        if name == META_SET_EXECUTION_MODE:
            mode = args.get("mode")
            if mode == "workflow":
                workflow_name = args.get("workflow_name", "")
                task.observed = True
                if workflow_name not in READ_ONLY_WORKFLOWS:
                    task.acted = True
                    task.changed_state = True
                    task.last_action_step = task.tool_steps
                if not result_block.get("is_error"):
                    task.verified = True
                    task.last_verification_step = task.tool_steps
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
            })
            return
        self._emit(task, "tool_started", f"Tool started: {name}", {"tool": name})

    def _emit_tool_finished(self, task: TaskRun, name: str, args: dict, result_block: dict) -> None:
        success = not result_block.get("is_error")
        if name == META_SET_EXECUTION_MODE and args.get("mode") == "workflow":
            self._emit(task, "workflow_finished", f"Workflow finished: {args.get('workflow_name', '')}", {
                "workflow_name": args.get("workflow_name", ""),
                "success": success,
            })
            return
        self._emit(task, "tool_finished", f"Tool finished: {name}", {
            "tool": name,
            "success": success,
        })

    def _commit_final(self, messages: list[dict], task: TaskRun) -> None:
        if task.final_reply:
            messages.append({"role": "assistant", "content": [{"type": "text", "text": task.final_reply}]})
        self.controller.conversation_manager.commit_turn(messages)

    def _emit(self, task: TaskRun, stage: str, message: str, data: dict[str, Any] | None = None) -> None:
        event = TaskEvent(stage=stage, message=message, data=data or {})
        task.events.append(event)
        self.controller._emit_task_event_obj(event)


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
    )
    if any(k in text for k in ops_keywords):
        return True
    if any(k in text for k in non_ops_keywords):
        return False
    return True


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


def _model_response_summary(content: list) -> str:
    tool_names = [_block_attr(block, "name") for block in content if _block_type(block) == "tool_use"]
    if tool_names:
        return "Model requested tools: " + ", ".join(str(name) for name in tool_names)
    text = " ".join(str(_block_attr(block, "text") or "") for block in content if _block_type(block) == "text")
    return (text[:160] if text else "Model returned no tool calls.")


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


def _block_type(block) -> str:
    if isinstance(block, dict):
        return block.get("type", "")
    return getattr(block, "type", "")


def _block_attr(block, key: str):
    if isinstance(block, dict):
        return block.get(key)
    return getattr(block, key, None)
