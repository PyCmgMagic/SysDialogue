"""EnvPanel — F4 环境画像侧边栏（脱敏后的 EnvProfile）。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Static

from sysdialogue.runtime.capability_probe import EnvProfileSanitizer

if TYPE_CHECKING:
    from sysdialogue.runtime.capability_probe import EnvProfile


# 字段归类：分区标题 → 字段 key 列表（顺序即展示顺序）
_SECTIONS: list[tuple[str, list[str]]] = [
    ("操作系统", [
        "os_type", "distro", "distro_version", "kernel", "arch",
        "hostname", "uptime",
    ]),
    ("硬件 & 资源", [
        "cpu_count", "cpu_model", "memory_total", "memory_available",
        "disk_total", "disk_free", "load_avg",
    ]),
    ("运行时 & 包管理", [
        "python_version", "shell", "package_manager",
        "init_system", "systemd_available",
    ]),
    ("可用工具链", [
        "available_tools", "installed_packages",
    ]),
    ("网络 & 访问", [
        "hostname", "default_interface", "ipv4_address",
        "ssh_available", "remote_mode",
    ]),
    ("安全策略", [
        "selinux_status", "apparmor_status",
        "sudo_available", "nopasswd_sudo",
    ]),
]

_KEY_LABELS: dict[str, str] = {
    "os_type":            "系统类型",
    "distro":             "发行版",
    "distro_version":     "版本",
    "kernel":             "内核",
    "arch":               "架构",
    "hostname":           "主机名",
    "uptime":             "运行时长",
    "cpu_count":          "CPU 核数",
    "cpu_model":          "CPU 型号",
    "memory_total":       "内存总量",
    "memory_available":   "内存可用",
    "disk_total":         "磁盘总量",
    "disk_free":          "磁盘可用",
    "load_avg":           "负载均值",
    "python_version":     "Python",
    "shell":              "Shell",
    "package_manager":    "包管理器",
    "init_system":        "Init 系统",
    "systemd_available":  "systemd",
    "available_tools":    "检测到的工具",
    "installed_packages": "已安装包（部分）",
    "default_interface":  "默认网卡",
    "ipv4_address":       "IPv4 地址",
    "ssh_available":      "SSH",
    "remote_mode":        "远程模式",
    "selinux_status":     "SELinux",
    "apparmor_status":    "AppArmor",
    "sudo_available":     "sudo",
    "nopasswd_sudo":      "NOPASSWD sudo",
}


class EnvPanel(Vertical):
    """右侧栏 — 环境画像（脱敏后，与注入 System Prompt 的内容一致）。"""

    CSS = """
    EnvPanel {
        height: 100%;
        width: 100%;
        padding: 0;
    }

    EnvPanel #env_header {
        background: $success 16%;
        padding: 0 2;
        height: 2;
        content-align: left middle;
        text-style: bold;
        border-bottom: solid $success 20%;
    }

    EnvPanel #env_scroll {
        height: 1fr;
        padding: 0 1;
    }

    EnvPanel .section_title {
        text-style: bold;
        color: $accent;
        margin: 1 0 0 1;
    }

    EnvPanel .env_table {
        margin: 0 0 0 1;
    }
    """

    def __init__(self, env_profile: "EnvProfile"):
        super().__init__()
        self.env_profile = env_profile

    def compose(self) -> ComposeResult:
        yield Static("🖥  环境画像  ·  F4 切换", id="env_header")
        scroll = VerticalScroll(id="env_scroll")
        with scroll:
            yield from self._build_sections()

    def _build_sections(self):
        sanitized: dict[str, Any] = EnvProfileSanitizer.sanitize(self.env_profile)
        rendered_keys: set[str]   = set()

        for section_title, keys in _SECTIONS:
            rows: list[tuple[str, str]] = []
            for key in keys:
                if key not in sanitized:
                    continue
                rendered_keys.add(key)
                rows.append((_KEY_LABELS.get(key, key), _fmt_value(sanitized[key])))
            if not rows:
                continue
            yield Static(section_title, classes="section_title")
            yield Static(_render_kv_table(rows), classes="env_table")

        # 未分类字段兜底
        leftover = [
            (k, v) for k, v in sanitized.items() if k not in rendered_keys
        ]
        if leftover:
            yield Static("其他", classes="section_title")
            rows = [(_KEY_LABELS.get(k, k), _fmt_value(v)) for k, v in leftover]
            yield Static(_render_kv_table(rows), classes="env_table")


def _fmt_value(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(str(v) for v in value) if value else "—"
    if isinstance(value, bool):
        return "是" if value else "否"
    if value is None or value == "":
        return "—"
    return str(value)


def _render_kv_table(rows: list[tuple[str, str]]) -> str:
    """用 Rich markup 渲染简洁的两列键值表。"""
    lines = []
    for label, value in rows:
        lines.append(f"[dim]{label}[/dim]  {value}")
    return "\n".join(lines)
