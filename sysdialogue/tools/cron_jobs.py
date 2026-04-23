"""工具: manage_cron — 计划任务管理（只调度静态工具/workflow，不接受任意 shell）。"""

from __future__ import annotations

import uuid

from sysdialogue.runtime.secure_runner import SafeExecutor
from sysdialogue.runtime.target_fs import TargetFileAccess
from sysdialogue.tools.base import ToolResult


_SYSDIALOGUE_MARKER = "# sysdialogue:job:"
_SYSTEM_CRON_DIR = "/etc/cron.d"
_VALID_SCOPES = {"user", "system"}


def cron_state_dir(executor: SafeExecutor) -> str:
    fs = TargetFileAccess(executor)
    return fs.join(fs.home_dir(), ".sysdialogue")


def cron_index_path(executor: SafeExecutor) -> str:
    fs = TargetFileAccess(executor)
    return fs.join(cron_state_dir(executor), "cron_index.json")


def load_cron_index(executor: SafeExecutor) -> dict:
    fs = TargetFileAccess(executor)
    index_path = cron_index_path(executor)
    if not fs.exists(index_path):
        return {}
    return fs.read_json(index_path)


def get_cron_job(executor: SafeExecutor, job_id: str) -> dict | None:
    return load_cron_index(executor).get(job_id)


def cron_command(job_id: str) -> str:
    return f"sysdialogue --run-scheduled-job {job_id}"


def system_cron_path(job_id: str) -> str:
    return f"{_SYSTEM_CRON_DIR}/sysdialogue-{job_id}"


def manage_cron(
    executor: SafeExecutor,
    action: str,
    scope: str = "user",
    schedule: str | None = None,
    job_target: dict | None = None,
    job_id: str | None = None,
) -> ToolResult:
    """计划任务管理。"""
    if scope not in _VALID_SCOPES:
        return ToolResult(success=False, error=f"无效 scope: {scope}，仅支持 user/system")
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
    index = load_cron_index(executor)
    if scope == "user":
        cmd = ["crontab", "-l"]
    else:
        cmd = ["ls", "-la", _SYSTEM_CRON_DIR]
    out, code = executor.run(cmd, timeout=5)
    return ToolResult(
        success=(code == 0 or code == 1),
        data={"crontab": out, "managed": index},
        cmd_trace=[" ".join(cmd)],
    )


def _create(executor: SafeExecutor, scope: str, schedule: str | None,
            job_target: dict | None) -> ToolResult:
    if not schedule:
        return ToolResult(success=False, error="create 需要 schedule 参数")
    if not job_target:
        return ToolResult(success=False, error="create 需要 job_target 参数")
    if not _valid_schedule(schedule):
        return ToolResult(success=False, error=f"无效的 cron 表达式：{schedule}")

    kind = job_target.get("kind", "")
    if kind not in ("tool", "workflow"):
        return ToolResult(success=False, error="job_target.kind 只允许 'tool' 或 'workflow'")

    bid = f"job_{uuid.uuid4().hex[:8]}"
    index = load_cron_index(executor)
    index[bid] = {
        "job_id": bid,
        "scope": scope,
        "schedule": schedule,
        "job_target": job_target,
        "enabled": True,
    }
    try:
        _save_and_sync(executor, index, bid)
    except Exception as e:
        index.pop(bid, None)
        _safe_save_index(executor, index)
        return ToolResult(success=False, error=f"计划任务安装失败：{e}")
    return ToolResult(
        success=True,
        data=index[bid],
        cmd_trace=[f"cron create {bid} {scope} {schedule}"],
    )


def _update(executor: SafeExecutor, job_id: str | None, schedule: str | None,
            job_target: dict | None) -> ToolResult:
    if not job_id:
        return ToolResult(success=False, error="update 需要 job_id 参数")
    index = load_cron_index(executor)
    if job_id not in index:
        return ToolResult(success=False, error=f"job_id {job_id} 不存在")
    original = dict(index[job_id])
    if schedule:
        if not _valid_schedule(schedule):
            return ToolResult(success=False, error=f"无效的 cron 表达式：{schedule}")
        index[job_id]["schedule"] = schedule
    if job_target:
        kind = job_target.get("kind", "")
        if kind not in ("tool", "workflow"):
            return ToolResult(success=False, error="job_target.kind 只允许 'tool' 或 'workflow'")
        index[job_id]["job_target"] = job_target
    try:
        _save_and_sync(executor, index, job_id)
    except Exception as e:
        index[job_id] = original
        _safe_save_and_sync(executor, index, job_id)
        return ToolResult(success=False, error=f"计划任务更新失败：{e}")
    return ToolResult(success=True, data=index[job_id], cmd_trace=[f"cron update {job_id}"])


