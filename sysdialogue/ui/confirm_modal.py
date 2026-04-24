"""ConfirmModal — WARN-HIGH 操作确认弹窗。"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, ScrollableContainer
from textual.screen import ModalScreen
from textual.widgets import Button, Static

if TYPE_CHECKING:
    from sysdialogue.security.approval_rules import ConfirmationRequest


class ConfirmModal(ModalScreen[dict]):
    """WARN-HIGH 操作确认。返回审批决策对象。"""

    CSS = """
    ConfirmModal {
        align: center middle;
    }
    #modal_box {
        width: 80%;
        max-width: 90;
        height: 80%;
        max-height: 80%;
        border: heavy $warning;
        background: $surface;
        padding: 1 2;
        layout: vertical;
    }
    #modal_title {
        text-align: center;
        text-style: bold;
        background: $warning 30%;
        padding: 0 1;
    }
    #modal_body_scroll {
        height: 1fr;
        border: round $warning 35%;
        margin-top: 1;
        padding: 1 0;
    }
    #modal_body {
        height: auto;
        padding: 0 1;
    }
    #modal_buttons {
        height: 3;
        align: center middle;
        dock: bottom;
        margin-top: 1;
    }
    #modal_buttons Button {
        margin: 0 2;
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
        yield Container(
            Static("⚠️  操作确认 — WARN-HIGH", id="modal_title"),
            ScrollableContainer(
                Static(self._render_body(), id="modal_body"),
                id="modal_body_scroll",
            ),
            Horizontal(
                Button("批准本次 (Enter)", id="btn_approve_once", variant="warning"),
                Button("本会话总是允许 (A)", id="btn_approve_always", variant="success"),
                Button("拒绝 (Esc)", id="btn_deny", variant="default"),
                id="modal_buttons",
            ),
            id="modal_box",
        )

    def _render_body(self) -> str:
        r = self.request
        try:
            args_str = json.dumps(r.args, ensure_ascii=False, indent=2)
        except Exception:
            args_str = str(r.args)
        rule_ids = ", ".join(r.risk.rule_ids) if r.risk.rule_ids else "-"
        rollback = r.rollback_hint or r.risk.rollback_hint or "无自动回滚方案"
        return (
            f"工具：{r.tool}\n"
            f"风险等级：{r.risk.level}\n"
            f"规则 ID：{rule_ids}\n"
            f"\n"
            f"原因：{r.risk.reason}\n"
            f"\n"
            f"参数：\n{args_str}\n"
            f"\n"
            f"回滚方案：{rollback}"
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_approve_once":
            self.dismiss({"approved": True, "decision": "once"})
        elif event.button.id == "btn_approve_always":
            self.dismiss({"approved": True, "decision": "always_this_session"})
        else:
            self.dismiss({"approved": False, "decision": "deny"})

    def action_approve_once(self) -> None:
        self.dismiss({"approved": True, "decision": "once"})

    def action_approve_always(self) -> None:
        self.dismiss({"approved": True, "decision": "always_this_session"})

    def action_deny(self) -> None:
        self.dismiss({"approved": False, "decision": "deny"})
