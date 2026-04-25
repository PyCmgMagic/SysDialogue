"""High-risk operation confirmation modal for the TUI."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, ScrollableContainer, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static

if TYPE_CHECKING:
    from sysdialogue.security.approval_rules import ConfirmationRequest


_RISK_META: dict[str, tuple[str, str]] = {
    "BLOCK": ("🔴  BLOCK", "此操作已被策略永久拦截，无法授权执行。"),
    "WARN-HIGH": ("🟠  WARN-HIGH", "高风险操作，可能对系统造成不可逆影响，需要你明确授权。"),
    "WARN-MED": ("🟡  WARN-MED", "中等风险操作，建议确认参数无误后授权执行。"),
    "WARN-LOW": ("🟢  WARN-LOW", "低风险操作，确认后即可继续。"),
}


class ConfirmModal(ModalScreen[dict]):
    """Return an approval decision object for WARN-HIGH confirmations."""

    CSS = """
    ConfirmModal {
        align: center middle;
    }

    #modal_box {
        width: 84%;
        max-width: 96;
        height: auto;
        max-height: 88%;
        border: heavy $warning;
        background: $surface;
        layout: vertical;
    }

    #risk_banner {
        text-align: center;
        text-style: bold;
        padding: 0 2;
        height: 3;
        content-align: center middle;
    }

    #risk_banner.level-BLOCK,
    #risk_banner.level-WARN-HIGH {
        background: $error 20%;
        color: $error;
    }

    #risk_banner.level-WARN-MED,
    #risk_banner.level-WARN-LOW {
        background: $warning 20%;
        color: $warning;
    }

    #risk_explanation {
        text-align: center;
        color: $text-muted;
        padding: 0 2 1 2;
        height: auto;
    }

    #modal_body_scroll {
        height: 1fr;
        min-height: 14;
        max-height: 32;
        border: round $primary 25%;
        margin: 0 2;
        padding: 0;
    }

    #modal_body {
        padding: 1 2;
        height: auto;
    }

    .section_label {
        text-style: bold;
        color: $text-muted;
        margin: 1 0 0 0;
    }

    .section_value {
        padding: 0 0 0 2;
        margin: 0 0 1 0;
    }

    .args_block {
        background: $boost 8%;
        border: round $primary 20%;
        padding: 0 1;
        margin: 0 0 1 0;
    }

    #modal_buttons {
        height: 4;
        align: center middle;
        padding: 0 2;
        border-top: solid $primary 15%;
    }

    #modal_buttons Button {
        margin: 0 1;
        min-width: 18;
    }
    """

    BINDINGS = [
        Binding("enter", "approve_once", "批准本次"),
        Binding("escape", "deny", "拒绝"),
        Binding("y", "approve_once", show=False),
        Binding("a", "approve_always", "本会话总是允许"),
        Binding("n", "deny", show=False),
    ]

    def __init__(self, request: "ConfirmationRequest"):
        super().__init__()
        self.request = request

    def compose(self) -> ComposeResult:
        request = self.request
        level = request.risk.level or "WARN-HIGH"
        badge_label, explanation = _RISK_META.get(level, ("⚠  " + level, "请确认后再执行此操作。"))
        is_block = level == "BLOCK"

        buttons: list[Button] = []
        if not is_block:
            buttons.extend(
                [
                    Button("批准本次 (Enter)", id="btn_approve_once", variant="warning"),
                    Button("本会话总是允许 (A)", id="btn_approve_always", variant="success"),
                ]
            )
        buttons.append(Button("拒绝 / 关闭 (Esc)", id="btn_deny", variant="default"))

        yield Container(
            Static(
                f"  操作审批请求  —  {badge_label}  ",
                id="risk_banner",
                classes=f"level-{level}",
            ),
            Static(explanation, id="risk_explanation"),
            ScrollableContainer(
                Vertical(*self._build_body_rows(request), id="modal_body"),
                id="modal_body_scroll",
            ),
            Horizontal(*buttons, id="modal_buttons"),
            id="modal_box",
        )

    def _build_body_rows(self, request: "ConfirmationRequest") -> list[Static]:
        try:
            args_str = json.dumps(request.args, ensure_ascii=False, indent=2)
        except Exception:
            args_str = str(request.args)

        rule_ids = request.risk.rule_ids or []
        rollback = request.rollback_hint or request.risk.rollback_hint
        return [
            Static("操作工具", classes="section_label"),
            Static(request.tool or "（未知）", classes="section_value"),
            Static("风险原因", classes="section_label"),
            Static(request.risk.reason or "未提供具体原因。", classes="section_value"),
            Static("触发规则", classes="section_label"),
            Static("、".join(rule_ids) if rule_ids else "—", classes="section_value"),
            Static("调用参数", classes="section_label"),
            Static(args_str, classes="args_block"),
            Static("回滚方案", classes="section_label"),
            Static(
                rollback or "⚠  无自动回滚方案，操作执行后需手动恢复。",
                classes="section_value",
            ),
        ]

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_approve_once":
            self.action_approve_once()
        elif event.button.id == "btn_approve_always":
            self.action_approve_always()
        else:
            self.action_deny()

    def action_approve_once(self) -> None:
        if (self.request.risk.level or "") == "BLOCK":
            return
        self.dismiss({"approved": True, "decision": "once"})

    def action_approve_always(self) -> None:
        if (self.request.risk.level or "") == "BLOCK":
            return
        self.dismiss({"approved": True, "decision": "always_this_session"})

    def action_deny(self) -> None:
        self.dismiss({"approved": False, "decision": "deny"})
