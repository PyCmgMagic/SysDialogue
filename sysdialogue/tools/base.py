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

    def to_dict(self) -> dict:
        d: dict = {"success": self.success}
        if self.data is not None:
            d["data"] = self.data
        if self.error:
            d["error"] = self.error
        return d
