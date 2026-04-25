"""Persistent session/task/lock stores for cross-surface SysDialogue state."""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from filelock import FileLock

from sysdialogue.agent.conversation import ConversationManager


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _iso_age_seconds(ts: str) -> float:
    try:
        then = datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return float("inf")
    return max(0.0, (datetime.now(timezone.utc) - then).total_seconds())


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


@dataclass
class SessionSummary:
    session_id: str
    title: str
    updated_at: str
    status: str
    last_user_message: str


@dataclass
class SessionRecord:
    session_id: str
    title: str
    created_at: str
    updated_at: str
    status: str
    surface: str = "unknown"
    active_task_id: str = ""
    user_messages: list[str] = field(default_factory=list)
    final_replies: list[str] = field(default_factory=list)
    entries: list[dict[str, Any]] = field(default_factory=list)
    task_events: list[dict[str, Any]] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)
    history: list[dict[str, Any]] = field(default_factory=list)
    events_summary: dict[str, Any] = field(default_factory=dict)
    pending_confirmation: dict[str, Any] | None = None
    pending_input: dict[str, Any] | None = None
    technical_details: str = ""

    def summary(self) -> SessionSummary:
        return SessionSummary(
            session_id=self.session_id,
            title=self.title,
            updated_at=self.updated_at,
            status=self.status,
            last_user_message=self.user_messages[-1] if self.user_messages else "",
        )


@dataclass
class TaskEventRecord:
    ts: str
    stage: str
    message: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class TaskStepRecord:
    step_id: str
    status: str = "pending"
    kind: str = "tool_call"
    tool: str = ""
    purpose: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    expected_risk: str = "UNKNOWN"
    actual_risk: str = "UNKNOWN"
    rule_ids: list[str] = field(default_factory=list)
    error: str = ""
    workflow_step_type: str = ""
    audit_refs: list[str] = field(default_factory=list)
    lock_scope: str = ""
    depends_on: list[str] = field(default_factory=list)
    finding_id: str = ""
    severity: str = ""
    blocking: bool = False
    resolution: str = ""
    source_ref: str = ""
    updated_at: str = field(default_factory=_now_iso)


@dataclass
class TaskRecord:
    task_id: str
    session_id: str
    surface: str
    goal: str
    mode: str = "direct"
    status: str = "ready"
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)
    heartbeat_ts: str = field(default_factory=_now_iso)
    current_phase: str = "analysis"
    iteration_budget: int = 0
    iteration_limit: int = 0
    plan_id: str = ""
    workflow_name: str = ""
    resume_message: str = ""
    technical_details: str = ""
    observed: bool = False
    acted: bool = False
    verified: bool = False
    changed_state: bool = False
    tool_steps: int = 0
    last_action_step: int = 0
    last_verification_step: int = 0
    failed_mutations: list[str] = field(default_factory=list)
    steps: list[TaskStepRecord] = field(default_factory=list)
    events: list[TaskEventRecord] = field(default_factory=list)
    audit_refs: list[str] = field(default_factory=list)

    def is_final(self) -> bool:
        return self.status in {
            "completed",
            "failed",
            "rolled_back",
            "cancelled",
            "blocked",
        }

    def next_pending_step(self) -> TaskStepRecord | None:
        for step in self.steps:
            if step.status in {"pending", "running"}:
                return step
        return None


@dataclass
class LockLease:
    scope: str
    scope_hash: str
    task_id: str
    session_id: str
    surface: str
    acquired_at: str
    heartbeat_ts: str


