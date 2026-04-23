"""SysDialogue TUI main interface."""

from __future__ import annotations

import threading
import traceback
from typing import TYPE_CHECKING, Any

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Vertical
from textual.widgets import Footer, Header, Input, RichLog, Static

from sysdialogue.ui.audit_panel import AuditPanel
from sysdialogue.ui.confirm_modal import ConfirmModal
from sysdialogue.ui.env_panel import EnvPanel
from sysdialogue.ui.input_modal import InputModal

if TYPE_CHECKING:
    from sysdialogue.agent.controller import AgentController
    from sysdialogue.security.approval_rules import ConfirmationRequest


class SysDialogueTUI(App):
    """SysDialogue v6 Textual application."""

    CSS = """
    Screen {
        layout: vertical;
    }
    #main_layout {
        height: 1fr;
        layout: horizontal;
    }
    #left_pane {
        width: 65%;
        height: 100%;
        layout: vertical;
    }
    #right_pane {
        width: 35%;
        height: 100%;
        background: $panel;
        border-left: tall $primary 40%;
    }
    #conversation {
        height: 1fr;
        border: round $primary 40%;
        padding: 0 1;
    }
    #status_bar {
        height: 1;
        background: $primary 20%;
        padding: 0 1;
    }
    """

    BINDINGS = [
        Binding("f3", "toggle_audit", "审计"),
        Binding("f4", "toggle_env", "环境"),
        Binding("ctrl+c", "cancel_current", "取消"),
        Binding("ctrl+d", "quit", "退出"),
        Binding("ctrl+l", "clear_log", "清屏"),
    ]

    def __init__(self, controller: "AgentController"):
        super().__init__()
        self.controller = controller
        controller.confirm_callback = self._confirm_callback
        controller.input_callback = self._input_callback
        self._right_panel_mode = "audit"
        self._worker: threading.Thread | None = None
        self._confirm_state: dict[str, Any] | None = None
        self._input_state: dict[str, Any] | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Container(
            Vertical(
                RichLog(id="conversation", highlight=True, markup=True, wrap=True),
                Input(
                    placeholder="输入运维需求（例：查看系统版本 / 重启 nginx）…",
                    id="user_input",
                ),
                id="left_pane",
            ),
            Vertical(
                AuditPanel(self.controller.audit_log),
                id="right_pane",
            ),
            id="main_layout",
        )
        yield Static("状态：就绪", id="status_bar")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "SysDialogue v6"
        self.sub_title = "Linux 运维智能代理"
        self._write_log(
            "[bold cyan]欢迎使用 SysDialogue v6[/bold cyan]\n"
            "输入自然语言运维需求，Enter 发送。\n"
            "F3 切换审计面板 / F4 切换环境画像 / Ctrl+C 取消当前执行 / Ctrl+D 退出。\n"
        )
        self.query_one("#user_input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "user_input":
            return

        text = (event.value or "").strip()
        if not text:
            return
        event.input.value = ""
        self._write_log(f"[bold green]> 你:[/bold green] {text}")

        event.input.disabled = True
        self._set_status("思考中…")

        def worker() -> None:
            try:
                reply = self.controller.run_turn(text)
            except Exception:
                reply = f"[错误] {traceback.format_exc()}"
            self.call_from_thread(self._on_turn_done, reply)

        self._worker = threading.Thread(target=worker, daemon=True)
        self._worker.start()

    def _on_turn_done(self, reply: str) -> None:
        self._worker = None
        self._write_log(f"[bold magenta]SysDialogue:[/bold magenta] {reply}\n")
        self._refresh_audit_panel()
        input_box = self.query_one("#user_input", Input)
        input_box.disabled = False
        input_box.focus()
        self._set_status("就绪")

    def _write_log(self, text: str) -> None:
        self.query_one("#conversation", RichLog).write(text)

    def _refresh_audit_panel(self) -> None:
        try:
            panel = self.query_one(AuditPanel)
            panel.refresh_data()
        except Exception:
            pass

    def _set_status(self, text: str) -> None:
        try:
            self.query_one("#status_bar", Static).update(f"状态：{text}")
        except Exception:
            pass

    def _refresh_runtime_status(self) -> None:
        if self.controller.is_cancel_requested():
            self._set_status("取消中…")
        elif self._confirm_state is not None:
            self._set_status("等待确认")
        elif self._input_state is not None:
            self._set_status("等待输入")
        elif self._worker is not None and self._worker.is_alive():
            self._set_status("思考中…")
        else:
            self._set_status("就绪")

    def _confirm_callback(self, req: "ConfirmationRequest") -> bool:
        event = threading.Event()
        result: dict[str, bool] = {"ok": False}
        state: dict[str, Any] = {
            "event": event,
            "result": result,
            "resolved": False,
            "screen": None,
        }
        self._confirm_state = state

        def show() -> None:
            if state["resolved"]:
                return
            self._write_log(
                f"[yellow]需要确认:[/yellow] {req.tool} ({req.risk.level})"
            )
            self._refresh_runtime_status()
            modal = ConfirmModal(req)
            state["screen"] = modal

            def on_close(approved: bool | None) -> None:
                self._resolve_confirm_state(bool(approved), state=state)

            self.push_screen(modal, on_close)

        self.call_from_thread(show)
        event.wait()
        return result["ok"]

    def _input_callback(self, prompt: str, multiline: bool) -> str:
        event = threading.Event()
        result: dict[str, str] = {"value": ""}
        state: dict[str, Any] = {
            "event": event,
            "result": result,
            "resolved": False,
            "screen": None,
        }
        self._input_state = state

        def show() -> None:
            if state["resolved"]:
                return
            mode = "多行" if multiline else "单行"
            self._write_log(f"[yellow]需要输入:[/yellow] {prompt} ({mode})")
            self._refresh_runtime_status()
            modal = InputModal(prompt=prompt, multiline=multiline)
            state["screen"] = modal

            def on_close(value: str | None) -> None:
                self._resolve_input_state(value or "", state=state)

            self.push_screen(modal, on_close)

        self.call_from_thread(show)
        if not event.wait(timeout=300):
            self.call_from_thread(lambda: self._resolve_input_state("", state=state, dismiss=True))
        return result["value"]

    def _resolve_confirm_state(
        self,
        approved: bool,
        *,
        state: dict[str, Any] | None = None,
        dismiss: bool = False,
    ) -> None:
        current = state or self._confirm_state
        if current is None or current["resolved"]:
            return
        current["resolved"] = True
        current["result"]["ok"] = bool(approved)
        if dismiss and current.get("screen") is not None:
            try:
                current["screen"].dismiss(bool(approved))
            except Exception:
                pass
        current["event"].set()
        if self._confirm_state is current:
            self._confirm_state = None
        self._refresh_runtime_status()

    def _resolve_input_state(
        self,
        value: str,
        *,
        state: dict[str, Any] | None = None,
        dismiss: bool = False,
    ) -> None:
        current = state or self._input_state
        if current is None or current["resolved"]:
            return
        current["resolved"] = True
        current["result"]["value"] = value
        if dismiss and current.get("screen") is not None:
            try:
                current["screen"].dismiss(value)
            except Exception:
                pass
        current["event"].set()
        if self._input_state is current:
            self._input_state = None
        self._refresh_runtime_status()

    def action_toggle_audit(self) -> None:
        self._switch_right_panel("audit")

    def action_toggle_env(self) -> None:
        self._switch_right_panel("env")

    def action_cancel_current(self) -> None:
        busy = (
            (self._worker is not None and self._worker.is_alive())
            or self._confirm_state is not None
            or self._input_state is not None
        )
        if not busy:
            self._write_log("[yellow]当前没有正在执行的任务。[/yellow]")
            return

        self.controller.request_cancel()
        self._write_log("[yellow]已请求取消当前执行。[/yellow]")
        self._set_status("取消中…")
        self._resolve_confirm_state(False, dismiss=True)
        self._resolve_input_state("", dismiss=True)

    def _switch_right_panel(self, mode: str) -> None:
        right = self.query_one("#right_pane", Vertical)
        right.remove_children()
        if mode == "env":
            right.mount(EnvPanel(self.controller.env_profile))
            self._right_panel_mode = "env"
            return

        panel = AuditPanel(self.controller.audit_log)
        right.mount(panel)
        self.call_later(panel.refresh_data)
        self._right_panel_mode = "audit"

    def action_clear_log(self) -> None:
        self.query_one("#conversation", RichLog).clear()

    def action_quit(self) -> None:
        self.exit()


def run_tui(controller: "AgentController") -> None:
    """Launch the TUI with the provided controller."""
    app = SysDialogueTUI(controller)
    app.run()
