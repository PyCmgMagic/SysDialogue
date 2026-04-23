"""工具: read_file, write_file, delete_path, create_directory, copy_move_path."""

from __future__ import annotations

from sysdialogue.runtime.secure_runner import SafeExecutor
from sysdialogue.runtime.target_fs import TargetFileAccess
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

    fs = TargetFileAccess(executor)
    target_path = fs.expand(path)
    if not fs.exists(target_path):
        return ToolResult(success=False, error=f"文件不存在：{path}")
    if not fs.is_file(target_path):
        return ToolResult(success=False, error=f"路径不是文件：{path}")

    try:
        content = fs.read_bytes(target_path)
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

        return ToolResult(
            success=True,
            data="\n".join(selected) + note,
            cmd_trace=[f"target_fs.read_bytes {target_path}"],
        )
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

    fs = TargetFileAccess(executor)
    target_path = fs.expand(path)

    if mode == "create_only" and fs.exists(target_path):
        return ToolResult(success=False, error=f"文件已存在（create_only 模式）：{path}")

    if create_backup and fs.exists(target_path):
        from sysdialogue.tools.backup_restore import backup_path
        br = backup_path(
            action="create",
            path=target_path,
            backup_label=backup_label or "write_file auto-backup",
            executor=executor,
        )
        if not br.success:
            return ToolResult(success=False, error=f"备份失败：{br.error}")

    try:
        if mode == "append":
            fs.append_text(target_path, content)
        else:
            fs.write_text(target_path, content, atomic=atomic)
        return ToolResult(
            success=True,
            data=f"已写入 {target_path}",
            cmd_trace=[f"target_fs.write_text {target_path}"],
        )
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

    fs = TargetFileAccess(executor)
    target_path = fs.expand(path)
    if not fs.exists(target_path):
        return ToolResult(success=False, error=f"路径不存在：{path}")

    try:
        fs.remove(target_path, recursive=recursive)
        return ToolResult(
            success=True,
            data=f"已删除：{target_path}",
            cmd_trace=[f"target_fs.remove {target_path} recursive={recursive}"],
        )
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

    try:
        fs = TargetFileAccess(executor)
        target_path = fs.expand(path)
        fs.mkdir(target_path, parents=parents)
        return ToolResult(
            success=True,
            data=f"已创建目录：{target_path}",
            cmd_trace=[f"target_fs.mkdir {target_path}"],
        )
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

    fs = TargetFileAccess(executor)
    src_path = fs.expand(src)
    dst_path = fs.expand(dst)
    if not fs.exists(src_path):
        return ToolResult(success=False, error=f"源路径不存在：{src}")

    try:
        if action == "copy":
            fs.copy(src_path, dst_path, recursive=fs.is_dir(src_path))
            return ToolResult(
                success=True,
                data=f"已拷贝 {src_path} → {dst_path}",
                cmd_trace=[f"target_fs.copy {src_path} {dst_path}"],
            )
        elif action == "move":
            fs.move(src_path, dst_path)
            return ToolResult(
                success=True,
                data=f"已移动 {src_path} → {dst_path}",
                cmd_trace=[f"target_fs.move {src_path} {dst_path}"],
            )
        else:
            return ToolResult(success=False, error=f"未知 action: {action}")
    except Exception as e:
        return ToolResult(success=False, error=str(e))
