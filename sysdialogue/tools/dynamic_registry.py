"""DynamicToolRegistry — DynTool persistence + three-layer execution chain.

第一层：CommandSafetyChecker（形态 + 远程锁门）
第二层：StaticRuleMapper + RiskClassifier（对象语义）
第三层：UNKNOWN 用户确认（始终触发）

持久化：~/.sysdialogue/dynamic_tools.json + filelock
上限：20 个工具
"""

from __future__ import annotations

import json
import os
import re
import shlex
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Callable, TypedDict

try:
    import filelock  # type: ignore
    _HAS_FILELOCK = True
except ImportError:
    _HAS_FILELOCK = False

from sysdialogue.security.command_safety import check_command
from sysdialogue.security import path_policies as pp
from sysdialogue.security.risk_classifier import classify as risk_classify
from sysdialogue.tools.base import ToolResult

if TYPE_CHECKING:
    from sysdialogue.runtime.capability_probe import EnvProfile
    from sysdialogue.runtime.secure_runner import SafeExecutor


MAX_DYNAMIC_TOOLS = 20


class DynamicTool(TypedDict):
    tool_id: str
    name: str
    description: str
    cmd_template: list[str]
    execution_mode: str
    shell_command: str
    cwd: str | None
    params: dict
    risk_level: str          # 永远 "UNKNOWN"
    estimated_risk: str
    reversible: bool
    created_at: str
    created_for: str
    consequences: str
    risk_assessment: str
    changes_state: bool
    usage_count: int
    safety_overrides: int
    signature: str


@dataclass
class DynToolResult:
    success: bool
    blocked: bool = False
    cancelled: bool = False
    output: str = ""
    exit_code: int = 0
    reason: str = ""
    cmd: list[str] = field(default_factory=list)
    effective_cmd: list[str] = field(default_factory=list)
    privileged: bool = False
    privilege_reason: str = ""
    cwd: str | None = None
    final_risk: str = "UNKNOWN"
    declared_changes_state: bool = True
    changes_state: bool = True
    safety_profile: str = "standard"
    execution_mode: str = "argv"
    shell_command: str = ""
    hard_blocked: bool = False


_READ_ONLY_COMMAND_BASENAMES = {
    "echo",
    "printf",
    "true",
    "false",
    "pwd",
    "whoami",
    "id",
    "uname",
    "date",
    "cat",
    "head",
    "tail",
    "grep",
    "find",
    "ls",
    "stat",
    "df",
    "du",
    "ps",
    "ss",
    "netstat",
    "ip",
    "journalctl",
}

_READ_ONLY_MAPPED_TOOL_KEYS = {
    "get_port_status",
    "get_network_info",
    "list_processes",
    "get_disk_usage",
    "manage_service:status",
}


# --------------------------------------------------------------------------
# StaticRuleMapper — 把 argv 反向映射到静态工具名（启发式）
# --------------------------------------------------------------------------

@dataclass
class MappedTool:
    tool: str
    args: dict
    confidence: str  # "high" | "low"


class StaticRuleMapper:
    """极简反向映射：仅覆盖最常见的几个静态工具。

    覆盖不到的命令返回 None，由 UNKNOWN 确认兜底。
    """

    @staticmethod
    def map(cmd: list[str]) -> "MappedTool | None":
        if not cmd:
            return None
        base = cmd[0].rsplit("/", 1)[-1]

        # systemctl <action> <name>
        if base == "systemctl" and len(cmd) >= 3:
            action = cmd[1]
            name = cmd[2]
            if action in ("start", "stop", "restart", "status",
                          "enable", "disable", "reload", "daemon-reload"):
                return MappedTool(
                    tool="manage_service",
                    args={"name": name, "action": action},
                    confidence="high",
                )

        # ss / netstat → get_port_status
        if base in ("ss", "netstat"):
            return MappedTool(tool="get_port_status", args={}, confidence="high")

        # ip addr / ifconfig → get_network_info
        if base == "ip" and len(cmd) > 1 and cmd[1] == "addr":
            return MappedTool(tool="get_network_info", args={}, confidence="high")
        if base == "ifconfig":
            return MappedTool(tool="get_network_info", args={}, confidence="high")

        # ps aux → list_processes
        if base == "ps":
            return MappedTool(tool="list_processes", args={}, confidence="low")

        # kill <pid>
        if base == "kill" and len(cmd) >= 2:
            try:
                pid = int(cmd[-1])
                return MappedTool(tool="kill_process",
                                  args={"pid": pid}, confidence="high")
            except ValueError:
                pass

        # df → get_disk_usage
        if base == "df":
            return MappedTool(tool="get_disk_usage", args={"path": "/"},
                              confidence="low")

        # useradd <name>
        if base == "useradd" and len(cmd) >= 2:
            return MappedTool(tool="create_user",
                              args={"username": cmd[-1]}, confidence="high")
        if base == "userdel" and len(cmd) >= 2:
            return MappedTool(tool="delete_user",
                              args={"username": cmd[-1]}, confidence="high")

        return None


