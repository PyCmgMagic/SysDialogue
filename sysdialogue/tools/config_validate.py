"""工具: validate_config."""

from __future__ import annotations

import json

import yaml

from sysdialogue.runtime.secure_runner import SafeExecutor
from sysdialogue.runtime.target_fs import TargetFileAccess
from sysdialogue.tools.base import ToolResult

_SUPPORTED_TYPES = {
    "nginx", "apache", "sshd", "sysctl", "sudoers",
    "systemd-unit", "json", "yaml", "toml", "fstab", "cron", "auto",
}


def validate_config(
    executor: SafeExecutor,
    path: str,
    target_type: str = "auto",
) -> ToolResult:
    """校验配置文件语法。"""
    fs = TargetFileAccess(executor)
    target_path = fs.expand(path)
    if not fs.exists(target_path):
        return ToolResult(success=False, error=f"文件不存在：{path}")

    if target_type == "auto":
        target_type = _detect_type(path)

    traces: list[str] = []

    if target_type == "nginx":
        cmd = ["nginx", "-t", "-c", target_path]
        out, code = executor.run(cmd, timeout=10)
        traces.append(" ".join(cmd))
        return ToolResult(success=(code == 0), data=out, error=out if code != 0 else "", cmd_trace=traces)

    if target_type == "apache":
        cmd = ["apachectl", "-t"]
        out, code = executor.run(cmd, timeout=10)
        traces.append(" ".join(cmd))
        return ToolResult(success=(code == 0), data=out, error=out if code != 0 else "", cmd_trace=traces)

    if target_type == "sshd":
        cmd = ["sshd", "-t", "-f", target_path]
        out, code = executor.run(cmd, timeout=10)
        traces.append(" ".join(cmd))
        return ToolResult(success=(code == 0), data=out, error=out if code != 0 else "", cmd_trace=traces)

    if target_type == "sudoers":
        cmd = ["visudo", "-c", "-f", target_path]
        out, code = executor.run(cmd, timeout=10)
        traces.append(" ".join(cmd))
        return ToolResult(success=(code == 0), data=out, error=out if code != 0 else "", cmd_trace=traces)

    if target_type == "systemd-unit":
        cmd = ["systemd-analyze", "verify", target_path]
        out, code = executor.run(cmd, timeout=10)
        traces.append(" ".join(cmd))
        return ToolResult(success=(code == 0), data=out, error=out if code != 0 else "", cmd_trace=traces)

    if target_type == "sysctl":
        cmd = ["sysctl", "--system", "--dry-run"]
        out, code = executor.run(cmd, timeout=10)
        traces.append(" ".join(cmd))
        return ToolResult(success=(code == 0), data=out, error=out if code != 0 else "", cmd_trace=traces)

    if target_type == "json":
        try:
            content = fs.read_text(target_path, encoding="utf-8", errors="replace")
            json.loads(content)
            return ToolResult(success=True, data="JSON 语法合法", cmd_trace=[f"target_fs.read_text {target_path}"])
        except json.JSONDecodeError as e:
            return ToolResult(success=False, error=f"JSON 解析错误：{e}", cmd_trace=[])

    if target_type == "yaml":
        try:
            content = fs.read_text(target_path, encoding="utf-8", errors="replace")
            yaml.safe_load(content)
            return ToolResult(success=True, data="YAML 语法合法", cmd_trace=[f"target_fs.read_text {target_path}"])
        except yaml.YAMLError as e:
            return ToolResult(success=False, error=f"YAML 解析错误：{e}", cmd_trace=[])

    if target_type == "toml":
        try:
            import tomllib
        except ImportError:
            try:
                import tomli as tomllib  # type: ignore
            except ImportError:
                return ToolResult(success=False, error="tomllib/tomli 未安装，无法校验 TOML")
        try:
            content = fs.read_bytes(target_path)
            tomllib.loads(content.decode())
            return ToolResult(success=True, data="TOML 语法合法", cmd_trace=[f"target_fs.read_bytes {target_path}"])
        except Exception as e:
            return ToolResult(success=False, error=f"TOML 解析错误：{e}", cmd_trace=[])

    return ToolResult(success=False, error=f"不支持的配置类型：{target_type}")


def _detect_type(path: str) -> str:
    p = path.lower()
    if "nginx" in p:
        return "nginx"
    if "apache" in p or "httpd" in p:
        return "apache"
    if "sshd" in p:
        return "sshd"
    if "sudoers" in p:
        return "sudoers"
    if p.endswith(".service") or p.endswith(".timer") or p.endswith(".socket"):
        return "systemd-unit"
    if p.endswith(".json"):
        return "json"
    if p.endswith(".yaml") or p.endswith(".yml"):
        return "yaml"
    if p.endswith(".toml"):
        return "toml"
    if "sysctl" in p:
        return "sysctl"
    if "cron" in p:
        return "cron"
    if "fstab" in p:
        return "fstab"
    return "unknown"
