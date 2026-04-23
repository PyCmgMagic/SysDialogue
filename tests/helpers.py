from __future__ import annotations

from typing import Callable

from sysdialogue.runtime.secure_runner import RunResult, SafeExecutor


class RecordingExecutor(SafeExecutor):
    """Minimal executor double for unit tests."""

    def __init__(
        self,
        handler: Callable[[list[str], int], tuple[str, int] | tuple[str, int, str]] | None = None,
    ):
        self.handler = handler or (lambda cmd, timeout: ("", 0))
        self.calls: list[list[str]] = []

    def _raw_run(self, cmd: list[str], timeout: int) -> RunResult:
        self.calls.append(cmd)
        result = self.handler(cmd, timeout)
        if len(result) == 2:
            stdout, exit_code = result
            stderr = ""
        else:
            stdout, exit_code, stderr = result
        return RunResult(stdout=stdout, stderr=stderr, exit_code=exit_code)
