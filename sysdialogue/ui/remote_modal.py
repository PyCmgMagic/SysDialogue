"""RemoteModal — TUI 内 SSH 远程连接配置弹窗。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static


@dataclass
class RemoteConnectionInfo:
    """SSH 连接信息，由弹窗收集后返回。"""
    host: str
    port: int = 22
    username: str = "root"
    password: str | None = None
    key_filename: str | None = None


class RemoteModal(ModalScreen[RemoteConnectionInfo | None]):
    """弹窗：配置 SSH 连接，支持测试连通性后确认连接。"""

    CSS = """
    RemoteModal {
        align: center middle;
    }

    #remote_box {
        width: 88%;
        max-width: 110;
        height: auto;
        max-height: 90%;
        border: heavy $primary;
        background: $surface;
        layout: vertical;
    }

    #remote_header {
        background: $primary 20%;
        padding: 0 2;
        height: 2;
        content-align: left middle;
        text-style: bold;
        border-bottom: solid $primary 20%;
    }

    #remote_desc {
        padding: 1 2 0 2;
        color: $text-muted;
        height: auto;
    }

    #remote_form {
        padding: 1 2;
        height: auto;
    }

    .field_row {
        height: auto;
        margin: 0 0 1 0;
    }

    .field_label {
        height: 1;
        color: $text-muted;
        text-style: bold;
        margin: 0 0 0 1;
    }

    .field_input {
        width: 100%;
        border: round $primary 40%;
        margin: 0 1;
    }

    .field_input:focus {
        border: round $accent 80%;
    }

    #remote_keyhint {
        padding: 0 2;
        color: $text-muted;
        height: 1;
    }

    #remote_status {
        padding: 0 2;
        height: auto;
        color: $text-muted;
    }

    #remote_status.success {
        color: $success;
    }

    #remote_status.error {
        color: $error;
    }

    #remote_buttons {
        height: 4;
        align: center middle;
        padding: 0 2;
        border-top: solid $primary 15%;
    }

    #remote_buttons Button {
        margin: 0 1;
        min-width: 18;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "取消"),
    ]

    def __init__(self, current_remote: str | None = None):
        super().__init__()
        self.current_remote = current_remote
        self._testing = False

    def compose(self) -> ComposeResult:
        # 预填当前连接信息
        host_val = ""
        port_val = "22"
        user_val = "root"
        if self.current_remote:
            parts = self.current_remote.split("@")
            if len(parts) == 2:
                user_val = parts[0]
                hp = parts[1]
            else:
                hp = parts[0]
            if ":" in hp:
                h, p = hp.rsplit(":", 1)
                host_val = h
                port_val = p
            else:
                host_val = hp

        yield Container(
            Static("  🌐  连接远程服务器  —  SSH", id="remote_header"),
            Static(
                "输入目标服务器信息，连接后所有运维操作将在远程执行。",
                id="remote_desc",
            ),
            Vertical(
                # Host
                Static("主机地址 (必填)", classes="field_label"),
                Horizontal(
                    Input(
                        placeholder="192.168.1.100 或 server.example.com",
                        value=host_val,
                        id="input_host",
                        classes="field_input",
                    ),
                    classes="field_row",
                ),
                # Port
                Static("端口", classes="field_label"),
                Horizontal(
                    Input(
                        placeholder="22",
                        value=port_val,
                        id="input_port",
                        classes="field_input",
                    ),
                    classes="field_row",
                ),
                # Username
                Static("用户名", classes="field_label"),
                Horizontal(
                    Input(
                        placeholder="root",
                        value=user_val,
                        id="input_user",
                        classes="field_input",
                    ),
                    classes="field_row",
                ),
                # SSH Key
                Static("SSH 密钥路径 (可选，留空使用默认 ~/.ssh/id_rsa)", classes="field_label"),
                Horizontal(
                    Input(
                        placeholder="~/.ssh/id_rsa",
                        id="input_key",
                        classes="field_input",
                    ),
                    classes="field_row",
                ),
                # Password
                Static("密码 (可选，优先使用密钥)", classes="field_label"),
                Horizontal(
                    Input(
                        placeholder="留空使用密钥认证",
                        id="input_pass",
                        password=True,
                        classes="field_input",
                    ),
                    classes="field_row",
                ),
                id="remote_form",
            ),
            Static("", id="remote_status"),
            Static("Enter 连接  ·  F2 测试连接  ·  Esc 取消", id="remote_keyhint"),
            Horizontal(
                Button("连接  Enter", id="btn_connect", variant="primary"),
                Button("测试  F2", id="btn_test", variant="default"),
                Button("取消  Esc", id="btn_cancel", variant="default"),
                id="remote_buttons",
            ),
            id="remote_box",
        )

    def on_mount(self) -> None:
        self.query_one("#input_host", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if self._testing:
            return
        # 焦点跳转到下一个输入框，或在密码框触发连接
        input_ids = ["input_host", "input_port", "input_user", "input_key", "input_pass"]
        try:
            current_idx = input_ids.index(event.input.id)
        except ValueError:
            return
        if current_idx < len(input_ids) - 1:
            self.query_one(f"#{input_ids[current_idx + 1]}", Input).focus()
        else:
            self.action_connect()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_connect":
            self.action_connect()
        elif event.button.id == "btn_test":
            self.action_test()
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_connect(self) -> None:
        if self._testing:
            return
        info = self._collect_info()
        if info is None:
            self._set_status("请输入主机地址！", error=True)
            return
        self.dismiss(info)

    def action_test(self) -> None:
        if self._testing:
            return
        info = self._collect_info()
        if info is None:
            self._set_status("请输入主机地址！", error=True)
            return

        self._testing = True
        self._set_status("⏳ 正在测试连接（最多 8 秒）...")
        self.query_one("#btn_connect", Button).disabled = True
        self.query_one("#btn_test", Button).disabled = True

        import threading

        result_box: list[tuple[bool, str]] = []

        def worker() -> None:
            ok, msg = _test_ssh_connection(info)
            result_box.append((ok, msg))

        t = threading.Thread(target=worker, daemon=True)
        t.start()

        # 在主线程轮询结果，每 200ms 检查一次，硬超时 8 秒
        elapsed = 0.0
        import time
        start = time.monotonic()

        def _poll() -> None:
            nonlocal elapsed
            if result_box:
                ok, msg = result_box[0]
                self._on_test_done(ok, msg)
                return
            elapsed = time.monotonic() - start
            if elapsed >= 8.0:
                self._on_test_done(False, "连接超时（8 秒），请检查服务器地址和网络")
                return
            self.set_timer(0.2, _poll)

        self.set_timer(0.2, _poll)

    def _on_test_done(self, ok: bool, msg: str) -> None:
        self._testing = False
        self.query_one("#btn_connect", Button).disabled = False
        self.query_one("#btn_test", Button).disabled = False
        if ok:
            self._set_status(f"✓ {msg}", success=True)
        else:
            self._set_status(f"✗ {msg}", error=True)

    def _collect_info(self) -> RemoteConnectionInfo | None:
        host = self.query_one("#input_host", Input).value.strip()
        if not host:
            return None
        port_str = self.query_one("#input_port", Input).value.strip() or "22"
        try:
            port = int(port_str)
        except ValueError:
            port = 22
        username = self.query_one("#input_user", Input).value.strip() or "root"
        password = self.query_one("#input_pass", Input).value.strip() or None
        key_filename = self.query_one("#input_key", Input).value.strip() or None
        return RemoteConnectionInfo(
            host=host,
            port=port,
            username=username,
            password=password,
            key_filename=key_filename,
        )

    def _set_status(self, text: str, *, error: bool = False, success: bool = False) -> None:
        status = self.query_one("#remote_status", Static)
        status.update(text)
        status.remove_class("error")
        status.remove_class("success")
        if error:
            status.add_class("error")
        elif success:
            status.add_class("success")


def _test_ssh_connection(info: RemoteConnectionInfo) -> tuple[bool, str]:
    """测试 SSH 连接，返回 (成功, 消息)。

    总超时上限约 11 秒：TCP 预检 2s + SSH connect 6s + 验证 3s。
    """
    import socket

    # ── 阶段 1: TCP 端口预检（2 秒快速失败）──
    try:
        sock = socket.create_connection((info.host, info.port), timeout=2)
        sock.close()
    except socket.timeout:
        return False, f"连接超时：{info.host}:{info.port} 在 2 秒内无响应，请检查地址"
    except socket.gaierror:
        return False, f"无法解析主机名：{info.host}"
    except ConnectionRefusedError:
        return False, f"连接被拒绝：{info.host}:{info.port} 上 SSH 服务未启动"
    except OSError as exc:
        return False, f"网络不可达：{exc}"

    # ── 阶段 2: SSH 握手 ──
    try:
        import paramiko
    except ImportError:
        return False, "paramiko 未安装"

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            hostname=info.host,
            port=info.port,
            username=info.username,
            password=info.password,
            key_filename=info.key_filename,
            timeout=6,
            auth_timeout=6,
            banner_timeout=6,
            allow_agent=False,
            look_for_keys=False,
        )
    except paramiko.AuthenticationException:
        client.close()
        return False, "认证失败：用户名或密码/密钥不正确"
    except paramiko.SSHException as exc:
        client.close()
        return False, f"SSH 协议错误: {exc}"
    except socket.timeout:
        client.close()
        return False, "SSH 握手超时"
    except Exception as exc:
        client.close()
        return False, f"连接失败: {exc}"

    # ── 阶段 3: 验证命令 ──
    try:
        _, stdout, _ = client.exec_command("uname -n", timeout=3)
        output = stdout.read().decode("utf-8", errors="replace").strip()
        exit_code = stdout.channel.recv_exit_status()
        client.close()
        if exit_code == 0 and output:
            return True, f"连接成功！主机名: {output}"
        return True, "连接成功"
    except Exception as exc:
        try:
            client.close()
        except Exception:
            pass
        return True, f"连接成功（验证命令异常: {exc}）"
