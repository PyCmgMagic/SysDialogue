"""SysDialogue TUI 主界面 — 五区布局 + 快捷键 + AgentController 后台线程驱动。

区域：
  1. 顶部：标题栏 + 快捷键提示
  2. 主区：对话历史（RichLog）
  3. 输入区：单行文本输入
  4. 右侧：可切换审计/环境面板（F3/F4）
  5. 底部：状态栏（idle / thinking / confirming）
"""

from __future__ import annotations

import threading
import traceback
from typing import TYPE_CHECKING, Any

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Footer, Header, Input, RichLog, Static

from sysdialogue.ui.audit_panel import AuditPanel
from sysdialogue.ui.confirm_modal import ConfirmModal
from sysdialogue.ui.env_panel import EnvPanel

if TYPE_CHECKING:
    from sysdialogue.agent.controller import AgentController
    from sysdialogue.security.approval_rules import ConfirmationRequest


class SysDialogueTUI(App):
    """SysDialogue v6 TUI 主应用。"""

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
    #input_area {
        height: 3;
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
        Binding("ctrl+c", "quit", "退出"),
        Binding("ctrl+l", "clear_log", "清屏"),
    ]

    def __init__(self, controller: "AgentController"):
        super().__init__()
        self.controller = controller
        # 注入交互回调
        controller.confirm_callback = self._confirm_callback
        controller.input_callback = self._input_callback
        self._right_panel_mode = "audit"  # "audit" | "env"
        self._worker: threading.Thread | None = None

    # ------------------------------------------------------------------
    # 布局
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Container(
            Vertical(
                RichLog(id="conversation", highlight=True, markup=True, wrap=True),
                Input(placeholder="输入运维需求（例：查看系统版本 / 重启 nginx）…",
                      id="user_input"),
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
        log = self.query_one("#conversation", RichLog)
        log.write(
            "[bold cyan]欢迎使用 SysDialogue v6[/bold cyan]\n"
            "输入自然语言运维需求，Enter 发送。\n"
            "F3 切换审计面板 / F4 切换环境画像 / Ctrl+C 退出。\n"
        )
        self.query_one("#user_input", Input).focus()

    # ------------------------------------------------------------------
    # 输入处理
    # ------------------------------------------------------------------

    def on_input_submitted(self, event: Input.Submitted) -> None:
        # 如果正在 workflow input 劫持中，优先处理
        if self._input_hijack is not None:
            hijack = self._input_hijack
            self._input_hijack = None
            try:
                hijack(event)
            except Exception as e:
                log = self.query_one("#conversation", RichLog)
                log.write(f"[red]input hijack 异常：{e}[/red]")
            return

        text = (event.value or "").strip()
        if not text:
            return
        event.input.value = ""

        log = self.query_one("#conversation", RichLog)
        log.write(f"[bold green]> 你:[/bold green] {text}")

        # 禁用输入，开后台线程
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
        log = self.query_one("#conversation", RichLog)
        log.write(f"[bold magenta]SysDialogue:[/bold magenta] {reply}\n")
        # 刷新审计面板
        try:
            ap = self.query_one(AuditPanel)
            ap.refresh_data()
        except Exception:
            pass
        inp = self.query_one("#user_input", Input)
        inp.disabled = False
        inp.focus()
        self._set_status("就绪")

    def _set_status(self, text: str) -> None:
        try:
            self.query_one("#status_bar", Static).update(f"状态：{text}")
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 交互回调（供 AgentController / WorkflowEngine 使用）
    # ------------------------------------------------------------------

    def _confirm_callback(self, req: "ConfirmationRequest") -> bool:
        """同步回调：在 worker 线程中被调用，通过 event 阻塞等 modal 返回。"""
        event = threading.Event()
        result: dict[str, Any] = {"ok": False}

        def show() -> None:
            def on_close(approved: bool | None) -> None:
                result["ok"] = bool(approved)
                event.set()
            self.push_screen(ConfirmModal(req), on_close)

        self.call_from_thread(show)
        event.wait()
        return result["ok"]

    def _input_callback(self, prompt: str, multiline: bool) -> str:
        """同步 input 回调：TUI 下通过 prompt 在对话区展示，等待用户下一次 Input 提交。

        简化实现：一次性请求，不支持多行。后续可换为 Modal 对话框。
        """
        event = threading.Event()
        result: dict[str, str] = {"value": ""}

        def show() -> None:
            log = self.query_one("#conversation", RichLog)
            log.write(f"[yellow]🔸 请输入:[/yellow] {prompt}")

            inp = self.query_one("#user_input", Input)
            # 临时把 Input 的 submitted 事件处理劫持到这里
            def on_submit(ev: Input.Submitted) -> None:
                result["value"] = ev.value or ""
                ev.input.value = ""
                event.set()
                # 取消劫持
                self._input_hijack = None

            self._input_hijack = on_submit

        self.call_from_thread(show)
        event.wait(timeout=300)  # 最长等 5 分钟
        return result["value"]

    # 默认劫持为 None；on_input_submitted 先检查
    _input_hijack: Any = None

    # ------------------------------------------------------------------
    # 快捷键动作
    # ------------------------------------------------------------------

    def action_toggle_audit(self) -> None:
        self._switch_right_panel("audit")

    def action_toggle_env(self) -> None:
        self._switch_right_panel("env")

    def _switch_right_panel(self, mode: str) -> None:
        right = self.query_one("#right_pane", Vertical)
        right.remove_children()
        if mode == "env":
            right.mount(EnvPanel(self.controller.env_profile))
            self._right_panel_mode = "env"
        else:
            ap = AuditPanel(self.controller.audit_log)
            right.mount(ap)
            # mount 完成后刷新数据
            self.call_later(ap.refresh_data)
            self._right_panel_mode = "audit"

    def action_clear_log(self) -> None:
        self.query_one("#conversation", RichLog).clear()

    def action_quit(self) -> None:
        self.exit()


def run_tui(controller: "AgentController") -> None:
    """对外入口：用给定 controller 启动 TUI。"""
    app = SysDialogueTUI(controller)
    app.run()
