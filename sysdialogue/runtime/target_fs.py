"""Target filesystem helper for local and remote executors."""

from __future__ import annotations

import json
import os
import posixpath
import shutil
import stat as stat_mod
import uuid
from pathlib import Path
from typing import Any

from sysdialogue.runtime.secure_runner import SafeExecutor
from sysdialogue.runtime.ssh_adapter import RemoteExecutor


class TargetFileAccess:
    """File access facade that keeps local/remote semantics aligned."""

    def __init__(self, executor: SafeExecutor):
        self.executor = executor
        self._remote_home: str | None = None

    @property
    def is_remote(self) -> bool:
        return isinstance(self.executor, RemoteExecutor)

    def home_dir(self) -> str:
        if not self.is_remote:
            return str(Path.home())
        if self._remote_home is None:
            sftp = self._remote_sftp()
            self._remote_home = sftp.normalize(".")
        return self._remote_home

    def join(self, *parts: str) -> str:
        if self.is_remote:
            cleaned = [p for p in parts if p]
            if not cleaned:
                return ""
            head, *tail = cleaned
            return posixpath.join(head, *tail)
        return str(Path(parts[0]).joinpath(*parts[1:]))

    def expand(self, path: str) -> str:
        if self.is_remote:
            if path == "~":
                return self.home_dir()
            if path.startswith("~/"):
                return posixpath.join(self.home_dir(), path[2:])
            return path
        return str(Path(path).expanduser())

    def exists(self, path: str) -> bool:
        path = self.expand(path)
        if self.is_remote:
            try:
                self._remote_sftp().stat(path)
                return True
            except OSError:
                return False
        return Path(path).exists()

    def is_file(self, path: str) -> bool:
        path = self.expand(path)
        if self.is_remote:
            try:
                return stat_mod.S_ISREG(self._remote_sftp().stat(path).st_mode)
            except OSError:
                return False
        return Path(path).is_file()

    def is_dir(self, path: str) -> bool:
        path = self.expand(path)
        if self.is_remote:
            try:
                return stat_mod.S_ISDIR(self._remote_sftp().stat(path).st_mode)
            except OSError:
                return False
        return Path(path).is_dir()

    def read_bytes(self, path: str) -> bytes:
        path = self.expand(path)
        if self.is_remote:
            with self._remote_sftp().open(path, "rb") as fh:
                return fh.read()
        return Path(path).read_bytes()

    def read_text(self, path: str, encoding: str = "utf-8", errors: str = "strict") -> str:
        return self.read_bytes(path).decode(encoding, errors=errors)

    def write_bytes(self, path: str, data: bytes, *, atomic: bool = False) -> None:
        path = self.expand(path)
        parent = self.dirname(path)
        if parent:
            self.mkdir(parent, parents=True)
        if self.is_remote:
            sftp = self._remote_sftp()
            if atomic:
                tmp = self.join(parent or ".", f".{self.basename(path)}.{uuid.uuid4().hex}.tmp")
                with sftp.open(tmp, "wb") as fh:
                    fh.write(data)
                    try:
                        fh.flush()
                    except Exception:
                        pass
                try:
                    self._remote_atomic_rename(sftp, tmp, path)
                except Exception:
                    try:
                        sftp.remove(tmp)
                    except Exception:
                        pass
                    raise
                return
            with sftp.open(path, "wb") as fh:
                fh.write(data)
            return

        if atomic:
            tmp = str(Path(path).with_name(f".{Path(path).name}.{uuid.uuid4().hex}.tmp"))
            Path(tmp).write_bytes(data)
            os.replace(tmp, path)
        else:
            Path(path).write_bytes(data)

    def write_text(self, path: str, content: str, *, atomic: bool = False,
                   encoding: str = "utf-8") -> None:
        self.write_bytes(path, content.encode(encoding), atomic=atomic)

    def append_text(self, path: str, content: str, *, encoding: str = "utf-8") -> None:
        path = self.expand(path)
        parent = self.dirname(path)
        if parent:
            self.mkdir(parent, parents=True)
        if self.is_remote:
            with self._remote_sftp().open(path, "ab") as fh:
                fh.write(content.encode(encoding))
            return
        with open(path, "a", encoding=encoding) as fh:
            fh.write(content)

    def mkdir(self, path: str, *, parents: bool = True) -> None:
        path = self.expand(path)
        if self.is_remote:
            self._mkdir_remote(path, parents=parents)
            return
        Path(path).mkdir(parents=parents, exist_ok=True)

    def remove(self, path: str, *, recursive: bool = False) -> None:
        path = self.expand(path)
        if self.is_remote:
            if recursive and self.is_dir(path):
                self._run_remote(["rm", "-rf", path])
            elif self.is_dir(path):
                self._remote_sftp().rmdir(path)
            else:
                self._remote_sftp().remove(path)
            return

        target = Path(path)
        if target.is_dir():
            if recursive:
                shutil.rmtree(target)
            else:
                target.rmdir()
        else:
            target.unlink()

    def copy(self, src: str, dst: str, *, recursive: bool = False) -> None:
        src = self.expand(src)
        dst = self.expand(dst)
        if self.is_remote:
            cmd = ["cp", "-a", src, dst]
            self._run_remote(cmd)
            return

        src_path = Path(src)
        dst_path = Path(dst)
        if src_path.is_dir():
            if recursive:
                shutil.copytree(src_path, dst_path)
            else:
                raise IsADirectoryError(src)
        else:
            dst_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_path, dst_path)

    def move(self, src: str, dst: str) -> None:
        src = self.expand(src)
        dst = self.expand(dst)
        if self.is_remote:
            self._run_remote(["mv", src, dst])
            return
        dst_path = Path(dst)
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(src, dst)

    def chmod(self, path: str, mode: int) -> None:
        path = self.expand(path)
        if self.is_remote:
            self._remote_sftp().chmod(path, mode)
            return
        os.chmod(path, mode)

    def read_json(self, path: str) -> Any:
        return json.loads(self.read_text(path, encoding="utf-8"))

    def write_json(self, path: str, data: Any, *, atomic: bool = False) -> None:
        self.write_text(
            path,
            json.dumps(data, indent=2, ensure_ascii=False),
            atomic=atomic,
            encoding="utf-8",
        )

    def write_text_privileged(
        self,
        path: str,
        content: str,
        *,
        mode: int | None = None,
        encoding: str = "utf-8",
    ) -> None:
        data = content.encode(encoding)
        if not self._needs_remote_privilege():
            self.write_bytes(path, data, atomic=True)
            if mode is not None:
                self.chmod(path, mode)
            return

        path = self.expand(path)
        parent = self.dirname(path)
        if parent:
            self.mkdir_privileged(parent)
        tmp_dir = self.join(self.home_dir(), ".sysdialogue", "tmp")
        self.mkdir(tmp_dir, parents=True)
        tmp = self.join(tmp_dir, f"{self.basename(path)}.{uuid.uuid4().hex}.tmp")
        self.write_bytes(tmp, data, atomic=True)
        try:
            cmd = ["install"]
            if mode is not None:
                cmd.extend(["-m", f"{mode:04o}"])
            cmd.extend([tmp, path])
            out, code = self.executor.run_privileged(cmd, timeout=15)
            if code != 0:
                raise OSError(out or f"privileged install failed: {path}")
        finally:
            if self.exists(tmp):
                self.remove(tmp)

    def write_json_privileged(self, path: str, data: Any, *, mode: int | None = None) -> None:
        self.write_text_privileged(
            path,
            json.dumps(data, indent=2, ensure_ascii=False),
            mode=mode,
            encoding="utf-8",
        )

    def mkdir_privileged(self, path: str) -> None:
        if not self._needs_remote_privilege():
            self.mkdir(path, parents=True)
            return
        out, code = self.executor.run_privileged(["mkdir", "-p", self.expand(path)], timeout=10)
        if code != 0:
            raise OSError(out or f"privileged mkdir failed: {path}")

    def remove_privileged(self, path: str, *, recursive: bool = False) -> None:
        if not self._needs_remote_privilege():
            self.remove(path, recursive=recursive)
            return
        cmd = ["rm", "-rf" if recursive else "-f", self.expand(path)]
        out, code = self.executor.run_privileged(cmd, timeout=15)
        if code != 0:
            raise OSError(out or f"privileged remove failed: {path}")

    def dirname(self, path: str) -> str:
        if self.is_remote:
            return posixpath.dirname(path)
        return str(Path(path).parent)

    def basename(self, path: str) -> str:
        if self.is_remote:
            return posixpath.basename(path)
        return Path(path).name

    def _remote_atomic_rename(self, sftp, tmp: str, path: str) -> None:
        posix_rename = getattr(sftp, "posix_rename", None)
        if callable(posix_rename):
            try:
                posix_rename(tmp, path)
                return
            except OSError:
                pass
        try:
            sftp.rename(tmp, path)
            return
        except OSError:
            pass
        try:
            sftp.remove(path)
        except OSError:
            pass
        sftp.rename(tmp, path)

    def remote_run(self, cmd: list[str], *, timeout: int = 30) -> tuple[str, int]:
        return self.executor.run(cmd, timeout=timeout)

    def _mkdir_remote(self, path: str, *, parents: bool) -> None:
        sftp = self._remote_sftp()
        if not parents:
            sftp.mkdir(path)
            return

        normalized = path if path.startswith("/") else posixpath.join(self.home_dir(), path)
        parts = [p for p in normalized.split("/") if p]
        current = "/" if normalized.startswith("/") else ""
        for part in parts:
            current = posixpath.join(current, part) if current else part
            try:
                sftp.stat(current)
            except OSError:
                sftp.mkdir(current)

    def _remote_sftp(self):
        remote = self.executor
        if not isinstance(remote, RemoteExecutor):
            raise RuntimeError("remote sftp requested for non-remote executor")
        return remote.open_sftp()

    def _run_remote(self, cmd: list[str], timeout: int = 30) -> None:
        out, code = self.executor.run(cmd, timeout=timeout)
        if code != 0:
            raise OSError(out or f"command failed: {' '.join(cmd)}")

    def _needs_remote_privilege(self) -> bool:
        remote = self.executor
        return isinstance(remote, RemoteExecutor) and remote.username != "root"

