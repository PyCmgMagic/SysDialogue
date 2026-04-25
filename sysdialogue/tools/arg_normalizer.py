"""Normalize common model argument aliases before tool dispatch."""

from __future__ import annotations

import shlex
from typing import Any


def normalize_tool_call_args(name: str, args: Any) -> Any:
    """Return schema-compatible args for known fuzzy tool-call shapes."""
    if not isinstance(args, dict):
        return args
    if name == "manage_container":
        return _normalize_manage_container_args(args)
    if name == "modify_user_groups":
        return _normalize_modify_user_groups_args(args)
    return args


def _normalize_manage_container_args(args: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(args)
    normalized["action"] = str(normalized.get("action") or "list")

    if not normalized.get("name"):
        for alias in ("container_name", "container", "container_id"):
            value = normalized.pop(alias, None)
            if value:
                normalized["name"] = value
                break

    if "command" not in normalized:
        for alias in ("arguments", "argv", "cmd", "exec_command"):
            if alias in normalized:
                normalized["command"] = normalized.pop(alias)
                break
    else:
        for alias in ("arguments", "argv", "cmd", "exec_command"):
            normalized.pop(alias, None)

    if "command" in normalized:
        normalized["command"] = _coerce_argv(normalized.get("command"))

    allowed = {
        "action",
        "backend",
        "name",
        "image",
        "ports",
        "env_vars",
        "volumes",
        "restart_policy",
        "command",
        "lines",
        "retries",
        "interval_sec",
        "success_contains",
    }
    return {key: value for key, value in normalized.items() if key in allowed}


def _normalize_modify_user_groups_args(args: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(args)
    nested = normalized.pop("arguments", None)
    if isinstance(nested, dict):
        for key, value in nested.items():
            normalized.setdefault(key, value)
    elif nested is not None and "groups" not in normalized:
        normalized["groups"] = nested

    if "group" in normalized and "groups" not in normalized:
        normalized["groups"] = [normalized.pop("group")]
    if "groups" in normalized:
        value = normalized["groups"]
        if isinstance(value, str):
            normalized["groups"] = [item for item in (part.strip() for part in value.split(",")) if item]
        elif isinstance(value, (list, tuple)):
            normalized["groups"] = [str(item) for item in value if str(item).strip()]

    normalized["action"] = str(normalized.get("action") or "add")
    allowed = {"username", "groups", "action"}
    return {key: value for key, value in normalized.items() if key in allowed}


def _coerce_argv(value: Any) -> list[str]:
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
