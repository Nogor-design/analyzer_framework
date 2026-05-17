"""
Smoke tests for the horizon_overview HTML section.

The section must:
  - emit a placeholder when no bundle / store_dir is supplied,
  - render a populated bundle without crashing,
  - load a bundle from a HorizonPredictionStore on disk,
  - be registered in SECTION_REGISTRY under the id `horizon_overview`.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from ta_foundation.prediction.horizon_models import (
    CandleHorizonOutcome,
    CandleHorizonPrediction,
)
from ta_foundation.prediction.horizon_reports import (
    AgentLeaderboardRow,
    HorizonReportBundle,
    build_full_report,
)
from ta_foundation.prediction.horizon_store import HorizonPredictionStore
from ta_foundation.reports.html.registry import SECTION_REGISTRY
from ta_foundation.reports.html.sections.horizon_overview import (
    render_horizon_overview,
)


def _pred(*, agent_id: str, asof: str, p_bull: float = 0.7) -> CandleHorizonPrediction:
    return CandleHorizonPrediction(
        agent_id=agent_id,
        instrument="NQ",
        contract="H25",
        timeframe="5m",
        asof_timestamp=asof,
        session_label="ny_open",
        horizon_candles=3,
        bullish_probability=p_bull,
        bearish_probability=(1 - p_bull) / 2,
        neutral_probability=(1 - p_bull) / 2,
        feature_snapshot={"regime": "trend_up", "prior_atr": 5.0},
        sample_size=50,
        method_used="test",
    )


def _outcome(pred_id: str, *, composite: float = 0.6) -> CandleHorizonOutcome:
    return CandleHorizonOutcome(
        prediction_id=pred_id,
        actual_direction="bullish",
        actual_return_atr=1.0,
        composite_score=composite,
    )


class TestRegistration:
    def test_section_id_registered(self):
        assert "horizon_overview" in SECTION_REGISTRY
        assert SECTION_REGISTRY["horizon_overview"].render_fn is render_horizon_overview


class TestEmptyContext:
    def test_returns_placeholder_when_no_bundle_or_store(self):
        html = render_horizon_overview({})
        assert "No horizon prediction data available" in html
        assert "store_dir" in html

    def test_returns_placeholder_when_options_incomplete(self):
        # store_dir set but no instrument/contract — still incomplete
        html = render_horizon_overview({"options": {"store_dir": "/tmp/horizon"}})
        assert "No horizon prediction data available" in html


class TestRenderBundle:
    def test_renders_in_memory_bundle(self):
        bundle = HorizonReportBundle(
            leaderboard=[
                AgentLeaderboardRow(
                    agent_id="stat",
                    sample_count=50,
                    sample_count_non_abstain=45,
                    abstention_rate=0.10,
                    direction_accuracy=0.62,
                    mean_composite_score=0.55,
                    mean_brier_direction=0.30,
                    ece=0.05,
                    drift_delta=-0.01,
                    drift_flag=False,
                ),
            ],
        )
        html = render_horizon_overview({"horizon_bundle": bundle})
        assert "Agent Leaderboard" in html
        assert "stat" in html
        assert "0.5500" in html      # composite formatted to 4dp
        # Sections that are empty render with their "no data" placeholders
        assert "Best-Edge Cells" in html
        assert "Calibration" in html

    def test_loads_from_store_dir(self, tmp_path: Path):
        store = HorizonPredictionStore(tmp_path, "NQ", "H25")
        for i in range(30):
            asof = f"2026-04-01T10:{i % 60:02d}:00-06:00"
            p = _pred(agent_id="stat", asof=asof)
            o = _outcome(p.prediction_id)
            store.save_prediction(p)
            store.save_outcome(o)

        ctx = {
            "options": {
                "store_dir": str(tmp_path),
                "instrument": "NQ",
                "contract": "H25",
                "min_samples_cell": 5,
                "min_samples_edge": 10,
                "min_samples_calibration": 10,
            },
        }
        html = render_horizon_overview(ctx)
        assert "Agent Leaderboard" in html
        assert "stat" in html
        assert "Timeframe × Horizon" in html
        # Best edge needs a regime, which our predictions have
        assert "Best-Edge Cells" in html

    def test_html_is_self_contained(self, tmp_path: Path):
        bundle = build_full_report(
            HorizonPredictionStore(tmp_path, "NQ", "H25"),
        )
        html = render_horizon_overview({"horizon_bundle": bundle})
        # No external resource references that would break offline viewing
        assert "<script" not in html
        # Top-level wrapper present
        assert html.strip().startswith("<div")
        assert html.strip().endswith("</div>")
