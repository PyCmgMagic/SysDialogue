"""Slash command layer shared by TUI, Web, and Simple CLI."""

from __future__ import annotations

import json
import shlex
from dataclasses import dataclass
from typing import Any

from sysdialogue.audit.serializers import export_audit_jsonl, export_replay_package
from sysdialogue.audit.trace_store import AuditLog
from sysdialogue.agent.acceptance_checklist import render_acceptance_checklist
from sysdialogue.agent.acceptance_runner import render_guided_acceptance
from sysdialogue.agent.evidence_matrix import render_evidence_matrix
from sysdialogue.agent.playbook_catalog import render_playbook_command_output
from sysdialogue.agent.remote_guidance import render_remote_examples
from sysdialogue.agent.release_readiness import render_release_gate_report, render_release_readiness_report
from sysdialogue.security.output_sanitizer import sanitize_text
from sysdialogue.tools.meta_tools import META_TOOL_SCHEMAS


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
        if command == "/doctor":
            return CommandResult(_doctor(controller))
        if command == "/check-model":
            return CommandResult(_check_model(controller))
        if command == "/examples":
            return CommandResult(_examples(controller))
        if command in {"/playbooks", "/workflows"}:
            return CommandResult(_playbooks(controller))
        if command in {"/evidence", "/verification"}:
            return CommandResult(render_evidence_matrix())
        if command in {"/acceptance", "/release-checklist"}:
            return CommandResult(render_acceptance_checklist(getattr(controller, "env_profile", {}) or {}))
        if command in {"/acceptance-runner", "/acceptance-run"}:
            return CommandResult(render_guided_acceptance(getattr(controller, "env_profile", {}) or {}))
        if command in {"/release-readiness", "/readiness"}:
            return CommandResult(render_release_readiness_report(arg.strip() or None))
        if command in {"/release-gate", "/gate"}:
            return CommandResult(render_release_gate_report(arg.strip() or None)[0])
        if command == "/next":
            return CommandResult(_next(controller))
        if command == "/resume":
            return _resume(controller)
        if command in {"/abandon", "/abandon-task"}:
            return CommandResult(_abandon(controller, arg))
        if command == "/locks":
            return CommandResult(_locks(controller))
        if command == "/plan":
            return CommandResult(_active_task_steps(controller))
        if command == "/audit":
            return CommandResult(_audit(controller))
        if command == "/export-audit":
            return CommandResult(_export_audit(controller, arg))
        if command == "/export-replay":
            return CommandResult(_export_replay(controller, arg))
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
    sections = [
        ("基础操作", [
            ("/help", "显示本帮助信息"),
            ("/status", "查看当前会话与任务状态"),
            ("/examples", "常用运维任务示例（可直接复制使用）"),
            ("/playbooks", "生产工作流列表（多步骤自动化）"),
        ]),
        ("任务控制", [
            ("/next", "推荐下一步操作（任务中断/失败时）"),
            ("/resume", "恢复中断的任务"),
            ("/abandon [task_id]", "放弃任务并释放锁"),
            ("/plan", "查看当前任务步骤"),
            ("/locks", "查看当前持有的锁"),
        ]),
        ("诊断与审计", [
            ("/doctor", "检查代理状态、工具可用性"),
            ("/check-model", "验证模型是否支持工具调用"),
            ("/audit", "查看近期审计记录"),
            ("/export-audit [session_id]", "导出审计日志（JSONL）"),
            ("/export-replay [session_id]", "导出回放包（ZIP）"),
        ]),
        ("验证与发布", [
            ("/evidence", "查看执行证据矩阵"),
            ("/acceptance", "查看验收清单"),
            ("/acceptance-runner", "运行引导式验收检查"),
            ("/release-readiness [path]", "发布准备度报告"),
            ("/release-gate [path]", "发布门禁检查"),
        ]),
        ("配置与工具", [
            ("/tools", "查看可用工具列表"),
            ("/skills", "查看已安装的技能"),
            ("/skill <名称> [参数]", "激活指定技能"),
            ("/skill-reload", "重新扫描技能文件"),
            ("/permissions", "查看权限策略"),
            ("/memory [内容]", "查看/保存记忆笔记"),
            ("/forget <memory_id>", "删除指定记忆"),
            ("/compact [摘要]", "压缩当前上下文到记忆"),
            ("/hooks", "查看配置的钩子"),
            ("/target [set key=value]", "查看/修改目标环境配置"),
            ("/why [工具名]", "解释权限决策原因"),
        ]),
    ]
    lines = ["📖  **SysDialogue 命令参考**", ""]
    for section_name, cmds in sections:
        lines.append(f"**【{section_name}】**")
        for cmd, desc in cmds:
            lines.append(f"  `{cmd}` — {desc}")
        lines.append("")
    lines.append("**快捷键**：F2 历史 · F3 审计 · F4 环境 · F5 远程 · Ctrl+L 清屏 · Ctrl+D 退出")
    return "\n".join(lines)


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


