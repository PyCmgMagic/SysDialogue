"""工具: manage_cron — 计划任务管理（只调度静态工具/workflow，不接受任意 shell）。"""

from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path

from sysdialogue.runtime.secure_runner import SafeExecutor
from sysdialogue.tools.base import ToolResult

_CRON_INDEX = Path(os.path.expanduser("~/.sysdialogue/cron_index.json"))
_CRON_DIR = Path("/etc/cron.d")


def _load_index() -> dict:
    if not _CRON_INDEX.exists():
        return {}
    with open(_CRON_INDEX, encoding="utf-8") as f:
        return json.load(f)


def _save_index(data: dict) -> None:
    _CRON_INDEX.parent.mkdir(parents=True, exist_ok=True)
    with open(_CRON_INDEX, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def manage_cron(
    executor: SafeExecutor,
    action: str,
    scope: str = "user",
    schedule: str | None = None,
    job_target: dict | None = None,
    job_id: str | None = None,
) -> ToolResult:
    """计划任务管理。"""
    if action == "list":
        return _list(executor, scope)
    if action == "create":
        return _create(executor, scope, schedule, job_target)
    if action == "update":
        return _update(executor, job_id, schedule, job_target)
    if action in ("delete", "enable", "disable"):
        return _modify(executor, job_id, action)
    return ToolResult(success=False, error=f"未知 action: {action}")


def _list(executor: SafeExecutor, scope: str) -> ToolResult:
    index = _load_index()
    if scope == "user":
        cmd = ["crontab", "-l"]
    else:
        cmd = ["ls", "-la", str(_CRON_DIR)]
    out, code = executor.run(cmd, timeout=5)
    return ToolResult(success=(code == 0 or code == 1), data={"crontab": out, "managed": index}, cmd_trace=[" ".join(cmd)])


def _create(executor: SafeExecutor, scope: str, schedule: str | None, job_target: dict | None) -> ToolResult:
    if not schedule:
        return ToolResult(success=False, error="create 需要 schedule 参数")
    if not job_target:
        return ToolResult(success=False, error="create 需要 job_target 参数")
    if not _valid_schedule(schedule):
        return ToolResult(success=False, error=f"无效的 cron 表达式：{schedule}")

    kind = job_target.get("kind", "")
    if kind not in ("tool", "workflow"):
        return ToolResult(success=False, error="job_target.kind 只允许 'tool' 或 'workflow'")

    bid = str(uuid.uuid4())[:8]
    index = _load_index()
    index[bid] = {
        "job_id": bid,
        "scope": scope,
        "schedule": schedule,
        "job_target": job_target,
        "enabled": True,
    }
    _save_index(index)
    return ToolResult(success=True, data={"job_id": bid, "schedule": schedule, "job_target": job_target})


def _update(executor: SafeExecutor, job_id: str | None, schedule: str | None, job_target: dict | None) -> ToolResult:
    if not job_id:
        return ToolResult(success=False, error="update 需要 job_id 参数")
    index = _load_index()
    if job_id not in index:
        return ToolResult(success=False, error=f"job_id {job_id} 不存在")
    if schedule:
        if not _valid_schedule(schedule):
            return ToolResult(success=False, error=f"无效的 cron 表达式：{schedule}")
        index[job_id]["schedule"] = schedule
    if job_target:
        kind = job_target.get("kind", "")
        if kind not in ("tool", "workflow"):
            return ToolResult(success=False, error="job_target.kind 只允许 'tool' 或 'workflow'")
        index[job_id]["job_target"] = job_target
    _save_index(index)
    return ToolResult(success=True, data=index[job_id])


def _modify(executor: SafeExecutor, job_id: str | None, action: str) -> ToolResult:
    if not job_id:
        return ToolResult(success=False, error=f"{action} 需要 job_id 参数")
    index = _load_index()
    if job_id not in index:
        return ToolResult(success=False, error=f"job_id {job_id} 不存在")
    if action == "delete":
        del index[job_id]
    elif action == "enable":
        index[job_id]["enabled"] = True
    elif action == "disable":
        index[job_id]["enabled"] = False
    _save_index(index)
    return ToolResult(success=True, data=f"{action} 成功：{job_id}")


def _valid_schedule(s: str) -> bool:
    parts = s.strip().split()
    return len(parts) == 5
