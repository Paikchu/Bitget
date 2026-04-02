#!/usr/bin/env python3
"""
Backtest: 均线密集 + 量价三K确认 (反手版) on historical 15m OHLCV.

Data source : Bitget public API via ccxt (no API key needed).
Fill model  : next bar's open price (realistic bar-close-signal execution).
Position    : one-way; long/short/flat.  Reversals close old leg then open new.
Sizing      : fixed-fraction of running equity (margin_pct × leverage per trade).
"""

from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import List, Optional

import ccxt
import numpy as np
import pandas as pd

from bitget_bot.strategy import add_indicators, evaluate_bar_fixed


# ──────────────────────────────────────────────
#  Data fetching
# ──────────────────────────────────────────────

def fetch_ohlcv_full(
    symbol: str,
    timeframe: str,
    since_ms: int,
    limit_per_req: int = 200,   # Bitget returns at most 200 candles per request
) -> pd.DataFrame:
    """Paginate through Bitget history and return a clean DataFrame."""
    ex = ccxt.bitget({"options": {"defaultType": "swap"}, "enableRateLimit": True})
    ex.load_markets()
    tf_ms = int(ex.parse_timeframe(timeframe) * 1000)
    now_ms = ex.milliseconds()

    all_rows: list = []
    cur = since_ms
    print(f"Fetching {symbol} {timeframe} data from Bitget …", flush=True)

    while cur < now_ms:
        rows = ex.fetch_ohlcv(symbol, timeframe=timeframe, since=cur, limit=limit_per_req)
        if not rows:
            break
        all_rows.extend(rows)
        last_ts = rows[-1][0]
        print(
            f"  … {len(all_rows)} bars,  last={pd.Timestamp(last_ts, unit='ms', tz='UTC')}",
            end="\r",
            flush=True,
        )
        next_cur = last_ts + tf_ms
        if next_cur <= cur:   # guard against infinite loop
            break
        cur = next_cur

    print(f"\n  Total bars fetched: {len(all_rows)}")
    df = pd.DataFrame(all_rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df = df.drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True)

    # Drop the last (potentially incomplete) bar
    df = df[df["timestamp"] + tf_ms <= now_ms].reset_index(drop=True)
    return df


# ──────────────────────────────────────────────
#  Trade record
# ──────────────────────────────────────────────

@dataclass
class Trade:
    direction: str          # "long" | "short"
    entry_bar: int
    entry_time: pd.Timestamp
    entry_price: float
    exit_bar: Optional[int] = None
    exit_time: Optional[pd.Timestamp] = None
    exit_price: Optional[float] = None
    pnl_pct: Optional[float] = None    # % move in price (×leverage for return on margin)
    pnl_usdt: Optional[float] = None   # realised P&L in USDT after fees (equity delta)
    notional: Optional[float] = None   # notional at entry (= margin × leverage)
    fee_usdt: Optional[float] = None   # total fees paid (entry + exit)


# ──────────────────────────────────────────────
#  Core simulation helpers
# ──────────────────────────────────────────────

def _resolve_target(sig, position: int) -> int:
    """
    Replicate resolve_pine_target logic but with {-1, 0, +1} ints.
    """
    sim = position
    if sig.long_entry:
        sim = 1
    if sig.short_entry:
        sim = -1
    if sim > 0 and sig.close_long:
        sim = 0
    elif sim < 0 and sig.close_short:
        sim = 0
    return sim


def _close_trade(
    trade: Trade,
    exit_bar: int,
    exit_time: pd.Timestamp,
    exit_price: float,
    fee_rate: float = 0.0,
) -> float:
    """Fill exit fields on an open Trade; return the net USDT P&L after fees.

    fee_rate is applied twice (entry + exit), each as a fraction of notional.
    e.g. fee_rate=0.0005 means 0.05% per order side = 0.10% round-trip.
    """
    trade.exit_bar = exit_bar
    trade.exit_time = exit_time
    trade.exit_price = exit_price
    if trade.direction == "long":
        ret = (exit_price - trade.entry_price) / trade.entry_price
    else:
        ret = (trade.entry_price - exit_price) / trade.entry_price
    trade.pnl_pct = ret * 100.0
    gross_pnl = trade.notional * ret
    fees = trade.notional * fee_rate * 2  # entry + exit fees
    trade.fee_usdt = round(fees, 6)
    trade.pnl_usdt = gross_pnl - fees
    return trade.pnl_usdt


# ──────────────────────────────────────────────
#  Main backtest engine
# ──────────────────────────────────────────────

