from __future__ import annotations

import paramiko

from sysdialogue.runtime.ssh_adapter import _AutoAddKnownHostPolicy, _load_host_keys


def test_load_host_keys_handles_windows_bom_prefixed_known_hosts(tmp_path) -> None:
    key = paramiko.RSAKey.generate(1024)
    known_hosts = tmp_path / "known_hosts"
    line = f"example.com {key.get_name()} {key.get_base64()}\n".encode("ascii")
    known_hosts.write_bytes(b"\xff\xfe\r\n" + line)

    client = paramiko.SSHClient()
    _load_host_keys(client, str(known_hosts), system=True)

    loaded = client._system_host_keys.lookup("example.com")  # noqa: SLF001
    assert loaded is not None
    assert loaded[key.get_name()].get_base64() == key.get_base64()


def test_auto_add_known_host_policy_appends_unknown_key(tmp_path) -> None:
    key = paramiko.RSAKey.generate(1024)
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text("existing-host ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQC\n", encoding="utf-8")

    client = paramiko.SSHClient()
    _AutoAddKnownHostPolicy(known_hosts).missing_host_key(client, "[example.com]:2222", key)

    content = known_hosts.read_text(encoding="utf-8")
    assert f"[example.com]:2222 {key.get_name()} {key.get_base64()}" in content
    assert client._host_keys.lookup("[example.com]:2222") is not None  # noqa: SLF001
