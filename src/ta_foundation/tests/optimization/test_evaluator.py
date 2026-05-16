from ta_foundation.optimization.evaluator import EvaluationConfig, evaluate_results
from ta_foundation.optimization.result_intake import IntakeResultRow


def _row(**overrides):
    values = {
        "run_id": "BotA",
        "result_path": "",
        "total_net_profit": 1000.0,
        "profit_factor": 2.0,
        "max_drawdown": -500.0,
        "trades": 20,
        "percent_profitable": 0.6,
        "avg_trade": 50.0,
        "start_dt": "",
        "end_dt": "",
        "traded_days": 10,
        "percent_days_traded": 50.0,
        "last_5_trade_profit": 500.0,
        "prior_5_trade_profit": 100.0,
        "recent_trade_delta": 400.0,
        "start_hour": 4,
        "duration_hours": 2,
        "reverse": "false",
        "average_fast": 5,
        "average_slow": 200,
        "use_trend": "false",
        "use_trend_reverse": "false",
        "max_stop": 100,
        "max_tp_ratio": 1.5,
        "profit_stop": 1000,
        "loss_stop": 800,
        "max_trades": 3,
        "long_enabled": "true",
        "short_enabled": "true",
        "bot_name": "Bot A",
        "warnings": "",
    }
    values.update(overrides)
    return IntakeResultRow(**values)


def test_evaluate_results_passes_good_candidate():
    candidates = evaluate_results([_row()], EvaluationConfig())

    assert candidates[0].status == "pass"
    assert candidates[0].mode == "breakout"
    assert candidates[0].session_bucket == "London Late"
    assert candidates[0].use_trend == "false"
    assert candidates[0].use_trend_reverse == "false"
    assert candidates[0].slow_ma_family == "middle_trend"
    assert candidates[0].risk_shape == "tight_stop_high_rr"
    assert candidates[0].profit_stop == 1000
    assert candidates[0].loss_stop == 800
    assert candidates[0].max_trades == 3
    assert candidates[0].direction == "both"
    assert candidates[0].score > 0
    assert candidates[0].reasons == "passed hard filters"


def test_evaluate_results_rejects_bad_candidate_with_reasons():
    candidates = evaluate_results([
        _row(run_id="Bad", total_net_profit=-10, profit_factor=0.8, max_drawdown=-3000, trades=2)
    ])

    candidate = candidates[0]
    assert candidate.status == "reject"
    assert "non-positive net profit" in candidate.reasons
    assert "profit factor below" in candidate.reasons
    assert "drawdown above" in candidate.reasons
    assert "trades below" in candidate.reasons
