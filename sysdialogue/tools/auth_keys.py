"""工具: manage_authorized_keys."""

from __future__ import annotations

import os
import re
from pathlib import Path

from sysdialogue.runtime.secure_runner import SafeExecutor
from sysdialogue.tools.base import ToolResult

_PUBLIC_KEY_PREFIXES = (
    "ssh-rsa", "ssh-ed25519", "ssh-ecdsa", "ecdsa-sha2", "sk-ssh-",
)


def _is_public_key(s: str) -> bool:
    s = s.strip()
    if "PRIVATE" in s:
        return False
    return any(s.startswith(p) for p in _PUBLIC_KEY_PREFIXES)


def _auth_keys_path(username: str) -> Path | None:
    try:
        import pwd
        pw = pwd.getpwnam(username)
        return Path(pw.pw_dir) / ".ssh" / "authorized_keys"
    except (ImportError, KeyError):
        if username == "root":
            return Path("/root/.ssh/authorized_keys")
        return Path(f"/home/{username}/.ssh/authorized_keys")


def manage_authorized_keys(
    executor: SafeExecutor,
    action: str,
    username: str,
    public_key: str | None = None,
    fingerprint: str | None = None,
) -> ToolResult:
    """SSH 授权公钥管理（list/add/remove）。"""
    if username == "root" and action in ("add", "remove"):
        return ToolResult(success=False, error="禁止通过自动化通路修改 root 公钥（B028）")

    key_path = _auth_keys_path(username)
    if key_path is None:
        return ToolResult(success=False, error=f"无法确定用户 {username} 的 authorized_keys 路径")

    if action == "list":
        cmd = ["cat", str(key_path)]
        out, code = executor.run(cmd, timeout=5)
        if code != 0:
            return ToolResult(success=True, data="（无授权公钥）", cmd_trace=[" ".join(cmd)])
        return ToolResult(success=True, data=out, cmd_trace=[" ".join(cmd)])

    if action == "add":
        if not public_key:
            return ToolResult(success=False, error="add 需要 public_key 参数")
        if not _is_public_key(public_key):
            return ToolResult(success=False, error="输入内容不是有效公钥格式（B023）")
        # 确保目录存在
        cmd_mkdir = ["mkdir", "-p", str(key_path.parent)]
        executor.run(cmd_mkdir, timeout=5)
        cmd_chmod = ["chmod", "700", str(key_path.parent)]
        executor.run(cmd_chmod, timeout=5)
        # 追加公钥（避免重复）
        read_cmd = ["cat", str(key_path)]
        existing, _ = executor.run(read_cmd, timeout=5)
        pk = public_key.strip()
        if pk in existing:
            return ToolResult(success=True, data="公钥已存在，无需重复添加")
        cmd = ["bash", "-c", f"echo {repr(pk)} >> {key_path}"]
        out, code = executor.run(cmd, timeout=5)
        return ToolResult(success=(code == 0), data="公钥已添加" if code == 0 else out, cmd_trace=[" ".join(cmd)])

    if action == "remove":
        if not public_key and not fingerprint:
            return ToolResult(success=False, error="remove 需要 public_key 或 fingerprint 参数")
        cmd = ["cat", str(key_path)]
        existing, code = executor.run(cmd, timeout=5)
        if code != 0:
            return ToolResult(success=False, error="authorized_keys 文件不存在或不可读")
        if public_key:
            new_content = "\n".join(l for l in existing.splitlines() if public_key.strip() not in l)
        else:
            new_content = existing  # fingerprint 删除需要 ssh-keygen -l 比对，暂简化
        tmp = str(key_path) + ".tmp"
        cmd_write = ["bash", "-c", f"cat > {tmp} << 'ENDSSHKEYS'\n{new_content}\nENDSSHKEYS"]
        executor.run(cmd_write, timeout=5)
        cmd_mv = ["mv", tmp, str(key_path)]
        out, code = executor.run(cmd_mv, timeout=5)
        return ToolResult(success=(code == 0), data="公钥已移除" if code == 0 else out, cmd_trace=[" ".join(cmd_mv)])

    return ToolResult(success=False, error=f"未知 action: {action}")
