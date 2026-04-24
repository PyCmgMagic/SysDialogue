"""Argument normalization helpers for dynamic tool execution."""

from __future__ import annotations

from typing import Any


_DYN_INLINE_KEYS = {
    "tool_id",
    "tool_name",
    "cmd_template",
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
    nested = args.get("args")
    if not isinstance(nested, dict):
        return args

    has_top_level_spec = bool(args.get("tool_id")) or bool(args.get("cmd_template"))
    has_nested_spec = bool(nested.get("tool_id")) or bool(nested.get("cmd_template"))
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
