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
    assert 'api("/api-config", "POST"' in script
    assert 'api("/target", "POST"' in script
    assert 'api("/tasks"' in script
    assert 'api("/audit"' in script
    assert 'rootApi("/sessions"' in script
    assert 'rootApi("/targets/test", "POST"' in script
    assert 'rootApi("/targets", "POST"' in script
    assert 'rootApi(`/targets/${encodeURIComponent(profile.target_id)}`, "DELETE")' in script
    assert "!response.ok" in script


def test_web_template_has_readable_chinese_labels() -> None:
    template, script, style = _web_sources()
    combined = "\n".join([template, script, style])

    for label in ["新建会话", "任务对话", "待确认", "待输入", "恢复任务", "目标机器", "password"]:
        assert label in combined

    for mojibake in ["閺傛澘缂?", "鏉╂劗娣?", "瀵板懐鈥樼拋", "閸欐牗绉烽幍褑", "褰撳墠", "鐩爣"]:
        assert mojibake not in combined


def test_web_template_uses_three_column_console_with_settings_modal() -> None:
    template, script, style = _web_sources()

    assert '<link rel="stylesheet" href="/static/web.css">' in template
    assert '<script src="/static/web.js"></script>' in template
    assert "console-shell" in template
    assert "settings-modal" in template
    assert "settings-panel" in template
    assert "btn-open-settings" in template
    assert "btn-close-settings" in template
    assert "sidebar" in template
    assert "workspace" in template
    assert "inspector" in template
    assert "saved-targets" in template
    assert "api-config" in template
    assert "btn-api-apply" in template
    assert "api-key-pill" in template
    assert 'id="api-key" type="text"' in template
    assert "btn-target-save" in template
    assert "btn-target-delete" in template
    assert template.index("session-list") < template.index("settings-modal")
    assert template.index("settings-modal") < template.index("target-panel")
    assert "grid-template-columns: 320px minmax(480px, 1fr) 380px" in style
    assert "function openSettings()" in script
    assert "function closeSettings()" in script
    assert "function applyApiConfig()" in script
    assert "function groupSessionsByTarget" in script
    assert "function groupEventsByTask" in script
    assert "event.task_id || event.data?.task_id" in script
    assert "compact-details" in script
    assert "collapsedSessionGroups" in script
    assert "data-session-group" in script
    assert "session-group-title" in style
    assert ".session-group.collapsed .session-group-items" in style
    assert ".compact-details summary" in style
    assert "task-detail-grid" in style
    assert "transform: translate(-50%, -50%)" in style
    assert "flex: 1" in style
