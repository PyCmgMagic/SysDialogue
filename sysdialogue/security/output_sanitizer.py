"""Unified redaction helpers for user-visible and persisted output."""

from __future__ import annotations

import json
import re
from typing import Any


REDACTED = "<redacted>"
TRUNCATED = "[OUTPUT TRUNCATED]"
DEFAULT_TEXT_LIMIT = 12000
DEFAULT_CONTAINER_LIMIT = 80

_SENSITIVE_KEY_RE = re.compile(
    r"(?i)(password|passwd|pwd|secret|token|api[_-]?key|apikey|authorization|"
    r"access[_-]?key|session[_-]?key|credential|private[_-]?key|client[_-]?secret)"
)
_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
    re.S,
)
_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(api[_-]?key|token|secret|password|passwd|pwd|client[_-]?secret|"
    r"access[_-]?key|authorization)\b\s*[:=]\s*(['\"]?)[^\s'\"\n\r]+",
)
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_SK_KEY_RE = re.compile(r"\bsk-[A-Za-z0-9_\-]{4,}\b")
_AWS_ACCESS_KEY_RE = re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b")
_AWS_SECRET_RE = re.compile(r"(?i)\baws_secret_access_key\s*=\s*[A-Za-z0-9/+=]{20,}")
_KUBE_TOKEN_RE = re.compile(r"(?i)\b(token|client-key-data|client-certificate-data):\s+[A-Za-z0-9+/=._-]{20,}")


def sanitize_text(value: Any, *, limit: int = DEFAULT_TEXT_LIMIT) -> str:
    """Redact common credentials from text and cap long output."""
    text = str(value or "")
    text = _PRIVATE_KEY_RE.sub(REDACTED, text)
    text = _AWS_SECRET_RE.sub(lambda match: f"{match.group(0).split('=')[0].strip()}={REDACTED}", text)
    text = _KUBE_TOKEN_RE.sub(lambda match: f"{match.group(1)}: {REDACTED}", text)
    text = _BEARER_RE.sub(f"Bearer {REDACTED}", text)
    text = _SK_KEY_RE.sub(REDACTED, text)
    text = _AWS_ACCESS_KEY_RE.sub(REDACTED, text)
    text = _ASSIGNMENT_RE.sub(lambda match: f"{match.group(1)}={REDACTED}", text)
    if limit and len(text) > limit:
        return text[: max(0, limit - len(TRUNCATED) - 1)] + "\n" + TRUNCATED
    return text


def sanitize_command(cmd: Any, *, limit: int = DEFAULT_TEXT_LIMIT) -> Any:
    """Sanitize command argv or command-like strings for logs and exports."""
    if isinstance(cmd, list):
        return [sanitize_text(str(part), limit=limit) for part in cmd[:DEFAULT_CONTAINER_LIMIT]]
    if isinstance(cmd, tuple):
        return tuple(sanitize_text(str(part), limit=limit) for part in cmd[:DEFAULT_CONTAINER_LIMIT])
    return sanitize_text(cmd, limit=limit)


def sanitize_value(value: Any, *, limit: int = DEFAULT_TEXT_LIMIT, depth: int = 0) -> Any:
    """Recursively sanitize a JSON-like value without mutating the input."""
    if depth > 8:
        return "<truncated>"
    if isinstance(value, dict):
        safe: dict[str, Any] = {}
        for key, item in list(value.items())[:DEFAULT_CONTAINER_LIMIT]:
            key_str = str(key)
            if _SENSITIVE_KEY_RE.search(key_str):
                safe[key_str] = REDACTED
            elif key_str in {"cmd", "cmd_trace", "command", "argv"}:
                safe[key_str] = sanitize_command(item, limit=limit)
            else:
                safe[key_str] = sanitize_value(item, limit=limit, depth=depth + 1)
        return safe
    if isinstance(value, list):
        return [sanitize_value(item, limit=limit, depth=depth + 1) for item in value[:DEFAULT_CONTAINER_LIMIT]]
    if isinstance(value, tuple):
        return tuple(sanitize_value(item, limit=limit, depth=depth + 1) for item in value[:DEFAULT_CONTAINER_LIMIT])
    if isinstance(value, str):
        return sanitize_text(value, limit=limit)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return sanitize_text(str(value), limit=limit)


def sanitize_tool_result(result: Any, *, limit: int = DEFAULT_TEXT_LIMIT) -> dict[str, Any]:
    """Convert a ToolResult-like object to a sanitized dictionary."""
    if hasattr(result, "to_dict"):
        try:
            raw = result.to_dict(sanitize=False)
        except TypeError:
            raw = result.to_dict()
    elif isinstance(result, dict):
        raw = dict(result)
    else:
        raw = {"data": str(result)}
    return sanitize_value(raw, limit=limit)


def sanitize_json_text(value: Any, *, limit: int = DEFAULT_TEXT_LIMIT) -> str:
    try:
        rendered = json.dumps(sanitize_value(value, limit=limit), ensure_ascii=False, default=str)
    except Exception:
        rendered = sanitize_text(str(value), limit=limit)
    return sanitize_text(rendered, limit=limit)
