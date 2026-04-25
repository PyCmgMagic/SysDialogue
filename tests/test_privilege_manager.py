from __future__ import annotations

import threading
from typing import Callable, Optional

import pytest

from sysdialogue.runtime.privilege_manager import PrivilegeManager
from sysdialogue.runtime.secure_runner import LocalExecutor, RunResult


class FakeLocalExecutor(LocalExecutor):
    """LocalExecutor that never spawns subprocesses; handler decides each outcome.

    Handler signature: ``(cmd, stdin_bytes) -> (stdout, stderr, exit_code)``.
    Calls are recorded with the cmd and whatever bytes were piped to stdin so
    tests can assert passwords never leak into argv.
    """

    def __init__(
        self,
        handler: Callable[[list[str], Optional[bytes]], tuple[str, str, int]],
        privilege_manager: Optional[PrivilegeManager] = None,
    ):
        super().__init__(privilege_manager=privilege_manager)
        self.handler = handler
        self.calls: list[tuple[list[str], Optional[bytes]]] = []

    # Force non-root so run_privileged always takes the sudo branch.
    def run_privileged(self, cmd, timeout=30, cwd=None):  # noqa: D401 - reuse parent logic
        import os as _os

        original_geteuid = getattr(_os, "geteuid", None)
        if original_geteuid is not None:
            _os.geteuid = lambda: 1000  # type: ignore[assignment]
        try:
            return super().run_privileged(cmd, timeout=timeout, cwd=cwd)
        finally:
            if original_geteuid is not None:
                _os.geteuid = original_geteuid  # type: ignore[assignment]

    def _raw_run_with_stdin(self, cmd, *, timeout, stdin_bytes, cwd=None):
        self.calls.append((list(cmd), stdin_bytes))
        stdout, stderr, exit_code = self.handler(list(cmd), stdin_bytes)
        return RunResult(stdout=stdout, stderr=stderr, exit_code=exit_code)

    def _raw_run(self, cmd, timeout, cwd=None):
        self.calls.append((list(cmd), None))
        stdout, stderr, exit_code = self.handler(list(cmd), None)
        return RunResult(stdout=stdout, stderr=stderr, exit_code=exit_code)


# ---------------------------------------------------------------------------
# PrivilegeManager itself
# ---------------------------------------------------------------------------


def test_ensure_password_uses_cached_value() -> None:
    calls: list[str] = []

    def cb(prompt: str, multiline: bool, sensitive: bool = False) -> str:
        calls.append(prompt)
        return "s3cret"

    pm = PrivilegeManager(input_callback=cb)
    assert pm.ensure_password() == "s3cret"
    assert pm.ensure_password() == "s3cret"
    # Callback fired exactly once because the second call reused the cache.
    assert len(calls) == 1


def test_ensure_password_force_refresh_reprompts() -> None:
    answers = iter(["first", "second"])

    def cb(prompt: str, multiline: bool, sensitive: bool = False) -> str:
        return next(answers)

    pm = PrivilegeManager(input_callback=cb)
    assert pm.ensure_password() == "first"
    assert pm.ensure_password(force_refresh=True) == "second"


def test_ensure_password_returns_none_without_callback() -> None:
    pm = PrivilegeManager()
    assert pm.ensure_password() is None
    assert pm.has_password is False


def test_ensure_password_empty_answer_not_cached() -> None:
    calls = {"n": 0}

    def cb(prompt: str, multiline: bool, sensitive: bool = False) -> str:
        calls["n"] += 1
        return ""

    pm = PrivilegeManager(input_callback=cb)
    assert pm.ensure_password() is None
    assert pm.ensure_password() is None
    # Both calls re-prompted because no value was ever cached.
    assert calls["n"] == 2


def test_ensure_password_supports_legacy_two_arg_callback() -> None:
    def cb(prompt, multiline):
        return "legacy"

    pm = PrivilegeManager(input_callback=cb)
    assert pm.ensure_password() == "legacy"


def test_invalidate_clears_cache() -> None:
    pm = PrivilegeManager(input_callback=lambda p, m, sensitive=False: "pw")
    pm.ensure_password()
    assert pm.has_password is True
    pm.invalidate()
    assert pm.has_password is False


def test_concurrent_callers_see_single_prompt() -> None:
    """Two threads racing on an empty cache must fire only one prompt."""
    gate = threading.Event()
    call_count = {"n": 0}

    def cb(prompt: str, multiline: bool, sensitive: bool = False) -> str:
        call_count["n"] += 1
        gate.wait(timeout=2)
        return "shared"

    pm = PrivilegeManager(input_callback=cb)

    results: list[Optional[str]] = []
    lock = threading.Lock()

    def worker() -> None:
        value = pm.ensure_password()
        with lock:
            results.append(value)

    t1 = threading.Thread(target=worker)
    t2 = threading.Thread(target=worker)
    t1.start()
    t2.start()
    # Let the first prompt settle before releasing; the second caller is blocked
    # on the manager lock and will reuse the cached value.
    gate.set()
    t1.join(timeout=3)
    t2.join(timeout=3)
    assert results == ["shared", "shared"]
    assert call_count["n"] == 1


# ---------------------------------------------------------------------------
# LocalExecutor interactive elevation
# ---------------------------------------------------------------------------


