"""工具: read_file, write_file, delete_path, create_directory, copy_move_path."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from sysdialogue.runtime.secure_runner import SafeExecutor
from sysdialogue.tools.base import ToolResult
from sysdialogue.security import path_policies as pp

MAX_BYTES = 8192


def read_file(
    executor: SafeExecutor,
    path: str,
    mode: str = "head",
    start_line: int | None = None,
    end_line: int | None = None,
    head_lines: int = 50,
    tail_lines: int = 50,
    max_bytes: int = MAX_BYTES,
) -> ToolResult:
    """读取文件内容（head/tail/range 模式）。"""
    if pp.has_path_traversal(path):
        return ToolResult(success=False, error="路径包含 .. 组件（B005）")
    if pp.matches_sensitive_credential(path):
        return ToolResult(success=False, error=f"禁止读取凭证文件 {path}（B011）")

    p = Path(path)
    if not p.exists():
        return ToolResult(success=False, error=f"文件不存在：{path}")
    if not p.is_file():
        return ToolResult(success=False, error=f"路径不是文件：{path}")

    try:
        content = p.read_bytes()
        if len(content) > max_bytes:
            text = content[:max_bytes].decode("utf-8", errors="replace")
            note = f"\n[内容已截断，显示前 {max_bytes} 字节，总计 {len(content)} 字节]"
        else:
            text = content.decode("utf-8", errors="replace")
            note = ""

        lines = text.splitlines()

        if mode == "range" and start_line is not None and end_line is not None:
            selected = lines[start_line - 1: end_line]
        elif mode == "tail":
            selected = lines[-tail_lines:]
        else:  # head
            selected = lines[:head_lines]

        return ToolResult(success=True, data="\n".join(selected) + note)
    except Exception as e:
        return ToolResult(success=False, error=str(e))


def write_file(
    executor: SafeExecutor,
    path: str,
    content: str,
    mode: str = "overwrite",
    atomic: bool = True,
    create_backup: bool = False,
    backup_label: str = "",
) -> ToolResult:
    """写入文件（overwrite/append/create_only）。"""
    if pp.has_path_traversal(path):
        return ToolResult(success=False, error="路径包含 .. 组件（B005）")
    if pp.matches_critical_edit(path):
        return ToolResult(success=False, error=f"禁止写入关键系统文件 {path}（B012）")

    p = Path(path)

    if mode == "create_only" and p.exists():
        return ToolResult(success=False, error=f"文件已存在（create_only 模式）：{path}")

    if create_backup and p.exists():
        from sysdialogue.tools.backup_restore import backup_path
        br = backup_path("create", path=path, backup_label=backup_label or "write_file auto-backup")
        if not br.success:
            return ToolResult(success=False, error=f"备份失败：{br.error}")

    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        if mode == "append":
            with open(p, "a", encoding="utf-8") as f:
                f.write(content)
        elif atomic:
            tmp = str(p) + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(content)
            os.replace(tmp, str(p))
        else:
            p.write_text(content, encoding="utf-8")
        return ToolResult(success=True, data=f"已写入 {path}")
    except Exception as e:
        return ToolResult(success=False, error=str(e))


def delete_path(
    executor: SafeExecutor,
    path: str,
    recursive: bool = False,
) -> ToolResult:
    """删除文件或目录。"""
    if pp.has_path_traversal(path):
        return ToolResult(success=False, error="路径包含 .. 组件（B005）")
    if pp.matches_v41_block(path):
        return ToolResult(success=False, error=f"禁止删除敏感路径 {path}（B014）")
    if recursive:
        for root_path in ["/", "/etc", "/usr", "/boot", "/lib", "/bin", "/sbin"]:
            if pp.normalize(path) == pp.normalize(root_path):
                return ToolResult(success=False, error=f"禁止递归删除系统目录 {path}（B013）")

    p = Path(path)
    if not p.exists():
        return ToolResult(success=False, error=f"路径不存在：{path}")

    try:
        if p.is_dir() and recursive:
            shutil.rmtree(str(p))
        elif p.is_dir():
            p.rmdir()
        else:
            p.unlink()
        return ToolResult(success=True, data=f"已删除：{path}")
    except Exception as e:
        return ToolResult(success=False, error=str(e))


def create_directory(
    executor: SafeExecutor,
    path: str,
    parents: bool = True,
) -> ToolResult:
    """创建目录。"""
    if pp.has_path_traversal(path):
        return ToolResult(success=False, error="路径包含 .. 组件（B005）")

    p = Path(path)
    try:
        p.mkdir(parents=parents, exist_ok=True)
        return ToolResult(success=True, data=f"已创建目录：{path}")
    except Exception as e:
        return ToolResult(success=False, error=str(e))


def copy_move_path(
    executor: SafeExecutor,
    src: str,
    dst: str,
    action: str = "copy",
) -> ToolResult:
    """拷贝或移动文件/目录。"""
    if pp.has_path_traversal(src) or pp.has_path_traversal(dst):
        return ToolResult(success=False, error="路径包含 .. 组件（B005）")

    s = Path(src)
    if not s.exists():
        return ToolResult(success=False, error=f"源路径不存在：{src}")

    try:
        if action == "copy":
            if s.is_dir():
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)
            return ToolResult(success=True, data=f"已拷贝 {src} → {dst}")
        elif action == "move":
            shutil.move(src, dst)
            return ToolResult(success=True, data=f"已移动 {src} → {dst}")
        else:
            return ToolResult(success=False, error=f"未知 action: {action}")
    except Exception as e:
        return ToolResult(success=False, error=str(e))
