"""Persistent web session service for the SysDialogue operations console."""

from __future__ import annotations

import os
import threading
import uuid
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

from sysdialogue.agent.error_presentation import present_error
from sysdialogue.agent.state_store import LockStore, SessionStore, TaskRecord, TaskStore
from sysdialogue.agent.target_profile import TargetProfile, TargetProfileStore
from sysdialogue.app.config import AppConfig
from sysdialogue.app.runtime_factory import create_runtime
from sysdialogue.audit.serializers import export_audit_jsonl, export_replay_package, format_audit_table
from sysdialogue.audit.trace_store import AuditLog


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
    sensitive: bool = False
    event: threading.Event = field(default_factory=threading.Event)
    value: str = ""


def _copy_app_config(config: AppConfig) -> AppConfig:
    if isinstance(config, AppConfig):
        return replace(config)
    return AppConfig(
        api_key=str(getattr(config, "api_key", "") or ""),
        base_url=str(getattr(config, "base_url", "") or ""),
        model=str(getattr(config, "model", "") or ""),
        remote_mode=bool(getattr(config, "remote_mode", False)),
        ssh_host=str(getattr(config, "ssh_host", "") or ""),
        ssh_port=int(getattr(config, "ssh_port", 22) or 22),
        ssh_user=str(getattr(config, "ssh_user", "") or ""),
        ssh_key_file=str(getattr(config, "ssh_key_file", "") or ""),
        ssh_password=str(getattr(config, "ssh_password", "") or ""),
        ssh_sudo_password=str(getattr(config, "ssh_sudo_password", "") or ""),
        workflows_dir=str(getattr(config, "workflows_dir", "") or ""),
        max_iterations=int(getattr(config, "max_iterations", 160) or 160),
    )


