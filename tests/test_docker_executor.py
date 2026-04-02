"""
Integration tests for Docker sandbox executor.
Requires Docker daemon running and strategy-sandbox:latest built.
Mark: pytest -m integration
"""
import pytest
from bitget_bot.sandbox.docker_executor import run_strategy_in_sandbox, SandboxError

MINIMAL_STRATEGY = """
import numpy as np
import pandas as pd

def add_indicators(df):
    out = df.copy()
    out['sma5'] = out['close'].rolling(5).mean()
    return out

def get_signal(df, i, params):
    if i < 5 or pd.isna(df['sma5'].iloc[i]):
        return {'long_entry': False, 'short_entry': False,
                'close_long': False, 'close_short': False}
    c = float(df['close'].iloc[i])
    s = float(df['sma5'].iloc[i])
    return {
        'long_entry': c > s,
        'short_entry': c < s,
        'close_long': c < s,
        'close_short': c > s,
    }
"""

MALICIOUS_STRATEGY = """
import os
def add_indicators(df): return df
def get_signal(df, i, p): return {}
"""

def _make_ohlcv(n=50):
    import time
    base_ts = int(time.time() * 1000) - n * 900_000
    rows = []
    price = 35000.0
    for k in range(n):
        price += (k % 7 - 3) * 10
        rows.append([base_ts + k * 900_000, price, price + 50, price - 50, price + 25, 100 + k])
    return rows

@pytest.mark.integration
def test_valid_strategy_runs():
    result = run_strategy_in_sandbox(
        strategy_code=MINIMAL_STRATEGY,
        ohlcv=_make_ohlcv(50),
        params={"initial_equity": 10000, "leverage": 5, "fee_rate": 0.0005, "margin_pct": 100},
    )
    assert result["success"] is True
    assert "summary" in result
    assert "equity_curve" in result
    assert "trades" in result

@pytest.mark.integration
def test_malicious_code_blocked_by_ast():
    with pytest.raises(SandboxError, match="代码安全检查"):
        run_strategy_in_sandbox(strategy_code=MALICIOUS_STRATEGY, ohlcv=[], params={})

@pytest.mark.integration
def test_equity_curve_max_300_points():
    result = run_strategy_in_sandbox(
        strategy_code=MINIMAL_STRATEGY,
        ohlcv=_make_ohlcv(50),
        params={"initial_equity": 10000, "leverage": 5, "fee_rate": 0.0005, "margin_pct": 100},
    )
    assert len(result["equity_curve"]) <= 300
