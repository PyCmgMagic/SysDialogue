"""InputModal — 工作流 / 智能代理补充输入弹窗。"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static

try:
    from textual.widgets import TextArea
except ImportError:  # pragma: no cover
    TextArea = None  # type: ignore[assignment]


class InputModal(ModalScreen[str | None]):
    """收集单行或多行补充输入，由工作流或智能代理在执行中途触发。"""

    CSS = """
    InputModal {
        align: center middle;
    }

    #modal_box {
        width: 84%;
        max-width: 104;
        height: auto;
        max-height: 88%;
        border: heavy $accent;
        background: $surface;
        layout: vertical;
    }

    /* ── 顶部标题 ── */
    #modal_header {
        background: $accent 20%;
        padding: 0 2;
        height: 2;
        content-align: left middle;
        text-style: bold;
        border-bottom: solid $accent 20%;
    }

    /* ── 提示语区 ── */
    #modal_prompt_area {
        padding: 1 2 0 2;
        height: auto;
    }

    #modal_prompt_label {
        color: $text-muted;
        text-style: bold;
        height: 1;
    }

    #modal_prompt_text {
        padding: 0 0 1 0;
        height: auto;
    }

    /* ── 输入控件 ── */
    #modal_input_area {
        padding: 0 2;
        height: auto;
    }

    #modal_input {
        width: 100%;
        border: round $accent 40%;
    }

    #modal_input:focus {
        border: round $accent 80%;
    }

    #modal_textarea {
        width: 100%;
        height: 10;
        border: round $accent 40%;
    }

    #modal_textarea:focus {
        border: round $accent 80%;
    }

    /* ── 快捷键提示 ── */
    #modal_keyhint {
        padding: 0 2 1 2;
        color: $text-muted;
        height: 1;
    }

    /* ── 按钮 ── */
    #modal_buttons {
        height: 4;
        align: center middle;
        padding: 0 2;
        border-top: solid $primary 15%;
    }

    #modal_buttons Button {
        margin: 0 2;
        min-width: 16;
    }
    """

    BINDINGS = [
        Binding("f2",         "submit", "提交"),
        Binding("ctrl+enter", "submit", show=False),
        Binding("escape",     "cancel", "取消"),
    ]

    def __init__(self, prompt: str, multiline: bool):
        super().__init__()
        self.prompt         = prompt
        self.multiline      = multiline
        self._use_textarea  = bool(multiline and TextArea is not None)

    def compose(self) -> ComposeResult:
        if self.multiline:
            title    = "✏  多行输入请求"
            keyhint  = (
                "F2 或 Ctrl+Enter 提交  ·  Esc 取消输入"
                if self._use_textarea
                else "Enter / F2 提交  ·  Esc 取消  （当前环境不支持多行，将以单行模式采集）"
            )
        else:
            title   = "✏  输入请求"
            keyhint = "Enter 或 F2 提交  ·  Esc 取消"

        yield Container(
            Static(f"  {title}", id="modal_header"),
            Vertical(
                Static("智能代理需要你提供以下信息：", id="modal_prompt_label"),
                Static(self.prompt or "（无具体说明）", id="modal_prompt_text"),
                id="modal_prompt_area",
            ),
            Vertical(
                self._build_input_widget(),
                id="modal_input_area",
            ),
            Static(keyhint, id="modal_keyhint"),
            Horizontal(
                Button("提交  F2", id="btn_submit", variant="primary"),
                Button("取消  Esc", id="btn_cancel", variant="default"),
                id="modal_buttons",
            ),
            id="modal_box",
        )

    def _build_input_widget(self):
        if self._use_textarea:
            return TextArea(id="modal_textarea")  # type: ignore[misc]
        placeholder = "在此输入内容…"
        if self.multiline and not self._use_textarea:
            placeholder = "在此输入内容（当前版本不支持多行，按 Enter 提交）…"
        return Input(placeholder=placeholder, id="modal_input")

    def on_mount(self) -> None:
        if self._use_textarea:
            self.query_one("#modal_textarea").focus()
        else:
            self.query_one("#modal_input", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_submit":
            self.dismiss(self._read_value())
        else:
            self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "modal_input":
            self.dismiss(event.value)

    def action_submit(self) -> None:
        self.dismiss(self._read_value())

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _read_value(self) -> str:
        if self._use_textarea:
            return self.query_one("#modal_textarea").text  # type: ignore[no-any-return]
        return self.query_one("#modal_input", Input).value
