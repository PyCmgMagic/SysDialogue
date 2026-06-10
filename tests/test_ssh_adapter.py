from __future__ import annotations

import shlex
import socket

import paramiko

import sysdialogue.runtime.ssh_adapter as ssh_adapter_module
from sysdialogue.runtime.secure_runner import MAX_OUTPUT_BYTES
from sysdialogue.runtime.ssh_adapter import (
    RemoteExecutor,
    SSHConfig,
    _AutoAddKnownHostPolicy,
    _load_host_keys,
    _render_proxy_command,
)


def test_load_host_keys_handles_windows_bom_prefixed_known_hosts(tmp_path) -> None:
    key = paramiko.RSAKey.generate(1024)
    known_hosts = tmp_path / "known_hosts"
    line = f"example.com {key.get_name()} {key.get_base64()}\n".encode("ascii")
    known_hosts.write_bytes(b"\xff\xfe\r\n" + line)

    client = paramiko.SSHClient()
    _load_host_keys(client, str(known_hosts), system=True)

    loaded = client._system_host_keys.lookup("example.com")  # noqa: SLF001
    assert loaded is not None
    assert loaded[key.get_name()].get_base64() == key.get_base64()


def test_auto_add_known_host_policy_appends_unknown_key(tmp_path) -> None:
    key = paramiko.RSAKey.generate(1024)
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text("existing-host ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQC\n", encoding="utf-8")

    client = paramiko.SSHClient()
    _AutoAddKnownHostPolicy(known_hosts).missing_host_key(client, "[example.com]:2222", key)

    content = known_hosts.read_text(encoding="utf-8")
    assert f"[example.com]:2222 {key.get_name()} {key.get_base64()}" in content
    assert client._host_keys.lookup("[example.com]:2222") is not None  # noqa: SLF001


def test_render_proxy_command_expands_target_placeholders_safely() -> None:
    rendered = _render_proxy_command(
        "ssh -W %h:%p bastion --user %r",
        SSHConfig(host="target internal;bad", port=2222, username="alice"),
    )

    assert rendered == "ssh -W 'target internal;bad':2222 bastion --user alice"
    assert shlex.split(rendered) == [
        "ssh",
        "-W",
        "target internal;bad:2222",
        "bastion",
        "--user",
        "alice",
    ]


