"""
Phase 1 unit tests for the horizon prediction system.

Covers:
  - horizon_models      (round-trip serialization, normalize_direction_probs)
  - session_classifier  (default windows, DST-safe, midnight-wrap)
  - horizon_outcome_measurer (direction, MFE/MAE, threshold first-hit)
  - horizon_scorer      (Brier, composite, abstain handling, weight normalization)
  - statistical_probability_agent (deterministic baseline + abstain on small sample)
"""
from __future__ import annotations

from datetime import time as dtime

import numpy as np
import pandas as pd
import pytest

from ta_foundation.prediction.horizon_models import (
    CandleHorizonOutcome,
    CandleHorizonPrediction,
)
from ta_foundation.prediction.session_classifier import (
    SessionConfig,
    label_session,
    label_sessions_for_index,
)
from ta_foundation.prediction.horizon_outcome_measurer import measure_horizon_outcome
from ta_foundation.prediction.horizon_scorer import (
    HorizonCompositeWeights,
    score_horizon_prediction,
)
from ta_foundation.prediction.statistical_probability_agent import (
    StatisticalProbabilityAgent,
    StatisticalProbabilityAgentConfig,
)

DENVER = "America/Denver"
NY = "America/New_York"


# ---------------------------------------------------------------------------
# Helpers — synthetic bar series
# ---------------------------------------------------------------------------

def _make_bars(
    n: int,
    seed: int = 7,
    start: str = "2026-01-02 09:30",
    freq: str = "5min",
    drift: float = 0.0,
    sigma: float = 1.0,
    base: float = 20000.0,
) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    times = pd.date_range(start=start, periods=n, freq=freq, tz=NY).tz_convert(DENVER)
    closes = np.cumsum(rng.normal(loc=drift, scale=sigma, size=n)) + base
    bodies = rng.normal(loc=0.0, scale=sigma * 0.5, size=n)
    opens = closes - bodies
    highs = np.maximum(opens, closes) + np.abs(rng.normal(0.0, sigma * 0.4, size=n))
    lows = np.minimum(opens, closes) - np.abs(rng.normal(0.0, sigma * 0.4, size=n))
    return pd.DataFrame({
        "dt": times,
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": rng.integers(100, 1000, size=n),
    })


# ---------------------------------------------------------------------------
# horizon_models
# ---------------------------------------------------------------------------

class TestHorizonModels:
    def test_prediction_round_trip(self):
        p = CandleHorizonPrediction(
            agent_id="test",
            instrument="NQ",
            contract="H25",
            timeframe="5m",
            asof_timestamp="2026-04-21T10:00:00-06:00",
            session_label="ny_open",
            horizon_candles=3,
            bullish_probability=0.55,
            bearish_probability=0.20,
            neutral_probability=0.25,
            sample_size=42,
            method_used="conditional_frequency_v1",
        )
        d = p.as_dict()
        p2 = CandleHorizonPrediction.from_dict(d)
        assert p2.agent_id == "test"
        assert p2.instrument == "NQ"
        assert p2.timeframe == "5m"
        assert p2.bullish_probability == pytest.approx(0.55)
        assert p2.sample_size == 42

    def test_outcome_round_trip(self):
        o = CandleHorizonOutcome(
            prediction_id="abc",
            timeframe="5m",
            horizon_candles=3,
            actual_return_points=12.5,
            actual_direction="bullish",
            upside_threshold_hit=True,
            threshold_hit_order="upside_first",
            composite_score=0.74,
        )
        d = o.as_dict()
        o2 = CandleHorizonOutcome.from_dict(d)
        assert o2.prediction_id == "abc"
        assert o2.actual_direction == "bullish"
        assert o2.upside_threshold_hit is True
        assert o2.threshold_hit_order == "upside_first"
        assert o2.composite_score == pytest.approx(0.74)

    def test_normalize_direction_probs(self):
        p = CandleHorizonPrediction(
            bullish_probability=0.6,
            bearish_probability=0.3,
            neutral_probability=0.1,
        )
        # already sums to 1.0 — no-op
        p.normalize_direction_probs()
        assert p.bullish_probability + p.bearish_probability + p.neutral_probability == pytest.approx(1.0)

        # un-normalized → renormalize
        p2 = CandleHorizonPrediction(
            bullish_probability=2.0,
            bearish_probability=1.0,
            neutral_probability=1.0,
        )
        p2.normalize_direction_probs()
        assert p2.bullish_probability == pytest.approx(0.5)
        assert p2.bearish_probability == pytest.approx(0.25)
        assert p2.neutral_probability == pytest.approx(0.25)

        # all-zero (abstain) → no-op
        p3 = CandleHorizonPrediction(
            bullish_probability=0.0,
            bearish_probability=0.0,
            neutral_probability=0.0,
        )
        p3.normalize_direction_probs()
        assert p3.bullish_probability == 0.0


