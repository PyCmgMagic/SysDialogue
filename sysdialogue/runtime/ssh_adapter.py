"""RemoteExecutor — SSH 远程命令执行（known_hosts 校验，单命令 exec）。"""

from __future__ import annotations

import shlex
from dataclasses import dataclass

try:
    import paramiko
    _HAS_PARAMIKO = True
except ImportError:
    _HAS_PARAMIKO = False

from sysdialogue.runtime.secure_runner import RunResult, SafeExecutor, MAX_OUTPUT_BYTES


@dataclass
class SSHConfig:
    host: str
    port: int = 22
    username: str = "root"
    password: str | None = None
    key_filename: str | None = None
    known_hosts_file: str | None = None  # None = ~/.ssh/known_hosts


class RemoteExecutor(SafeExecutor):
    """通过 SSH 连接远程 Linux 执行命令。

    安全约束：
    - 强制 known_hosts 校验（RejectPolicy），防止中间人攻击
    - 每条命令独立 exec_command（不复用 shell 会话）
    - 不开放 PTY（避免交互式 shell 逃逸）
    """

    def __init__(self, config: SSHConfig):
        if not _HAS_PARAMIKO:
            raise RuntimeError("paramiko 未安装，无法使用远程模式。请运行: pip install paramiko")
        self._config = config
        self._client: "paramiko.SSHClient | None" = None

    def connect(self) -> None:
        import paramiko
        client = paramiko.SSHClient()
        if self._config.known_hosts_file:
            client.load_host_keys(self._config.known_hosts_file)
        else:
            client.load_system_host_keys()
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
        client.connect(
            hostname=self._config.host,
            port=self._config.port,
            username=self._config.username,
            password=self._config.password,
            key_filename=self._config.key_filename,
            timeout=15,
            allow_agent=True,
            look_for_keys=True,
        )
        self._client = client

    def disconnect(self) -> None:
        if self._client:
            self._client.close()
            self._client = None

    def open_sftp(self):
        if self._client is None:
            self.connect()
        return self._client.open_sftp()  # type: ignore[union-attr]

    def __enter__(self) -> "RemoteExecutor":
        self.connect()
        return self

    def __exit__(self, *_) -> None:
        self.disconnect()

    def _raw_run(self, cmd: list[str], timeout: int) -> RunResult:
        if self._client is None:
            self.connect()
        # 构造单条命令字符串（list→shell-quoted 字符串）
        cmd_str = " ".join(shlex.quote(c) for c in cmd)
        _, stdout_f, stderr_f = self._client.exec_command(  # type: ignore[union-attr]
            cmd_str, timeout=timeout, get_pty=False
        )
        stdout_bytes = stdout_f.read(MAX_OUTPUT_BYTES + 1)
        stderr_bytes = stderr_f.read(MAX_OUTPUT_BYTES)
        exit_code = stdout_f.channel.recv_exit_status()
        truncated = len(stdout_bytes) > MAX_OUTPUT_BYTES
        stdout = stdout_bytes[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace").strip()
        stderr = stderr_bytes.decode("utf-8", errors="replace").strip()
        return RunResult(
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            truncated=truncated,
        )
