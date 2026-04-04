import importlib

from fastapi.testclient import TestClient


def test_versions_endpoint_returns_seeded_builtin_version_for_new_database(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            [
                "SYMBOL=BTC/USDT:USDT",
                "TIMEFRAME=15m",
                "LEVERAGE=5",
                "DRY_RUN=true",
                "POLL_INTERVAL=60",
                "INITIAL_EQUITY=10000",
                "MARGIN_USAGE_PCT=100",
                "SQUEEZE_THRESHOLD=0.35",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("DB_PATH", str(tmp_path / "bot.db"))

    import bitget_bot.db as db_module
    import bitget_bot.api as api_module

    db = importlib.reload(db_module)
    api = importlib.reload(api_module)

    monkeypatch.setattr(api, "_settings_env_path", lambda: env_path)
    monkeypatch.setattr(api, "start_loop", lambda **kwargs: None)

    with TestClient(api.app) as client:
        response = client.get("/api/strategy/versions")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["version_no"] == 1
    assert data[0]["source"] == "builtin_import"
