"""Persistent web session service."""

from __future__ import annotations

import os
import threading
import uuid
from dataclasses import dataclass, field, replace
from typing import Any

from sysdialogue.app.config import AppConfig
from sysdialogue.agent.error_presentation import present_error
from sysdialogue.audit.serializers import format_audit_table
from sysdialogue.app.runtime_factory import create_runtime


@dataclass
class _PendingConfirmation:
    request_id: str
    tool: str
    reason: str
    risk_level: str
    rollback_hint: str
    event: threading.Event = field(default_factory=threading.Event)
    approved: bool = False
    decision: str = "once"


@dataclass
class _PendingInput:
    request_id: str
    prompt: str
    multiline: bool
    event: threading.Event = field(default_factory=threading.Event)
    value: str = ""


class WebSession:
    def __init__(self, config, session_id: str):
        self.config = config
        self.runtime = create_runtime(
            config,
            session_id=session_id,
            require_api=True,
            confirm_callback=self._confirm_callback,
            input_callback=self._input_callback,
            surface="web",
        )
        self.runtime.controller.event_callback = self._event_callback
        self.session_id = session_id
        self.pending_confirmation: _PendingConfirmation | None = None
        self.pending_input: _PendingInput | None = None
        self._worker: threading.Thread | None = None
        self._lock = threading.Lock()
        self.runtime.session_store.recover_interrupted(
            self.session_id,
            self.runtime.task_store,
            surface="web",
        )
        self._recover_unowned_pending()

    def state(self) -> dict:
        with self._lock:
            self._recover_unowned_pending()
            record = self.runtime.session_store.ensure(self.session_id, surface="web")
            task = self.runtime.task_store.load(record.active_task_id) if record.active_task_id else None
            audit_lines = format_audit_table(self.runtime.audit_log.read_all()).splitlines()[-20:]
            last_result = next(
                (
                    entry["text"]
                    for entry in reversed(record.entries)
                    if entry.get("role") in {"assistant", "error"}
                ),
                "",
            )
            return {
                "session_id": self.session_id,
                "status": record.status,
                "entries": list(record.entries),
                "task_events": list(record.task_events),
                "pending_confirmation": _pending_confirmation_payload(self.pending_confirmation, record.pending_confirmation),
                "pending_input": _pending_input_payload(self.pending_input, record.pending_input),
                "context": dict(record.context),
                "result_summary": last_result,
                "audit_tail": audit_lines,
                "active_task": _task_payload(task),
                "resume_available": bool(task and task.status == "interrupted"),
                "technical_details": record.technical_details,
                "traces": [span.__dict__ for span in self.runtime.trace_store.list_spans(self.session_id, limit=50)],
                "memory": [record.__dict__ for record in self.runtime.memory_manager.list_records(limit=20)],
                "permission_policy": self.runtime.permission_policy.render_summary(),
                "skills": [skill.__dict__ for skill in self.runtime.skill_manager.list_skills()],
                "hooks": [hook.__dict__ for hook in self.runtime.hook_manager.list_rules()],
                "target_config": _target_payload(self.config, self.runtime.env_profile),
                "permission_explain": self.runtime.permission_policy.explain_tool(
                    tool="*",
                    args={},
                    risk_level="SAFE",
                    target=str(self.runtime.env_profile.get("host") or self.runtime.env_profile.get("hostname") or ""),
                ),
            }

    def start_turn(self, text: str) -> None:
        with self._lock:
            if self._worker and self._worker.is_alive():
                raise RuntimeError("当前会话仍在执行中")
            self.runtime.session_store.set_status(self.session_id, "running", surface="web")
            self._worker = threading.Thread(target=self._run_turn, args=(text,), daemon=True)
            self._worker.start()

    def run_command(self, text: str) -> str:
        with self._lock:
            if self._worker and self._worker.is_alive():
                raise RuntimeError("当前会话仍在执行中")
            if not (text or "").strip().startswith("/"):
                raise RuntimeError("command must start with /")
            reply = self.runtime.controller.run_turn(text)
            return reply

    def submit_turn_input(self, text: str) -> None:
        with self._lock:
            if self.pending_input is None:
                if self._resolve_persisted_input(text):
                    return
                self._recover_unowned_pending()
                raise RuntimeError("当前没有待输入请求")
            pending = self.pending_input
            self.pending_input = None
            self.runtime.session_store.resolve_pending_input(
                self.session_id,
                value=text,
                surface="web",
            )
        pending.value = text
        pending.event.set()

    def submit_confirmation(self, approved: bool, decision: str = "once") -> None:
        clean_decision = str(decision or "once").strip().lower()
        if clean_decision not in {"once", "always_this_session", "deny"}:
            clean_decision = "once" if approved else "deny"
        with self._lock:
            if self.pending_confirmation is None:
                if self._resolve_persisted_confirmation(approved, clean_decision):
                    return
                self._recover_unowned_pending()
                raise RuntimeError("当前没有待确认请求")
            pending = self.pending_confirmation
            self.pending_confirmation = None
            self.runtime.session_store.resolve_pending_confirmation(
                self.session_id,
                approved=approved,
                surface="web",
            )
        pending.approved = approved
        pending.decision = clean_decision
        pending.event.set()

    def needs_input_response(self) -> bool:
        with self._lock:
            if self.pending_input is not None:
                return True
            record = self.runtime.session_store.ensure(self.session_id, surface="web")
            pending = record.pending_input or {}
            return record.status == "waiting_input" and bool(pending) and not pending.get("resolved")

    def resume(self) -> None:
        with self._lock:
            if self._worker and self._worker.is_alive():
                raise RuntimeError("当前会话仍在执行中")
            record = self.runtime.session_store.ensure(self.session_id, surface="web")
            if not record.active_task_id:
                raise RuntimeError("当前没有可恢复的任务")
            task = self.runtime.task_store.load(record.active_task_id)
            if task is None or task.status != "interrupted":
                raise RuntimeError("当前没有可恢复的任务")
            self.runtime.controller.force_resume_task(task.task_id)
            self.runtime.session_store.set_status(self.session_id, "running", surface="web")
            self._worker = threading.Thread(
                target=self._run_turn,
                args=(f"继续任务：{task.goal}",),
                daemon=True,
            )
            self._worker.start()

    def activate_skill(self, name: str, args: dict[str, Any] | None = None) -> str:
        if not name:
            raise RuntimeError("skill name cannot be empty")
        invocation = self.runtime.skill_manager.activate(name, args or {}, source="user")
        self.runtime.controller.conversation_manager.context[f"skill:{invocation.name}"] = invocation.context
        self.runtime.session_store.sync_manager(
            self.session_id,
            self.runtime.controller.conversation_manager,
            surface="web",
        )
        self.runtime.controller._emit_task_event(
            "skill_activated",
            f"Skill activated: {invocation.name}",
            {"skill": invocation.name, "source": "web", "record_path": invocation.record_path},
        )
        return f"Activated skill {invocation.name}."

    def configure_target(self, payload: dict[str, Any]) -> str:
        with self._lock:
            if self._worker and self._worker.is_alive():
                raise RuntimeError("当前会话仍在执行中，不能切换目标机器")
            if self.pending_confirmation is not None or self.pending_input is not None:
                raise RuntimeError("当前存在待确认/待输入请求，不能切换目标机器")
            new_config = _target_config_from_payload(self.config, payload)
            old_runtime = self.runtime
            new_runtime = create_runtime(
                new_config,
                session_id=self.session_id,
                require_api=True,
                confirm_callback=self._confirm_callback,
                input_callback=self._input_callback,
                surface="web",
            )
            new_runtime.controller.event_callback = self._event_callback
            self.runtime = new_runtime
            self.config = new_config
            self.runtime.session_store.recover_interrupted(
                self.session_id,
                self.runtime.task_store,
                surface="web",
            )
            summary = _target_payload(self.config, self.runtime.env_profile)["summary"]
            self.runtime.controller.conversation_manager.context["target"] = summary
            self.runtime.controller.conversation_manager.context["target_mode"] = (
                "remote" if self.config.remote_mode else "local"
            )
            self.runtime.session_store.sync_manager(
                self.session_id,
                self.runtime.controller.conversation_manager,
                surface="web",
            )
            self.runtime.session_store.append_entry(
                self.session_id,
                "system",
                f"目标机器已切换：{summary}",
                surface="web",
            )
            self.runtime.session_store.set_status(self.session_id, "ready", surface="web")
        try:
            old_runtime.close()
        except Exception:
            pass
        return summary

    def cancel(self) -> None:
        self.runtime.controller.request_cancel()
        with self._lock:
            if self.pending_confirmation is not None:
                self.pending_confirmation.approved = False
                self.pending_confirmation.event.set()
                self.pending_confirmation = None
            if self.pending_input is not None:
                self.pending_input.value = ""
                self.pending_input.event.set()
                self.pending_input = None
            self.runtime.session_store.set_status(
                self.session_id,
                "cancelling",
                surface="web",
                pending_confirmation=None,
                pending_input=None,
            )

    def close(self) -> None:
        self.runtime.close()

    def _run_turn(self, text: str) -> None:
        try:
            self.runtime.controller.run_turn(text)
        except Exception as exc:
            presentation = present_error(exc)
            user_text = (
                f"{presentation.user_summary}\n"
                f"影响：{presentation.impact}\n"
                f"建议：{presentation.suggested_next_action}"
            )
            with self._lock:
                self.runtime.session_store.append_entry(
                    self.session_id,
                    "error",
                    user_text,
                    surface="web",
                    technical_details=presentation.technical_details,
                )
                self.runtime.session_store.set_status(
                    self.session_id,
                    "failed",
                    surface="web",
                    active_task_id="",
                    technical_details=presentation.technical_details,
                )
        finally:
            with self._lock:
                self._worker = None

    def _confirm_callback(self, req) -> bool:
        pending = _PendingConfirmation(
            request_id=f"confirm_{uuid.uuid4().hex[:8]}",
            tool=req.tool,
            reason=req.risk.reason,
            risk_level=req.risk.level,
            rollback_hint=req.rollback_hint or req.risk.rollback_hint,
        )
        with self._lock:
            self.pending_confirmation = pending
            self.runtime.session_store.set_status(
                self.session_id,
                "waiting_confirm",
                surface="web",
                pending_confirmation={
                    "tool": pending.tool,
                    "reason": pending.reason,
                    "risk_level": pending.risk_level,
                    "rollback_hint": pending.rollback_hint,
                    "owner_pid": os.getpid(),
                    "request_id": pending.request_id,
                    "resolved": False,
                },
            )
        while not pending.event.wait(timeout=0.25):
            record = self.runtime.session_store.load(self.session_id)
            data = record.pending_confirmation if record else None
            if (
                isinstance(data, dict)
                and data.get("request_id") == pending.request_id
                and data.get("resolved")
            ):
                pending.approved = bool(data.get("approved"))
                pending.decision = str(data.get("decision") or ("once" if pending.approved else "deny"))
                break
            if self.runtime.controller.is_cancel_requested():
                pending.approved = False
                break
        with self._lock:
            if self.pending_confirmation is pending:
                self.pending_confirmation = None
            status = "cancelling" if self.runtime.controller.is_cancel_requested() else "running"
            self.runtime.session_store.clear_pending(self.session_id, surface="web", status=status)
        return {
            "approved": pending.approved,
            "decision": pending.decision if pending.approved else "deny",
        }

    def _input_callback(self, prompt: str, multiline: bool) -> str:
        pending = _PendingInput(
            request_id=f"input_{uuid.uuid4().hex[:8]}",
            prompt=prompt,
            multiline=multiline,
        )
        with self._lock:
            self.pending_input = pending
            self.runtime.session_store.set_status(
                self.session_id,
                "waiting_input",
                surface="web",
                pending_input={
                    "prompt": prompt,
                    "multiline": multiline,
                    "owner_pid": os.getpid(),
                    "request_id": pending.request_id,
                    "resolved": False,
                },
            )
        while not pending.event.wait(timeout=0.25):
            record = self.runtime.session_store.load(self.session_id)
            data = record.pending_input if record else None
            if (
                isinstance(data, dict)
                and data.get("request_id") == pending.request_id
                and data.get("resolved")
            ):
                pending.value = str(data.get("value") or "")
                break
            if self.runtime.controller.is_cancel_requested():
                pending.value = ""
                break
        with self._lock:
            if self.pending_input is pending:
                self.pending_input = None
            status = "cancelling" if self.runtime.controller.is_cancel_requested() else "running"
            self.runtime.session_store.clear_pending(self.session_id, surface="web", status=status)
        return pending.value

    def _event_callback(self, event) -> None:
        # Persistence is handled inside ReActRunner / SessionStore; keep this callback for in-process wakeups only.
        return None

    def _recover_unowned_pending(self) -> None:
        record = self.runtime.session_store.ensure(self.session_id, surface="web")
        if record.status not in {"waiting_confirm", "waiting_input"}:
            return
        pending = record.pending_confirmation or record.pending_input
        if not pending:
            return
        owner_pid = pending.get("owner_pid") if isinstance(pending, dict) else None
        if isinstance(pending, dict) and pending.get("resolved"):
            return
        if owner_pid and _process_alive(owner_pid):
            return
        detail = "Pending web confirmation/input lost its owning worker process."
        if record.active_task_id:
            try:
                self.runtime.task_store.mark_interrupted(record.active_task_id, technical_details=detail)
            except Exception:
                pass
        self.runtime.session_store.mark_interrupted(
            self.session_id,
            technical_details=detail,
            keep_active_task=bool(record.active_task_id),
            surface="web",
        )

    def _resolve_persisted_confirmation(self, approved: bool, decision: str = "once") -> bool:
        record = self.runtime.session_store.ensure(self.session_id, surface="web")
        pending = record.pending_confirmation or {}
        if record.status != "waiting_confirm" or not pending or pending.get("resolved"):
            return False
        owner_pid = pending.get("owner_pid")
        if owner_pid and not _process_alive(owner_pid):
            return False
        self.runtime.session_store.resolve_pending_confirmation(
            self.session_id,
            approved=approved,
            surface="web",
        )
        record = self.runtime.session_store.load(self.session_id)
        if record and record.pending_confirmation:
            pending = dict(record.pending_confirmation)
            pending["decision"] = decision if approved else "deny"
            self.runtime.session_store.set_status(
                self.session_id,
                "running",
                surface="web",
                pending_confirmation=pending,
            )
        return True

    def _resolve_persisted_input(self, text: str) -> bool:
        record = self.runtime.session_store.ensure(self.session_id, surface="web")
        pending = record.pending_input or {}
        if record.status != "waiting_input" or not pending or pending.get("resolved"):
            return False
        owner_pid = pending.get("owner_pid")
        if owner_pid and not _process_alive(owner_pid):
            return False
        self.runtime.session_store.resolve_pending_input(
            self.session_id,
            value=text,
            surface="web",
        )
        return True


