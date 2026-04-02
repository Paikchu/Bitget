"""
Pine v5 strategy port: 均线密集 + 量价三K确认 (反手版)
Evaluates only on fully closed 15m bars (bar close semantics).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd


def ta_value_when(
    condition: np.ndarray,
    source: np.ndarray,
    occurrence: int,
    at: int,
) -> float:
    """Pine ta.valuewhen: occurrence 0 = most recent true bar, 1 = second most recent, etc."""
    c = condition[: at + 1]
    s = source[: at + 1]
    idxs = np.flatnonzero(c)
    if len(idxs) <= occurrence:
        return float("nan")
    bar = idxs[-(occurrence + 1)]
    return float(s[bar])


def crossover(series: np.ndarray, ref: np.ndarray, i: int) -> bool:
    if i < 1:
        return False
    return series[i] > ref[i] and series[i - 1] <= ref[i - 1]


def crossunder(series: np.ndarray, ref: np.ndarray, i: int) -> bool:
    if i < 1:
        return False
    return series[i] < ref[i] and series[i - 1] >= ref[i - 1]


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Expects columns: open, high, low, close, volume; index or column timestamp ms optional."""
    o = df["open"].astype(float)
    c = df["close"].astype(float)
    v = df["volume"].astype(float)
    out = df.copy()
    out["m20"] = c.rolling(20, min_periods=20).mean()
    out["m60"] = c.rolling(60, min_periods=60).mean()
    out["m120"] = c.rolling(120, min_periods=120).mean()
    out["e20"] = c.ewm(span=20, adjust=False).mean()
    out["e60"] = c.ewm(span=60, adjust=False).mean()
    out["e120"] = c.ewm(span=120, adjust=False).mean()
    ma_cols = ["m20", "m60", "m120", "e20", "e60", "e120"]
    out["upper_line"] = out[ma_cols].max(axis=1)
    out["lower_line"] = out[ma_cols].min(axis=1)
    mid = (out["upper_line"] + out["lower_line"]) / 2
    out["gap_percent"] = np.where(
        mid > 0,
        (out["upper_line"] - out["lower_line"]) / mid * 100.0,
        np.nan,
    )
    return out


@dataclass
class BarSignals:
    long_entry: bool
    short_entry: bool
    close_long: bool
    close_short: bool
    long_tp_confirm: bool
    short_tp_confirm: bool
    is_squeezed: bool


def evaluate_bar_fixed(
    o: np.ndarray,
    c: np.ndarray,
    v: np.ndarray,
    m20: np.ndarray,
    m60: np.ndarray,
    m120: np.ndarray,
    e20: np.ndarray,
    e60: np.ndarray,
    e120: np.ndarray,
    i: int,
    squeeze_threshold: float,
) -> BarSignals:
    def _squeeze_at(idx: int) -> bool:
        upper = max(m20[idx], m60[idx], m120[idx], e20[idx], e60[idx], e120[idx])
        lower = min(m20[idx], m60[idx], m120[idx], e20[idx], e60[idx], e120[idx])
        mid = (upper + lower) / 2
        if mid <= 0:
            return False
        gap_pct = (upper - lower) / mid * 100.0
        return gap_pct <= squeeze_threshold

    is_squeezed = _squeeze_at(i)

    bull = c > o
    bear = c < o

    if i < 1:
        long_signal = short_signal = False
    else:
        last_down_vol_prev = ta_value_when(bear, v, 1, i - 1)
        last_up_vol_prev = ta_value_when(bull, v, 1, i - 1)
        long_k1_prev = crossover(c, m20, i - 1) and v[i - 1] > last_down_vol_prev
        short_k1_prev = crossunder(c, m20, i - 1) and v[i - 1] > last_up_vol_prev
        long_signal = is_squeezed and long_k1_prev and (c[i] > m20[i])
        short_signal = is_squeezed and short_k1_prev and (c[i] < m20[i])

    long_tp_confirm = crossunder(c, m60, i)
    short_tp_confirm = crossover(c, m60, i)

    # Pine: if close < m120 stop; else if crossunder m60 TP (long). Short symmetric.
    close_long = (c[i] < m120[i]) or (c[i] >= m120[i] and long_tp_confirm)
    close_short = (c[i] > m120[i]) or (c[i] <= m120[i] and short_tp_confirm)

    return BarSignals(
        long_entry=long_signal,
        short_entry=short_signal,
        close_long=close_long,
        close_short=close_short,
        long_tp_confirm=long_tp_confirm,
        short_tp_confirm=short_tp_confirm,
        is_squeezed=is_squeezed,
    )


def evaluate_last_closed_bar(
    df: pd.DataFrame,
    squeeze_threshold: float = 0.35,
) -> Optional[BarSignals]:
    """
    df: must include open, close, volume, and timestamp ms in index or 'timestamp' column.
    Last row = last fully closed bar.
    """
    need = {"open", "high", "low", "close", "volume"}
    if not need.issubset(df.columns):
        raise ValueError(f"df must have columns {need}")

    d = add_indicators(df)
    i = len(d) - 1
    if i < 120 or pd.isna(d["m120"].iloc[i]):
        return None

    o = d["open"].astype(float).values
    c = d["close"].astype(float).values
    v = d["volume"].astype(float).values
    m20 = d["m20"].astype(float).values
    m60 = d["m60"].astype(float).values
    m120 = d["m120"].astype(float).values
    e20 = d["e20"].astype(float).values
    e60 = d["e60"].astype(float).values
    e120 = d["e120"].astype(float).values

    return evaluate_bar_fixed(
        o, c, v, m20, m60, m120, e20, e60, e120, i, squeeze_threshold
    )


def ohlcv_to_df(ohlcv: list) -> pd.DataFrame:
    """CCXT ohlcv rows: [timestamp, open, high, low, close, volume]."""
    df = pd.DataFrame(
        ohlcv,
        columns=["timestamp", "open", "high", "low", "close", "volume"],
    )
    return df
