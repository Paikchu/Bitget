"""
Strategy Studio API Router — /api/strategy/*

Endpoints:
  POST /api/strategy/generate           translate Markdown → Python via DeepSeek
  POST /api/strategy/backtest           submit a sandbox backtest job
  GET  /api/strategy/backtest/{job_id}  poll job status
  GET  /api/strategy/builtin            list built-in strategies
  GET  /api/strategy/builtin/{id}       get built-in strategy details
"""
from __future__ import annotations

import logging
import os
import re
import threading
import uuid
from itertools import product
from statistics import median
from datetime import datetime, timezone
from typing import Any, Optional

import ccxt
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from bitget_bot import db as _db
from bitget_bot.ai_feedback import (
    analyze_backtest as _analyze_backtest_feedback,
    analyze_experiment as _analyze_experiment_feedback,
)

log = logging.getLogger("bitget_bot.strategy_router")

router = APIRouter(prefix="/api/strategy")

# In-memory job store (reset on restart)
_jobs: dict[str, dict] = {}


# ─────────────────────────────────────────────────────────────
#  DeepSeek system prompt
# ─────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """你是一个专业的量化交易策略代码生成器。

用户会用自然语言（Markdown 格式）描述他们的交易策略，你需要将其翻译成符合以下接口规范的 Python 代码。

## 必须实现的接口

```python
import numpy as np
import pandas as pd

def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    # 在这里添加指标计算
    return out

def get_signal(df: pd.DataFrame, i: int, params: dict) -> dict:
    return {
        'long_entry':  False,
        'short_entry': False,
        'close_long':  False,
        'close_short': False,
    }
```

## 严格规则

1. 只能导入：numpy (as np), pandas (as pd), math, dataclasses, typing, collections, functools, itertools, datetime
2. 禁止：os, sys, subprocess, socket, requests, ccxt, exec, eval, open
3. 必须实现 add_indicators 和 get_signal 两个函数
4. get_signal 中用 df['column'].iloc[i-1] 访问历史数据
5. get_signal 开头做边界检查（如 if i < 20: return {四个 False}）
6. 代码必须可直接运行，不含 TODO 或占位符
7. 若策略使用最近 N 根 K 线最高价/最低价作为突破或止损参考，窗口必须排除当前 bar；也就是在 get_signal 中读取这些 rolling 值时，应使用 df['xxx'].iloc[i-1]，再和当前 bar 的 close/volume 做比较。

