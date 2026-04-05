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

            CREATE TABLE IF NOT EXISTS strategy_versions (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                version_no        INTEGER NOT NULL UNIQUE,
                source            TEXT    NOT NULL,
                title             TEXT,
                markdown          TEXT    NOT NULL,
                code              TEXT    NOT NULL,
                model             TEXT,
                parent_version_id INTEGER,
                created_at        TEXT    NOT NULL,
                CHECK (source IN ('generate', 'builtin_import', 'restore', 'fix'))
            );

            CREATE TABLE IF NOT EXISTS strategy_version_backtests (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy_version_id INTEGER NOT NULL,
                job_id              TEXT    NOT NULL,
                summary_json        TEXT    NOT NULL,
                created_at          TEXT    NOT NULL,
                FOREIGN KEY (strategy_version_id) REFERENCES strategy_versions(id)
            );

            CREATE TABLE IF NOT EXISTS strategy_experiments (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy_version_id INTEGER,
                strategy_code       TEXT    NOT NULL,
                job_id              TEXT    NOT NULL,
                status              TEXT    NOT NULL,
                config_json         TEXT    NOT NULL,
                scenario_summary_json TEXT  NOT NULL,
                aggregate_summary_json TEXT,
                error               TEXT,
                created_at          TEXT    NOT NULL,
                updated_at          TEXT    NOT NULL,
                FOREIGN KEY (strategy_version_id) REFERENCES strategy_versions(id)
            );

            CREATE TABLE IF NOT EXISTS strategy_experiment_runs (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                experiment_id  INTEGER NOT NULL,
                run_key        TEXT    NOT NULL,
                params_json    TEXT    NOT NULL,
                scenario_tag   TEXT    NOT NULL,
                result_json    TEXT    NOT NULL,
                created_at     TEXT    NOT NULL,
                FOREIGN KEY (experiment_id) REFERENCES strategy_experiments(id)
            );

            CREATE TABLE IF NOT EXISTS strategy_experiment_feedback (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                experiment_id  INTEGER NOT NULL UNIQUE,
                feedback_json  TEXT    NOT NULL,
                prompt_version TEXT    NOT NULL,
                schema_version TEXT    NOT NULL,
                model          TEXT,
                created_at     TEXT    NOT NULL,
                updated_at     TEXT    NOT NULL,
                FOREIGN KEY (experiment_id) REFERENCES strategy_experiments(id)
            );
        """)
        ensure_default_strategy_version(conn)
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


def _row_to_dict(row: sqlite3.Row | None) -> Optional[dict]:
    return dict(row) if row else None


def _extract_title(markdown: str) -> Optional[str]:
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip() or None
    return None


def get_next_strategy_version_no(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COALESCE(MAX(version_no), 0) + 1 AS next_no FROM strategy_versions").fetchone()
    return int(row["next_no"])


def ensure_default_strategy_version(conn: sqlite3.Connection) -> None:
    row = conn.execute("SELECT COUNT(*) AS count FROM strategy_versions").fetchone()
    if int(row["count"]) != 0:
        return

    from bitget_bot.strategies.load_default import BUILTIN_STRATEGIES

    default_strategy = BUILTIN_STRATEGIES["ma_squeeze"]
    conn.execute(
        """
        INSERT INTO strategy_versions
        (version_no, source, title, markdown, code, model, parent_version_id, created_at)
        VALUES (?,?,?,?,?,?,?,?)
        """,
        (
            1,
            "builtin_import",
            _extract_title(default_strategy["markdown"]) or default_strategy["name"],
            default_strategy["markdown"],
            default_strategy["code"],
            None,
            None,
            _now(),
        ),
    )


def create_strategy_version(
    markdown: str,
    code: str,
    source: str,
    model: Optional[str] = None,
    parent_version_id: Optional[int] = None,
) -> dict:
    with _get_conn() as conn:
        version_no = get_next_strategy_version_no(conn)
        created_at = _now()
        cur = conn.execute(
            """
            INSERT INTO strategy_versions
            (version_no, source, title, markdown, code, model, parent_version_id, created_at)
            VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                version_no,
                source,
                _extract_title(markdown),
                markdown,
                code,
                model,
                parent_version_id,
                created_at,
            ),
        )
        version_id = cur.lastrowid
        row = conn.execute("SELECT * FROM strategy_versions WHERE id = ?", (version_id,)).fetchone()
    return _hydrate_strategy_version(dict(row))


