from __future__ import annotations

from dataclasses import dataclass

from sysdialogue.runtime.ssh_adapter import RemoteExecutor, SSHConfig
from sysdialogue.runtime.target_fs import TargetFileAccess


@dataclass
class _Stat:
    st_mode: int = 0o100644


class _MemoryFile:
    def __init__(self, sftp: "_MemorySFTP", path: str):
        self.sftp = sftp
        self.path = path
        self.data = bytearray()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.sftp.files[self.path] = bytes(self.data)

    def write(self, data: bytes) -> None:
        self.data.extend(data)

    def flush(self) -> None:
        pass


class _MemorySFTP:
    def __init__(self):
        self.files = {"/tmp/demo.txt": b"old"}
        self.dirs = {"/", "/tmp"}
        self.rename_calls = 0
        self.remove_calls: list[str] = []

    def normalize(self, path: str) -> str:
        return "/home/alice" if path == "." else path

    def stat(self, path: str):
        if path in self.files:
            return _Stat()
        if path in self.dirs:
            return _Stat(0o040755)
        raise OSError(path)

    def mkdir(self, path: str) -> None:
        self.dirs.add(path)

    def open(self, path: str, mode: str):
        assert mode == "wb"
        return _MemoryFile(self, path)

    def rename(self, src: str, dst: str) -> None:
        self.rename_calls += 1
        if self.rename_calls == 1:
            raise OSError("Failure")
        self.files[dst] = self.files.pop(src)

    def remove(self, path: str) -> None:
        self.remove_calls.append(path)
        self.files.pop(path, None)


def test_remote_atomic_write_falls_back_when_plain_rename_cannot_replace() -> None:
    executor = RemoteExecutor(SSHConfig(host="example.test", username="root"))
    sftp = _MemorySFTP()
    executor._client = object()
    executor.open_sftp = lambda: sftp  # type: ignore[method-assign]

    TargetFileAccess(executor).write_text("/tmp/demo.txt", "new", atomic=True)

    assert sftp.files["/tmp/demo.txt"] == b"new"
    assert sftp.rename_calls == 2
    assert sftp.remove_calls == ["/tmp/demo.txt"]
