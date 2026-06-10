from __future__ import annotations

import json
import zipfile
from types import SimpleNamespace

from click.testing import CliRunner

from sysdialogue.agent.controller import AgentController, LLMResponse
from sysdialogue.agent.acceptance_runner import (
    guided_acceptance_to_dict,
    render_guided_acceptance,
    run_guided_acceptance,
)
from sysdialogue.app.acceptance_collection import (
    collect_conversation_acceptance_evidence,
    collect_recovery_acceptance_evidence,
    collect_replay_acceptance_evidence,
    collect_ui_acceptance_evidence,
)
from sysdialogue.app import cli as cli_module
from sysdialogue.agent.command_registry import CommandRegistry
from sysdialogue.agent.release_readiness import analyze_release_readiness, analyze_release_readiness_text
from sysdialogue.agent.state_store import LockStore, SessionStore, TaskStore
from sysdialogue.agent.trace_store import TraceStore
from sysdialogue.app.cli import main
from sysdialogue.audit.trace_store import AuditLog
from sysdialogue.runtime.secure_runner import LocalExecutor
from sysdialogue.tools.registry import ToolRegistry


def test_guided_acceptance_runner_marks_safe_preflight_and_manual_gates() -> None:
    run = run_guided_acceptance(
        {
            "remote_mode": True,
            "ssh_user": "alice",
            "host": "example.test",
            "ssh_port": 2222,
        }
    )
    payload = guided_acceptance_to_dict(run)

    assert payload["target"] == "ssh://example.test:2222"
    assert payload["mode"] == "safe-preflight"
    statuses = {step["stepId"]: step["status"] for step in payload["steps"]}
    assert statuses["A01"] == "pass"
    assert statuses["A10"] == "pass"
    assert statuses["A07"] == "partial"
    assert {step["mode"] for step in payload["steps"]} >= {"auto-local", "manual", "operator-approved"}


def test_guided_acceptance_artifact_feeds_release_readiness() -> None:
    artifact = render_guided_acceptance({"remote_mode": False})
    readiness = analyze_release_readiness_text(artifact, source="runner")

    assert "Guided acceptance runner artifact:" in artifact
    assert readiness.overall == "partial"
    assert sum(1 for check in readiness.checks if check.status == "pass") >= 2
    assert any(check.step_id == "A07" and check.status == "partial" for check in readiness.checks)


def test_guided_acceptance_read_only_collect_marks_collected_gates() -> None:
    artifact = render_guided_acceptance(
        {"remote_mode": True, "ssh_user": "alice", "host": "example.test", "ssh_port": 2222},
        mode="read-only-collect",
        collected={
            "A03": {"status": "pass", "evidence": "Doctor collected for ssh target."},
            "A06": {"status": "pass", "evidence": "security_audit final_status=completed"},
        },
    )
    readiness = analyze_release_readiness_text(artifact, source="runner")

    assert "Mode: read-only-collect" in artifact
    assert "Runner mode: auto-read-only" in artifact
    statuses = {check.step_id: check.status for check in readiness.checks}
    assert statuses["A03"] == "pass"
    assert statuses["A06"] == "pass"


def test_guided_acceptance_model_check_marks_a02_collected() -> None:
    artifact = render_guided_acceptance(
        {"remote_mode": False},
        mode="model-check",
        collected={
            "A02": {
                "status": "pass",
                "evidence": "Model tool-call diagnostic collected: status ok; returned diagnostic tool call.",
            }
        },
    )
    readiness = analyze_release_readiness_text(artifact, source="runner")

    assert "Mode: model-check" in artifact
    assert "Runner mode: auto-model" in artifact
    statuses = {check.step_id: check.status for check in readiness.checks}
    assert statuses["A02"] == "pass"


def test_recovery_acceptance_collection_exercises_next_and_abandon(tmp_path) -> None:
    controller = AgentController(
        executor=LocalExecutor(),
        env_profile={"remote_mode": False},
        audit_log=AuditLog(log_dir=str(tmp_path / "audit")),
        registry=ToolRegistry(),
        llm_client=SimpleNamespace(messages_create=lambda **kwargs: None),
        session_store=SessionStore(str(tmp_path / "sessions")),
        task_store=TaskStore(str(tmp_path / "tasks")),
        lock_store=LockStore(str(tmp_path / "locks")),
        command_registry=CommandRegistry(),
    )

    collected = collect_recovery_acceptance_evidence(controller)
    artifact = render_guided_acceptance({"remote_mode": False}, mode="recovery-drill", collected=collected)
    readiness = analyze_release_readiness_text(artifact, source="runner")

    assert collected["A08"]["status"] == "pass"
    assert "/next" in collected["A08"]["evidence"]
    assert "/abandon" in collected["A08"]["evidence"]
    statuses = {check.step_id: check.status for check in readiness.checks}
    assert statuses["A08"] == "pass"


