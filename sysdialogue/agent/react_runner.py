"""Task-level ReAct runtime for SysDialogue."""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from sysdialogue.agent.state_store import TaskRecord, TaskStepRecord
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
RESUME_KEYWORDS = ("continue", "resume", "继续", "接着", "继续上次", "继续任务", "重试上次")


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
    mode: str = "direct"
    plan_id: str = ""
    steps: list[TaskStepRecord] = field(default_factory=list)
    current_phase: str = "analysis"
    resumed: bool = False
    resume_message: str = ""
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
    technical_details: str = ""


class ReActRunner:
    """Drive one user turn through explicit observe/act/verify/finish steps."""

    def __init__(self, controller: "AgentController"):
        self.controller = controller

    def run(self, user_message: str) -> str:
        from sysdialogue.agent.controller import _content_as_list, _extract_text

        self.controller.clear_cancel()
        session_store = self.controller.session_store
        task_store = self.controller.task_store
        if session_store is not None and task_store is not None:
            session_store.recover_interrupted(
                self.controller.session_id,
                task_store,
                surface=self.controller.surface,
            )

        task = self._load_or_create_task(user_message)
        self.controller.bind_task(task.task_id)
        self._persist_task_state(task, status="running", current_phase=task.current_phase)

        if session_store is not None:
            session_store.append_user_turn(
                self.controller.session_id,
                user_message,
                surface=self.controller.surface,
                active_task_id=task.task_id,
            )

        try:
            self._emit(
                task,
                "task_started",
                "ReAct task started",
                {
                    "task_id": task.task_id,
                    "requires_environment_feedback": task.requires_environment_feedback,
                    "iteration_budget": task.iteration_budget,
                    "iteration_limit": task.iteration_limit,
                    "resumed": task.resumed,
                    "resume_message": task.resume_message,
                },
            )
            messages = self._prepare_messages(task, user_message)
            all_tools = self.controller.registry.all_schemas() + META_TOOL_SCHEMAS
            empty_action_turns = 0

            for iteration in range(task.iteration_budget):
                if self.controller.is_cancel_requested():
                    task.final_status = "cancelled"
                    task.final_reply = "当前执行已取消。"
                    self._emit(task, "task_failed", "ReAct task cancelled.", {"status": "cancelled"})
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
                    task.technical_details = str(exc)
                    task.final_reply = (
                        f"LLM 调用失败：{exc}\n"
                        "请检查 OPENAI_API_KEY、OPENAI_BASE_URL、OPENAI_MODEL，"
                        "以及当前 OpenAI-compatible 服务是否支持 Chat Completions / tool_calls。"
                    )
                    self._emit(
                        task,
                        "task_failed",
                        "LLM call failed",
                        {
                            "status": "failed",
                            "error_type": type(exc).__name__,
                            "error_summary": "模型服务调用失败，任务已停止。",
                            "error_detail": str(exc),
                            "next_steps": [
                                "检查 OPENAI_API_KEY、OPENAI_BASE_URL、OPENAI_MODEL",
                                "确认当前模型支持 Chat Completions tool_calls",
                            ],
                            "technical_details": str(exc),
                        },
                    )
                    self._commit_final(messages, task)
                    return task.final_reply

                content = _content_as_list(response.content)
                messages.append({"role": "assistant", "content": content})
                tool_blocks = [block for block in content if _block_type(block) == "tool_use"]
                self._emit(
                    task,
                    "model_response",
                    _model_response_summary(content),
                    {
                        "iteration": iteration + 1,
                        "tool_count": len(tool_blocks),
                        "tool_names": [str(_block_attr(block, "name") or "") for block in tool_blocks],
                        "analysis_summary": _analysis_summary(content),
                        "visible_text_preview": _visible_text_preview(content),
                    },
                )

                if not tool_blocks:
                    empty_action_turns += 1
                    if empty_action_turns <= 2:
                        correction = _react_correction_message(user_message, _extract_text(content))
                        messages.append(
                            {
                                "role": "user",
                                "content": correction,
                                "sysdialogue_internal": True,
                            }
                        )
                        self._emit_correction(
                            task,
                            "Model did not use ReAct tools; correction injected.",
                            {"attempt": empty_action_turns},
                        )
                        continue

                    task.final_status = "failed"
                    task.final_reply = (
                        "模型连续返回普通文本，未按 ReAct 协议调用工具或 finish_task。\n"
                        "请确认当前模型支持 Chat Completions tool_calls。"
                    )
                    self._emit(
                        task,
                        "task_failed",
                        "ReAct protocol was not followed.",
                        {
                            "status": "failed",
                            "error_summary": "模型未按工具协议完成任务收口。",
                            "error_detail": task.final_reply,
                            "next_steps": ["确认当前模型支持 tool_calls", "将任务拆小后重试"],
                            "correction_count": task.correction_count,
                        },
                    )
                    self._commit_final(messages, task)
                    return task.final_reply

                empty_action_turns = 0
                result_blocks: list[dict[str, Any]] = []
                final_ready = False

                if any(_block_attr(block, "name") == META_FINISH_TASK for block in tool_blocks) and len(tool_blocks) > 1:
                    for block in tool_blocks:
                        result_blocks.append(
                            _tool_result(
                                _block_attr(block, "id"),
                                "finish_task must be the only tool call in the final ReAct turn.",
                                is_error=True,
                            )
                        )
                    messages.append({"role": "user", "content": result_blocks})
                    self._emit_correction(task, "finish_task was mixed with other tool calls.")
                    continue

                for block in tool_blocks:
                    if self.controller.is_cancel_requested():
                        result_blocks.extend(_cancelled_results_for_pending(tool_blocks, block, include_current=True))
                        break

                    name = _block_attr(block, "name")
                    args = _block_attr(block, "input") or {}
                    tool_use_id = _block_attr(block, "id")

                    if name == META_FINISH_TASK:
                        result_block = self._handle_finish_task(task, args, tool_use_id)
                        result_blocks.append(result_block)
                        final_ready = not result_block.get("is_error")
                        continue

                    plan_error = self._guard_plan_step(task, name, args)
                    if plan_error:
                        result_blocks.append(_tool_result(tool_use_id, plan_error, is_error=True))
                        self._emit_correction(task, "Planned step deviation rejected.", {"errors": [plan_error]})
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
                f"已达到本任务动态 ReAct 预算（{task.iteration_budget} 轮），任务仍未完成。\n"
                "请缩小任务范围或分步骤重试。"
            )
            self._emit(
                task,
                "task_failed",
                "Maximum ReAct iterations reached.",
                {
                    "status": "failed",
                    "error_summary": "已达到本任务动态 ReAct 预算，任务未完成。",
                    "error_detail": task.final_reply,
                    "iteration_budget": task.iteration_budget,
                    "iteration_limit": task.iteration_limit,
                    "next_steps": ["缩小任务范围", "把目标拆成更明确的单步请求"],
                },
            )
            self._commit_final(messages, task)
            return task.final_reply
        finally:
            self.controller.unbind_task()

    def _load_or_create_task(self, user_message: str) -> TaskRun:
        session_store = self.controller.session_store
        task_store = self.controller.task_store
        session_id = self.controller.session_id
        hard_limit = _clamp_iteration_limit(self.controller.max_iterations)
        requires_environment_feedback = _requires_environment_feedback(user_message)

        session_record = (
            session_store.ensure(session_id, surface=self.controller.surface)
            if session_store is not None
            else None
        )
        forced_resume_task_id = self.controller.consume_forced_resume_task_id()
        if task_store is not None and forced_resume_task_id:
            existing = task_store.load(forced_resume_task_id)
            if existing is not None and existing.status == "interrupted":
                task_store.update(
                    existing.task_id,
                    status="running",
                    current_phase=existing.current_phase or "resume",
                    resume_message=(
                        existing.resume_message
                        or "Previous task was explicitly resumed by the user."
                    ),
                    heartbeat_ts=datetime.now(timezone.utc).isoformat(),
                )
                if session_store is not None:
                    session_store.set_status(
                        session_id,
                        "running",
                        surface=self.controller.surface,
                        active_task_id=existing.task_id,
                    )
                existing = task_store.load(existing.task_id) or existing
                return _task_run_from_record(existing, resumed=True)

        active_task_id = session_record.active_task_id if session_record is not None else ""
        if task_store is not None and active_task_id:
            existing = task_store.load(active_task_id)
            if existing is not None and existing.status == "interrupted" and _looks_like_resume(user_message, existing.goal):
                task_store.update(
                    existing.task_id,
                    status="running",
                    current_phase=existing.current_phase or "resume",
                    resume_message=existing.resume_message or "Previous task was interrupted. Continue from the last durable step.",
                    heartbeat_ts=datetime.now(timezone.utc).isoformat(),
                )
                existing = task_store.load(existing.task_id) or existing
                return _task_run_from_record(existing, resumed=True)

        task_id = f"task_{uuid.uuid4().hex[:8]}"
        budget = _iteration_budget(
            user_message,
            hard_limit=hard_limit,
            requires_environment_feedback=requires_environment_feedback,
        )
        if task_store is not None:
            record = task_store.create(
                task_id=task_id,
                session_id=session_id,
                surface=self.controller.surface,
                goal=user_message,
                mode="direct",
                status="running",
                current_phase="analysis",
                iteration_budget=budget,
                iteration_limit=hard_limit,
            )
            return _task_run_from_record(record)
        return TaskRun(
            task_id=task_id,
            goal=user_message,
            requires_environment_feedback=requires_environment_feedback,
            iteration_budget=budget,
            iteration_limit=hard_limit,
        )

    def _prepare_messages(self, task: TaskRun, user_message: str) -> list[dict[str, Any]]:
        messages = self.controller.conversation_manager.prepare_turn(user_message)
        if task.resumed and task.resume_message:
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "Resume context: continue the previous interrupted task if still applicable. "
                        f"Task goal: {task.goal}. Resume note: {task.resume_message}"
                    ),
                    "sysdialogue_internal": True,
                }
            )
        if task.mode == "plan" and task.steps:
            executable_steps = _executable_plan_steps(task.steps)
            if executable_steps:
                frontier = [
                    {
                        "step_id": step.step_id,
                        "tool": step.tool,
                        "args": step.args,
                        "purpose": step.purpose,
                        "finding_id": step.finding_id,
                        "severity": step.severity,
                    }
                    for step in executable_steps
                ]
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Frozen plan is active. You must follow one executable plan step exactly. "
                            f"plan_id={task.plan_id or '<unknown>'}; "
                            f"frontier={json.dumps(frontier, ensure_ascii=False)}"
                        ),
                        "sysdialogue_internal": True,
                    }
                )
        return messages

    def _persist_task_state(
        self,
        task: TaskRun,
        *,
        status: str | None = None,
        current_phase: str | None = None,
        technical_details: str | None = None,
    ) -> None:
        if self.controller.task_store is None:
            return
        changes: dict[str, Any] = {
            "mode": task.mode,
            "plan_id": task.plan_id,
            "current_phase": current_phase or task.current_phase,
            "observed": task.observed,
            "acted": task.acted,
            "verified": task.verified,
            "changed_state": task.changed_state,
            "tool_steps": task.tool_steps,
            "last_action_step": task.last_action_step,
            "last_verification_step": task.last_verification_step,
            "failed_mutations": list(task.failed_mutations),
            "resume_message": task.resume_message,
        }
        if status is not None:
            changes["status"] = status
        if technical_details is not None:
            changes["technical_details"] = technical_details
        try:
            self.controller.task_store.update(task.task_id, **changes)
            self.controller.task_store.set_steps(task.task_id, list(task.steps))
        except FileNotFoundError:
            pass

    def _guard_plan_step(self, task: TaskRun, name: str, args: dict[str, Any]) -> str | None:
        if task.mode != "plan":
            return None
        executable_steps = _executable_plan_steps(task.steps)
        if not executable_steps:
            return "All frozen plan steps are already completed. Finish with finish_task or create a new plan."
        matching = [
            step
            for step in executable_steps
            if step.tool == name and _normalized_json(step.args) == _normalized_json(args)
        ]
        if not matching:
            expected = ", ".join(f"{step.step_id}:{step.tool}" for step in executable_steps)
            return (
                "Frozen plan deviation rejected. "
                f"Executable frontier is [{expected}], but the model tried {name} with non-matching args."
            )
        next_step = matching[0]
        next_step.status = "running"
        next_step.updated_at = datetime.now(timezone.utc).isoformat()
        self._persist_task_state(task)
        return None

    def _handle_finish_task(self, task: TaskRun, args: dict, tool_use_id: str) -> dict:
        errors = _validate_finish_args(task, args)
        if errors:
            self._emit_correction(task, "finish_task rejected by completion gate.", {"errors": errors})
            return _tool_result(tool_use_id, "\n".join(errors), is_error=True)

        status = args["status"]
        task.final_status = status
        task.changed_state = task.changed_state or bool(args.get("changed_state"))
        task.current_phase = "finish"
        if args.get("verification"):
            task.verified = True
            self._emit(task, "verification", args.get("verification", "Verification recorded."))
        task.final_reply = _format_final_reply(args)
        self._emit(
            task,
            "task_finished" if status in {"completed", "partial", "need_info", "blocked"} else "task_failed",
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
        payload = _parse_tool_result_json(result_block)

        if name == META_SET_EXECUTION_MODE:
            mode = args.get("mode")
            task.observed = True
            if mode == "plan" and success and self.controller.task_store is not None:
                record = self.controller.task_store.load(task.task_id)
                if record is not None:
                    task.mode = record.mode
                    task.plan_id = record.plan_id
                    task.steps = list(record.steps)
                    task.current_phase = "plan"
            elif mode == "workflow":
                task.mode = "workflow"
                workflow_name = args.get("workflow_name", "")
                if success and workflow_name not in READ_ONLY_WORKFLOWS:
                    task.acted = True
                    task.changed_state = True
                    task.last_action_step = task.tool_steps
                    if workflow_name in WORKFLOWS_WITH_INTERNAL_VERIFICATION:
                        task.verified = True
                        task.last_verification_step = task.tool_steps + 1
                if success and workflow_name in READ_ONLY_WORKFLOWS:
                    task.verified = True
                    task.last_verification_step = task.tool_steps
                if self.controller.task_store is not None:
                    record = self.controller.task_store.load(task.task_id)
                    if record is not None:
                        task.steps = list(record.steps)
            else:
                task.mode = "direct"
            self._persist_task_state(task)
            return

        if name == META_PROPOSE_DYNAMIC_TOOL:
            task.observed = True
            task.current_phase = "observe"
            self._persist_task_state(task)
            return

        if name == META_EXECUTE_DYNAMIC_TOOL:
            changes_state = bool(payload.get("changes_state", True))
            task.observed = True
            if not success:
                if changes_state:
                    task.failed_mutations.append(_tool_action_key(name, args))
                self._update_plan_step(task, name, args, success=False, error=result_block.get("content", ""))
                self._persist_task_state(task)
                return
            if changes_state:
                task.acted = True
                task.changed_state = True
                task.last_action_step = task.tool_steps
                task.current_phase = "act"
            else:
                task.current_phase = "observe"
            self._update_plan_step(task, name, args, success=True)
            self._persist_task_state(task)
            return

        if not success:
            task.observed = True
            task.current_phase = "observe"
            if _is_mutating_tool(name, args):
                task.failed_mutations.append(_tool_action_key(name, args))
            self._update_plan_step(task, name, args, success=False, error=result_block.get("content", ""))
            self._persist_task_state(task)
            return

        if _is_mutating_tool(name, args):
            task.acted = True
            task.changed_state = True
            task.last_action_step = task.tool_steps
            task.current_phase = "act"
        else:
            task.observed = True
            task.current_phase = "observe"

        if _is_verification_tool(name, args):
            task.verified = True
            task.last_verification_step = task.tool_steps
            task.current_phase = "verify"

        task.observed = True
        self._update_plan_step(task, name, args, success=True)
        self._persist_task_state(task)

    def _update_plan_step(self, task: TaskRun, name: str, args: dict, *, success: bool, error: str = "") -> None:
        if task.mode != "plan":
            return
        next_step = _next_step_for_tool(task.steps, name, args)
        if next_step is None:
            return
        next_step.status = "completed" if success else "failed"
        next_step.error = _truncate(str(error), 600) if error else ""
        next_step.updated_at = datetime.now(timezone.utc).isoformat()
        if success:
            task.current_phase = "act"

    def _emit_tool_started(self, task: TaskRun, name: str, args: dict) -> None:
        if name == META_SET_EXECUTION_MODE and args.get("mode") == "workflow":
            self._emit(
                task,
                "workflow_started",
                f"Workflow started: {args.get('workflow_name', '')}",
                {
                    "workflow_name": args.get("workflow_name", ""),
                    "args_preview": _preview_json(args),
                },
            )
            return
        self._emit(
            task,
            "tool_started",
            f"Tool started: {name}",
            {
                "tool": name,
                "args_preview": _preview_json(args),
            },
        )

    def _emit_tool_finished(self, task: TaskRun, name: str, args: dict, result_block: dict) -> None:
        success = not result_block.get("is_error")
        if name == META_SET_EXECUTION_MODE and args.get("mode") == "workflow":
            self._emit(
                task,
                "workflow_finished",
                f"Workflow finished: {args.get('workflow_name', '')}",
                {
                    "workflow_name": args.get("workflow_name", ""),
                    "success": success,
                    **_tool_result_display_data(result_block),
                },
            )
            return
        self._emit(
            task,
            "tool_finished",
            f"Tool finished: {name}",
            {
                "tool": name,
                "success": success,
                **_tool_result_display_data(result_block),
            },
        )

    def _commit_final(self, messages: list[dict], task: TaskRun) -> None:
        if task.final_reply:
            messages.append({"role": "assistant", "content": [{"type": "text", "text": task.final_reply}]})
        persisted_messages = [message for message in messages if not message.get("sysdialogue_internal")]
        self.controller.conversation_manager.commit_turn(persisted_messages)

        if self.controller.session_store is not None:
            self.controller.session_store.sync_manager(
                self.controller.session_id,
                self.controller.conversation_manager,
                surface=self.controller.surface,
            )
            entry_role = "assistant" if task.final_status != "failed" else "error"
            if task.final_reply:
                self.controller.session_store.append_entry(
                    self.controller.session_id,
                    entry_role,
                    task.final_reply,
                    surface=self.controller.surface,
                    technical_details=task.technical_details,
                )
            self.controller.session_store.set_status(
                self.controller.session_id,
                task.final_status or "completed",
                surface=self.controller.surface,
                active_task_id="",
                technical_details=task.technical_details,
            )

        final_status = _final_task_store_status(task.final_status)
        self._persist_task_state(
            task,
            status=final_status,
            current_phase="finish",
            technical_details=task.technical_details,
        )

    def _emit(self, task: TaskRun, stage: str, message: str, data: dict[str, Any] | None = None) -> None:
        payload = data or {}
        event = TaskEvent(stage=stage, message=message, data=payload)
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


def _task_run_from_record(record: TaskRecord, *, resumed: bool = False) -> TaskRun:
    return TaskRun(
        task_id=record.task_id,
        goal=record.goal,
        requires_environment_feedback=_requires_environment_feedback(record.goal),
        mode=record.mode or "direct",
        plan_id=record.plan_id or "",
        steps=list(record.steps),
        current_phase=record.current_phase or "analysis",
        resumed=resumed,
        resume_message=record.resume_message or "",
        observed=bool(record.observed),
        acted=bool(record.acted),
        verified=bool(record.verified),
        changed_state=bool(record.changed_state),
        tool_steps=int(record.tool_steps or 0),
        last_action_step=int(record.last_action_step or 0),
        last_verification_step=int(record.last_verification_step or 0),
        failed_mutations=list(record.failed_mutations or []),
        iteration_budget=int(record.iteration_budget or 0),
        iteration_limit=int(record.iteration_limit or 0),
        technical_details=record.technical_details or "",
    )


def _next_pending_step(steps: list[TaskStepRecord]) -> TaskStepRecord | None:
    for step in steps:
        if step.status in {"pending", "running"}:
            return step
    return None


def _executable_plan_steps(steps: list[TaskStepRecord]) -> list[TaskStepRecord]:
    completed = {
        step.step_id
        for step in steps
        if step.status in {"completed", "skipped", "rolled_back"}
    }
    executable: list[TaskStepRecord] = []
    for step in steps:
        if step.status not in {"pending", "running"}:
            continue
        if all(dep in completed for dep in (step.depends_on or [])):
            executable.append(step)
    return executable


def _next_step_for_tool(steps: list[TaskStepRecord], name: str, args: dict) -> TaskStepRecord | None:
    for step in steps:
        if step.status not in {"pending", "running"}:
            continue
        if step.tool != name:
            return None
        if _normalized_json(step.args) != _normalized_json(args):
            return None
        return step
    return None


def _normalized_json(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    except Exception:
        return str(value)


def _looks_like_resume(user_message: str, goal: str) -> bool:
    text = (user_message or "").strip().lower()
    if not text:
        return False
    if any(keyword in text for keyword in RESUME_KEYWORDS):
        return True
    goal_text = (goal or "").strip().lower()
    return bool(goal_text and text == goal_text)


def _final_task_store_status(status: str) -> str:
    return {
        "completed": "completed",
        "partial": "completed",
        "failed": "failed",
        "blocked": "blocked",
        "need_info": "blocked",
        "cancelled": "cancelled",
    }.get(status, status or "completed")


def _requires_environment_feedback(user_message: str) -> bool:
    text = (user_message or "").strip().lower()
    if not text:
        return False

    greetings = {"你好", "您好", "hi", "hello", "hey", "在吗", "谢谢", "thanks"}
    if text in greetings:
        return False

    non_ops_keywords = (
        "怎么运行",
        "如何运行",
        "文档",
        "设计",
        "解释",
        "说明",
        "框架",
        "react",
        "codex",
        "claude",
        "openai",
        "提示词",
        "计划",
        "review",
    )
    ops_keywords = (
        "检查",
        "查看",
        "状态",
        "启动",
        "停止",
        "重启",
        "reload",
        "restart",
        "安装",
        "删除",
        "修改",
        "写入",
        "备份",
        "恢复",
        "回滚",
        "验证",
        "服务",
        "端口",
        "日志",
        "磁盘",
        "内存",
        "cpu",
        "防火墙",
        "cron",
        "nginx",
        "docker",
        "podman",
        "ssh",
        "服务器",
        "/etc",
        "远程",
        "配置",
        "密钥",
        "安全",
        "审计",
        "权限",
        "环境变量",
        "连接",
        "key",
        "token",
        "secret",
        ".env",
    )
    if any(keyword in text for keyword in ops_keywords):
        return True
    if any(keyword in text for keyword in non_ops_keywords):
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
        "修改",
        "写入",
        "备份",
        "恢复",
        "回滚",
        "发布",
        "部署",
        "rollout",
        "workflow",
        "工作流",
        "动态工具",
        "dyntool",
        "container",
        "docker",
        "podman",
        "迁移",
        "升级",
        "多步",
        "验证",
        "修复",
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
        if task.mode == "plan":
            incomplete = [step.step_id for step in task.steps if step.status not in {"completed", "skipped", "rolled_back"}]
            if incomplete:
                errors.append(
                    "Frozen plan tasks cannot be completed before all planned steps finish. "
                    f"Remaining steps: {', '.join(incomplete)}."
                )
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
        parts.append("证据：" + "；".join(str(item) for item in args["evidence"]))
    if args.get("changed_state"):
        parts.append("状态变更：已发生受控变更。")
    if args.get("remaining_risks"):
        parts.append("剩余风险：" + "；".join(str(item) for item in args["remaining_risks"]))
    if args.get("next_steps"):
        parts.append("下一步：" + "；".join(str(item) for item in args["next_steps"]))
    if args.get("no_action_reason"):
        parts.append(f"未执行系统操作：{args['no_action_reason']}")
    return "\n".join(part for part in parts if part)


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
    return text[:160] if text else "Model returned no tool calls."


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
    return text[: max(0, limit - 3)] + "..."


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
    if not isinstance(args, dict):
        return name
    action = (args.get("action") or "").lower()
    if action:
        return f"{name}:{action}"
    if name == "get_set_system_config" and ("value" not in args or args.get("value") is None):
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
