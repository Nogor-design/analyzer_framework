from __future__ import annotations

import pandas as pd

from ta_foundation.analysis.regime_recommender.outcomes import summarize_trade_outcomes


def test_summarize_trade_outcomes_computes_metrics():
    trades = pd.DataFrame(
        {
            "profit": [100.0, -50.0, 25.0],
            "mae": [20.0, 40.0, 10.0],
            "mfe": [150.0, 30.0, 60.0],
        }
    )

    out = summarize_trade_outcomes(trades, baseline={"net_pnl": 50, "max_drawdown": -20})
    assert out["trades_count"] == 3
    assert out["net_pnl"] == 75.0
    assert out["max_drawdown"] <= 0.0
    assert out["mae_p50"] >= 0.0
    assert out["mfe_p50"] >= 0.0
    assert out["baseline_net_pnl"] == 50.0
