"""工具: manage_authorized_keys."""

from __future__ import annotations

import base64
import hashlib
from sysdialogue.runtime.secure_runner import SafeExecutor
from sysdialogue.runtime.target_fs import TargetFileAccess
from sysdialogue.tools.base import ToolResult

_PUBLIC_KEY_PREFIXES = (
    "ssh-rsa", "ssh-ed25519", "ssh-ecdsa", "ecdsa-sha2", "sk-ssh-",
)


def _is_public_key(s: str) -> bool:
    s = s.strip()
    if "PRIVATE" in s:
        return False
    return any(s.startswith(p) for p in _PUBLIC_KEY_PREFIXES)


def _public_key_fingerprint(public_key: str) -> str | None:
    try:
        parts = public_key.strip().split()
        if len(parts) < 2:
            return None
        blob = base64.b64decode(parts[1].encode("ascii"))
        digest = hashlib.sha256(blob).digest()
        encoded = base64.b64encode(digest).decode("ascii").rstrip("=")
        return f"SHA256:{encoded}"
    except Exception:
        return None


def _auth_keys_path(executor: SafeExecutor, username: str) -> str:
    out, code = executor.run(["getent", "passwd", username], timeout=5)
    if code == 0 and out:
        entry = out.splitlines()[0].split(":")
        if len(entry) >= 6 and entry[5]:
            return f"{entry[5]}/.ssh/authorized_keys"
    if username == "root":
        return "/root/.ssh/authorized_keys"
    return f"/home/{username}/.ssh/authorized_keys"


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

    fs = TargetFileAccess(executor)
    key_path = _auth_keys_path(executor, username)
    key_dir = fs.dirname(key_path)

    if action == "list":
        if not fs.exists(key_path):
            return ToolResult(success=True, data="（无授权公钥）", cmd_trace=[f"target_fs.read_text {key_path}"])
        out = fs.read_text(key_path, encoding="utf-8", errors="replace")
        return ToolResult(success=True, data=out, cmd_trace=[f"target_fs.read_text {key_path}"])

    if action == "add":
        if not public_key:
            return ToolResult(success=False, error="add 需要 public_key 参数")
        if not _is_public_key(public_key):
            return ToolResult(success=False, error="输入内容不是有效公钥格式（B023）")
        fs.mkdir(key_dir, parents=True)
        fs.chmod(key_dir, 0o700)
        existing = fs.read_text(key_path, encoding="utf-8", errors="replace") if fs.exists(key_path) else ""
        pk = public_key.strip()
        existing_lines = [line.strip() for line in existing.splitlines() if line.strip()]
        if pk in existing_lines:
            return ToolResult(success=True, data="公钥已存在，无需重复添加")
        content = existing
        if content and not content.endswith("\n"):
            content += "\n"
        content += pk + "\n"
        fs.write_text(key_path, content, atomic=True)
        fs.chmod(key_path, 0o600)
        return ToolResult(success=True, data="公钥已添加", cmd_trace=[f"target_fs.write_text {key_path}"])

    if action == "remove":
        if not public_key and not fingerprint:
            return ToolResult(success=False, error="remove 需要 public_key 或 fingerprint 参数")
        if not fs.exists(key_path):
            return ToolResult(success=False, error="authorized_keys 文件不存在或不可读")
        existing = fs.read_text(key_path, encoding="utf-8", errors="replace")
        lines = existing.splitlines()
        if public_key:
            new_lines = [line for line in lines if public_key.strip() not in line]
        else:
            wanted = fingerprint.strip()
            new_lines = []
            for line in lines:
                line_fp = _public_key_fingerprint(line)
                if line_fp != wanted:
                    new_lines.append(line)
        new_content = "\n".join(line for line in new_lines if line.strip())
        if new_content:
            new_content += "\n"
        fs.write_text(key_path, new_content, atomic=True)
        fs.chmod(key_path, 0o600)
        return ToolResult(success=True, data="公钥已移除", cmd_trace=[f"target_fs.write_text {key_path}"])

    return ToolResult(success=False, error=f"未知 action: {action}")