def _passwordless_handler(cmd, stdin_bytes):
    if cmd[:2] == ["sudo", "-n"]:
        return ("done", "", 0)
    return ("", "unexpected", 1)


def test_local_run_privileged_passwordless_path() -> None:
    executor = FakeLocalExecutor(handler=_passwordless_handler)
    out, code = executor.run_privileged(["systemctl", "status", "nginx"])
    assert code == 0
    assert "done" in out
    # No stdin should ever have been piped.
    assert all(stdin is None for _, stdin in executor.calls)


def test_local_run_privileged_without_manager_fails_gracefully() -> None:
    def handler(cmd, stdin_bytes):
        return ("", "a password is required", 1)

    executor = FakeLocalExecutor(handler=handler)
    out, code = executor.run_privileged(["ls", "/root"])
    assert code != 0
    # With no PrivilegeManager we must stay non-interactive: only one call.
    assert len(executor.calls) == 1


def test_local_run_privileged_prompts_validates_and_retries() -> None:
    state = {"validated": False}

    def handler(cmd, stdin_bytes):
        if cmd[:3] == ["sudo", "-n", "--"]:
            if state["validated"]:
                return ("ok", "", 0)
            return ("", "a password is required", 1)
        if cmd[:4] == ["sudo", "-S", "-p", ""] and cmd[4] == "-v":
            if stdin_bytes == b"correct\n":
                state["validated"] = True
                return ("", "", 0)
            return ("", "Sorry, try again.", 1)
        return ("", f"unexpected: {cmd}", 1)

    pm = PrivilegeManager(
        input_callback=lambda p, m, sensitive=False: "correct",
    )
    executor = FakeLocalExecutor(handler=handler, privilege_manager=pm)
    out, code = executor.run_privileged(["systemctl", "restart", "nginx"])
    assert code == 0
    assert "ok" in out

    # Password bytes must only appear on the -v validation call.
    stdin_sends = [stdin for cmd, stdin in executor.calls if stdin is not None]
    assert stdin_sends == [b"correct\n"]
    # The privileged command itself ran with sudo -n (no stdin).
    final_call = executor.calls[-1]
    assert final_call[0][:3] == ["sudo", "-n", "--"]
    assert final_call[1] is None


def test_local_run_privileged_wrong_password_reprompts_once_then_fails() -> None:
    answers = iter(["wrong1", "wrong2"])

    def cb(prompt, multiline, sensitive=False):
        return next(answers)

    def handler(cmd, stdin_bytes):
        if cmd[:3] == ["sudo", "-n", "--"]:
            return ("", "a password is required", 1)
        if cmd[4] == "-v":
            return ("", "Sorry, try again.", 1)
        return ("", "unexpected", 1)

    pm = PrivilegeManager(input_callback=cb)
    executor = FakeLocalExecutor(handler=handler, privilege_manager=pm)
    out, code = executor.run_privileged(["ls", "/root"])
    assert code != 0
    # Cache must be cleared after failed auth so future calls re-prompt.
    assert pm.has_password is False
    # Exactly two validation attempts were made.
    validation_calls = [c for c, _ in executor.calls if len(c) > 4 and c[4] == "-v"]
    assert len(validation_calls) == 2


def test_local_run_privileged_caches_password_across_calls() -> None:
    prompts = {"n": 0}

    def cb(prompt, multiline, sensitive=False):
        prompts["n"] += 1
        return "pw"

    def handler(cmd, stdin_bytes):
        if cmd[:3] == ["sudo", "-n", "--"]:
            # Simulate a warm timestamp after first -v succeeds, but for the
            # very first sudo -n (before auth) fail so the interactive path fires.
            if state.get("warm"):
                return ("ok", "", 0)
            return ("", "password required", 1)
        if cmd[4] == "-v":
            state["warm"] = True
            return ("", "", 0)
        return ("", "unexpected", 1)

    state: dict = {}
    pm = PrivilegeManager(input_callback=cb)
    executor = FakeLocalExecutor(handler=handler, privilege_manager=pm)

    out1, code1 = executor.run_privileged(["systemctl", "start", "a"])
    out2, code2 = executor.run_privileged(["systemctl", "start", "b"])
    assert code1 == 0 and code2 == 0
    # Second call hit the warm sudo -n directly — no re-prompt, no re-validate.
    assert prompts["n"] == 1


def test_runtime_bundle_close_clears_password(tmp_path, monkeypatch) -> None:
    """runtime.close() must wipe the cached sudo password."""
    from sysdialogue.app.config import AppConfig
    from sysdialogue.app.runtime_factory import create_runtime

    config = AppConfig()

    # Prevent create_runtime from probing the real environment by stubbing
    # CapabilityProbe.probe to a no-op.
    from sysdialogue.runtime import capability_probe as cap_mod

    monkeypatch.setattr(
        cap_mod.CapabilityProbe,
        "probe",
        lambda self: cap_mod._unknown_profile(False, 22),
    )

    bundle = create_runtime(
        config,
        session_id="pm-close-test",
        input_callback=lambda p, m, sensitive=False: "pw",
    )
    try:
        bundle.privilege_manager.ensure_password()
        assert bundle.privilege_manager.has_password is True
    finally:
        bundle.close()
    assert bundle.privilege_manager.has_password is False
