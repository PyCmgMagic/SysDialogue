from __future__ import annotations

import json
import base64
import zipfile
from io import BytesIO
from types import SimpleNamespace

from fastapi.testclient import TestClient

from sysdialogue.app import web_api
from tests.helpers import RecordingExecutor


def _client(monkeypatch) -> TestClient:
    monkeypatch.setattr(web_api, "manager", web_api.WebSessionManager())
    return TestClient(web_api.create_app())


def _passing_acceptance_text() -> str:
    lines = ["Operator acceptance checklist:"]
    for index in range(1, 11):
        step_id = f"A{index:02d}"
        evidence = "token=sk-secret1234" if step_id == "A03" else f"{step_id} checked"
        lines.append(f"- [x] {step_id} gate")
        lines.append(f"  Evidence: {evidence}")
    return "\n".join(lines)


def test_web_acceptance_route_returns_template_without_connected_session(monkeypatch) -> None:
    client = _client(monkeypatch)

    response = client.get("/api/release/acceptance")

    assert response.status_code == 200
    payload = response.json()
    assert payload["connected"] is False
    assert payload["target"] == "local-or-placeholder"
    assert "Operator acceptance checklist:" in payload["text"]
    assert "A10 evidence matrix attached" in payload["text"]


def test_web_acceptance_runner_route_returns_guided_artifact(monkeypatch) -> None:
    client = _client(monkeypatch)

    response = client.get("/api/release/acceptance-runner")

    assert response.status_code == 200
    payload = response.json()
    assert payload["connected"] is False
    assert payload["run"]["mode"] == "safe-preflight"
    assert "Guided acceptance runner artifact:" in payload["artifact"]
    assert payload["readiness"]["overall"] == "partial"
    statuses = {step["stepId"]: step["status"] for step in payload["run"]["steps"]}
    assert statuses["A01"] == "pass"
    assert statuses["A10"] == "pass"
    assert statuses["A07"] == "partial"


def test_web_acceptance_runner_read_only_collect_requires_connected_session(monkeypatch) -> None:
    client = _client(monkeypatch)

    response = client.get("/api/release/acceptance-runner?mode=read-only-collect")

    assert response.status_code == 404
    assert "connected session" in response.text


def test_web_acceptance_runner_model_check_requires_connected_session(monkeypatch) -> None:
    client = _client(monkeypatch)

    response = client.get("/api/release/acceptance-runner?mode=model-check")

    assert response.status_code == 404
    assert "model-check" in response.text


def test_web_acceptance_runner_conversation_check_requires_connected_session(monkeypatch) -> None:
    client = _client(monkeypatch)

    response = client.get("/api/release/acceptance-runner?mode=conversation-check")

    assert response.status_code == 404
    assert "conversation-check" in response.text


def test_web_acceptance_runner_ui_review_uses_collector_without_session(monkeypatch) -> None:
    client = _client(monkeypatch)
    monkeypatch.setattr(
        web_api,
        "collect_ui_acceptance_evidence",
        lambda controller=None: {
            "A04": {
                "status": "pass",
                "evidence": "A04 UI review found slash, TUI, and Web Release controls.",
                "manual_action": "",
            },
        },
    )

    response = client.get("/api/release/acceptance-runner?mode=ui-review")

    assert response.status_code == 200
    payload = response.json()
    assert payload["run"]["mode"] == "ui-review"
    statuses = {step["stepId"]: step["status"] for step in payload["run"]["steps"]}
    assert statuses["A04"] == "pass"
    assert "Runner mode: auto-ui" in payload["artifact"]
    assert "A04 UI review found slash" in payload["artifact"]


def test_web_acceptance_runner_recovery_drill_requires_connected_session(monkeypatch) -> None:
    client = _client(monkeypatch)

    response = client.get("/api/release/acceptance-runner?mode=recovery-drill")

    assert response.status_code == 404
    assert "recovery-drill" in response.text


def test_web_acceptance_runner_model_check_uses_session_llm(monkeypatch) -> None:
    client = _client(monkeypatch)

    class FakeDiagnosticClient:
        model = "release-model"
        base_url = "https://api.example.test/v1"

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

    web_api.manager._sessions["srv_1"] = SimpleNamespace(
        bundle=SimpleNamespace(
            env_profile={"remote_mode": False},
            controller=SimpleNamespace(llm_client=FakeDiagnosticClient()),
        ),
        connection=web_api.ServerConnectionIn(
            id="srv_1",
            mode="local",
            host="",
            port=22,
            user="",
        ),
        runtime_config=web_api.RuntimeConfigIn(model="release-model"),
    )
    web_api.manager._latest_server_id = "srv_1"

    response = client.get("/api/release/acceptance-runner?serverId=srv_1&mode=model-check")

    assert response.status_code == 200
    payload = response.json()
    assert payload["run"]["mode"] == "model-check"
    statuses = {step["stepId"]: step["status"] for step in payload["run"]["steps"]}
    assert statuses["A02"] == "pass"
    assert "Runner mode: auto-model" in payload["artifact"]
    assert "Model tool-call diagnostic collected" in payload["artifact"]


