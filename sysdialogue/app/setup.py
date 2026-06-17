"""交互式配置向导 — 首次使用时引导用户设置 API Key、模型等。

用法:
  sysdialogue --setup         交互式配置
  sysdialogue --setup --reset 重新配置
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.prompt import Confirm, Prompt
from rich.table import Table
from rich.text import Text

console = Console()

# ─────────────────────────────────────────────────────────
# 全局配置路径
# ─────────────────────────────────────────────────────────
_CONFIG_DIR = Path.home() / ".sysdialogue"
_CONFIG_FILE = _CONFIG_DIR / "config"

_BANNER = r"""
  ____            ____        _                   _
 / ___| _   _ ___|  _ \  __ _| |_ __ _  ___  __ _| |
 \___ \| | | / __| | | |/ _` | __/ _` |/ _ \/ _` | |
  ___) | |_| \__ \ |_| | (_| | || (_| |  __/ (_| | |
 |____/ \__, |___/____/ \__,_|\__\__,_|\___|\__,_|_|
        |___/
"""

_PRESETS = {
    "1": {
        "label": "OpenAI",
        "url": "https://api.openai.com/v1",
        "models": ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "o4-mini"],
    },
    "2": {
        "label": "DeepSeek",
        "url": "https://api.deepseek.com",
        "models": ["deepseek-chat", "deepseek-reasoner"],
    },
    "3": {
        "label": "智谱 AI (ZhipuAI)",
        "url": "https://open.bigmodel.cn/api/paas/v4",
        "models": ["glm-4-plus", "glm-4-flash"],
    },
    "4": {
        "label": "通义千问 (Qwen)",
        "url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "models": ["qwen-max", "qwen-plus", "qwen-turbo"],
    },
    "5": {
        "label": "Ollama (本地)",
        "url": "http://localhost:11434/v1",
        "models": ["llama3", "qwen2:7b", "gemma2"],
    },
    "6": {
        "label": "自定义 / 其他 OpenAI 兼容服务",
        "url": "",
        "models": [],
    },
}


# ─────────────────────────────────────────────────────────
# 公共 API
# ─────────────────────────────────────────────────────────
def get_config_path() -> Path:
    """返回全局配置文件路径。"""
    return _CONFIG_FILE


def has_global_config() -> bool:
    """检查是否已有全局配置。"""
    return _CONFIG_FILE.exists() and _CONFIG_FILE.stat().st_size > 0


def load_global_config() -> dict[str, str]:
    """从全局配置文件加载键值对。"""
    if not _CONFIG_FILE.exists():
        return {}
    result: dict[str, str] = {}
    for line in _CONFIG_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            result[key.strip()] = value.strip()
    return result


def save_global_config(values: dict[str, str]) -> Path:
    """保存配置到全局配置文件。"""
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    lines = [
        "# SysDialogue 全局配置",
        "# 由 sysdialogue --setup 生成，也可手动编辑",
        f"# 最后修改: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
    ]
    for key, value in values.items():
        lines.append(f"{key}={value}")
    lines.append("")
    _CONFIG_FILE.write_text("\n".join(lines), encoding="utf-8")
    return _CONFIG_FILE


def show_config() -> int:
    """显示当前配置状态。返回 0。"""
    config = load_global_config()
    if not config:
        console.print()
        console.print("[yellow]尚未配置。[/yellow] 运行 [bold]sysdialogue --setup[/bold] 进行配置。")
        console.print()
        return 0

    console.print()
    table = Table(title="SysDialogue 当前配置", show_header=True, border_style="cyan", padding=(0, 1))
    table.add_column("配置项", style="bold cyan")
    table.add_column("值", style="white")
    table.add_column("来源", style="dim")

    for key, value in config.items():
        if key == "OPENAI_API_KEY":
            display = _mask_key(value)
        else:
            display = value
        table.add_row(key, display, "~/.sysdialogue/config")

    console.print(table)
    console.print()
    console.print("[dim]提示: sysdialogue --setup 重新配置 | sysdialogue --setup --reset 重置[/dim]")
    console.print()
    return 0


def run_setup(*, reset: bool = False) -> int:
    """运行交互式配置向导。返回 0 表示成功。"""
    existing = load_global_config() if not reset else {}

    # ── 欢迎页 ──
    console.print()
    console.print(Text(_BANNER, style="bold cyan"))
    if existing and not reset:
        console.print(Panel(
            "[bold]重新配置[/bold] — 当前值已预填，按 Enter 保留，输入新值覆盖。",
            border_style="cyan", padding=(0, 2),
        ))
    else:
        console.print(Panel(
            "[bold]欢迎！[/bold] 让我们花 30 秒配置好你的 AI 连接。\n"
            "按 Enter 保留默认值，输入新值则覆盖。",
            border_style="cyan", padding=(0, 2),
        ))
    console.print()

    # ── 步骤 1/4: 选择服务商 ──
    _print_step(1, 4, "选择 AI 服务商")

    if existing and not reset:
        current_url = existing.get("OPENAI_BASE_URL", "")
        # 自动检测之前的服务商
        detected = _detect_provider(current_url)
        if detected:
            console.print(f"  [dim]当前配置: {detected}[/dim]")
            console.print()

    for k, p in _PRESETS.items():
        icon = "●" if k == "1" else "○"
        console.print(f"  [{k}] {icon} {p['label']}")
    console.print()

    choice = Prompt.ask("  请选择", choices=list(_PRESETS.keys()), default="1")
    preset = _PRESETS[choice]
    console.print()

    # ── 步骤 2/4: API Key ──
    _print_step(2, 4, "API Key")

    current_key = existing.get("OPENAI_API_KEY", "")
    masked = _mask_key(current_key) if current_key else "[dim red]未设置[/dim red]"
    console.print(f"  当前: {masked}")
    if preset["label"] == "Ollama (本地)":
        console.print("  [dim]Ollama 本地运行，API Key 可留空[/dim]")
        api_key = Prompt.ask("  API Key", default=current_key or "ollama", password=True, show_default=False)
    else:
        api_key = Prompt.ask("  输入 API Key", default=current_key, password=True, show_default=False)
    console.print()

    # ── 步骤 3/4: Base URL + 模型 ──
    _print_step(3, 4, "连接信息")

    current_url = existing.get("OPENAI_BASE_URL", "") or preset["url"]
    base_url = Prompt.ask(
        "  Base URL",
        default=current_url or "https://api.openai.com/v1",
    )

    # 模型选择
    current_model = existing.get("OPENAI_MODEL", "") or ""
    if preset["models"]:
        console.print()
        console.print("  推荐模型:")
        for i, m in enumerate(preset["models"], 1):
            console.print(f"    [{i}] {m}")
        console.print(f"    [0] 自定义输入")
        console.print()

        model_choice = Prompt.ask(
            "  选择模型编号或输入模型名",
            default=current_model or preset["models"][0],
        )
        # 如果用户输入了数字，映射到模型名
        if model_choice.isdigit():
            idx = int(model_choice)
            if 1 <= idx <= len(preset["models"]):
                model = preset["models"][idx - 1]
            else:
                model = model_choice
        else:
            model = model_choice
    else:
        model = Prompt.ask(
            "  模型名称",
            default=current_model,
        )
    console.print()

    # ── 步骤 4/4: 可选设置 ──
    _print_step(4, 4, "可选设置 (按 Enter 跳过)")

    current_iter = existing.get("SYSDIALOGUE_MAX_ITER", "160")
    want_advanced = Confirm.ask("  配置高级选项？", default=False)
    max_iter = current_iter
    if want_advanced:
        console.print()
        max_iter = Prompt.ask("  最大迭代次数 (20-300)", default=current_iter)
        console.print()

    # ── 汇总确认 ──
    config_values: dict[str, str] = {}
    if api_key:
        config_values["OPENAI_API_KEY"] = api_key
    if base_url:
        config_values["OPENAI_BASE_URL"] = base_url
    if model:
        config_values["OPENAI_MODEL"] = model
    if max_iter and max_iter != "160":
        config_values["SYSDIALOGUE_MAX_ITER"] = max_iter

    console.print()
    _print_summary(config_values)

    if not Confirm.ask("  [bold]保存以上配置？[/bold]", default=True):
        console.print("\n  [yellow]已取消，配置未保存。[/yellow]\n")
        return 1

    path = save_global_config(config_values)

    # ── 连接测试 ──
    console.print()
    if api_key and api_key != "ollama" and base_url:
        if Confirm.ask("  [bold]测试连接？[/bold]", default=True):
            ok, msg = _test_connection(api_key, base_url, model)
            if ok:
                console.print(f"  [green]✓ {msg}[/green]")
            else:
                console.print(f"  [red]✗ {msg}[/red]")
                console.print("  [dim]配置已保存，你可以稍后修改。[/dim]")

    # ── 完成 ──
    console.print()
    console.print(Panel(
        f"[green bold]✓ 配置完成！[/green bold]\n\n"
        f"配置保存至: [cyan]{path}[/cyan]\n\n"
        f"[bold]下一步:[/bold]\n"
        f"  sysdialogue              启动 TUI 终端界面\n"
        f"  sysdialogue-web          启动 Web 控制台\n"
        f"  sysdialogue config       查看当前配置\n"
        f"  sysdialogue --setup      重新配置",
        border_style="green",
        padding=(1, 2),
    ))
    console.print()
    return 0


# ─────────────────────────────────────────────────────────
# 内部辅助
# ─────────────────────────────────────────────────────────
def _print_step(current: int, total: int, title: str) -> None:
    """打印步骤指示器。"""
    bar = ""
    for i in range(1, total + 1):
        if i < current:
            bar += "[green]━━[/green]"
        elif i == current:
            bar += "[bold cyan]━━[/bold cyan]"
        else:
            bar += "[dim]──[/dim]"
    console.print(f"  [bold]步骤 {current}/{total}[/bold]  {bar}  [bold white]{title}[/bold white]")
    console.print()


def _print_summary(config: dict[str, str]) -> None:
    """打印配置汇总表格。"""
    table = Table(show_header=False, border_style="cyan", padding=(0, 1))
    table.add_column("配置项", style="bold", width=24)
    table.add_column("值")

    for key, value in config.items():
        if key == "OPENAI_API_KEY":
            display = _mask_key(value)
            table.add_row(key, display)
        else:
            table.add_row(key, value or "[dim]默认[/dim]")

    console.print(table)


def _mask_key(key: str) -> str:
    """遮掩 API Key 中间部分。"""
    if not key:
        return ""
    if len(key) <= 8:
        return key[:2] + "••••"
    return key[:4] + "••••" + key[-4:]


def _detect_provider(url: str) -> str:
    """根据 URL 自动检测服务商。"""
    for preset in _PRESETS.values():
        if preset["url"] and url and preset["url"].split("//")[0] in url:
            return preset["label"]
    return ""


def _test_connection(api_key: str, base_url: str, model: str) -> tuple[bool, str]:
    """测试 API 连接。返回 (成功, 消息)。"""
    try:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
            transient=True,
        ) as progress:
            task = progress.add_task("正在测试连接...", total=None)

            import urllib.request
            import urllib.error

            url = base_url.rstrip("/") + "/models"
            req = urllib.request.Request(url, method="GET")
            req.add_header("Authorization", f"Bearer {api_key}")

            progress.update(task, description="正在调用 /models 接口...")

            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read().decode())
                    models = data.get("data", [])
                    count = len(models)
                    # 检查目标模型是否存在
                    model_ids = {m.get("id", "") for m in models}
                    if model and model in model_ids:
                        return True, f"连接成功，模型 {model} 可用 (共 {count} 个模型)"
                    elif model:
                        # 模型不在列表中但连接成功了
                        return True, f"连接成功 (共 {count} 个模型)，但 {model} 不在列表中，请确认模型名"
                    return True, f"连接成功，共 {count} 个可用模型"
            except urllib.error.HTTPError as e:
                if e.code == 401:
                    return False, "API Key 无效或已过期"
                if e.code == 403:
                    return False, "权限不足，请检查 API Key 权限"
                if e.code == 404:
                    return False, f"端点不存在: {url}"
                return False, f"HTTP {e.code}: {e.reason}"
            except urllib.error.URLError as e:
                return False, f"无法连接: {e.reason}"
            except Exception as e:
                return False, f"连接失败: {e}"
    except Exception:
        return False, "测试组件加载失败"
