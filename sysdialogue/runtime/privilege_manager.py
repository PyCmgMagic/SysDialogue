"""In-session sudo password manager for interactive privilege elevation.

Security invariants:
  - The password is kept in process memory only. It is never written to disk,
    logs, argv, audit entries, trace spans, or environment variables.
  - Access is serialized by a lock so concurrent privileged tool calls do not
    trigger multiple simultaneous prompts.
  - ``clear()`` wipes the cached password and should be called on runtime
    teardown.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Callable, Optional

InputCallback = Callable[..., str]

DEFAULT_PROMPT = "需要 sudo 密码以执行特权命令（仅本次会话内存中保留）"


@dataclass
class PrivilegeManager:
    input_callback: Optional[InputCallback] = None
    _password: Optional[str] = field(default=None, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def has_password(self) -> bool:
        return self._password is not None

    @property
    def can_prompt(self) -> bool:
        return self.input_callback is not None

    def set_input_callback(self, cb: Optional[InputCallback]) -> None:
        self.input_callback = cb

    def get_cached(self) -> Optional[str]:
        return self._password

    def ensure_password(
        self,
        *,
        prompt: str = DEFAULT_PROMPT,
        force_refresh: bool = False,
    ) -> Optional[str]:
        """Return the cached password, prompting interactively if needed.

        Returns ``None`` if no callback is available or the user supplies an
        empty value. Serialized across threads — only one prompt can be in
        flight at a time; other callers wait and see the freshly cached value.
        """
        with self._lock:
            if not force_refresh and self._password is not None:
                return self._password
            cb = self.input_callback
            if cb is None:
                return None
            value = _invoke_sensitive_input(cb, prompt=prompt)
            if not value:
                return None
            self._password = value
            return self._password

    def invalidate(self) -> None:
        with self._lock:
            self._password = None

    def clear(self) -> None:
        self.invalidate()


def _invoke_sensitive_input(cb: InputCallback, *, prompt: str) -> str:
    """Call the input callback requesting masked input.

    Accepts legacy two-argument callbacks (``prompt``, ``multiline``) so
    downstream surfaces can upgrade to the sensitive-aware signature over time.
    """
    try:
        value = cb(prompt, False, sensitive=True)
    except TypeError:
        value = cb(prompt, False)
    return value or ""