def _modify(executor: SafeExecutor, job_id: str | None, action: str) -> ToolResult:
    if not job_id:
        return ToolResult(success=False, error=f"{action} 需要 job_id 参数")
    index = load_cron_index(executor)
    entry = index.get(job_id)
    if entry is None:
        return ToolResult(success=False, error=f"job_id {job_id} 不存在")

    if action == "delete":
        try:
            _remove_installed_job(executor, entry)
            del index[job_id]
            _save_index(executor, index)
        except Exception as e:
            return ToolResult(success=False, error=f"计划任务删除失败：{e}")
    else:
        original = dict(entry)
        entry["enabled"] = (action == "enable")
        try:
            _save_and_sync(executor, index, job_id)
        except Exception as e:
            index[job_id] = original
            _safe_save_and_sync(executor, index, job_id)
            return ToolResult(success=False, error=f"计划任务 {action} 失败：{e}")
    return ToolResult(success=True, data=f"{action} 成功：{job_id}", cmd_trace=[f"cron {action} {job_id}"])


def _save_index(executor: SafeExecutor, data: dict) -> None:
    fs = TargetFileAccess(executor)
    state_dir = cron_state_dir(executor)
    fs.mkdir(state_dir, parents=True)
    fs.write_json(cron_index_path(executor), data, atomic=True)


def _save_and_sync(executor: SafeExecutor, data: dict, job_id: str) -> None:
    _save_index(executor, data)
    _sync_installed_jobs(executor, data, data[job_id]["scope"])


def _safe_save_index(executor: SafeExecutor, data: dict) -> None:
    try:
        _save_index(executor, data)
    except Exception:
        pass


def _safe_save_and_sync(executor: SafeExecutor, data: dict, job_id: str) -> None:
    try:
        _save_and_sync(executor, data, job_id)
    except Exception:
        pass


def _sync_installed_jobs(executor: SafeExecutor, data: dict, scope: str) -> None:
    if scope not in _VALID_SCOPES:
        raise ValueError(f"invalid cron scope: {scope}")
    if scope == "user":
        _sync_user_crontab(executor, data)
        return
    _sync_system_cron(executor, data)


def _sync_user_crontab(executor: SafeExecutor, data: dict) -> None:
    fs = TargetFileAccess(executor)
    out, code = executor.run(["crontab", "-l"], timeout=5)
    current_lines = [] if code != 0 else out.splitlines()
    filtered = [line for line in current_lines if _SYSDIALOGUE_MARKER not in line]
    managed = []
    for entry in data.values():
        if entry.get("scope") != "user" or not entry.get("enabled", True):
            continue
        managed.append(
            f"{entry['schedule']} {cron_command(entry['job_id'])} {_SYSDIALOGUE_MARKER}{entry['job_id']}"
        )

    rendered_lines = filtered
    if filtered and managed:
        rendered_lines.append("")
    rendered_lines.extend(managed)
    content = "\n".join(line for line in rendered_lines if line is not None).strip()
    if content:
        content += "\n"

    tmp_path = fs.join(cron_state_dir(executor), "user-crontab.tmp")
    fs.mkdir(cron_state_dir(executor), parents=True)
    fs.write_text(tmp_path, content, atomic=True)
    try:
        out_apply, code_apply = executor.run(["crontab", tmp_path], timeout=10)
        if code_apply != 0:
            raise OSError(out_apply or "crontab install failed")
    finally:
        if fs.exists(tmp_path):
            fs.remove(tmp_path)


def _sync_system_cron(executor: SafeExecutor, data: dict) -> None:
    fs = TargetFileAccess(executor)
    enabled_ids = set()
    for entry in data.values():
        if entry.get("scope") != "system":
            continue
        job_id = entry["job_id"]
        cron_path = system_cron_path(job_id)
        if entry.get("enabled", True):
            content = (
                "SHELL=/bin/sh\n"
                "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin\n\n"
                f"{entry['schedule']} root {cron_command(job_id)}\n"
            )
            fs.write_text(cron_path, content, atomic=True)
            fs.chmod(cron_path, 0o644)
            enabled_ids.add(job_id)
        elif fs.exists(cron_path):
            fs.remove(cron_path)

    ls_out, ls_code = executor.run(["ls", "-1", _SYSTEM_CRON_DIR], timeout=5)
    if ls_code != 0:
        return
    for line in ls_out.splitlines():
        if not line.startswith("sysdialogue-job_"):
            continue
        job_id = line.removeprefix("sysdialogue-")
        if job_id not in enabled_ids and job_id not in data:
            fs.remove(f"{_SYSTEM_CRON_DIR}/{line}")


def _remove_installed_job(executor: SafeExecutor, entry: dict) -> None:
    fs = TargetFileAccess(executor)
    if entry.get("scope") == "system":
        cron_path = system_cron_path(entry["job_id"])
        if fs.exists(cron_path):
            fs.remove(cron_path)
        return
    data = load_cron_index(executor)
    data = {jid: item for jid, item in data.items() if jid != entry["job_id"]}
    _sync_user_crontab(executor, data)


def _valid_schedule(s: str) -> bool:
    parts = s.strip().split()
    return len(parts) == 5
