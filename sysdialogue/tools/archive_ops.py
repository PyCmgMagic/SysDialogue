"""工具: manage_archive."""

from __future__ import annotations

from sysdialogue.runtime.secure_runner import SafeExecutor
from sysdialogue.tools.base import ToolResult
from sysdialogue.security import path_policies as pp

MAX_EXTRACT_FILES = 10_000
MAX_EXTRACT_BYTES = 2 * 1024 * 1024 * 1024  # 2 GB


def manage_archive(
    executor: SafeExecutor,
    action: str,
    archive_path: str,
    source_path: str | None = None,
    target_path: str | None = None,
    format: str = "auto",
    strip_components: int = 0,
) -> ToolResult:
    """归档压缩管理（list/create/extract）。"""
    if action == "list":
        cmd = _list_cmd(archive_path, format)
        if not cmd:
            return ToolResult(success=False, error="无法识别归档格式")
        out, code = executor.run(cmd, timeout=30)
        return ToolResult(success=(code == 0), data=out, cmd_trace=[" ".join(cmd)])

    if action == "create":
        if not source_path:
            return ToolResult(success=False, error="create 需要 source_path 参数")
        cmd = _create_cmd(archive_path, source_path, format)
        if not cmd:
            return ToolResult(success=False, error="无法识别归档格式")
        out, code = executor.run(cmd, timeout=120)
        return ToolResult(success=(code == 0), data=out or f"已创建 {archive_path}", cmd_trace=[" ".join(cmd)])

    if action == "extract":
        if not target_path:
            return ToolResult(success=False, error="extract 需要 target_path 参数")
        if pp.matches_archive_block(target_path):
            return ToolResult(success=False, error=f"禁止解压到系统目录 {target_path}（B021）")
        # 先 list 检查条目安全性
        list_cmd = _list_cmd(archive_path, format)
        if list_cmd:
            list_out, _ = executor.run(list_cmd, timeout=10)
            issue = _check_archive_entries(list_out, target_path)
            if issue:
                return ToolResult(success=False, error=f"归档安全检查失败：{issue}（B027）")

        cmd = _extract_cmd(archive_path, target_path, format, strip_components)
        if not cmd:
            return ToolResult(success=False, error="无法识别归档格式")
        out, code = executor.run(cmd, timeout=120)
        return ToolResult(success=(code == 0), data=out or f"已解压到 {target_path}", cmd_trace=[" ".join(cmd)])

    return ToolResult(success=False, error=f"未知 action: {action}")


def _detect_format(path: str) -> str:
    p = path.lower()
    if p.endswith(".tar.gz") or p.endswith(".tgz"):
        return "tar.gz"
    if p.endswith(".tar"):
        return "tar"
    if p.endswith(".zip"):
        return "zip"
    return "tar.gz"


def _list_cmd(archive_path: str, fmt: str) -> list[str] | None:
    if fmt == "auto":
        fmt = _detect_format(archive_path)
    if fmt in ("tar", "tar.gz"):
        flag = "tzf" if fmt == "tar.gz" else "tf"
        return ["tar", f"-{flag}", archive_path]
    if fmt == "zip":
        return ["unzip", "-l", archive_path]
    return None


def _create_cmd(archive_path: str, source_path: str, fmt: str) -> list[str] | None:
    if fmt == "auto":
        fmt = _detect_format(archive_path)
    if fmt == "tar.gz":
        return ["tar", "-czf", archive_path, source_path]
    if fmt == "tar":
        return ["tar", "-cf", archive_path, source_path]
    if fmt == "zip":
        return ["zip", "-r", archive_path, source_path]
    return None


def _extract_cmd(archive_path: str, target_path: str, fmt: str, strip: int) -> list[str] | None:
    if fmt == "auto":
        fmt = _detect_format(archive_path)
    if fmt in ("tar", "tar.gz"):
        flag = "xzf" if fmt == "tar.gz" else "xf"
        cmd = ["tar", f"-{flag}", archive_path, "-C", target_path]
        if strip:
            cmd += [f"--strip-components={strip}"]
        return cmd
    if fmt == "zip":
        return ["unzip", "-o", archive_path, "-d", target_path]
    return None


def _check_archive_entries(listing: str, target_path: str) -> str | None:
    """检查归档条目是否有安全问题。"""
    import os
    target = pp.normalize(target_path)
    for line in listing.splitlines():
        entry = line.strip().split()[-1] if line.strip() else ""
        if not entry:
            continue
        if entry.startswith("/"):
            return f"归档包含绝对路径条目：{entry}"
        if ".." in entry.split("/"):
            return f"归档包含路径穿越条目：{entry}"
        resolved = os.path.normpath(os.path.join(target, entry))
        if not resolved.startswith(target):
            return f"归档条目逃逸目标目录：{entry}"
    return None
