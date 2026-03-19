from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from ta_foundation.reports.html.sections.anchor_tp_sl_recommendations import render_anchor_tp_sl_recommendations


@dataclass
class _Pkg:
    assets: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)


def test_anchor_tp_sl_recommendations_renders_recommendation_metrics() -> None:
    pkg = _Pkg(
        assets={
            "anchor_interaction": {
                "recommendations": pd.DataFrame(
                    [
                        {
                            "anchor_id": "EMA_21_close",
                            "tp_atr": 1.2,
                            "sl_atr": 0.6,
                            "stability_score": 0.84,
                            "robust_score": 0.31,
                            "expectancy_score": 0.37,
                            "fold_agreement": 0.75,
                            "neighbor_consistency": 0.67,
                            "tail_dependency_share": 0.22,
                            "sample_quality_flag": "ok",
                        }
                    ]
                ),
                "tp_sl_candidates": pd.DataFrame(
                    [
                        {
                            "anchor_id": "EMA_21_close",
                            "tp_atr": 1.2,
                            "sl_atr": 0.6,
                            "stability_score": 0.84,
                            "robust_score": 0.31,
                            "expectancy_score": 0.37,
                            "sample_quality_flag": "ok",
                        }
                    ]
                ),
                "trade_recommendation_alignment": pd.DataFrame(
                    [
                        {
                            "trade_id": 1,
                            "matched_anchor_id": "EMA_21_close",
                            "recommended_tp_atr": 1.2,
                            "recommended_sl_atr": 0.6,
                            "realized_tp_atr": 1.1,
                            "realized_sl_atr": 0.5,
                            "realized_outcome_atr": 0.8,
                            "fit_distance": 0.2,
                            "recommended_sample_quality_flag": "ok",
                        }
                    ]
                ),
            }
        }
    )

    html = render_anchor_tp_sl_recommendations({"packages": {"BronzeApolloGod": pkg}, "options": {}})

    assert "MA Anchor TP/SL Recommendations" in html
    assert "BronzeApolloGod — Recommendations" in html
    assert "BronzeApolloGod — TP/SL Grid" in html
    assert "BronzeApolloGod — Strategy Trade Alignment" in html
    assert "Conservative" in html
    assert "fit_distance" in html


def test_anchor_tp_sl_recommendations_handles_missing_assets() -> None:
    pkg = _Pkg()

    html = render_anchor_tp_sl_recommendations({"packages": {"BronzeApolloGod": pkg}, "options": {}})

    assert "No in-memory MA Anchor recommendations are attached" in html