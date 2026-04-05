"""
Poll Bitget USDT-M perpetuals on 15m (configurable) closed bars and execute
the ported Pine strategy. Bitget has no Pine runtime; this is the practical auto-trade path.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from dotenv import load_dotenv

from bitget_bot.strategy import evaluate_last_closed_bar, ohlcv_to_df

# DB layer is imported lazily so that a missing / broken db module never
# prevents the trading loop from running.
try:
    from bitget_bot import db as _db
    _DB_AVAILABLE = True
except Exception:  # pragma: no cover
    _db = None  # type: ignore[assignment]
    _DB_AVAILABLE = False

log = logging.getLogger("bitget_bot")


def _env_bool(key: str, default: bool) -> bool:
    v = os.environ.get(key)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _env_float(key: str, default: float) -> float:
    v = os.environ.get(key)
    if v is None or v.strip() == "":
        return default
    return float(v)


def _env_int(key: str, default: int) -> int:
    v = os.environ.get(key)
    if v is None or v.strip() == "":
        return default
    return int(v)


def make_exchange(config: Optional[dict[str, Any]] = None) -> Any:
    config = config or {}
    import ccxt

    api_key = _normalize_secret(config.get("BITGET_API_KEY", os.environ.get("BITGET_API_KEY", "")))
    secret = _normalize_secret(config.get("BITGET_API_SECRET", os.environ.get("BITGET_API_SECRET", "")))
    password = _normalize_secret(config.get("BITGET_API_PASSPHRASE", os.environ.get("BITGET_API_PASSPHRASE", "")))

    ex = ccxt.bitget(
        {
            "apiKey": api_key,
            "secret": secret,
            "password": password,
            "options": {"defaultType": "swap"},
            "enableRateLimit": True,
        }
    )
    return ex


def _normalize_secret(value: Any) -> str:
    text = str(value or "").strip()
    placeholder_tokens = {
        "your_api_key_here",
        "your_api_secret_here",
        "your_passphrase_here",
        "changeme",
    }
    return "" if text.lower() in placeholder_tokens else text


def _to_jsonable_primitive(value: Any) -> Any:
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            return value
    return value


def timeframe_ms(ex: Any, timeframe: str) -> int:
    return int(ex.parse_timeframe(timeframe) * 1000)


def fetch_closed_ohlcv(ex: Any, symbol: str, timeframe: str, limit: int = 300) -> list:
    raw = ex.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    tf_ms = timeframe_ms(ex, timeframe)
    now = ex.milliseconds()
    closed = [row for row in raw if row[0] + tf_ms <= now]
    return closed


def signed_contracts(ex: Any, symbol: str, dry_run: bool = False) -> float:
    if dry_run and not (getattr(ex, "apiKey", None) and getattr(ex, "secret", None)):
        return 0.0
    positions = ex.fetch_positions([symbol])
    for p in positions:
        if p.get("symbol") != symbol:
            continue
        c = float(p.get("contracts") or 0)
        if c == 0:
            continue
        side = (p.get("side") or "").lower()
        if side == "short":
            return -c
        return c
    return 0.0


def order_amount_base(
    ex: Any,
    symbol: str,
    price: float,
    margin_usage_pct: float,
    leverage: int,
) -> float:
    """Approximate base size from available USDT swap balance (matches % of equity intent)."""
    bal = ex.fetch_balance({"type": "swap"})
    usdt = bal.get("USDT") or {}
    free = float(usdt.get("free") or 0)
    if free <= 0 or price <= 0:
        return 0.0
    notional = free * (margin_usage_pct / 100.0) * float(leverage)
    amount = notional / price
    market = ex.market(symbol)
    min_amt = market.get("limits", {}).get("amount", {}).get("min")
    if min_amt is not None and amount < float(min_amt):
        return 0.0
    return float(ex.amount_to_precision(symbol, amount))


def market_set_target(
    ex: Any,
    symbol: str,
    target_signed: float,
    margin_usage_pct: float,
    leverage: int,
    dry_run: bool,
) -> None:
    """One-way net position: target_signed > 0 long, < 0 short, 0 flat."""
    ticker = ex.fetch_ticker(symbol)
    price = float(ticker["last"] or ticker["close"] or 0)
    current = signed_contracts(ex, symbol, dry_run=dry_run)
    delta = target_signed - current
    if abs(delta) < 1e-12:
        log.info("No trade: already at target %.8f", target_signed)
        return

    amt = abs(delta)
    amt = float(ex.amount_to_precision(symbol, amt))
    min_amt = ex.market(symbol).get("limits", {}).get("amount", {}).get("min") or 0
    if amt < float(min_amt):
        log.warning("Order size %.8f below minimum %.8f", amt, float(min_amt))
        return

    side = "buy" if delta > 0 else "sell"
    params = {"oneWayMode": True}
    if dry_run:
        log.info(
            "[DRY_RUN] would %s %s %s (target=%.8f current=%.8f)",
            side,
            amt,
            symbol,
            target_signed,
            current,
        )
        return

    ex.create_market_order(symbol, side, amt, params=params)
    log.info("%s %s %s (target=%.8f prev=%.8f)", side, amt, symbol, target_signed, current)


def resolve_pine_target(sig, position_before: float, order_amt: float) -> float:
    """
    Match Pine script order: `strategy.entry` long, then `strategy.entry` short,
    then exit blocks on resulting side/size.
    """
    sim = position_before
    if sig.long_entry:
        sim = order_amt
    if sig.short_entry:
        sim = -order_amt
    if sim > 0 and sig.close_long:
        sim = 0.0
    elif sim < 0 and sig.close_short:
        sim = 0.0
    return sim


# ─────────────────────────────────────────────────────────────
#  DB helper functions — completely isolated from strategy logic.
#  Every function catches its own exceptions so a DB failure
#  never propagates into the trading loop.
# ─────────────────────────────────────────────────────────────

def _db_log_signal(sig: Any, bar_ts_ms: int, symbol: str) -> None:
    """Write a strategy-signal event to bot_events. Never raises."""
    if not _DB_AVAILABLE:
        return
    if not (sig.long_entry or sig.short_entry or sig.close_long or sig.close_short):
        return
    try:
        ts = datetime.fromtimestamp(bar_ts_ms / 1000, tz=timezone.utc).isoformat()
        parts = []
        if sig.long_entry:
            parts.append("LONG_ENTRY")
        if sig.short_entry:
            parts.append("SHORT_ENTRY")
        if sig.close_long:
            parts.append("CLOSE_LONG")
        if sig.close_short:
            parts.append("CLOSE_SHORT")
        _db.log_event(
            "signal",
            f"{symbol} @ {ts}: {' + '.join(parts)}",
            {
                "symbol": symbol,
                "bar_ts": ts,
                "long_entry": bool(_to_jsonable_primitive(sig.long_entry)),
                "short_entry": bool(_to_jsonable_primitive(sig.short_entry)),
                "close_long": bool(_to_jsonable_primitive(sig.close_long)),
                "close_short": bool(_to_jsonable_primitive(sig.close_short)),
                "is_squeezed": bool(_to_jsonable_primitive(sig.is_squeezed)),
            },
        )
    except Exception as exc:
        log.warning("DB signal log skipped: %s", exc)


def _db_record_position_change(
    symbol: str,
    current: float,
    target: float,
    price: float,
    bar_ts_ms: int,
    dry_run: bool,
    db_state: dict,
) -> None:
    """
    Detect open / close / reversal from (current → target) and write to DB.
    Mutates db_state in place. Never raises.

    db_state keys:
        trade_id       int | None   — row id of the currently open trade
        direction      str | None   — 'long' | 'short' | None
        entry_price    float        — entry price of the current trade
        notional       float        — notional USDT at entry
        running_equity float        — cumulative equity (initial + closed PnL)
    """
    if not _DB_AVAILABLE:
        return
    try:
        ts = datetime.fromtimestamp(bar_ts_ms / 1000, tz=timezone.utc).isoformat()

        prev_dir: Optional[str] = db_state.get("direction")
        prev_id: Optional[int] = db_state.get("trade_id")

        new_dir: Optional[str] = None
        if target > 1e-12:
            new_dir = "long"
        elif target < -1e-12:
            new_dir = "short"

        # ── Close existing leg if direction changes or going flat ────────
        if prev_dir is not None and new_dir != prev_dir and prev_id is not None:
            entry_px = db_state.get("entry_price") or price
            entry_notional = db_state.get("notional", 0.0)
            if prev_dir == "long":
                pnl_pct = (price - entry_px) / entry_px * 100.0 if entry_px else 0.0
            else:
                pnl_pct = (entry_px - price) / entry_px * 100.0 if entry_px else 0.0
            pnl_usdt = entry_notional * (pnl_pct / 100.0)

            _db.close_trade(prev_id, ts, price, pnl_pct, pnl_usdt)
            db_state["running_equity"] = db_state.get("running_equity", 0.0) + pnl_usdt
            db_state["trade_id"] = None
            db_state["direction"] = None
            db_state["entry_price"] = 0.0
            db_state["notional"] = 0.0
            _db.log_event(
                "order",
                f"Closed {prev_dir} @ {price:.2f}  PnL {pnl_usdt:+.2f} USDT ({pnl_pct:+.2f}%)",
                {"symbol": symbol, "trade_id": prev_id,
                 "pnl_pct": round(pnl_pct, 4), "pnl_usdt": round(pnl_usdt, 2)},
            )

        # ── Open new leg ─────────────────────────────────────────────────
        if new_dir is not None:
            notional = abs(target) * price if price > 0 else 0.0
            tid = _db.insert_trade_open(symbol, new_dir, ts, price, notional, dry_run)
            db_state["trade_id"] = tid
            db_state["direction"] = new_dir
            db_state["entry_price"] = price
            db_state["notional"] = notional
            _db.log_event(
                "order",
                f"Opened {new_dir} @ {price:.2f}  notional {notional:.2f} USDT",
                {"symbol": symbol, "trade_id": tid,
                 "price": price, "notional": round(notional, 2)},
            )
    except Exception as exc:
        log.warning("DB position record skipped (non-critical): %s", exc)


def _db_snapshot_equity(bar_ts_ms: int, db_state: dict) -> None:
    """Append an equity snapshot. Never raises."""
    if not _DB_AVAILABLE or db_state is None:
        return
    try:
        ts = datetime.fromtimestamp(bar_ts_ms / 1000, tz=timezone.utc).isoformat()
        _db.insert_equity(ts, db_state.get("running_equity", 0.0))
    except Exception as exc:
        log.warning("DB equity snapshot skipped (non-critical): %s", exc)


# ─────────────────────────────────────────────────────────────


def run_cycle(
    ex: Any,
    symbol: str,
    timeframe: str,
    squeeze_threshold: float,
    margin_usage_pct: float,
    leverage: int,
    dry_run: bool,
    db_state: Optional[dict] = None,
) -> None:
    ohlcv = fetch_closed_ohlcv(ex, symbol, timeframe, limit=300)
    if len(ohlcv) < 130:
        log.warning("Not enough closed candles: %s", len(ohlcv))
        return

    df = ohlcv_to_df(ohlcv)
    sig = evaluate_last_closed_bar(
        df,
        squeeze_threshold=squeeze_threshold,
    )
    if sig is None:
        log.warning("Indicators not ready")
        return

    last_ts = ohlcv[-1][0]
    log.info(
        "Bar %s  squeezed=%s  long_in=%s  short_in=%s  close_L=%s  close_S=%s",
        ex.iso8601(last_ts),
        sig.is_squeezed,
        sig.long_entry,
        sig.short_entry,
        sig.close_long,
        sig.close_short,
    )

    # ── Log signal event to DB (non-critical, does not affect trading) ──
    _db_log_signal(sig, last_ts, symbol)

    current = signed_contracts(ex, symbol, dry_run=dry_run)
    order_amt = 0.0
    price = 0.0  # captured here so DB helpers can use it later
    if sig.long_entry or sig.short_entry:
        if dry_run and not (getattr(ex, "apiKey", None) and getattr(ex, "secret", None)):
            log.warning("Entry signal but no API keys; add keys or disable DRY_RUN to size orders")
            return
        ticker = ex.fetch_ticker(symbol)
        price = float(ticker["last"] or ticker["close"] or 0)
        order_amt = order_amount_base(ex, symbol, price, margin_usage_pct, leverage)
        if order_amt <= 0:
            log.warning("Skipping entry: size 0 (check balance / min amount / price)")
            return

    target = resolve_pine_target(sig, current, order_amt)
    if abs(target - current) < 1e-12:
        return

    # For close-only signals price wasn't fetched above; get it now for DB.
    if db_state is not None and _DB_AVAILABLE and price == 0.0:
        try:
            _t = ex.fetch_ticker(symbol)
            price = float(_t.get("last") or _t.get("close") or 0)
        except Exception:
            pass

    market_set_target(ex, symbol, target, margin_usage_pct, leverage, dry_run)

    # ── Record position change to DB (non-critical, does not affect trading) ─
    if db_state is not None:
        _db_record_position_change(symbol, current, target, price, last_ts, dry_run, db_state)


def main() -> None:
    load_dotenv()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    p = argparse.ArgumentParser(description="Bitget bot: MA squeeze + volume confirmation (15m)")
    p.add_argument("--symbol", default=os.environ.get("SYMBOL", "BTC/USDT:USDT"))
    p.add_argument("--timeframe", default=os.environ.get("TIMEFRAME", "15m"))
    p.add_argument("--once", action="store_true", help="Single evaluation then exit")
    p.add_argument("--interval", type=int, default=60, help="Seconds between polls when not --once")
    args = p.parse_args()

    dry_run = _env_bool("DRY_RUN", True)
    squeeze = _env_float("SQUEEZE_THRESHOLD", 0.35)
    lev = _env_int("LEVERAGE", 5)
    margin_pct = _env_float("MARGIN_USAGE_PCT", 100.0)

    ex = make_exchange()
    ex.load_markets()

    if not dry_run:
        missing = [
            k
            for k, v in [
                ("BITGET_API_KEY", os.environ.get("BITGET_API_KEY")),
                ("BITGET_API_SECRET", os.environ.get("BITGET_API_SECRET")),
                ("BITGET_API_PASSPHRASE", os.environ.get("BITGET_API_PASSPHRASE")),
            ]
            if not v
        ]
        if missing:
            log.error("Missing env: %s (or set DRY_RUN=true)", ", ".join(missing))
            sys.exit(1)
        try:
            ex.set_leverage(lev, args.symbol)
        except Exception as e:
            log.warning("set_leverage: %s", e)

    last_bar_ts: Optional[int] = None

    # ── DB initialisation (non-critical) ────────────────────────────────
    initial_equity = _env_float("INITIAL_EQUITY", 10_000.0)
    db_state: Optional[dict] = None
    if _DB_AVAILABLE:
        try:
            _db.init_db()
            db_state = {
                "trade_id": None,        # DB row id of the currently open trade
                "direction": None,       # 'long' | 'short' | None
                "entry_price": 0.0,
                "notional": 0.0,
                "running_equity": initial_equity,
            }
            log.info("DB ready — starting equity %.2f USDT", initial_equity)
        except Exception as _e:
            log.warning("DB init failed, running without persistence: %s", _e)
    # ────────────────────────────────────────────────────────────────────

    while True:
        try:
            ohlcv = fetch_closed_ohlcv(ex, args.symbol, args.timeframe, limit=300)
            bar_ts = ohlcv[-1][0] if ohlcv else None
            new_bar = bar_ts is not None and bar_ts != last_bar_ts
            if args.once:
                run_cycle(
                    ex,
                    args.symbol,
                    args.timeframe,
                    squeeze,
                    margin_pct,
                    lev,
                    dry_run,
                    db_state=db_state,
                )
                if bar_ts is not None:
                    _db_snapshot_equity(bar_ts, db_state)
            elif new_bar:
                last_bar_ts = bar_ts
                run_cycle(
                    ex,
                    args.symbol,
                    args.timeframe,
                    squeeze,
                    margin_pct,
                    lev,
                    dry_run,
                    db_state=db_state,
                )
                _db_snapshot_equity(bar_ts, db_state)
        except Exception as e:
            log.exception("cycle error: %s", e)

        if args.once:
            break
        time.sleep(max(5, args.interval))


def start_loop(
    symbol: str,
    timeframe: str,
    squeeze: float,
    margin_pct: float,
    lev: int,
    dry_run: bool,
    db_state: Optional[dict] = None,
    poll_interval: int = 60,
    config_provider: Optional[Callable[[], dict[str, Any]]] = None,
    on_config_applied: Optional[Callable[[dict[str, Any] | None, str | None], None]] = None,
) -> None:
    """
    Blocking trading loop for programmatic use (e.g. a background thread from api.py).
    All parameters are explicit — no argparse, no env-var side-effects.
    This function never returns; it is designed to run inside a daemon thread.
    """
    def _snapshot_config() -> dict[str, Any]:
        if config_provider is None:
            return {
                "BITGET_API_KEY": os.environ.get("BITGET_API_KEY", ""),
                "BITGET_API_SECRET": os.environ.get("BITGET_API_SECRET", ""),
                "BITGET_API_PASSPHRASE": os.environ.get("BITGET_API_PASSPHRASE", ""),
                "SYMBOL": symbol,
                "TIMEFRAME": timeframe,
                "SQUEEZE_THRESHOLD": squeeze,
                "MARGIN_USAGE_PCT": margin_pct,
                "LEVERAGE": lev,
                "DRY_RUN": dry_run,
                "POLL_INTERVAL": poll_interval,
                "INITIAL_EQUITY": db_state.get("running_equity", 0.0) if db_state else 0.0,
            }
        return config_provider()

    def _build_exchange(config: dict[str, Any]) -> Any:
        ex = make_exchange(config)
        ex.load_markets()
        if not config["DRY_RUN"]:
            try:
                ex.set_leverage(config["LEVERAGE"], config["SYMBOL"])
            except Exception as e:
                log.warning("set_leverage: %s", e)
        return ex

    ex = None
    current_config = _snapshot_config()
    current_signature = None
    last_bar_ts: Optional[int] = None

    while True:
        try:
            pending_config = _snapshot_config()
            pending_signature = (
                pending_config["BITGET_API_KEY"],
                pending_config["BITGET_API_SECRET"],
                pending_config["BITGET_API_PASSPHRASE"],
                pending_config["SYMBOL"],
                pending_config["TIMEFRAME"],
                pending_config["SQUEEZE_THRESHOLD"],
                pending_config["MARGIN_USAGE_PCT"],
                pending_config["LEVERAGE"],
                pending_config["DRY_RUN"],
                pending_config["POLL_INTERVAL"],
            )
            if ex is None or pending_signature != current_signature:
                ex = _build_exchange(pending_config)
                current_config = pending_config
                current_signature = pending_signature
                if on_config_applied is not None:
                    on_config_applied(current_config, None)
                log.info(
                    "start_loop config applied: %s %s lev=%dx dry_run=%s",
                    current_config["SYMBOL"],
                    current_config["TIMEFRAME"],
                    current_config["LEVERAGE"],
                    current_config["DRY_RUN"],
                )

            ohlcv = fetch_closed_ohlcv(ex, current_config["SYMBOL"], current_config["TIMEFRAME"], limit=300)
            bar_ts = ohlcv[-1][0] if ohlcv else None
            new_bar = bar_ts is not None and bar_ts != last_bar_ts
            if new_bar:
                last_bar_ts = bar_ts
                run_cycle(
                    ex,
                    current_config["SYMBOL"],
                    current_config["TIMEFRAME"],
                    current_config["SQUEEZE_THRESHOLD"],
                    current_config["MARGIN_USAGE_PCT"],
                    current_config["LEVERAGE"],
                    current_config["DRY_RUN"],
                    db_state=db_state,
                )
                if db_state is not None and bar_ts is not None:
                    _db_snapshot_equity(bar_ts, db_state)
        except Exception as e:
            log.exception("cycle error: %s", e)
            if on_config_applied is not None:
                on_config_applied(None, str(e))
        interval = current_config["POLL_INTERVAL"] if current_config else poll_interval
        time.sleep(max(5, interval))


if __name__ == "__main__":
    main()