class WebSessionStore:
    def __init__(self, config):
        self.config = config
        self._sessions: dict[str, WebSession] = {}
        self._lock = threading.Lock()

    def get(self, session_id: str) -> WebSession:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                session = WebSession(self.config, session_id)
                self._sessions[session_id] = session
            return session


def _target_config_from_payload(config: AppConfig, payload: dict[str, Any]) -> AppConfig:
    mode = str((payload or {}).get("mode") or "local").strip().lower()
    if mode not in {"local", "ssh", "remote"}:
        raise RuntimeError("目标模式必须是 local 或 ssh")
    new_config = replace(config)
    if mode == "local":
        new_config.remote_mode = False
        new_config.ssh_host = ""
        new_config.ssh_port = 22
        new_config.ssh_user = ""
        new_config.ssh_key_file = ""
        return new_config

    host = str(payload.get("host") or "").strip()
    user = str(payload.get("user") or "").strip()
    key_file = str(payload.get("ssh_key_file") or payload.get("key_file") or "").strip()
    raw_port = payload.get("port") or 22
    try:
        port = int(raw_port)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("SSH 端口必须是 1..65535 的整数") from exc
    if not host:
        raise RuntimeError("SSH 目标 host 不能为空")
    if not user:
        raise RuntimeError("SSH 用户名不能为空")
    if not (1 <= port <= 65535):
        raise RuntimeError("SSH 端口必须是 1..65535")
    if key_file and not os.path.exists(os.path.expanduser(key_file)):
        raise RuntimeError(f"SSH 私钥文件不存在：{key_file}")
    new_config.remote_mode = True
    new_config.ssh_host = host
    new_config.ssh_port = port
    new_config.ssh_user = user
    new_config.ssh_key_file = os.path.expanduser(key_file) if key_file else ""
    return new_config


