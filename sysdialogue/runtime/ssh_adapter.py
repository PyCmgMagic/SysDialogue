"""RemoteExecutor — SSH 远程命令执行（known_hosts 校验，单命令 exec）。"""

from __future__ import annotations

import codecs
import os
import shlex
from dataclasses import dataclass
from pathlib import Path

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
    auto_add_host_keys: bool = True


class RemoteExecutor(SafeExecutor):
    """通过 SSH 连接远程 Linux 执行命令。

    安全约束：
    - 校验已知 known_hosts；首次连接默认采用 TOFU 写入 known_hosts
    - 已知主机 key 变化时仍由 Paramiko 拒绝，防止静默覆盖
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
        known_hosts_path = _known_hosts_path(self._config.known_hosts_file)
        if self._config.known_hosts_file:
            _load_host_keys(client, self._config.known_hosts_file, system=False)
        else:
            _load_host_keys(client, None, system=True)
        if self._config.auto_add_host_keys:
            client.set_missing_host_key_policy(_AutoAddKnownHostPolicy(known_hosts_path))
        else:
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
        f"known_hosts 文件无法解析：{path}。请确认它是 OpenSSH known_hosts 格式。"
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