# --------------------------------------------------------------------------
# DynamicToolRegistry
# --------------------------------------------------------------------------

class DynamicToolRegistry:
    """DynTool persistence + safety-gated execution chain."""

    def __init__(
        self,
        *,
        storage_path: str | None = None,
    ):
        self.storage_path = Path(storage_path or os.path.expanduser(
            "~/.sysdialogue/dynamic_tools.json"
        ))

    # ------------------------------------------------------------------
    # 存取
    # ------------------------------------------------------------------

    def _load(self) -> dict[str, DynamicTool]:
        if not self.storage_path.exists():
            return {}
        try:
            with open(self.storage_path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return {}

    def _save(self, data: dict[str, DynamicTool]) -> None:
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = str(self.storage_path) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, str(self.storage_path))
        try:
            os.chmod(self.storage_path, 0o600)
        except OSError:
            pass

    def _with_lock(self, fn):
        """在 filelock 保护下执行写操作。filelock 不可用时降级为无锁。"""
        if not _HAS_FILELOCK:
            return fn()
        lk_path = str(self.storage_path) + ".lock"
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        with filelock.FileLock(lk_path, timeout=10):
            return fn()

    # ------------------------------------------------------------------
    # 注册
    # ------------------------------------------------------------------

    def list_tools(self) -> list[DynamicTool]:
        data = self._load()
        return list(data.values())

    def render_prompt_summary(self, limit: int = 8) -> str:
        tools = self.list_tools()
        if not tools:
            return ""
        tools.sort(
            key=lambda item: (
                int(item.get("usage_count", 0)),
                str(item.get("created_at", "")),
            ),
            reverse=True,
        )
        lines = ["[Reusable DynTools]"]
        for tool in tools[:limit]:
            params = ", ".join(list((tool.get("params") or {}).keys())[:4]) or "-"
            mode = "mutating" if tool.get("changes_state", True) else "read-only"
            execution_mode = str(tool.get("execution_mode") or "argv")
            if execution_mode == "shell":
                template = str(tool.get("shell_command") or "")[:80]
            else:
                template = " ".join((tool.get("cmd_template") or [])[:4])
            lines.append(
                f"  - {tool.get('tool_id')}: {tool.get('name')} | {mode} | {execution_mode} | params: {params} | template: {template}"
            )
        return "\n".join(lines)

    def register(
        self,
        *,
        name: str,
        description: str,
        cmd_template: list[str],
        execution_mode: str = "argv",
        shell_command: str = "",
        params: dict,
        cwd: str | None = None,
        consequences: str,
        risk_assessment: str,
        estimated_risk: str,
        changes_state: bool = True,
        reversible: bool = False,
        created_for: str = "",
    ) -> DynamicTool:
        """Register a new DynTool."""
        _validate_tool_spec(
            name=name,
            cmd_template=cmd_template,
            execution_mode=execution_mode,
            shell_command=shell_command,
            cwd=cwd,
            params=params,
        )
        signature = _tool_signature(
            cmd_template=cmd_template,
            execution_mode=execution_mode,
            shell_command=shell_command,
            cwd=cwd,
            params=params,
            changes_state=bool(changes_state),
            reversible=bool(reversible),
        )

        def _op():
            data = self._load()
            existing = _find_by_signature(data, signature)
            if existing is not None:
                return {**existing, "reused_existing": True}
            if len(data) >= MAX_DYNAMIC_TOOLS:
                raise RuntimeError(f"DynTool 上限 {MAX_DYNAMIC_TOOLS} 已达")
            tid = "dyn_" + uuid.uuid4().hex[:8]
            tool: DynamicTool = {
                "tool_id": tid,
                "name": name,
                "description": description,
                "cmd_template": cmd_template,
                "execution_mode": _normalize_execution_mode(execution_mode),
                "shell_command": shell_command,
                "cwd": cwd,
                "params": params,
                "risk_level": "UNKNOWN",
                "estimated_risk": estimated_risk,
                "reversible": reversible,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "created_for": created_for,
                "consequences": consequences,
                "risk_assessment": risk_assessment,
                "changes_state": bool(changes_state),
                "usage_count": 0,
                "safety_overrides": 0,
                "signature": signature,
            }
            data[tid] = tool
            self._save(data)
            return {**tool, "reused_existing": False}

        return self._with_lock(_op)

    def get(self, tool_id: str) -> DynamicTool | None:
        return self._load().get(tool_id)

    def delete(self, tool_id: str) -> bool:
        def _op():
            data = self._load()
            if tool_id not in data:
                return False
            del data[tool_id]
            self._save(data)
            return True

        return self._with_lock(_op)

    # ------------------------------------------------------------------
    # 执行（三层链）
    # ------------------------------------------------------------------

    def execute(
        self,
        tool_id: str,
        args: dict,
        *,
        executor: "SafeExecutor",
        env_profile: "EnvProfile",
        confirm_fn: Callable[[dict], bool],
        timeout: int = 30,
        safety_profile: str = "standard",
        privileged: bool = False,
        cwd: str | None = None,
    ) -> DynToolResult:
        """Execute a DynTool through the three-layer safety chain."""
        tool = self.get(tool_id)
        if tool is None:
            return DynToolResult(
                success=False, blocked=True,
                reason=f"DynTool 不存在：{tool_id}",
            )
        return self._execute_spec(
            tool_id=tool_id,
            tool=tool,
            args=args,
            executor=executor,
            env_profile=env_profile,
            confirm_fn=confirm_fn,
            timeout=timeout,
            cwd=cwd,
            safety_profile=safety_profile,
            privileged=privileged,
            persist_usage=True,
        )

    def execute_inline(
        self,
        *,
        name: str,
        description: str,
        cmd_template: list[str],
        execution_mode: str = "argv",
        shell_command: str = "",
        params: dict,
        cwd: str | None = None,
        args: dict,
        consequences: str,
        risk_assessment: str,
        estimated_risk: str,
        changes_state: bool = True,
        reversible: bool = False,
        executor: "SafeExecutor",
        env_profile: "EnvProfile",
        confirm_fn: Callable[[dict], bool],
        timeout: int = 30,
        safety_profile: str = "standard",
        privileged: bool = False,
    ) -> DynToolResult:
        try:
            _validate_tool_spec(
                name=name or _default_tool_name(cmd_template or [shell_command]),
                cmd_template=cmd_template,
                execution_mode=execution_mode,
                shell_command=shell_command,
                cwd=cwd,
                params=params,
            )
        except ValueError as exc:
            return DynToolResult(
                success=False,
                blocked=True,
                reason=f"DynTool 临时执行参数非法：{exc}",
                declared_changes_state=bool(changes_state),
                changes_state=True,
            )
        tool: DynamicTool = {
            "tool_id": "adhoc_" + uuid.uuid4().hex[:8],
            "name": name or _default_tool_name(cmd_template or [shell_command]),
            "description": description or "Ad-hoc dynamic execution",
            "cmd_template": cmd_template,
            "execution_mode": _normalize_execution_mode(execution_mode),
            "shell_command": shell_command,
            "cwd": cwd,
            "params": params,
            "risk_level": "UNKNOWN",
            "estimated_risk": estimated_risk,
            "reversible": bool(reversible),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "created_for": "adhoc_execution",
            "consequences": consequences,
            "risk_assessment": risk_assessment,
            "changes_state": bool(changes_state),
            "usage_count": 0,
            "safety_overrides": 0,
            "signature": _tool_signature(
                cmd_template=cmd_template,
                execution_mode=execution_mode,
                shell_command=shell_command,
                cwd=cwd,
                params=params,
                changes_state=bool(changes_state),
                reversible=bool(reversible),
            ),
        }
        return self._execute_spec(
            tool_id=tool["tool_id"],
            tool=tool,
            args=args,
            executor=executor,
            env_profile=env_profile,
            confirm_fn=confirm_fn,
            timeout=timeout,
            cwd=cwd,
            safety_profile=safety_profile,
            privileged=privileged,
            persist_usage=False,
        )

    def _execute_spec(
        self,
        *,
        tool_id: str,
        tool: DynamicTool,
        args: dict,
        executor: "SafeExecutor",
        env_profile: "EnvProfile",
        confirm_fn: Callable[[dict], bool],
        timeout: int,
        cwd: str | None,
        safety_profile: str,
        privileged: bool,
        persist_usage: bool,
    ) -> DynToolResult:
        declared_changes_state = bool(tool.get("changes_state", True))
        effective_cwd = cwd if cwd is not None else tool.get("cwd")
        safety_profile = _normalize_safety_profile(safety_profile)
        execution_mode = _normalize_execution_mode(str(tool.get("execution_mode") or "argv"))
        shell_template = str(tool.get("shell_command") or "")

        missing = _missing_required_args(tool, args)
        if missing:
            return DynToolResult(
                success=False, blocked=True,
                reason=f"DynTool 缺少必填参数：{', '.join(missing)}",
                declared_changes_state=declared_changes_state,
                changes_state=True,
            )

        # 渲染 cmd
        shell_command = ""
        if execution_mode == "shell":
            shell_command = self._render_shell(shell_template, args)
            cmd = _shell_command_check_argv(shell_command)
        else:
            cmd = self._render(tool["cmd_template"], args)
        cwd_error = _validate_cwd(effective_cwd, executor, env_profile)
        if cwd_error:
            return DynToolResult(
                success=False, blocked=True, cmd=cmd, cwd=effective_cwd,
                reason=cwd_error,
                declared_changes_state=declared_changes_state,
                changes_state=True,
            )
        unresolved = _unresolved_placeholders(cmd)
        if execution_mode == "shell":
            unresolved.extend(_unresolved_placeholders_in_text(shell_command))
            unresolved = sorted(set(unresolved))
        if unresolved:
            return DynToolResult(
                success=False, blocked=True, cmd=cmd,
                reason=f"DynTool 命令仍包含未解析参数：{', '.join(unresolved)}",
                declared_changes_state=declared_changes_state,
                changes_state=True,
            )

        if execution_mode == "shell" and safety_profile == "standard":
            return DynToolResult(
                success=False,
                blocked=True,
                cmd=cmd,
                cwd=effective_cwd,
                final_risk="BLOCK",
                reason="DynTool shell execution requires operator or break_glass safety_profile.",
                declared_changes_state=declared_changes_state,
                changes_state=True,
                safety_profile=safety_profile,
                execution_mode=execution_mode,
                shell_command=shell_command,
                hard_blocked=True,
            )

        privileged_cmd = _strip_sudo_prefix(cmd)
        auto_privilege_reason = ""
        if privileged_cmd is None and execution_mode == "argv":
            auto_privilege_reason = _auto_privilege_reason(cmd, env_profile)
        requires_privileged = bool(privileged) or privileged_cmd is not None or bool(auto_privilege_reason)
        check_cmd = privileged_cmd if privileged_cmd is not None else cmd

        hard_block = _hard_block_reason(
            cmd=check_cmd,
            shell_command=shell_command,
            execution_mode=execution_mode,
            env_profile=env_profile,
        )
        if hard_block:
            return DynToolResult(
                success=False,
                blocked=True,
                cmd=cmd,
                effective_cmd=check_cmd,
                cwd=effective_cwd,
                final_risk="BLOCK",
                reason=hard_block,
                declared_changes_state=declared_changes_state,
                changes_state=True,
                safety_profile=safety_profile,
                execution_mode=execution_mode,
                shell_command=shell_command,
                hard_blocked=True,
            )

        container_block = "" if safety_profile == "break_glass" else _container_command_block_reason(
            check_cmd,
            env_profile,
            privileged=requires_privileged,
        )
        if container_block:
            return DynToolResult(
                success=False,
                blocked=True,
                cmd=cmd,
                cwd=effective_cwd,
                final_risk="BLOCK",
                reason=container_block,
                declared_changes_state=declared_changes_state,
                changes_state=True,
            )

        mapped = StaticRuleMapper.map(check_cmd)
        effective_changes_state = _effective_changes_state(
            declared_changes_state=declared_changes_state,
            cmd=check_cmd,
            mapped=mapped,
        )

        # 第一层：形态检查
        safety = check_command(check_cmd, env_profile)
        safety = _profile_adjusted_safety(safety, safety_profile)
        if safety.level == "BLOCK":
            return DynToolResult(
                success=False, blocked=True, cmd=cmd,
                final_risk="BLOCK",
                reason=f"CommandSafetyChecker BLOCK：{', '.join(safety.rule_ids)} {safety.reason}",
                declared_changes_state=declared_changes_state,
                changes_state=effective_changes_state,
            )

        # 第二层：StaticRuleMapper → RiskClassifier
        highest = safety.level
        if mapped is not None:
            rc = risk_classify(mapped.tool, mapped.args, env_profile)
            if rc.level == "BLOCK":
                if safety_profile in {"operator", "break_glass"} and not _hard_rule_ids(rc.rule_ids):
                    rc.level = "WARN-HIGH"
                    rc.requires_confirmation = safety_profile != "break_glass"
                else:
                    return DynToolResult(
                        success=False, blocked=True, cmd=cmd,
                        final_risk="BLOCK",
                        reason=f"瀵硅薄璇箟 BLOCK锛堟槧灏勫埌 {mapped.tool}锛夛細{rc.reason}",
                        declared_changes_state=declared_changes_state,
                        changes_state=effective_changes_state,
                        safety_profile=safety_profile,
                        execution_mode=execution_mode,
                        shell_command=shell_command,
                        hard_blocked=True,
                    )
            if rc.level == "BLOCK":
                return DynToolResult(
                    success=False, blocked=True, cmd=cmd,
                    final_risk="BLOCK",
                    reason=f"对象语义 BLOCK（映射到 {mapped.tool}）：{rc.reason}",
                    declared_changes_state=declared_changes_state,
                    changes_state=effective_changes_state,
                )
            if _level_rank(rc.level) > _level_rank(highest):
                highest = rc.level

        # 第三层：UNKNOWN 确认（始终触发）
        decision_payload = {
            "tool_id": tool_id,
            "tool_name": tool["name"],
            "cmd": cmd,
            "effective_cmd": check_cmd,
            "privileged": requires_privileged,
            "privilege_reason": auto_privilege_reason or ("explicit_sudo_prefix" if privileged_cmd is not None else ""),
            "cwd": effective_cwd,
            "dynamic_mode": "registered" if persist_usage else "inline",
            "safety_profile": safety_profile,
            "execution_mode": execution_mode,
            "shell_command": shell_command,
            "hard_blocked": False,
            "safety_level": safety.level,
            "safety_rules": safety.rule_ids,
            "mapped_tool": mapped.tool if mapped else None,
            "consequences": tool["consequences"],
            "risk_assessment": tool["risk_assessment"],
            "final_risk": highest,
            "declared_changes_state": declared_changes_state,
            "changes_state": effective_changes_state,
        }
        try:
            ok = confirm_fn(decision_payload)
        except Exception as e:
            return DynToolResult(
                success=False, blocked=True, cmd=cmd,
                reason=f"confirm_fn 异常：{e}",
                declared_changes_state=declared_changes_state,
                changes_state=effective_changes_state,
            )
        if not ok:
            return DynToolResult(
                success=False, cancelled=True, cmd=cmd,
                final_risk=highest, reason="用户未批准",
                declared_changes_state=declared_changes_state,
                changes_state=effective_changes_state,
            )

        # Execute sudo-shaped commands through the executor privilege path so
        # password prompts remain centralized, auditable, and cwd-aware.
        if execution_mode == "shell" and requires_privileged:
            output, exit_code = executor.run_privileged_shell(shell_command, timeout=timeout, cwd=effective_cwd)
        elif execution_mode == "shell":
            output, exit_code = executor.run_shell(shell_command, timeout=timeout, cwd=effective_cwd)
        elif requires_privileged:
            output, exit_code = executor.run_privileged(check_cmd, timeout=timeout, cwd=effective_cwd)
        else:
            output, exit_code = executor.run(cmd, timeout=timeout, cwd=effective_cwd)

        # 更新用量统计
        if persist_usage:
            def _inc():
                data = self._load()
                if tool_id in data:
                    data[tool_id]["usage_count"] = data[tool_id].get("usage_count", 0) + 1
                    if safety.level != "SAFE":
                        data[tool_id]["safety_overrides"] = \
                            data[tool_id].get("safety_overrides", 0) + 1
                    self._save(data)
            try:
                self._with_lock(_inc)
            except Exception:
                pass

        return DynToolResult(
            success=(exit_code == 0),
            cmd=cmd,
            effective_cmd=check_cmd,
            privileged=requires_privileged,
            privilege_reason=auto_privilege_reason or ("explicit_sudo_prefix" if privileged_cmd is not None else ""),
            output=output,
            exit_code=exit_code,
            cwd=effective_cwd,
            final_risk=highest,
            declared_changes_state=declared_changes_state,
            changes_state=effective_changes_state,
            safety_profile=safety_profile,
            execution_mode=execution_mode,
            shell_command=shell_command,
            hard_blocked=False,
        )

    # ------------------------------------------------------------------
    # 模板渲染
    # ------------------------------------------------------------------

    @staticmethod
    def _render(cmd_template: list[str], args: dict) -> list[str]:
        rendered: list[str] = []
        for tok in cmd_template:
            rendered.append(re.sub(
                r"\{(\w+)\}",
                lambda m: str(args.get(m.group(1), m.group(0))),
                tok,
            ))
        return rendered

    @staticmethod
    def _render_shell(shell_command: str, args: dict) -> str:
        return re.sub(
            r"\{(\w+)\}",
            lambda m: str(args.get(m.group(1), m.group(0))),
            str(shell_command or ""),
        )

    @staticmethod
    def _render_shell(shell_command: str, args: dict) -> str:
        return re.sub(
            r"\{(\w+)\}",
            lambda m: str(args.get(m.group(1), m.group(0))),
            str(shell_command or ""),
        )