def test_remote_executor_uses_proxy_command_socket(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeProxy:
        def __init__(self, command: str):
            self.command = command
            self.closed = False
            captured["proxy"] = self

        def close(self) -> None:
            self.closed = True

    class FakeClient:
        def set_missing_host_key_policy(self, policy) -> None:
            self.policy = policy

        def connect(self, **kwargs) -> None:
            captured["connect"] = kwargs

        def close(self) -> None:
            captured["client_closed"] = True

    monkeypatch.setattr(ssh_adapter_module.paramiko, "SSHClient", FakeClient)
    monkeypatch.setattr(ssh_adapter_module.paramiko, "ProxyCommand", FakeProxy)
    monkeypatch.setattr(ssh_adapter_module, "_load_host_keys", lambda *args, **kwargs: None)

    executor = RemoteExecutor(
        SSHConfig(
            host="target.internal",
            port=2222,
            username="alice",
            proxy_command="ssh -W %h:%p bastion.example.com",
        )
    )
    executor.connect()

    proxy = captured["proxy"]
    assert proxy.command == "ssh -W target.internal:2222 bastion.example.com"
    assert captured["connect"]["sock"] is proxy
    executor.disconnect()
    assert proxy.closed is True
    assert captured["client_closed"] is True


class _FakeChannel:
    def __init__(self, exit_code: int = 0):
        self.exit_code = exit_code

    def recv_exit_status(self) -> int:
        return self.exit_code


class _FakeStream:
    def __init__(self, payload: bytes, exit_code: int = 0):
        self.payload = payload
        self.channel = _FakeChannel(exit_code)

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            return self.payload
        return self.payload[:size]


class _FakeStdin:
    def __init__(self):
        self.writes: list[str] = []
        self.flushed = False
        self.channel = _FakeChannel()

    def write(self, text: str) -> None:
        self.writes.append(text)

    def flush(self) -> None:
        self.flushed = True


class _TimeoutStream(_FakeStream):
    def read(self, size: int = -1) -> bytes:
        raise socket.timeout("timed out")


class _FakeClient:
    def __init__(self, stdout, stderr):
        self.stdout = stdout
        self.stderr = stderr
        self.stdin = _FakeStdin()
        self.commands: list[str] = []

    def exec_command(self, cmd: str, timeout: int, get_pty: bool):
        self.commands.append(cmd)
        return self.stdin, self.stdout, self.stderr


def test_remote_executor_reports_stderr_truncation() -> None:
    executor = RemoteExecutor(SSHConfig(host="example.test"))
    stderr = _FakeStream(b"x" * (MAX_OUTPUT_BYTES + 1), exit_code=7)
    executor._client = _FakeClient(_FakeStream(b"", exit_code=7), stderr)

    result = executor.run_full(["demo"], timeout=5)

    assert result.exit_code == 7
    assert result.truncated is True
    assert len(result.stderr.encode("utf-8")) == MAX_OUTPUT_BYTES
    assert "[OUTPUT TRUNCATED]" in executor.run(["demo"], timeout=5)[0]


def test_remote_executor_reports_socket_timeout() -> None:
    executor = RemoteExecutor(SSHConfig(host="example.test"))
    executor._client = _FakeClient(_TimeoutStream(b""), _FakeStream(b""))

    result = executor.run_full(["sleep", "60"], timeout=1)

    assert result.exit_code == 124
    assert result.timed_out is True
    assert result.stderr == "Command timed out"


def test_remote_executor_runs_privileged_command_with_sudo_password() -> None:
    executor = RemoteExecutor(
        SSHConfig(host="example.test", username="alice", sudo_password="secret")
    )
    client = _FakeClient(_FakeStream(b"ok"), _FakeStream(b""))
    executor._client = client

    out, code = executor.run_privileged(["systemctl", "restart", "demo.service"], timeout=5)

    assert code == 0
    assert out == "ok"
    assert client.stdin.writes == ["secret\n"]
    assert "secret" not in client.commands[0]
    assert client.commands[0].startswith("sudo -S -p '' -- systemctl restart demo.service")


def test_remote_executor_quotes_cwd_before_command() -> None:
    executor = RemoteExecutor(SSHConfig(host="example.test"))
    client = _FakeClient(_FakeStream(b"ok"), _FakeStream(b""))
    executor._client = client

    out, code = executor.run(["mvn", "test"], timeout=5, cwd="/tmp/app dir")

    assert code == 0
    assert out == "ok"
    assert client.commands[0] == "cd '/tmp/app dir' && mvn test"


def test_remote_executor_runs_shell_command_with_cwd() -> None:
    executor = RemoteExecutor(SSHConfig(host="example.test"))
    client = _FakeClient(_FakeStream(b"ok"), _FakeStream(b""))
    executor._client = client

    out, code = executor.run_shell("echo hi && pwd > out.txt", timeout=5, cwd="/tmp/app dir")

    assert code == 0
    assert out == "ok"
    assert client.commands[0] == "cd '/tmp/app dir' && echo hi && pwd > out.txt"


def test_remote_executor_runs_privileged_shell_with_sudo_password() -> None:
    executor = RemoteExecutor(
        SSHConfig(host="example.test", username="alice", sudo_password="secret")
    )
    client = _FakeClient(_FakeStream(b"ok"), _FakeStream(b""))
    executor._client = client

    out, code = executor.run_privileged_shell("systemctl restart demo.service", timeout=5)

    assert code == 0
    assert out == "ok"
    assert client.stdin.writes == ["secret\n"]
    assert "secret" not in client.commands[0]
    assert "sudo -S -p '' -- sh -lc 'systemctl restart demo.service'" in client.commands[0]
