"""工具: manage_hosts_entries."""

from __future__ import annotations

from sysdialogue.runtime.secure_runner import SafeExecutor
from sysdialogue.runtime.target_fs import TargetFileAccess
from sysdialogue.tools.base import ToolResult

HOSTS_FILE = "/etc/hosts"


def manage_hosts_entries(
    executor: SafeExecutor,
    action: str,
    hostname: str | None = None,
    ip_addrs: list[str] | None = None,
    comment: str | None = None,
) -> ToolResult:
    """管理 /etc/hosts 条目（list/add/update/delete）。"""
    fs = TargetFileAccess(executor)
    hosts_path = fs.expand(HOSTS_FILE)

    if action == "list":
        try:
            content = fs.read_text(hosts_path, encoding="utf-8", errors="replace")
        except Exception as e:
            return ToolResult(success=False, error=str(e))
        return ToolResult(success=True, data=content, cmd_trace=[f"target_fs.read_text {hosts_path}"])

    # 受保护条目检查
    if hostname and hostname.lower() == "localhost":
        return ToolResult(success=False, error="禁止修改 localhost 受保护条目（B024）")

    try:
        content = fs.read_text(hosts_path, encoding="utf-8", errors="replace")
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

    try:
        fs.write_text(hosts_path, new_content, atomic=True)
    except Exception as e:
        return ToolResult(success=False, error=str(e))

    return ToolResult(
        success=True,
        data=f"{hosts_path} {action} 成功",
        cmd_trace=[f"target_fs.write_text {hosts_path}"],
    )


def _line_matches(line: str, hostname: str) -> bool:
    parts = line.strip().split()
    if len(parts) >= 2 and not line.strip().startswith("#"):
        return hostname in parts[1:]
    return False
