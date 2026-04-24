"""SSH remote command execution."""

from __future__ import annotations

import shlex
import socket
from dataclasses import dataclass

try:
    import paramiko

    _HAS_PARAMIKO = True
except ImportError:
    _HAS_PARAMIKO = False

from sysdialogue.runtime.secure_runner import MAX_OUTPUT_BYTES, RunResult, SafeExecutor


@dataclass
class SSHConfig:
    host: str
    port: int = 22
    username: str = "root"
    password: str | None = None
    key_filename: str | None = None
    known_hosts_file: str | None = None  # None = ~/.ssh/known_hosts
    sudo_password: str | None = None


class RemoteExecutor(SafeExecutor):
    """Execute commands on a remote Linux host over SSH."""

    def __init__(self, config: SSHConfig):
        if not _HAS_PARAMIKO:
            raise RuntimeError("paramiko is required for remote mode. Install it with: pip install paramiko")
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

    @property
    def username(self) -> str:
        return self._config.username

    @property
    def has_sudo_password(self) -> bool:
        return bool(self._config.sudo_password)

    def __enter__(self) -> "RemoteExecutor":
        self.connect()
        return self

    def __exit__(self, *_) -> None:
        self.disconnect()

    def _raw_run(self, cmd: list[str], timeout: int, cwd: str | None = None) -> RunResult:
        if self._client is None:
            self.connect()
        cmd_str = _quote_command(cmd)
        if cwd:
            cmd_str = f"cd {shlex.quote(cwd)} && {cmd_str}"
        return self._run_command_string(cmd_str, timeout=timeout)

    def run_privileged(self, cmd: list[str], timeout: int = 30, cwd: str | None = None) -> tuple[str, int]:
        """Run a command through the configured privilege path.

        Root remotes run directly. Non-root remotes use sudo non-interactively:
        first with the configured sudo password, otherwise with passwordless
        sudo. The password is passed over stdin and never appears in argv,
        stdout/stderr, command traces, or audit logs.
        """
        if self._config.username == "root":
            return self.run(cmd, timeout=timeout, cwd=cwd)
        cmd_str = _quote_command(["sudo", "-S", "-p", "", "--", *cmd])
        if cwd:
            cmd_str = f"cd {shlex.quote(cwd)} && {cmd_str}"
        if self._config.sudo_password:
            result = self._run_command_string(
                cmd_str,
                timeout=timeout,
                stdin_text=f"{self._config.sudo_password}\n",
            )
        else:
            cmd_str = _quote_command(["sudo", "-n", "--", *cmd])
            if cwd:
                cmd_str = f"cd {shlex.quote(cwd)} && {cmd_str}"
            result = self._run_command_string(
                cmd_str,
                timeout=timeout,
            )
        return _combine_result(result)

    def _run_command_string(
        self,
        cmd_str: str,
        *,
        timeout: int,
        stdin_text: str | None = None,
    ) -> RunResult:
        if self._client is None:
            self.connect()
        try:
            stdin_f, stdout_f, stderr_f = self._client.exec_command(  # type: ignore[union-attr]
                cmd_str, timeout=timeout, get_pty=False
            )
            if stdin_text:
                stdin_f.write(stdin_text)
                stdin_f.flush()
                try:
                    stdin_f.channel.shutdown_write()
                except Exception:
                    pass
            stdout_bytes = stdout_f.read(MAX_OUTPUT_BYTES + 1)
            stderr_bytes = stderr_f.read(MAX_OUTPUT_BYTES + 1)
            exit_code = stdout_f.channel.recv_exit_status()
        except socket.timeout:
            return RunResult(stdout="", stderr="Command timed out", exit_code=124, timed_out=True)
        truncated = len(stdout_bytes) > MAX_OUTPUT_BYTES or len(stderr_bytes) > MAX_OUTPUT_BYTES
        return RunResult(
            stdout=stdout_bytes[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace").strip(),
            stderr=stderr_bytes[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace").strip(),
            exit_code=exit_code,
            truncated=truncated,
        )


def _quote_command(cmd: list[str]) -> str:
    return " ".join(shlex.quote(c) for c in cmd)


def _combine_result(result: RunResult) -> tuple[str, int]:
    combined = result.stdout
    if result.stderr:
        combined = (combined + "\n" + result.stderr).strip()
    if result.timed_out:
        combined += "\n[TIMEOUT]"
    if result.truncated:
        combined += "\n[OUTPUT TRUNCATED]"
    return combined, result.exit_code
