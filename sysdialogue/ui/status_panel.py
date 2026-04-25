"""StatusPanel — 右侧栏顶部：实时系统资源速览。"""

from __future__ import annotations

import shutil
from typing import TYPE_CHECKING

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static

from sysdialogue.ui.theme import get_glyphs, get_theme

if TYPE_CHECKING:
    from sysdialogue.runtime.capability_probe import EnvProfile


# ─────────────────────────────── psutil 可选依赖 ────────────────────────────

try:
    import psutil  # type: ignore
    _HAS_PSUTIL = True
except Exception:  # pragma: no cover
    psutil = None  # type: ignore
    _HAS_PSUTIL = False


class StatusPanel(Vertical):
    """实时资源速览：CPU、内存、磁盘、负载，每 3 秒刷新一次。"""

    DEFAULT_CSS = """
    StatusPanel {
        height: auto;
        max-height: 14;
        padding: 0;
        background: $panel;
        border-bottom: solid $primary 15%;
    }
    StatusPanel #status_header {
        background: $primary 12%;
        padding: 0 2;
        height: 1;
        text-style: bold;
    }
    StatusPanel #status_body {
        padding: 1 2 1 2;
        height: auto;
    }
    """

    def __init__(self, env_profile: "EnvProfile | None" = None):
        super().__init__()
        self.env_profile = env_profile
        self._header = Static("", id="status_header")
        self._body   = Static("", id="status_body")

    def compose(self) -> ComposeResult:
        yield self._header
        yield self._body

    def on_mount(self) -> None:
        g = get_glyphs()
        header = Text()
        header.append(f" {g.info}  系统状态", style="bold")
        self._header.update(header)
        self.refresh_data()
        self.set_interval(3.0, self.refresh_data)

    # ─────────────────────────────── render ─────────────────────────────────

    def refresh_data(self) -> None:
        self._body.update(self._build_body())

    def _build_body(self) -> Text:
        t = get_theme()
        body = Text()

        if not _HAS_PSUTIL:
            body.append("未安装 psutil，无法显示实时状态。\n", style="dim")
            body.append("pip install psutil 后重启即可。", style="dim italic")
            return body

        # CPU
        try:
            cpu = psutil.cpu_percent(interval=None)
            body.append_text(_bar_row("CPU ", cpu, 100, suffix=f"{cpu:>5.1f}%"))
            body.append("\n")
        except Exception:
            pass

        # Memory
        try:
            mem = psutil.virtual_memory()
            body.append_text(_bar_row(
                "MEM ",
                mem.percent, 100,
                suffix=f"{_fmt_bytes(mem.used)} / {_fmt_bytes(mem.total)}",
            ))
            body.append("\n")
        except Exception:
            pass

        # Disk (/ 根分区)
        try:
            du = shutil.disk_usage("/")
            pct = du.used / du.total * 100 if du.total else 0
            body.append_text(_bar_row(
                "DSK ",
                pct, 100,
                suffix=f"{_fmt_bytes(du.used)} / {_fmt_bytes(du.total)}",
            ))
            body.append("\n")
        except Exception:
            pass

        # Load avg
        try:
            if hasattr(psutil, "getloadavg"):
                la1, la5, la15 = psutil.getloadavg()
                body.append(f"LOAD  {la1:.2f}  {la5:.2f}  {la15:.2f}", style=t.muted)
        except Exception:
            pass

        return body


# ─────────────────────────────── helpers ────────────────────────────────────

def _bar_row(label: str, value: float, total: float, *, suffix: str = "", width: int = 14) -> Text:
    t = get_theme()
    pct = max(0.0, min(1.0, value / total if total else 0))
    filled = int(pct * width)
    empty  = width - filled

    # 颜色按阈值
    if pct >= 0.9:
        color = t.error
    elif pct >= 0.75:
        color = t.warning
    else:
        color = t.success

    row = Text()
    row.append(label, style="bold")
    row.append(" ")
    row.append("█" * filled, style=color)
    row.append("░" * empty, style=t.muted)
    row.append("  ")
    row.append(suffix, style="dim")
    return row


def _fmt_bytes(n: float) -> str:
    for unit in ("B", "K", "M", "G", "T"):
        if abs(n) < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}P"
