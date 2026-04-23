"""工具: backup_path, replace_in_file."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

from sysdialogue.runtime.secure_runner import SafeExecutor
from sysdialogue.runtime.target_fs import TargetFileAccess
from sysdialogue.tools.base import ToolResult
from sysdialogue.security import path_policies as pp


def _backup_dir(fs: TargetFileAccess) -> str:
    return fs.join(fs.home_dir(), ".sysdialogue", "backups")


def _backup_index_path(fs: TargetFileAccess) -> str:
    return fs.join(_backup_dir(fs), "index.json")


def _load_index(fs: TargetFileAccess) -> dict:
    idx = _backup_index_path(fs)
    if not fs.exists(idx):
        return {}
    return fs.read_json(idx)


def _save_index(fs: TargetFileAccess, data: dict) -> None:
    fs.mkdir(_backup_dir(fs), parents=True)
    fs.write_json(_backup_index_path(fs), data, atomic=True)


def backup_path(
    executor: SafeExecutor,
    action: str,
    path: str | None = None,
    backup_id: str | None = None,
    backup_label: str | None = None,
) -> ToolResult:
    """备份/列出/还原/删除备份。
    """
    fs = TargetFileAccess(executor)
    backups_root = _backup_dir(fs)

    if action == "list":
        index = _load_index(fs)
        if path:
            target_path = fs.expand(path)
            entries = {k: v for k, v in index.items() if v.get("original_path") == target_path}
        else:
            entries = index
        return ToolResult(success=True, data=entries, cmd_trace=[f"target_fs.read_json {_backup_index_path(fs)}"])

    if action == "create":
        if not path:
            return ToolResult(success=False, error="create 需要 path 参数")
        src = fs.expand(path)
        if not fs.exists(src):
            return ToolResult(success=False, error=f"路径不存在：{path}")
        bid = str(uuid.uuid4())[:8]
        ts = datetime.now(timezone.utc).isoformat()
        dest = fs.join(backups_root, bid)
        try:
            fs.mkdir(backups_root, parents=True)
            fs.copy(src, dest, recursive=fs.is_dir(src))
        except Exception as e:
            return ToolResult(success=False, error=str(e))
        index = _load_index(fs)
        index[bid] = {
            "backup_id": bid,
            "original_path": src,
            "created_at": ts,
            "label": backup_label or "",
            "is_dir": fs.is_dir(src),
        }
        _save_index(fs, index)
        return ToolResult(
            success=True,
            data={"backup_id": bid, "path": src, "created_at": ts},
            cmd_trace=[f"target_fs.copy {src} {dest}"],
        )

    if action == "restore":
        if not backup_id:
            return ToolResult(success=False, error="restore 需要 backup_id 参数")
        index = _load_index(fs)
        entry = index.get(backup_id)
        if not entry:
            return ToolResult(success=False, error=f"备份 {backup_id} 不存在")
        src = fs.join(backups_root, backup_id)
        dst = entry["original_path"]
        if pp.matches_critical_edit(dst):
            return ToolResult(success=False, error=f"禁止自动还原关键系统文件 {dst}（B019）")
        try:
            if entry.get("is_dir"):
                if fs.exists(dst):
                    fs.remove(dst, recursive=True)
                fs.copy(src, dst, recursive=True)
            else:
                parent = fs.dirname(dst)
                if parent:
                    fs.mkdir(parent, parents=True)
                if fs.exists(dst) and fs.is_dir(dst):
                    fs.remove(dst, recursive=True)
                fs.copy(src, dst, recursive=False)
        except Exception as e:
            return ToolResult(success=False, error=str(e))
        return ToolResult(
            success=True,
            data={"restored": dst, "from_backup": backup_id},
            cmd_trace=[f"target_fs.copy {src} {dst}"],
        )

    if action == "delete":
        if not backup_id:
            return ToolResult(success=False, error="delete 需要 backup_id 参数")
        index = _load_index(fs)
        entry = index.pop(backup_id, None)
        if not entry:
            return ToolResult(success=False, error=f"备份 {backup_id} 不存在")
        src = fs.join(backups_root, backup_id)
        try:
            if fs.exists(src):
                fs.remove(src, recursive=entry.get("is_dir", False))
        except Exception as e:
            return ToolResult(success=False, error=str(e))
        _save_index(fs, index)
        return ToolResult(
            success=True,
            data=f"备份 {backup_id} 已删除",
            cmd_trace=[f"target_fs.remove {src} recursive={entry.get('is_dir', False)}"],
        )

    return ToolResult(success=False, error=f"未知 action: {action}")


def replace_in_file(
    executor: SafeExecutor,
    path: str,
    search: str,
    replace: str,
    match_type: str = "literal",
    expected_matches: int | None = None,
    max_replacements: int = 1,
    create_backup: bool = True,
    dry_run: bool = False,
) -> ToolResult:
    """精准替换文件内容（literal 或 regex）。

    返回 data 包含 diff_preview 和 actual_matches。
    """
    if pp.has_path_traversal(path):
        return ToolResult(success=False, error="路径包含 .. 组件（B005）")
    if pp.matches_critical_edit(path):
        return ToolResult(success=False, error=f"禁止编辑关键系统文件 {path}（B018）")

    fs = TargetFileAccess(executor)
    target_path = fs.expand(path)
    if not fs.exists(target_path):
        return ToolResult(success=False, error=f"文件不存在：{path}")

    original = fs.read_text(target_path, encoding="utf-8", errors="replace")

    if match_type == "regex":
        pattern = re.compile(search)
        matches = pattern.findall(original)
        count = len(matches)
        new_content = pattern.sub(replace, original, count=max_replacements)
    else:
        count = original.count(search)
        new_content = original.replace(search, replace, max_replacements)

    if expected_matches is not None and count != expected_matches:
        return ToolResult(
            success=False,
            error=f"期望匹配 {expected_matches} 处，实际找到 {count} 处，操作已取消",
            data={"actual_matches": count},
        )

    # 生成 diff 预览（简单行级别）
    diff_lines = []
    orig_lines = original.splitlines()
    new_lines = new_content.splitlines()
    for i, (ol, nl) in enumerate(zip(orig_lines, new_lines)):
        if ol != nl:
            diff_lines.append(f"- {ol}")
            diff_lines.append(f"+ {nl}")
    diff_preview = "\n".join(diff_lines[:40])

    if dry_run:
        return ToolResult(
            success=True,
            data={"dry_run": True, "actual_matches": count, "diff_preview": diff_preview},
        )

    # 实际写入
    if create_backup:
        result = backup_path(
            executor=executor,
            action="create",
            path=target_path,
            backup_label="replace_in_file auto-backup",
        )
        if not result.success:
            return ToolResult(success=False, error=f"备份失败：{result.error}")
        backup_id = result.data.get("backup_id") if result.data else None
    else:
        backup_id = None

    # 原子写入
    try:
        fs.write_text(target_path, new_content, atomic=True)
    except Exception as e:
        return ToolResult(success=False, error=str(e))

    return ToolResult(
        success=True,
        data={
            "actual_matches": count,
            "diff_preview": diff_preview,
            "backup_id": backup_id,
            "risk_level": "WARN-HIGH",
        },
        cmd_trace=[f"target_fs.write_text {target_path}"],
    )