def run_backtest(
    df: pd.DataFrame,
    squeeze_threshold: float = 0.35,
    initial_equity: float = 10_000.0,
    leverage: int = 5,
    margin_pct: float = 100.0,
    fee_rate: float = 0.0,
) -> tuple[List[Trade], pd.Series]:
    """
    Bar-by-bar backtest.

    Signal on bar i  →  fill at bar i+1 open.
    Returns (list_of_trades, equity_curve_as_Series indexed by bar timestamp).
    """
    d = add_indicators(df)

    o   = d["open"].astype(float).values
    c   = d["close"].astype(float).values
    v   = d["volume"].astype(float).values
    m20 = d["m20"].astype(float).values
    m60 = d["m60"].astype(float).values
    m120= d["m120"].astype(float).values
    e20 = d["e20"].astype(float).values
    e60 = d["e60"].astype(float).values
    e120= d["e120"].astype(float).values
    times = pd.to_datetime(d["timestamp"], unit="ms", utc=True)

    n = len(d)
    equity    = initial_equity
    position  = 0           # -1 | 0 | +1
    entry_price = 0.0
    trades: List[Trade] = []
    equity_arr = np.empty(n)
    equity_arr[:] = np.nan
    equity_arr[0] = equity

    # Need 120 bars for m120, and at least i+1 for the fill bar
    for i in range(120, n - 1):
        if any(math.isnan(x) for x in [m20[i], m60[i], m120[i], e20[i], e60[i], e120[i]]):
            equity_arr[i] = equity
            continue

        sig = evaluate_bar_fixed(
            o, c, v, m20, m60, m120, e20, e60, e120,
            i, squeeze_threshold,
        )

        new_pos = _resolve_target(sig, position)
        fill_price = float(o[i + 1])
        fill_bar   = i + 1
        fill_time  = times.iloc[i + 1]

        # ── Close existing leg if position is changing ──────────────────
        if position != 0 and new_pos != position:
            pnl = _close_trade(trades[-1], fill_bar, fill_time, fill_price, fee_rate)
            equity += pnl
            position = 0

        # ── Open new leg ─────────────────────────────────────────────────
        if new_pos != 0 and new_pos != position:
            notional = equity * (margin_pct / 100.0) * leverage
            direction = "long" if new_pos == 1 else "short"
            trades.append(Trade(
                direction=direction,
                entry_bar=fill_bar,
                entry_time=fill_time,
                entry_price=fill_price,
                notional=notional,
            ))
            position = new_pos
            entry_price = fill_price

        equity_arr[i + 1] = equity

    # ── Force-close any open trade at the last bar ───────────────────────
    if position != 0 and trades and trades[-1].exit_price is None:
        last_bar  = n - 1
        last_time = times.iloc[last_bar]
        last_price = float(c[last_bar])
        pnl = _close_trade(trades[-1], last_bar, last_time, last_price, fee_rate)
        equity += pnl

    # Forward-fill equity_arr for bars with no trade activity
    last_eq = initial_equity
    for i in range(n):
        if math.isnan(equity_arr[i]):
            equity_arr[i] = last_eq
        else:
            last_eq = equity_arr[i]

    eq_series = pd.Series(equity_arr, index=times, name="equity")
    return trades, eq_series


# ──────────────────────────────────────────────
#  Reporting
# ──────────────────────────────────────────────

def _safe_pf(wins: list, losses: list) -> str:
    if not losses or sum(losses) == 0:
        return "∞"
    return f"{sum(wins) / abs(sum(losses)):.2f}"