def _level_rank(level: str) -> int:
    return {"SAFE": 0, "WARN-LOW": 1, "WARN-HIGH": 2, "BLOCK": 3}.get(level, 0)


_HARD_SAFETY_RULES = {"CS003", "CS004", "CS010", "B010", "B015", "B016", "B017"}


def _hard_rule_ids(rule_ids: list[str]) -> bool:
    return any(str(rule_id) in _HARD_SAFETY_RULES for rule_id in (rule_ids or []))


def _profile_adjusted_safety(safety, safety_profile: str):
    profile = _normalize_safety_profile(safety_profile)
    if profile in {"operator", "break_glass"} and safety.level == "BLOCK" and not _hard_rule_ids(safety.rule_ids):
        return type(safety)(
            level="WARN-HIGH",
            rule_ids=list(safety.rule_ids),
            reason=f"soft-block downgraded by {profile}: {safety.reason}",
        )
    return safety


def _hard_block_reason(
    *,
    cmd: list[str],
    shell_command: str,
    execution_mode: str,
    env_profile: "EnvProfile",
) -> str:
    rule_ids: list[str] = []
    base_names = {_command_basename(part).lower() for part in (cmd or [])}
    if base_names.intersection({"su", "runuser"}):
        rule_ids.append("CS010")
    if base_names.intersection({"mkfs", "dd", "shred"}) or any(name.startswith("mkfs.") for name in base_names):
        rule_ids.append("CS004")
    if _is_rm_rf_core(cmd):
        rule_ids.append("CS003")
    if _references_sensitive_credential(cmd):
        rule_ids.append("CREDENTIAL_PATH")

    shell_text = str(shell_command or "")
    if execution_mode == "shell" and shell_text:
        lowered = shell_text.lower()
        if re.search(r"(^|[;&|()\s])(su|runuser)(\s|$)", lowered):
            rule_ids.append("CS010")
        if re.search(r"\becho\s+\S+\s*\|\s*(sudo|su|runuser)\b", lowered) or re.search(r"\|\s*sudo\s+-s\b", lowered, re.I):
            rule_ids.append("PASSWORD_PIPE")
        if re.search(r"(^|[;&|()\s])(mkfs(\.\S+)?|dd|shred)(\s|$)", lowered):
            rule_ids.append("CS004")
        if re.search(r"\brm\s+[^;&|]*-[^\s;&|]*r[^\s;&|]*f[^\s;&|]*\s+(/|/etc|/usr|/boot|/lib|/bin|/sbin)(\s|$|[;&|])", lowered):
            rule_ids.append("CS003")
        shell_tokens = _shell_command_check_argv(shell_text)
        if _references_sensitive_credential(shell_tokens):
            rule_ids.append("CREDENTIAL_PATH")

    if env_profile and (env_profile or {}).get("remote_mode"):
        from sysdialogue.security.remote_lockout import assess_cmd as lockout_assess_cmd
        lockout = lockout_assess_cmd(cmd, env_profile)
        if lockout.level == "BLOCK":
            rule_ids.extend(lockout.rule_ids)
            if lockout.reason:
                return f"CommandSafetyChecker BLOCK: {', '.join(sorted(set(rule_ids)))} {lockout.reason}"

    if rule_ids:
        return f"CommandSafetyChecker BLOCK: {', '.join(sorted(set(rule_ids)))} hard-blocked unsafe dynamic command"
    return ""


