"""
FastAPI backend for the Bitget bot dashboard.

Start:
    uvicorn bitget_bot.api:app --host 0.0.0.0 --port 8080

At startup the trading loop is launched in a daemon thread automatically.
All REST endpoints and the WebSocket are served from the same process.

REST
  GET  /api/status             bot state + current position
  GET  /api/trades             paginated trade history
  GET  /api/equity             equity curve
  GET  /api/ohlcv              K-line data (Bitget public API, no key needed)
  GET  /api/events             recent bot events log
  POST /api/backtest           trigger async backtest job
  GET  /api/backtest/{job_id}  poll job result

WebSocket
  /ws  real-time stream of trade_open / trade_close / signal / equity_update

Static
  /    frontend build (Phase 3 — only mounted when frontend/dist/ exists)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import ccxt
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from bitget_bot import db as _db
from bitget_bot.runner import start_loop
from bitget_bot.settings_manager import SettingsValidationError
from bitget_bot.settings_manager import load_runtime_settings
from bitget_bot.settings_manager import load_settings_snapshot
from bitget_bot.settings_manager import save_settings
from bitget_bot.settings_manager import test_settings_connections
from bitget_bot.strategy_router import router as strategy_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("bitget_bot.api")


# ─────────────────────────────────────────────────────────────
#  Env helpers (duplicated from runner to avoid importing privates)
# ─────────────────────────────────────────────────────────────

def _bool(key: str, default: bool) -> bool:
    v = os.environ.get(key)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "on")


def _float(key: str, default: float) -> float:
    v = os.environ.get(key)
    return default if not v or not v.strip() else float(v)


def _int(key: str, default: int) -> int:
    v = os.environ.get(key)
    return default if not v or not v.strip() else int(v)


# ─────────────────────────────────────────────────────────────
#  Runtime state
# ─────────────────────────────────────────────────────────────

_bot_config: dict = {}
_bot_running = threading.Event()
_runtime_lock = threading.Lock()
_runtime_config: dict = {
    "settings": {},
    "version": 0,
    "applied_version": 0,
    "last_apply_error": "",
}

# Active WebSocket connections — only touched by async coroutines (no lock needed)
_ws_clients: list[WebSocket] = []

# In-memory backtest results (reset on restart)
_backtest_jobs: dict[str, dict] = {}


# ─────────────────────────────────────────────────────────────
#  Bot daemon thread
# ─────────────────────────────────────────────────────────────

def _bot_thread(db_state: dict) -> None:
    """Entry point for the background trading loop thread."""
    try:
        _bot_running.set()
        start_loop(
            symbol=_bot_config["symbol"],
            timeframe=_bot_config["timeframe"],
            squeeze=_bot_config["squeeze"],
            margin_pct=_bot_config["margin_pct"],
            lev=_bot_config["lev"],
            dry_run=_bot_config["dry_run"],
            db_state=db_state,
            poll_interval=_bot_config["poll_interval"],
            config_provider=_bot_runtime_provider,
            on_config_applied=_mark_config_applied,
        )
    except Exception:
        log.exception("Bot thread crashed")
    finally:
        _bot_running.clear()


# ─────────────────────────────────────────────────────────────
#  WebSocket broadcaster
#
#  Design: polls the DB every second for new bot_events rows and
#  equity snapshots, then pushes them to all connected clients.
#  No shared state needed between the runner thread and this coroutine.
# ─────────────────────────────────────────────────────────────

async def _broadcast(msg: dict) -> None:
    """Send msg to every WS client; silently remove dead connections."""
    dead: list[WebSocket] = []
    for ws in list(_ws_clients):
        try:
            await ws.send_json(msg)
        except Exception:
            dead.append(ws)
    for ws in dead:
        if ws in _ws_clients:
            _ws_clients.remove(ws)


async def _ws_broadcaster() -> None:
    last_event_id = 0
    last_equity_ts = ""

    while True:
        await asyncio.sleep(1)
        if not _ws_clients:
            continue
        try:
            # ── New bot_events ─────────────────────────────────────
            for ev in _db.get_events_since(last_event_id):
                last_event_id = ev["id"]
                payload = json.loads(ev["payload"]) if ev.get("payload") else {}

                if ev["event_type"] == "order":
                    # Attach full trade data so the frontend can update its table
                    trade_id = payload.get("trade_id")
                    trade = _db.get_trade_by_id(trade_id) if trade_id else None
                    if trade:
                        msg_type = "trade_close" if trade.get("exit_time") else "trade_open"
                        await _broadcast({"type": msg_type, "data": trade})
                else:
                    await _broadcast({
                        "type": ev["event_type"],
                        "data": {"message": ev["message"], **payload},
                        "ts": ev["created_at"],
                    })

            # ── Equity snapshot ────────────────────────────────────
            eq = _db.get_latest_equity()
            if eq and eq["ts"] != last_equity_ts:
                last_equity_ts = eq["ts"]
                await _broadcast({"type": "equity_update", "data": eq})

        except Exception as exc:
            log.debug("Broadcaster error: %s", exc)


# ─────────────────────────────────────────────────────────────
#  Lifespan
# ─────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    load_dotenv()
    _db.init_db()
    runtime_settings = load_runtime_settings(_settings_env_path())
    with _runtime_lock:
        _runtime_config["settings"] = runtime_settings.copy()
        _runtime_config["version"] = 1
        _runtime_config["applied_version"] = 0
        _runtime_config["last_apply_error"] = ""
    _sync_bot_config(runtime_settings)

    db_state: dict = {
        "trade_id": None,
        "direction": None,
        "entry_price": 0.0,
        "notional": 0.0,
        "running_equity": runtime_settings["INITIAL_EQUITY"],
    }

    threading.Thread(
        target=_bot_thread, args=(db_state,), daemon=True, name="bot-loop"
    ).start()
    log.info(
        "Bot thread started — %s %s dry_run=%s",
        _bot_config["symbol"], _bot_config["timeframe"], _bot_config["dry_run"],
    )

    asyncio.create_task(_ws_broadcaster())
    yield


# ─────────────────────────────────────────────────────────────
#  App
# ─────────────────────────────────────────────────────────────

app = FastAPI(title="Bitget Bot Dashboard API", version="1.0.0", lifespan=lifespan)
app.include_router(strategy_router)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─────────────────────────────────────────────────────────────
#  GET /api/status
# ─────────────────────────────────────────────────────────────

@app.get("/api/status")
def api_status():
    open_trade = _db.get_open_trade()
    latest_eq = _db.get_latest_equity()
    initial = _bot_config.get("initial_equity", 10_000.0)
    current = latest_eq["equity"] if latest_eq else initial
    return {
        "running": _bot_running.is_set(),
        "dry_run": _bot_config.get("dry_run", True),
        "symbol": _bot_config.get("symbol", ""),
        "timeframe": _bot_config.get("timeframe", ""),
        "leverage": _bot_config.get("lev", 5),
        "current_position": open_trade["direction"] if open_trade else None,
        "open_trade": open_trade,
        "current_equity": round(current, 2),
        "initial_equity": initial,
        "return_pct": round((current - initial) / initial * 100, 2) if initial else 0.0,
        "runtime": {
            "config_version": _runtime_config.get("version", 0),
            "applied_version": _runtime_config.get("applied_version", 0),
            "last_apply_error": _runtime_config.get("last_apply_error", ""),
        },
    }


def _settings_env_path() -> Path:
    return Path(__file__).resolve().parent.parent / ".env"


def _runtime_settings_snapshot() -> dict:
    with _runtime_lock:
        return dict(_runtime_config.get("settings", {}))


def _sync_bot_config(settings: dict) -> None:
    _bot_config.update(
        symbol=settings["SYMBOL"],
        timeframe=settings["TIMEFRAME"],
        squeeze=settings["SQUEEZE_THRESHOLD"],
        margin_pct=settings["MARGIN_USAGE_PCT"],
        lev=settings["LEVERAGE"],
        dry_run=settings["DRY_RUN"],
        poll_interval=settings["POLL_INTERVAL"],
        initial_equity=settings["INITIAL_EQUITY"],
    )


def _bot_runtime_provider() -> dict:
    return _runtime_settings_snapshot()


def _mark_config_applied(settings: dict | None, error: str | None) -> None:
    with _runtime_lock:
        if error:
            _runtime_config["last_apply_error"] = error
        elif settings is not None:
            _runtime_config["applied_version"] = _runtime_config["version"]
            _runtime_config["last_apply_error"] = ""
            _runtime_config["settings"] = settings.copy()
            _sync_bot_config(settings)


def _apply_runtime_settings(payload: dict) -> dict:
    result = save_settings(payload, env_path=_settings_env_path())
    settings = result["settings"]
    for key, value in settings.items():
        os.environ[key] = "true" if value is True else "false" if value is False else str(value)
    with _runtime_lock:
        _runtime_config["settings"] = settings.copy()
        _runtime_config["version"] = int(_runtime_config.get("version", 0)) + 1
        _runtime_config["last_apply_error"] = ""
    _sync_bot_config(settings)
    return result


class SettingsUpdateRequest(BaseModel):
    BITGET_API_KEY: str | None = None
    BITGET_API_SECRET: str | None = None
    BITGET_API_PASSPHRASE: str | None = None
    SYMBOL: str | None = None
    TIMEFRAME: str | None = None
    LEVERAGE: int | None = None
    DRY_RUN: bool | None = None
    POLL_INTERVAL: int | None = None
    INITIAL_EQUITY: float | None = None
    MARGIN_USAGE_PCT: float | None = None
    SQUEEZE_THRESHOLD: float | None = None
    DEEPSEEK_API_KEY: str | None = None


@app.get("/api/settings")
def api_get_settings():
    return load_settings_snapshot(_settings_env_path(), _runtime_config)


@app.put("/api/settings")
def api_put_settings(req: SettingsUpdateRequest):
    payload = req.model_dump(exclude_unset=True)
    try:
        result = _apply_runtime_settings(payload)
    except SettingsValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    runtime = {
        "config_version": _runtime_config["version"],
        "applied_version": _runtime_config["applied_version"],
        "last_apply_error": _runtime_config["last_apply_error"],
    }
    return {
        "applied": runtime["config_version"] == runtime["applied_version"],
        "updates": (result or {}).get("updates", payload),
        "runtime": runtime,
    }


@app.post("/api/settings/test")
def api_test_settings(req: SettingsUpdateRequest):
    payload = req.model_dump(exclude_unset=True)
    current = load_runtime_settings(_settings_env_path())
    merged = {**current, **payload}
    return test_settings_connections(merged)


# ─────────────────────────────────────────────────────────────
#  GET /api/trades
# ─────────────────────────────────────────────────────────────

@app.get("/api/trades")
def api_trades(
    page: int = 1,
    limit: int = 50,
    direction: str = None,
    closed_only: bool = False,
):
    limit = min(limit, 200)
    offset = (page - 1) * limit
    total, rows = _db.get_trades(
        limit=limit, offset=offset, direction=direction, closed_only=closed_only
    )
    return {"total": total, "page": page, "limit": limit, "trades": rows}


# ─────────────────────────────────────────────────────────────
#  GET /api/equity
# ─────────────────────────────────────────────────────────────

@app.get("/api/equity")
def api_equity(days: int = 30):
    points = _db.get_equity_curve(days=days)
    latest_eq = _db.get_latest_equity()
    initial = _bot_config.get("initial_equity", 10_000.0)
    current = latest_eq["equity"] if latest_eq else initial
    stats = _db.get_summary_stats()
    return {
        "initial_equity": initial,
        "current_equity": round(current, 2),
        "return_pct": round((current - initial) / initial * 100, 2) if initial else 0.0,
        "stats": stats,
        "points": points,
    }


# ─────────────────────────────────────────────────────────────
#  GET /api/ohlcv
# ─────────────────────────────────────────────────────────────

@app.get("/api/ohlcv")
def api_ohlcv(
    symbol: str = "BTC/USDT:USDT",
    timeframe: str = "15m",
    limit: int = 200,
):
    try:
        ex = ccxt.bitget({"options": {"defaultType": "swap"}, "enableRateLimit": True})
        raw = ex.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit + 1)
        tf_ms = int(ex.parse_timeframe(timeframe) * 1000)
        now_ms = ex.milliseconds()
        closed = [r for r in raw if r[0] + tf_ms <= now_ms][-limit:]
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "candles": [
                {"ts": r[0], "open": r[1], "high": r[2], "low": r[3], "close": r[4], "volume": r[5]}
                for r in closed
            ],
        }
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"OHLCV fetch failed: {exc}")


# ─────────────────────────────────────────────────────────────
#  GET /api/events
# ─────────────────────────────────────────────────────────────

@app.get("/api/events")
def api_events(limit: int = 100):
    return _db.get_events(limit=limit)


# ─────────────────────────────────────────────────────────────
#  POST /api/backtest   GET /api/backtest/{job_id}
# ─────────────────────────────────────────────────────────────

class BacktestRequest(BaseModel):
    symbol: str = "BTC/USDT:USDT"
    days: int = 90
    squeeze: float = 0.35
    equity: float = 10_000.0
    leverage: int = 5
    margin_pct: float = 100.0
    fee_rate: float = 0.0005   # 0.05% per order side (entry + exit = 0.10% round-trip)


@app.post("/api/backtest")
def api_run_backtest(req: BacktestRequest):
    try:
        import backtest as bt  # root-level module; importable when CWD = project root
    except ImportError as exc:
        raise HTTPException(status_code=500, detail=f"backtest module unavailable: {exc}")

    job_id = (
        f"bt_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        f"_{uuid.uuid4().hex[:6]}"
    )
    _backtest_jobs[job_id] = {"status": "running", "job_id": job_id}

    def _run() -> None:
        try:
            from datetime import timedelta
            import numpy as np

            since_ms = int(
                (datetime.now(timezone.utc) - timedelta(days=req.days)).timestamp() * 1000
            )
            df = bt.fetch_ohlcv_full(req.symbol, "15m", since_ms)
            trades, eq_series = bt.run_backtest(
                df,
                squeeze_threshold=req.squeeze,
                initial_equity=req.equity,
                leverage=req.leverage,
                margin_pct=req.margin_pct,
                fee_rate=req.fee_rate,
            )
            closed = [t for t in trades if t.pnl_usdt is not None]
            pnls = [t.pnl_usdt for t in closed]
            wins = [p for p in pnls if p > 0]
            losses = [p for p in pnls if p <= 0]
            total_fees = sum(t.fee_usdt or 0.0 for t in closed)

            eq_vals = eq_series.values
            peak = np.maximum.accumulate(eq_vals)
            dd = (eq_vals - peak) / np.where(peak > 0, peak, 1) * 100.0
            total_pnl = sum(pnls)

            _backtest_jobs[job_id] = {
                "status": "done",
                "job_id": job_id,
                "summary": {
                    "total_trades": len(closed),
                    "long_trades": sum(1 for t in closed if t.direction == "long"),
                    "short_trades": sum(1 for t in closed if t.direction == "short"),
                    "wins": len(wins),
                    "losses": len(losses),
                    "win_rate_pct": round(len(wins) / len(closed) * 100, 2) if closed else 0.0,
                    "profit_factor": (
                        round(sum(wins) / abs(sum(losses)), 2)
                        if losses and sum(losses) != 0
                        else None
                    ),
                    "total_pnl_usdt": round(total_pnl, 2),
                    "total_return_pct": round(total_pnl / req.equity * 100, 2),
                    "total_fee_usdt": round(total_fees, 2),
                    "fee_rate_pct": round(req.fee_rate * 100, 4),
                    "leverage": req.leverage,
                    "initial_equity": req.equity,
                    "final_equity": round(req.equity + total_pnl, 2),
                    "max_drawdown_pct": round(float(np.min(dd)), 2),
                    "date_from": eq_series.index[0].isoformat() if len(eq_series) else None,
                    "date_to": eq_series.index[-1].isoformat() if len(eq_series) else None,
                },
                # Downsample to keep payload small (every 10th point)
                "equity_curve": [
                    {"ts": ts.isoformat(), "equity": round(float(v), 2)}
                    for ts, v in eq_series.iloc[::10].items()
                ],
                "trades": [
                    {
                        "direction": t.direction,
                        "entry_time": t.entry_time.isoformat() if t.entry_time else None,
                        "entry_price": t.entry_price,
                        "exit_time": t.exit_time.isoformat() if t.exit_time else None,
                        "exit_price": t.exit_price,
                        "pnl_pct": round(t.pnl_pct, 4) if t.pnl_pct is not None else None,
                        "pnl_usdt": round(t.pnl_usdt, 2),
                        "fee_usdt": round(t.fee_usdt or 0.0, 4),
                        "notional": round(t.notional or 0.0, 2),
                    }
                    for t in closed
                ],
            }
        except Exception as exc:
            log.exception("Backtest job %s failed", job_id)
            _backtest_jobs[job_id] = {"status": "error", "job_id": job_id, "error": str(exc)}

    threading.Thread(target=_run, daemon=True, name=f"backtest-{job_id}").start()
    return {"job_id": job_id, "status": "running"}


@app.get("/api/backtest/{job_id}")
def api_get_backtest(job_id: str):
    job = _backtest_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


# ─────────────────────────────────────────────────────────────
#  WebSocket /ws
# ─────────────────────────────────────────────────────────────

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    _ws_clients.append(ws)
    log.info("WS connect — total clients: %d", len(_ws_clients))
    try:
        # Send current bot state immediately on connect
        open_trade = _db.get_open_trade()
        latest_eq = _db.get_latest_equity()
        await ws.send_json({
            "type": "bot_status",
            "data": {
                "running": _bot_running.is_set(),
                "dry_run": _bot_config.get("dry_run", True),
                "symbol": _bot_config.get("symbol", ""),
                "position": open_trade["direction"] if open_trade else None,
                "equity": latest_eq["equity"] if latest_eq else _bot_config.get("initial_equity", 0.0),
            },
        })
        while True:
            await ws.receive_text()  # keep connection alive; client messages ignored
    except WebSocketDisconnect:
        pass
    finally:
        if ws in _ws_clients:
            _ws_clients.remove(ws)
        log.info("WS disconnect — total clients: %d", len(_ws_clients))


# ─────────────────────────────────────────────────────────────
#  Static frontend (Phase 3)
#  Only mounted when frontend/dist/ exists so the API works
#  standalone during development without the frontend build.
# ─────────────────────────────────────────────────────────────

_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if _DIST.is_dir():
    from fastapi.staticfiles import StaticFiles
    app.mount("/", StaticFiles(directory=str(_DIST), html=True), name="static")
    log.info("Serving frontend from %s", _DIST)
