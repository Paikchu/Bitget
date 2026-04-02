"""
Validates that the migrated strategy produces identical results to the original.
"""
import pytest
import time
import numpy as np
import pandas as pd


def _make_test_df(n_bars=500) -> pd.DataFrame:
    rng = np.random.default_rng(42)
    base = 35000.0
    closes = base + np.cumsum(rng.normal(0, 50, n_bars))
    opens = closes - rng.normal(0, 20, n_bars)
    highs = np.maximum(opens, closes) + rng.uniform(10, 80, n_bars)
    lows = np.minimum(opens, closes) - rng.uniform(10, 80, n_bars)
    vols = rng.uniform(50, 200, n_bars)
    base_ts = int(time.time() * 1000) - n_bars * 900_000
    timestamps = [base_ts + i * 900_000 for i in range(n_bars)]
    return pd.DataFrame({
        "timestamp": timestamps,
        "open": opens, "high": highs, "low": lows,
        "close": closes, "volume": vols,
    })


def test_indicator_columns_match():
    from bitget_bot.strategy import add_indicators as orig_add
    from bitget_bot.strategies.ma_squeeze_studio import add_indicators as new_add

    df = _make_test_df(200)
    old_df = orig_add(df.copy())
    new_df = new_add(df.copy())

    for col in ["m20", "m60", "m120", "e20", "e60", "e120"]:
        pd.testing.assert_series_equal(
            old_df[col].reset_index(drop=True),
            new_df[col].reset_index(drop=True),
            check_names=False,
            rtol=1e-10,
        )


def test_trade_count_and_directions_match():
    """Migration is correct if trade count and directions match original."""
    from backtest import run_backtest as orig_run
    from bitget_bot.strategies.ma_squeeze_studio import add_indicators, get_signal

    df = _make_test_df(500)

    # Original backtest
    orig_trades, _ = orig_run(
        df.copy(), squeeze_threshold=0.35, initial_equity=10_000,
        leverage=5, margin_pct=100, fee_rate=0.0,
    )
    orig_closed = [t for t in orig_trades if t.pnl_usdt is not None]

    # New strategy backtest (manual loop)
    d = add_indicators(df.copy())
    n = len(d)
    position = 0
    new_trades = []  # (direction,)
    new_exits = 0

    for i in range(1, n - 1):
        sig = get_signal(d, i, {"squeeze_threshold": 0.35})
        long_e = bool(sig.get("long_entry", False))
        short_e = bool(sig.get("short_entry", False))
        close_l = bool(sig.get("close_long", False))
        close_s = bool(sig.get("close_short", False))

        new_pos = position
        if long_e: new_pos = 1
        if short_e: new_pos = -1
        if position > 0 and close_l: new_pos = 0
        if position < 0 and close_s: new_pos = 0

        if position != 0 and new_pos != position:
            new_exits += 1
            position = 0
        if new_pos != 0 and new_pos != position:
            new_trades.append("long" if new_pos == 1 else "short")
            position = new_pos

    assert len(orig_closed) == new_exits, (
        f"Trade count mismatch: original={len(orig_closed)}, migrated={new_exits}"
    )
    orig_dirs = [t.direction for t in orig_closed]
    new_dirs = new_trades[:len(orig_closed)]
    assert orig_dirs == new_dirs, (
        f"Direction mismatch at first difference: orig={orig_dirs[:5]}, new={new_dirs[:5]}"
    )