class SessionStore:
    """Persistent user-visible session state under ~/.sysdialogue/sessions."""

    def __init__(self, storage_dir: str | None = None):
        self.storage_dir = Path(storage_dir or os.path.expanduser("~/.sysdialogue/sessions"))
        self.storage_dir.mkdir(parents=True, exist_ok=True)

    def ensure(self, session_id: str, *, surface: str = "unknown", title: str = "") -> SessionRecord:
        record = self.load(session_id)
        if record is not None:
            if surface and not record.surface:
                record.surface = surface
                self.save(record)
            return record
        now = _now_iso()
        record = SessionRecord(
            session_id=session_id,
            title=title or "Untitled conversation",
            created_at=now,
            updated_at=now,
            status="ready",
            surface=surface,
        )
        self.save(record)
        return record

    def load(self, session_id: str) -> SessionRecord | None:
        data = _read_json(self._path(session_id))
        if data is None:
            return None
        return SessionRecord(
            session_id=str(data.get("session_id") or session_id),
            title=str(data.get("title") or session_id),
            created_at=str(data.get("created_at") or ""),
            updated_at=str(data.get("updated_at") or ""),
            status=str(data.get("status") or "unknown"),
            surface=str(data.get("surface") or "unknown"),
            active_task_id=str(data.get("active_task_id") or ""),
            user_messages=list(data.get("user_messages") or []),
            final_replies=list(data.get("final_replies") or []),
            entries=list(data.get("entries") or []),
            task_events=list(data.get("task_events") or []),
            context=dict(data.get("context") or {}),
            history=list(data.get("history") or []),
            events_summary=dict(data.get("events_summary") or {}),
            pending_confirmation=data.get("pending_confirmation") or None,
            pending_input=data.get("pending_input") or None,
            technical_details=str(data.get("technical_details") or ""),
        )

    def save(self, record: SessionRecord) -> SessionRecord:
        record.updated_at = _now_iso()
        _atomic_write_json(self._path(record.session_id), asdict(record))
        return record

    def mutate(self, session_id: str, mutator: Callable[[SessionRecord], None], *, surface: str = "unknown") -> SessionRecord:
        lock = FileLock(str(self._path(session_id)) + ".lock", timeout=10)
        with lock:
            record = self.ensure(session_id, surface=surface)
            mutator(record)
            return self.save(record)

    def list_summaries(self, limit: int = 30) -> list[SessionSummary]:
        records = [record for record in (self.load(path.stem) for path in self.storage_dir.glob("*.json")) if record]
        records.sort(key=lambda item: item.updated_at, reverse=True)
        return [record.summary() for record in records[:limit]]

    def restore_to_manager(self, session_id: str, manager: ConversationManager) -> SessionRecord:
        record = self.load(session_id)
        if record is None:
            raise FileNotFoundError(f"Conversation history not found: {session_id}")
        manager.history = _sanitize_history(record.history)
        manager.context = dict(record.context)
        return record

    def save_turn(
        self,
        *,
        session_id: str,
        manager: ConversationManager,
        user_message: str,
        final_reply: str,
        status: str,
        events_summary: dict[str, Any] | None = None,
        surface: str = "unknown",
        entry_role: str = "assistant",
        technical_details: str = "",
    ) -> SessionRecord:
        def mutate(record: SessionRecord) -> None:
            if not record.title or record.title == "Untitled conversation":
                record.title = _title_from_message(user_message)
            record.surface = surface or record.surface
            record.status = status
            if user_message:
                record.user_messages.append(_truncate(user_message, 500))
                entry: dict[str, Any] = {"role": "user", "text": user_message}
                if record.active_task_id:
                    entry["task_id"] = record.active_task_id
                record.entries.append(entry)
            if final_reply:
                record.final_replies.append(_truncate(final_reply, 2000))
                entry = {"role": entry_role, "text": final_reply}
                if record.active_task_id:
                    entry["task_id"] = record.active_task_id
                record.entries.append(entry)
            record.entries = record.entries[-200:]
            record.context = _json_safe_dict(manager.context)
            record.history = _sanitize_history(manager.history)
            record.events_summary = _sanitize_events(events_summary or {})
            record.pending_confirmation = None
            record.pending_input = None
            if status in {"completed", "failed", "cancelled", "blocked", "need_info", "partial", "rolled_back"}:
                record.active_task_id = ""
            if technical_details:
                record.technical_details = technical_details

        return self.mutate(session_id, mutate, surface=surface)

    def append_entry(
        self,
        session_id: str,
        role: str,
        text: str,
        *,
        surface: str = "unknown",
        technical_details: str = "",
        task_id: str = "",
    ) -> SessionRecord:
        def mutate(record: SessionRecord) -> None:
            record.surface = surface or record.surface
            entry: dict[str, Any] = {"role": role, "text": text}
            effective_task_id = task_id or record.active_task_id
            if effective_task_id:
                entry["task_id"] = effective_task_id
            if technical_details:
                entry["technical_details"] = technical_details
            record.entries.append(entry)
            record.entries = record.entries[-200:]
            if role == "user" and text.strip():
                record.user_messages.append(_truncate(text, 500))
                if not record.title or record.title == "Untitled conversation":
                    record.title = _title_from_message(text)
            if role in {"assistant", "error"} and text.strip():
                record.final_replies.append(_truncate(text, 2000))
            if technical_details:
                record.technical_details = technical_details

        return self.mutate(session_id, mutate, surface=surface)

    def append_user_turn(
        self,
        session_id: str,
        text: str,
        *,
        surface: str = "unknown",
        active_task_id: str | None = None,
    ) -> SessionRecord:
        """Persist the user side of a turn before long-running execution starts."""
        def mutate(record: SessionRecord) -> None:
            clean = str(text or "").strip()
            record.surface = surface or record.surface
            record.status = "running"
            if active_task_id is not None:
                record.active_task_id = active_task_id
            if clean:
                record.user_messages.append(_truncate(clean, 500))
                entry: dict[str, Any] = {"role": "user", "text": clean}
                if active_task_id:
                    entry["task_id"] = active_task_id
                record.entries.append(entry)
                record.entries = record.entries[-200:]
                if not record.title or record.title == "Untitled conversation":
                    record.title = _title_from_message(clean)

        return self.mutate(session_id, mutate, surface=surface)

    def append_task_event(
        self,
        session_id: str,
        event: dict[str, Any],
        *,
        surface: str = "unknown",
    ) -> SessionRecord:
        def mutate(record: SessionRecord) -> None:
            record.surface = surface or record.surface
            record.task_events.append(_json_safe(event))
            record.task_events = record.task_events[-200:]

        return self.mutate(session_id, mutate, surface=surface)

    def sync_manager(
        self,
        session_id: str,
        manager: ConversationManager,
        *,
        surface: str = "unknown",
        events_summary: dict[str, Any] | None = None,
    ) -> SessionRecord:
        def mutate(record: SessionRecord) -> None:
            record.surface = surface or record.surface
            record.context = _json_safe_dict(manager.context)
            record.history = _sanitize_history(manager.history)
            if events_summary is not None:
                record.events_summary = _sanitize_events(events_summary)

        return self.mutate(session_id, mutate, surface=surface)

    def set_status(
        self,
        session_id: str,
        status: str,
        *,
        surface: str = "unknown",
        active_task_id: str | None = None,
        pending_confirmation: dict[str, Any] | None = None,
        pending_input: dict[str, Any] | None = None,
        technical_details: str | None = None,
    ) -> SessionRecord:
        def mutate(record: SessionRecord) -> None:
            record.surface = surface or record.surface
            record.status = status
            if active_task_id is not None:
                record.active_task_id = active_task_id
            record.pending_confirmation = _json_safe(pending_confirmation) if pending_confirmation else None
            record.pending_input = _json_safe(pending_input) if pending_input else None
            if technical_details is not None:
                record.technical_details = technical_details

        return self.mutate(session_id, mutate, surface=surface)

    def resolve_pending_confirmation(
        self,
        session_id: str,
        *,
        approved: bool,
        surface: str = "unknown",
    ) -> SessionRecord:
        def mutate(record: SessionRecord) -> None:
            if not record.pending_confirmation:
                raise RuntimeError("当前没有待确认请求")
            pending = dict(record.pending_confirmation)
            pending["resolved"] = True
            pending["approved"] = bool(approved)
            pending["resolved_at"] = _now_iso()
            record.pending_confirmation = _json_safe(pending)
            record.status = "running"
            record.surface = surface or record.surface

        return self.mutate(session_id, mutate, surface=surface)

    def resolve_pending_input(
        self,
        session_id: str,
        *,
        value: str,
        surface: str = "unknown",
    ) -> SessionRecord:
        def mutate(record: SessionRecord) -> None:
            if not record.pending_input:
                raise RuntimeError("当前没有待输入请求")
            pending = dict(record.pending_input)
            pending["resolved"] = True
            pending["value"] = str(value or "")
            pending["resolved_at"] = _now_iso()
            record.pending_input = _json_safe(pending)
            record.status = "running"
            record.surface = surface or record.surface

        return self.mutate(session_id, mutate, surface=surface)

    def clear_pending(
        self,
        session_id: str,
        *,
        surface: str = "unknown",
        status: str | None = None,
    ) -> SessionRecord:
        def mutate(record: SessionRecord) -> None:
            record.surface = surface or record.surface
            if status is not None:
                record.status = status
            record.pending_confirmation = None
            record.pending_input = None

        return self.mutate(session_id, mutate, surface=surface)

    def mark_interrupted(
        self,
        session_id: str,
        *,
        technical_details: str,
        keep_active_task: bool = True,
        surface: str = "unknown",
    ) -> SessionRecord:
        def mutate(record: SessionRecord) -> None:
            record.surface = surface or record.surface
            record.status = "interrupted"
            record.pending_confirmation = None
            record.pending_input = None
            record.technical_details = technical_details
            record.entries.append({
                "role": "error",
                "text": "上一轮在服务重启或任务失去心跳后中断，请重新发起或继续任务。",
            })
            record.entries = record.entries[-200:]
            if not keep_active_task:
                record.active_task_id = ""

        return self.mutate(session_id, mutate, surface=surface)

    def recover_interrupted(self, session_id: str, task_store: "TaskStore", *, surface: str = "unknown") -> SessionRecord:
        record = self.ensure(session_id, surface=surface)
        if not record.active_task_id:
            return record
        task = task_store.load(record.active_task_id)
        if task is None:
            return self.mark_interrupted(
                session_id,
                technical_details="active task metadata missing during recovery",
                keep_active_task=False,
                surface=surface,
            )
        if record.status not in {"running", "waiting_confirm", "waiting_input"}:
            return record
        if task_store.is_stale(task):
            task_store.mark_interrupted(
                task.task_id,
                technical_details="Task heartbeat expired while recovering session state.",
            )
            return self.mark_interrupted(
                session_id,
                technical_details="Recovered interrupted task after restart or lost heartbeat.",
                keep_active_task=True,
                surface=surface,
            )
        return record

    def _path(self, session_id: str) -> Path:
        safe = "".join(ch for ch in (session_id or "") if ch.isalnum() or ch in ("-", "_"))
        return self.storage_dir / f"{safe or 'default'}.json"