class WebSession:
    def __init__(
        self,
        config: AppConfig,
        session_id: str,
        *,
        config_update_callback: Any | None = None,
    ):
        self.config = _copy_app_config(config)
        self._config_update_callback = config_update_callback
        self.runtime = create_runtime(
            self.config,
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

    def state(self) -> dict[str, Any]:
        with self._lock:
            self._recover_unowned_pending()
            record = self.runtime.session_store.ensure(self.session_id, surface="web")
            task = self.runtime.task_store.load(record.active_task_id) if record.active_task_id else None
            audit_lines = format_audit_table(AuditLog(self.session_id).read_all()).splitlines()[-20:]
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
                "pending_confirmation": _pending_confirmation_payload(
                    self.pending_confirmation,
                    record.pending_confirmation,
                ),
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
                "api_config": _api_config_payload(self.config),
                "target_config": _target_payload(self.config, self.runtime.env_profile),
                "permission_explain": self.runtime.permission_policy.explain_tool(
                    tool="*",
                    args={},
                    risk_level="SAFE",
                    target=str(self.runtime.env_profile.get("host") or self.runtime.env_profile.get("hostname") or ""),
                ),
                "sessions_hint": [summary.__dict__ for summary in self.runtime.session_store.list_summaries(limit=12)],
                "task_summary": self.list_tasks(limit=12),
                "locks": [_lease_payload(lease) for lease in self.runtime.lock_store.list_leases()],
                "target_profiles": [_target_profile_payload(profile) for profile in self.runtime.target_profile_store.list_profiles(limit=20)],
                "ui_warnings": _ui_warnings(record, task),
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

    def export_audit_file(self) -> Path | None:
        if not self.runtime.audit_log.path.exists():
            return None
        return export_audit_jsonl(self.runtime.audit_log)

    def export_replay(self) -> Path | None:
        if not self.runtime.audit_log.path.exists():
            return None
        return export_replay_package(self.runtime.audit_log)

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
                args=("/resume",),
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
            f"已加载技能：{invocation.name}",
            {"skill": invocation.name, "source": "web", "record_path": invocation.record_path},
        )
        return f"已加载技能 {invocation.name}。"

    def configure_target(self, payload: dict[str, Any]) -> str:
        with self._lock:
            if self._worker and self._worker.is_alive():
                raise RuntimeError("当前会话仍在执行中，不能切换目标机器")
            if self.pending_confirmation is not None or self.pending_input is not None:
                raise RuntimeError("当前存在待确认/待输入请求，不能切换目标机器")
            new_config = _target_config_from_payload(self.config, payload, self.runtime.target_profile_store)
            old_runtime = self.runtime
            try:
                new_runtime = create_runtime(
                    new_config,
                    session_id=self.session_id,
                    require_api=True,
                    confirm_callback=self._confirm_callback,
                    input_callback=self._input_callback,
                    surface="web",
                )
            except RuntimeError:
                raise
            except Exception as exc:
                raise RuntimeError(f"SSH 连接失败：{_friendly_target_error(exc)}") from exc
            new_runtime.controller.event_callback = self._event_callback
            self.runtime = new_runtime
            self.config = new_config
            if self._config_update_callback is not None:
                self._config_update_callback(new_config)
            self.runtime.session_store.recover_interrupted(
                self.session_id,
                self.runtime.task_store,
                surface="web",
            )
            target = _target_payload(self.config, self.runtime.env_profile)
            summary = target["summary"]
            self._remember_current_target(target, payload)
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

    def configure_api(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            if self._worker and self._worker.is_alive():
                raise RuntimeError("当前会话仍在执行中，不能切换模型配置")
            if self.pending_confirmation is not None or self.pending_input is not None:
                raise RuntimeError("当前存在待确认/待输入请求，不能切换模型配置")
            new_config = _api_config_from_payload(self.config, payload)
            old_runtime = self.runtime
            try:
                new_runtime = create_runtime(
                    new_config,
                    session_id=self.session_id,
                    require_api=True,
                    confirm_callback=self._confirm_callback,
                    input_callback=self._input_callback,
                    surface="web",
                )
            except Exception as exc:
                raise RuntimeError(f"模型配置失败：{exc}") from exc
            new_runtime.controller.event_callback = self._event_callback
            self.runtime = new_runtime
            self.config = new_config
            if self._config_update_callback is not None:
                self._config_update_callback(new_config)
            self.runtime.session_store.recover_interrupted(
                self.session_id,
                self.runtime.task_store,
                surface="web",
            )
            self.runtime.controller.conversation_manager.context["api_model"] = self.config.model
            if self.config.base_url:
                self.runtime.controller.conversation_manager.context["api_base_url"] = self.config.base_url
            self.runtime.session_store.sync_manager(
                self.session_id,
                self.runtime.controller.conversation_manager,
                surface="web",
            )
            self.runtime.session_store.append_entry(
                self.session_id,
                "system",
                f"模型配置已更新：{self.config.model}",
                surface="web",
            )
            self.runtime.session_store.set_status(self.session_id, "ready", surface="web")
        try:
            old_runtime.close()
        except Exception:
            pass
        return _api_config_payload(self.config)

    def list_tasks(self, *, limit: int = 50) -> list[dict[str, Any]]:
        return [_task_payload(task) for task in self.runtime.task_store.list_records(session_id=self.session_id, limit=limit)]

    def task_detail(self, task_id: str) -> dict[str, Any]:
        task = self.runtime.task_store.load(task_id)
        if task is None or task.session_id != self.session_id:
            raise FileNotFoundError(f"Task not found: {task_id}")
        payload = asdict(task)
        payload["summary"] = _task_payload(task)
        return payload

    def audit_summary(self, *, limit: int = 100) -> dict[str, Any]:
        audit = AuditLog(self.session_id)
        records = audit.read_all()
        return {
            "session_id": self.session_id,
            "records": records[-limit:],
            "table": format_audit_table(records).splitlines()[-limit:],
            "count": len(records),
        }

    def export_audit(self) -> dict[str, str]:
        path = export_replay_package(AuditLog(self.session_id))
        return {"path": str(path)}

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

    def _confirm_callback(self, req) -> dict[str, Any]:
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

    def _input_callback(self, prompt: str, multiline: bool, sensitive: bool = False) -> str:
        pending = _PendingInput(
            request_id=f"input_{uuid.uuid4().hex[:8]}",
            prompt=prompt,
            multiline=multiline,
            sensitive=bool(sensitive),
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
                    "sensitive": bool(sensitive),
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
        # Persistence is handled inside ReActRunner / SessionStore; keep this callback for wakeups only.
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
            next_pending = dict(record.pending_confirmation)
            next_pending["decision"] = decision if approved else "deny"
            self.runtime.session_store.set_status(
                self.session_id,
                "running",
                surface="web",
                pending_confirmation=next_pending,
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

    def _remember_current_target(self, target: dict[str, Any], payload: dict[str, Any]) -> None:
        target_id = self.runtime.target_profile_store.target_id_from_env(
            {
                **self.runtime.env_profile,
                "remote_mode": target.get("remote_mode"),
                "host": target.get("host"),
                "ssh_port": target.get("port"),
            }
        )
        profile = self.runtime.target_profile_store.load(target_id) or TargetProfile(target_id=target_id)
        profile.label = str(payload.get("label") or profile.label or target.get("summary") or "")
        profile.facts.update(
            {
                "mode": target.get("mode"),
                "host": target.get("host"),
                "port": target.get("port"),
                "user": target.get("user"),
                "ssh_key_file": target.get("ssh_key_file"),
                "password_configured": bool(target.get("password_configured")),
                "summary": target.get("summary"),
            }
        )
        if self.config.ssh_password:
            profile.facts["ssh_password"] = self.config.ssh_password
        env = target.get("env") or {}
        if env:
            profile.facts["env"] = env
        self.runtime.target_profile_store.save(profile)


class WebSessionStore:
    def __init__(self, config: AppConfig):
        self.config = _copy_app_config(config)
        self._sessions: dict[str, WebSession] = {}
        self._lock = threading.Lock()
        self.session_store = SessionStore()
        self.task_store = TaskStore()
        self.lock_store = LockStore()
        self.target_profile_store = TargetProfileStore()

    def get(self, session_id: str) -> WebSession:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                session = WebSession(
                    self.config,
                    session_id,
                    config_update_callback=self._update_api_defaults,
                )
                self._sessions[session_id] = session
            return session

    def _update_api_defaults(self, config: AppConfig) -> None:
        """Keep future Web sessions aligned with the latest model settings."""
        with self._lock:
            self.config = replace(
                self.config,
                api_key=config.api_key,
                base_url=config.base_url,
                model=config.model,
            )

    def create_session(self) -> dict[str, Any]:
        session_id = f"web_{uuid.uuid4().hex[:12]}"
        record = self.session_store.ensure(session_id, surface="web", title="新的 Web 会话")
        return record.summary().__dict__

    def list_sessions(self, *, limit: int = 30) -> list[dict[str, Any]]:
        records = [
            record
            for record in (self.session_store.load(path.stem) for path in self.session_store.storage_dir.glob("*.json"))
            if record
        ]
        records.sort(key=lambda item: item.updated_at, reverse=True)
        return [_session_summary_payload(record) for record in records[:limit]]

    def list_locks(self) -> list[dict[str, Any]]:
        return [_lease_payload(lease) for lease in self.lock_store.list_leases()]

    def list_targets(self) -> list[dict[str, Any]]:
        return [_target_profile_payload(profile) for profile in self.target_profile_store.list_profiles(limit=50)]

    def save_target(self, payload: dict[str, Any]) -> dict[str, Any]:
        config = _target_config_from_payload(self.config, payload or {}, self.target_profile_store)
        target = _target_payload(config, {
            "os": "unknown",
            "distro": "unknown",
            "hostname": config.ssh_host if config.remote_mode else "local",
        })
        target_id = self.target_profile_store.target_id_from_env(
            {
                "remote_mode": target["remote_mode"],
                "host": target["host"],
                "ssh_port": target["port"],
            }
        )
        profile = self.target_profile_store.load(target_id) or TargetProfile(target_id=target_id)
        profile.label = str((payload or {}).get("label") or profile.label or target["summary"])
        profile.facts.update(
            {
                "mode": target["mode"],
                "host": target["host"],
                "port": target["port"],
                "user": target["user"],
                "ssh_key_file": target["ssh_key_file"],
                "password_configured": bool(config.ssh_password),
                "summary": target["summary"],
            }
        )
        if config.ssh_password:
            profile.facts["ssh_password"] = config.ssh_password
        return _target_profile_payload(self.target_profile_store.save(profile))

    def delete_target(self, target_id: str) -> bool:
        return self.target_profile_store.delete(target_id)

    def test_target(self, payload: dict[str, Any]) -> dict[str, Any]:
        probe_session_id = f"target_test_{uuid.uuid4().hex[:8]}"
        runtime = None
        try:
            probe_config = _target_config_from_payload(self.config, payload, self.target_profile_store)
            runtime = create_runtime(probe_config, session_id=probe_session_id, require_api=False, surface="web")
            target = _target_payload(probe_config, runtime.env_profile)
            return {"ok": True, "summary": target["summary"], "target": target}
        except RuntimeError as exc:
            return {"ok": False, "category": _target_error_category(exc), "message": str(exc)}
        except Exception as exc:
            return {"ok": False, "category": _target_error_category(exc), "message": _friendly_target_error(exc)}
        finally:
            if runtime is not None:
                try:
                    runtime.close()
                except Exception:
                    pass
            _cleanup_probe_artifacts(probe_session_id)


def _target_config_from_payload(
    config: AppConfig,
    payload: dict[str, Any],
    target_store: TargetProfileStore | None = None,
) -> AppConfig:
    payload = payload or {}
    mode = str(payload.get("mode") or "local").strip().lower()
    if mode not in {"local", "ssh", "remote"}:
        raise RuntimeError("目标模式必须是 local 或 ssh")
    new_config = replace(config)
    if mode == "local":
        new_config.remote_mode = False
        new_config.ssh_host = ""
        new_config.ssh_port = 22
        new_config.ssh_user = ""
        new_config.ssh_key_file = ""
        new_config.ssh_password = ""
        return new_config

    saved_facts: dict[str, Any] = {}
    target_id = str(payload.get("target_id") or "").strip()
    if target_id and target_store is not None:
        profile = target_store.load(target_id)
        if profile is not None:
            saved_facts = dict(profile.facts or {})
    host = str(payload.get("host") or saved_facts.get("host") or "").strip()
    user = str(payload.get("user") or saved_facts.get("user") or "").strip()
    key_file = str(
        payload.get("ssh_key_file")
        or payload.get("key_file")
        or saved_facts.get("ssh_key_file")
        or ""
    ).strip()
    password = str(payload.get("password") or saved_facts.get("ssh_password") or saved_facts.get("password") or "")
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
    new_config.ssh_password = password
    return new_config


def _cleanup_probe_artifacts(session_id: str) -> None:
    """Remove transient session/audit files created by connection probes."""
    cleanup_paths = [
        SessionStore()._path(session_id),
        TaskStore()._path(session_id),
        AuditLog(session_id).path,
    ]
    for path in cleanup_paths:
        try:
            if path.exists():
                path.unlink()
        except OSError:
            pass


def _api_config_payload(config: AppConfig) -> dict[str, Any]:
    return {
        "base_url": config.base_url,
        "model": config.model,
        "api_key": config.api_key,
        "api_key_configured": bool(config.api_key),
    }


def _api_config_from_payload(config: AppConfig, payload: dict[str, Any]) -> AppConfig:
    new_config = replace(config)
    if "base_url" in payload:
        new_config.base_url = str(payload.get("base_url") or "").strip()
    if "model" in payload:
        new_config.model = str(payload.get("model") or "").strip()
    raw_key = str(payload.get("api_key") or "").strip()
    if raw_key:
        new_config.api_key = raw_key
    if not new_config.api_key:
        raise RuntimeError("OpenAI API Key 不能为空")
    if not new_config.model:
        raise RuntimeError("OpenAI 模型不能为空")
    return new_config


def _target_payload(config: AppConfig, env_profile: dict[str, Any]) -> dict[str, Any]:
    if config.remote_mode:
        summary = f"ssh://{config.ssh_user}@{config.ssh_host}:{config.ssh_port}"
        mode = "ssh"
    else:
        summary = "本机控制端"
        mode = "local"
    return {
        "mode": mode,
        "summary": summary,
        "host": config.ssh_host if config.remote_mode else "",
        "port": config.ssh_port if config.remote_mode else 22,
        "user": config.ssh_user if config.remote_mode else "",
        "ssh_key_file": config.ssh_key_file if config.remote_mode else "",
        "password_configured": bool(config.remote_mode and config.ssh_password),
        "remote_mode": bool(config.remote_mode),
        "env": {
            "os": env_profile.get("os", "unknown"),
            "distro": env_profile.get("distro", "unknown"),
            "hostname": env_profile.get("hostname", ""),
            "init_system": env_profile.get("init_system", "unknown"),
            "package_manager": env_profile.get("package_manager", "unknown"),
        },
    }


def _session_summary_payload(record) -> dict[str, Any]:
    payload = record.summary().__dict__
    context = dict(getattr(record, "context", {}) or {})
    target_summary = str(context.get("target") or "").strip()
    target_mode = str(context.get("target_mode") or "").strip().lower()
    is_ssh = target_mode in {"remote", "ssh"} or target_summary.startswith("ssh://")
    if is_ssh:
        label = target_summary or "SSH target"
        group = label.replace("ssh://", "SSH ", 1)
        payload.update(
            {
                "target_mode": "ssh",
                "target_summary": label,
                "target_group": group,
            }
        )
        return payload
    payload.update(
        {
            "target_mode": "local",
            "target_summary": target_summary or "本机控制端",
            "target_group": "本机会话",
        }
    )
    return payload


def _task_payload(task: TaskRecord | None) -> dict[str, Any] | None:
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
        "updated_at": task.updated_at,
        "observed": task.observed,
        "acted": task.acted,
        "verified": task.verified,
        "changed_state": task.changed_state,
        "failed_mutations": list(task.failed_mutations),
        "steps_count": len(task.steps),
        "events_count": len(task.events),
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
            "request_id": pending.request_id,
        }
    if not persisted or persisted.get("resolved"):
        return None
    return {
        "tool": persisted.get("tool", ""),
        "reason": persisted.get("reason", ""),
        "risk_level": persisted.get("risk_level", ""),
        "rollback_hint": persisted.get("rollback_hint", ""),
        "recoverable": bool(_process_alive(persisted.get("owner_pid"))),
        "request_id": persisted.get("request_id", ""),
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
            "request_id": pending.request_id,
        }
    if not persisted or persisted.get("resolved"):
        return None
    return {
        "prompt": persisted.get("prompt", ""),
        "multiline": bool(persisted.get("multiline")),
        "recoverable": bool(_process_alive(persisted.get("owner_pid"))),
        "request_id": persisted.get("request_id", ""),
    }


def _lease_payload(lease) -> dict[str, Any]:
    return {
        "scope": lease.scope,
        "scope_hash": lease.scope_hash,
        "task_id": lease.task_id,
        "session_id": lease.session_id,
        "surface": lease.surface,
        "acquired_at": lease.acquired_at,
        "heartbeat_ts": lease.heartbeat_ts,
    }


def _target_profile_payload(profile: TargetProfile) -> dict[str, Any]:
    facts = dict(profile.facts)
    password_configured = bool(facts.get("ssh_password") or facts.get("password") or facts.get("password_configured"))
    facts.pop("ssh_password", None)
    facts.pop("password", None)
    facts["password_configured"] = password_configured
    return {
        "target_id": profile.target_id,
        "label": profile.label,
        "facts": facts,
        "common_services": list(profile.common_services),
        "risk_preferences": dict(profile.risk_preferences),
        "last_verification": profile.last_verification,
        "updated_at": profile.updated_at,
    }


def _ui_warnings(record, task: TaskRecord | None) -> list[str]:
    warnings: list[str] = []
    if record.status == "interrupted":
        warnings.append("上一轮任务已中断，可以点击恢复或重新发起。")
    if task and task.status == "interrupted":
        warnings.append("当前任务心跳已失效，恢复会从安全的阶段边界继续。")
    return warnings


def _friendly_target_error(exc: BaseException) -> str:
    text = str(exc) or exc.__class__.__name__
    category = _target_error_category(exc)
    if category == "auth":
        return f"SSH 认证失败，请检查用户名、密码、私钥或 ssh-agent。原始信息：{text}"
    if category == "known_hosts":
        return f"known_hosts 校验失败，请确认目标主机指纹可信后再连接。原始信息：{text}"
    if category == "timeout":
        return f"连接超时，请检查网络、端口和防火墙。原始信息：{text}"
    if category == "not_found":
        return f"请求的 Web 路由不存在，请刷新页面或重启 Web 服务。原始信息：{text}"
    return text


def _target_error_category(exc: BaseException) -> str:
    text = str(exc).lower()
    name = exc.__class__.__name__.lower()
    if "not found" in text or "404" in text:
        return "not_found"
    if "auth" in text or "authentication" in text or "permission denied" in text:
        return "auth"
    if "known_hosts" in text or "host key" in text or "fingerprint" in text:
        return "known_hosts"
    if "timed out" in text or "timeout" in text:
        return "timeout"
    if "connection refused" in text or "no route" in text:
        return "network"
    if "ssh" in text or "paramiko" in name:
        return "ssh"
    return "runtime"


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