只输出 Python 代码，不含任何 Markdown 代码块标记，代码以 import 语句开头。"""


_WINDOW_LOOKBACK_COLUMNS = ("high_20", "low_20", "high_10", "low_10")


def _normalize_generated_code(code: str) -> str:
    normalized = code.strip()
    for column in _WINDOW_LOOKBACK_COLUMNS:
        pattern = rf"df\[(['\"])({re.escape(column)})\1\]\.iloc\[i\]"
        replacement = rf"df[\1\2\1].iloc[i-1]"
        normalized = re.sub(pattern, replacement, normalized)
    return normalized


# ─────────────────────────────────────────────────────────────
#  OHLCV fetch helper
# ─────────────────────────────────────────────────────────────

def _fetch_ohlcv(symbol: str, timeframe: str, days: int) -> list:
    from datetime import timedelta
    ex = ccxt.bitget({"options": {"defaultType": "swap"}, "enableRateLimit": True})
    since_ms = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)
    tf_ms = int(ex.parse_timeframe(timeframe) * 1000)
    now_ms = ex.milliseconds()
    all_rows, cur = [], since_ms
    while cur < now_ms:
        rows = ex.fetch_ohlcv(symbol, timeframe=timeframe, since=cur, limit=200)
        if not rows:
            break
        all_rows.extend(rows)
        last_ts = rows[-1][0]
        next_cur = last_ts + tf_ms
        if next_cur <= cur:
            break
        cur = next_cur
    seen = set()
    clean = []
    for r in sorted(all_rows, key=lambda x: x[0]):
        if r[0] not in seen and r[0] + tf_ms <= now_ms:
            seen.add(r[0])
            clean.append(r)
    return clean


# ─────────────────────────────────────────────────────────────
#  Request / response models
# ─────────────────────────────────────────────────────────────

class GenerateRequest(BaseModel):
    markdown: str
    model: str = "deepseek-chat"


class BacktestRequest(BaseModel):
    strategy_code: str
    strategy_version_id: Optional[int] = None
    symbol: str = "BTC/USDT:USDT"
    timeframe: str = "15m"
    days: int = 90
    initial_equity: float = 10_000.0
    leverage: int = 5
    margin_pct: float = 100.0
    fee_rate: float = 0.0005
    squeeze_threshold: float = 0.35
    stop_loss_pct: Optional[float] = None
    take_profit_pct: Optional[float] = None
    risk_reward_ratio: Optional[float] = None
    trailing_stop_enabled: bool = False


class ExperimentRequest(BaseModel):
    strategy_code: str
    strategy_version_id: Optional[int] = None
    symbol: str = "BTC/USDT:USDT"
    initial_equity: float = 10_000.0
    leverage: int = 5
    margin_pct: float = 100.0
    fee_rate: float = 0.0005
    squeeze_threshold: float = 0.35
    parameter_grid: dict[str, list[Any] | Any]


# ─────────────────────────────────────────────────────────────
#  POST /api/strategy/generate
# ─────────────────────────────────────────────────────────────

@router.post("/generate")
def generate_strategy(req: GenerateRequest):
    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        raise HTTPException(
            status_code=500,
            detail="DEEPSEEK_API_KEY not configured — set it in .env (see .env.example)",
        )

    try:
        client = _make_deepseek_client(api_key)
        response = client.chat.completions.create(
            model=req.model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": req.markdown},
            ],
            temperature=0.1,
            max_tokens=4096,
        )
        raw_code = response.choices[0].message.content or ""
        # Strip markdown fences if present
        code = re.sub(r"^```(?:python)?\s*\n?", "", raw_code.strip(), flags=re.IGNORECASE)
        code = re.sub(r"\n?```\s*$", "", code.strip())
        code = _normalize_generated_code(code)
        version = _persist_generated_version(req.markdown, code, req.model)
        return {"code": code, "model": req.model, "version": _serialize_version_summary(version)}
    except HTTPException:
        raise
    except Exception as exc:
        log.exception("DeepSeek API call failed")
        raise HTTPException(status_code=502, detail=f"DeepSeek API error: {exc}")


def _make_deepseek_client(api_key: str):
    from openai import OpenAI

    return OpenAI(
        api_key=api_key,
        base_url="https://api.deepseek.com/v1",
    )


def _persist_generated_version(markdown: str, code: str, model: str) -> dict:
    return _db.create_strategy_version(
        markdown=markdown,
        code=code,
        source="generate",
        model=model,
    )


def _serialize_version_summary(version: dict) -> dict:
    return {
        "id": version["id"],
        "version_no": version["version_no"],
        "title": version.get("title"),
        "source": version["source"],
        "model": version.get("model"),
        "created_at": version["created_at"],
        "parent_version_id": version.get("parent_version_id"),
        "latest_backtest_summary": version.get("latest_backtest_summary"),
        "latest_backtest_at": version.get("latest_backtest_at"),
    }


@router.get("/versions")
def list_versions(limit: int = 50, offset: int = 0):
    versions = _db.list_strategy_versions(limit=limit, offset=offset)
    return [_serialize_version_summary(version) for version in versions]


@router.get("/versions/{version_id}")
def get_version(version_id: int):
    version = _db.get_strategy_version(version_id)
    if not version:
        raise HTTPException(status_code=404, detail="Strategy version not found")
    return version


# ─────────────────────────────────────────────────────────────
#  POST /api/strategy/backtest
# ─────────────────────────────────────────────────────────────

@router.post("/backtest")
def start_backtest(req: BacktestRequest):
    job_id = f"sb_{uuid.uuid4().hex[:12]}"
    _jobs[job_id] = {"status": "running", "job_id": job_id}

    def _run():
        try:
            from bitget_bot.sandbox.docker_executor import run_strategy_in_sandbox, SandboxError

            ohlcv = _fetch_ohlcv(req.symbol, req.timeframe, req.days)
            if not ohlcv:
                raise ValueError(f"No OHLCV data returned for {req.symbol} {req.timeframe}")

            params = {
                "symbol": req.symbol,
                "timeframe": req.timeframe,
                "initial_equity": req.initial_equity,
                "leverage": req.leverage,
                "margin_pct": req.margin_pct,
                "fee_rate": req.fee_rate,
                "squeeze_threshold": req.squeeze_threshold,
                "stop_loss_pct": req.stop_loss_pct,
                "take_profit_pct": req.take_profit_pct,
                "risk_reward_ratio": req.risk_reward_ratio,
                "trailing_stop_enabled": req.trailing_stop_enabled,
            }
            result = run_strategy_in_sandbox(req.strategy_code, ohlcv, params)
            if req.strategy_version_id and result.get("summary"):
                _db.record_strategy_version_backtest(
                    strategy_version_id=req.strategy_version_id,
                    job_id=job_id,
                    summary=result["summary"],
                )
            _jobs[job_id] = {
                "status": "done",
                "job_id": job_id,
                **result,
            }
        except Exception as exc:
            log.exception("Strategy backtest job %s failed", job_id)
            _jobs[job_id] = {"status": "error", "job_id": job_id, "error": str(exc)}

    threading.Thread(target=_run, daemon=True, name=f"sb-{job_id}").start()
    return {"job_id": job_id, "status": "running"}


# ─────────────────────────────────────────────────────────────
#  GET /api/strategy/backtest/{job_id}
# ─────────────────────────────────────────────────────────────

@router.get("/backtest/{job_id}")
def get_backtest(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.post("/backtest/{job_id}/feedback")
def generate_backtest_feedback(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.get("status") != "done":
        raise HTTPException(status_code=400, detail="Backtest is not completed")
    if not job.get("trades"):
        raise HTTPException(status_code=400, detail="Backtest has no trades")

    analyzed = _analyze_backtest_feedback(job)
    job["feedback"] = {
        "job_id": job_id,
        "feedback": analyzed["feedback"],
        "prompt_version": analyzed["prompt_version"],
        "schema_version": analyzed["schema_version"],
        "model": analyzed["model"],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    return job["feedback"]


@router.get("/backtest/{job_id}/feedback")
def get_backtest_feedback(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    feedback = job.get("feedback")
    if not feedback:
        raise HTTPException(status_code=404, detail="Backtest feedback not found")
    return feedback


def _normalize_grid(parameter_grid: dict[str, list[Any] | Any]) -> dict[str, list[Any]]:
    normalized: dict[str, list[Any]] = {}
    for key, value in parameter_grid.items():
        normalized[key] = value if isinstance(value, list) else [value]
    return normalized


def _build_experiment_cases(req: ExperimentRequest) -> list[dict]:
    grid = _normalize_grid(req.parameter_grid)
    keys = list(grid.keys())
    cases = []
    for combo in product(*(grid[key] for key in keys)):
        params = dict(zip(keys, combo))
        timeframe = params.get("timeframe", "15m")
        days = int(params.get("days", 90))
        scenario_tag = f"{timeframe}-{days}d"
        cases.append({"params": params, "scenario_tag": scenario_tag})
    return cases


def _aggregate_experiment_runs(runs: list[dict]) -> dict:
    summaries = [run["result"].get("summary", {}) for run in runs]
    returns = [summary.get("total_return_pct", 0.0) for summary in summaries]
    drawdowns = [summary.get("max_drawdown_pct", 0.0) for summary in summaries]
    positive_runs = sum(1 for value in returns if value > 0)
    return {
        "run_count": len(runs),
        "best_total_return_pct": round(max(returns), 4) if returns else 0.0,
        "median_total_return_pct": round(median(returns), 4) if returns else 0.0,
        "worst_max_drawdown_pct": round(max(drawdowns), 4) if drawdowns else 0.0,
        "positive_run_count": positive_runs,
        "stability_score": round((positive_runs / len(runs) * 100), 2) if runs else 0.0,
    }


@router.post("/experiments")
def start_experiment(req: ExperimentRequest):
    job_id = f"exp_{uuid.uuid4().hex[:12]}"
    cases = _build_experiment_cases(req)
    scenario_summary = [case["scenario_tag"] for case in cases]
    experiment = _db.create_strategy_experiment(
        strategy_version_id=req.strategy_version_id,
        strategy_code=req.strategy_code,
        config=req.model_dump(),
        scenario_summary=scenario_summary,
        job_id=job_id,
    )
    _jobs[job_id] = {"status": "running", "job_id": job_id, "experiment_id": experiment["id"]}

    def _run():
        try:
            from bitget_bot.sandbox.docker_executor import run_strategy_in_sandbox

            saved_runs = []
            for index, case in enumerate(cases, start=1):
                case_params = {
                    "symbol": req.symbol,
                    "timeframe": case["params"].get("timeframe", "15m"),
                    "initial_equity": req.initial_equity,
                    "leverage": req.leverage,
                    "margin_pct": req.margin_pct,
                    "fee_rate": req.fee_rate,
                    "squeeze_threshold": req.squeeze_threshold,
                    "days": int(case["params"].get("days", 90)),
                    "stop_loss_pct": case["params"].get("stop_loss_pct"),
                    "take_profit_pct": case["params"].get("take_profit_pct"),
                    "risk_reward_ratio": case["params"].get("risk_reward_ratio"),
                    "trailing_stop_enabled": bool(case["params"].get("trailing_stop_enabled", False)),
                }
                ohlcv = _fetch_ohlcv(req.symbol, case_params["timeframe"], case_params["days"])
                result = run_strategy_in_sandbox(req.strategy_code, ohlcv, case_params)
                saved_runs.append(
                    _db.add_strategy_experiment_run(
                        experiment_id=experiment["id"],
                        run_key=f"run-{index}",
                        params=case["params"],
                        scenario_tag=case["scenario_tag"],
                        result=result,
                    )
                )
            aggregate_summary = _aggregate_experiment_runs(saved_runs)
            _db.update_strategy_experiment_status(experiment["id"], "done", aggregate_summary=aggregate_summary)
            _jobs[job_id] = {
                "status": "done",
                "job_id": job_id,
                "experiment_id": experiment["id"],
                "aggregate_summary": aggregate_summary,
            }
        except Exception as exc:
            log.exception("Strategy experiment job %s failed", job_id)
            _db.update_strategy_experiment_status(experiment["id"], "error", error=str(exc))
            _jobs[job_id] = {"status": "error", "job_id": job_id, "experiment_id": experiment["id"], "error": str(exc)}

    threading.Thread(target=_run, daemon=True, name=f"exp-{job_id}").start()
    return {"job_id": job_id, "status": "running", "experiment_id": experiment["id"]}


@router.get("/experiments/{experiment_id}")
def get_experiment(experiment_id: int):
    experiment = _db.get_strategy_experiment(experiment_id)
    if not experiment:
        raise HTTPException(status_code=404, detail="Experiment not found")
    return experiment


@router.get("/experiments/{experiment_id}/runs")
def get_experiment_runs(experiment_id: int):
    experiment = _db.get_strategy_experiment(experiment_id)
    if not experiment:
        raise HTTPException(status_code=404, detail="Experiment not found")
    return _db.list_strategy_experiment_runs(experiment_id)


@router.post("/experiments/{experiment_id}/feedback")
def generate_experiment_feedback(experiment_id: int):
    experiment = _db.get_strategy_experiment(experiment_id)
    if not experiment:
        raise HTTPException(status_code=404, detail="Experiment not found")
    runs = _db.list_strategy_experiment_runs(experiment_id)
    if not runs:
        raise HTTPException(status_code=400, detail="Experiment has no runs")
    analyzed = _analyze_experiment_feedback(experiment, runs)
    stored = _db.save_strategy_experiment_feedback(
        experiment_id=experiment_id,
        feedback=analyzed["feedback"],
        prompt_version=analyzed["prompt_version"],
        schema_version=analyzed["schema_version"],
        model=analyzed["model"],
    )
    return stored


@router.get("/experiments/{experiment_id}/feedback")
def get_experiment_feedback(experiment_id: int):
    feedback = _db.get_strategy_experiment_feedback(experiment_id)
    if not feedback:
        raise HTTPException(status_code=404, detail="Experiment feedback not found")
    return feedback


# ─────────────────────────────────────────────────────────────
#  GET /api/strategy/builtin
# ─────────────────────────────────────────────────────────────

@router.get("/builtin")
def list_builtins():
    from bitget_bot.strategies.load_default import BUILTIN_STRATEGIES
    return [
        {"id": k, "name": v["name"]}
        for k, v in BUILTIN_STRATEGIES.items()
    ]


# ─────────────────────────────────────────────────────────────
#  GET /api/strategy/builtin/{strategy_id}
# ─────────────────────────────────────────────────────────────

@router.get("/builtin/{strategy_id}")
def get_builtin(strategy_id: str):
    from bitget_bot.strategies.load_default import BUILTIN_STRATEGIES
    strat = BUILTIN_STRATEGIES.get(strategy_id)
    if not strat:
        raise HTTPException(status_code=404, detail=f"Built-in strategy '{strategy_id}' not found")
    return strat


# ── Debug endpoints ────────────────────────────────────────────────────────────

from bitget_bot.sandbox.code_validator import validate_code_full as _validate_full  # noqa: E402


class ValidateRequest(BaseModel):
    code: str


class FixRequest(BaseModel):
    code: str
    error_message: str
    error_type: str = "unknown"


_FIX_SYSTEM_PROMPT = """你是一个 Python 交易策略调试专家。
用户会给你一段有错误的策略代码和对应的错误信息，你需要修复代码中的错误并返回完整的修复后代码。

