"""DynamicToolRegistry — DynTool 持久化 + 三层执行链（竞赛模式关闭）。

参考 claudeplan6.md §6。竞赛模式下 execute() 直接拒绝；开发态保留完整能力。

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
    params: dict
    risk_level: str          # 永远 "UNKNOWN"
    estimated_risk: str
    reversible: bool
    created_at: str
    created_for: str
    consequences: str
    risk_assessment: str
    usage_count: int
    safety_overrides: int


@dataclass
class DynToolResult:
    success: bool
    blocked: bool = False
    cancelled: bool = False
    output: str = ""
    exit_code: int = 0
    reason: str = ""
    cmd: list[str] = field(default_factory=list)
    final_risk: str = "UNKNOWN"


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
    """DynTool 持久化 + 三层执行链。

    - competition_mode=True（默认）：execute() 直接拒绝
    - 开发态：走 CS → StaticRuleMapper+RiskClassifier → UNKNOWN 确认 → 执行
    """

    def __init__(
        self,
        *,
        storage_path: str | None = None,
        competition_mode: bool = True,
    ):
        self.storage_path = Path(storage_path or os.path.expanduser(
            "~/.sysdialogue/dynamic_tools.json"
        ))
        self.competition_mode = competition_mode

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

    def register(
        self,
        *,
        name: str,
        description: str,
        cmd_template: list[str],
        params: dict,
        consequences: str,
        risk_assessment: str,
        estimated_risk: str,
        reversible: bool = False,
        created_for: str = "",
    ) -> DynamicTool:
        """注册一个新 DynTool（竞赛模式仍允许注册但不可执行）。"""
        if len(cmd_template) > 10:
            raise ValueError("cmd_template 元素数量超过 10")
        for t in cmd_template:
            if len(t) > 256:
                raise ValueError("cmd_template 元素长度超过 256")

        def _op():
            data = self._load()
            if len(data) >= MAX_DYNAMIC_TOOLS:
                raise RuntimeError(f"DynTool 上限 {MAX_DYNAMIC_TOOLS} 已达")
            tid = "dyn_" + uuid.uuid4().hex[:8]
            tool: DynamicTool = {
                "tool_id": tid,
                "name": name,
                "description": description,
                "cmd_template": cmd_template,
                "params": params,
                "risk_level": "UNKNOWN",
                "estimated_risk": estimated_risk,
                "reversible": reversible,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "created_for": created_for,
                "consequences": consequences,
                "risk_assessment": risk_assessment,
                "usage_count": 0,
                "safety_overrides": 0,
            }
            data[tid] = tool
            self._save(data)
            return tool

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
    ) -> DynToolResult:
        """竞赛模式直接拒绝；开发态走三层执行链。"""
        if self.competition_mode:
            return DynToolResult(
                success=False, blocked=True,
                reason="竞赛模式下 DynTool 已关闭，禁止执行",
            )

        tool = self.get(tool_id)
        if tool is None:
            return DynToolResult(
                success=False, blocked=True,
                reason=f"DynTool 不存在：{tool_id}",
            )

        # 渲染 cmd
        cmd = self._render(tool["cmd_template"], args)

        # 第一层：形态检查
        safety = check_command(cmd, env_profile)
        if safety.level == "BLOCK":
            return DynToolResult(
                success=False, blocked=True, cmd=cmd,
                final_risk="BLOCK",
                reason=f"CommandSafetyChecker BLOCK：{', '.join(safety.rule_ids)} {safety.reason}",
            )

        # 第二层：StaticRuleMapper → RiskClassifier
        highest = safety.level
        mapped = StaticRuleMapper.map(cmd)
        if mapped is not None:
            rc = risk_classify(mapped.tool, mapped.args, env_profile)
            if rc.level == "BLOCK":
                return DynToolResult(
                    success=False, blocked=True, cmd=cmd,
                    final_risk="BLOCK",
                    reason=f"对象语义 BLOCK（映射到 {mapped.tool}）：{rc.reason}",
                )
            if _level_rank(rc.level) > _level_rank(highest):
                highest = rc.level

        # 第三层：UNKNOWN 确认（始终触发）
        decision_payload = {
            "tool_id": tool_id,
            "tool_name": tool["name"],
            "cmd": cmd,
            "safety_level": safety.level,
            "safety_rules": safety.rule_ids,
            "mapped_tool": mapped.tool if mapped else None,
            "consequences": tool["consequences"],
            "risk_assessment": tool["risk_assessment"],
            "final_risk": highest,
        }
        try:
            ok = confirm_fn(decision_payload)
        except Exception as e:
            return DynToolResult(
                success=False, blocked=True, cmd=cmd,
                reason=f"confirm_fn 异常：{e}",
            )
        if not ok:
            return DynToolResult(
                success=False, cancelled=True, cmd=cmd,
                final_risk=highest, reason="用户未批准",
            )

        # 实际执行
        output, exit_code = executor.run(cmd, timeout=timeout)

        # 更新用量统计
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
            cmd=cmd, output=output, exit_code=exit_code,
            final_risk=highest,
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


def _level_rank(level: str) -> int:
    return {"SAFE": 0, "WARN-LOW": 1, "WARN-HIGH": 2, "BLOCK": 3}.get(level, 0)
