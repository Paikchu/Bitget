"""
Sandbox runner — executed inside the Docker container.
Reads JSON from stdin, runs a bar-by-bar backtest, writes JSON to stdout.
"""
import json
import sys
import resource
import traceback
import types

import numpy as np
import pandas as pd


def _apply_resource_limits():
    try:
        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
    except Exception:
        pass
    try:
        resource.setrlimit(resource.RLIMIT_CPU, (60, 60))
    except Exception:
        pass


_ALLOWED_IMPORT_TOPS = frozenset({
    "numpy", "pandas", "math", "cmath", "statistics",
    "dataclasses", "typing", "collections", "functools",
    "itertools", "operator", "datetime", "decimal",
    "fractions", "numbers", "abc", "copy",
})


def _restricted_import(name, globals=None, locals=None, fromlist=(), level=0):
    top = name.split(".")[0]
    if top not in _ALLOWED_IMPORT_TOPS:
        raise ImportError(f"Import of '{name}' is not allowed in strategy code")
    return __import__(name, globals, locals, fromlist, level)


SAFE_BUILTINS = {
    "__import__": _restricted_import,
    "print": print,
    "range": range,
    "len": len,
    "int": int,
    "float": float,
    "str": str,
    "bool": bool,
    "list": list,
    "dict": dict,
    "tuple": tuple,
    "set": set,
    "frozenset": frozenset,
    "min": min,
    "max": max,
    "sum": sum,
    "abs": abs,
    "round": round,
    "sorted": sorted,
    "reversed": reversed,
    "enumerate": enumerate,
    "zip": zip,
    "map": map,
    "filter": filter,
    "any": any,
    "all": all,
    "isinstance": isinstance,
    "issubclass": issubclass,
    "hasattr": hasattr,
    "getattr": getattr,
    "setattr": setattr,
    "ValueError": ValueError,
    "TypeError": TypeError,
    "IndexError": IndexError,
    "KeyError": KeyError,
    "AttributeError": AttributeError,
    "Exception": Exception,
    "StopIteration": StopIteration,
    "NotImplementedError": NotImplementedError,
    "RuntimeError": RuntimeError,
    "ZeroDivisionError": ZeroDivisionError,
    "True": True,
    "False": False,
    "None": None,
    "__build_class__": __build_class__,
}


def _load_strategy(code: str):
    """Compile and exec user strategy code in a restricted namespace."""
    namespace = {
        "__builtins__": SAFE_BUILTINS,
        "np": np,
        "numpy": np,
        "pd": pd,
        "pandas": pd,
    }
    compiled = compile(code, "<strategy>", "exec")
    exec(compiled, namespace)  # noqa: S102
    return namespace


