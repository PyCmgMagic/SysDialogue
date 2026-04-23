"""工具: manage_hosts_entries."""

from __future__ import annotations

import re
from pathlib import Path

from sysdialogue.runtime.secure_runner import SafeExecutor
from sysdialogue.tools.base import ToolResult

HOSTS_FILE = "/etc/hosts"
_PROTECTED = {("127.0.0.1", "localhost"), ("::1", "localhost")}


def manage_hosts_entries(
    executor: SafeExecutor,
    action: str,
    hostname: str | None = None,
    ip_addrs: list[str] | None = None,
    comment: str | None = None,
) -> ToolResult:
    """管理 /etc/hosts 条目（list/add/update/delete）。"""
    if action == "list":
        cmd = ["cat", HOSTS_FILE]
        out, code = executor.run(cmd, timeout=5)
        return ToolResult(success=(code == 0), data=out, cmd_trace=[" ".join(cmd)])

    # 受保护条目检查
    if hostname and hostname.lower() == "localhost":
        return ToolResult(success=False, error="禁止修改 localhost 受保护条目（B024）")

    p = Path(HOSTS_FILE)
    try:
        content = p.read_text(encoding="utf-8")
    except Exception as e:
        return ToolResult(success=False, error=str(e))

    lines = content.splitlines()

    if action in ("add", "update"):
        if not hostname or not ip_addrs:
            return ToolResult(success=False, error=f"{action} 需要 hostname 和 ip_addrs 参数")
        new_lines = []
        for ip in ip_addrs:
            entry = f"{ip}\t{hostname}"
            if comment:
                entry += f"\t# {comment}"
            new_lines.append(entry)

        if action == "update":
            # 删除旧条目
            filtered = [l for l in lines if not _line_matches(l, hostname)]
            filtered.extend(new_lines)
            new_content = "\n".join(filtered) + "\n"
        else:
            new_content = "\n".join(lines + new_lines) + "\n"

    elif action == "delete":
        if not hostname:
            return ToolResult(success=False, error="delete 需要 hostname 参数")
        filtered = [l for l in lines if not _line_matches(l, hostname)]
        new_content = "\n".join(filtered) + "\n"
    else:
        return ToolResult(success=False, error=f"未知 action: {action}")

    tmp = HOSTS_FILE + ".tmp"
    try:
        import os
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(new_content)
        os.replace(tmp, HOSTS_FILE)
    except Exception as e:
        # 清理残留的 .tmp 文件，避免磁盘泄漏
        try:
            import os as _os
            if _os.path.exists(tmp):
                _os.unlink(tmp)
        except OSError:
            pass
        return ToolResult(success=False, error=str(e))

    return ToolResult(success=True, data=f"/etc/hosts {action} 成功")


def _line_matches(line: str, hostname: str) -> bool:
    parts = line.strip().split()
    if len(parts) >= 2 and not line.strip().startswith("#"):
        return hostname in parts[1:]
    return False
