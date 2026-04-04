from pathlib import Path

import pytest

from bitget_bot.settings_manager import SettingsValidationError
from bitget_bot.settings_manager import load_settings_snapshot
from bitget_bot.settings_manager import save_settings
from bitget_bot.settings_manager import test_settings_connections


def test_load_settings_snapshot_masks_sensitive_values(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            [
                "BITGET_API_KEY=abc123456789",
                "BITGET_API_SECRET=super-secret",
                "BITGET_API_PASSPHRASE=phrase",
                "DEEPSEEK_API_KEY=deepseek-secret",
                "SYMBOL=ETH/USDT:USDT",
                "TIMEFRAME=5m",
                "LEVERAGE=8",
                "DRY_RUN=false",
                "POLL_INTERVAL=12",
                "INITIAL_EQUITY=2000",
                "MARGIN_USAGE_PCT=75.5",
                "SQUEEZE_THRESHOLD=0.25",
            ]
        ),
        encoding="utf-8",
    )

    snapshot = load_settings_snapshot(env_path=env_path, runtime_state=None)

    assert snapshot["trading"]["symbol"] == "ETH/USDT:USDT"
    assert snapshot["trading"]["bitget_api_key"]["is_set"] is True
    assert snapshot["trading"]["bitget_api_key"]["value"] != "abc123456789"
    assert snapshot["system"]["deepseek_api_key"]["is_set"] is True
    assert snapshot["system"]["deepseek_api_key"]["value"] != "deepseek-secret"


def test_save_settings_updates_requested_fields_only(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            [
                "BITGET_API_KEY=old-key",
                "BITGET_API_SECRET=old-secret",
                "BITGET_API_PASSPHRASE=old-pass",
                "SYMBOL=BTC/USDT:USDT",
                "TIMEFRAME=15m",
                "LEVERAGE=5",
                "DRY_RUN=true",
                "POLL_INTERVAL=60",
                "INITIAL_EQUITY=10000",
                "MARGIN_USAGE_PCT=100",
                "SQUEEZE_THRESHOLD=0.35",
                "DEEPSEEK_API_KEY=old-deepseek",
            ]
        ),
        encoding="utf-8",
    )

    result = save_settings(
        {
            "BITGET_API_KEY": "new-key",
            "BITGET_API_SECRET": "",
            "SYMBOL": "ETH/USDT:USDT",
            "LEVERAGE": 12,
            "DRY_RUN": False,
        },
        env_path=env_path,
    )

    env_content = env_path.read_text(encoding="utf-8")

    assert "BITGET_API_KEY=new-key" in env_content
    assert "BITGET_API_SECRET=" in env_content
    assert "BITGET_API_PASSPHRASE=old-pass" in env_content
    assert "SYMBOL=ETH/USDT:USDT" in env_content
    assert "LEVERAGE=12" in env_content
    assert "DRY_RUN=false" in env_content
    assert result["updates"]["BITGET_API_SECRET"] == ""


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"LEVERAGE": 0}, "LEVERAGE"),
        ({"POLL_INTERVAL": 4}, "POLL_INTERVAL"),
        ({"INITIAL_EQUITY": 0}, "INITIAL_EQUITY"),
        ({"MARGIN_USAGE_PCT": 0}, "MARGIN_USAGE_PCT"),
        ({"SQUEEZE_THRESHOLD": 0}, "SQUEEZE_THRESHOLD"),
        ({"SYMBOL": ""}, "SYMBOL"),
    ],
)
def test_save_settings_rejects_invalid_values(tmp_path, payload, expected):
    env_path = tmp_path / ".env"
    env_path.write_text("SYMBOL=BTC/USDT:USDT\n", encoding="utf-8")

    with pytest.raises(SettingsValidationError) as exc:
        save_settings(payload, env_path=env_path)

    assert expected in str(exc.value)


def test_test_settings_connections_uses_supplied_values(monkeypatch):
    recorded = {}

    class FakeBitget:
        def __init__(self, config):
            recorded["bitget_config"] = config

        def fetch_balance(self, params):
            recorded["bitget_fetch_balance"] = params
            return {"USDT": {"free": 1}}

    class FakeResponse:
        choices = []

    class FakeCompletions:
        def create(self, **kwargs):
            recorded["deepseek_request"] = kwargs
            return FakeResponse()

    class FakeChat:
        completions = FakeCompletions()

    class FakeOpenAI:
        def __init__(self, **kwargs):
            recorded["deepseek_config"] = kwargs
            self.chat = FakeChat()

    monkeypatch.setattr("bitget_bot.settings_manager.ccxt.bitget", FakeBitget)
    monkeypatch.setattr("bitget_bot.settings_manager.OpenAI", FakeOpenAI)

    result = test_settings_connections(
        {
            "BITGET_API_KEY": "key-1",
            "BITGET_API_SECRET": "secret-1",
            "BITGET_API_PASSPHRASE": "pass-1",
            "DEEPSEEK_API_KEY": "deep-1",
        }
    )

    assert result["bitget"]["ok"] is True
    assert result["deepseek"]["ok"] is True
    assert recorded["bitget_config"]["apiKey"] == "key-1"
    assert recorded["deepseek_config"]["api_key"] == "deep-1"
