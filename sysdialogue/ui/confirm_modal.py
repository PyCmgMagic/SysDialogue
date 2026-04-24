"""ConfirmModal — 高风险操作二次确认弹窗。"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, ScrollableContainer, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static

if TYPE_CHECKING:
    from sysdialogue.security.approval_rules import ConfirmationRequest


_RISK_META: dict[str, tuple[str, str, str]] = {
    # level → (badge_label, border_color, explanation)
    "BLOCK":     ("🔴  BLOCK",     "red",    "此操作已被策略永久拦截，无法授权执行。"),
    "WARN-HIGH": ("🟠  WARN-HIGH", "red",    "高风险操作，可能对系统造成不可逆影响，需要你明确授权。"),
    "WARN-MED":  ("🟡  WARN-MED",  "yellow", "中等风险操作，建议确认参数无误后授权执行。"),
    "WARN-LOW":  ("🟢  WARN-LOW",  "yellow", "低风险操作，确认后即可继续。"),
}


class ConfirmModal(ModalScreen[bool]):
    """高风险操作二次确认弹窗。返回 True = 允许执行 / False = 拒绝。"""

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

    /* ── 顶部风险标题栏 ── */
    #risk_banner {
        text-align: center;
        text-style: bold;
        padding: 0 2;
        height: 3;
        content-align: center middle;
    }
    #risk_banner.level-BLOCK     { background: $error   40%; color: $error; }
    #risk_banner.level-WARN-HIGH { background: $error   25%; color: $error; }
    #risk_banner.level-WARN-MED  { background: $warning 30%; color: $warning; }
    #risk_banner.level-WARN-LOW  { background: $warning 20%; color: $warning; }

    /* ── 风险说明副标题 ── */
    #risk_explanation {
        text-align: center;
        color: $text-muted;
        padding: 0 2 1 2;
        height: auto;
    }

    /* ── 主体信息滚动区 ── */
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

    /* ── 分区标签 ── */
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

    /* ── 操作按钮 ── */
    #modal_buttons {
        height: 4;
        align: center middle;
        padding: 0 2;
        border-top: solid $primary 15%;
    }

    #btn_approve {
        margin: 0 2 0 0;
        min-width: 22;
    }

    #btn_deny {
        min-width: 18;
    }
    """

    BINDINGS = [
        Binding("enter",   "approve", "允许执行"),
        Binding("escape",  "deny",    "拒绝"),
        Binding("y",       "approve", show=False),
        Binding("n",       "deny",    show=False),
    ]

    def __init__(self, request: "ConfirmationRequest"):
        super().__init__()
        self.request = request

    def compose(self) -> ComposeResult:
        r = self.request
        level = r.risk.level or "WARN-HIGH"
        badge_label, _, explanation = _RISK_META.get(
            level, ("⚠  " + level, "yellow", "请确认后再执行此操作。")
        )
        is_block = level == "BLOCK"

        yield Container(
            # ── 顶部风险标题 ──
            Static(
                f"  操作审批请求  —  {badge_label}  ",
                id="risk_banner",
                classes=f"level-{level}",
            ),
            Static(explanation, id="risk_explanation"),

            # ── 详情滚动区 ──
            ScrollableContainer(
                Vertical(
                    *self._build_body_rows(r),
                    id="modal_body",
                ),
                id="modal_body_scroll",
            ),

            # ── 按钮 ──
            Horizontal(
                *(
                    [
                        Button("允许执行  ↵", id="btn_approve", variant="warning"),
                        Button("拒绝  Esc",   id="btn_deny",    variant="default"),
                    ]
                    if not is_block else
                    [Button("确认关闭  Esc", id="btn_deny", variant="default")]
                ),
                id="modal_buttons",
            ),
            id="modal_box",
        )

    # ─────────────────────────────── body sections ──────────────────────────

    def _build_body_rows(self, r: "ConfirmationRequest") -> list:
        rows: list = []

        # 工具名称
        rows += [
            Static("操作工具", classes="section_label"),
            Static(r.tool or "（未知）", classes="section_value"),
        ]

        # 风险原因
        rows += [
            Static("风险原因", classes="section_label"),
            Static(r.risk.reason or "未提供具体原因。", classes="section_value"),
        ]

        # 匹配规则
        rule_ids = r.risk.rule_ids or []
        rows += [
            Static("触发规则", classes="section_label"),
            Static(
                "、".join(rule_ids) if rule_ids else "—",
                classes="section_value",
            ),
        ]

        # 调用参数
        try:
            args_str = json.dumps(r.args, ensure_ascii=False, indent=2)
        except Exception:
            args_str = str(r.args)
        rows += [
            Static("调用参数", classes="section_label"),
            Static(args_str, classes="args_block"),
        ]

        # 回滚方案
        rollback = r.rollback_hint or r.risk.rollback_hint
        rows += [
            Static("回滚方案", classes="section_label"),
            Static(
                rollback if rollback else "⚠  无自动回滚方案，操作执行后需手动恢复。",
                classes="section_value",
            ),
        ]

        return rows

    # ─────────────────────────────── events ─────────────────────────────────

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_approve":
            self.dismiss(True)
        else:
            self.dismiss(False)

    def action_approve(self) -> None:
        if (self.request.risk.level or "") == "BLOCK":
            return
        self.dismiss(True)

    def action_deny(self) -> None:
        self.dismiss(False)