# ---------------------------------------------------------------------------
# session_classifier
# ---------------------------------------------------------------------------

class TestSessionClassifier:
    def test_default_windows(self):
        ts = pd.Timestamp("2026-04-21 09:35", tz=NY)
        assert label_session(ts) == "ny_open"

        ts = pd.Timestamp("2026-04-21 12:30", tz=NY)
        assert label_session(ts) == "ny_midday"

        ts = pd.Timestamp("2026-04-21 15:30", tz=NY)
        assert label_session(ts) == "ny_close"

        ts = pd.Timestamp("2026-04-21 04:00", tz=NY)
        assert label_session(ts) == "london"

        # asia wraps midnight
        ts = pd.Timestamp("2026-04-21 21:00", tz=NY)
        assert label_session(ts) == "asia"

        ts = pd.Timestamp("2026-04-21 01:00", tz=NY)
        assert label_session(ts) == "asia"

        # 03:00 boundary — london starts here, asia ends here
        ts = pd.Timestamp("2026-04-21 03:00", tz=NY)
        assert label_session(ts) == "london"

    def test_naive_timestamp_rejected(self):
        with pytest.raises(ValueError, match="tz-aware"):
            label_session(pd.Timestamp("2026-04-21 09:35"))

    def test_dst_handling(self):
        # November DST cutover (US): Sunday 02:00 EDT → 01:00 EST
        # 09:30 NY on either side should still be ny_open.
        before = pd.Timestamp("2026-11-01 09:30", tz=NY)
        after = pd.Timestamp("2026-11-08 09:30", tz=NY)
        assert label_session(before) == "ny_open"
        assert label_session(after) == "ny_open"

    def test_vectorized_labelling(self):
        idx = pd.DatetimeIndex(pd.date_range("2026-04-21 00:00", periods=24, freq="h", tz=NY))
        labels = label_sessions_for_index(idx)
        assert len(labels) == 24
        assert labels.iloc[0] == "asia"
        assert labels.iloc[10] == "ny_open"     # 10:00 NY
        assert labels.iloc[12] == "ny_midday"   # 12:00 NY
        assert labels.iloc[15] == "ny_close"    # 15:00 NY

    def test_custom_config(self):
        # Tighter NY-open window
        cfg = SessionConfig(
            windows=[("opening_drive", dtime(9, 30), dtime(10, 0))],
            fallback_label="other",
        )
        ts = pd.Timestamp("2026-04-21 09:45", tz=NY)
        assert label_session(ts, cfg) == "opening_drive"
        ts = pd.Timestamp("2026-04-21 10:30", tz=NY)
        assert label_session(ts, cfg) == "other"


# ---------------------------------------------------------------------------
# horizon_outcome_measurer
# ---------------------------------------------------------------------------