def _is_rm_rf_core(cmd: list[str]) -> bool:
    if not cmd:
        return False
    try:
        rm_index = next(
            index for index, part in enumerate(cmd)
            if _command_basename(str(part)).lower() in {"rm", "rmdir"}
        )
    except StopIteration:
        return False
    args = [str(part) for part in cmd[rm_index + 1:]]
    flags = "".join(arg for arg in args if arg.startswith("-"))
    has_recursive_force = "r" in flags.lower() and "f" in flags.lower()
    if not has_recursive_force:
        return False
    core_targets = {"/", "/etc", "/usr", "/boot", "/lib", "/bin", "/sbin"}
    for arg in args:
        if arg.startswith("-"):
            continue
        normalized = pp.normalize(arg)
        if normalized in core_targets:
            return True
    return False


def _references_sensitive_credential(tokens: list[str]) -> bool:
    for token in tokens or []:
        text = str(token).strip("'\"")
        if not text or text.startswith("-"):
            continue
        if pp.matches_sensitive_credential(text):
            return True
    return False


def _shell_command_check_argv(shell_command: str) -> list[str]:
    """Parse shell-like text into tokens used for safety analysis."""
    try:
        return shlex.split(str(shell_command or ""), posix=True)
    except ValueError:
        return [str(shell_command or "")]


