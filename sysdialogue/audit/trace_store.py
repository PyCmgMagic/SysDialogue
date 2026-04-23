"""AuditLog — JSONL 审计日志，记录 command_trace / decision_trace / env_profile_id。"""

from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class AuditLog:
    """线程安全的 JSONL 审计日志。

    每个 session 一个文件，路径：~/.sysdialogue/audit/<session_id>.jsonl
    """

    def __init__(self, session_id: str | None = None, log_dir: str | None = None):
        self.session_id = session_id or str(uuid.uuid4())[:8]
        _dir = Path(log_dir or os.path.expanduser("~/.sysdialogue/audit"))
        _dir.mkdir(parents=True, exist_ok=True)
        self._path = _dir / f"{self.session_id}.jsonl"
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # 公共写入接口
    # ------------------------------------------------------------------

    def log_decision(
        self,
        tool: str,
        args: dict,
        risk_level: str,
        rule_ids: list[str],
        reason: str,
        decision: str,         # "SAFE" | "WARN-LOW" | "WARN-HIGH" | "BLOCK" | "user_cancelled"
        plan_id: str | None = None,
        workflow_id: str | None = None,
        env_profile_id: str | None = None,
    ) -> str:
        entry_id = self._write({
            "type": "decision",
            "tool": tool,
            "args": args,
            "risk_level": risk_level,
            "rule_ids": rule_ids,
            "reason": reason,
            "decision": decision,
            "plan_id": plan_id,
            "workflow_id": workflow_id,
            "env_profile_id": env_profile_id,
        })
        return entry_id

    def log_command(
        self,
        tool: str,
        cmd: list[str],
        exit_code: int,
        output_preview: str,
        plan_id: str | None = None,
        workflow_id: str | None = None,
    ) -> str:
        entry_id = self._write({
            "type": "command_trace",
            "tool": tool,
            "cmd": cmd,
            "exit_code": exit_code,
            "output_preview": output_preview[:1024] if output_preview else "",
            "plan_id": plan_id,
            "workflow_id": workflow_id,
        })
        return entry_id

    def log_workflow_step(
        self,
        workflow_id: str,
        step_id: str,
        step_type: str,
        status: str,         # "started" | "completed" | "failed" | "skipped" | "rolled_back"
        detail: Any = None,
    ) -> str:
        entry_id = self._write({
            "type": "workflow_step",
            "workflow_id": workflow_id,
            "step_id": step_id,
            "step_type": step_type,
            "status": status,
            "detail": detail,
        })
        return entry_id

    def log_env_profile(self, profile: dict) -> str:
        env_id = str(uuid.uuid4())[:8]
        self._write({
            "type": "env_profile",
            "env_profile_id": env_id,
            "profile": profile,
        })
        return env_id

    def log_final(
        self,
        plan_id: str | None = None,
        workflow_id: str | None = None,
        final_status: str = "completed",  # "completed"|"rolled_back"|"failed"|"rollback_failed"
        detail: str = "",
    ) -> None:
        self._write({
            "type": "final",
            "plan_id": plan_id,
            "workflow_id": workflow_id,
            "final_status": final_status,
            "detail": detail,
        })

    # ------------------------------------------------------------------

    def _write(self, data: dict) -> str:
        entry_id = str(uuid.uuid4())[:8]
        record = {
            "id": entry_id,
            "session_id": self.session_id,
            "ts": datetime.now(timezone.utc).isoformat(),
            **data,
        }
        line = json.dumps(record, ensure_ascii=False)
        with self._lock:
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        return entry_id

    def read_all(self) -> list[dict]:
        if not self._path.exists():
            return []
        records = []
        with open(self._path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        return records

    @property
    def path(self) -> Path:
        return self._path