def print_report(
    trades: List[Trade],
    equity_series: pd.Series,
    initial_equity: float,
) -> None:
    closed = [t for t in trades if t.pnl_usdt is not None]

    if not closed:
        print("\nNo closed trades in the backtest window.")
        return

    pnls  = [t.pnl_usdt for t in closed]
    wins  = [p for p in pnls if p > 0]
    losses= [p for p in pnls if p <= 0]

    total_pnl     = sum(pnls)
    final_equity  = initial_equity + total_pnl
    total_ret_pct = total_pnl / initial_equity * 100.0
    win_rate      = len(wins) / len(closed) * 100.0

    eq = equity_series.values
    peak    = np.maximum.accumulate(eq)
    dd_pct  = (eq - peak) / np.where(peak > 0, peak, 1) * 100.0
    max_dd  = float(np.min(dd_pct))

    longs  = [t for t in closed if t.direction == "long"]
    shorts = [t for t in closed if t.direction == "short"]

    sep = "─" * 62

    print(f"\n{sep}")
    print(f"  BACKTEST RESULTS  —  {equity_series.index[0].strftime('%Y-%m-%d')} → {equity_series.index[-1].strftime('%Y-%m-%d')}")
    print(sep)
    print(f"  Total closed trades : {len(closed)}")
    print(f"  Long / Short        : {len(longs)} / {len(shorts)}")
    print(f"  Win rate            : {win_rate:.1f}%  ({len(wins)}W / {len(losses)}L)")
    print(f"  Profit factor       : {_safe_pf(wins, losses)}")
    print(f"  Total PnL           : ${total_pnl:+,.2f}  ({total_ret_pct:+.2f}%)")
    print(f"  Initial equity      : ${initial_equity:,.2f}")
    print(f"  Final equity        : ${final_equity:,.2f}")
    print(f"  Avg win             : ${(sum(wins)/len(wins) if wins else 0):+,.2f}")
    print(f"  Avg loss            : ${(sum(losses)/len(losses) if losses else 0):+,.2f}")
    print(f"  Max drawdown        : {max_dd:.2f}%")

    if longs:
        lw  = len([t for t in longs if t.pnl_usdt > 0])
        lpnl= sum(t.pnl_usdt for t in longs)
        print(f"  Long  trades        : {len(longs):3d}  WR={lw/len(longs)*100:.1f}%  PnL=${lpnl:+,.2f}")
    if shorts:
        sw  = len([t for t in shorts if t.pnl_usdt > 0])
        spnl= sum(t.pnl_usdt for t in shorts)
        print(f"  Short trades        : {len(shorts):3d}  WR={sw/len(shorts)*100:.1f}%  PnL=${spnl:+,.2f}")

    print(sep)

    # ── Per-trade table (most recent 30) ─────────────────────────────────
    display = closed[-30:]
    offset  = max(0, len(closed) - 30) + 1
    print(f"\n{'#':>4}  {'Dir':>5}  {'Entry (UTC)':>17}  {'Entry$':>9}  {'Exit$':>9}  {'PnL%':>7}  {'PnL$':>10}")
    print("─" * 72)
    for k, t in enumerate(display, start=offset):
        et = t.entry_time.strftime("%Y-%m-%d %H:%M") if t.entry_time else "?"
        print(
            f"{k:>4}  {t.direction:>5}  {et:>17}  "
            f"{t.entry_price:>9.2f}  {t.exit_price:>9.2f}  "
            f"{t.pnl_pct:>+7.2f}%  {t.pnl_usdt:>+10.2f}"
        )
    if len(closed) > 30:
        print(f"  … ({len(closed) - 30} earlier trades not shown; use --out-csv to export all)")


# ──────────────────────────────────────────────
#  CLI
# ──────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser(
        description="Backtest MA-squeeze + volume-confirm strategy on Bitget 15m data",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--symbol",     default="BTC/USDT:USDT", help="Bitget swap symbol")
    p.add_argument("--days",       type=int,   default=90,    help="History lookback in days")
    p.add_argument("--squeeze",    type=float, default=0.35,  help="MA squeeze threshold %%")
    p.add_argument("--equity",     type=float, default=10_000, help="Starting equity (USDT)")
    p.add_argument("--leverage",   type=int,   default=5,     help="Futures leverage")
    p.add_argument("--margin-pct", type=float, default=100.0, help="Equity%% used as margin per trade")
    p.add_argument("--fee-rate",   type=float, default=0.0005, help="Taker fee per order side (0.0005 = 0.05%%)")
    p.add_argument("--out-csv",    default="",                help="Save all trades to this CSV path")
    args = p.parse_args()

    since_ms = int((datetime.now(timezone.utc) - timedelta(days=args.days)).timestamp() * 1000)
    df = fetch_ohlcv_full(args.symbol, "15m", since_ms)

    if len(df) < 130:
        print(f"ERROR: only {len(df)} closed bars — need ≥ 130 to warm up indicators.")
        sys.exit(1)

    first = pd.Timestamp(df["timestamp"].iloc[0],  unit="ms", tz="UTC")
    last  = pd.Timestamp(df["timestamp"].iloc[-1], unit="ms", tz="UTC")
    print(
        f"Backtesting {len(df)} bars  "
        f"({first.strftime('%Y-%m-%d')} → {last.strftime('%Y-%m-%d')})  "
        f"squeeze={args.squeeze}  leverage={args.leverage}x  margin={args.margin_pct}%"
    )

    trades, equity_series = run_backtest(
        df,
        squeeze_threshold=args.squeeze,
        initial_equity=args.equity,
        leverage=args.leverage,
        margin_pct=args.margin_pct,
        fee_rate=args.fee_rate,
    )

    print_report(trades, equity_series, args.equity)

    if args.out_csv:
        rows = [
            {
                "direction":   t.direction,
                "entry_time":  t.entry_time,
                "entry_price": t.entry_price,
                "exit_time":   t.exit_time,
                "exit_price":  t.exit_price,
                "pnl_pct":     t.pnl_pct,
                "pnl_usdt":    t.pnl_usdt,
                "notional":    t.notional,
            }
            for t in trades
            if t.pnl_usdt is not None
        ]
        pd.DataFrame(rows).to_csv(args.out_csv, index=False)
        print(f"\nTrades saved → {args.out_csv}")


if __name__ == "__main__":
    main()
