"""Shared safe command executor primitives."""

from __future__ import annotations

import os
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass

from sysdialogue.runtime.privilege_manager import PrivilegeManager

MAX_OUTPUT_BYTES = 512 * 1024  # 512 KB


@dataclass
class RunResult:
    stdout: str
    stderr: str
    exit_code: int
    truncated: bool = False
    timed_out: bool = False


class SafeExecutor(ABC):
    """Base executor with timeout, truncation, and error normalization."""

    def run(self, cmd: list[str], timeout: int = 30, cwd: str | None = None) -> tuple[str, int]:
        result = self.run_full(cmd, timeout=timeout, cwd=cwd)
        return _combine_result(result)

    def run_privileged(self, cmd: list[str], timeout: int = 30, cwd: str | None = None) -> tuple[str, int]:
        """Run a command that requires system privileges.

        Tool code must opt into this path explicitly. The default implementation
        uses non-interactive sudo so callers do not hang on a password prompt.
        """
        return self.run(["sudo", "-n", "--", *cmd], timeout=timeout, cwd=cwd)

    def run_full(self, cmd: list[str], timeout: int = 30, cwd: str | None = None) -> RunResult:
        try:
            return self._raw_run(cmd, timeout, cwd=cwd)
        except Exception as e:
            return RunResult(stdout="", stderr=str(e), exit_code=1)

    @abstractmethod
    def _raw_run(self, cmd: list[str], timeout: int, cwd: str | None = None) -> RunResult:
        ...


class LocalExecutor(SafeExecutor):
    """Local process executor using shell=False."""

    def __init__(self, privilege_manager: PrivilegeManager | None = None):
        self.privilege_manager = privilege_manager

    def run_privileged(self, cmd: list[str], timeout: int = 30, cwd: str | None = None) -> tuple[str, int]:
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            return self.run(cmd, timeout=timeout, cwd=cwd)
        sudo_cmd = ["sudo", "-n", "--", *cmd]
        result = self.run_full(sudo_cmd, timeout=timeout, cwd=cwd)
        if result.exit_code == 0 or self.privilege_manager is None:
            return _combine_result(result)
        password = self.privilege_manager.ensure_password()
        for attempt in range(2):
            if not password:
                return _combine_result(result)
            validate = self._raw_run_with_stdin(
                ["sudo", "-S", "-p", "", "-v"],
                timeout=timeout,
                stdin_bytes=(password + "\n").encode("utf-8"),
                cwd=cwd,
            )
            if validate.exit_code == 0:
                return self.run(sudo_cmd, timeout=timeout, cwd=cwd)
            self.privilege_manager.invalidate()
            if attempt == 0:
                password = self.privilege_manager.ensure_password(force_refresh=True)
                continue
            return _combine_result(validate)
        return _combine_result(result)

    def _raw_run_with_stdin(
        self,
        cmd: list[str],
        *,
        timeout: int,
        stdin_bytes: bytes | None,
        cwd: str | None = None,
    ) -> RunResult:
        try:
            proc = subprocess.run(
                cmd,
                input=stdin_bytes,
                capture_output=True,
                timeout=timeout,
                shell=False,
                cwd=cwd,
            )
            stdout = _truncate(proc.stdout.decode("utf-8", errors="replace"))
            stderr = _truncate(proc.stderr.decode("utf-8", errors="replace"))
            truncated = (
                len(proc.stdout) >= MAX_OUTPUT_BYTES or len(proc.stderr) >= MAX_OUTPUT_BYTES
            )
            return RunResult(stdout=stdout, stderr=stderr, exit_code=proc.returncode, truncated=truncated)
        except subprocess.TimeoutExpired:
            return RunResult(stdout="", stderr="Command timed out", exit_code=124, timed_out=True)
        except FileNotFoundError:
            return RunResult(stdout="", stderr=f"Command not found: {cmd[0]}", exit_code=127)


    def _raw_run(self, cmd: list[str], timeout: int, cwd: str | None = None) -> RunResult:
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                timeout=timeout,
                shell=False,
                cwd=cwd,
            )
            stdout = _truncate(proc.stdout.decode("utf-8", errors="replace"))
            stderr = _truncate(proc.stderr.decode("utf-8", errors="replace"))
            truncated = (
                len(proc.stdout) >= MAX_OUTPUT_BYTES or len(proc.stderr) >= MAX_OUTPUT_BYTES
            )
            return RunResult(
                stdout=stdout,
                stderr=stderr,
                exit_code=proc.returncode,
                truncated=truncated,
            )
        except subprocess.TimeoutExpired:
            return RunResult(stdout="", stderr="Command timed out", exit_code=124, timed_out=True)
        except FileNotFoundError:
            return RunResult(stdout="", stderr=f"Command not found: {cmd[0]}", exit_code=127)


def _combine_result(result: RunResult) -> tuple[str, int]:
    combined = result.stdout
    if result.stderr:
        combined = (combined + "\n" + result.stderr).strip()
    if result.timed_out:
        combined += "\n[TIMEOUT]"
    if result.truncated:
        combined += "\n[OUTPUT TRUNCATED]"
    return combined, result.exit_code


def _truncate(text: str) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) > MAX_OUTPUT_BYTES:
        return encoded[:MAX_OUTPUT_BYTES].decode("utf-8", errors="ignore")
    return text