class TaskStore:
    """Persistent task state under ~/.sysdialogue/tasks."""

    def __init__(self, storage_dir: str | None = None, *, stale_after: int = 30):
        self.storage_dir = Path(storage_dir or os.path.expanduser("~/.sysdialogue/tasks"))
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.stale_after = stale_after

    def load(self, task_id: str) -> TaskRecord | None:
        data = _read_json(self._path(task_id))
        if data is None:
            return None
        return TaskRecord(
            task_id=str(data.get("task_id") or task_id),
            session_id=str(data.get("session_id") or ""),
            surface=str(data.get("surface") or "unknown"),
            goal=str(data.get("goal") or ""),
            mode=str(data.get("mode") or "direct"),
            status=str(data.get("status") or "ready"),
            created_at=str(data.get("created_at") or _now_iso()),
            updated_at=str(data.get("updated_at") or _now_iso()),
            heartbeat_ts=str(data.get("heartbeat_ts") or _now_iso()),
            current_phase=str(data.get("current_phase") or "analysis"),
            iteration_budget=int(data.get("iteration_budget") or 0),
            iteration_limit=int(data.get("iteration_limit") or 0),
            plan_id=str(data.get("plan_id") or ""),
            workflow_name=str(data.get("workflow_name") or ""),
            resume_message=str(data.get("resume_message") or ""),
            technical_details=str(data.get("technical_details") or ""),
            observed=bool(data.get("observed")),
            acted=bool(data.get("acted")),
            verified=bool(data.get("verified")),
            changed_state=bool(data.get("changed_state")),
            tool_steps=int(data.get("tool_steps") or 0),
            last_action_step=int(data.get("last_action_step") or 0),
            last_verification_step=int(data.get("last_verification_step") or 0),
            failed_mutations=list(data.get("failed_mutations") or []),
            steps=[TaskStepRecord(**item) for item in list(data.get("steps") or [])],
            events=[TaskEventRecord(**item) for item in list(data.get("events") or [])],
            audit_refs=list(data.get("audit_refs") or []),
        )

    def list_records(self, *, session_id: str | None = None, limit: int = 50) -> list[TaskRecord]:
        records = [record for record in (self.load(path.stem) for path in self.storage_dir.glob("*.json")) if record]
        if session_id:
            records = [record for record in records if record.session_id == session_id]
        records.sort(key=lambda item: item.updated_at, reverse=True)
        return records[:limit]

    def save(self, record: TaskRecord) -> TaskRecord:
        record.updated_at = _now_iso()
        _atomic_write_json(self._path(record.task_id), asdict(record))
        return record

    def create(
        self,
        *,
        task_id: str,
        session_id: str,
        surface: str,
        goal: str,
        mode: str = "direct",
        status: str = "running",
        current_phase: str = "analysis",
        iteration_budget: int = 0,
        iteration_limit: int = 0,
        resume_message: str = "",
    ) -> TaskRecord:
        existing = self.load(task_id)
        if existing is not None:
            return existing
        record = TaskRecord(
            task_id=task_id,
            session_id=session_id,
            surface=surface,
            goal=goal,
            mode=mode,
            status=status,
            current_phase=current_phase,
            iteration_budget=iteration_budget,
            iteration_limit=iteration_limit,
            resume_message=resume_message,
        )
        return self.save(record)

    def mutate(self, task_id: str, mutator: Callable[[TaskRecord], None]) -> TaskRecord:
        lock = FileLock(str(self._path(task_id)) + ".lock", timeout=10)
        with lock:
            record = self.load(task_id)
            if record is None:
                raise FileNotFoundError(f"Task not found: {task_id}")
            mutator(record)
            return self.save(record)

    def heartbeat(self, task_id: str) -> TaskRecord:
        return self.mutate(task_id, lambda record: setattr(record, "heartbeat_ts", _now_iso()))

    def update(self, task_id: str, **changes: Any) -> TaskRecord:
        def mutate(record: TaskRecord) -> None:
            for key, value in changes.items():
                if hasattr(record, key):
                    setattr(record, key, value)

        return self.mutate(task_id, mutate)

    def append_event(self, task_id: str, event: TaskEventRecord | dict[str, Any], *, max_events: int = 200) -> TaskRecord:
        item = event if isinstance(event, TaskEventRecord) else TaskEventRecord(**event)

        def mutate(record: TaskRecord) -> None:
            record.events.append(item)
            record.events = record.events[-max_events:]

        return self.mutate(task_id, mutate)

    def set_steps(self, task_id: str, steps: list[TaskStepRecord]) -> TaskRecord:
        return self.mutate(task_id, lambda record: setattr(record, "steps", steps))

    def update_step(self, task_id: str, step_id: str, **changes: Any) -> TaskRecord:
        def mutate(record: TaskRecord) -> None:
            for step in record.steps:
                if step.step_id != step_id:
                    continue
                for key, value in changes.items():
                    if hasattr(step, key):
                        setattr(step, key, value)
                step.updated_at = _now_iso()
                break

        return self.mutate(task_id, mutate)

    def mark_interrupted(self, task_id: str, *, technical_details: str) -> TaskRecord:
        return self.update(
            task_id,
            status="interrupted",
            current_phase="resume",
            technical_details=technical_details,
        )

    def is_stale(self, record: TaskRecord, *, stale_after: int | None = None) -> bool:
        if record.is_final():
            return False
        return _iso_age_seconds(record.heartbeat_ts) > float(stale_after or self.stale_after)

    def _path(self, task_id: str) -> Path:
        safe = "".join(ch for ch in (task_id or "") if ch.isalnum() or ch in ("-", "_"))
        return self.storage_dir / f"{safe or 'task'}.json"


