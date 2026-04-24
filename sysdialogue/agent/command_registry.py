"""Slash command layer shared by TUI, Web, and Simple CLI."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class CommandResult:
    output: str
    resume_task_id: str = ""
    resume_goal: str = ""


class CommandRegistry:
    """Small command dispatcher for control-plane operations."""

    def execute(self, controller: Any, text: str) -> CommandResult:
        command, arg = _split_command(text)
        if command in {"/help", "/commands"}:
            return CommandResult(_help_text())
        if command == "/status":
            return CommandResult(_status(controller))
        if command == "/resume":
            return _resume(controller)
        if command == "/locks":
            return CommandResult(_locks(controller))
        if command == "/plan":
            return CommandResult(_active_task_steps(controller))
        if command == "/audit":
            return CommandResult(_audit(controller))
        if command == "/memory":
            return CommandResult(_memory(controller, arg))
        if command == "/tools":
            return CommandResult(_tools(controller))
        if command == "/permissions":
            return CommandResult(_permissions(controller))
        if command == "/compact":
            return CommandResult(_compact(controller, arg))
        return CommandResult(f"Unknown command: {command}\n\n{_help_text()}")


def _split_command(text: str) -> tuple[str, str]:
    stripped = (text or "").strip()
    if not stripped.startswith("/"):
        return "", stripped
    parts = stripped.split(maxsplit=1)
    return parts[0].lower(), parts[1] if len(parts) > 1 else ""


def _help_text() -> str:
    return "\n".join(
        [
            "Available commands:",
            "- /status: show session and active task state",
            "- /resume: explicitly resume the interrupted active task",
            "- /locks: show current lock leases",
            "- /plan: show active durable plan/workflow steps",
            "- /audit: show recent audit records",
            "- /memory [text]: list memory or remember a note",
            "- /tools: show static and reusable dynamic tools",
            "- /permissions: show active permission policy summary",
            "- /compact [summary]: compact current context into memory",
        ]
    )


def _status(controller: Any) -> str:
    record = controller.session_store.ensure(controller.session_id, surface=controller.surface)
    task = controller.task_store.load(record.active_task_id) if record.active_task_id else None
    lines = [
        f"Session: {record.session_id}",
        f"Surface: {record.surface}",
        f"Status: {record.status}",
    ]
    if task is not None:
        lines.extend(
            [
                f"Task: {task.task_id}",
                f"Task status: {task.status}",
                f"Mode: {task.mode}",
                f"Phase: {task.current_phase}",
                f"Goal: {task.goal}",
            ]
        )
    else:
        lines.append("Task: none")
    return "\n".join(lines)


def _resume(controller: Any) -> CommandResult:
    record = controller.session_store.ensure(controller.session_id, surface=controller.surface)
    if not record.active_task_id:
        return CommandResult("No interrupted active task is available for this session.")
    task = controller.task_store.load(record.active_task_id)
    if task is None or task.status != "interrupted":
        return CommandResult("No interrupted active task is available for this session.")
    return CommandResult(
        f"Resuming task {task.task_id}: {task.goal}",
        resume_task_id=task.task_id,
        resume_goal=task.goal,
    )


def _locks(controller: Any) -> str:
    lock_store = getattr(controller, "lock_store", None)
    if lock_store is None:
        return "LockStore is unavailable."
    leases = lock_store.list_leases() if hasattr(lock_store, "list_leases") else []
    if not leases:
        return "No active lock leases."
    return "\n".join(f"- {lease.scope}: task={lease.task_id} surface={lease.surface}" for lease in leases)


def _active_task_steps(controller: Any) -> str:
    record = controller.session_store.ensure(controller.session_id, surface=controller.surface)
    task = controller.task_store.load(record.active_task_id) if record.active_task_id else None
    if task is None:
        return "No active durable task."
    if not task.steps:
        return f"Active task {task.task_id} has no fixed step graph. Phase={task.current_phase}."
    lines = [f"Task {task.task_id} steps:"]
    for step in task.steps:
        lines.append(f"- {step.step_id}: {step.status} {step.tool} ({step.purpose})")
    return "\n".join(lines)


def _audit(controller: Any) -> str:
    records = controller.audit_log.read_all()[-10:]
    if not records:
        return "No audit records yet."
    lines = ["Recent audit records:"]
    for record in records:
        lines.append(f"- {record.get('type', 'record')}: {record.get('tool', '')} {record.get('decision', '')}")
    return "\n".join(lines)


def _memory(controller: Any, arg: str) -> str:
    manager = getattr(controller, "memory_manager", None)
    if manager is None:
        return "MemoryManager is unavailable."
    if arg.strip():
        record = manager.remember(scope="global", key=f"note:{controller.session_id}", value=arg.strip(), source="command")
        return f"Remembered {record.memory_id}."
    return manager.render_prompt_summary()


def _tools(controller: Any) -> str:
    lines = ["Static tools:"]
    for name, desc in controller.registry.describe():
        lines.append(f"- {name}: {desc}")
    dynamic = getattr(controller, "dynamic_registry", None)
    if dynamic is not None:
        summary = dynamic.render_prompt_summary(limit=20)
        lines.extend(["", summary])
    return "\n".join(lines)


def _permissions(controller: Any) -> str:
    policy = getattr(controller, "permission_policy", None)
    if policy is None:
        return "PermissionPolicy is unavailable."
    return policy.render_summary()


def _compact(controller: Any, arg: str) -> str:
    manager = getattr(controller, "memory_manager", None)
    if manager is None:
        return "MemoryManager is unavailable."
    summary = arg.strip()
    if not summary:
        context = controller.conversation_manager.render_context()
        summary = f"Session context summary:\n{context}"
    record = manager.compact_session(session_id=controller.session_id, summary=summary)
    controller.conversation_manager.context["last_compaction_memory_id"] = record.memory_id
    return f"Compacted current context into {record.memory_id}."