def _target_payload(config: AppConfig, env_profile: dict[str, Any]) -> dict[str, Any]:
    if config.remote_mode:
        summary = f"ssh://{config.ssh_user}@{config.ssh_host}:{config.ssh_port}"
        mode = "ssh"
    else:
        summary = "local control machine"
        mode = "local"
    return {
        "mode": mode,
        "summary": summary,
        "host": config.ssh_host if config.remote_mode else "",
        "port": config.ssh_port if config.remote_mode else 22,
        "user": config.ssh_user if config.remote_mode else "",
        "ssh_key_file": config.ssh_key_file if config.remote_mode else "",
        "remote_mode": bool(config.remote_mode),
        "env": {
            "os": env_profile.get("os", "unknown"),
            "distro": env_profile.get("distro", "unknown"),
            "hostname": env_profile.get("hostname", ""),
            "init_system": env_profile.get("init_system", "unknown"),
            "package_manager": env_profile.get("package_manager", "unknown"),
        },
    }


def _task_payload(task) -> dict[str, Any] | None:
    if task is None:
        return None
    return {
        "task_id": task.task_id,
        "goal": task.goal,
        "mode": task.mode,
        "status": task.status,
        "current_phase": task.current_phase,
        "iteration_budget": task.iteration_budget,
        "iteration_limit": task.iteration_limit,
        "plan_id": task.plan_id,
        "workflow_name": task.workflow_name,
        "resume_message": task.resume_message,
    }