def test_conversation_acceptance_collection_runs_without_command_trace(tmp_path) -> None:
    class ConversationLLM:
        def __init__(self):
            self.calls = []

        def messages_create(self, *, system, messages, tools):
            self.calls.append({"system": system, "messages": messages, "tools": tools})
            return LLMResponse(
                content=[
                    {
                        "type": "tool_use",
                        "id": "finish_chat",
                        "name": "finish_task",
                        "input": {
                            "status": "completed",
                            "summary": "Ready to help.",
                            "no_action_reason": "Plain conversation does not require system access.",
                        },
                    }
                ],
                stop_reason="tool_use",
            )

    llm = ConversationLLM()
    controller = AgentController(
        executor=LocalExecutor(),
        env_profile={"remote_mode": False},
        audit_log=AuditLog(log_dir=str(tmp_path / "audit")),
        registry=ToolRegistry(),
        llm_client=llm,
        session_store=SessionStore(str(tmp_path / "sessions")),
        task_store=TaskStore(str(tmp_path / "tasks")),
        lock_store=LockStore(str(tmp_path / "locks")),
        trace_store=TraceStore(str(tmp_path / "traces")),
        command_registry=CommandRegistry(),
    )

    collected = collect_conversation_acceptance_evidence(controller)
    artifact = render_guided_acceptance({"remote_mode": False}, mode="conversation-check", collected=collected)
    readiness = analyze_release_readiness_text(artifact, source="runner")

    assert collected["A05"]["status"] == "pass"
    assert "command_trace_count': 0" in collected["A05"]["evidence"]
    assert [tool["name"] for tool in llm.calls[0]["tools"]] == ["finish_task"]
    assert not [record for record in controller.audit_log.read_all() if record.get("type") == "command_trace"]
    statuses = {check.step_id: check.status for check in readiness.checks}
    assert statuses["A05"] == "pass"


def test_replay_acceptance_collection_exports_real_replay_zip(tmp_path) -> None:
    artifacts = tmp_path / "release"
    artifacts.mkdir()
    audit = AuditLog(session_id="acceptance_replay", log_dir=str(tmp_path / "audit"))
    audit.log_env_profile({"remote_mode": True, "host": "stage.example.test"})
    audit.log_final(final_status="completed", detail="A09 acceptance replay drill completed.")

    collected = collect_replay_acceptance_evidence(audit, export_dir=artifacts)
    artifact = render_guided_acceptance(
        {"remote_mode": True, "ssh_user": "alice", "host": "stage.example.test", "ssh_port": 22},
        mode="replay-export",
        collected=collected,
    )
    (artifacts / "acceptance-replay.md").write_text(artifact, encoding="utf-8")
    readiness = analyze_release_readiness(artifacts)

    assert collected["A09"]["status"] == "pass"
    assert "SUMMARY.md" in collected["A09"]["evidence"]
    assert "session.jsonl" in collected["A09"]["evidence"]
    replay_paths = list(artifacts.glob("replay_acceptance_replay_*.zip"))
    assert len(replay_paths) == 1
    with zipfile.ZipFile(replay_paths[0]) as archive:
        assert {"SUMMARY.md", "session.jsonl", "summary.json"} <= set(archive.namelist())
    statuses = {check.step_id: check.status for check in readiness.checks}
    kinds = {artifact.kind for artifact in readiness.artifacts}
    assert statuses["A09"] == "pass"
    assert "replay-zip" in kinds


def test_ui_acceptance_collection_checks_operator_surfaces() -> None:
    collected = collect_ui_acceptance_evidence()
    artifact = render_guided_acceptance({"remote_mode": False}, mode="ui-review", collected=collected)
    readiness = analyze_release_readiness_text(artifact, source="runner")

    assert collected["A04"]["status"] == "pass"
    assert "/help" in collected["A04"]["evidence"]
    assert "Checklist" in collected["A04"]["evidence"]
    assert "Runner mode: auto-ui" in artifact
    statuses = {check.step_id: check.status for check in readiness.checks}
    assert statuses["A04"] == "pass"


def test_slash_acceptance_runner_command_uses_current_target() -> None:
    controller = SimpleNamespace(
        env_profile={
            "remote_mode": True,
            "current_user": "deploy",
            "host": "prod.example.test",
            "ssh_port": 2200,
        }
    )
    registry = CommandRegistry()

    reply = registry.execute(controller, "/acceptance-runner").output
    alias_reply = registry.execute(controller, "/acceptance-run").output
    help_reply = registry.execute(controller, "/help").output

    assert "Guided acceptance runner artifact:" in reply
    assert "ssh://prod.example.test:2200" in reply
    assert "A07 mutation safety gate passed" in reply
    assert alias_reply == reply
    assert "/acceptance-runner" in help_reply


