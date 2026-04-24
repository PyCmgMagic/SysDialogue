"""Persistent permission policy for tools and dynamic commands.

The policy is intentionally additive: it may deny or ask for additional
confirmation, but it never downgrades a BLOCK risk decision. Without explicit
rules it preserves the existing RiskClassifier behavior.
"""

from __future__ import annotations

import fnmatch
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class PolicyDecision:
    action: str = "ask"  # allow | ask | deny
    reason: str = "No matching permission rule."
    rule_id: str = ""

    @property
    def is_denied(self) -> bool:
        return self.action == "deny"

    @property
    def requires_confirmation(self) -> bool:
        return self.action == "ask"


@dataclass
class PermissionRule:
    rule_id: str
    action: str
    kind: str = "tool"  # tool | command | path | risk | target
    pattern: str = "*"
    description: str = ""
    scopes: list[str] = field(default_factory=list)


class PermissionPolicy:
    """Small JSON-backed allow/ask/deny policy.

    The most specific matching rule wins. Equal-specificity rules use the later
    file entry, which lets operators place broad defaults first and local
    overrides later. The default policy preserves existing tool behavior;
    dynamic command families still ask by default.
    """

    def __init__(self, path: str | None = None):
        self.path = Path(path or os.path.expanduser("~/.sysdialogue/policy.json"))
        self.rules: list[PermissionRule] = []
        self.session_grants: set[str] = set()
        self._load()

    def evaluate_tool(
        self,
        *,
        tool: str,
        args: dict[str, Any] | None = None,
        risk_level: str = "SAFE",
        target: str = "",
    ) -> PolicyDecision:
        if risk_level == "BLOCK":
            return PolicyDecision("deny", "RiskClassifier returned BLOCK; policy cannot override it.", "risk:block")

        key = self._grant_key("tool", tool, target)
        if key in self.session_grants and risk_level in {"SAFE", "WARN-LOW"}:
            return PolicyDecision("allow", "Allowed by this-session grant.", "session-grant")

        rule = self._best_tool_rule(
            tool=tool,
            args=args or {},
            risk_level=risk_level,
            target=target,
        )

        if rule is not None:
            return PolicyDecision(_normalize_action(rule.action), rule.description or f"Matched {rule.kind}:{rule.pattern}", rule.rule_id)

        return PolicyDecision("allow", f"{risk_level} tool without a stricter policy rule.", "default:risk-classifier")

    def evaluate_command(
        self,
        *,
        argv: list[str],
        risk_level: str = "UNKNOWN",
        target: str = "",
    ) -> PolicyDecision:
        if risk_level == "BLOCK":
            return PolicyDecision("deny", "Command safety returned BLOCK; policy cannot override it.", "risk:block")
        command = _basename(argv[0]) if argv else ""
        key = self._grant_key("command", command, target)
        if key in self.session_grants and risk_level in {"SAFE", "WARN-LOW"}:
            return PolicyDecision("allow", "Allowed by this-session grant.", "session-grant")
        rule = self._best_match(kind="command", value=command)
        if rule is not None:
            return PolicyDecision(_normalize_action(rule.action), rule.description or f"Matched command:{rule.pattern}", rule.rule_id)
        return PolicyDecision("ask", "Dynamic commands require explicit confirmation by default.", "default:command")

    def grant_for_session(self, *, kind: str, value: str, target: str = "") -> None:
        self.session_grants.add(self._grant_key(kind, value, target))

    def render_summary(self) -> str:
        if not self.rules:
            return "PermissionPolicy: default follows RiskClassifier; DynTool commands ask; BLOCK=deny."
        lines = ["PermissionPolicy rules:"]
        for rule in self.rules[:20]:
            lines.append(f"- {rule.rule_id}: {rule.action} {rule.kind}:{rule.pattern}")
        if len(self.rules) > 20:
            lines.append(f"- ... {len(self.rules) - 20} more")
        return "\n".join(lines)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"rules": [asdict(rule) for rule in self.rules]}
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        rules = []
        for item in data.get("rules") or []:
            if not isinstance(item, dict):
                continue
            try:
                rules.append(
                    PermissionRule(
                        rule_id=str(item.get("rule_id") or f"rule_{len(rules) + 1}"),
                        action=_normalize_action(str(item.get("action") or "ask")),
                        kind=str(item.get("kind") or "tool"),
                        pattern=str(item.get("pattern") or "*"),
                        description=str(item.get("description") or ""),
                        scopes=[str(scope) for scope in item.get("scopes") or []],
                    )
                )
            except Exception:
                continue
        self.rules = rules

    def _best_match(self, *, kind: str, value: str) -> PermissionRule | None:
        best: tuple[int, int, PermissionRule] | None = None
        for index, rule in enumerate(self.rules):
            if rule.kind != kind:
                continue
            if fnmatch.fnmatchcase(value, rule.pattern):
                candidate = (_rule_specificity(rule), index, rule)
                if best is None or candidate[:2] >= best[:2]:
                    best = candidate
        return best[2] if best is not None else None

    def _best_tool_rule(
        self,
        *,
        tool: str,
        args: dict[str, Any],
        risk_level: str,
        target: str = "",
    ) -> PermissionRule | None:
        matches: list[tuple[int, int, PermissionRule]] = []
        path_values = self._path_values(args)
        for index, rule in enumerate(self.rules):
            if rule.kind == "tool" and fnmatch.fnmatchcase(tool, rule.pattern):
                matches.append((_rule_specificity(rule), index, rule))
            elif rule.kind == "target" and target and fnmatch.fnmatchcase(target, rule.pattern):
                matches.append((_rule_specificity(rule), index, rule))
            elif rule.kind == "risk" and fnmatch.fnmatchcase(risk_level, rule.pattern):
                matches.append((_rule_specificity(rule), index, rule))
            elif rule.kind == "path" and any(fnmatch.fnmatchcase(path, rule.pattern) for path in path_values):
                matches.append((_rule_specificity(rule), index, rule))
        if not matches:
            return None
        matches.sort(key=lambda item: (item[0], item[1]))
        return matches[-1][2]

    def _path_values(self, args: dict[str, Any]) -> list[str]:
        candidates: list[str] = []
        for key in ("path", "file_path", "src", "dst", "target_path", "source_path", "archive_path"):
            value = args.get(key)
            if isinstance(value, str) and value:
                candidates.append(value.replace("\\", "/"))
        return candidates

    @staticmethod
    def _grant_key(kind: str, value: str, target: str = "") -> str:
        return f"{kind}:{value}:{target}"


def _normalize_action(action: str) -> str:
    normalized = str(action or "ask").lower()
    return normalized if normalized in {"allow", "ask", "deny"} else "ask"


def _basename(command: str) -> str:
    return str(command or "").replace("\\", "/").rsplit("/", 1)[-1]


def _rule_specificity(rule: PermissionRule) -> int:
    return _kind_weight(rule.kind) + _pattern_specificity(rule.pattern)


def _kind_weight(kind: str) -> int:
    return {
        "path": 400,
        "target": 300,
        "tool": 200,
        "command": 200,
        "risk": 100,
    }.get(str(kind or ""), 0)


def _pattern_specificity(pattern: str) -> int:
    text = str(pattern or "")
    wildcards = sum(text.count(token) for token in ("*", "?", "["))
    return max(0, len(text) - wildcards * 4)