class TestHorizonOutcomeMeasurer:
    def test_directional_bullish_horizon(self):
        # Hand-built monotonic up bars
        bars = pd.DataFrame({
            "dt": pd.date_range("2026-04-21 09:30", periods=10, freq="5min", tz=DENVER),
            "open":  [100, 101, 103, 105, 107, 109, 111, 113, 115, 117],
            "high":  [101, 103, 105, 107, 109, 111, 113, 115, 117, 119],
            "low":   [99,  100, 102, 104, 106, 108, 110, 112, 114, 116],
            "close": [101, 103, 105, 107, 109, 111, 113, 115, 117, 119],
        })
        out = measure_horizon_outcome(
            bars=bars,
            asof_idx=2,         # close=105
            horizon_candles=3,  # → bars 3,4,5 with closes 107,109,111
            prior_atr=2.0,
        )
        assert out.actual_direction == "bullish"
        assert out.actual_return_points == pytest.approx(6.0)
        assert out.actual_return_atr == pytest.approx(3.0)
        # MFE = max(high in bars 3,4,5) - asof_close = 111 - 105 = 6
        assert out.actual_mfe_points == pytest.approx(6.0)
        # MAE: lows in bars 3,4,5 are 104,106,108 → min=104, asof_close=105 → MAE=1
        assert out.actual_mae_points == pytest.approx(1.0)

    def test_directional_bearish_horizon(self):
        bars = pd.DataFrame({
            "dt": pd.date_range("2026-04-21 09:30", periods=10, freq="5min", tz=DENVER),
            "open":  [120, 118, 116, 114, 112, 110, 108, 106, 104, 102],
            "high":  [121, 119, 117, 115, 113, 111, 109, 107, 105, 103],
            "low":   [118, 116, 114, 112, 110, 108, 106, 104, 102, 100],
            "close": [118, 116, 114, 112, 110, 108, 106, 104, 102, 100],
        })
        out = measure_horizon_outcome(bars=bars, asof_idx=1, horizon_candles=3, prior_atr=2.0)
        # asof_close=116, future closes=114,112,110 → final 110, return=-6
        assert out.actual_direction == "bearish"
        assert out.actual_return_points == pytest.approx(-6.0)

    def test_neutral_horizon(self):
        # Tight chop: net displacement < 0.30 * ATR
        bars = pd.DataFrame({
            "dt": pd.date_range("2026-04-21 09:30", periods=10, freq="5min", tz=DENVER),
            "open":  [100] * 10,
            "high":  [101] * 10,
            "low":   [99] * 10,
            "close": [100] * 10,
        })
        out = measure_horizon_outcome(bars=bars, asof_idx=2, horizon_candles=3, prior_atr=2.0)
        assert out.actual_direction == "neutral"
        assert out.actual_return_points == pytest.approx(0.0)

    def test_threshold_upside_first(self):
        bars = pd.DataFrame({
            "dt": pd.date_range("2026-04-21 09:30", periods=10, freq="5min", tz=DENVER),
            "open":  [100, 100, 100, 100, 100, 100, 100, 100, 100, 100],
            "high":  [101, 101, 102, 105, 105, 105, 105, 105, 105, 105],   # bar idx 3 hits +5 first
            "low":   [99,  99,  99,  100, 100, 100, 100, 100, 100, 100],
            "close": [100, 100, 101, 105, 105, 105, 105, 105, 105, 105],
        })
        out = measure_horizon_outcome(
            bars=bars,
            asof_idx=1,
            horizon_candles=5,
            prior_atr=2.0,
            upside_threshold_points=4.0,
            downside_threshold_points=4.0,
        )
        assert out.upside_threshold_hit is True
        assert out.downside_threshold_hit is False
        assert out.threshold_hit_order == "upside_first"

    def test_threshold_downside_first(self):
        bars = pd.DataFrame({
            "dt": pd.date_range("2026-04-21 09:30", periods=10, freq="5min", tz=DENVER),
            "open":  [100, 100, 100, 100, 100, 100, 100, 100, 100, 100],
            "high":  [101, 101, 101, 101, 101, 101, 101, 101, 101, 101],
            "low":   [99,  99,  99,  93,  93,  93,  93,  93,  93,  93],   # bar idx 3 hits -7
            "close": [100, 100, 100, 95,  95,  95,  95,  95,  95,  95],
        })
        out = measure_horizon_outcome(
            bars=bars,
            asof_idx=1,
            horizon_candles=5,
            prior_atr=2.0,
            upside_threshold_points=4.0,
            downside_threshold_points=4.0,
        )
        assert out.upside_threshold_hit is False
        assert out.downside_threshold_hit is True
        assert out.threshold_hit_order == "downside_first"

    def test_threshold_neither(self):
        bars = pd.DataFrame({
            "dt": pd.date_range("2026-04-21 09:30", periods=10, freq="5min", tz=DENVER),
            "open":  [100] * 10,
            "high":  [101] * 10,
            "low":   [99]  * 10,
            "close": [100] * 10,
        })
        out = measure_horizon_outcome(
            bars=bars,
            asof_idx=1,
            horizon_candles=5,
            prior_atr=2.0,
            upside_threshold_points=4.0,
            downside_threshold_points=4.0,
        )
        assert out.threshold_hit_order == "neither"

    def test_invalid_inputs(self):
        bars = _make_bars(20)
        with pytest.raises(ValueError, match="horizon_candles"):
            measure_horizon_outcome(bars, asof_idx=5, horizon_candles=0, prior_atr=1.0)
        with pytest.raises(ValueError, match="asof_idx"):
            measure_horizon_outcome(bars, asof_idx=-1, horizon_candles=3, prior_atr=1.0)
        with pytest.raises(ValueError, match="empty"):
            measure_horizon_outcome(pd.DataFrame(), asof_idx=0, horizon_candles=3, prior_atr=1.0)

    def test_horizon_truncated_at_end_of_data(self):
        # Asof within `horizon` bars of the end of the series — should still
        # return a valid outcome with horizon_bars_observed < horizon_candles.
        bars = _make_bars(10)
        out = measure_horizon_outcome(bars, asof_idx=8, horizon_candles=5, prior_atr=2.0)
        # Only 1 bar after asof_idx=8 (the last bar at idx=9)
        assert out.horizon_bars_observed == 1