def test_web_acceptance_runner_conversation_check_uses_collector(monkeypatch) -> None:
    client = _client(monkeypatch)
    web_api.manager._sessions["srv_1"] = SimpleNamespace(
        bundle=SimpleNamespace(
            env_profile={"remote_mode": False},
            controller=object(),
        ),
        connection=web_api.ServerConnectionIn(
            id="srv_1",
            mode="local",
            host="",
            port=22,
            user="",
        ),
        runtime_config=web_api.RuntimeConfigIn(),
    )
    web_api.manager._latest_server_id = "srv_1"
    monkeypatch.setattr(
        web_api,
        "collect_conversation_acceptance_evidence",
        lambda controller: {
            "A05": {
                "status": "pass",
                "evidence": "A05 conversation check produced no command_trace records.",
                "manual_action": "",
            },
        },
    )

    response = client.get("/api/release/acceptance-runner?serverId=srv_1&mode=conversation-check")

    assert response.status_code == 200
    payload = response.json()
    assert payload["run"]["mode"] == "conversation-check"
    statuses = {step["stepId"]: step["status"] for step in payload["run"]["steps"]}
    assert statuses["A05"] == "pass"
    assert "A05 conversation check produced no command_trace records" in payload["artifact"]


def test_web_acceptance_runner_recovery_drill_uses_collector(monkeypatch) -> None:
    client = _client(monkeypatch)
    web_api.manager._sessions["srv_1"] = SimpleNamespace(
        bundle=SimpleNamespace(
            env_profile={"remote_mode": False},
            controller=object(),
        ),
        connection=web_api.ServerConnectionIn(
            id="srv_1",
            mode="local",
            host="",
            port=22,
            user="",
        ),
        runtime_config=web_api.RuntimeConfigIn(),
    )
    web_api.manager._latest_server_id = "srv_1"
    monkeypatch.setattr(
        web_api,
        "collect_recovery_acceptance_evidence",
        lambda controller: {
            "A08": {"status": "pass", "evidence": "A08 recovery drill ran /next and /abandon.", "manual_action": ""},
        },
    )

    response = client.get("/api/release/acceptance-runner?serverId=srv_1&mode=recovery-drill")

    assert response.status_code == 200
    payload = response.json()
    assert payload["run"]["mode"] == "recovery-drill"
    statuses = {step["stepId"]: step["status"] for step in payload["run"]["steps"]}
    assert statuses["A08"] == "pass"
    assert "A08 recovery drill ran /next and /abandon" in payload["artifact"]


def test_web_acceptance_runner_read_only_collect_uses_collector(monkeypatch) -> None:
    client = _client(monkeypatch)
    web_api.manager._sessions["srv_1"] = SimpleNamespace(
        bundle=SimpleNamespace(
            env_profile={
                "remote_mode": True,
                "host": "prod.example.test",
                "ssh_port": 2200,
            },
            controller=object(),
        ),
        connection=web_api.ServerConnectionIn(
            id="srv_1",
            mode="ssh",
            host="prod.example.test",
            port=2200,
            user="deploy",
        ),
        runtime_config=web_api.RuntimeConfigIn(),
    )
    web_api.manager._latest_server_id = "srv_1"
    monkeypatch.setattr(
        web_api,
        "collect_read_only_acceptance_evidence",
        lambda *args, **kwargs: {
            "A03": {"status": "pass", "evidence": "Doctor collected."},
            "A06": {"status": "pass", "evidence": "security_audit completed."},
        },
    )

    response = client.get("/api/release/acceptance-runner?serverId=srv_1&mode=read-only-collect")

    assert response.status_code == 200
    payload = response.json()
    assert payload["run"]["mode"] == "read-only-collect"
    statuses = {step["stepId"]: step["status"] for step in payload["run"]["steps"]}
    assert statuses["A03"] == "pass"
    assert statuses["A06"] == "pass"
    assert "Doctor collected." in payload["artifact"]


def test_web_acceptance_mutation_drill_requires_connected_session(monkeypatch) -> None:
    client = _client(monkeypatch)

    response = client.post("/api/release/mutation-drill", json={})

    assert response.status_code == 404
    assert "connected session" in response.text