def _doctor(controller: Any) -> str:
    issues: list[str] = []
    record = controller.session_store.ensure(controller.session_id, surface=controller.surface)
    task = controller.task_store.load(record.active_task_id) if record.active_task_id else None

    lines = [
        "SysDialogue doctor:",
        f"- Session: {record.session_id} ({record.status}, surface={record.surface})",
        f"- Safety profile: {getattr(controller, 'safety_profile', 'standard')}",
    ]

    if task is None:
        lines.append("- Active task: none")
    else:
        lines.append(f"- Active task: {task.task_id} ({task.status}, phase={task.current_phase})")
        if task.status == "interrupted":
            issues.append("An interrupted task is available; use /resume to continue it.")

    static_count = _count_static_tools(controller)
    lines.append(f"- Tools: {static_count} static + {len(META_TOOL_SCHEMAS)} meta")

    dynamic_registry = getattr(controller, "dynamic_registry", None)
    if dynamic_registry is not None and hasattr(dynamic_registry, "list_tools"):
        try:
            lines.append(f"- DynTools: {len(dynamic_registry.list_tools())} reusable")
        except Exception as exc:
            lines.append(f"- DynTools: unavailable ({type(exc).__name__})")
            issues.append("Dynamic tool registry could not be read.")
    else:
        lines.append("- DynTools: unavailable")

    llm_client = getattr(controller, "llm_client", None)
    model = str(getattr(llm_client, "model", "") or "")
    base_url = str(getattr(llm_client, "base_url", "") or "")
    llm_name = type(llm_client).__name__ if llm_client is not None else "none"
    if model:
        lines.append(f"- LLM: {llm_name} model={model} base_url={base_url or 'OpenAI SDK default'}")
    else:
        lines.append(f"- LLM: {llm_name} (model not configured on this client)")
        if llm_name == "NullLLMClient":
            issues.append("This entrypoint cannot call the model; start TUI/simple with API config for live tasks.")

    lines.append(f"- Memory: {_count_records(getattr(controller, 'memory_manager', None), 'list_records')}")
    lines.append(f"- Skills: {_count_records(getattr(controller, 'skill_manager', None), 'list_skills')}")
    lines.append(f"- Hooks: {_count_records(getattr(controller, 'hook_manager', None), 'list_rules')}")

    target_store = getattr(controller, "target_profile_store", None)
    if target_store is not None:
        try:
            target_id = target_store.target_id_from_env(getattr(controller, "env_profile", {}) or {})
            lines.append(f"- Target profile: {target_id}")
        except Exception as exc:
            lines.append(f"- Target profile: unavailable ({type(exc).__name__})")
            issues.append("Target profile store could not resolve the current target.")

    env = getattr(controller, "env_profile", {}) or {}
    if env.get("remote_mode"):
        lines.append(f"- Remote access: {_remote_access_summary(env)}")

    if issues:
        lines.append("")
        lines.append("Actionable notices:")
        lines.extend(f"- {sanitize_text(issue, limit=300)}" for issue in issues)
    else:
        lines.append("")
        lines.append("Actionable notices: none")
    return "\n".join(lines)


def _check_model(controller: Any) -> str:
    from sysdialogue.agent.model_diagnostics import diagnose_tool_call_support

    return diagnose_tool_call_support(getattr(controller, "llm_client", None)).to_text()


def _examples(controller: Any) -> str:
    env = getattr(controller, "env_profile", {}) or {}
    remote = bool(env.get("remote_mode"))
    container_backend = str(env.get("container_backend") or "none")
    service = _context_value(controller, "service_name") or _context_value(controller, "target:service") or "nginx"
    container = _context_value(controller, "container_name") or "my-container"

    examples = [
        "检查系统版本、CPU/内存负载、磁盘使用率和当前监听端口。",
        f"检查 {service} 服务状态，读取最近日志并判断是否健康。",
        f"为 {service} 的配置文件做只读检查，并说明需要哪些变更才能安全修改。",
        "执行一次安全审计工作流，重点检查用户、端口、服务和关键配置。",
        "查看当前审计记录并导出可复盘包。",
    ]
    if remote:
        examples.insert(1, "检查远程目标的 SSH 连通性风险，确认不会把当前会话锁在门外。")
    if container_backend in {"docker", "podman"}:
        examples.insert(2, f"检查容器 {container} 的状态、最近日志和健康探针。")
    else:
        examples.append("检查当前环境是否具备 Docker/Podman 能力，并给出缺失原因。")

    lines = [
        "Example tasks:",
        "Copy one line into the input box, then adjust names/paths as needed.",
    ]
    lines.extend(f"- {example}" for example in examples)
    if remote:
        lines.append("")
        lines.extend(render_remote_examples(env))
    lines.append("")
    lines.append("For recurring production workflows, run `/playbooks`.")
    return "\n".join(lines)