# ---------------------------------------------------------------------------
# horizon_scorer
# ---------------------------------------------------------------------------

class TestHorizonScorer:
    def _pred_outcome_pair(self):
        pred = CandleHorizonPrediction(
            timeframe="5m",
            horizon_candles=3,
            bullish_probability=0.7,
            bearish_probability=0.2,
            neutral_probability=0.1,
            expected_return_points=5.0,
            expected_mfe_points=6.0,
            expected_mae_points=2.0,
            upside_threshold_probability=0.6,
            downside_threshold_probability=0.2,
            neither_threshold_probability=0.2,
        )
        out = CandleHorizonOutcome(
            timeframe="5m",
            horizon_candles=3,
            actual_return_points=4.0,
            actual_direction="bullish",
            actual_mfe_points=5.0,
            actual_mae_points=1.5,
            threshold_hit_order="upside_first",
            upside_threshold_hit=True,
            prior_atr=2.0,
        )
        return pred, out

    def test_correct_confident_call_scores_well(self):
        pred, out = self._pred_outcome_pair()
        score_horizon_prediction(pred, out)
        # Direction was bullish predicted with 0.7 → Brier should be small
        assert out.brier_score_direction < 0.5
        assert out.direction_score > 0.7
        assert out.composite_score > 0.6

    def test_wrong_confident_call_scores_poorly(self):
        pred = CandleHorizonPrediction(
            bullish_probability=0.9,
            bearish_probability=0.05,
            neutral_probability=0.05,
            expected_return_points=10.0,
            upside_threshold_probability=0.85,
            downside_threshold_probability=0.05,
            neither_threshold_probability=0.10,
            expected_mfe_points=12.0,
            expected_mae_points=1.0,
        )
        out = CandleHorizonOutcome(
            actual_return_points=-8.0,
            actual_direction="bearish",
            actual_mfe_points=1.0,
            actual_mae_points=10.0,
            threshold_hit_order="downside_first",
            downside_threshold_hit=True,
            prior_atr=2.0,
        )
        score_horizon_prediction(pred, out)
        # Confident bullish call but bearish realized → Brier ~ (0.9-0)² + (0.05-1)² + (0.05-0)² = 0.81 + 0.9025 + 0.0025 = 1.715
        assert out.brier_score_direction > 1.0
        assert out.direction_score < 0.3
        assert out.composite_score < 0.5

    def test_abstain_skips_scoring(self):
        pred = CandleHorizonPrediction(abstain=True, abstain_reason="insufficient_samples")
        out = CandleHorizonOutcome(actual_direction="bullish", prior_atr=1.0)
        score_horizon_prediction(pred, out, calibration_error=0.2)
        # All non-calibration scores stay at zero
        assert out.composite_score == 0.0
        assert out.direction_score == 0.0
        assert out.calibration_error == pytest.approx(0.2)

    def test_weights_normalize(self):
        # Weights that don't sum to 1.0 — scorer must normalize them
        weights = HorizonCompositeWeights(
            direction=10.0, thresholds=10.0, return_=5.0, path=2.5, calibration=2.5,
        )
        validated = weights.validated()
        total = (
            validated.direction + validated.thresholds + validated.return_
            + validated.path + validated.calibration
        )
        assert total == pytest.approx(1.0)
        assert validated.direction == pytest.approx(10.0 / 30.0)

    def test_zero_weights_rejected(self):
        with pytest.raises(ValueError):
            HorizonCompositeWeights(
                direction=0.0, thresholds=0.0, return_=0.0, path=0.0, calibration=0.0,
            ).validated()

    def test_log_loss_finite_when_actual_class_zero_probability(self):
        pred = CandleHorizonPrediction(
            bullish_probability=0.0,    # actual is bullish → log loss should be finite, not inf
            bearish_probability=0.5,
            neutral_probability=0.5,
        )
        out = CandleHorizonOutcome(actual_direction="bullish", prior_atr=1.0)
        score_horizon_prediction(pred, out)
        assert np.isfinite(out.log_loss_direction)
        assert out.log_loss_direction > 5.0   # very large but not infinite


# ---------------------------------------------------------------------------
# statistical_probability_agent
# ---------------------------------------------------------------------------

