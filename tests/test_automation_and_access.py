from __future__ import annotations

import base64
import json
from pathlib import Path

from sysdialogue.agent.conversation import ConversationManager
from sysdialogue.app.config import AppConfig
from sysdialogue.app.jobs import run_scheduled_job
from sysdialogue.runtime.capability_probe import CapabilityProbe
from sysdialogue.security import path_policies as path_policies
from sysdialogue.security.risk_classifier import classify
from sysdialogue.tools import auth_keys as auth_keys_module
from sysdialogue.tools.auth_keys import _public_key_fingerprint, manage_authorized_keys
from sysdialogue.tools.config_validate import validate_config
from sysdialogue.tools.cron_jobs import manage_cron
from sysdialogue.tools.file_ops import copy_move_path
from sysdialogue.tools.firewall import manage_firewall

from tests.helpers import RecordingExecutor


def _fake_public_key(label: str) -> str:
    blob = base64.b64encode(label.encode("utf-8")).decode("ascii")
    return f"ssh-ed25519 {blob} {label}@example"


def test_manage_authorized_keys_removes_key_by_fingerprint(tmp_path: Path, monkeypatch) -> None:
    home_dir = tmp_path / "alice"
    key_path = home_dir / ".ssh" / "authorized_keys"
    executor = RecordingExecutor()
    monkeypatch.setattr(auth_keys_module, "_auth_keys_path", lambda executor, username: str(key_path))

    key_one = _fake_public_key("one")
    key_two = _fake_public_key("two")
    result_add_one = manage_authorized_keys(executor, "add", "alice", public_key=key_one)
    result_add_two = manage_authorized_keys(executor, "add", "alice", public_key=key_two)
    assert result_add_one.success and result_add_two.success

    fingerprint = _public_key_fingerprint(key_one)
    assert fingerprint is not None
    result_remove = manage_authorized_keys(
        executor,
        "remove",
        "alice",
        fingerprint=fingerprint,
    )

    assert result_remove.success is True
    authorized_keys = key_path.read_text(encoding="utf-8")
    assert key_one not in authorized_keys
    assert key_two in authorized_keys


def test_manage_cron_create_updates_index_and_installs_user_crontab(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))

    captured: dict[str, str] = {}

    def handler(cmd: list[str], timeout: int):
        if cmd == ["crontab", "-l"]:
            return ("MAILTO=ops@example.com\n", 0)
        if cmd and cmd[0] == "crontab" and len(cmd) == 2:
            captured["installed"] = Path(cmd[1]).read_text(encoding="utf-8")
            return ("", 0)
        return ("", 0)

    executor = RecordingExecutor(handler=handler)
    result = manage_cron(
        executor,
        action="create",
        scope="user",
        schedule="*/5 * * * *",
        job_target={"kind": "tool", "name": "get_system_info", "args": {}},
    )

    assert result.success is True
    job_id = result.data["job_id"]

    index_path = tmp_path / ".sysdialogue" / "cron_index.json"
    index_data = json.loads(index_path.read_text(encoding="utf-8"))
    assert job_id in index_data
    assert f"sysdialogue --run-scheduled-job {job_id}" in captured["installed"]
    assert f"# sysdialogue:job:{job_id}" in captured["installed"]


def test_manage_cron_rolls_back_index_when_install_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))

    def handler(cmd: list[str], timeout: int):
        if cmd == ["crontab", "-l"]:
            return ("", 1)
        if cmd and cmd[0] == "crontab" and len(cmd) == 2:
            return ("install failed", 1)
        return ("", 0)

    result = manage_cron(
        RecordingExecutor(handler=handler),
        action="create",
        scope="user",
        schedule="*/5 * * * *",
        job_target={"kind": "tool", "name": "get_system_info", "args": {}},
    )

    assert result.success is False
    index_path = tmp_path / ".sysdialogue" / "cron_index.json"
    assert json.loads(index_path.read_text(encoding="utf-8")) == {}


def test_manage_cron_rejects_invalid_scope() -> None:
    result = manage_cron(
        RecordingExecutor(),
        action="create",
        scope="typo",
        schedule="*/5 * * * *",
        job_target={"kind": "tool", "name": "get_system_info", "args": {}},
    )

    assert result.success is False
    assert "scope" in result.error


def test_manage_firewall_iptables_delete_uses_delete_op_and_policy() -> None:
    executor = RecordingExecutor()

    result = manage_firewall(
        executor,
        action="delete",
        backend="iptables",
        target={"port": 443, "protocol": "tcp", "source_ip": "1.2.3.4"},
        policy="accept",
    )

    assert result.success is True
    assert executor.calls[0] == [
        "iptables",
        "-D",
        "INPUT",
        "-s",
        "1.2.3.4",
        "-p",
        "tcp",
        "--dport",
        "443",
        "-j",
        "ACCEPT",
    ]


