from bitget_bot import strategy_router
from fastapi import HTTPException
import numpy as np


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


def test_start_backtest_passes_requested_timeframe_into_data_fetch_and_params(monkeypatch):
    captured = {}

    def _fake_fetch_ohlcv(symbol, timeframe, days):
        captured["fetch"] = {
            "symbol": symbol,
            "timeframe": timeframe,
            "days": days,
        }
        return [[1, 1, 1, 1, 1, 1]]

    def _fake_run_strategy_in_sandbox(strategy_code, ohlcv, params):
        captured["params"] = params
        return {
            "success": True,
            "summary": {"total_return_pct": 1.2, "total_trades": 2},
            "equity_curve": [],
            "trades": [],
        }

    monkeypatch.setattr(strategy_router, "_fetch_ohlcv", _fake_fetch_ohlcv)
    monkeypatch.setitem(__import__("sys").modules, "bitget_bot.sandbox.docker_executor", type("M", (), {
        "run_strategy_in_sandbox": staticmethod(_fake_run_strategy_in_sandbox),
        "SandboxError": Exception,
    })())

    class _ImmediateThread:
        def __init__(self, target, **kwargs):
            self._target = target

        def start(self):
            self._target()

    monkeypatch.setattr(strategy_router.threading, "Thread", _ImmediateThread)

    response = strategy_router.start_backtest(
        strategy_router.BacktestRequest(
            strategy_code="print('x')",
            symbol="BTC/USDT:USDT",
            timeframe="4h",
            days=30,
        )
    )
    job = strategy_router.get_backtest(response["job_id"])

    assert job["status"] == "done"
    assert captured["fetch"] == {
        "symbol": "BTC/USDT:USDT",
        "timeframe": "4h",
        "days": 30,
    }
    assert captured["params"]["timeframe"] == "4h"


def test_start_experiment_runs_parameter_grid_and_persists_runs(monkeypatch):
    persisted = {"experiment": None, "runs": []}

    monkeypatch.setattr(strategy_router, "_fetch_ohlcv", lambda symbol, timeframe, days: [[1, 100, 101, 99, 100, 1]])

    def _fake_run_strategy_in_sandbox(strategy_code, ohlcv, params):
        return {
            "success": True,
            "summary": {
                "total_return_pct": float(params["days"]),
                "max_drawdown_pct": 2.0,
                "total_trades": 1,
            },
            "equity_curve": [],
            "trades": [],
        }

    monkeypatch.setitem(__import__("sys").modules, "bitget_bot.sandbox.docker_executor", type("M", (), {
        "run_strategy_in_sandbox": staticmethod(_fake_run_strategy_in_sandbox),
        "SandboxError": Exception,
    })())
    monkeypatch.setattr(
        strategy_router._db,
        "create_strategy_experiment",
        lambda **kwargs: persisted.update({"experiment": kwargs}) or {"id": 9, **kwargs, "status": "running"},
    )
    monkeypatch.setattr(
        strategy_router._db,
        "add_strategy_experiment_run",
        lambda **kwargs: persisted["runs"].append(kwargs) or kwargs,
    )
    monkeypatch.setattr(
        strategy_router._db,
        "update_strategy_experiment_status",
        lambda experiment_id, status, aggregate_summary=None, error=None: {"id": experiment_id, "status": status},
    )

    class _ImmediateThread:
        def __init__(self, target, **kwargs):
            self._target = target

        def start(self):
            self._target()

    monkeypatch.setattr(strategy_router.threading, "Thread", _ImmediateThread)

    response = strategy_router.start_experiment(
        strategy_router.ExperimentRequest(
            strategy_code="print('x')",
            parameter_grid={"timeframe": ["15m", "4h"], "days": [30]},
        )
    )

    assert response["status"] == "running"
    assert persisted["experiment"]["scenario_summary"] == ["15m-30d", "4h-30d"]
    assert len(persisted["runs"]) == 2
    assert {run["scenario_tag"] for run in persisted["runs"]} == {"15m-30d", "4h-30d"}


