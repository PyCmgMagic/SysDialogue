"""Shared safe command executor primitives."""

from __future__ import annotations

import os
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

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

    def run(self, cmd: list[str], timeout: int = 30) -> tuple[str, int]:
        result = self.run_full(cmd, timeout=timeout)
        return _combine_result(result)

    def run_privileged(self, cmd: list[str], timeout: int = 30) -> tuple[str, int]:
        """Run a command that requires system privileges.

        Tool code must opt into this path explicitly. The default implementation
        uses non-interactive sudo so callers do not hang on a password prompt.
        """
        return self.run(["sudo", "-n", "--", *cmd], timeout=timeout)

    def run_full(self, cmd: list[str], timeout: int = 30) -> RunResult:
        try:
            return self._raw_run(cmd, timeout)
        except Exception as e:
            return RunResult(stdout="", stderr=str(e), exit_code=1)

    @abstractmethod
    def _raw_run(self, cmd: list[str], timeout: int) -> RunResult:
        ...


class LocalExecutor(SafeExecutor):
    """Local process executor using shell=False.

    Supports an optional :class:`PrivilegeManager` for interactive sudo
    elevation. When a privileged call fails because sudo needs a password, the
    executor prompts the user once (cached for the session), validates via
    ``sudo -S -v``, and only then re-runs the real command with ``sudo -n``.
    The password is piped via stdin and never appears in argv, so it cannot
    leak into process listings, command traces, or audit logs.
    """

    def __init__(self, privilege_manager: Optional[PrivilegeManager] = None):
        self.privilege_manager = privilege_manager

    def run_privileged(self, cmd: list[str], timeout: int = 30) -> tuple[str, int]:
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            return self.run(cmd, timeout=timeout)

        sudo_cmd = ["sudo", "-n", "--", *cmd]
        result = self._raw_run_with_stdin(sudo_cmd, timeout=timeout, stdin_bytes=None)
        if result.exit_code == 0:
            return _combine_result(result)

        pm = self.privilege_manager
        if pm is None or not pm.can_prompt:
            return _combine_result(result)

        last_failure = result
        for attempt in range(2):
            pw = pm.ensure_password(force_refresh=(attempt > 0))
            if not pw:
                pm.invalidate()
                return _combine_result(last_failure)

            validation = self._raw_run_with_stdin(
                ["sudo", "-S", "-p", "", "-v"],
                timeout=timeout,
                stdin_bytes=(pw + "\n").encode("utf-8"),
            )
            if validation.exit_code == 0:
                final = self._raw_run_with_stdin(
                    sudo_cmd, timeout=timeout, stdin_bytes=None
                )
                return _combine_result(final)
            last_failure = validation

        pm.invalidate()
        return _combine_result(last_failure)

    def _raw_run(self, cmd: list[str], timeout: int) -> RunResult:
        return self._raw_run_with_stdin(cmd, timeout=timeout, stdin_bytes=None)

    def _raw_run_with_stdin(
        self,
        cmd: list[str],
        *,
        timeout: int,
        stdin_bytes: Optional[bytes],
    ) -> RunResult:
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                timeout=timeout,
                shell=False,
                input=stdin_bytes,
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
