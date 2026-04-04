from bitget_bot import strategy_router
from fastapi import HTTPException


def test_normalize_generated_code_uses_previous_window_levels():
    source = """
import pandas as pd

def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out['high_20'] = out['high'].rolling(window=20).max()
    out['low_20'] = out['low'].rolling(window=20).min()
    out['high_10'] = out['high'].rolling(window=10).max()
    out['low_10'] = out['low'].rolling(window=10).min()
    return out

def get_signal(df: pd.DataFrame, i: int, params: dict) -> dict:
    current_high_20 = df['high_20'].iloc[i]
    current_low_20 = df['low_20'].iloc[i]
    current_high_10 = df['high_10'].iloc[i]
    current_low_10 = df['low_10'].iloc[i]
    return {
        'long_entry': current_high_20,
        'short_entry': current_low_20,
        'close_long': current_low_10,
        'close_short': current_high_10,
    }
"""

    normalized = strategy_router._normalize_generated_code(source)

    assert "df['high_20'].iloc[i-1]" in normalized
    assert "df['low_20'].iloc[i-1]" in normalized
    assert "df['high_10'].iloc[i-1]" in normalized
    assert "df['low_10'].iloc[i-1]" in normalized
    assert "df['high_20'].iloc[i]" not in normalized


def test_generate_strategy_persists_version(monkeypatch):
    saved_payload = {}

    class _FakeCompletions:
        @staticmethod
        def create(**kwargs):
            class _Message:
                content = "import pandas as pd\n\ndef add_indicators(df):\n    return df\n\ndef get_signal(df, i, params):\n    return {}"

            class _Choice:
                message = _Message()

            class _Response:
                choices = [_Choice()]

            return _Response()

    class _FakeChat:
        completions = _FakeCompletions()

    class _FakeClient:
        chat = _FakeChat()

    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(strategy_router, "_make_deepseek_client", lambda api_key: _FakeClient())
    monkeypatch.setattr(
        strategy_router,
        "_persist_generated_version",
        lambda markdown, code, model: saved_payload.update(
            {"markdown": markdown, "code": code, "model": model}
        ) or {
            "id": 7,
            "version_no": 7,
            "source": "generate",
            "created_at": "2026-04-04T00:00:00+00:00",
        },
        raising=False,
    )

    result = strategy_router.generate_strategy(
        strategy_router.GenerateRequest(markdown="# Test", model="deepseek-chat")
    )

    assert result["version"]["version_no"] == 7
    assert saved_payload["markdown"] == "# Test"
    assert saved_payload["model"] == "deepseek-chat"
    assert "def add_indicators" in saved_payload["code"]


def test_generate_strategy_does_not_persist_version_on_failure(monkeypatch):
    persisted = {"called": False}

    class _FailingCompletions:
        @staticmethod
        def create(**kwargs):
            raise RuntimeError("boom")

    class _FailingChat:
        completions = _FailingCompletions()

    class _FailingClient:
        chat = _FailingChat()

    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(strategy_router, "_make_deepseek_client", lambda api_key: _FailingClient())
    monkeypatch.setattr(
        strategy_router,
        "_persist_generated_version",
        lambda *args, **kwargs: persisted.update({"called": True}),
        raising=False,
    )

    try:
        strategy_router.generate_strategy(
            strategy_router.GenerateRequest(markdown="# Test", model="deepseek-chat")
        )
    except Exception:
        pass

    assert persisted["called"] is False


def test_list_versions_returns_summaries(monkeypatch):
    monkeypatch.setattr(
        strategy_router._db,
        "list_strategy_versions",
        lambda limit=50, offset=0: [
            {
                "id": 3,
                "version_no": 3,
                "title": "Three",
                "source": "generate",
                "model": "deepseek-chat",
                "created_at": "2026-04-04T00:00:00+00:00",
                "parent_version_id": None,
                "latest_backtest_summary": {"total_return_pct": 8.2},
                "latest_backtest_at": "2026-04-04T01:00:00+00:00",
                "markdown": "# Three",
                "code": "print('3')",
            }
        ],
    )

    result = strategy_router.list_versions()

    assert result == [
        {
            "id": 3,
            "version_no": 3,
            "title": "Three",
            "source": "generate",
            "model": "deepseek-chat",
            "created_at": "2026-04-04T00:00:00+00:00",
            "parent_version_id": None,
            "latest_backtest_summary": {"total_return_pct": 8.2},
            "latest_backtest_at": "2026-04-04T01:00:00+00:00",
        }
    ]


