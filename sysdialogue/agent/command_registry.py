"""Slash command layer shared by TUI, Web, and Simple CLI."""

from __future__ import annotations

import json
import shlex
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
        if command == "/skills":
            return CommandResult(_skills(controller))
        if command == "/skill":
            return CommandResult(_skill(controller, arg))
        if command == "/skill-reload":
            return CommandResult(_skill_reload(controller))
        if command == "/hooks":
            return CommandResult(_hooks(controller))
        if command == "/forget":
            return CommandResult(_forget(controller, arg))
        if command == "/target":
            return CommandResult(_target(controller, arg))
        if command == "/why":
            return CommandResult(_why(controller, arg))
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
            "- /skills: list installed Markdown skills",
            "- /skill <name> [json args]: activate a skill for this session",
            "- /skill-reload: rescan project/user skills",
            "- /hooks: show configured hooks",
            "- /forget <memory_id>: remove one memory record",
            "- /target [set key=value]: show or update current target profile",
            "- /why [tool]: explain the current permission decision",
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
    if summary.startswith("--preview"):
        preview = summary.removeprefix("--preview").strip()
        if not preview:
            context = controller.conversation_manager.render_context()
            preview = f"Session context summary:\n{context}"
        return manager.compact_preview(session_id=controller.session_id, summary=preview)
    if not summary:
        context = controller.conversation_manager.render_context()
        summary = f"Session context summary:\n{context}"
    record = manager.compact_session(session_id=controller.session_id, summary=summary)
    controller.conversation_manager.context["last_compaction_memory_id"] = record.memory_id
    return f"Compacted current context into {record.memory_id}."


def _skills(controller: Any) -> str:
    manager = getattr(controller, "skill_manager", None)
    if manager is None:
        return "SkillManager is unavailable."
    skills = manager.list_skills()
    if not skills:
        return "No skills installed. Add SKILL.md under .sysdialogue/skills/<name>/ or ~/.sysdialogue/skills/<name>/."
    lines = ["Installed skills:"]
    for skill in skills:
        flags = []
        if skill.user_invocable:
            flags.append("user")
        if skill.model_invocable:
            flags.append("model")
        tools = ", ".join(skill.allowed_tools) if skill.allowed_tools else "no tool preference"
        lines.append(f"- {skill.name} [{skill.scope}; {', '.join(flags)}; tools={tools}]: {skill.description}")
    return "\n".join(lines)


def _skill(controller: Any, arg: str) -> str:
    manager = getattr(controller, "skill_manager", None)
    if manager is None:
        return "SkillManager is unavailable."
    name, raw_args = _split_name_and_tail(arg)
    if not name:
        return "Usage: /skill <name> [json args]"
    try:
        parsed_args = json.loads(raw_args) if raw_args.strip() else {}
    except json.JSONDecodeError as exc:
        return f"Skill args must be JSON object: {exc}"
    if not isinstance(parsed_args, dict):
        return "Skill args must be a JSON object."
    try:
        invocation = manager.activate(name, parsed_args, source="user")
    except Exception as exc:
        return f"Skill activation failed: {exc}"
    controller.conversation_manager.context[f"skill:{invocation.name}"] = invocation.context
    if getattr(controller, "session_store", None) is not None:
        controller.session_store.sync_manager(
            controller.session_id,
            controller.conversation_manager,
            surface=controller.surface,
        )
    if hasattr(controller, "_emit_task_event"):
        controller._emit_task_event(
            "skill_activated",
            f"Skill activated: {invocation.name}",
            {"skill": invocation.name, "source": "user", "record_path": invocation.record_path},
        )
    if hasattr(controller, "_trace"):
        controller._trace(
            "handoff",
            "activate_skill",
            summary=f"Activated skill {invocation.name} from command",
            data={"skill": invocation.name, "record_path": invocation.record_path},
        )
    return f"Activated skill {invocation.name}. It was added to reusable context; no OS operation was executed."


def _skill_reload(controller: Any) -> str:
    manager = getattr(controller, "skill_manager", None)
    if manager is None:
        return "SkillManager is unavailable."
    skills = manager.reload()
    return f"Reloaded {len(skills)} skill(s)."


def _hooks(controller: Any) -> str:
    manager = getattr(controller, "hook_manager", None)
    if manager is None:
        return "HookManager is unavailable."
    manager.reload()
    return manager.render_summary()


def _forget(controller: Any, arg: str) -> str:
    manager = getattr(controller, "memory_manager", None)
    if manager is None:
        return "MemoryManager is unavailable."
    memory_id = arg.strip()
    if not memory_id:
        return "Usage: /forget <memory_id>"
    return f"Forgot {memory_id}." if manager.forget(memory_id) else f"Memory not found: {memory_id}"


def _target(controller: Any, arg: str) -> str:
    store = getattr(controller, "target_profile_store", None)
    if store is None:
        return "TargetProfileStore is unavailable."
    target_id = store.target_id_from_env(getattr(controller, "env_profile", {}) or {})
    stripped = arg.strip()
    if not stripped:
        return store.render_prompt_summary(target_id)
    if stripped.startswith("set "):
        assignment = stripped[4:].strip()
        if "=" not in assignment:
            return "Usage: /target set key=value"
        key, value = assignment.split("=", 1)
        profile = store.remember_fact(target_id, key.strip(), value.strip())
        controller.conversation_manager.context[f"target:{key.strip()}"] = value.strip()
        if getattr(controller, "session_store", None) is not None:
            controller.session_store.sync_manager(
                controller.session_id,
                controller.conversation_manager,
                surface=controller.surface,
            )
        return f"Updated target {profile.target_id}: {key.strip()}={value.strip()}"
    return "Usage: /target or /target set key=value"


def _why(controller: Any, arg: str) -> str:
    policy = getattr(controller, "permission_policy", None)
    if policy is None:
        return "PermissionPolicy is unavailable."
    tool = (arg.strip().split() or ["*"])[0]
    target = str((getattr(controller, "env_profile", {}) or {}).get("host") or (getattr(controller, "env_profile", {}) or {}).get("hostname") or "")
    explanation = policy.explain_tool(tool=tool, args={}, risk_level="SAFE", target=target)
    matched = explanation.get("matched_rule") or {}
    candidates = explanation.get("candidate_rules") or []
    lines = [
        f"## Permission decision for `{tool}`",
        "",
        f"- Action: `{explanation.get('action')}`",
        f"- Rule: `{explanation.get('rule_id')}`",
        f"- Reason: {explanation.get('reason')}",
        f"- Decision logic: {explanation.get('decision_reason')}",
    ]
    if matched:
        lines.append(f"- Matched rule: `{matched.get('rule_id')}` ({matched.get('kind')}:{matched.get('pattern')})")
    if explanation.get("suggested_always_grant"):
        lines.append("- Tip: this can be approved as `always_this_session` from the confirmation dialog.")
    if candidates:
        lines.extend(["", "Candidate rules:"])
        for rule in candidates[:5]:
            lines.append(f"- `{rule.get('rule_id')}` {rule.get('action')} {rule.get('kind')}:{rule.get('pattern')}")
    return "\n".join(lines)


def _split_name_and_tail(text: str) -> tuple[str, str]:
    stripped = (text or "").strip()
    if not stripped:
        return "", ""
    try:
        parts = shlex.split(stripped, posix=False)
    except ValueError:
        parts = stripped.split(maxsplit=1)
    if not parts:
        return "", ""
    name = parts[0].strip("\"'")
    tail = stripped[len(parts[0]):].strip()
    return name, tail
