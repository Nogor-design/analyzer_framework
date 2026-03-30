from __future__ import annotations

from ta_foundation.core.model import AnalysisPackage
from ta_foundation.reports.html.sections.regime_parameter_recommendation import render_regime_parameter_recommendation


def test_render_regime_parameter_recommendation_renders_payload():
    pkg = AnalysisPackage(
        run_id="run1",
        metadata={
            "derived": {
                "regime_recommender": {
                    "regime": {"regime_id": "trend_up", "primary": "trend_up"},
                    "recommendation": {
                        "confidence": 0.77,
                        "decision": "RECOMMEND_PARAMS",
                        "parameter_reasons": [
                            {"name": "MaxStop", "baseline": 200, "recommended": 230, "because": ["vol_expanding"]}
                        ],
                    },
                    "template_bundle": {
                        "templates": [
                            {"session": "ny_early", "start_time": "07:30", "duration": "02:30", "path": "x.xml"}
                        ]
                    },
                }
            }
        },
    )

    html = render_regime_parameter_recommendation({"packages": {"run1": pkg}, "options": {}})
    assert "Regime Recommendation" in html
    assert "RECOMMEND_PARAMS" in html
    assert "ny_early" in html
    assert "MaxStop" in html