def test_get_version_returns_detail(monkeypatch):
    monkeypatch.setattr(
        strategy_router._db,
        "get_strategy_version",
        lambda version_id: {
            "id": version_id,
            "version_no": 4,
            "title": "Four",
            "source": "generate",
            "model": "deepseek-chat",
            "created_at": "2026-04-04T00:00:00+00:00",
            "parent_version_id": None,
            "latest_backtest_summary": {"total_return_pct": 2.1},
            "latest_backtest_at": "2026-04-04T01:00:00+00:00",
            "markdown": "# Four",
            "code": "print('4')",
        },
    )

    result = strategy_router.get_version(4)

    assert result["id"] == 4
    assert result["markdown"] == "# Four"
    assert result["latest_backtest_summary"]["total_return_pct"] == 2.1


def test_start_backtest_records_summary_for_strategy_version(monkeypatch):
    recorded = {}

    monkeypatch.setattr(strategy_router, "_fetch_ohlcv", lambda symbol, timeframe, days: [[1, 1, 1, 1, 1, 1]])

    def _fake_run_strategy_in_sandbox(strategy_code, ohlcv, params):
        return {
            "success": True,
            "summary": {"total_return_pct": 9.5, "total_trades": 4},
            "equity_curve": [],
            "trades": [],
        }

    monkeypatch.setitem(__import__("sys").modules, "bitget_bot.sandbox.docker_executor", type("M", (), {
        "run_strategy_in_sandbox": staticmethod(_fake_run_strategy_in_sandbox),
        "SandboxError": Exception,
    })())
    monkeypatch.setattr(
        strategy_router._db,
        "record_strategy_version_backtest",
        lambda strategy_version_id, job_id, summary: recorded.update(
            {
                "strategy_version_id": strategy_version_id,
                "job_id": job_id,
                "summary": summary,
            }
        ),
    )

    class _ImmediateThread:
        def __init__(self, target, **kwargs):
            self._target = target

        def start(self):
            self._target()

    monkeypatch.setattr(strategy_router.threading, "Thread", _ImmediateThread)

    response = strategy_router.start_backtest(
        strategy_router.BacktestRequest(
            strategy_code="print('x')",
            strategy_version_id=12,
        )
    )

    job = strategy_router.get_backtest(response["job_id"])

    assert job["status"] == "done"
    assert recorded["strategy_version_id"] == 12
    assert recorded["summary"]["total_return_pct"] == 9.5


def test_start_backtest_skips_version_record_without_strategy_version_id(monkeypatch):
    called = {"value": False}

    monkeypatch.setattr(strategy_router, "_fetch_ohlcv", lambda symbol, timeframe, days: [[1, 1, 1, 1, 1, 1]])

    def _fake_run_strategy_in_sandbox(strategy_code, ohlcv, params):
        return {
            "success": True,
            "summary": {"total_return_pct": 3.0, "total_trades": 1},
            "equity_curve": [],
            "trades": [],
        }

    monkeypatch.setitem(__import__("sys").modules, "bitget_bot.sandbox.docker_executor", type("M", (), {
        "run_strategy_in_sandbox": staticmethod(_fake_run_strategy_in_sandbox),
        "SandboxError": Exception,
    })())
    monkeypatch.setattr(
        strategy_router._db,
        "record_strategy_version_backtest",
        lambda *args, **kwargs: called.update({"value": True}),
    )

    class _ImmediateThread:
        def __init__(self, target, **kwargs):
            self._target = target

        def start(self):
            self._target()

    monkeypatch.setattr(strategy_router.threading, "Thread", _ImmediateThread)

    response = strategy_router.start_backtest(
        strategy_router.BacktestRequest(strategy_code="print('x')")
    )
    job = strategy_router.get_backtest(response["job_id"])

    assert job["status"] == "done"
    assert called["value"] is False


def test_get_version_raises_404_when_missing(monkeypatch):
    monkeypatch.setattr(strategy_router._db, "get_strategy_version", lambda version_id: None)

    try:
        strategy_router.get_version(999)
    except HTTPException as exc:
        assert exc.status_code == 404
        assert exc.detail == "Strategy version not found"
    else:
        raise AssertionError("Expected HTTPException")
