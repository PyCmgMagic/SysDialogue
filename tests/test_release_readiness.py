from __future__ import annotations

import json
import zipfile
from pathlib import Path

from click.testing import CliRunner

from sysdialogue.agent.acceptance_bundle import build_acceptance_bundle_from_path
from sysdialogue.agent.command_registry import CommandRegistry
from sysdialogue.agent.release_readiness import (
    analyze_release_readiness,
    evaluate_release_gate,
    release_readiness_to_dict,
    render_release_gate_report,
    render_release_readiness_report,
)
from sysdialogue.app.cli import main


def _passing_acceptance_markdown() -> str:
    lines = ["Operator acceptance checklist:", "Target: ssh://stage.example.test:22", "Runbook:"]
    for index in range(1, 11):
        step_id = f"A{index:02d}"
        if step_id == "A02":
            evidence = "Diagnostic returned a model tool call for the release model."
        elif step_id == "A03":
            evidence = "Doctor passed for ssh target with token=sk-secret1234 redacted."
        elif step_id == "A04":
            evidence = "A04 command-surface review collected from /help, TUI welcome, and Web Release controls."
        elif step_id == "A05":
            evidence = "A05 non-invasive conversation check collected command_trace_count=0."
        elif step_id == "A07":
            evidence = "Operator-approved A07 mutation drill completed with approval and post-change verification."
        elif step_id == "A08":
            evidence = "/next recommended recovery and /resume completed without repeating side effects."
        else:
            evidence = f"{step_id} completed"
        lines.append(f"- [x] {step_id} acceptance item")
        lines.append(f"  Evidence: {evidence}")
    return "\n".join(lines)


def test_release_readiness_report_summarizes_completed_artifacts(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "acceptance.md").write_text(_passing_acceptance_markdown(), encoding="utf-8")
    with zipfile.ZipFile(artifacts / "replay.zip", "w") as archive:
        archive.writestr("SUMMARY.md", "# Replay summary\n")
        archive.writestr("audit.jsonl", json.dumps({"type": "final", "final_status": "completed"}))

    report = render_release_readiness_report(artifacts)
    readiness = analyze_release_readiness(artifacts)

    assert readiness.overall == "pass"
    assert evaluate_release_gate(readiness).passed is True
    assert release_readiness_to_dict(readiness)["releaseGate"]["exitCode"] == 0
    assert "Overall: pass" in report
    assert "Release gate: pass" in report
    assert "pass=10" in report
    assert "replay ZIP contains SUMMARY.md" in report
    assert "Acceptance target is still a placeholder" not in report
    assert "token=<redacted>" in report
    assert "sk-secret1234" not in report


def test_release_readiness_report_marks_missing_and_failed_checks(tmp_path: Path) -> None:
    payload = {
        "checks": [
            {"id": "A01", "status": "pass", "evidence": "verify passed"},
            {"id": "A02", "status": "failed", "evidence": "model rejected tool call"},
            {"id": "A07", "status": "partial", "evidence": "approval shown, mutation skipped"},
        ]
    }
    path = tmp_path / "readiness.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    report = render_release_readiness_report(path)

    assert "Overall: fail" in report
    assert "A02 [fail]" in report
    assert "A07 [partial]" in report
    assert "missing=7" in report
    assert "No replay/audit artifact detected" in report
    assert "Release gate: blocked" in report


def test_release_readiness_keeps_weak_all_pass_artifact_partial(tmp_path: Path) -> None:
    artifacts = tmp_path / "weak"
    artifacts.mkdir()
    lines = ["Operator acceptance checklist:", "Target: local-or-placeholder", "Runbook:"]
    for index in range(1, 11):
        step_id = f"A{index:02d}"
        lines.append(f"- [x] {step_id} acceptance item")
        lines.append(f"  Evidence: {step_id} completed")
    (artifacts / "acceptance.md").write_text("\n".join(lines), encoding="utf-8")
    with zipfile.ZipFile(artifacts / "attachment.zip", "w") as archive:
        archive.writestr("notes.txt", "not a replay")

    readiness = analyze_release_readiness(artifacts)
    report = render_release_readiness_report(artifacts)

    assert readiness.overall == "partial"
    assert evaluate_release_gate(readiness).passed is False
    assert release_readiness_to_dict(readiness)["releaseGate"]["passed"] is False
    next_actions = release_readiness_to_dict(readiness)["releaseGate"]["nextActions"]
    assert any(item.kind == "zip-manifest" for item in readiness.artifacts)
    assert "ZIP artifact present but no replay" in report
    assert "Acceptance target is still a placeholder" in report
    assert "A02 is marked pass but does not include model tool-call" in report
    assert "A04 is marked pass but does not include slash, TUI, and Web command-surface" in report
    assert "A05 is marked pass but does not include no-command-trace conversation" in report
    assert "A07 is marked pass but does not include operator-approved mutation drill" in report
    assert "A08 is marked pass but does not show /next plus /resume or /abandon" in report
    assert "Next actions:" in report
    assert any("--check-model" in action for action in next_actions)
    assert any("ui-review" in action for action in next_actions)
    assert any("conversation-check" in action for action in next_actions)
    assert any("operator-approved-drill" in action for action in next_actions)
    assert any("recovery-drill" in action for action in next_actions)
    assert any("/export-replay" in action for action in next_actions)


