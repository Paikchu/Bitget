import json

from bitget_bot.sandbox import docker_executor


def test_run_strategy_falls_back_to_sdk_when_cli_missing(monkeypatch):
    expected = {"success": True, "summary": {}, "equity_curve": [], "trades": []}

    monkeypatch.setattr(docker_executor, "validate_strategy_code", lambda code: [])
    monkeypatch.setattr(docker_executor, "_resolve_docker_bin", lambda: None)
    monkeypatch.setattr(
        docker_executor,
        "_run_with_docker_sdk",
        lambda strategy_code, ohlcv, params, timeout: expected,
        raising=False,
    )

    result = docker_executor.run_strategy_in_sandbox(
        strategy_code="def add_indicators(df): return df\n\ndef get_signal(df, i, params): return {}",
        ohlcv=[],
        params={},
    )

    assert result == expected
