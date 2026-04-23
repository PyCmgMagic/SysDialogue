"""In-memory web session service."""

from __future__ import annotations

import threading
import traceback
from dataclasses import dataclass, field

from sysdialogue.audit.serializers import format_audit_table
from sysdialogue.app.runtime_factory import create_runtime


@dataclass
class _PendingConfirmation:
    tool: str
    reason: str
    risk_level: str
    rollback_hint: str
    event: threading.Event = field(default_factory=threading.Event)
    approved: bool = False


@dataclass
class _PendingInput:
    prompt: str
    multiline: bool
    event: threading.Event = field(default_factory=threading.Event)
    value: str = ""


class WebSession:
    def __init__(self, config, session_id: str):
        self.runtime = create_runtime(
            config,
            session_id=session_id,
            require_api=True,
            confirm_callback=self._confirm_callback,
            input_callback=self._input_callback,
        )
        self.session_id = session_id
        self.entries: list[dict] = []
        self.status = "ready"
        self.pending_confirmation: _PendingConfirmation | None = None
        self.pending_input: _PendingInput | None = None
        self._worker: threading.Thread | None = None
        self._lock = threading.Lock()

    def state(self) -> dict:
        with self._lock:
            audit_lines = format_audit_table(self.runtime.audit_log.read_all()).splitlines()[-20:]
            last_result = next(
                (
                    entry["text"]
                    for entry in reversed(self.entries)
                    if entry.get("role") in {"assistant", "error"}
                ),
                "",
            )
            return {
                "session_id": self.session_id,
                "status": self.status,
                "entries": list(self.entries),
                "pending_confirmation": (
                    {
                        "tool": self.pending_confirmation.tool,
                        "reason": self.pending_confirmation.reason,
                        "risk_level": self.pending_confirmation.risk_level,
                        "rollback_hint": self.pending_confirmation.rollback_hint,
                    }
                    if self.pending_confirmation else None
                ),
                "pending_input": (
                    {
                        "prompt": self.pending_input.prompt,
                        "multiline": self.pending_input.multiline,
                    }
                    if self.pending_input else None
                ),
                "context": dict(self.runtime.controller.conversation_manager.context),
                "result_summary": last_result,
                "audit_tail": audit_lines,
            }

    def start_turn(self, text: str) -> None:
        with self._lock:
            if self._worker and self._worker.is_alive():
                raise RuntimeError("当前会话仍在执行中")
            self.entries.append({"role": "user", "text": text})
            self.status = "thinking"
            self._worker = threading.Thread(target=self._run_turn, args=(text,), daemon=True)
            self._worker.start()

    def submit_turn_input(self, text: str) -> None:
        with self._lock:
            if self.pending_input is None:
                raise RuntimeError("当前没有待输入请求")
            pending = self.pending_input
            self.pending_input = None
            self.status = "thinking"
        pending.value = text
        pending.event.set()

    def submit_confirmation(self, approved: bool) -> None:
        with self._lock:
            if self.pending_confirmation is None:
                raise RuntimeError("当前没有待确认请求")
            pending = self.pending_confirmation
            self.pending_confirmation = None
            self.status = "thinking"
        pending.approved = approved
        pending.event.set()

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
            self.status = "cancelling"

    def close(self) -> None:
        self.runtime.close()

    def _run_turn(self, text: str) -> None:
        try:
            reply = self.runtime.controller.run_turn(text)
            role = "assistant"
        except Exception:
            reply = traceback.format_exc()
            role = "error"
        with self._lock:
            self.entries.append({"role": role, "text": reply})
            self.status = "ready"

    def _confirm_callback(self, req) -> bool:
        pending = _PendingConfirmation(
            tool=req.tool,
            reason=req.risk.reason,
            risk_level=req.risk.level,
            rollback_hint=req.rollback_hint or req.risk.rollback_hint,
        )
        with self._lock:
            self.pending_confirmation = pending
            self.status = "confirming"
        pending.event.wait()
        return pending.approved

    def _input_callback(self, prompt: str, multiline: bool) -> str:
        pending = _PendingInput(prompt=prompt, multiline=multiline)
        with self._lock:
            self.pending_input = pending
            self.status = "input"
        pending.event.wait()
        return pending.value


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