def test_web_acceptance_mutation_drill_uses_operator_collector(monkeypatch) -> None:
    client = _client(monkeypatch)
    web_api.manager._sessions["srv_1"] = SimpleNamespace(
        bundle=SimpleNamespace(
            env_profile={
                "remote_mode": True,
                "host": "stage.example.test",
                "ssh_port": 2200,
            },
            controller=object(),
        ),
        connection=web_api.ServerConnectionIn(
            id="srv_1",
            mode="ssh",
            host="stage.example.test",
            port=2200,
            user="deploy",
        ),
        runtime_config=web_api.RuntimeConfigIn(),
    )
    web_api.manager._latest_server_id = "srv_1"
    monkeypatch.setattr(
        web_api,
        "collect_operator_approved_mutation_drill_evidence",
        lambda *args, **kwargs: {
            "A07": {"status": "pass", "evidence": "Operator-approved A07 mutation drill final_status=completed."},
        },
    )

    response = client.post(
        "/api/release/mutation-drill",
        json={
            "serverId": "srv_1",
            "workflowName": "service_restart",
            "args": {"service_name": "sysdialogue-a07-test"},
            "approvalPhrase": "I APPROVE A07 MUTATION DRILL",
            "impact": "Restart disposable test service only.",
            "rollback": "Start disposable test service if restart fails.",
            "verification": "Status check must run after restart.",
            "disposableTarget": True,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["run"]["mode"] == "operator-approved-drill"
    statuses = {step["stepId"]: step["status"] for step in payload["run"]["steps"]}
    assert statuses["A07"] == "pass"
    assert "Operator-approved A07 mutation drill" in payload["artifact"]


def test_web_release_readiness_route_summarizes_submitted_text_and_redacts(monkeypatch) -> None:
    client = _client(monkeypatch)

    response = client.post(
        "/api/release/readiness",
        json={
            "content": _passing_acceptance_text(),
            "source": "web note Authorization: Bearer very-secret-token",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    rendered = json.dumps(payload, ensure_ascii=False)
    assert payload["readiness"]["counts"]["pass"] == 10
    assert payload["readiness"]["overall"] == "partial"
    assert payload["readiness"]["releaseGate"]["passed"] is False
    assert payload["readiness"]["releaseGate"]["exitCode"] == 1
    assert payload["readiness"]["releaseGate"]["nextActions"]
    assert any("/export-replay" in action for action in payload["readiness"]["releaseGate"]["nextActions"])
    assert "No replay/audit artifact detected" in payload["report"]
    assert "Next actions:" in payload["report"]
    assert "Operator acceptance checklist:" not in payload["report"]
    assert "sk-secret1234" not in rendered
    assert "very-secret-token" not in rendered
    assert "<redacted>" in rendered


def test_web_acceptance_bundle_route_returns_sanitized_zip(monkeypatch) -> None:
    client = _client(monkeypatch)

    response = client.post(
        "/api/release/acceptance-bundle",
        json={
            "content": _passing_acceptance_text() + "\nReplay package: SUMMARY.md attached",
            "source": "web Authorization: Bearer secret-token",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["encoding"] == "base64"
    assert payload["fileName"].endswith(".zip")
    with zipfile.ZipFile(BytesIO(base64.b64decode(payload["content"]))) as archive:
        names = set(archive.namelist())
        rendered = "\n".join(archive.read(name).decode("utf-8") for name in sorted(names))
    assert "release-readiness.md" in names
    assert "acceptance-checks.jsonl" in names
    assert "sk-secret1234" not in rendered
    assert "secret-token" not in rendered
    assert "<redacted>" in rendered
    assert payload["readiness"]["counts"]["pass"] == 10


def test_web_acceptance_manager_uses_connected_session_target() -> None:
    manager = web_api.WebSessionManager()
    manager._sessions["srv_1"] = SimpleNamespace(
        bundle=SimpleNamespace(
            env_profile={
                "remote_mode": True,
                "host": "prod.example.test",
                "ssh_port": 2200,
                "ssh_proxy_command_configured": True,
            }
        ),
        connection=web_api.ServerConnectionIn(
            id="srv_1",
            mode="ssh",
            host="prod.example.test",
            port=2200,
            user="deploy",
        ),
    )
    manager._latest_server_id = "srv_1"

    payload = manager.acceptance_checklist("srv_1")

    assert payload["connected"] is True
    assert payload["target"] == "ssh://prod.example.test:2200 via ProxyCommand"
    assert "sysdialogue --doctor --remote deploy@prod.example.test:2200" in payload["text"]


def test_web_terminal_keeps_cwd_and_hides_success_exit_code() -> None:
    manager = web_api.WebSessionManager()
    executor = RecordingExecutor()
    executor_outputs = iter([
        ("__SYSDIALOGUE_CWD__/\n", 0),
        ("bin\netc\n__SYSDIALOGUE_CWD__/\n", 0),
        ("boom\n__SYSDIALOGUE_CWD__/\n", 2),
    ])

    executor.handler = lambda _cmd, _timeout: next(executor_outputs)
    manager._sessions["srv_1"] = SimpleNamespace(
        bundle=SimpleNamespace(
            executor=executor,
            audit_log=SimpleNamespace(log_command=lambda **_kwargs: None, read_all=lambda: []),
        ),
        lock=web_api.RLock(),
        connection=web_api.ServerConnectionIn(id="srv_1", mode="ssh", host="example.test", user="root"),
        terminal_cwd="",
    )

    cd_payload = manager.run_command(web_api.CommandRequest(serverId="srv_1", command="cd /"))
    ls_payload = manager.run_command(web_api.CommandRequest(serverId="srv_1", command="ls"))
    fail_payload = manager.run_command(web_api.CommandRequest(serverId="srv_1", command="false"))

    assert cd_payload["lines"] == []
    assert cd_payload["cwd"] == "/"
    assert ls_payload["lines"] == ["bin", "etc"]
    assert ls_payload["cwd"] == "/"
    assert fail_payload["lines"] == ["boom", "[exit 2]"]
    assert executor.shell_cwd_calls == [None, "/", "/"]
