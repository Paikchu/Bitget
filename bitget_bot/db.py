"""
Persistence layer for the Bitget bot dashboard.

Deliberately isolated: no imports from strategy.py or runner.py.
All public functions are safe to call from any context.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("bitget_bot.db")

# Resolved once at import time; override via DB_PATH env var before importing.
_DB_PATH: Path = Path(os.environ.get("DB_PATH", "data/bot.db"))


# ─────────────────────────────────────────────────────────────
#  Connection helpers
# ─────────────────────────────────────────────────────────────

def _get_conn() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─────────────────────────────────────────────────────────────
#  Schema
# ─────────────────────────────────────────────────────────────

def init_db() -> None:
    """Create tables if they do not exist (idempotent)."""
    with _get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS trades (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol      TEXT    NOT NULL,
                direction   TEXT    NOT NULL,           -- 'long' | 'short'
                entry_time  TEXT    NOT NULL,           -- ISO-8601 UTC
                entry_price REAL    NOT NULL,
                exit_time   TEXT,                       -- NULL while position is open
                exit_price  REAL,
                pnl_pct     REAL,                       -- price move %
                pnl_usdt    REAL,                       -- realised P&L in USDT
                notional    REAL,                       -- notional USDT at entry
                is_dry_run  INTEGER NOT NULL DEFAULT 1, -- 1 = simulated
                created_at  TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS equity_snapshots (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                ts         TEXT    NOT NULL,   -- ISO-8601 UTC of the signal bar
                equity     REAL    NOT NULL,   -- running equity in USDT
                created_at TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS bot_events (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT    NOT NULL,   -- 'signal' | 'order' | 'error' | 'info'
                message    TEXT    NOT NULL,
                payload    TEXT,               -- JSON blob, optional
                created_at TEXT    NOT NULL
            );
        """)
    log.info("Database ready at %s", _DB_PATH)


# ─────────────────────────────────────────────────────────────
#  Writes
# ─────────────────────────────────────────────────────────────

def insert_trade_open(
    symbol: str,
    direction: str,
    entry_time: str,
    entry_price: float,
    notional: float,
    is_dry_run: bool,
) -> int:
    """Insert an open trade record and return its row id."""
    with _get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO trades
               (symbol, direction, entry_time, entry_price, notional, is_dry_run, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (symbol, direction, entry_time, entry_price, notional, int(is_dry_run), _now()),
        )
        return cur.lastrowid  # type: ignore[return-value]


def close_trade(
    trade_id: int,
    exit_time: str,
    exit_price: float,
    pnl_pct: float,
    pnl_usdt: float,
) -> None:
    """Fill exit fields on an existing open trade row."""
    with _get_conn() as conn:
        conn.execute(
            """UPDATE trades
               SET exit_time=?, exit_price=?, pnl_pct=?, pnl_usdt=?
               WHERE id=?""",
            (exit_time, exit_price, pnl_pct, pnl_usdt, trade_id),
        )


def insert_equity(ts: str, equity: float) -> None:
    """Append a single equity snapshot."""
    with _get_conn() as conn:
        conn.execute(
            "INSERT INTO equity_snapshots (ts, equity, created_at) VALUES (?,?,?)",
            (ts, equity, _now()),
        )


def log_event(
    event_type: str,
    message: str,
    payload: Optional[dict] = None,
) -> None:
    """Append an event to the bot_events log."""
    with _get_conn() as conn:
        conn.execute(
            "INSERT INTO bot_events (event_type, message, payload, created_at) VALUES (?,?,?,?)",
            (event_type, message, json.dumps(payload) if payload else None, _now()),
        )


# ─────────────────────────────────────────────────────────────
#  Reads  (used by Phase-2 API; safe to call at any time)
# ─────────────────────────────────────────────────────────────

def get_trades(
    limit: int = 50,
    offset: int = 0,
    direction: Optional[str] = None,
    closed_only: bool = False,
) -> tuple[int, list[dict]]:
    """Return (total_count, list_of_trade_dicts) ordered newest first."""
    conn = _get_conn()
    conditions: list[str] = []
    params: list[Any] = []
    if direction:
        conditions.append("direction = ?")
        params.append(direction)
    if closed_only:
        conditions.append("exit_time IS NOT NULL")
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    total: int = conn.execute(
        f"SELECT COUNT(*) FROM trades {where}", params
    ).fetchone()[0]
    rows = conn.execute(
        f"SELECT * FROM trades {where} ORDER BY id DESC LIMIT ? OFFSET ?",
        params + [limit, offset],
    ).fetchall()
    conn.close()
    return total, [dict(r) for r in rows]


def get_open_trade() -> Optional[dict]:
    """Return the single currently open trade, or None."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM trades WHERE exit_time IS NULL ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_equity_curve(
    days: int = 30,
) -> list[dict]:
    """Return equity snapshots for the last N days, oldest first."""
    conn = _get_conn()
    cutoff = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    from datetime import timedelta
    cutoff = cutoff - timedelta(days=days)
    rows = conn.execute(
        "SELECT ts, equity FROM equity_snapshots WHERE ts >= ? ORDER BY ts ASC",
        (cutoff.isoformat(),),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_events(limit: int = 100) -> list[dict]:
    """Return the most recent bot events."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM bot_events ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_summary_stats() -> dict:
    """Return aggregate statistics over all closed trades."""
    conn = _get_conn()
    row = conn.execute("""
        SELECT
            COUNT(*)                                              AS total_trades,
            SUM(CASE WHEN direction='long'  THEN 1 ELSE 0 END)  AS long_trades,
            SUM(CASE WHEN direction='short' THEN 1 ELSE 0 END)  AS short_trades,
            SUM(CASE WHEN pnl_usdt > 0      THEN 1 ELSE 0 END)  AS wins,
            SUM(CASE WHEN pnl_usdt <= 0     THEN 1 ELSE 0 END)  AS losses,
            SUM(pnl_usdt)                                        AS total_pnl_usdt,
            AVG(CASE WHEN pnl_usdt > 0 THEN pnl_usdt END)       AS avg_win_usdt,
            AVG(CASE WHEN pnl_usdt <= 0 THEN pnl_usdt END)      AS avg_loss_usdt
        FROM trades
        WHERE exit_time IS NOT NULL
    """).fetchone()
    conn.close()
    d = dict(row)
    total = d["total_trades"] or 0
    wins  = d["wins"] or 0
    d["win_rate_pct"] = round(wins / total * 100, 2) if total > 0 else 0.0
    return d
