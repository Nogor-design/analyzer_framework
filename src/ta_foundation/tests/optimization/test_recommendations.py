from ta_foundation.optimization.evaluator import EvaluatedCandidate
from ta_foundation.optimization.recommendations import build_recommendations


def _candidate(**overrides):
    values = {
        "run_id": "BotA",
        "status": "pass",
        "score": 90.0,
        "mode": "breakout",
        "session_bucket": "London Early",
        "start_hour": 0,
        "duration_hours": 2,
        "total_net_profit": 1000.0,
        "profit_factor": 2.0,
        "max_drawdown": -500.0,
        "trades": 20,
        "percent_days_traded": 50.0,
        "recent_trade_delta": 100.0,
        "average_fast": 5,
        "average_slow": 200,
        "use_trend": "false",
        "use_trend_reverse": "false",
        "slow_ma_family": "middle_trend",
        "max_stop": 100,
        "max_tp_ratio": 1.5,
        "profit_stop": 1000,
        "loss_stop": 800,
        "max_trades": 3,
        "risk_shape": "tight_stop_high_rr",
        "direction": "both",
        "bot_name": "Bot A",
        "reasons": "passed hard filters",
    }
    values.update(overrides)
    return EvaluatedCandidate(**values)


def test_build_recommendations_prefers_mode_session_diversity():
    recommendations = build_recommendations(
        [
            _candidate(run_id="BreakoutLondon", score=100, mode="breakout", session_bucket="London Early"),
            _candidate(run_id="BreakoutLondonClone", score=99, mode="breakout", session_bucket="London Early"),
            _candidate(run_id="RegressionLondon", score=80, mode="regression", session_bucket="London Early"),
            _candidate(run_id="BreakoutPremarket", score=70, mode="breakout", session_bucket="Pre-Market"),
        ],
        count=3,
    )

    assert [row.run_id for row in recommendations] == [
        "BreakoutLondon",
        "RegressionLondon",
        "BreakoutPremarket",
    ]


def test_build_recommendations_ignores_rejected_candidates():
    recommendations = build_recommendations(
        [
            _candidate(run_id="Rejected", status="reject", score=100),
            _candidate(run_id="Passed", score=50),
        ],
        count=8,
    )

    assert [row.run_id for row in recommendations] == ["Passed"]