def test_generate_experiment_feedback_persists_analysis(monkeypatch):
    saved = {}
    monkeypatch.setattr(
        strategy_router._db,
        "get_strategy_experiment",
        lambda experiment_id: {
            "id": experiment_id,
            "strategy_code": "print('x')",
            "config": {"parameter_grid": {"timeframe": ["15m"]}},
            "aggregate_summary": {"best_total_return_pct": 3.2},
            "status": "done",
        },
    )
    monkeypatch.setattr(
        strategy_router._db,
        "list_strategy_experiment_runs",
        lambda experiment_id: [
            {
                "params": {"timeframe": "15m", "days": 90},
                "scenario_tag": "15m-90d",
                "result": {"summary": {"total_return_pct": 3.2, "max_drawdown_pct": 1.1}, "trades": []},
            }
        ],
    )
    monkeypatch.setattr(
        strategy_router,
        "_analyze_experiment_feedback",
        lambda experiment, runs: {
            "feedback": {
                "overall_score": 81,
                "stop_loss_assessment": {"label": "neutral", "score": 70, "summary": "ok"},
                "take_profit_assessment": {"label": "early", "score": 58, "summary": "too early"},
                "robustness_assessment": {"label": "moderate", "score": 67, "summary": "mixed"},
                "top_issues": [],
                "recommended_experiments": [],
                "parameter_adjustment_hints": [],
                "confidence": 0.74,
                "evidence": [],
            },
            "model": "deepseek-chat",
            "prompt_version": "v1",
            "schema_version": "feedback.v1",
        },
    )
    monkeypatch.setattr(
        strategy_router._db,
        "save_strategy_experiment_feedback",
        lambda **kwargs: saved.update(kwargs) or {"id": 3, **kwargs},
    )

    result = strategy_router.generate_experiment_feedback(7)

    assert result["feedback"]["overall_score"] == 81
    assert saved["experiment_id"] == 7
    assert saved["schema_version"] == "feedback.v1"


def test_generate_backtest_feedback_stores_feedback_on_job(monkeypatch):
    strategy_router._jobs["sb_test_feedback"] = {
        "job_id": "sb_test_feedback",
        "status": "done",
        "summary": {"total_trades": 3},
        "trades": [{"direction": "long", "pnl_usdt": -10}],
    }
    monkeypatch.setattr(
        strategy_router,
        "_analyze_backtest_feedback",
        lambda job: {
            "feedback": {
                "overall_score": 71,
                "overall_diagnosis": {"headline": "Weak entries", "summary": "Repeated quick losses."},
                "issue_groups": [],
                "priority_actions": ["Tighten entry filter"],
                "evidence": ["2 fast losses"],
                "confidence": 0.8,
            },
            "model": "deepseek-chat",
            "prompt_version": "v1",
            "schema_version": "trade-feedback.v1",
        },
    )

    result = strategy_router.generate_backtest_feedback("sb_test_feedback")

    assert result["job_id"] == "sb_test_feedback"
    assert result["schema_version"] == "trade-feedback.v1"
    assert strategy_router._jobs["sb_test_feedback"]["feedback"]["feedback"]["overall_score"] == 71


def test_generate_backtest_feedback_requires_completed_job():
    strategy_router._jobs["sb_test_running"] = {"job_id": "sb_test_running", "status": "running"}

    try:
        strategy_router.generate_backtest_feedback("sb_test_running")
    except HTTPException as exc:
        assert exc.status_code == 400
        assert exc.detail == "Backtest is not completed"
    else:
        raise AssertionError("Expected HTTPException")


def test_generate_backtest_feedback_requires_trades():
    strategy_router._jobs["sb_test_empty"] = {
        "job_id": "sb_test_empty",
        "status": "done",
        "summary": {"total_trades": 0},
        "trades": [],
    }

    try:
        strategy_router.generate_backtest_feedback("sb_test_empty")
    except HTTPException as exc:
        assert exc.status_code == 400
        assert exc.detail == "Backtest has no trades"
    else:
        raise AssertionError("Expected HTTPException")


def test_db_log_signal_normalizes_numpy_bool_payload(monkeypatch):
    captured = {}

    monkeypatch.setattr(strategy_router, "_db", strategy_router._db)
    from bitget_bot import runner

    monkeypatch.setattr(
        runner._db,
        "log_event",
        lambda event_type, message, payload=None: captured.update(
            {"event_type": event_type, "message": message, "payload": payload}
        ),
    )

    class _Sig:
        long_entry = np.bool_(True)
        short_entry = np.bool_(False)
        close_long = np.bool_(False)
        close_short = np.bool_(True)
        is_squeezed = np.bool_(False)

    runner._db_log_signal(_Sig(), 1711929600000, "BTC/USDT:USDT")

    assert captured["event_type"] == "signal"
    assert captured["payload"]["long_entry"] is True
    assert captured["payload"]["close_short"] is True
    assert captured["payload"]["is_squeezed"] is False


def test_get_version_raises_404_when_missing(monkeypatch):
    monkeypatch.setattr(strategy_router._db, "get_strategy_version", lambda version_id: None)

    try:
        strategy_router.get_version(999)
    except HTTPException as exc:
        assert exc.status_code == 404
        assert exc.detail == "Strategy version not found"
    else:
        raise AssertionError("Expected HTTPException")
