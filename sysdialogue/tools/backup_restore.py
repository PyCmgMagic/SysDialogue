"""工具: backup_path, replace_in_file."""

from __future__ import annotations

import json
import os
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sysdialogue.tools.base import ToolResult
from sysdialogue.security import path_policies as pp

_BACKUP_DIR = Path(os.path.expanduser("~/.sysdialogue/backups"))


def _backup_index_path() -> Path:
    _BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    return _BACKUP_DIR / "index.json"


def _load_index() -> dict:
    idx = _backup_index_path()
    if not idx.exists():
        return {}
    with open(idx, encoding="utf-8") as f:
        return json.load(f)


def _save_index(data: dict) -> None:
    with open(_backup_index_path(), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def backup_path(
    action: str,
    path: str | None = None,
    backup_id: str | None = None,
    backup_label: str | None = None,
) -> ToolResult:
    """备份/列出/还原/删除备份。

    注意：此工具不需要 executor，直接操作本机文件系统。
    远程模式需改为通过 executor 实现（此版本仅支持本地）。
    """
    if action == "list":
        index = _load_index()
        if path:
            entries = {k: v for k, v in index.items() if v.get("original_path") == path}
        else:
            entries = index
        return ToolResult(success=True, data=entries)

    if action == "create":
        if not path:
            return ToolResult(success=False, error="create 需要 path 参数")
        src = Path(path)
        if not src.exists():
            return ToolResult(success=False, error=f"路径不存在：{path}")
        bid = str(uuid.uuid4())[:8]
        ts = datetime.now(timezone.utc).isoformat()
        dest = _BACKUP_DIR / bid
        try:
            if src.is_dir():
                shutil.copytree(str(src), str(dest))
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(src), str(dest))
        except Exception as e:
            return ToolResult(success=False, error=str(e))
        index = _load_index()
        index[bid] = {
            "backup_id": bid,
            "original_path": str(src.resolve()),
            "created_at": ts,
            "label": backup_label or "",
            "is_dir": src.is_dir(),
        }
        _save_index(index)
        return ToolResult(success=True, data={"backup_id": bid, "path": path, "created_at": ts})

    if action == "restore":
        if not backup_id:
            return ToolResult(success=False, error="restore 需要 backup_id 参数")
        index = _load_index()
        entry = index.get(backup_id)
        if not entry:
            return ToolResult(success=False, error=f"备份 {backup_id} 不存在")
        src = _BACKUP_DIR / backup_id
        dst = Path(entry["original_path"])
        if pp.matches_critical_edit(str(dst)):
            return ToolResult(success=False, error=f"禁止自动还原关键系统文件 {dst}（B019）")
        try:
            if entry.get("is_dir"):
                if dst.exists():
                    shutil.rmtree(str(dst))
                shutil.copytree(str(src), str(dst))
            else:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(src), str(dst))
        except Exception as e:
            return ToolResult(success=False, error=str(e))
        return ToolResult(success=True, data={"restored": str(dst), "from_backup": backup_id})

    if action == "delete":
        if not backup_id:
            return ToolResult(success=False, error="delete 需要 backup_id 参数")
        index = _load_index()
        entry = index.pop(backup_id, None)
        if not entry:
            return ToolResult(success=False, error=f"备份 {backup_id} 不存在")
        src = _BACKUP_DIR / backup_id
        try:
            if src.is_dir():
                shutil.rmtree(str(src))
            elif src.exists():
                src.unlink()
        except Exception as e:
            return ToolResult(success=False, error=str(e))
        _save_index(index)
        return ToolResult(success=True, data=f"备份 {backup_id} 已删除")

    return ToolResult(success=False, error=f"未知 action: {action}")


def replace_in_file(
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

    p = Path(path)
    if not p.exists():
        return ToolResult(success=False, error=f"文件不存在：{path}")

    original = p.read_text(encoding="utf-8", errors="replace")

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
        result = backup_path("create", path=path, backup_label="replace_in_file auto-backup")
        if not result.success:
            return ToolResult(success=False, error=f"备份失败：{result.error}")
        backup_id = result.data.get("backup_id") if result.data else None
    else:
        backup_id = None

    # 原子写入
    tmp = str(p) + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(new_content)
        os.replace(tmp, str(p))
    except Exception as e:
        if os.path.exists(tmp):
            os.unlink(tmp)
        return ToolResult(success=False, error=str(e))

    return ToolResult(
        success=True,
        data={
            "actual_matches": count,
            "diff_preview": diff_preview,
            "backup_id": backup_id,
            "risk_level": "WARN-HIGH",
        },
    )