def _run_backtest(ohlcv: list, strategy_ns: dict, params: dict) -> dict:
    initial_equity = float(params.get("initial_equity", 10000))
    leverage = float(params.get("leverage", 1))
    fee_rate = float(params.get("fee_rate", 0.0005))
    margin_pct = float(params.get("margin_pct", 100)) / 100.0

    columns = ["timestamp", "open", "high", "low", "close", "volume"]
    df_raw = pd.DataFrame(ohlcv, columns=columns)
    df_raw["timestamp"] = pd.to_datetime(df_raw["timestamp"], unit="ms", utc=True)

    add_indicators = strategy_ns["add_indicators"]
    get_signal = strategy_ns["get_signal"]

    df = add_indicators(df_raw.copy())

    equity = initial_equity
    position = 0  # +1 long, -1 short, 0 flat
    entry_price = 0.0
    entry_time = None
    notional = 0.0

    equity_curve = [{"ts": str(df["timestamp"].iloc[0]), "equity": round(equity, 2)}]
    trades = []

    n = len(df)

    for i in range(1, n - 1):
        sig = get_signal(df, i, params)
        long_entry = bool(sig.get("long_entry", False))
        short_entry = bool(sig.get("short_entry", False))
        close_long = bool(sig.get("close_long", False))
        close_short = bool(sig.get("close_short", False))

        fill_price = float(df["open"].iloc[i + 1])
        ts_now = str(df["timestamp"].iloc[i])
        ts_fill = str(df["timestamp"].iloc[i + 1])

        if position == 1 and close_long:
            fee_usdt = notional * fee_rate * 2
            pnl_usdt = (fill_price - entry_price) / entry_price * notional - fee_usdt
            pnl_pct = (fill_price - entry_price) / entry_price * leverage * 100
            equity += pnl_usdt
            trades.append({
                "direction": "long",
                "entry_time": entry_time,
                "entry_price": entry_price,
                "exit_time": ts_fill,
                "exit_price": fill_price,
                "pnl_pct": round(pnl_pct, 4),
                "pnl_usdt": round(pnl_usdt, 4),
                "fee_usdt": round(fee_usdt, 4),
                "notional": round(notional, 4),
            })
            position = 0
            equity_curve.append({"ts": ts_fill, "equity": round(equity, 2)})

        elif position == -1 and close_short:
            fee_usdt = notional * fee_rate * 2
            pnl_usdt = (entry_price - fill_price) / entry_price * notional - fee_usdt
            pnl_pct = (entry_price - fill_price) / entry_price * leverage * 100
            equity += pnl_usdt
            trades.append({
                "direction": "short",
                "entry_time": entry_time,
                "entry_price": entry_price,
                "exit_time": ts_fill,
                "exit_price": fill_price,
                "pnl_pct": round(pnl_pct, 4),
                "pnl_usdt": round(pnl_usdt, 4),
                "fee_usdt": round(fee_usdt, 4),
                "notional": round(notional, 4),
            })
            position = 0
            equity_curve.append({"ts": ts_fill, "equity": round(equity, 2)})

        if position == 0:
            if long_entry:
                position = 1
                entry_price = fill_price
                entry_time = ts_fill
                notional = equity * margin_pct * leverage
            elif short_entry:
                position = -1
                entry_price = fill_price
                entry_time = ts_fill
                notional = equity * margin_pct * leverage

    # Force-close any open trade at last bar close
    if position != 0:
        fill_price = float(df["close"].iloc[-1])
        ts_fill = str(df["timestamp"].iloc[-1])
        fee_usdt = notional * fee_rate * 2
        if position == 1:
            pnl_usdt = (fill_price - entry_price) / entry_price * notional - fee_usdt
            pnl_pct = (fill_price - entry_price) / entry_price * leverage * 100
            direction = "long"
        else:
            pnl_usdt = (entry_price - fill_price) / entry_price * notional - fee_usdt
            pnl_pct = (entry_price - fill_price) / entry_price * leverage * 100
            direction = "short"
        equity += pnl_usdt
        trades.append({
            "direction": direction,
            "entry_time": entry_time,
            "entry_price": entry_price,
            "exit_time": ts_fill,
            "exit_price": fill_price,
            "pnl_pct": round(pnl_pct, 4),
            "pnl_usdt": round(pnl_usdt, 4),
            "fee_usdt": round(fee_usdt, 4),
            "notional": round(notional, 4),
        })
        equity_curve.append({"ts": ts_fill, "equity": round(equity, 2)})

    # Summary stats
    total_trades = len(trades)
    long_trades = sum(1 for t in trades if t["direction"] == "long")
    short_trades = sum(1 for t in trades if t["direction"] == "short")
    wins = sum(1 for t in trades if t["pnl_usdt"] > 0)
    losses = sum(1 for t in trades if t["pnl_usdt"] <= 0)
    win_rate_pct = (wins / total_trades * 100) if total_trades > 0 else 0.0
    gross_profit = sum(t["pnl_usdt"] for t in trades if t["pnl_usdt"] > 0)
    gross_loss = abs(sum(t["pnl_usdt"] for t in trades if t["pnl_usdt"] < 0))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float("inf")
    total_pnl_usdt = equity - initial_equity
    total_return_pct = (total_pnl_usdt / initial_equity) * 100
    total_fee_usdt = sum(t["fee_usdt"] for t in trades)

    # Max drawdown from equity_curve
    peak = initial_equity
    max_dd = 0.0
    running_eq = initial_equity
    for point in equity_curve:
        eq = point["equity"]
        if eq > peak:
            peak = eq
        dd = (peak - eq) / peak * 100 if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd

    date_from = str(df["timestamp"].iloc[0])
    date_to = str(df["timestamp"].iloc[-1])

    # Downsample equity curve to max 300 points
    if len(equity_curve) > 300:
        step = len(equity_curve) / 300
        equity_curve = [equity_curve[int(i * step)] for i in range(300)] + [equity_curve[-1]]

    summary = {
        "total_trades": total_trades,
        "long_trades": long_trades,
        "short_trades": short_trades,
        "wins": wins,
        "losses": losses,
        "win_rate_pct": round(win_rate_pct, 2),
        "profit_factor": round(profit_factor, 4) if profit_factor != float("inf") else None,
        "total_pnl_usdt": round(total_pnl_usdt, 4),
        "total_return_pct": round(total_return_pct, 4),
        "total_fee_usdt": round(total_fee_usdt, 4),
        "initial_equity": initial_equity,
        "final_equity": round(equity, 4),
        "max_drawdown_pct": round(max_dd, 4),
        "date_from": date_from,
        "date_to": date_to,
    }

    return {
        "success": True,
        "summary": summary,
        "equity_curve": equity_curve,
        "trades": trades,
    }


def main():
    _apply_resource_limits()
    try:
        payload = json.loads(sys.stdin.read())
        code = payload["strategy_code"]
        ohlcv = payload["ohlcv"]
        params = payload.get("params", {})

        strategy_ns = _load_strategy(code)
        result = _run_backtest(ohlcv, strategy_ns, params)
        print(json.dumps(result))
    except Exception:
        print(json.dumps({"success": False, "error": traceback.format_exc()}))


if __name__ == "__main__":
    main()
