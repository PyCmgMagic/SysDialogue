"""SysDialogue TUI — 主题与符号系统。

两套主题：
  - slate : 冷灰蓝（默认，偏科技感）
  - euler : openEuler 冷白绿（契合赛题）

符号系统：
  - 默认使用 ASCII 安全符号（`>>`、`::`、`[+]` 等）
  - 若终端支持 NerdFont，启用更精细的图标（会在 runtime 检测 env）
"""

from __future__ import annotations

import os
from dataclasses import dataclass


# ─────────────────────────────── 主题定义 ───────────────────────────────────

@dataclass(frozen=True)
class Theme:
    name:      str
    primary:   str
    accent:    str
    success:   str
    warning:   str
    error:     str
    muted:     str
    banner_fg: str
    banner_bg_tint: int   # 0-100 蒙层百分比


THEMES: dict[str, Theme] = {
    "slate": Theme(
        name="slate",
        primary="#5b8dee",
        accent="#3ddccd",
        success="#3dd68c",
        warning="#f5c842",
        error="#f55a5a",
        muted="#6b7494",
        banner_fg="#5b8dee",
        banner_bg_tint=10,
    ),
    "euler": Theme(
        name="euler",
        primary="#00b388",   # openEuler 官方绿
        accent="#6fd6b0",
        success="#5fd9a3",
        warning="#e8b341",
        error="#e85a5a",
        muted="#7b8a85",
        banner_fg="#00b388",
        banner_bg_tint=8,
    ),
}


def get_theme() -> Theme:
    name = os.environ.get("SYSDIALOGUE_THEME", "slate").lower()
    return THEMES.get(name, THEMES["slate"])


# ─────────────────────────────── 符号系统 ───────────────────────────────────

@dataclass(frozen=True)
class Glyphs:
    """状态与结构符号。ASCII 默认 / NerdFont 增强。"""
    # 状态
    ok:        str
    fail:      str
    warn:      str
    running:   str
    pending:   str
    cancelled: str
    blocked:   str
    info:      str
    # 结构
    arrow_run:  str   # 工具正在执行
    bullet:     str   # 列表项
    sep:        str   # meta 分隔符
    caret_open: str   # 折叠展开
    caret_shut: str   # 折叠收起
    # 分区图标（折叠标题前）
    sec_think:  str
    sec_tool:   str
    sec_verify: str
    sec_result: str
    sec_error:  str
    sec_debug:  str


ASCII_GLYPHS = Glyphs(
    ok="[+]",       fail="[x]",     warn="[!]",     running="...",
    pending="[ ]",  cancelled="[-]", blocked="[#]",  info="[i]",
    arrow_run=">>", bullet="-",     sep="::",
    caret_open="v", caret_shut=">",
    sec_think="::", sec_tool="**",  sec_verify="++",
    sec_result=">>", sec_error="!!", sec_debug="##",
)


NERD_GLYPHS = Glyphs(
    ok="",       fail="",     warn="",     running="",
    pending="",  cancelled="", blocked="", info="",
    arrow_run="", bullet="",     sep="·",
    caret_open="", caret_shut="",
    sec_think="", sec_tool="",  sec_verify="",
    sec_result="", sec_error="", sec_debug="",
)


def _nerdfont_enabled() -> bool:
    val = os.environ.get("SYSDIALOGUE_NERDFONT", "").strip().lower()
    return val in {"1", "true", "yes", "on"}


def get_glyphs() -> Glyphs:
    return NERD_GLYPHS if _nerdfont_enabled() else ASCII_GLYPHS


# ─────────────────────────────── Textual CSS 变量导出 ───────────────────────

def theme_css_vars() -> str:
    """生成 Textual CSS 片段，把主题颜色映射到内置 variable。"""
    t = get_theme()
    return f"""
    Screen {{
        background: $surface;
    }}
    /* 主题覆盖 */
    .theme-primary {{ color: {t.primary}; }}
    .theme-accent  {{ color: {t.accent}; }}
    .theme-success {{ color: {t.success}; }}
    .theme-warn    {{ color: {t.warning}; }}
    .theme-error   {{ color: {t.error}; }}
    .theme-muted   {{ color: {t.muted}; }}
    """
