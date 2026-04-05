import json

from bitget_bot import ai_feedback


def test_parse_feedback_response_supports_plain_json_fallback():
    content = json.dumps(
        {
            "overall_score": 81,
            "stop_loss_assessment": {"label": "neutral", "score": 70, "summary": "ok"},
            "take_profit_assessment": {"label": "early", "score": 58, "summary": "too early"},
            "robustness_assessment": {"label": "moderate", "score": 67, "summary": "mixed"},
            "top_issues": [],
            "recommended_experiments": [],
            "parameter_adjustment_hints": [],
            "confidence": 0.74,
            "evidence": [],
        }
    )

    parsed = ai_feedback._parse_feedback_response(content)

    assert parsed["overall_score"] == 81
    assert parsed["take_profit_assessment"]["label"] == "early"


def test_analyze_experiment_falls_back_when_json_schema_is_unsupported(monkeypatch):
    calls = {"count": 0}

    class _Message:
        def __init__(self, content):
            self.content = content

    class _Choice:
        def __init__(self, content):
            self.message = _Message(content)

    class _Response:
        def __init__(self, content):
            self.choices = [_Choice(content)]

    class _Completions:
        @staticmethod
        def create(**kwargs):
            calls["count"] += 1
            if calls["count"] == 1:
                raise Exception("This response_format type is unavailable now")
            return _Response(
                json.dumps(
                    {
                        "overall_score": 77,
                        "stop_loss_assessment": {"label": "tight", "score": 52, "summary": "too tight"},
                        "take_profit_assessment": {"label": "neutral", "score": 66, "summary": "ok"},
                        "robustness_assessment": {"label": "moderate", "score": 61, "summary": "mixed"},
                        "top_issues": ["stop loss too tight"],
                        "recommended_experiments": [],
                        "parameter_adjustment_hints": ["widen stop loss"],
                        "confidence": 0.68,
                        "evidence": ["high stop-out rate"],
                    }
                )
            )

    class _Chat:
        completions = _Completions()

    class _Client:
        chat = _Chat()

    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(ai_feedback, "OpenAI", lambda **kwargs: _Client())

    result = ai_feedback.analyze_experiment({"id": 1}, [{"scenario_tag": "15m-90d", "params": {}, "result": {"summary": {}, "trades": []}}])

    assert calls["count"] == 2
    assert result["feedback"]["overall_score"] == 77


def test_analyze_backtest_falls_back_when_json_schema_is_unsupported(monkeypatch):
    calls = {"count": 0}

    class _Message:
        def __init__(self, content):
            self.content = content

    class _Choice:
        def __init__(self, content):
            self.message = _Message(content)

    class _Response:
        def __init__(self, content):
            self.choices = [_Choice(content)]

    class _Completions:
        @staticmethod
        def create(**kwargs):
            calls["count"] += 1
            if calls["count"] == 1:
                raise Exception("This response_format type is unavailable now")
            return _Response(
                json.dumps(
                    {
                        "overall_score": 72,
                        "overall_diagnosis": {
                            "headline": "Too many weak entries",
                            "summary": "Repeated short-duration losses indicate low-quality entries.",
                        },
                        "issue_groups": [
                            {
                                "title": "Quick stop-outs",
                                "pattern": "Several trades lose within a few bars.",
                                "impact": "Frequent small losses accumulate.",
                                "why_it_happens": "Entry confirmation is too weak.",
                                "trade_numbers": [2, 4, 5],
                                "recommendations": ["Require stronger breakout confirmation"],
                            }
                        ],
                        "priority_actions": ["Reduce entries in chop"],
                        "evidence": ["3 quick losses within 5 bars"],
                        "confidence": 0.81,
                    }
                )
            )

    class _Chat:
        completions = _Completions()

    class _Client:
        chat = _Chat()

    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(ai_feedback, "OpenAI", lambda **kwargs: _Client())

    result = ai_feedback.analyze_backtest(
        {
            "job_id": "sb_demo",
            "summary": {"total_trades": 3},
            "trades": [
                {"direction": "long", "pnl_usdt": -10, "holding_bars": 1},
                {"direction": "long", "pnl_usdt": -8, "holding_bars": 2},
                {"direction": "short", "pnl_usdt": 15, "holding_bars": 6},
            ],
        }
    )

    assert calls["count"] == 2
    assert result["feedback"]["overall_diagnosis"]["headline"] == "Too many weak entries"


def test_analyze_backtest_normalizes_alias_fields_from_json_object_fallback(monkeypatch):
    calls = {"count": 0}

    class _Message:
        def __init__(self, content):
            self.content = content

    class _Choice:
        def __init__(self, content):
            self.message = _Message(content)

    class _Response:
        def __init__(self, content):
            self.choices = [_Choice(content)]

    class _Completions:
        @staticmethod
        def create(**kwargs):
            calls["count"] += 1
            if calls["count"] == 1:
                raise Exception("This response_format type is unavailable now")
            return _Response(
                json.dumps(
                    {
                        "overall_score": 4,
                        "overall_diagnosis": "Frequent weak entries cause repeated small losses.",
                        "issue_groups": [
                            {
                                "issue_name": "Overtrading cluster",
                                "description": "Several losses happen in a tight time window.",
                                "trade_numbers": [11, 12, 13],
                                "evidence": "Trades 11-13 all lost quickly after entry.",
                                "suggested_fix": "Add a cooldown after a loss.",
                            }
                        ],
                        "priority_actions": ["Reduce trade frequency"],
                        "evidence": "Quick losses dominate the sample.",
                        "confidence": 0.88,
                    }
                )
            )

    class _Chat:
        completions = _Completions()

    class _Client:
        chat = _Chat()

    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(ai_feedback, "OpenAI", lambda **kwargs: _Client())

    result = ai_feedback.analyze_backtest(
        {
            "job_id": "sb_demo",
            "summary": {"total_trades": 3},
            "trades": [
                {"direction": "long", "pnl_usdt": -10, "holding_bars": 1},
                {"direction": "long", "pnl_usdt": -8, "holding_bars": 2},
                {"direction": "short", "pnl_usdt": 15, "holding_bars": 6},
            ],
        }
    )

    assert calls["count"] == 2
    assert result["feedback"]["overall_diagnosis"]["headline"] == "总诊断"
    assert result["feedback"]["overall_diagnosis"]["summary"] == "Frequent weak entries cause repeated small losses."
    assert result["feedback"]["issue_groups"][0]["title"] == "Overtrading cluster"
    assert result["feedback"]["issue_groups"][0]["pattern"] == "Several losses happen in a tight time window."
    assert result["feedback"]["issue_groups"][0]["why_it_happens"] == "Trades 11-13 all lost quickly after entry."
    assert result["feedback"]["issue_groups"][0]["recommendations"] == ["Add a cooldown after a loss."]
    assert result["feedback"]["evidence"] == ["Quick losses dominate the sample."]
