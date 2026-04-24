"""InputModal - workflow/user input collection dialog."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static

try:
    from textual.widgets import TextArea
except ImportError:  # pragma: no cover - depends on installed Textual version
    TextArea = None  # type: ignore[assignment]


class InputModal(ModalScreen[str | None]):
    """Collect a single-line or multi-line input value."""

    CSS = """
    InputModal {
        align: center middle;
    }
    #modal_box {
        width: 82%;
        max-width: 100;
        height: auto;
        max-height: 85%;
        border: heavy $accent;
        background: $surface;
        padding: 1 2;
    }
    #modal_title {
        text-align: center;
        text-style: bold;
        background: $accent 25%;
        padding: 0 1;
    }
    #modal_prompt {
        padding: 1 0;
    }
    #modal_hint {
        color: $text-muted;
        padding: 0 0 1 0;
    }
    #modal_input {
        width: 100%;
    }
    #modal_textarea {
        width: 100%;
        height: 12;
    }
    #modal_buttons {
        height: 3;
        align: center middle;
        margin-top: 1;
    }
    #modal_buttons Button {
        margin: 0 1;
    }
    """

    BINDINGS = [
        Binding("f2", "submit", "提交"),
        Binding("ctrl+enter", "submit", show=False),
        Binding("escape", "cancel", "取消"),
    ]

    def __init__(self, prompt: str, multiline: bool, sensitive: bool = False):
        super().__init__()
        self.prompt = prompt
        self.multiline = multiline
        self.sensitive = bool(sensitive)
        # Sensitive inputs (passwords) always collapse to single-line masked Input,
        # regardless of multiline, so the value never renders in a TextArea.
        self._use_textarea = bool(multiline and TextArea is not None and not self.sensitive)

    def compose(self) -> ComposeResult:
        hint = "按 F2 或 Ctrl+Enter 提交，Esc 取消。"
        if not self.multiline:
            hint = "按 Enter 或 F2 提交，Esc 取消。"
        elif not self._use_textarea:
            hint = "当前 Textual 版本缺少 TextArea，将退化为单行输入；按 Enter 或 F2 提交。"

        yield Container(
            Static("输入请求", id="modal_title"),
            Static(self.prompt, id="modal_prompt"),
            Static(hint, id="modal_hint"),
            Vertical(self._compose_input_widget()),
            Horizontal(
                Button("提交", id="btn_submit", variant="primary"),
                Button("取消", id="btn_cancel", variant="default"),
                id="modal_buttons",
            ),
            id="modal_box",
        )

    def _compose_input_widget(self):
        if self._use_textarea:
            yield TextArea(id="modal_textarea")  # type: ignore[misc]
        else:
            placeholder = "请输入内容"
            if self.sensitive:
                placeholder = "请输入密码（不回显）"
            elif self.multiline:
                placeholder = "请输入内容（当前环境将按单行模式采集）"
            yield Input(
                placeholder=placeholder,
                id="modal_input",
                password=self.sensitive,
            )

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