def list_strategy_versions(limit: int = 50, offset: int = 0) -> list[dict]:
    with _get_conn() as conn:
        rows = conn.execute(
            """
            SELECT
                v.*,
                b.summary_json AS latest_backtest_summary_json,
                b.created_at AS latest_backtest_at
            FROM strategy_versions v
            LEFT JOIN strategy_version_backtests b
              ON b.id = (
                SELECT sb.id
                FROM strategy_version_backtests sb
                WHERE sb.strategy_version_id = v.id
                ORDER BY sb.id DESC
                LIMIT 1
              )
            ORDER BY v.version_no DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        ).fetchall()
    return [_hydrate_strategy_version(dict(row)) for row in rows]


def get_strategy_version(version_id: int) -> Optional[dict]:
    with _get_conn() as conn:
        row = conn.execute(
            """
            SELECT
                v.*,
                b.summary_json AS latest_backtest_summary_json,
                b.created_at AS latest_backtest_at
            FROM strategy_versions v
            LEFT JOIN strategy_version_backtests b
              ON b.id = (
                SELECT sb.id
                FROM strategy_version_backtests sb
                WHERE sb.strategy_version_id = v.id
                ORDER BY sb.id DESC
                LIMIT 1
              )
            WHERE v.id = ?
            """,
            (version_id,),
        ).fetchone()
    return _hydrate_strategy_version(dict(row)) if row else None


def get_latest_strategy_version() -> Optional[dict]:
    with _get_conn() as conn:
        row = conn.execute(
            """
            SELECT
                v.*,
                b.summary_json AS latest_backtest_summary_json,
                b.created_at AS latest_backtest_at
            FROM strategy_versions v
            LEFT JOIN strategy_version_backtests b
              ON b.id = (
                SELECT sb.id
                FROM strategy_version_backtests sb
                WHERE sb.strategy_version_id = v.id
                ORDER BY sb.id DESC
                LIMIT 1
              )
            ORDER BY v.version_no DESC
            LIMIT 1
            """
        ).fetchone()
    return _hydrate_strategy_version(dict(row)) if row else None


def record_strategy_version_backtest(
    strategy_version_id: int,
    job_id: str,
    summary: dict,
) -> dict:
    with _get_conn() as conn:
        created_at = _now()
        cur = conn.execute(
            """
            INSERT INTO strategy_version_backtests
            (strategy_version_id, job_id, summary_json, created_at)
            VALUES (?,?,?,?)
            """,
            (strategy_version_id, job_id, json.dumps(summary), created_at),
        )
        row = conn.execute(
            "SELECT * FROM strategy_version_backtests WHERE id = ?",
            (cur.lastrowid,),
        ).fetchone()
    out = dict(row)
    out["summary"] = json.loads(out.pop("summary_json"))
    return out


def _hydrate_strategy_version(version: dict) -> dict:
    summary_json = version.pop("latest_backtest_summary_json", None)
    version["latest_backtest_summary"] = json.loads(summary_json) if summary_json else None
    version["latest_backtest_at"] = version.get("latest_backtest_at")
    return version


def create_strategy_experiment(
    strategy_code: str,
    config: dict,
    scenario_summary: list[str],
    job_id: str,
    strategy_version_id: Optional[int] = None,
) -> dict:
    with _get_conn() as conn:
        created_at = _now()
        cur = conn.execute(
            """
            INSERT INTO strategy_experiments
            (strategy_version_id, strategy_code, job_id, status, config_json, scenario_summary_json, aggregate_summary_json, error, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (
                strategy_version_id,
                strategy_code,
                job_id,
                "running",
                json.dumps(config),
                json.dumps(scenario_summary),
                None,
                None,
                created_at,
                created_at,
            ),
        )
        row = conn.execute("SELECT * FROM strategy_experiments WHERE id = ?", (cur.lastrowid,)).fetchone()
    return _hydrate_strategy_experiment(dict(row))


def add_strategy_experiment_run(
    experiment_id: int,
    run_key: str,
    params: dict,
    scenario_tag: str,
    result: dict,
) -> dict:
    with _get_conn() as conn:
        created_at = _now()
        cur = conn.execute(
            """
            INSERT INTO strategy_experiment_runs
            (experiment_id, run_key, params_json, scenario_tag, result_json, created_at)
            VALUES (?,?,?,?,?,?)
            """,
            (experiment_id, run_key, json.dumps(params), scenario_tag, json.dumps(result), created_at),
        )
        row = conn.execute("SELECT * FROM strategy_experiment_runs WHERE id = ?", (cur.lastrowid,)).fetchone()
    return _hydrate_strategy_experiment_run(dict(row))


def update_strategy_experiment_status(
    experiment_id: int,
    status: str,
    aggregate_summary: Optional[dict] = None,
    error: Optional[str] = None,
) -> dict:
    with _get_conn() as conn:
        conn.execute(
            """
            UPDATE strategy_experiments
            SET status = ?, aggregate_summary_json = ?, error = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                status,
                json.dumps(aggregate_summary) if aggregate_summary is not None else None,
                error,
                _now(),
                experiment_id,
            ),
        )
        row = conn.execute("SELECT * FROM strategy_experiments WHERE id = ?", (experiment_id,)).fetchone()
    return _hydrate_strategy_experiment(dict(row))


def get_strategy_experiment(experiment_id: int) -> Optional[dict]:
    with _get_conn() as conn:
        row = conn.execute("SELECT * FROM strategy_experiments WHERE id = ?", (experiment_id,)).fetchone()
    return _hydrate_strategy_experiment(dict(row)) if row else None


def list_strategy_experiment_runs(experiment_id: int) -> list[dict]:
    with _get_conn() as conn:
        rows = conn.execute(
            """
            SELECT * FROM strategy_experiment_runs
            WHERE experiment_id = ?
            ORDER BY id ASC
            """,
            (experiment_id,),
        ).fetchall()
    return [_hydrate_strategy_experiment_run(dict(row)) for row in rows]


def save_strategy_experiment_feedback(
    experiment_id: int,
    feedback: dict,
    prompt_version: str,
    schema_version: str,
    model: Optional[str],
) -> dict:
    with _get_conn() as conn:
        created_at = _now()
        conn.execute(
            """
            INSERT INTO strategy_experiment_feedback
            (experiment_id, feedback_json, prompt_version, schema_version, model, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(experiment_id) DO UPDATE SET
                feedback_json = excluded.feedback_json,
                prompt_version = excluded.prompt_version,
                schema_version = excluded.schema_version,
                model = excluded.model,
                updated_at = excluded.updated_at
            """,
            (
                experiment_id,
                json.dumps(feedback),
                prompt_version,
                schema_version,
                model,
                created_at,
                created_at,
            ),
        )
        row = conn.execute(
            "SELECT * FROM strategy_experiment_feedback WHERE experiment_id = ?",
            (experiment_id,),
        ).fetchone()
    return _hydrate_strategy_experiment_feedback(dict(row))


def get_strategy_experiment_feedback(experiment_id: int) -> Optional[dict]:
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM strategy_experiment_feedback WHERE experiment_id = ?",
            (experiment_id,),
        ).fetchone()
    return _hydrate_strategy_experiment_feedback(dict(row)) if row else None


def _hydrate_strategy_experiment(row: dict) -> dict:
    row["config"] = json.loads(row.pop("config_json"))
    row["scenario_summary"] = json.loads(row.pop("scenario_summary_json"))
    aggregate_summary_json = row.pop("aggregate_summary_json", None)
    row["aggregate_summary"] = json.loads(aggregate_summary_json) if aggregate_summary_json else None
    return row


def _hydrate_strategy_experiment_run(row: dict) -> dict:
    row["params"] = json.loads(row.pop("params_json"))
    row["result"] = json.loads(row.pop("result_json"))
    return row


def _hydrate_strategy_experiment_feedback(row: dict) -> dict:
    row["feedback"] = json.loads(row.pop("feedback_json"))
    return row


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


def get_events_since(last_id: int) -> list[dict]:
    """Return events with id > last_id, oldest first. Used by the WS broadcaster."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM bot_events WHERE id > ? ORDER BY id ASC LIMIT 100",
        (last_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_latest_equity() -> Optional[dict]:
    """Return the most recent equity snapshot, or None."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM equity_snapshots ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_trade_by_id(trade_id: int) -> Optional[dict]:
    """Fetch a single trade row by primary key."""
    conn = _get_conn()
    row = conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


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
