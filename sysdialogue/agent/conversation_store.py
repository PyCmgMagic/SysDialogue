"""Persistent user-visible conversation history for TUI sessions."""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sysdialogue.agent.conversation import ConversationManager


@dataclass
class ConversationSummary:
    session_id: str
    title: str
    updated_at: str
    status: str
    last_user_message: str


@dataclass
class ConversationRecord:
    session_id: str
    title: str
    created_at: str
    updated_at: str
    status: str
    user_messages: list[str] = field(default_factory=list)
    final_replies: list[str] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)
    history: list[dict] = field(default_factory=list)
    events_summary: dict[str, Any] = field(default_factory=dict)

    def summary(self) -> ConversationSummary:
        last_user = self.user_messages[-1] if self.user_messages else ""
        return ConversationSummary(
            session_id=self.session_id,
            title=self.title,
            updated_at=self.updated_at,
            status=self.status,
            last_user_message=last_user,
        )


class ConversationStore:
    """JSON-file conversation store under ~/.sysdialogue/conversations."""

    def __init__(self, storage_dir: str | None = None):
        self.storage_dir = Path(storage_dir or os.path.expanduser("~/.sysdialogue/conversations"))
        self.storage_dir.mkdir(parents=True, exist_ok=True)

    def save_turn(
        self,
        *,
        session_id: str,
        manager: ConversationManager,
        user_message: str,
        final_reply: str,
        status: str,
        events_summary: dict[str, Any] | None = None,
    ) -> ConversationRecord:
        now = datetime.now(timezone.utc).isoformat()
        record = self.load(session_id)
        if record is None:
            record = ConversationRecord(
                session_id=session_id or str(uuid.uuid4())[:8],
                title=_title_from_message(user_message),
                created_at=now,
                updated_at=now,
                status=status,
            )
        record.updated_at = now
        record.status = status
        if user_message:
            record.user_messages.append(_truncate(user_message, 500))
        if final_reply:
            record.final_replies.append(_truncate(final_reply, 2000))
        record.context = _json_safe_dict(manager.context)
        record.history = _sanitize_history(manager.history)
        record.events_summary = _sanitize_events(events_summary or {})
        self._write(record)
        return record

    def load(self, session_id: str) -> ConversationRecord | None:
        path = self._path(session_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return ConversationRecord(
            session_id=str(data.get("session_id") or session_id),
            title=str(data.get("title") or session_id),
            created_at=str(data.get("created_at") or ""),
            updated_at=str(data.get("updated_at") or ""),
            status=str(data.get("status") or "unknown"),
            user_messages=list(data.get("user_messages") or []),
            final_replies=list(data.get("final_replies") or []),
            context=dict(data.get("context") or {}),
            history=list(data.get("history") or []),
            events_summary=dict(data.get("events_summary") or {}),
        )

    def list_summaries(self, limit: int = 30) -> list[ConversationSummary]:
        records = [record for record in (self.load(path.stem) for path in self.storage_dir.glob("*.json")) if record]
        records.sort(key=lambda item: item.updated_at, reverse=True)
        return [record.summary() for record in records[:limit]]

    def restore_to_manager(self, session_id: str, manager: ConversationManager) -> ConversationRecord:
        record = self.load(session_id)
        if record is None:
            raise FileNotFoundError(f"Conversation history not found: {session_id}")
        manager.history = list(record.history)
        manager.context = dict(record.context)
        return record

    def _write(self, record: ConversationRecord) -> None:
        data = asdict(record)
        path = self._path(record.session_id)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass

    def _path(self, session_id: str) -> Path:
        safe = "".join(ch for ch in (session_id or "") if ch.isalnum() or ch in ("-", "_"))
        return self.storage_dir / f"{safe or 'default'}.json"


def _sanitize_history(history: list[dict]) -> list[dict]:
    sanitized: list[dict] = []
    for message in history:
        role = message.get("role")
        if role not in {"user", "assistant"}:
            continue
        content = message.get("content")
        if isinstance(content, str):
            sanitized.append({"role": role, "content": _truncate(content, 1200)})
            continue
        text = _text_blocks(content)
        if text:
            sanitized.append({"role": role, "content": _truncate(text, 1200)})
    return sanitized[-20:]


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
    return text[: max(0, limit - 1)] + "…"