def _unresolved_placeholders_in_text(text: str) -> list[str]:
    return sorted(set(re.findall(r"\{(\w+)\}", str(text or ""))))


def _validate_cwd(cwd: str | None, executor: "SafeExecutor", env_profile: "EnvProfile") -> str:
    if cwd in (None, ""):
        return ""
    cwd_text = str(cwd)
    is_remote = bool((env_profile or {}).get("remote_mode")) if isinstance(env_profile, dict) else False
    is_absolute = cwd_text.startswith("/") if is_remote else os.path.isabs(cwd_text)
    if not is_absolute:
        return "DynTool cwd must be an absolute directory path."
    if pp.has_path_traversal(cwd_text):
        return "DynTool cwd must not contain path traversal."
    if pp.matches_sensitive_credential(cwd_text):
        return "DynTool cwd must not point at a sensitive credential path."
    if is_remote:
        _, code = executor.run(["test", "-d", cwd_text], timeout=5)
        if code != 0:
            return f"DynTool cwd does not exist or is not a directory: {cwd_text}"
    elif not os.path.isdir(cwd_text):
        return f"DynTool cwd does not exist or is not a directory: {cwd_text}"
    return ""


def _container_command_block_reason(
    cmd: list[str],
    env_profile: "EnvProfile",
    *,
    privileged: bool = False,
) -> str:
    if not cmd:
        return ""
    base = str(cmd[0]).replace("\\", "/").rsplit("/", 1)[-1].lower()
    if base not in {"docker", "podman"}:
        return ""
    backend = str((env_profile or {}).get("container_backend") or "none").lower()
    if backend == base:
        return ""
    error = str((env_profile or {}).get("container_backend_error") or "")
    if privileged and base == "docker" and error == "docker_permission_denied":
        return ""
    detail = "当前环境未检测到可用 Docker/Podman 后端"
    if error == "docker_permission_denied":
        detail = "当前用户没有 Docker socket 访问权限"
    elif error == "docker_unavailable":
        detail = "Docker 命令存在但 daemon/API 不可用"
    elif error == "podman_unavailable":
        detail = "Podman 命令存在但不可用"
    return (
        f"DynTool 拒绝直接执行 {base} 命令：{detail}。"
        "请先修复容器运行时/权限，或改用非容器静态工具完成当前任务。"
    )


