from __future__ import annotations

from pathlib import Path


def test_web_template_understands_durable_task_statuses() -> None:
    template = Path("sysdialogue/web/templates/index.html").read_text(encoding="utf-8")

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
        assert status in template

    assert 'api("/resume", "POST"' in template
    assert 'api("/command", "POST"' in template
    assert "memory-summary" in template
    assert "policy-summary" in template
    assert "trace-summary" in template
    assert "skill-summary" in template
    assert "hook-summary" in template
    assert "!response.ok" in template


def test_web_template_has_readable_chinese_labels() -> None:
    template = Path("sysdialogue/web/templates/index.html").read_text(encoding="utf-8")

    for label in ["新建会话", "构建任何运维任务", "待确认", "待输入", "继续上次任务"]:
        assert label in template

    for mojibake in ["鏂板缓", "杩愮淮", "寰呯‘璁", "鍙栨秷鎵ц"]:
        assert mojibake not in template
