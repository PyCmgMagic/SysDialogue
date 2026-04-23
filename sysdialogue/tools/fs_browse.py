"""工具: list_directory, stat_path, search_file_content."""

from __future__ import annotations

import hashlib
import os
import stat as stat_mod

from sysdialogue.runtime.secure_runner import SafeExecutor
from sysdialogue.tools.base import ToolResult
from sysdialogue.security import path_policies as pp


def list_directory(
    executor: SafeExecutor,
    path: str = ".",
    recursive: bool = False,
    max_depth: int = 1,
    include_hidden: bool = False,
    max_entries: int = 200,
    sort_by: str = "name",
) -> ToolResult:
    """列出目录内容（不依赖 executor 直接在本地读取，兼容远程模式回退到 ls）。"""
    if pp.has_path_traversal(path):
        return ToolResult(success=False, error="路径包含 .. 组件（B005）")
    if pp.matches_sensitive_dir(path):
        return ToolResult(success=False, error=f"禁止枚举敏感凭证目录 {path}（B031）")

    # 优先用 ls（兼容远程模式）
    cmd = ["ls", "-la" if include_hidden else "-l"]
    if recursive:
        cmd.append("-R")
    cmd.append(path)
    out, code = executor.run(cmd, timeout=15)
    if code != 0:
        return ToolResult(success=False, error=out)

    lines = out.splitlines()
    if len(lines) > max_entries + 5:
        truncated_note = f"\n[已截断，显示前 {max_entries} 条，共 {len(lines)} 条]"
        lines = lines[: max_entries + 5]
        out = "\n".join(lines) + truncated_note

    return ToolResult(success=True, data=out, cmd_trace=[" ".join(cmd)])


def stat_path(
    executor: SafeExecutor,
    path: str,
    follow_symlink: bool = True,
    with_hash: bool = False,
    hash_algo: str = "sha256",
) -> ToolResult:
    """获取文件/目录元数据，可选哈希。"""
    if pp.has_path_traversal(path):
        return ToolResult(success=False, error="路径包含 .. 组件（B005）")

    cmd = ["stat", path]
    out, code = executor.run(cmd, timeout=5)
    if code != 0:
        return ToolResult(success=False, error=out)

    data: dict = {"stat": out}

    if with_hash:
        algo = hash_algo.lower() if hash_algo in ("md5", "sha1", "sha256", "sha512") else "sha256"
        cmd_hash = [f"{algo}sum", path]
        out_hash, code_hash = executor.run(cmd_hash, timeout=30)
        if code_hash == 0:
            data["hash"] = {"algo": algo, "value": out_hash.split()[0] if out_hash else ""}

    return ToolResult(success=True, data=data, cmd_trace=[" ".join(cmd)])


def search_file_content(
    executor: SafeExecutor,
    search_path: str,
    pattern: str,
    file_glob: str = "*",
    regex: bool = False,
    case_sensitive: bool = True,
    max_matches: int = 50,
) -> ToolResult:
    """在文件内容中搜索文本（grep）。"""
    if pp.has_path_traversal(search_path):
        return ToolResult(success=False, error="路径包含 .. 组件（B005）")
    if pp.matches_sensitive_credential(search_path):
        return ToolResult(success=False, error=f"禁止检索凭证文件 {search_path}（B025）")
    if pp.matches_v41_block(search_path):
        return ToolResult(success=False, error=f"禁止检索敏感路径 {search_path}（B025）")

    cmd = ["grep"]
    if not case_sensitive:
        cmd.append("-i")
    if regex:
        cmd.append("-E")
    else:
        cmd.append("-F")
    cmd += ["-r", "--include", file_glob, "-m", str(max_matches), pattern, search_path]

    out, code = executor.run(cmd, timeout=30)
    if code == 1 and not out:
        return ToolResult(success=True, data="（无匹配）", cmd_trace=[" ".join(cmd)])
    if code > 1:
        return ToolResult(success=False, error=out, cmd_trace=[" ".join(cmd)])
    return ToolResult(success=True, data=out, cmd_trace=[" ".join(cmd)])