def _auto_privilege_reason(cmd: list[str], env_profile: "EnvProfile") -> str:
    """Prefer task completion for Docker socket permission errors.

    If Docker is present but the current user cannot read the socket, the safe
    repair path is the executor's privileged runner. That path keeps passwords
    out of argv/audit while preserving all DynTool safety, confirmation, and
    ReAct verification gates.
    """
    if not cmd:
        return ""
    base = str(cmd[0]).replace("\\", "/").rsplit("/", 1)[-1].lower()
    if base != "docker":
        return ""
    backend = str((env_profile or {}).get("container_backend") or "none").lower()
    error = str((env_profile or {}).get("container_backend_error") or "")
    if backend == "none" and error == "docker_permission_denied":
        return "docker_socket_permission_denied"
    return ""


_SUDO_PASSTHROUGH_FLAGS = {"-n", "-S", "-H", "-E", "-k", "-K"}


def _strip_sudo_prefix(cmd: list[str]) -> list[str] | None:
    """Return the inner command if ``cmd`` is a plain ``sudo ...`` invocation.

    Handles the common argv shapes produced by the model, e.g.
    ``["sudo", "usermod", ...]``, ``["sudo", "-n", "--", "systemctl", "restart", "x"]``.
    Returns ``None`` if ``cmd`` uses sudo features we do not want to transparently
    rewrite (``-u user``, ``-i``, ``-s``, etc.) — those stay on the normal
    ``run`` path so the model-visible behavior does not silently change.
    """
    if not cmd or cmd[0] != "sudo":
        return None
    i = 1
    while i < len(cmd):
        tok = cmd[i]
        if tok == "--":
            i += 1
            break
        if tok in _SUDO_PASSTHROUGH_FLAGS:
            i += 1
            continue
        # First non-flag token is the inner program — stop scanning.
        if not tok.startswith("-"):
            break
        # Anything else (-u, -i, -s, -p, -g, ...) is not safely rewritable.
        return None
    inner = cmd[i:]
    if not inner:
        return None
    return inner


