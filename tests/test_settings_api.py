from pathlib import Path

from fastapi.testclient import TestClient

from bitget_bot import api


def test_get_settings_returns_masked_snapshot(tmp_path, monkeypatch):
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
    monkeypatch.setattr(api, "_settings_env_path", lambda: env_path)
    api._runtime_config = {
        "version": 3,
        "applied_version": 2,
        "last_apply_error": "reload failed",
    }
    client = TestClient(api.app)

    response = client.get("/api/settings")

    assert response.status_code == 200
    data = response.json()
    assert data["trading"]["bitget_api_key"]["is_set"] is True
    assert data["trading"]["bitget_api_key"]["value"] != "old-key"
    assert data["runtime"]["config_version"] == 3
    assert data["runtime"]["applied_version"] == 2
    assert data["runtime"]["last_apply_error"] == "reload failed"


def test_put_settings_persists_updates_and_bumps_runtime_version(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "SYMBOL=BTC/USDT:USDT\nTIMEFRAME=15m\nLEVERAGE=5\nDRY_RUN=true\nPOLL_INTERVAL=60\nINITIAL_EQUITY=10000\nMARGIN_USAGE_PCT=100\nSQUEEZE_THRESHOLD=0.35\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(api, "_settings_env_path", lambda: env_path)
    api._runtime_config = {"version": 0, "applied_version": 0, "last_apply_error": "", "settings": {}}
    client = TestClient(api.app)

    response = client.put(
        "/api/settings",
        json={"SYMBOL": "ETH/USDT:USDT", "LEVERAGE": 9, "BITGET_API_KEY": "new-key"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["applied"] is False
    assert body["runtime"]["config_version"] == 1
    assert body["updates"]["SYMBOL"] == "ETH/USDT:USDT"
    assert "SYMBOL=ETH/USDT:USDT" in env_path.read_text(encoding="utf-8")
    assert "BITGET_API_KEY=new-key" in env_path.read_text(encoding="utf-8")


def test_post_settings_test_uses_request_values(monkeypatch):
    client = TestClient(api.app)
    monkeypatch.setattr(
        api,
        "test_settings_connections",
        lambda payload: {
            "bitget": {"ok": payload["BITGET_API_KEY"] == "abc"},
            "deepseek": {"ok": payload["DEEPSEEK_API_KEY"] == "def"},
        },
    )

    response = client.post(
        "/api/settings/test",
        json={"BITGET_API_KEY": "abc", "DEEPSEEK_API_KEY": "def"},
    )

    assert response.status_code == 200
    assert response.json()["bitget"]["ok"] is True