def test_cli_acceptance_runner_runs_without_api_config(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(
        main,
        ["--acceptance-runner", "--remote", "alice@example.test:2222"],
        env={},
    )

    assert result.exit_code == 0
    assert "Guided acceptance runner artifact:" in result.output
    assert "ssh://example.test:2222" in result.output
    assert "OPENAI_API_KEY" not in result.output


def test_cli_acceptance_runner_read_only_collect_is_opt_in(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)

    class FakeRuntime:
        controller = object()

        def close(self) -> None:
            pass

    monkeypatch.setattr(cli_module, "create_runtime", lambda *args, **kwargs: FakeRuntime())
    monkeypatch.setattr(
        cli_module,
        "collect_read_only_acceptance_evidence",
        lambda *args, **kwargs: {
            "A03": {"status": "pass", "evidence": "Doctor collected."},
            "A06": {"status": "pass", "evidence": "security_audit completed."},
        },
    )

    result = CliRunner().invoke(
        main,
        ["--acceptance-runner", "--acceptance-runner-mode", "read-only-collect"],
        env={},
    )

    assert result.exit_code == 0
    assert "Mode: read-only-collect" in result.output
    assert "Doctor collected." in result.output
    assert "security_audit completed." in result.output


def test_cli_acceptance_runner_model_check_collects_a02(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)

    class FakeOpenAIChatClient:
        def __init__(self, *, api_key, base_url, model):
            self.api_key = api_key
            self.base_url = base_url or ""
            self.model = model

        def messages_create(self, *, system, messages, tools):
            return SimpleNamespace(
                content=[
                    {
                        "type": "tool_use",
                        "id": "call_diag",
                        "name": "diagnostic_ping",
                        "input": {"ok": True},
                    }
                ],
                stop_reason="tool_use",
            )

    monkeypatch.setattr(cli_module, "OpenAIChatClient", FakeOpenAIChatClient)

    result = CliRunner().invoke(
        main,
        ["--acceptance-runner", "--acceptance-runner-mode", "model-check", "--model", "release-model"],
        env={"OPENAI_API_KEY": "key", "OPENAI_BASE_URL": "https://api.example.test/v1"},
    )

    assert result.exit_code == 0
    assert "Mode: model-check" in result.output
    assert "Runner mode: auto-model" in result.output
    assert "Model tool-call diagnostic collected" in result.output
    assert "A02 model tool-call diagnostic passed" in result.output


def test_cli_acceptance_runner_conversation_check_collects_a05(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)

    class FakeRuntime:
        controller = SimpleNamespace()

        def close(self) -> None:
            pass

    monkeypatch.setattr(cli_module, "create_runtime", lambda *args, **kwargs: FakeRuntime())
    monkeypatch.setattr(
        cli_module,
        "collect_conversation_acceptance_evidence",
        lambda controller: {
            "A05": {
                "status": "pass",
                "evidence": "A05 conversation check produced no command_trace records.",
                "manual_action": "",
            },
        },
    )

    result = CliRunner().invoke(
        main,
        ["--acceptance-runner", "--acceptance-runner-mode", "conversation-check", "--model", "release-model"],
        env={"OPENAI_API_KEY": "key"},
    )

    assert result.exit_code == 0
    assert "Mode: conversation-check" in result.output
    assert "Runner mode: auto-conversation" in result.output
    assert "A05 conversation check produced no command_trace records" in result.output


def test_cli_acceptance_runner_ui_review_collects_a04(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)

    monkeypatch.setattr(
        cli_module,
        "collect_ui_acceptance_evidence",
        lambda: {
            "A04": {
                "status": "pass",
                "evidence": "A04 UI review found /help and Web Release controls.",
                "manual_action": "",
            },
        },
    )

    result = CliRunner().invoke(
        main,
        ["--acceptance-runner", "--acceptance-runner-mode", "ui-review"],
        env={},
    )

    assert result.exit_code == 0
    assert "Mode: ui-review" in result.output
    assert "Runner mode: auto-ui" in result.output
    assert "A04 UI review found /help and Web Release controls" in result.output


def test_cli_acceptance_runner_recovery_drill_collects_a08(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)

    class FakeRuntime:
        controller = SimpleNamespace()

        def close(self) -> None:
            pass

    monkeypatch.setattr(cli_module, "create_runtime", lambda *args, **kwargs: FakeRuntime())
    monkeypatch.setattr(
        cli_module,
        "collect_recovery_acceptance_evidence",
        lambda controller: {
            "A08": {"status": "pass", "evidence": "A08 recovery drill ran /next and /abandon.", "manual_action": ""},
        },
    )

    result = CliRunner().invoke(
        main,
        ["--acceptance-runner", "--acceptance-runner-mode", "recovery-drill"],
        env={},
    )

    assert result.exit_code == 0
    assert "Mode: recovery-drill" in result.output
    assert "A08 recovery drill ran /next and /abandon" in result.output


def test_cli_acceptance_runner_replay_export_requires_session(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(
        main,
        ["--acceptance-runner", "--acceptance-runner-mode", "replay-export"],
        env={},
    )

    assert result.exit_code != 0
    assert "--acceptance-replay-session is required" in result.output


def test_cli_acceptance_runner_replay_export_collects_a09(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    audit = AuditLog(session_id="cli_replay_acceptance")
    audit.log_env_profile({"remote_mode": False})
    audit.log_final(final_status="completed", detail="CLI A09 replay export completed.")
    export_dir = tmp_path / "release-evidence"

    result = CliRunner().invoke(
        main,
        [
            "--acceptance-runner",
            "--acceptance-runner-mode",
            "replay-export",
            "--acceptance-replay-session",
            "cli_replay_acceptance",
            "--export-dir",
            str(export_dir),
        ],
        env={"HOME": str(tmp_path), "USERPROFILE": str(tmp_path)},
    )

    assert result.exit_code == 0
    assert "Mode: replay-export" in result.output
    assert "Runner mode: auto-replay" in result.output
    assert "A09 replay export collected" in result.output
    assert list(export_dir.glob("replay_cli_replay_acceptance_*.zip"))


def test_cli_acceptance_runner_operator_drill_requires_plan(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(
        main,
        ["--acceptance-runner", "--acceptance-runner-mode", "operator-approved-drill"],
        env={},
    )

    assert result.exit_code != 0
    assert "--acceptance-drill-plan is required" in result.output


def test_cli_acceptance_runner_operator_drill_uses_collector(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    plan = tmp_path / "drill.json"
    plan.write_text(json.dumps({"workflow_name": "service_restart"}), encoding="utf-8")

    class FakeRuntime:
        controller = object()

        def close(self) -> None:
            pass

    monkeypatch.setattr(cli_module, "create_runtime", lambda *args, **kwargs: FakeRuntime())
    monkeypatch.setattr(
        cli_module,
        "collect_operator_approved_mutation_drill_evidence",
        lambda *args, **kwargs: {
            "A07": {"status": "pass", "evidence": "A07 drill completed."},
        },
    )

    result = CliRunner().invoke(
        main,
        ["--acceptance-runner", "--acceptance-runner-mode", "operator-approved-drill", "--acceptance-drill-plan", str(plan)],
        env={},
    )

    assert result.exit_code == 0
    assert "Mode: operator-approved-drill" in result.output
    assert "A07 drill completed." in result.output


def test_cli_acceptance_suite_writes_local_evidence_kit(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)

    class FakeRuntime:
        controller = SimpleNamespace()

        def close(self) -> None:
            pass

    monkeypatch.setattr(cli_module, "create_runtime", lambda *args, **kwargs: FakeRuntime())
    monkeypatch.setattr(
        cli_module,
        "collect_ui_acceptance_evidence",
        lambda: {
            "A04": {
                "status": "pass",
                "evidence": "A04 suite UI review passed.",
                "manual_action": "",
            },
        },
    )
    monkeypatch.setattr(
        cli_module,
        "collect_recovery_acceptance_evidence",
        lambda controller: {
            "A08": {"status": "pass", "evidence": "A08 suite recovery ran /next and /abandon.", "manual_action": ""},
        },
    )
    out = tmp_path / "release-evidence"

    result = CliRunner().invoke(
        main,
        ["--acceptance-suite", str(out), "--remote", "alice@stage.example.test:2222"],
        env={},
    )

    assert result.exit_code == 0
    assert "Acceptance suite written" in result.output
    assert (out / "acceptance-safe-preflight.md").exists()
    assert (out / "acceptance-ui.md").exists()
    assert (out / "acceptance-recovery.md").exists()
    assert (out / "README.md").exists()
    readiness = (out / "release-readiness.md").read_text(encoding="utf-8")
    assert "Release gate: blocked" in readiness
    assert "A04 suite UI review passed" in (out / "acceptance-ui.md").read_text(encoding="utf-8")
    assert "A08 suite recovery ran /next and /abandon" in (out / "acceptance-recovery.md").read_text(encoding="utf-8")
    assert "ssh://stage.example.test:2222" in (out / "README.md").read_text(encoding="utf-8")
