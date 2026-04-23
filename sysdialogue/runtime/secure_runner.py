"""SafeExecutor — 统一超时、截断、异常处理的执行器基类。"""

from __future__ import annotations

import subprocess
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass

MAX_OUTPUT_BYTES = 512 * 1024  # 512 KB


@dataclass
class RunResult:
    stdout: str
    stderr: str
    exit_code: int
    truncated: bool = False
    timed_out: bool = False


class SafeExecutor(ABC):
    """抽象执行器，子类实现 _raw_run。"""

    def run(self, cmd: list[str], timeout: int = 30) -> tuple[str, int]:
        """执行命令，返回 (output, exit_code)；output 是 stdout+stderr 的合并。"""
        result = self.run_full(cmd, timeout=timeout)
        combined = result.stdout
        if result.stderr:
            combined = (combined + "\n" + result.stderr).strip()
        if result.timed_out:
            combined += "\n[TIMEOUT]"
        if result.truncated:
            combined += "\n[OUTPUT TRUNCATED]"
        return combined, result.exit_code

    def run_full(self, cmd: list[str], timeout: int = 30) -> RunResult:
        try:
            return self._raw_run(cmd, timeout)
        except Exception as e:
            return RunResult(stdout="", stderr=str(e), exit_code=1)

    @abstractmethod
    def _raw_run(self, cmd: list[str], timeout: int) -> RunResult:
        ...


class LocalExecutor(SafeExecutor):
    """本地进程执行器，shell=False，避免注入。"""

    def _raw_run(self, cmd: list[str], timeout: int) -> RunResult:
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                timeout=timeout,
                shell=False,
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


def _truncate(text: str) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) > MAX_OUTPUT_BYTES:
        return encoded[:MAX_OUTPUT_BYTES].decode("utf-8", errors="ignore")
    return text
