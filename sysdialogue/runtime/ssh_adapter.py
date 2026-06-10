"""SSH remote command execution."""

from __future__ import annotations

import codecs
import os
import shlex
import socket
from dataclasses import dataclass
from pathlib import Path

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
    auto_add_host_keys: bool = True
    sudo_password: str | None = None
    proxy_command: str | None = None


class RemoteExecutor(SafeExecutor):
    """Execute commands on a remote Linux host over SSH.

    Known host keys are verified. Unknown hosts default to TOFU and are appended
    to known_hosts; changed keys are still rejected by Paramiko.
    """

    def __init__(self, config: SSHConfig):
        if not _HAS_PARAMIKO:
            raise RuntimeError("paramiko is required for remote mode. Install it with: pip install paramiko")
        self._config = config
        self._client: "paramiko.SSHClient | None" = None
        self._proxy = None

    def connect(self) -> None:
        import paramiko

        client = paramiko.SSHClient()
        known_hosts_path = _known_hosts_path(self._config.known_hosts_file)
        if self._config.known_hosts_file:
            _load_host_keys(client, self._config.known_hosts_file, system=False)
        else:
            _load_host_keys(client, None, system=True)
        if self._config.auto_add_host_keys:
            client.set_missing_host_key_policy(_AutoAddKnownHostPolicy(known_hosts_path))
        else:
            client.set_missing_host_key_policy(paramiko.RejectPolicy())
        sock = None
        if self._config.proxy_command:
            rendered = _render_proxy_command(self._config.proxy_command, self._config)
            sock = paramiko.ProxyCommand(rendered)
            self._proxy = sock
        try:
            client.connect(
                hostname=self._config.host,
                port=self._config.port,
                username=self._config.username,
                password=self._config.password,
                key_filename=self._config.key_filename,
                timeout=15,
                allow_agent=True,
                look_for_keys=True,
                sock=sock,
            )
        except Exception:
            if sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass
                self._proxy = None
            raise
        self._client = client

    def disconnect(self) -> None:
        if self._client:
            self._client.close()
            self._client = None
        if self._proxy is not None:
            try:
                self._proxy.close()
            except Exception:
                pass
            self._proxy = None

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

    def run_shell(self, command: str, timeout: int = 30, cwd: str | None = None) -> tuple[str, int]:
        cmd_str = str(command)
        if cwd:
            cmd_str = f"cd {shlex.quote(cwd)} && {cmd_str}"
        result = self._run_command_string(cmd_str, timeout=timeout)
        return _combine_result(result)

    def run_privileged(self, cmd: list[str], timeout: int = 30, cwd: str | None = None) -> tuple[str, int]:
        """Run a command through the configured privilege path.

        Root remotes run directly. Non-root remotes use sudo non-interactively:
        first with the configured sudo password, otherwise with passwordless
        sudo. The password is passed over stdin and never appears in argv,
        stdout/stderr, command traces, or audit logs.
        """
        if self._config.username == "root":
            return self.run(cmd, timeout=timeout, cwd=cwd)
        if self._config.sudo_password:
            cmd_str = _quote_command(["sudo", "-S", "-p", "", "--", *cmd])
            if cwd:
                cmd_str = f"cd {shlex.quote(cwd)} && {cmd_str}"
            result = self._run_command_string(
                cmd_str,
                timeout=timeout,
                stdin_text=f"{self._config.sudo_password}\n",
            )
        else:
            cmd_str = _quote_command(["sudo", "-n", "--", *cmd])
            if cwd:
                cmd_str = f"cd {shlex.quote(cwd)} && {cmd_str}"
            result = self._run_command_string(cmd_str, timeout=timeout)
        return _combine_result(result)

    def run_privileged_shell(self, command: str, timeout: int = 30, cwd: str | None = None) -> tuple[str, int]:
        if self._config.username == "root":
            return self.run_shell(command, timeout=timeout, cwd=cwd)
        sudo_prefix = ["sudo", "-S", "-p", "", "--", "sh", "-lc", command]
        stdin_text = None
        if self._config.sudo_password:
            stdin_text = f"{self._config.sudo_password}\n"
        else:
            sudo_prefix = ["sudo", "-n", "--", "sh", "-lc", command]
        cmd_str = _quote_command(sudo_prefix)
        if cwd:
            cmd_str = f"cd {shlex.quote(cwd)} && {cmd_str}"
        result = self._run_command_string(cmd_str, timeout=timeout, stdin_text=stdin_text)
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