def _playbooks(controller: Any) -> str:
    return render_playbook_command_output(getattr(controller, "env_profile", {}) or {})


def _next(controller: Any) -> str:
    record = controller.session_store.ensure(controller.session_id, surface=controller.surface)
    task = _task_for_next(controller, record)
    if task is None:
        return (
            "No interrupted, blocked, failed, or need-info task needs continuation.\n"
            "Try /examples for safe starting points, or describe the next operational goal directly."
        )

    lines = [
        f"Next recommendation for task {task.task_id} ({task.status}):",
        f"- Goal: {sanitize_text(task.goal, limit=500)}",
        f"- Phase: {task.current_phase or 'unknown'}",
    ]

    if task.status == "interrupted":
        lines.append("- Recommended action: run `/resume` to continue the interrupted task.")
        lines.append("- Alternative: run `/abandon` if this task is stale or no longer needed.")
    elif task.status == "blocked":
        lines.append("- Recommended action: start a new turn with the missing information or corrected parameters below.")
    elif task.status == "failed":
        lines.append("- Recommended action: review the failure, then retry with a narrower goal or safer parameters.")
    elif task.status == "cancelled":
        lines.append("- Recommended action: rerun the original goal when you are ready.")
    else:
        lines.append("- Recommended action: inspect `/status` and `/plan`, then continue with the next explicit instruction.")

    advice = _latest_task_advice(task)
    if advice:
        lines.append("")
        lines.append("Task advice:")
        lines.extend(f"- {item}" for item in advice)

    pending = [step for step in getattr(task, "steps", []) if step.status in {"pending", "running"}]
    failed = [step for step in getattr(task, "steps", []) if step.status in {"failed", "blocked"}]
    if pending:
        step = pending[0]
        lines.append("")
        lines.append(
            "Next pending step: "
            f"`{step.step_id}` {step.tool or step.workflow_step_type or step.kind} - {sanitize_text(step.purpose, limit=300)}"
        )
        if step.last_rejected_args:
            lines.append(f"Last rejected args: {sanitize_text(json.dumps(step.last_rejected_args, ensure_ascii=False), limit=500)}")
    if failed:
        step = failed[-1]
        lines.append("")
        lines.append(
            "Last failed step: "
            f"`{step.step_id}` {step.tool or step.workflow_step_type or step.kind} - {sanitize_text(step.error or step.result_summary, limit=500)}"
        )

    if task.changed_state and not task.verified:
        lines.append("- Safety note: this task changed state but lacks final verification; verify before claiming completion.")
    if task.failed_mutations:
        lines.append("- Safety note: failed mutation attempts exist; inspect audit/replay before retrying.")
    if task.technical_details:
        lines.append(f"- Technical detail: {sanitize_text(task.technical_details, limit=500)}")

    lines.append("")
    lines.append("Useful commands: /status, /plan, /audit, /why <tool>, /export-replay")
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


def _abandon(controller: Any, arg: str) -> str:
    record = controller.session_store.ensure(controller.session_id, surface=controller.surface)
    task_id = (arg or "").strip() or record.active_task_id
    if not task_id:
        return "No active task to abandon. Use `/next` to inspect recent blocked or failed tasks."

    task = controller.task_store.load(task_id)
    if task is None:
        controller.session_store.set_status(
            controller.session_id,
            "ready",
            surface=controller.surface,
            active_task_id="",
            technical_details=f"Cleared missing active task reference: {task_id}",
        )
        return f"Cleared missing active task reference: {task_id}."

    if task.is_final() and record.active_task_id != task_id:
        return f"Task {task_id} is already final ({task.status}); nothing to abandon."

    controller.task_store.update(
        task_id,
        status="cancelled",
        current_phase="abandoned",
        technical_details="Abandoned by user command.",
    )
    released = _release_locks_for_task(controller, task_id)
    controller.session_store.set_status(
        controller.session_id,
        "ready",
        surface=controller.surface,
        active_task_id="",
        technical_details=f"Abandoned task {task_id}.",
    )
    suffix = f" Released {released} lock(s)." if released else ""
    return f"Abandoned {task_id}.{suffix}"


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


def _export_audit(controller: Any, arg: str) -> str:
    audit = _audit_for_session(controller, arg)
    if not audit.path.exists():
        return f"Audit session not found: {audit.session_id}"
    path = export_audit_jsonl(audit)
    return f"Exported audit: {path}"


