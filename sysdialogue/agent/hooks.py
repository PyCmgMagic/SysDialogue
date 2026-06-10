"""Controlled hook support for SysDialogue v9."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sysdialogue.security.output_sanitizer import sanitize_text


HOOK_EVENTS = {
    "task_started",
    "pre_tool",
    "post_tool",
    "approval_requested",
    "lock_conflict",
    "task_finished",
    "task_failed",
}

HOOK_ACTIONS = {"notify", "inject_context", "execute_command"}


@dataclass(frozen=True)
class HookRule:
    hook_id: str
    event: str
    action: str
    message: str = ""
    context: str = ""
    cmd_template: tuple[str, ...] = ()
    args: dict[str, Any] = field(default_factory=dict)
    timeout: int = 10
    enabled: bool = True
    source: str = "user"


@dataclass(frozen=True)
class HookEvent:
    event: str
    task_id: str = ""
    session_id: str = ""
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class HookResult:
    hook_id: str
    action: str
    status: str
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)


class HookManager:
    """Read project/user hooks and execute safe, bounded actions."""

    def __init__(
        self,
        *,
        project_root: str | Path | None = None,
        user_path: str | Path | None = None,
    ):
        self.project_root = Path(project_root or Path.cwd())
        self.project_path = self.project_root / ".sysdialogue" / "hooks.json"
        self.user_path = Path(user_path or os.path.expanduser("~/.sysdialogue/hooks.json"))
        self._rules: list[HookRule] | None = None
        self._counts: dict[tuple[str, str], int] = {}

    def reload(self) -> list[HookRule]:
        rules: list[HookRule] = []
        for path, source in ((self.user_path, "user"), (self.project_path, "project")):
            rules.extend(_load_rules(path, source=source))
        self._rules = rules
        self._counts.clear()
        return list(rules)

    def list_rules(self) -> list[HookRule]:
        if self._rules is None:
            self.reload()
        return list(self._rules or [])

    def render_summary(self, *, limit: int = 12) -> str:
        rules = [rule for rule in self.list_rules() if rule.enabled]
        if not rules:
            return "Hooks: no enabled hooks."
        lines = ["Hooks:"]
        for rule in rules[:limit]:
            lines.append(f"- {rule.hook_id}: {rule.event} -> {rule.action}")
        if len(rules) > limit:
            lines.append(f"- ... {len(rules) - limit} more")
        return "\n".join(lines)

    def run(
        self,
        event: HookEvent,
        *,
        controller: Any | None = None,
        allow_execute: bool = True,
    ) -> list[HookResult]:
        if event.event not in HOOK_EVENTS:
            return []
        results: list[HookResult] = []
        for rule in self.list_rules():
            if not rule.enabled or rule.event != event.event:
                continue
            count_key = (event.task_id or event.session_id or "global", rule.hook_id)
            count = self._counts.get(count_key, 0)
            if count >= 3:
                results.append(HookResult(rule.hook_id, rule.action, "skipped", "hook execution limit reached"))
                continue
            self._counts[count_key] = count + 1
            if rule.action == "notify":
                results.append(HookResult(rule.hook_id, rule.action, "ok", _render_template(rule.message, event.payload)))
            elif rule.action == "inject_context":
                message = _render_template(rule.context or rule.message, event.payload)
                if controller is not None and message:
                    controller.conversation_manager.context[f"hook:{rule.hook_id}"] = message
                results.append(HookResult(rule.hook_id, rule.action, "ok", message))
            elif rule.action == "execute_command":
                results.append(self._execute_command_hook(rule, event, controller=controller, allow_execute=allow_execute))
        return results

    def _execute_command_hook(
        self,
        rule: HookRule,
        event: HookEvent,
        *,
        controller: Any | None,
        allow_execute: bool,
    ) -> HookResult:
        if not allow_execute:
            return HookResult(rule.hook_id, rule.action, "skipped", "execute_command hooks are disabled for this event")
        if controller is None or getattr(controller, "dynamic_registry", None) is None:
            return HookResult(rule.hook_id, rule.action, "error", "dynamic command runtime is unavailable")
        if not rule.cmd_template:
            return HookResult(rule.hook_id, rule.action, "error", "hook cmd_template is empty")
        payload = {
            "tool_name": f"hook_{rule.hook_id}",
            "cmd_template": list(rule.cmd_template),
            "args": dict(rule.args),
            "intent_summary": f"Hook {rule.hook_id} for {event.event}",
            "consequences": "Hook command configured by local SysDialogue policy.",
            "risk_assessment": "Hook command uses DynTool safety, policy, audit, and trace gates.",
            "estimated_risk": "UNKNOWN",
            "changes_state": True,
            "timeout": min(max(int(rule.timeout or 10), 1), 30),
        }
        try:
            result = controller._handle_execute_dynamic_tool(payload, f"hook_{rule.hook_id}")
        except Exception as exc:
            return HookResult(rule.hook_id, rule.action, "error", str(exc), {"error_type": type(exc).__name__})
        status = "error" if result.get("is_error") else "ok"
        return HookResult(rule.hook_id, rule.action, status, str(result.get("content") or "")[:500])


def _load_rules(path: Path, *, source: str) -> list[HookRule]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    raw_rules = data.get("hooks") if isinstance(data, dict) else data
    if not isinstance(raw_rules, list):
        return []
    rules: list[HookRule] = []
    for index, item in enumerate(raw_rules):
        if not isinstance(item, dict):
            continue
        event = str(item.get("event") or "").strip()
        action = str(item.get("action") or "").strip()
        if event not in HOOK_EVENTS or action not in HOOK_ACTIONS:
            continue
        try:
            timeout = int(item.get("timeout") or 10)
        except (TypeError, ValueError):
            timeout = 10
        rules.append(
            HookRule(
                hook_id=str(item.get("id") or item.get("hook_id") or f"{source}_{index + 1}"),
                event=event,
                action=action,
                message=str(item.get("message") or ""),
                context=str(item.get("context") or ""),
                cmd_template=tuple(str(token) for token in (item.get("cmd_template") or [])[:10]),
                args=dict(item.get("args") or {}),
                timeout=min(max(timeout, 1), 30),
                enabled=bool(item.get("enabled", True)),
                source=source,
            )
        )
    return rules


def _render_template(template: str, payload: dict[str, Any]) -> str:
    rendered = str(template or "")
    for key, value in (payload or {}).items():
        if isinstance(value, (str, int, float, bool)):
            rendered = rendered.replace("{" + str(key) + "}", sanitize_text(value, limit=1000))
    return sanitize_text(rendered, limit=4000)
