"""用户确认协议 — 标准化 WARN-HIGH 确认请求的展示结构。"""

from __future__ import annotations

from dataclasses import dataclass

from sysdialogue.security.risk_classifier import RiskDecision


@dataclass
class ConfirmationRequest:
    tool: str
    args: dict
    risk: RiskDecision
    rollback_hint: str = ""

    def to_display(self) -> dict:
        return {
            "tool": self.tool,
            "risk_level": self.risk.level,
            "rule_ids": self.risk.rule_ids,
            "reason": self.risk.reason,
            "rollback_hint": self.rollback_hint or "无自动回滚方案",
            "args_preview": self.args,
        }
