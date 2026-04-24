"""HistoryModal — 历史会话选择器。"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Static

from sysdialogue.agent.conversation_store import ConversationSummary


_STATUS_BADGES = {
    "completed":   "✓ 已完成",
    "failed":      "✕ 异常",
    "cancelled":   "○ 已取消",
    "interrupted": "◐ 中断",
    "running":     "◐ 运行中",
}


class HistoryModal(ModalScreen[str | None]):
    """浏览并恢复历史对话。按 Enter 恢复选中会话，Esc 关闭。"""

    CSS = """
    HistoryModal {
        align: center middle;
    }

    #history_box {
        width: 92%;
        max-width: 120;
        height: 80%;
        max-height: 44;
        border: heavy $primary;
        background: $surface;
        layout: vertical;
    }

    #history_header {
        background: $primary 20%;
        padding: 0 2;
        height: 2;
        content-align: left middle;
        text-style: bold;
        border-bottom: solid $primary 20%;
    }

    #history_hint {
        padding: 0 2;
        height: 1;
        color: $text-muted;
        border-bottom: solid $primary 10%;
    }

    #history_table {
        height: 1fr;
    }

    #history_buttons {
        height: 4;
        align: center middle;
        padding: 0 2;
        border-top: solid $primary 15%;
    }

    #history_buttons Button {
        margin: 0 2;
        min-width: 18;
    }
    """

    BINDINGS = [
        Binding("enter",  "restore", "恢复会话"),
        Binding("escape", "cancel",  "关闭"),
    ]

    def __init__(self, summaries: list[ConversationSummary]):
        super().__init__()
        self.summaries = summaries

    def compose(self) -> ComposeResult:
        yield Container(
            Static("📂  历史会话", id="history_header"),
            Static(
                "↑ ↓ 选择行  ·  Enter 恢复  ·  Esc 关闭  ·  恢复后上下文立即生效，历史工具不重放",
                id="history_hint",
            ),
            DataTable(id="history_table", zebra_stripes=True),
            Horizontal(
                Button("恢复会话  ↵", id="btn_restore", variant="primary"),
                Button("关闭  Esc",   id="btn_cancel",  variant="default"),
                id="history_buttons",
            ),
            id="history_box",
        )

    def on_mount(self) -> None:
        table = self.query_one("#history_table", DataTable)
        table.cursor_type = "row"
        table.add_columns("时间", "状态", "会话主题", "最近一条指令")
        for s in self.summaries:
            table.add_row(
                _time_label(s.updated_at),
                _STATUS_BADGES.get(s.status, s.status),
                _truncate(s.title, 32),
                _truncate(s.last_user_message, 44),
                key=s.session_id,
            )
        if self.summaries:
            table.focus()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.dismiss(str(event.row_key.value))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_restore":
            self.action_restore()
        else:
            self.action_cancel()

    def action_restore(self) -> None:
        table = self.query_one("#history_table", DataTable)
        if not self.summaries or table.cursor_row < 0:
            self.dismiss(None)
            return
        row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
        self.dismiss(str(row_key.value))

    def action_cancel(self) -> None:
        self.dismiss(None)


def _time_label(value: str) -> str:
    if not value:
        return "—"
    # "2026-04-25T14:32:09" → "04-25 14:32"
    v = value.replace("T", " ")
    return v[5:16] if len(v) >= 16 else v


def _truncate(text: str, max_len: int) -> str:
    if not text:
        return "—"
    text = text.strip()
    return text if len(text) <= max_len else text[:max_len - 1] + "…"
