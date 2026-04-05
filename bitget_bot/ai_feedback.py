from __future__ import annotations

import json
import os
from statistics import mean
from typing import Any

from openai import OpenAI

SCHEMA_VERSION = "feedback.v1"
PROMPT_VERSION = "v1"
TRADE_SCHEMA_VERSION = "trade-feedback.v1"
TRADE_PROMPT_VERSION = "v1"

_SYSTEM_PROMPT = """你是一个量化回测分析助手。

你会收到一个 experiment 的结构化结果。你的任务是严格基于这些统计结果，输出结构化诊断。

规则：
1. 只根据输入证据判断，不要臆造不存在的交易细节
2. 分别评估止损、止盈、稳健性
3. 给出下一轮建议实验，不要改写策略代码
4. 输出必须符合给定 JSON Schema
"""

_TRADE_SYSTEM_PROMPT = """你是一个量化交易回测诊断助手。

你会收到一次单次回测的完整结构化结果，包括 summary、全部 trades，以及一些派生统计。
你的任务是找出这次回测里反复出现的共性问题，尤其是频繁亏损、重复开单、入场过早、止损止盈设置不合理、出场拖延等模式。

规则：
1. 必须先给总诊断，再给问题交易分组
2. 所有结论都必须基于输入里的 summary、trade 记录和派生统计
3. 典型案例必须引用 trade_numbers，trade_numbers 是 1-based 的交易编号
4. 不要改写整段策略代码，要给可以直接执行的优化方向
5. 输出必须符合给定 JSON Schema
"""

_FEEDBACK_SCHEMA: dict[str, Any] = {
    "name": SCHEMA_VERSION,
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "overall_score": {"type": "integer"},
            "stop_loss_assessment": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "label": {"type": "string"},
                    "score": {"type": "integer"},
                    "summary": {"type": "string"},
                },
                "required": ["label", "score", "summary"],
            },
            "take_profit_assessment": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "label": {"type": "string"},
                    "score": {"type": "integer"},
                    "summary": {"type": "string"},
                },
                "required": ["label", "score", "summary"],
            },
            "robustness_assessment": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "label": {"type": "string"},
                    "score": {"type": "integer"},
                    "summary": {"type": "string"},
                },
                "required": ["label", "score", "summary"],
            },
            "top_issues": {
                "type": "array",
                "items": {"type": "string"},
            },
            "recommended_experiments": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "title": {"type": "string"},
                        "change": {"type": "string"},
                        "reason": {"type": "string"},
                    },
                    "required": ["title", "change", "reason"],
                },
            },
            "parameter_adjustment_hints": {
                "type": "array",
                "items": {"type": "string"},
            },
            "confidence": {"type": "number"},
            "evidence": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
        "required": [
            "overall_score",
            "stop_loss_assessment",
            "take_profit_assessment",
            "robustness_assessment",
            "top_issues",
            "recommended_experiments",
            "parameter_adjustment_hints",
            "confidence",
            "evidence",
        ],
    },
}

_TRADE_FEEDBACK_SCHEMA: dict[str, Any] = {
    "name": TRADE_SCHEMA_VERSION,
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "overall_score": {"type": "integer"},
            "overall_diagnosis": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "headline": {"type": "string"},
                    "summary": {"type": "string"},
                },
                "required": ["headline", "summary"],
            },
            "issue_groups": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "title": {"type": "string"},
                        "pattern": {"type": "string"},
                        "impact": {"type": "string"},
                        "why_it_happens": {"type": "string"},
                        "trade_numbers": {
                            "type": "array",
                            "items": {"type": "integer"},
                        },
                        "recommendations": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": [
                        "title",
                        "pattern",
                        "impact",
                        "why_it_happens",
                        "trade_numbers",
                        "recommendations",
                    ],
                },
            },
            "priority_actions": {
                "type": "array",
                "items": {"type": "string"},
            },
            "evidence": {
                "type": "array",
                "items": {"type": "string"},
            },
            "confidence": {"type": "number"},
        },
        "required": [
            "overall_score",
            "overall_diagnosis",
            "issue_groups",
            "priority_actions",
            "evidence",
            "confidence",
        ],
    },
}


def _make_client() -> tuple[OpenAI, str]:
    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip() or os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("AI feedback requires DEEPSEEK_API_KEY or OPENAI_API_KEY")

    base_url = os.environ.get("AI_FEEDBACK_BASE_URL", "https://api.deepseek.com/v1")
    model = os.environ.get("AI_FEEDBACK_MODEL", "deepseek-chat")
    return OpenAI(api_key=api_key, base_url=base_url), model


