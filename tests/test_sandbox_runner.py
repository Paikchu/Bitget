import io
import json

from sandbox import sandbox_runner


def test_main_reads_payload_from_file(tmp_path, monkeypatch):
    payload = {
        "strategy_code": (
            "import pandas as pd\n"
            "def add_indicators(df):\n"
            "    return df\n\n"
            "def get_signal(df, i, params):\n"
            "    return {'long_entry': False, 'short_entry': False, 'close_long': False, 'close_short': False}\n"
        ),
        "ohlcv": [
            [1711929600000, 100.0, 101.0, 99.0, 100.5, 10.0],
            [1711930500000, 100.5, 102.0, 100.0, 101.5, 11.0],
            [1711931400000, 101.5, 103.0, 101.0, 102.0, 12.0],
        ],
        "params": {"initial_equity": 10000, "leverage": 1, "fee_rate": 0.0005, "margin_pct": 100},
    }
    payload_path = tmp_path / "payload.json"
    payload_path.write_text(json.dumps(payload), encoding="utf-8")

    stdout = io.StringIO()
    monkeypatch.setattr("sys.argv", ["sandbox_runner.py", str(payload_path)])
    monkeypatch.setattr("sys.stdout", stdout)

    sandbox_runner.main()

    result = json.loads(stdout.getvalue())
    assert result["success"] is True
    assert "summary" in result


def test_run_backtest_returns_diagnostic_trade_and_summary_fields():
    strategy_code = (
        "import pandas as pd\n"
        "def add_indicators(df):\n"
        "    return df\n\n"
        "def get_signal(df, i, params):\n"
        "    if i == 1:\n"
        "        return {'long_entry': True, 'short_entry': False, 'close_long': False, 'close_short': False}\n"
        "    if i == 2:\n"
        "        return {'long_entry': False, 'short_entry': False, 'close_long': True, 'close_short': False}\n"
        "    return {'long_entry': False, 'short_entry': False, 'close_long': False, 'close_short': False}\n"
    )
    strategy_ns = sandbox_runner._load_strategy(strategy_code)
    ohlcv = [
        [1711929600000, 100.0, 101.0, 99.0, 100.0, 10.0],
        [1711930500000, 100.0, 102.0, 99.5, 101.0, 11.0],
        [1711931400000, 101.0, 104.0, 100.5, 103.0, 12.0],
        [1711932300000, 103.0, 105.0, 102.0, 104.0, 13.0],
        [1711933200000, 104.0, 105.0, 100.0, 101.0, 14.0],
    ]

    result = sandbox_runner._run_backtest(
        ohlcv,
        strategy_ns,
        {"initial_equity": 10000, "leverage": 1, "fee_rate": 0.0005, "margin_pct": 100},
    )

    trade = result["trades"][0]
    summary = result["summary"]

    assert trade["exit_reason"] == "signal_exit"
    assert trade["holding_bars"] >= 1
    assert trade["holding_minutes"] >= 15
    assert "max_favorable_excursion_pct" in trade
    assert "max_adverse_excursion_pct" in trade
    assert "peak_profit_before_exit_pct" in trade
    assert "deepest_drawdown_before_exit_pct" in trade

    assert "avg_win_pct" in summary
    assert "avg_loss_pct" in summary
    assert "avg_win_usdt" in summary
    assert "avg_loss_usdt" in summary
    assert "expectancy_pct" in summary
    assert "expectancy_usdt" in summary
    assert "pnl_stddev" in summary
    assert "avg_holding_bars" in summary
    assert "long_win_rate_pct" in summary
    assert "short_win_rate_pct" in summary
    assert "max_consecutive_losses" in summary
    assert "recovery_factor" in summary