def _export_replay(controller: Any, arg: str) -> str:
    audit = _audit_for_session(controller, arg)
    if not audit.path.exists():
        return f"Audit session not found: {audit.session_id}"
    path = export_replay_package(audit)
    return f"Exported replay package: {path}"


def _audit_for_session(controller: Any, arg: str) -> AuditLog:
    session_id = (arg or "").strip() or getattr(controller.audit_log, "session_id", "")
    if session_id == getattr(controller.audit_log, "session_id", ""):
        return controller.audit_log
    return AuditLog(session_id=session_id)


def _memory(controller: Any, arg: str) -> str:
    manager = getattr(controller, "memory_manager", None)
    if manager is None:
        return "MemoryManager is unavailable."
    if arg.strip():
        record = manager.remember(scope="global", key=f"note:{controller.session_id}", value=arg.strip(), source="command")
        return f"Remembered {record.memory_id}."
    records = manager.list_records(limit=50)
    if not records:
        return "Memory records: none"
    lines = ["Memory records:"]
    for record in records:
        target = f", target={record.target_id}" if record.target_id else ""
        value = sanitize_text(record.value, limit=500)
        lines.append(f"- {record.memory_id} [{record.scope}{target}] {record.key}: {value}")
    return "\n".join(lines)


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
    profile = str(getattr(controller, "safety_profile", "standard") or "standard")
    return f"SafetyProfile: {profile}\n{policy.render_summary()}"


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


def _count_static_tools(controller: Any) -> int:
    registry = getattr(controller, "registry", None)
    if registry is None:
        return 0
    if hasattr(registry, "all_schemas"):
        try:
            return len(registry.all_schemas())
        except Exception:
            return 0
    if hasattr(registry, "names"):
        try:
            return len(registry.names())
        except Exception:
            return 0
    return 0


def _count_records(owner: Any, method_name: str) -> str:
    if owner is None:
        return "unavailable"
    method = getattr(owner, method_name, None)
    if method is None:
        return "unavailable"
    try:
        return str(len(method()))
    except Exception as exc:
        return f"unavailable ({type(exc).__name__})"


def _task_for_next(controller: Any, record: Any) -> Any | None:
    task_store = getattr(controller, "task_store", None)
    if task_store is None:
        return None
    if getattr(record, "active_task_id", ""):
        task = task_store.load(record.active_task_id)
        if task is not None:
            return task
    try:
        candidates = task_store.list_records(session_id=controller.session_id, limit=20)
    except Exception:
        return None
    interesting = {"interrupted", "blocked", "failed", "need_info", "cancelled"}
    return next((task for task in candidates if task.status in interesting), None)


def _latest_task_advice(task: Any) -> list[str]:
    advice: list[str] = []
    for event in reversed(list(getattr(task, "events", []) or [])):
        data = getattr(event, "data", {}) or {}
        if not isinstance(data, dict):
            continue
        for item in data.get("next_steps") or []:
            if isinstance(item, str) and item.strip() and item.strip() not in advice:
                advice.append(sanitize_text(item.strip(), limit=500))
        if data.get("no_action_reason"):
            advice.append("No action reason: " + sanitize_text(data["no_action_reason"], limit=500))
        if data.get("error_summary"):
            advice.append("Last error: " + sanitize_text(data["error_summary"], limit=500))
        if data.get("error_detail") and not advice:
            advice.append("Error detail: " + sanitize_text(data["error_detail"], limit=500))
        if len(advice) >= 5:
            break
    return advice[:5]


def _release_locks_for_task(controller: Any, task_id: str) -> int:
    lock_store = getattr(controller, "lock_store", None)
    if lock_store is None or not hasattr(lock_store, "list_leases"):
        return 0
    released = 0
    try:
        leases = lock_store.list_leases()
    except Exception:
        return 0
    for lease in leases:
        if getattr(lease, "task_id", "") != task_id:
            continue
        try:
            lock_store.release(lease.scope, task_id=task_id)
            released += 1
        except Exception:
            continue
    return released


def _context_value(controller: Any, key: str) -> str:
    manager = getattr(controller, "conversation_manager", None)
    context = getattr(manager, "context", {}) or {}
    value = context.get(key)
    return sanitize_text(value, limit=120).strip() if value not in (None, "") else ""


def _remote_access_summary(env: dict[str, Any]) -> str:
    host = sanitize_text(env.get("host") or env.get("hostname") or "remote", limit=120).strip() or "remote"
    port = str(env.get("ssh_port") or "22").strip() or "22"
    mode = "ProxyCommand" if env.get("ssh_proxy_command_configured") else "direct SSH"
    return f"ssh://{host}:{port} via {mode}"
