"""Conversation history picker for the TUI."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Static

from sysdialogue.agent.conversation_store import ConversationSummary


class HistoryModal(ModalScreen[str | None]):
    """Pick a persisted conversation to restore."""

    CSS = """
    HistoryModal {
        align: center middle;
    }
    #history_box {
        width: 90%;
        max-width: 110;
        height: 80%;
        border: heavy $primary;
        background: $surface;
        padding: 1 2;
        layout: vertical;
    }
    #history_title {
        text-style: bold;
        background: $primary 25%;
        padding: 0 1;
    }
    #history_table {
        height: 1fr;
        margin-top: 1;
    }
    #history_buttons {
        height: 3;
        align: center middle;
        margin-top: 1;
    }
    #history_buttons Button {
        margin: 0 2;
    }
    """

    BINDINGS = [
        Binding("enter", "restore", "恢复"),
        Binding("escape", "cancel", "关闭"),
    ]

    def __init__(self, summaries: list[ConversationSummary]):
        super().__init__()
        self.summaries = summaries

    def compose(self) -> ComposeResult:
        yield Container(
            Static("历史对话（Enter 恢复 / Esc 关闭）", id="history_title"),
            DataTable(id="history_table", zebra_stripes=True),
            Horizontal(
                Button("恢复 (Enter)", id="btn_restore", variant="primary"),
                Button("关闭 (Esc)", id="btn_cancel", variant="default"),
                id="history_buttons",
            ),
            id="history_box",
        )

    def on_mount(self) -> None:
        table = self.query_one("#history_table", DataTable)
        table.cursor_type = "row"
        table.add_columns("更新时间", "状态", "标题", "最近请求")
        for summary in self.summaries:
            table.add_row(
                _time_label(summary.updated_at),
                summary.status,
                summary.title,
                summary.last_user_message,
                key=summary.session_id,
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
        return "-"
    return value.replace("T", " ")[:16]
