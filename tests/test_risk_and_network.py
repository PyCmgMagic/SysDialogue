from __future__ import annotations

from sysdialogue.security.path_policies import private_subnet_key
from sysdialogue.security.risk_classifier import classify
from sysdialogue.tools.net_diag import check_endpoint

from tests.helpers import RecordingExecutor


def test_private_subnet_key_uses_ipv4_24_mask() -> None:
    assert private_subnet_key("10.12.34.56") == "10.12.34.0/24"
    assert private_subnet_key("127.0.0.1") is None


def test_wh025_escalates_after_more_than_ten_private_probes() -> None:
    decision = classify(
        "check_endpoint",
        {"kind": "tcp", "host": "10.0.0.55", "port": 443},
        env_profile={},
        session_counters={"private_probe_subnets": {"10.0.0.0/24": 10}},
    )

    assert decision.level == "WARN-HIGH"
    assert decision.rule_ids == ["WH025"]
    assert decision.requires_confirmation is True
    assert "10.0.0.0/24" in decision.reason


def test_wh025_checks_resolver_even_when_query_name_is_private() -> None:
    decision = classify(
        "resolve_dns",
        {"name": "10.0.1.10", "resolver": "10.0.0.53"},
        env_profile={},
        session_counters={"private_probe_subnets": {"10.0.0.0/24": 10}},
    )

    assert decision.level == "WARN-HIGH"
    assert decision.rule_ids == ["WH025"]


def test_check_endpoint_blocks_redirects_into_private_network() -> None:
    counters: dict = {}
    executor = RecordingExecutor(
        handler=lambda cmd, timeout: ("302 https://10.0.0.5/internal", 0)
        if cmd and cmd[0] == "curl"
        else ("", 0)
    )

    result = check_endpoint(
        executor,
        kind="http",
        host="example.com",
        path="/healthz",
        _session_counters=counters,
    )

    assert result.success is False
    assert "WH025" in result.error
    assert result.data["redirect_url"] == "https://10.0.0.5/internal"
    assert counters["private_probe_subnets"]["10.0.0.0/24"] == 1