class LockStore:
    """Cross-process resource leases under ~/.sysdialogue/locks."""

    def __init__(self, storage_dir: str | None = None, *, stale_after: int = 30):
        self.storage_dir = Path(storage_dir or os.path.expanduser("~/.sysdialogue/locks"))
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.stale_after = stale_after

    def acquire(
        self,
        scope: str,
        *,
        task_id: str,
        session_id: str,
        surface: str,
        timeout: float = 30.0,
        stale_after: int | None = None,
        on_stale_reclaim: Callable[[LockLease], None] | None = None,
    ) -> LockLease | None:
        deadline = time.monotonic() + timeout
        stale_seen: LockLease | None = None
        while True:
            lease, stale_seen = self._try_acquire_once(
                scope,
                task_id=task_id,
                session_id=session_id,
                surface=surface,
                stale_after=stale_after or self.stale_after,
            )
            if lease is not None:
                if stale_seen is not None and on_stale_reclaim is not None:
                    on_stale_reclaim(stale_seen)
                return lease
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.1)

    def load(self, scope: str) -> LockLease | None:
        path = self._scope_path(scope)
        data = _read_json(path)
        if data is None:
            return None
        return LockLease(
            scope=str(data.get("scope") or scope),
            scope_hash=str(data.get("scope_hash") or self._scope_hash(scope)),
            task_id=str(data.get("task_id") or ""),
            session_id=str(data.get("session_id") or ""),
            surface=str(data.get("surface") or "unknown"),
            acquired_at=str(data.get("acquired_at") or _now_iso()),
            heartbeat_ts=str(data.get("heartbeat_ts") or _now_iso()),
        )

    def heartbeat(self, scope: str, *, task_id: str) -> LockLease | None:
        path = self._scope_path(scope)
        lock = FileLock(str(path) + ".lock", timeout=10)
        with lock:
            current = self.load(scope)
            if current is None or current.task_id != task_id:
                return None
            current.heartbeat_ts = _now_iso()
            _atomic_write_json(path, asdict(current))
            return current

    def heartbeat_all(self, scopes: set[str], *, task_id: str) -> None:
        for scope in list(scopes):
            self.heartbeat(scope, task_id=task_id)

    def release(self, scope: str, *, task_id: str | None = None) -> None:
        path = self._scope_path(scope)
        if not path.exists():
            return
        lock = FileLock(str(path) + ".lock", timeout=10)
        with lock:
            current = self.load(scope)
            if current is None:
                return
            if task_id is not None and current.task_id != task_id:
                return
            try:
                path.unlink()
            except OSError:
                pass

    def release_all(self, scopes: set[str], *, task_id: str) -> None:
        for scope in list(scopes):
            self.release(scope, task_id=task_id)

    def list_leases(self) -> list[LockLease]:
        leases: list[LockLease] = []
        for path in self.storage_dir.glob("*.json"):
            data = _read_json(path)
            if not data:
                continue
            scope = str(data.get("scope") or "")
            if not scope:
                continue
            lease = self.load(scope)
            if lease is not None:
                leases.append(lease)
        leases.sort(key=lambda item: item.acquired_at)
        return leases

    def is_stale(self, lease: LockLease, *, stale_after: int | None = None) -> bool:
        return _iso_age_seconds(lease.heartbeat_ts) > float(stale_after or self.stale_after)

    def _try_acquire_once(
        self,
        scope: str,
        *,
        task_id: str,
        session_id: str,
        surface: str,
        stale_after: int,
    ) -> tuple[LockLease | None, LockLease | None]:
        path = self._scope_path(scope)
        lock = FileLock(str(path) + ".lock", timeout=10)
        with lock:
            current = self.load(scope)
            if current is not None and current.task_id != task_id and not self.is_stale(current, stale_after=stale_after):
                return None, None
            stale = current if current is not None and current.task_id != task_id else None
            now = _now_iso()
            lease = LockLease(
                scope=scope,
                scope_hash=self._scope_hash(scope),
                task_id=task_id,
                session_id=session_id,
                surface=surface,
                acquired_at=now if current is None or current.task_id != task_id else current.acquired_at,
                heartbeat_ts=now,
            )
            _atomic_write_json(path, asdict(lease))
            return lease, stale

    def _scope_hash(self, scope: str) -> str:
        return hashlib.sha256(scope.encode("utf-8")).hexdigest()[:16]

    def _scope_path(self, scope: str) -> Path:
        return self.storage_dir / f"{self._scope_hash(scope)}.json"


