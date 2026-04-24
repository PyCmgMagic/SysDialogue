from __future__ import annotations

import socket

from sysdialogue.runtime.secure_runner import MAX_OUTPUT_BYTES
from sysdialogue.runtime.ssh_adapter import RemoteExecutor, SSHConfig


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


class _TimeoutStream(_FakeStream):
    def read(self, size: int = -1) -> bytes:
        raise socket.timeout("timed out")


class _FakeClient:
    def __init__(self, stdout, stderr):
        self.stdout = stdout
        self.stderr = stderr

    def exec_command(self, cmd: str, timeout: int, get_pty: bool):
        return None, self.stdout, self.stderr


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
