from __future__ import annotations

from click.testing import CliRunner

from sysdialogue.agent.command_registry import CommandRegistry
from sysdialogue.agent.evidence_matrix import (
    EVIDENCE_MATRIX,
    PRODUCT_BAR_REQUIREMENTS,
    VERIFICATION_GATES,
    check_evidence_references,
    coverage_gaps,
    render_evidence_matrix,
)
from sysdialogue.app.cli import main
from sysdialogue.app.config import AppConfig
from sysdialogue.app.verify import run_verify


def test_evidence_matrix_covers_product_bar_and_verification_gates() -> None:
    text = render_evidence_matrix()
    item_ids = {item.item_id for item in EVIDENCE_MATRIX}

    assert {item_id for item_id, _ in PRODUCT_BAR_REQUIREMENTS} <= item_ids
    assert {item_id for item_id, _ in VERIFICATION_GATES} <= item_ids
    assert coverage_gaps() == []
    assert check_evidence_references() == []
    assert "Product Bar:" in text
    assert "Verification Gate:" in text
    assert "Startup:" in text
    assert "Mutation safety:" in text
    assert "tests/test_golden_scenarios.py" in text


def test_slash_evidence_command_lists_matrix_and_alias() -> None:
    registry = CommandRegistry()

    reply = registry.execute(object(), "/evidence").output
    alias_reply = registry.execute(object(), "/verification").output
    help_reply = registry.execute(object(), "/help").output

    assert "Completion evidence matrix:" in reply
    assert "PB01" in reply
    assert "VG07" in reply
    assert "Suggested smoke commands:" in reply
    assert alias_reply == reply
    assert "/evidence" in help_reply


def test_cli_evidence_runs_without_api_config(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(main, ["--evidence"], env={})

    assert result.exit_code == 0
    assert "Completion evidence matrix:" in result.output
    assert "OPENAI_API_KEY" not in result.output


def test_verify_reports_evidence_matrix_check(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.chdir(tmp_path)

    code = run_verify(AppConfig(api_key="", model=""))
    captured = capsys.readouterr()

    assert code == 0
    assert "[5/6] Evidence matrix:" in captured.out
    assert "coverage gaps: none" in captured.out
    assert "missing references: none" in captured.out