def test_release_readiness_does_not_treat_acceptance_bundle_jsonl_as_replay(tmp_path: Path) -> None:
    artifacts = tmp_path / "bundle-only"
    artifacts.mkdir()
    with zipfile.ZipFile(artifacts / "acceptance-bundle.zip", "w") as archive:
        archive.writestr("release-readiness.json", "{}")
        archive.writestr("acceptance-checks.jsonl", json.dumps({"stepId": "A01", "status": "pass"}))

    readiness = analyze_release_readiness(artifacts)

    kinds = {item.kind for item in readiness.artifacts}
    assert "acceptance-bundle" in kinds
    assert "replay-zip" not in kinds
    assert "audit-jsonl" not in kinds
    assert readiness.overall == "partial"


def test_release_gate_can_recompute_from_acceptance_bundle_zip(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "acceptance.md").write_text(_passing_acceptance_markdown(), encoding="utf-8")
    with zipfile.ZipFile(artifacts / "replay.zip", "w") as archive:
        archive.writestr("SUMMARY.md", "# Replay summary\n")
        archive.writestr("audit.jsonl", json.dumps({"type": "final", "final_status": "completed"}))

    bundle = build_acceptance_bundle_from_path(artifacts)
    bundle_path = tmp_path / "sysdialogue-acceptance-bundle.zip"
    bundle_path.write_bytes(bundle.content)

    readiness = analyze_release_readiness(bundle_path)
    result = CliRunner().invoke(main, ["--release-gate", str(bundle_path)], env={})

    kinds = {item.kind for item in readiness.artifacts}
    assert readiness.overall == "pass"
    assert evaluate_release_gate(readiness).passed is True
    assert "acceptance-bundle" in kinds
    assert "replay-zip" in kinds
    assert result.exit_code == 0
    assert "Release gate: pass" in result.output


def test_slash_release_readiness_command_and_alias(tmp_path: Path) -> None:
    path = tmp_path / "acceptance.md"
    path.write_text(_passing_acceptance_markdown(), encoding="utf-8")
    registry = CommandRegistry()

    reply = registry.execute(object(), f"/release-readiness {path}").output
    alias_reply = registry.execute(object(), f"/readiness {path}").output
    help_reply = registry.execute(object(), "/help").output

    assert "Release readiness report:" in reply
    assert "Overall: partial" in reply
    assert "No replay/audit artifact detected" in reply
    assert alias_reply == reply
    assert "/release-readiness" in help_reply


def test_slash_release_gate_command_and_alias(tmp_path: Path) -> None:
    path = tmp_path / "acceptance.md"
    path.write_text("Operator acceptance checklist:\n- [x] A01 startup\n", encoding="utf-8")
    registry = CommandRegistry()

    reply = registry.execute(object(), f"/release-gate {path}").output
    alias_reply = registry.execute(object(), f"/gate {path}").output
    help_reply = registry.execute(object(), "/help").output

    assert "Release gate: blocked" in reply
    assert "Blocking reasons:" in reply
    assert alias_reply == reply
    assert "/release-gate" in help_reply


def test_cli_release_readiness_runs_without_api_config(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "acceptance.md"
    path.write_text(_passing_acceptance_markdown(), encoding="utf-8")

    result = CliRunner().invoke(main, ["--release-readiness", str(path)], env={})

    assert result.exit_code == 0
    assert "Release readiness report:" in result.output
    assert "OPENAI_API_KEY" not in result.output


def test_release_gate_report_returns_nonzero_for_partial(tmp_path: Path) -> None:
    path = tmp_path / "acceptance.md"
    path.write_text("Operator acceptance checklist:\n- [x] A01 startup\n", encoding="utf-8")

    report, exit_code = render_release_gate_report(path)

    assert exit_code == 1
    assert "Release gate: blocked" in report
    assert "Overall readiness is partial" in report
    assert "Next actions:" in report
    assert "Complete A02, A03, A04, A05, A06, and 4 more check(s)" in report


def test_cli_release_gate_exits_nonzero_until_ready(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "acceptance.md"
    path.write_text("Operator acceptance checklist:\n- [x] A01 startup\n", encoding="utf-8")

    result = CliRunner().invoke(main, ["--release-gate", str(path)], env={})

    assert result.exit_code == 1
    assert "Release gate: blocked" in result.output
    assert "A02 is missing" in result.output
    assert "Re-run `sysdialogue --release-gate <artifact-dir-or-bundle.zip>`" in result.output


def test_cli_release_gate_exits_zero_for_strong_pass(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "acceptance.md").write_text(_passing_acceptance_markdown(), encoding="utf-8")
    with zipfile.ZipFile(artifacts / "replay.zip", "w") as archive:
        archive.writestr("SUMMARY.md", "# Replay summary\n")
        archive.writestr("session.jsonl", '{"type":"final","final_status":"completed"}\n')

    result = CliRunner().invoke(main, ["--release-gate", str(artifacts)], env={})

    assert result.exit_code == 0
    assert "Release gate: pass" in result.output