def test_capability_probe_sets_supports_system_cron_only_when_crontab_exists() -> None:
    def handler(cmd: list[str], timeout: int):
        if cmd == ["cat", "/etc/os-release"]:
            return ('ID=ubuntu\nVERSION_ID="22.04"\nPRETTY_NAME="Ubuntu 22.04"\n', 0)
        if cmd == ["uname", "-r"]:
            return ("6.8.0", 0)
        if cmd == ["uname", "-m"]:
            return ("x86_64", 0)
        if cmd == ["whoami"]:
            return ("tester", 0)
        if cmd == ["sudo", "-n", "true"]:
            return ("", 1)
        if cmd == ["cat", "/.dockerenv"]:
            return ("", 1)
        if cmd == ["cat", "/proc/1/cgroup"]:
            return ("", 1)
        if cmd[:2] == ["which", "crontab"]:
            return ("/usr/bin/crontab", 0)
        if cmd and cmd[0] == "which":
            return ("", 1)
        return ("", 1)

    profile = CapabilityProbe(RecordingExecutor(handler=handler)).probe()
    assert profile["supports_system_cron"] is True

    def no_crontab_handler(cmd: list[str], timeout: int):
        if cmd[:2] == ["which", "crontab"]:
            return ("", 1)
        return handler(cmd, timeout)

    profile_without_cron = CapabilityProbe(RecordingExecutor(handler=no_crontab_handler)).probe()
    assert profile_without_cron["supports_system_cron"] is False


def test_validate_config_supports_fstab_and_cron_static_checks(tmp_path: Path) -> None:
    executor = RecordingExecutor()
    fstab_path = tmp_path / "fstab"
    fstab_path.write_text("/dev/sda1 / ext4 defaults 0 1\n", encoding="utf-8")
    cron_path = tmp_path / "sysdialogue"
    cron_path.write_text("SHELL=/bin/sh\n*/5 * * * * sysdialogue --verify\n", encoding="utf-8")

    assert validate_config(executor, str(fstab_path), target_type="fstab").success is True
    assert validate_config(executor, str(cron_path), target_type="cron").success is True

    cron_path.write_text("* * * sysdialogue --verify\n", encoding="utf-8")
    invalid = validate_config(executor, str(cron_path), target_type="cron")
    assert invalid.success is False
    assert "cron" in invalid.error


def test_linux_path_policies_do_not_depend_on_host_os_separators() -> None:
    assert path_policies.normalize("/etc/passwd") == "/etc/passwd"
    assert path_policies.matches_system_dir("/etc/passwd") is True
    assert path_policies.matches_container_sensitive_bind("/etc/ssh/sshd_config") is True

    decision = classify(
        "copy_move_path",
        {"src": "/tmp/source", "dst": "/etc/passwd", "action": "copy"},
        env_profile={},
    )
    assert decision.level == "BLOCK"
    assert decision.rule_ids == ["B012"]

    result = copy_move_path(RecordingExecutor(), "/tmp/source", "/etc/passwd", action="copy")
    assert result.success is False
    assert "B012" in result.error


def test_scheduled_workflow_rejects_high_risk_steps_before_execution(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    workflows_dir = tmp_path / "workflows"
    workflows_dir.mkdir()
    (workflows_dir / "risky.yaml").write_text(
        """
name: risky
parameters: []
steps:
  - id: stop_service
    type: tool_call
    tool: manage_service
    args:
      name: nginx
      action: stop
""".lstrip(),
        encoding="utf-8",
    )
    state_dir = tmp_path / ".sysdialogue"
    state_dir.mkdir()
    (state_dir / "cron_index.json").write_text(
        json.dumps(
            {
                "job_risky": {
                    "job_id": "job_risky",
                    "scope": "user",
                    "schedule": "* * * * *",
                    "enabled": True,
                    "job_target": {"kind": "workflow", "name": "risky", "args": {}},
                }
            }
        ),
        encoding="utf-8",
    )

    code = run_scheduled_job(AppConfig(workflows_dir=str(workflows_dir)), "job_risky")

    assert code == 2


def test_conversation_trim_keeps_complete_tool_result_pairs() -> None:
    manager = ConversationManager(max_messages=5)
    messages: list[dict] = []
    for idx in range(3):
        tool_id = f"tool_{idx}"
        messages.extend(
            [
                {"role": "user", "content": f"request {idx}"},
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": tool_id,
                            "name": "get_system_info",
                            "input": {},
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_id,
                            "content": "{}",
                        }
                    ],
                },
                {"role": "assistant", "content": f"done {idx}"},
            ]
        )

    manager.commit_turn(messages)

    assert len(manager.history) == 4
    assert manager.history[0] == {"role": "user", "content": "request 2"}
    assert manager.history[2]["content"][0]["type"] == "tool_result"