def _pending_confirmation_payload(
    pending: _PendingConfirmation | None,
    persisted: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if pending is not None:
        return {
            "tool": pending.tool,
            "reason": pending.reason,
            "risk_level": pending.risk_level,
            "rollback_hint": pending.rollback_hint,
            "recoverable": True,
        }
    if not persisted:
        return None
    if persisted.get("resolved"):
        return None
    return {
        "tool": persisted.get("tool", ""),
        "reason": persisted.get("reason", ""),
        "risk_level": persisted.get("risk_level", ""),
        "rollback_hint": persisted.get("rollback_hint", ""),
        "recoverable": bool(_process_alive(persisted.get("owner_pid"))),
    }


def _pending_input_payload(
    pending: _PendingInput | None,
    persisted: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if pending is not None:
        return {
            "prompt": pending.prompt,
            "multiline": pending.multiline,
            "recoverable": True,
        }
    if not persisted:
        return None
    if persisted.get("resolved"):
        return None
    return {
        "prompt": persisted.get("prompt", ""),
        "multiline": bool(persisted.get("multiline")),
        "recoverable": bool(_process_alive(persisted.get("owner_pid"))),
    }


def _process_alive(pid: Any) -> bool:
    try:
        value = int(pid)
    except (TypeError, ValueError):
        return False
    if value <= 0:
        return False
    if value == os.getpid():
        return True
    if os.name == "nt":
        return _process_alive_windows(value)
    try:
        os.kill(value, 0)
    except OSError:
        return False
    return True


def _process_alive_windows(pid: int) -> bool:
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:
        return False

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        exit_code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return False
        return exit_code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)
