from __future__ import annotations

from pathlib import Path


def _web_sources() -> tuple[str, str, str]:
    template = Path("sysdialogue/web/templates/index.html").read_text(encoding="utf-8")
    script = Path("sysdialogue/web/static/web.js").read_text(encoding="utf-8")
    style = Path("sysdialogue/web/static/web.css").read_text(encoding="utf-8")
    return template, script, style


def test_web_template_understands_durable_task_statuses() -> None:
    _, script, _ = _web_sources()

    for status in [
        "running",
        "waiting_confirm",
        "waiting_input",
        "interrupted",
        "failed",
        "completed",
        "blocked",
        "cancelled",
    ]:
        assert status in script

    assert 'api("/resume", "POST"' in script
    assert 'api("/command", "POST"' in script
    assert 'api("/target", "POST"' in script
    assert 'api("/tasks"' in script
    assert 'api("/audit"' in script
    assert 'rootApi("/sessions"' in script
    assert 'rootApi("/targets/test", "POST"' in script
    assert "!response.ok" in script


def test_web_template_has_readable_chinese_labels() -> None:
    template, script, style = _web_sources()
    combined = "\n".join([template, script, style])

    for label in ["新建会话", "任务对话", "待确认", "待输入", "恢复任务", "目标机器", "password"]:
        assert label in combined

    for mojibake in ["閺傛澘缂?", "鏉╂劗娣?", "瀵板懐鈥樼拋", "閸欐牗绉烽幍褑", "褰撳墠", "鐩爣"]:
        assert mojibake not in combined


def test_web_template_uses_three_column_console_and_static_assets() -> None:
    template, _, style = _web_sources()

    assert '<link rel="stylesheet" href="/static/web.css">' in template
    assert '<script src="/static/web.js"></script>' in template
    assert "console-shell" in template
    assert "sidebar" in template
    assert "workspace" in template
    assert "inspector" in template
    assert "grid-template-columns: 320px minmax(480px, 1fr) 380px" in style