class TestStatisticalProbabilityAgent:
    def test_predicts_with_enough_history(self):
        bars = _make_bars(2000, seed=11)
        agent = StatisticalProbabilityAgent(
            config=StatisticalProbabilityAgentConfig(
                min_samples_local=8,
                min_samples_global=20,
                history_lookback_bars=1500,
            ),
        )
        pred = agent.predict(
            bars=bars,
            asof_idx=1900,
            horizon_candles=3,
            instrument="NQ",
            contract="H25",
            timeframe="5m",
        )
        assert pred.abstain is False
        assert pred.timeframe == "5m"
        assert pred.horizon_candles == 3
        assert pred.session_label  # always non-empty
        # Direction probabilities sum to ~1.0
        total = pred.bullish_probability + pred.bearish_probability + pred.neutral_probability
        assert total == pytest.approx(1.0, abs=1e-6)
        # Threshold probabilities sum to <= 1.0 (neither = 1 - up - down clipped)
        assert pred.upside_threshold_probability + pred.downside_threshold_probability <= 1.0 + 1e-6
        # Sample size positive
        assert pred.sample_size > 0
        assert pred.fallback_level in (0, 1, 2)
        assert pred.method_used == "conditional_frequency_v1"

    def test_abstains_on_tiny_history(self):
        bars = _make_bars(80, seed=3)
        agent = StatisticalProbabilityAgent(
            config=StatisticalProbabilityAgentConfig(
                min_samples_local=8,
                min_samples_global=200,   # impossibly large for 80 bars
            ),
        )
        pred = agent.predict(
            bars=bars,
            asof_idx=70,
            horizon_candles=5,
            instrument="NQ",
            contract="H25",
            timeframe="5m",
        )
        assert pred.abstain is True
        assert pred.abstain_reason == "insufficient_samples"

    def test_walk_forward_no_leakage(self):
        # Real leakage check: the same asof produces an identical prediction
        # whether or not future bars exist in the series. If bars after asof
        # leaked in, the two predictions would differ.
        bars_full = _make_bars(2000, seed=21)
        # Truncated series ends ~10 bars after asof — only enough for the
        # prediction's own horizon to be measurable, no further history.
        asof = 1500
        horizon = 3
        bars_truncated = bars_full.iloc[: asof + horizon + 1].reset_index(drop=True)

        agent = StatisticalProbabilityAgent()

        pred_full = agent.predict(
            bars=bars_full, asof_idx=asof, horizon_candles=horizon,
            instrument="NQ", contract="H25", timeframe="5m",
        )
        pred_trunc = agent.predict(
            bars=bars_truncated, asof_idx=asof, horizon_candles=horizon,
            instrument="NQ", contract="H25", timeframe="5m",
        )

        assert pred_full.sample_size == pred_trunc.sample_size
        assert pred_full.fallback_level == pred_trunc.fallback_level
        assert pred_full.bullish_probability == pytest.approx(pred_trunc.bullish_probability)
        assert pred_full.bearish_probability == pytest.approx(pred_trunc.bearish_probability)
        assert pred_full.expected_return_points == pytest.approx(pred_trunc.expected_return_points)
        assert pred_full.upside_threshold_probability == pytest.approx(
            pred_trunc.upside_threshold_probability
        )

    def test_deterministic_under_fixed_seed(self):
        bars = _make_bars(1500, seed=99)
        agent = StatisticalProbabilityAgent()
        a = agent.predict(bars, 1400, 3, "NQ", "H25", "5m")
        b = agent.predict(bars, 1400, 3, "NQ", "H25", "5m")
        assert a.bullish_probability == pytest.approx(b.bullish_probability)
        assert a.expected_return_points == pytest.approx(b.expected_return_points)
        assert a.sample_size == b.sample_size

    def test_full_lifecycle_with_outcome_and_score(self):
        # End-to-end: predict → measure → score → composite_score is finite
        bars = _make_bars(2000, seed=42)
        agent = StatisticalProbabilityAgent()
        asof = 1900
        pred = agent.predict(bars, asof, 3, "NQ", "H25", "5m")
        if pred.abstain:
            pytest.skip("Agent abstained; lifecycle test requires a non-abstain prediction")

        out = measure_horizon_outcome(
            bars=bars,
            asof_idx=asof,
            horizon_candles=3,
            prior_atr=pred.feature_snapshot["prior_atr"],
            upside_threshold_points=pred.upside_threshold_points,
            downside_threshold_points=pred.downside_threshold_points,
            prediction=pred,
        )
        assert out.prediction_id == pred.prediction_id
        assert out.timeframe == "5m"
        assert out.horizon_candles == 3

        score_horizon_prediction(pred, out)
        assert np.isfinite(out.composite_score)
        assert 0.0 <= out.composite_score <= 1.0
        assert 0.0 <= out.direction_score <= 1.0
        assert 0.0 <= out.threshold_score <= 1.0
