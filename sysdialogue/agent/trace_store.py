"""Local span trace store for agent observability."""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class TraceSpan:
    trace_id: str
    span_id: str
    span_type: str
    name: str
    session_id: str
    task_id: str = ""
    status: str = "ok"
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    ended_at: str = ""
    duration_ms: int = 0
    summary: str = ""
    data: dict[str, Any] = field(default_factory=dict)


class TraceStore:
    """Append-only JSONL trace store under ~/.sysdialogue/traces."""

    def __init__(self, storage_dir: str | None = None):
        self.storage_dir = Path(storage_dir or os.path.expanduser("~/.sysdialogue/traces"))
        self.storage_dir.mkdir(parents=True, exist_ok=True)

    def start_span(
        self,
        *,
        session_id: str,
        span_type: str,
        name: str,
        task_id: str = "",
        data: dict[str, Any] | None = None,
    ) -> TraceSpan:
        return TraceSpan(
            trace_id=f"trace_{session_id}",
            span_id=f"span_{uuid.uuid4().hex[:10]}",
            span_type=span_type,
            name=name,
            session_id=session_id,
            task_id=task_id,
            data=_safe_data(data or {}),
        )

    def end_span(
        self,
        span: TraceSpan,
        *,
        status: str = "ok",
        summary: str = "",
        data: dict[str, Any] | None = None,
    ) -> TraceSpan:
        ended = datetime.now(timezone.utc)
        span.ended_at = ended.isoformat()
        try:
            started = datetime.fromisoformat(span.started_at)
            span.duration_ms = int((ended - started).total_seconds() * 1000)
        except Exception:
            span.duration_ms = 0
        span.status = status
        span.summary = summary
        if data:
            span.data.update(_safe_data(data))
        self.append(span)
        return span

    def log_span(
        self,
        *,
        session_id: str,
        span_type: str,
        name: str,
        task_id: str = "",
        status: str = "ok",
        summary: str = "",
        data: dict[str, Any] | None = None,
    ) -> TraceSpan:
        span = self.start_span(
            session_id=session_id,
            span_type=span_type,
            name=name,
            task_id=task_id,
            data=data,
        )
        time.sleep(0)
        return self.end_span(span, status=status, summary=summary, data={})

    def append(self, span: TraceSpan) -> None:
        path = self._path(span.session_id)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(span), ensure_ascii=False) + "\n")

    def list_spans(self, session_id: str, limit: int = 100) -> list[TraceSpan]:
        path = self._path(session_id)
        if not path.exists():
            return []
        spans: list[TraceSpan] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            spans.append(
                TraceSpan(
                    trace_id=str(item.get("trace_id") or f"trace_{session_id}"),
                    span_id=str(item.get("span_id") or ""),
                    span_type=str(item.get("span_type") or ""),
                    name=str(item.get("name") or ""),
                    session_id=str(item.get("session_id") or session_id),
                    task_id=str(item.get("task_id") or ""),
                    status=str(item.get("status") or "ok"),
                    started_at=str(item.get("started_at") or ""),
                    ended_at=str(item.get("ended_at") or ""),
                    duration_ms=int(item.get("duration_ms") or 0),
                    summary=str(item.get("summary") or ""),
                    data=dict(item.get("data") or {}),
                )
            )
        return spans[-limit:]

    def _path(self, session_id: str) -> Path:
        safe = "".join(ch for ch in (session_id or "default") if ch.isalnum() or ch in ("-", "_"))
        return self.storage_dir / f"{safe or 'default'}.jsonl"


def _safe_data(data: dict[str, Any] | None) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in (data or {}).items():
        if any(token in str(key).lower() for token in ("secret", "token", "password", "api_key")):
            safe[str(key)] = "<redacted>"
        elif isinstance(value, (str, int, float, bool)) or value is None:
            safe[str(key)] = value if not (isinstance(value, str) and len(value) > 2000) else value[:2000]
        elif isinstance(value, (list, dict)):
            rendered = json.dumps(value, ensure_ascii=False, default=str)
            safe[str(key)] = rendered[:2000]
        else:
            safe[str(key)] = str(value)[:2000]
    return safe