策略代码必须满足：
1. 必须定义 add_indicators(df) 和 get_signal(df, i, params)
2. get_signal 返回含 long_entry, short_entry, close_long, close_short 四个布尔值的字典
3. 只能导入：numpy, pandas, math, dataclasses, typing, collections, functools, itertools, datetime
4. 禁止：os, sys, subprocess, exec, eval, open

修复原则：只修复错误，保留策略逻辑不变。只输出 Python 代码，不含 Markdown 代码块标记。"""


@router.post("/validate")
def validate_strategy(req: ValidateRequest):
    """Real-time code validation endpoint. Returns structured errors with line numbers."""
    return _validate_full(req.code)


@router.post("/fix")
def fix_strategy(req: FixRequest):
    """AI-assisted code fix. Sends code + error to DeepSeek, returns fixed code."""
    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        raise HTTPException(status_code=500, detail="未配置 DEEPSEEK_API_KEY")

    from openai import OpenAI
    client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com/v1")
    user_msg = (
        f"以下策略代码存在错误，请修复：\n\n"
        f"错误信息：\n{req.error_message}\n\n"
        f"错误类型：{req.error_type}\n\n"
        f"代码：\n{req.code}"
    )

    try:
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": _FIX_SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.05,
            max_tokens=4096,
        )
        fixed = response.choices[0].message.content.strip()
        if fixed.startswith("```"):
            lines = fixed.split("\n")
            fixed = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        validation = _validate_full(fixed)
        return {"code": fixed, "validation": validation, "fixed": validation["valid"]}
    except Exception as e:
        log.exception("AI fix failed")
        raise HTTPException(status_code=502, detail=f"AI 修复失败: {str(e)}")