def _load_host_keys(client: "paramiko.SSHClient", filename: str | None, *, system: bool) -> None:
    path = _known_hosts_path(filename)
    if not path.exists():
        if filename is None:
            return
        raise FileNotFoundError(str(path))

    target = client._system_host_keys if system else client._host_keys  # noqa: SLF001
    if not system:
        client._host_keys_filename = str(path)  # noqa: SLF001
    _load_known_hosts_file(target, path)


def _load_known_hosts_file(host_keys, path: Path) -> None:
    import paramiko
    from paramiko.hostkeys import HostKeyEntry

    raw = path.read_bytes()
    if not raw:
        return

    for text in _known_hosts_text_candidates(raw):
        if "\x00" in text[:512]:
            continue
        parsed = paramiko.HostKeys()
        saw_entry_line = False
        loaded = 0
        for lineno, line in enumerate(text.splitlines(), 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            saw_entry_line = True
            try:
                entry = HostKeyEntry.from_line(line, lineno)
            except paramiko.SSHException:
                continue
            if entry is None:
                continue
            for hostname in entry.hostnames:
                parsed.add(hostname, entry.key.get_name(), entry.key)
                loaded += 1
        if loaded or not saw_entry_line:
            _merge_host_keys(host_keys, parsed)
            return

    raise RuntimeError(
        f"known_hosts file could not be parsed: {path}. "
        "Please confirm it uses OpenSSH known_hosts format."
    )


def _known_hosts_text_candidates(raw: bytes) -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()

    def add(text: str) -> None:
        text = text.lstrip("\ufeff")
        if text not in seen:
            seen.add(text)
            candidates.append(text)

    for encoding in ("utf-8-sig", "utf-16", "utf-8"):
        try:
            add(raw.decode(encoding))
        except UnicodeDecodeError:
            pass

    stripped = raw
    for bom in (codecs.BOM_UTF8, codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE):
        if stripped.startswith(bom):
            stripped = stripped[len(bom):]
            break
    add(stripped.decode("utf-8", errors="replace"))
    return candidates


def _merge_host_keys(target, source) -> None:
    for entry in source._entries:  # noqa: SLF001
        for hostname in entry.hostnames:
            target.add(hostname, entry.key.get_name(), entry.key)


def _known_hosts_path(filename: str | None) -> Path:
    return Path(os.path.expanduser(filename or "~/.ssh/known_hosts"))


class _AutoAddKnownHostPolicy:
    def __init__(self, path: Path):
        self._path = path

    def missing_host_key(self, client, hostname, key) -> None:
        client._host_keys.add(hostname, key.get_name(), key)  # noqa: SLF001
        self._path.parent.mkdir(parents=True, exist_ok=True)
        line = f"{hostname} {key.get_name()} {key.get_base64()}\n"
        if self._path.exists() and self._path.stat().st_size:
            raw = self._path.read_bytes()
            prefix = b"" if raw.endswith((b"\n", b"\r")) else b"\n"
        else:
            prefix = b""
        with self._path.open("ab") as handle:
            handle.write(prefix + line.encode("utf-8"))


def _quote_command(cmd: list[str]) -> str:
    return " ".join(shlex.quote(c) for c in cmd)


def _render_proxy_command(template: str, config: SSHConfig) -> str:
    host = shlex.quote(config.host)
    port = str(config.port)
    user = shlex.quote(config.username or "")
    return (
        str(template)
        .replace("%h", host)
        .replace("%p", port)
        .replace("%r", user)
        .replace("{host}", host)
        .replace("{port}", port)
        .replace("{user}", user)
    )


def _combine_result(result: RunResult) -> tuple[str, int]:
    combined = result.stdout
    if result.stderr:
        combined = (combined + "\n" + result.stderr).strip()
    if result.timed_out:
        combined += "\n[TIMEOUT]"
    if result.truncated:
        combined += "\n[OUTPUT TRUNCATED]"
    return combined, result.exit_code