def _validate_tool_spec(
    *,
    name: str,
    cmd_template: list[str],
    execution_mode: str = "argv",
    shell_command: str = "",
    cwd: str | None = None,
    params: dict,
) -> None:
    if not str(name).strip():
        raise ValueError("DynTool name 不能为空")
    mode = _normalize_execution_mode(execution_mode)
    if mode == "argv" and not cmd_template:
        raise ValueError("cmd_template 不能为空")
    if mode == "shell" and not str(shell_command).strip():
        raise ValueError("shell_command cannot be empty")
    if len(cmd_template) > 10:
        raise ValueError("cmd_template 元素数量超过 10")
    if len(str(shell_command)) > 8192:
        raise ValueError("shell_command is too long")
    if not isinstance(params, dict):
        raise ValueError("params 必须是对象")
    for token in cmd_template:
        if len(str(token)) > 256:
            raise ValueError("cmd_template 元素长度超过 256")

    if cwd is not None and len(str(cwd)) > 512:
        raise ValueError("cwd is too long")


def _tool_signature(
    *,
    cmd_template: list[str],
    execution_mode: str = "argv",
    shell_command: str = "",
    cwd: str | None = None,
    params: dict,
    changes_state: bool,
    reversible: bool,
) -> str:
    normalized_params = {
        str(key): {
            "type": str((value or {}).get("type", "")),
            "required": bool((value or {}).get("required", False)),
        }
        for key, value in sorted((params or {}).items())
    }
    return json.dumps(
        {
            "execution_mode": _normalize_execution_mode(execution_mode),
            "cmd_template": [str(token) for token in cmd_template],
            "shell_command": str(shell_command or ""),
            "cwd": cwd or None,
            "params": normalized_params,
            "changes_state": bool(changes_state),
            "reversible": bool(reversible),
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _find_by_signature(data: dict[str, DynamicTool], signature: str) -> DynamicTool | None:
    requested_legacy = _drop_cwd_from_signature(signature)
    for tool in data.values():
        tool_signature = tool.get("signature") or _tool_signature(
            cmd_template=list(tool.get("cmd_template") or []),
            execution_mode=str(tool.get("execution_mode") or "argv"),
            shell_command=str(tool.get("shell_command") or ""),
            cwd=tool.get("cwd"),
            params=dict(tool.get("params") or {}),
            changes_state=bool(tool.get("changes_state", True)),
            reversible=bool(tool.get("reversible", False)),
        )
        legacy_signature = _legacy_tool_signature(
            cmd_template=list(tool.get("cmd_template") or []),
            params=dict(tool.get("params") or {}),
            changes_state=bool(tool.get("changes_state", True)),
            reversible=bool(tool.get("reversible", False)),
        )
        if tool_signature == signature or legacy_signature == signature or tool_signature == requested_legacy:
            return tool
    return None


def _drop_cwd_from_signature(signature: str) -> str:
    try:
        payload = json.loads(signature)
        if payload.get("cwd") is not None:
            return signature
        payload.pop("cwd", None)
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)
    except Exception:
        return signature


def _legacy_tool_signature(
    *,
    cmd_template: list[str],
    params: dict,
    changes_state: bool,
    reversible: bool,
) -> str:
    normalized_params = {
        str(key): {
            "type": str((value or {}).get("type", "")),
            "required": bool((value or {}).get("required", False)),
        }
        for key, value in sorted((params or {}).items())
    }
    return json.dumps(
        {
            "cmd_template": [str(token) for token in cmd_template],
            "params": normalized_params,
            "changes_state": bool(changes_state),
            "reversible": bool(reversible),
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _normalize_execution_mode(value: str) -> str:
    mode = str(value or "argv").strip().lower()
    return "shell" if mode == "shell" else "argv"


def _normalize_safety_profile(value: str) -> str:
    profile = str(value or "standard").strip().lower().replace("-", "_")
    return profile if profile in {"standard", "operator", "break_glass"} else "standard"


def _default_tool_name(cmd_template: list[str] | str) -> str:
    if isinstance(cmd_template, str):
        try:
            cmd_template = shlex.split(cmd_template, posix=True)
        except ValueError:
            cmd_template = [cmd_template]
    if not cmd_template:
        return "adhoc_command"
    return _command_basename(str(cmd_template[0])) or "adhoc_command"


def _effective_changes_state(
    *,
    declared_changes_state: bool,
    cmd: list[str],
    mapped: MappedTool | None,
) -> bool:
    if _is_read_only_dynamic_command(cmd):
        return False
    if declared_changes_state:
        return True
    if mapped is not None and _mapped_tool_key(mapped) in _READ_ONLY_MAPPED_TOOL_KEYS:
        return False
    return True


def _is_read_only_dynamic_command(cmd: list[str]) -> bool:
    if not cmd:
        return False
    base = _command_basename(cmd[0])
    if base in _READ_ONLY_COMMAND_BASENAMES:
        return True
    lowered = [str(part).lower() for part in cmd]
    if base in {"java", "javac", "mvn", "gradle", "node", "npm"}:
        return any(part in {"-version", "--version", "-v", "version"} for part in lowered[1:])
    if base == "systemctl":
        return len(lowered) >= 2 and lowered[1] in {"status", "is-active", "is-enabled", "show"}
    if base in {"docker", "podman"}:
        return len(lowered) >= 2 and lowered[1] in {
            "ps",
            "images",
            "inspect",
            "logs",
            "info",
            "version",
        }
    if base in {"curl", "wget"}:
        text = " ".join(lowered)
        write_markers = (" -x post", " -x put", " -x patch", " -x delete", " --request post", " --request put", " --request patch", " --request delete", " -d ", " --data")
        return not any(marker in f" {text} " for marker in write_markers)
    return False


def _mapped_tool_key(mapped: MappedTool) -> str:
    action = (mapped.args.get("action") or "").lower()
    return f"{mapped.tool}:{action}" if action else mapped.tool


def _command_basename(command: str) -> str:
    return command.replace("\\", "/").rsplit("/", 1)[-1]


def _missing_required_args(tool: DynamicTool, args: dict) -> list[str]:
    missing: list[str] = []
    for name, spec in (tool.get("params") or {}).items():
        if not isinstance(spec, dict) or not spec.get("required", False):
            continue
        if args.get(name) in (None, ""):
            missing.append(name)
    return missing


def _unresolved_placeholders(cmd: list[str]) -> list[str]:
    unresolved: list[str] = []
    for token in cmd:
        unresolved.extend(re.findall(r"\{(\w+)\}", token))
    return sorted(set(unresolved))
