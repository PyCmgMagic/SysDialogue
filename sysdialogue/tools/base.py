"""工具基础框架 — ToolResult 和工具定义结构。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolResult:
    success: bool
    data: Any = None
    error: str = ""
    exit_code: int = 0
    cmd_trace: list[str] = field(default_factory=list)

    def to_dict(self, *, sanitize: bool = True) -> dict:
        d: dict = {"success": self.success}
        if self.data is not None:
            d["data"] = self.data
        if self.error:
            d["error"] = self.error
        if self.exit_code:
            d["exit_code"] = self.exit_code
        if self.cmd_trace:
            d["cmd_trace"] = self.cmd_trace
        if sanitize:
            from sysdialogue.security.output_sanitizer import sanitize_value

            return sanitize_value(d)
        return d
