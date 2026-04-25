"""Argument normalization helpers for dynamic tool execution."""

from __future__ import annotations

import re
import shlex
from typing import Any


_DYN_INLINE_KEYS = {
    "tool_id",
    "tool_name",
    "cmd_template",
    "command",
    "cmd",
    "argv",
    "command_template",
    "command_argv",
    "execution_mode",
    "shell_command",
    "privileged",
    "cwd",
    "params",
    "intent_summary",
    "consequences",
    "risk_assessment",
    "estimated_risk",
    "changes_state",
    "reversible",
    "timeout",
}


_COMMAND_ALIASES = (
    "cmd_template",
    "argv",
    "command_argv",
    "command_template",
    "cmd",
    "command",
)


def normalize_execute_dynamic_tool_args(args: Any) -> Any:
    """Accept the common nested inline shape without weakening plan checks.

    Some models put the inline DynTool spec inside ``execute_dynamic_tool.args``
    and leave only narrative metadata at the top level. The tool schema expects
    the DynTool spec at the top level and reserves ``args`` for template
    bindings. Normalize before frozen plan matching so semantically equivalent
    calls are not rejected as plan deviations.
    """
    if not isinstance(args, dict):
        return args
    args = _normalize_dynamic_command_aliases(args)
    nested = args.get("args")
    if not isinstance(nested, dict):
        return args
    nested = _normalize_dynamic_command_aliases(nested)

    has_top_level_spec = bool(args.get("tool_id")) or bool(args.get("cmd_template")) or bool(args.get("shell_command"))
    has_nested_spec = bool(nested.get("tool_id")) or bool(nested.get("cmd_template")) or bool(nested.get("shell_command"))
    if has_top_level_spec or not has_nested_spec:
        return args

    normalized = dict(args)
    for key in _DYN_INLINE_KEYS:
        if key in nested and key not in normalized:
            normalized[key] = nested[key]

    template_args = nested.get("args")
    if isinstance(template_args, dict):
        normalized["args"] = template_args
        return normalized

    leftovers = {
        key: value
        for key, value in nested.items()
        if key not in _DYN_INLINE_KEYS and key != "continue_on_failure"
    }
    normalized["args"] = leftovers
    return normalized


def normalize_propose_dynamic_tool_args(args: Any) -> Any:
    """Normalize common DynTool proposal aliases before registration."""
    if not isinstance(args, dict):
        return args
    normalized = _normalize_dynamic_command_aliases(args)
    if normalized.get("shell_command") and not normalized.get("execution_mode"):
        normalized["execution_mode"] = "shell"
    if not str(normalized.get("proposed_tool_name") or "").strip():
        fallback_name = (
            normalized.get("tool_name")
            or normalized.get("name")
            or _default_tool_name(normalized.get("cmd_template") or normalized.get("shell_command"))
        )
        if fallback_name:
            normalized["proposed_tool_name"] = fallback_name
    if not str(normalized.get("intent_summary") or "").strip():
        template = normalized.get("cmd_template") or []
        if template:
            normalized["intent_summary"] = "Run dynamic command: " + " ".join(str(item) for item in template[:4])
        elif normalized.get("shell_command"):
            normalized["intent_summary"] = "Run dynamic shell command"
    return normalized


def _normalize_dynamic_command_aliases(args: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(args)
    if normalized.get("shell_command") and not normalized.get("execution_mode"):
        normalized["execution_mode"] = "shell"
    if not normalized.get("cmd_template"):
        for key in _COMMAND_ALIASES:
            if key not in normalized:
                continue
            template = _coerce_cmd_template(normalized.get(key))
            if template:
                normalized["cmd_template"] = template
                break
    if not str(normalized.get("tool_name") or "").strip():
        fallback = normalized.get("proposed_tool_name") or normalized.get("name")
        if fallback:
            normalized["tool_name"] = fallback
        else:
            generated = _default_tool_name(normalized.get("cmd_template"))
            if not generated and normalized.get("shell_command"):
                generated = _default_tool_name(normalized.get("shell_command"))
            if generated:
                normalized["tool_name"] = generated
    return normalized


def _coerce_cmd_template(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, tuple):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            return shlex.split(text, posix=True)
        except ValueError:
            return [text]
    return []


def _default_tool_name(cmd_template: Any) -> str:
    template = _coerce_cmd_template(cmd_template)
    if not template:
        return ""
    base = str(template[0]).replace("\\", "/").rsplit("/", 1)[-1]
    base = re.sub(r"[^A-Za-z0-9_]+", "_", base).strip("_").lower()
    if not base:
        base = "command"
    return f"adhoc_{base}"[:64]
