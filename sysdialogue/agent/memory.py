"""Layered persistent memory for reusable agent context."""

from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*([^\s]+)"),
    re.compile(r"(?i)bearer\s+[a-z0-9._\-]+"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
]


@dataclass
class MemoryRecord:
    memory_id: str
    scope: str
    key: str
    value: str
    source: str = "manual"
    target_id: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class MemoryManager:
    """Markdown/JSON memory store without embeddings.

    Scopes:
    - global: user/runtime preferences
    - target: remembered facts about a host/target
    - project: reusable project facts
    - session: compacted conversation summaries
    """

    def __init__(self, storage_dir: str | None = None):
        self.storage_dir = Path(storage_dir or os.path.expanduser("~/.sysdialogue/memory"))
        self.index_path = self.storage_dir / "index.json"
        self.storage_dir.mkdir(parents=True, exist_ok=True)

    def remember(
        self,
        *,
        scope: str,
        key: str,
        value: str,
        source: str = "manual",
        target_id: str = "",
    ) -> MemoryRecord:
        records = self._load()
        sanitized_value = redact_sensitive(value)
        existing = next(
            (
                record
                for record in records
                if record.scope == scope and record.key == key and record.target_id == target_id
            ),
            None,
        )
        if existing is None:
            existing = MemoryRecord(
                memory_id=f"mem_{uuid.uuid4().hex[:8]}",
                scope=scope,
                key=key,
                value=sanitized_value,
                source=source,
                target_id=target_id,
            )
            records.append(existing)
        else:
            existing.value = sanitized_value
            existing.source = source
            existing.updated_at = datetime.now(timezone.utc).isoformat()
        self._save(records)
        return existing

    def list_records(self, *, scope: str | None = None, target_id: str = "", limit: int = 50) -> list[MemoryRecord]:
        records = self._load()
        if scope:
            records = [record for record in records if record.scope == scope]
        if target_id:
            records = [record for record in records if record.target_id == target_id]
        records.sort(key=lambda record: record.updated_at, reverse=True)
        return records[:limit]

    def render_prompt_summary(self, *, target_id: str = "", limit: int = 12) -> str:
        records = self.list_records(target_id=target_id, limit=limit)
        if not records:
            return "Layered memory: no persisted reusable facts yet."
        lines = ["[Layered Memory]"]
        for record in records:
            prefix = f"{record.scope}"
            if record.target_id:
                prefix += f":{record.target_id}"
            lines.append(f"- {prefix} {record.key}: {record.value}")
        return "\n".join(lines)

    def compact_session(self, *, session_id: str, summary: str) -> MemoryRecord:
        return self.remember(
            scope="session",
            key=f"summary:{session_id}",
            value=summary,
            source="compact",
        )

    def _load(self) -> list[MemoryRecord]:
        if not self.index_path.exists():
            return []
        try:
            data = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        records: list[MemoryRecord] = []
        for item in data.get("records") or []:
            if not isinstance(item, dict):
                continue
            records.append(
                MemoryRecord(
                    memory_id=str(item.get("memory_id") or f"mem_{uuid.uuid4().hex[:8]}"),
                    scope=str(item.get("scope") or "global"),
                    key=str(item.get("key") or ""),
                    value=str(item.get("value") or ""),
                    source=str(item.get("source") or "manual"),
                    target_id=str(item.get("target_id") or ""),
                    created_at=str(item.get("created_at") or datetime.now(timezone.utc).isoformat()),
                    updated_at=str(item.get("updated_at") or datetime.now(timezone.utc).isoformat()),
                )
            )
        return records

    def _save(self, records: list[MemoryRecord]) -> None:
        payload = {"records": [asdict(record) for record in records]}
        tmp = self.index_path.with_suffix(self.index_path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.index_path)
        _write_markdown(self.storage_dir / "MEMORY.md", records)


def redact_sensitive(value: str) -> str:
    text = str(value or "")
    for pattern in SECRET_PATTERNS:
        text = pattern.sub(lambda match: f"{match.group(1) if match.groups() else 'secret'}=<redacted>", text)
    return text


def _write_markdown(path: Path, records: list[MemoryRecord]) -> None:
    lines = ["# SysDialogue Memory", ""]
    for record in sorted(records, key=lambda item: (item.scope, item.target_id, item.key)):
        target = f" ({record.target_id})" if record.target_id else ""
        lines.append(f"## {record.scope}{target} / {record.key}")
        lines.append("")
        lines.append(record.value)
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
