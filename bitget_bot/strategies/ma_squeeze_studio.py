"""
MA-squeeze + volume-confirm strategy adapted to the Studio get_signal interface.
Logic is identical to evaluate_bar_fixed in bitget_bot.strategy.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute MA-squeeze indicators.
    Produces columns identical to bitget_bot.strategy.add_indicators,
    plus gap_pct (bandwidth %), is_bull, is_bear.
    """
    o = df["open"].astype(float)
    c = df["close"].astype(float)
    out = df.copy()
    out["m20"] = c.rolling(20, min_periods=20).mean()
    out["m60"] = c.rolling(60, min_periods=60).mean()
    out["m120"] = c.rolling(120, min_periods=120).mean()
    out["e20"] = c.ewm(span=20, adjust=False).mean()
    out["e60"] = c.ewm(span=60, adjust=False).mean()
    out["e120"] = c.ewm(span=120, adjust=False).mean()
    out["is_bull"] = (c > o).astype(float)
    out["is_bear"] = (c < o).astype(float)
    ma_cols = ["m20", "m60", "m120", "e20", "e60", "e120"]
    upper = out[ma_cols].max(axis=1)
    lower = out[ma_cols].min(axis=1)
    mid = (upper + lower) / 2
    out["gap_pct"] = np.where(mid > 0, (upper - lower) / mid * 100.0, np.nan)
    return out


def _ta_value_when(
    condition: np.ndarray,
    source: np.ndarray,
    occurrence: int,
    at: int,
) -> float:
    """Pine ta.valuewhen: occurrence=0 most recent true bar, 1=second most recent."""
    c = condition[: at + 1]
    s = source[: at + 1]
    idxs = np.flatnonzero(c)
    if len(idxs) <= occurrence:
        return float("nan")
    bar = idxs[-(occurrence + 1)]
    return float(s[bar])


def _crossover(series: np.ndarray, ref: np.ndarray, i: int) -> bool:
    if i < 1:
        return False
    return bool(series[i] > ref[i] and series[i - 1] <= ref[i - 1])


def _crossunder(series: np.ndarray, ref: np.ndarray, i: int) -> bool:
    if i < 1:
        return False
    return bool(series[i] < ref[i] and series[i - 1] >= ref[i - 1])


def get_signal(df: pd.DataFrame, i: int, params: dict) -> dict:
    """
    Evaluate MA-squeeze signals at bar i.

    Implements identical logic to evaluate_bar_fixed in bitget_bot.strategy.
    Returns dict with keys: long_entry, short_entry, close_long, close_short.
    """
    squeeze_threshold = float(params.get("squeeze_threshold", 0.35))

    m20 = df["m20"].values
    m60 = df["m60"].values
    m120 = df["m120"].values
    e20 = df["e20"].values
    e60 = df["e60"].values
    e120 = df["e120"].values

    _no_signal = {
        "long_entry": False,
        "short_entry": False,
        "close_long": False,
        "close_short": False,
    }

    # Guard: skip bars where any MA hasn't warmed up yet (identical to original backtest loop)
    if any(math.isnan(x) for x in [m20[i], m60[i], m120[i], e20[i], e60[i], e120[i]]):
        return _no_signal

    c = df["close"].astype(float).values
    o = df["open"].astype(float).values
    v = df["volume"].astype(float).values

    # Squeeze check: identical to _squeeze_at(i) in evaluate_bar_fixed
    upper = max(m20[i], m60[i], m120[i], e20[i], e60[i], e120[i])
    lower = min(m20[i], m60[i], m120[i], e20[i], e60[i], e120[i])
    mid = (upper + lower) / 2
    is_squeezed = ((upper - lower) / mid * 100.0 <= squeeze_threshold) if mid > 0 else False

    bull = c > o  # numpy bool array
    bear = c < o  # numpy bool array

    long_signal = False
    short_signal = False

    if i >= 1:
        last_down_vol_prev = _ta_value_when(bear, v, 1, i - 1)
        last_up_vol_prev = _ta_value_when(bull, v, 1, i - 1)
        long_k1_prev = _crossover(c, m20, i - 1) and v[i - 1] > last_down_vol_prev
        short_k1_prev = _crossunder(c, m20, i - 1) and v[i - 1] > last_up_vol_prev
        long_signal = is_squeezed and long_k1_prev and (c[i] > m20[i])
        short_signal = is_squeezed and short_k1_prev and (c[i] < m20[i])

    long_tp = _crossunder(c, m60, i)
    short_tp = _crossover(c, m60, i)

    close_long = (c[i] < m120[i]) or (c[i] >= m120[i] and long_tp)
    close_short = (c[i] > m120[i]) or (c[i] <= m120[i] and short_tp)

    return {
        "long_entry": bool(long_signal),
        "short_entry": bool(short_signal),
        "close_long": bool(close_long),
        "close_short": bool(close_short),
    }