def _sanitize_history(history: list[dict]) -> list[dict]:
    sanitized: list[dict] = []
    for message in history:
        role = message.get("role")
        if role not in {"user", "assistant"}:
            continue
        content = message.get("content")
        if isinstance(content, str):
            sanitized.append({"role": role, "content": _message_content(role, content)})
            continue
        text = _text_blocks(content)
        if text:
            sanitized.append({"role": role, "content": _message_content(role, text)})
    return sanitized[-20:]


def _message_content(role: str, text: str) -> str | list[dict[str, str]]:
    text = _truncate(text, 1200)
    if role == "assistant":
        return [{"type": "text", "text": text}]
    return text


def _text_blocks(content: Any) -> str:
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text") or ""))
    return "\n".join(part for part in parts if part.strip())


def _sanitize_events(events: dict[str, Any]) -> dict[str, Any]:
    allowed = {"status", "thinking", "verification", "results", "errors"}
    return {key: _json_safe(value) for key, value in events.items() if key in allowed}


def _json_safe_dict(value: dict[str, Any]) -> dict[str, Any]:
    return {str(key): _json_safe(item) for key, item in (value or {}).items()}


def _json_safe(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return [_json_safe(item) for item in value[:50]]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in list(value.items())[:50]}
    return str(value)


def _title_from_message(message: str) -> str:
    text = " ".join((message or "").split())
    return _truncate(text, 48) or "Untitled conversation"


def _truncate(text: str, limit: int) -> str:
    text = str(text)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "..."
