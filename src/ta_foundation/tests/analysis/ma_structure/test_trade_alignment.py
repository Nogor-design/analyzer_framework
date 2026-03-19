from __future__ import annotations

import pandas as pd

from ta_foundation.analysis.ma_structure.trade_alignment import build_trade_recommendation_alignment


def test_build_trade_recommendation_alignment_matches_trade_to_best_recommendation() -> None:
    idx = pd.date_range("2025-02-03 08:00", periods=40, freq="min", tz="America/Denver")
    close = pd.Series([100.0 + (i * 0.1) for i in range(len(idx))])
    bars = pd.DataFrame(
        {
            "timestamp": idx,
            "open": close,
            "high": close + 0.2,
            "low": close - 0.1,
            "close": close,
            "volume": 100,
        }
    ).set_index("timestamp", drop=False)

    trades = pd.DataFrame(
        {
            "trade_id": [1],
            "entry_time": [idx[20]],
            "exit_time": [idx[25]],
            "entry_price": [float(close.iloc[20])],
            "exit_price": [float(close.iloc[25])],
            "market_pos": ["Long"],
        }
    )

    recommendations = pd.DataFrame(
        [
            {"anchor_id": "EMA_21_close", "tp_atr": 0.8, "sl_atr": 0.4, "stability_score": 0.8, "robust_score": 0.3, "sample_quality_flag": "ok"},
            {"anchor_id": "EMA_50_close", "tp_atr": 2.5, "sl_atr": 1.8, "stability_score": 0.6, "robust_score": 0.2, "sample_quality_flag": "thin"},
        ]
    )

    out = build_trade_recommendation_alignment(trades, recommendations, bars)

    assert not out.empty
    assert out.iloc[0]["matched_anchor_id"] == "EMA_21_close"
    assert float(out.iloc[0]["fit_distance"]) >= 0.0
