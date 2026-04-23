"""工具: manage_package, get_resource_stats."""

from __future__ import annotations

from sysdialogue.runtime.secure_runner import SafeExecutor
from sysdialogue.tools.base import ToolResult

VALID_ACTIONS = {
    "install", "remove", "update", "list", "search",
    "clean-cache", "hold", "unhold",
}


def manage_package(
    executor: SafeExecutor,
    name: str | None = None,
    names: list[str] | None = None,
    action: str = "list",
    manager: str = "auto",
    env_profile: dict | None = None,
) -> ToolResult:
    """包管理操作。"""
    if action not in VALID_ACTIONS:
        return ToolResult(success=False, error=f"无效 action: {action}")

    pkg_mgr = _resolve_manager(manager, env_profile)
    if not pkg_mgr:
        return ToolResult(success=False, error="无法确定包管理器，请指定 manager 参数")

    targets = names or ([name] if name else [])
    cmd = _build_cmd(pkg_mgr, action, targets)
    if not cmd:
        return ToolResult(success=False, error=f"{pkg_mgr} 不支持 {action}")

    timeout = 120 if action in ("install", "remove", "update") else 30
    out, code = executor.run(cmd, timeout=timeout)
    return ToolResult(
        success=(code == 0),
        data=out,
        error=out if code != 0 else "",
        cmd_trace=[" ".join(cmd)],
    )


def _resolve_manager(manager: str, env_profile: dict | None) -> str | None:
    if manager != "auto":
        return manager
    if env_profile:
        return env_profile.get("package_manager") or None
    return None


def _build_cmd(mgr: str, action: str, targets: list[str]) -> list[str] | None:
    t = targets
    if mgr == "apt":
        m = {
            "install": ["apt-get", "install", "-y"] + t,
            "remove": ["apt-get", "remove", "-y"] + t,
            "update": ["apt-get", "upgrade", "-y"] + t if t else ["apt-get", "upgrade", "-y"],
            "list": ["dpkg", "-l"] + t,
            "search": ["apt-cache", "search"] + t,
            "clean-cache": ["apt-get", "clean"],
            "hold": ["apt-mark", "hold"] + t,
            "unhold": ["apt-mark", "unhold"] + t,
        }
    elif mgr in ("dnf", "yum"):
        m = {
            "install": [mgr, "install", "-y"] + t,
            "remove": [mgr, "remove", "-y"] + t,
            "update": [mgr, "update", "-y"] + t if t else [mgr, "update", "-y"],
            "list": [mgr, "list", "installed"] + t,
            "search": [mgr, "search"] + t,
            "clean-cache": [mgr, "clean", "all"],
            "hold": ["dnf", "versionlock", "add"] + t,
            "unhold": ["dnf", "versionlock", "delete"] + t,
        }
    else:
        return None
    return m.get(action)


def get_resource_stats(
    executor: SafeExecutor,
    resource: str = "all",
    top_n_procs: int = 10,
) -> ToolResult:
    """获取 CPU/内存资源使用情况。"""
    results: dict = {}
    traces: list[str] = []

    if resource in ("cpu", "all"):
        cmd = ["top", "-bn1"]
        out, code = executor.run(cmd, timeout=10)
        traces.append(" ".join(cmd))
        if code == 0:
            results["cpu"] = out

    if resource in ("memory", "all"):
        cmd = ["free", "-h"]
        out, code = executor.run(cmd, timeout=5)
        traces.append(" ".join(cmd))
        if code == 0:
            results["memory"] = out

    if top_n_procs:
        cmd = ["ps", "aux", "--sort", "-%cpu"]
        out, code = executor.run(cmd, timeout=5)
        traces.append(" ".join(cmd))
        if code == 0:
            lines = out.splitlines()
            results["top_procs"] = "\n".join(lines[: top_n_procs + 1])

    return ToolResult(success=True, data=results, cmd_trace=traces)