def _request_structured_json(
    *,
    client: OpenAI,
    model: str,
    messages: list[dict[str, str]],
    schema: dict[str, Any],
    required_fields_text: str,
) -> dict[str, Any]:
    try:
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            response_format={"type": "json_schema", "json_schema": schema},
        )
        content = response.choices[0].message.content
    except Exception as exc:
        if "response_format type is unavailable" not in str(exc):
            raise
        fallback_messages = messages + [
            {
                "role": "system",
                "content": (
                    "直接返回 JSON 对象，不要使用 Markdown，不要添加额外说明。"
                    f"字段必须包含 {required_fields_text}。"
                ),
            }
        ]
        response = client.chat.completions.create(
            model=model,
            messages=fallback_messages,
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content
    return _parse_feedback_response(content)


def analyze_experiment(experiment: dict, runs: list[dict]) -> dict:
    client, model = _make_client()
    payload = {
        "experiment": {
            "id": experiment["id"],
            "config": experiment.get("config"),
            "aggregate_summary": experiment.get("aggregate_summary"),
            "scenario_summary": experiment.get("scenario_summary"),
        },
        "runs": [
            {
                "scenario_tag": run["scenario_tag"],
                "params": run["params"],
                "summary": run["result"].get("summary", {}),
                "trade_count": len(run["result"].get("trades", [])),
            }
            for run in runs
        ],
    }

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
    feedback = _request_structured_json(
        client=client,
        model=model,
        messages=messages,
        schema=_FEEDBACK_SCHEMA,
        required_fields_text=(
            "overall_score, stop_loss_assessment, take_profit_assessment, "
            "robustness_assessment, top_issues, recommended_experiments, "
            "parameter_adjustment_hints, confidence, evidence"
        ),
    )

    return {
        "feedback": feedback,
        "model": model,
        "prompt_version": PROMPT_VERSION,
        "schema_version": SCHEMA_VERSION,
    }


def analyze_backtest(job: dict[str, Any]) -> dict[str, Any]:
    client, model = _make_client()
    summary = job.get("summary") or {}
    trades = job.get("trades") or []
    payload = {
        "job_id": job.get("job_id"),
        "summary": summary,
        "trade_diagnostics": _build_trade_diagnostics(trades),
        "trades": [_serialize_trade(index, trade) for index, trade in enumerate(trades, start=1)],
    }
    messages = [
        {"role": "system", "content": _TRADE_SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
    feedback = _request_structured_json(
        client=client,
        model=model,
        messages=messages,
        schema=_TRADE_FEEDBACK_SCHEMA,
        required_fields_text=(
            "overall_score, overall_diagnosis, issue_groups, priority_actions, evidence, confidence"
        ),
    )
    feedback = _normalize_trade_feedback(feedback)
    return {
        "feedback": feedback,
        "model": model,
        "prompt_version": TRADE_PROMPT_VERSION,
        "schema_version": TRADE_SCHEMA_VERSION,
    }


def _serialize_trade(index: int, trade: dict[str, Any]) -> dict[str, Any]:
    return {
        "trade_number": index,
        "direction": trade.get("direction"),
        "entry_time": trade.get("entry_time"),
        "entry_price": trade.get("entry_price"),
        "exit_time": trade.get("exit_time"),
        "exit_price": trade.get("exit_price"),
        "pnl_pct": trade.get("pnl_pct"),
        "pnl_usdt": trade.get("pnl_usdt"),
        "fee_usdt": trade.get("fee_usdt"),
        "exit_reason": trade.get("exit_reason"),
        "holding_bars": trade.get("holding_bars"),
        "holding_minutes": trade.get("holding_minutes"),
        "max_favorable_excursion_pct": trade.get("max_favorable_excursion_pct"),
        "max_adverse_excursion_pct": trade.get("max_adverse_excursion_pct"),
        "peak_profit_before_exit_pct": trade.get("peak_profit_before_exit_pct"),
        "deepest_drawdown_before_exit_pct": trade.get("deepest_drawdown_before_exit_pct"),
    }


def _build_trade_diagnostics(trades: list[dict[str, Any]]) -> dict[str, Any]:
    if not trades:
        return {
            "trade_count": 0,
            "loss_count": 0,
            "win_count": 0,
            "quick_loss_trade_numbers": [],
            "small_winner_trade_numbers": [],
            "high_giveback_trade_numbers": [],
            "worst_trade_numbers": [],
            "long_loss_rate_pct": 0.0,
            "short_loss_rate_pct": 0.0,
        }

    losses = [trade for trade in trades if float(trade.get("pnl_usdt", 0)) <= 0]
    wins = [trade for trade in trades if float(trade.get("pnl_usdt", 0)) > 0]
    quick_losses = []
    small_winners = []
    high_giveback = []
    for index, trade in enumerate(trades, start=1):
        holding_bars = int(trade.get("holding_bars") or 0)
        pnl_usdt = float(trade.get("pnl_usdt") or 0)
        mfe = float(trade.get("max_favorable_excursion_pct") or 0)
        pnl_pct = float(trade.get("pnl_pct") or 0)
        if pnl_usdt <= 0 and holding_bars <= 4:
            quick_losses.append(index)
        if pnl_usdt > 0 and pnl_pct <= 1.0:
            small_winners.append(index)
        if mfe >= 2.0 and (mfe - pnl_pct) >= 1.5:
            high_giveback.append(index)

    worst_trades = sorted(
        enumerate(trades, start=1),
        key=lambda item: float(item[1].get("pnl_usdt") or 0),
    )[:5]
    long_trades = [trade for trade in trades if trade.get("direction") == "long"]
    short_trades = [trade for trade in trades if trade.get("direction") == "short"]

    return {
        "trade_count": len(trades),
        "loss_count": len(losses),
        "win_count": len(wins),
        "avg_holding_bars": round(mean([float(trade.get("holding_bars") or 0) for trade in trades]), 4),
        "avg_loss_holding_bars": round(mean([float(trade.get("holding_bars") or 0) for trade in losses]), 4) if losses else 0.0,
        "avg_win_holding_bars": round(mean([float(trade.get("holding_bars") or 0) for trade in wins]), 4) if wins else 0.0,
        "quick_loss_trade_numbers": quick_losses[:12],
        "small_winner_trade_numbers": small_winners[:12],
        "high_giveback_trade_numbers": high_giveback[:12],
        "worst_trade_numbers": [index for index, _trade in worst_trades],
        "long_loss_rate_pct": round(sum(1 for trade in long_trades if float(trade.get("pnl_usdt") or 0) <= 0) / len(long_trades) * 100, 2) if long_trades else 0.0,
        "short_loss_rate_pct": round(sum(1 for trade in short_trades if float(trade.get("pnl_usdt") or 0) <= 0) / len(short_trades) * 100, 2) if short_trades else 0.0,
    }


def _normalize_trade_feedback(feedback: dict[str, Any]) -> dict[str, Any]:
    normalized = feedback if isinstance(feedback, dict) else {}
    raw_diagnosis = normalized.get("overall_diagnosis")
    if isinstance(raw_diagnosis, dict):
        overall_diagnosis = {
            "headline": str(raw_diagnosis.get("headline") or "总诊断"),
            "summary": str(raw_diagnosis.get("summary") or ""),
        }
    else:
        summary = str(raw_diagnosis or "")
        overall_diagnosis = {
            "headline": "总诊断",
            "summary": summary,
        }

    issue_groups = []
    for index, item in enumerate(normalized.get("issue_groups") or []):
        if not isinstance(item, dict):
            continue
        recommendation = item.get("recommendations", item.get("suggested_fix", []))
        if isinstance(recommendation, str):
            recommendations = [recommendation]
        elif isinstance(recommendation, list):
            recommendations = [str(value) for value in recommendation if str(value).strip()]
        else:
            recommendations = []

        raw_trade_numbers = item.get("trade_numbers") or []
        trade_numbers = []
        for value in raw_trade_numbers:
            try:
                trade_numbers.append(int(value))
            except (TypeError, ValueError):
                continue

        issue_groups.append(
            {
                "title": str(item.get("title") or item.get("issue_name") or f"问题分组 {index + 1}"),
                "pattern": str(item.get("pattern") or item.get("description") or ""),
                "impact": str(item.get("impact") or ""),
                "why_it_happens": str(item.get("why_it_happens") or item.get("evidence") or ""),
                "trade_numbers": trade_numbers,
                "recommendations": recommendations,
            }
        )

    raw_evidence = normalized.get("evidence", [])
    if isinstance(raw_evidence, str):
        evidence = [raw_evidence] if raw_evidence.strip() else []
    elif isinstance(raw_evidence, list):
        evidence = [str(item) for item in raw_evidence if str(item).strip()]
    else:
        evidence = []

    priority_actions = normalized.get("priority_actions", [])
    if not isinstance(priority_actions, list):
        priority_actions = [str(priority_actions)] if str(priority_actions).strip() else []
    else:
        priority_actions = [str(item) for item in priority_actions if str(item).strip()]

    confidence = normalized.get("confidence", 0)
    try:
        confidence_value = float(confidence)
    except (TypeError, ValueError):
        confidence_value = 0.0

    overall_score = normalized.get("overall_score", 0)
    try:
        overall_score_value = int(overall_score)
    except (TypeError, ValueError):
        overall_score_value = 0

    return {
        "overall_score": overall_score_value,
        "overall_diagnosis": overall_diagnosis,
        "issue_groups": issue_groups,
        "priority_actions": priority_actions,
        "evidence": evidence,
        "confidence": confidence_value,
    }


def _parse_feedback_response(content: str) -> dict[str, Any]:
    return json.loads(content)
