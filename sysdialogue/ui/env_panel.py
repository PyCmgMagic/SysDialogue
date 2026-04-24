"""EnvPanel — F4 环境画像面板（脱敏后 EnvProfile）。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Label, Static

from sysdialogue.runtime.capability_probe import EnvProfileSanitizer

if TYPE_CHECKING:
    from sysdialogue.runtime.capability_probe import EnvProfile


class EnvPanel(Vertical):
    """显示 EnvProfile 的能力特征（脱敏后，与 SystemPrompt 注入内容一致）。"""

    CSS = """
    EnvPanel {
        height: 100%;
        width: 100%;
        padding: 0;
    }
    EnvPanel Label {
        background: $success 20%;
        padding: 0 1;
        text-style: bold;
    }
    EnvPanel Static {
        padding: 1;
    }
    """

    def __init__(self, env_profile: "EnvProfile"):
        super().__init__()
        self.env_profile = env_profile

    def compose(self) -> ComposeResult:
        yield Label("🖥  环境画像 (F4 切换)")
        yield Static(self._build_env_text(), id="env_content")

    def _build_env_text(self) -> str:
        sanitized = EnvProfileSanitizer.sanitize(self.env_profile)
        lines = []
        for key, value in sanitized.items():
            if isinstance(value, list):
                value = ", ".join(str(v) for v in value) if value else "-"
            lines.append(f"[bold]{key}[/bold]: {value}")
        return "\n".join(lines)
