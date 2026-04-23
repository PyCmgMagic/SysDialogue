"""CommandSafetyChecker — DynTool 通路的命令形态检查（CS001-CS009）。

用于 DynamicToolRegistry 执行前对 argv 做形态级安全校验。
远程锁门场景委托 `security/remote_lockout.py::assess_cmd` 统一处理。

规则优先级：BLOCK > WARN-HIGH > WARN-LOW > SAFE
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from sysdialogue.security import path_policies as pp
from sysdialogue.security.remote_lockout import assess_cmd as lockout_assess_cmd

if TYPE_CHECKING:
    from sysdialogue.runtime.capability_probe import EnvProfile


@dataclass
class SafetyDecision:
    level: str  # SAFE | WARN-HIGH | BLOCK
    rule_ids: list[str] = field(default_factory=list)
    reason: str = ""


_LEVEL_ORDER = {"SAFE": 0, "WARN-LOW": 1, "WARN-HIGH": 2, "BLOCK": 3}


def _elevate(a: SafetyDecision, b: SafetyDecision) -> SafetyDecision:
    """取更高级别的判定；同级别合并 rule_ids + reason。"""
    if _LEVEL_ORDER.get(b.level, 0) > _LEVEL_ORDER.get(a.level, 0):
        return b
    if _LEVEL_ORDER.get(b.level, 0) == _LEVEL_ORDER.get(a.level, 0) and b.rule_ids:
        merged = SafetyDecision(
            level=a.level,
            rule_ids=list(dict.fromkeys(a.rule_ids + b.rule_ids)),
            reason=(a.reason + "；" + b.reason).strip("；") if a.reason or b.reason else "",
        )
        return merged
    return a


_SHELL_METACHARS = ("&&", "||", "|", ";", "$(", "`", ">", ">>", "<", "2>&1")

_DESTRUCTIVE_CMDS = {"mkfs", "dd", "shred"}

_SYSTEM_DIR_PREFIXES_CS = (
    "/etc", "/usr", "/lib", "/bin", "/sbin",
    "/boot", "/var", "/srv", "/root",
)


def check_command(cmd: list[str], env_profile: "EnvProfile | None" = None) -> SafetyDecision:
    """对 argv 做 CS001-CS009 形态级校验，叠加远程锁门。"""
    if not cmd:
        return SafetyDecision(level="SAFE")

    decision = SafetyDecision(level="SAFE")
    base = _base_name(cmd[0])

    # CS001: shell 元字符
    for tok in cmd:
        if any(m in tok for m in _SHELL_METACHARS):
            decision = _elevate(decision, SafetyDecision(
                level="BLOCK", rule_ids=["CS001"],
                reason=f"argv 含 shell 元字符：{tok!r}",
            ))

    # CS002: 路径穿越 ..
    for tok in cmd:
        if pp.has_path_traversal(tok):
            decision = _elevate(decision, SafetyDecision(
                level="BLOCK", rule_ids=["CS002"],
                reason=f"argv 含路径穿越 ..：{tok!r}",
            ))

    # CS003: rm/rmdir + -rf + 系统目录
    if base in ("rm", "rmdir"):
        has_rf = any(a in ("-rf", "-fr", "-Rf", "-rR") for a in cmd) or \
                 ("-r" in cmd and "-f" in cmd) or \
                 ("-R" in cmd and "-f" in cmd)
        if has_rf:
            for a in cmd[1:]:
                if a.startswith("-"):
                    continue
                if _is_system_dir(a):
                    decision = _elevate(decision, SafetyDecision(
                        level="BLOCK", rule_ids=["CS003"],
                        reason=f"rm/rmdir -rf 系统目录：{a}",
                    ))

    # CS004: mkfs / dd / shred
    if base in _DESTRUCTIVE_CMDS:
        decision = _elevate(decision, SafetyDecision(
            level="BLOCK", rule_ids=["CS004"],
            reason=f"破坏性命令：{base}",
        ))
    elif base.startswith("mkfs."):
        decision = _elevate(decision, SafetyDecision(
            level="BLOCK", rule_ids=["CS004"],
            reason=f"破坏性命令：{base}",
        ))

    # CS005: chmod 777/000 + 系统目录
    if base == "chmod":
        mode = None
        targets = []
        for a in cmd[1:]:
            if a.startswith("-"):
                continue
            if mode is None:
                mode = a
            else:
                targets.append(a)
        if mode in ("777", "000", "0777", "0000"):
            for t in targets:
                if _is_system_dir(t):
                    decision = _elevate(decision, SafetyDecision(
                        level="WARN-HIGH", rule_ids=["CS005"],
                        reason=f"chmod {mode} 系统目录：{t}",
                    ))

    # CS006: chown + root 目标
    if base == "chown":
        for a in cmd[1:]:
            if a.startswith("-"):
                continue
            if a == "root" or a.startswith("root:") or a.endswith(":root"):
                decision = _elevate(decision, SafetyDecision(
                    level="WARN-HIGH", rule_ids=["CS006"],
                    reason=f"chown 目标含 root：{a}",
                ))
                break

    # CS007: 涉及 SENSITIVE_CREDENTIAL_PATHS
    for tok in cmd[1:]:
        if tok.startswith("-"):
            continue
        if pp.matches_sensitive_credential(tok):
            decision = _elevate(decision, SafetyDecision(
                level="WARN-HIGH", rule_ids=["CS007"],
                reason=f"argv 引用敏感凭证路径：{tok}",
            ))
            break

    # CS008: 任意 arg 超过 8192 字符
    for tok in cmd:
        if len(tok) > 8192:
            decision = _elevate(decision, SafetyDecision(
                level="WARN-HIGH", rule_ids=["CS008"],
                reason=f"argv 元素长度 {len(tok)} > 8192",
            ))
            break

    # CS009: curl/wget + 管道/输出执行
    if base in ("curl", "wget"):
        joined = " ".join(cmd)
        danger_patterns = (
            "| sh", "|sh", "| bash", "|bash",
            "-o -", "-o-",
            " | sudo ", "| python", "|python",
        )
        if any(p in joined for p in danger_patterns):
            decision = _elevate(decision, SafetyDecision(
                level="BLOCK", rule_ids=["CS009"],
                reason=f"{base} 命令含管道/输出执行模式",
            ))

    # 远程锁门叠加
    if env_profile is not None:
        lk = lockout_assess_cmd(cmd, env_profile)
        if lk.level != "SAFE":
            decision = _elevate(decision, SafetyDecision(
                level=lk.level, rule_ids=list(lk.rule_ids), reason=lk.reason,
            ))

    return decision


# --------------------------------------------------------------------------
# 辅助
# --------------------------------------------------------------------------

def _base_name(path: str) -> str:
    """取命令基础名，去掉路径前缀。"""
    return path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]


def _is_system_dir(path: str) -> bool:
    """按 POSIX 风格（目标 Linux）判定是否系统目录。"""
    n = path.replace("\\", "/").rstrip("/")
    if n == "":
        return True  # "/" 规范化为空字符串，视为根目录
    if not n.startswith("/"):
        n = "/" + n
    for prefix in _SYSTEM_DIR_PREFIXES_CS:
        if n == prefix or n.startswith(prefix + "/"):
            return True
    return False
