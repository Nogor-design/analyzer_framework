from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd
import pytest

from ta_foundation.prediction.models import (
    AgentStats,
    DailyPrediction,
    LevelOutcome,
    PredictionOutcome,
    SimilarContext,
)
from ta_foundation.prediction.outcome_measurer import measure_outcome
from ta_foundation.prediction.scorer import score_prediction
from ta_foundation.prediction.calibrator import (
    compute_agent_stats,
    extract_feature_vector,
    find_similar_contexts,
)
from ta_foundation.prediction.store import DuplicateOutcomeError, PredictionStore
from ta_foundation.prediction.orchestrator import (
    InsufficientBarsError,
    PredictionValidationError,
    measure_and_learn,
    predict_next_day,
    statistical_stub_agent,
    _validate_agent_response,
)

TZ = "America/Denver"
RNG = np.random.default_rng(7)


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------

def _prediction(
    target_date: str = "2026-04-21",
    trend_dir: str = "bullish",
    trend_conf: float = 0.8,
    is_trending: bool = True,
    chop_conf: float = 0.75,
    breakout_prob: float = 0.3,
    breakout_dir: str = "none",
    key_levels=None,
    agent_id: str = "agent_a",
) -> DailyPrediction:
    if key_levels is None:
        key_levels = [
            {"price": 19100.0, "label": "PDH", "level_type": "resistance", "touch_probability": 0.7},
            {"price": 18950.0, "label": "PDL", "level_type": "support", "touch_probability": 0.6},
            {"price": 19025.0, "label": "PP", "level_type": "pivot", "touch_probability": 0.5},
        ]
    return DailyPrediction(
        instrument="NQ",
        contract="H25",
        target_date=target_date,
        agent_id=agent_id,
        trend_direction=trend_dir,
        trend_confidence=trend_conf,
        is_trending=is_trending,
        chop_confidence=chop_conf,
        predicted_high=19200.0,
        predicted_low=18900.0,
        breakout_probability=breakout_prob,
        breakout_direction=breakout_dir,
        event_risk_score=0.2,
        key_levels=key_levels,
        reasoning="test prediction",
        context_snapshot={
            "regime_label": "trend_up",
            "had_high_impact_event": False,
            "feature_vector": [0.6, 0.55, 0.4, 0.3, 0.9, 0.2, 0.35, 0.55],
        },
    )


def _make_bars(
    open_price: float = 19000.0,
    trend: float = 0.05,         # per bar
    n_bars: int = 390,
    tz: str = TZ,
) -> pd.DataFrame:
    """Generate minute bars for one session."""
    start = pd.Timestamp("2026-04-21 08:00", tz=tz)
    rows = []
    price = open_price
    for i in range(n_bars):
        price += trend
        rows.append({
            "dt": start + pd.Timedelta(minutes=i),
            "open": round(price - 0.1, 2),
            "high": round(price + 0.5, 2),
            "low": round(price - 0.5, 2),
            "close": round(price, 2),
            "volume": 100.0,
        })
    return pd.DataFrame(rows)


def _outcome_after_score(
    prediction: DailyPrediction,
    bars: pd.DataFrame,
    prior_atr: float = 20.0,
) -> PredictionOutcome:
    outcome = measure_outcome(prediction, bars, prior_atr)
    score_prediction(prediction, outcome)
    return outcome


# ===========================================================================
# MODELS — serialization roundtrip
# ===========================================================================


class TestModelRoundtrip:
    def test_prediction_roundtrip(self):
        pred = _prediction()
        d = pred.as_dict()
        restored = DailyPrediction.from_dict(d)
        assert restored.prediction_id == pred.prediction_id
        assert restored.trend_direction == pred.trend_direction
        assert restored.key_levels == pred.key_levels

    def test_outcome_roundtrip(self):
        pred = _prediction()
        bars = _make_bars()
        outcome = _outcome_after_score(pred, bars)
        d = outcome.as_dict()
        restored = PredictionOutcome.from_dict(d)
        assert restored.prediction_id == outcome.prediction_id
        assert abs(restored.composite_score - outcome.composite_score) < 1e-6
        assert len(restored.levels_touched) == len(outcome.levels_touched)

    def test_level_outcome_roundtrip(self):
        lo = LevelOutcome(price=19100.0, label="PDH", was_touched=True, touch_price=19099.5, touch_bar_index=42)
        restored = LevelOutcome.from_dict(lo.as_dict())
        assert restored.price == lo.price
        assert restored.touch_bar_index == lo.touch_bar_index

    def test_agent_stats_roundtrip(self):
        stats = AgentStats(agent_id="x", sample_count=10, trend_accuracy=0.65)
        restored = AgentStats.from_dict(stats.as_dict())
        assert restored.agent_id == "x"
        assert restored.trend_accuracy == 0.65

    def test_prediction_json_safe(self):
        pred = _prediction()
        json.dumps(pred.as_dict())  # must not raise

    def test_outcome_json_safe(self):
        pred = _prediction()
        bars = _make_bars()
        outcome = _outcome_after_score(pred, bars)
        json.dumps(outcome.as_dict())

    def test_similar_context_as_dict_no_recursion(self):
        pred = _prediction()
        bars = _make_bars()
        outcome = _outcome_after_score(pred, bars)
        sc = SimilarContext(
            target_date="2026-04-21",
            similarity_score=0.85,
            prediction=pred,
            outcome=outcome,
            feature_vector=[0.5] * 8,
        )
        d = sc.as_dict()
        # context_snapshot must NOT be in the serialized output (recursion guard)
        assert "context_snapshot" not in json.dumps(d)
        json.dumps(d)  # must be serializable


# ===========================================================================
# OUTCOME MEASURER
# ===========================================================================


class TestOutcomeMeasurer:
    def test_trend_direction_bullish(self):
        pred = _prediction(trend_dir="bullish")
        bars = _make_bars(open_price=19000.0, trend=0.1)  # strong uptrend
        outcome = measure_outcome(pred, bars, prior_atr=20.0)
        assert outcome.actual_direction == "bullish"
        assert outcome.actual_close > outcome.actual_open

    def test_trend_direction_bearish(self):
        pred = _prediction(trend_dir="bearish")
        bars = _make_bars(open_price=19100.0, trend=-0.1)
        outcome = measure_outcome(pred, bars, prior_atr=20.0)
        assert outcome.actual_direction == "bearish"

    def test_trend_direction_neutral_small_move(self):
        pred = _prediction()
        bars = _make_bars(open_price=19000.0, trend=0.001)  # tiny drift
        outcome = measure_outcome(pred, bars, prior_atr=200.0)  # large ATR → tiny move = neutral
        assert outcome.actual_direction == "neutral"

    def test_efficiency_ratio_trending_bars(self):
        pred = _prediction()
        bars = _make_bars(trend=0.1)  # consistent trend, high ER
        outcome = measure_outcome(pred, bars, prior_atr=20.0)
        assert outcome.efficiency_ratio > 0.2

    def test_efficiency_ratio_choppy_bars(self):
        """Alternating bars should produce a low efficiency ratio."""
        pred = _prediction()
        n = 100
        prices = [19000.0 + (10 if i % 2 == 0 else -10) for i in range(n)]
        start = pd.Timestamp("2026-04-21 08:00", tz=TZ)
        bars = pd.DataFrame({
            "dt": [start + pd.Timedelta(minutes=i) for i in range(n)],
            "open": prices,
            "high": [p + 0.5 for p in prices],
            "low": [p - 0.5 for p in prices],
            "close": prices,
            "volume": [100.0] * n,
        })
        outcome = measure_outcome(pred, bars, prior_atr=20.0)
        assert outcome.efficiency_ratio < 0.5

    def test_level_touch_detected(self):
        """PDH at 19100 should be touched if bars reach that level."""
        pred = _prediction(key_levels=[
            {"price": 19050.0, "label": "target_level", "level_type": "resistance", "touch_probability": 0.7}
        ])
        bars = _make_bars(open_price=19000.0, trend=0.2)  # rises well above 19050
        outcome = measure_outcome(pred, bars, prior_atr=20.0)
        touches = {lo.label: lo.was_touched for lo in outcome.levels_touched}
        assert touches.get("target_level") is True

    def test_level_not_touched_far_away(self):
        pred = _prediction(key_levels=[
            {"price": 20000.0, "label": "far_level", "level_type": "resistance", "touch_probability": 0.2}
        ])
        bars = _make_bars(open_price=19000.0, trend=0.0)  # stays near 19000
        outcome = measure_outcome(pred, bars, prior_atr=20.0)
        touches = {lo.label: lo.was_touched for lo in outcome.levels_touched}
        assert touches.get("far_level") is False

    def test_breakout_detected_upside(self):
        """Strong upward trend after early range should trigger breakout."""
        pred = _prediction()
        bars = _make_bars(open_price=19000.0, trend=0.5)  # aggressive uptrend
        outcome = measure_outcome(pred, bars, prior_atr=20.0)
        assert outcome.breakout_occurred is True
        assert outcome.breakout_direction_actual == "up"

    def test_no_breakout_in_range_day(self):
        pred = _prediction()
        bars = _make_bars(open_price=19000.0, trend=0.0)
        outcome = measure_outcome(pred, bars, prior_atr=200.0)  # tiny range vs huge ATR
        assert outcome.breakout_occurred is False

    def test_atr_fallback_for_zero_atr(self):
        pred = _prediction()
        bars = _make_bars()
        outcome = measure_outcome(pred, bars, prior_atr=0.0)
        assert outcome.prior_atr == -1.0  # sentinel for fallback

    def test_empty_bars_returns_default_outcome(self):
        pred = _prediction()
        empty = pd.DataFrame(columns=["dt", "open", "high", "low", "close", "volume"])
        outcome = measure_outcome(pred, empty, prior_atr=20.0)
        assert outcome.actual_open == 0.0
        assert outcome.prediction_id == pred.prediction_id

    def test_actual_ohlc_correctness(self):
        pred = _prediction()
        bars = _make_bars(open_price=19000.0, trend=0.05)
        outcome = measure_outcome(pred, bars, prior_atr=20.0)
        assert outcome.actual_open == pytest.approx(bars.iloc[0]["open"], abs=0.01)
        assert outcome.actual_high == pytest.approx(bars["high"].max(), abs=0.01)
        assert outcome.actual_low == pytest.approx(bars["low"].min(), abs=0.01)
        assert outcome.actual_close == pytest.approx(bars.iloc[-1]["close"], abs=0.01)


# ===========================================================================
# SCORER
# ===========================================================================


class TestScorer:
    def test_composite_weights_sum_to_one(self):
        from ta_foundation.prediction.scorer import _W_BREAKOUT, _W_CHOP, _W_LEVELS, _W_TREND
        assert abs(_W_TREND + _W_CHOP + _W_LEVELS + _W_BREAKOUT - 1.0) < 1e-9

    def test_composite_in_range(self):
        pred = _prediction()
        bars = _make_bars()
        outcome = _outcome_after_score(pred, bars)
        assert 0.0 <= outcome.composite_score <= 1.0

    def test_confident_correct_trend_scores_high(self):
        pred = _prediction(trend_dir="bullish", trend_conf=0.95, is_trending=True)
        bars = _make_bars(trend=0.15)  # definitely bullish
        outcome = measure_outcome(pred, bars, prior_atr=20.0)
        # Manually set actual_direction to bullish for isolated test
        outcome.actual_direction = "bullish"
        score_prediction(pred, outcome)
        assert outcome.trend_score > 0.8

    def test_confident_wrong_trend_scores_low(self):
        pred = _prediction(trend_dir="bullish", trend_conf=0.95)
        bars = _make_bars(trend=-0.15)  # bearish
        outcome = measure_outcome(pred, bars, prior_atr=20.0)
        outcome.actual_direction = "bearish"
        score_prediction(pred, outcome)
        assert outcome.trend_score < 0.2

    def test_50pct_confidence_always_scores_half(self):
        """At 0.5 confidence, trend_score should be exactly 0.5 regardless of outcome."""
        pred_right = _prediction(trend_dir="bullish", trend_conf=0.5)
        pred_wrong = _prediction(trend_dir="bullish", trend_conf=0.5)

        bars = _make_bars()
        o_right = measure_outcome(pred_right, bars, 20.0)
        o_wrong = measure_outcome(pred_wrong, bars, 20.0)

        o_right.actual_direction = "bullish"
        o_wrong.actual_direction = "bearish"
        score_prediction(pred_right, o_right)
        score_prediction(pred_wrong, o_wrong)

        assert abs(o_right.trend_score - 0.5) < 1e-9
        assert abs(o_wrong.trend_score - 0.5) < 1e-9

    def test_breakout_brier_score_correct_call(self):
        """Confident correct breakout prediction should score near 1.0."""
        pred = _prediction(breakout_prob=0.9, breakout_dir="up")
        bars = _make_bars(trend=0.5)  # strong breakout
        outcome = measure_outcome(pred, bars, prior_atr=20.0)
        outcome.breakout_occurred = True
        outcome.breakout_direction_actual = "up"
        score_prediction(pred, outcome)
        assert outcome.breakout_score > 0.8

    def test_breakout_brier_score_wrong_call(self):
        """Confident wrong breakout prediction should score near 0.0."""
        pred = _prediction(breakout_prob=0.9)
        bars = _make_bars(trend=0.0)
        outcome = measure_outcome(pred, bars, prior_atr=200.0)
        outcome.breakout_occurred = False
        score_prediction(pred, outcome)
        assert outcome.breakout_score < 0.2

    def test_levels_score_neutral_when_no_levels(self):
        pred = _prediction(key_levels=[])
        bars = _make_bars()
        outcome = measure_outcome(pred, bars, 20.0)
        score_prediction(pred, outcome)
        assert outcome.levels_score == pytest.approx(0.5, abs=0.01)

    def test_all_scores_populated_after_scoring(self):
        pred = _prediction()
        bars = _make_bars()
        outcome = _outcome_after_score(pred, bars)
        for attr in ("trend_score", "chop_score", "levels_score", "breakout_score", "composite_score"):
            assert getattr(outcome, attr) > 0.0 or getattr(outcome, attr) == 0.0  # just not None


# ===========================================================================
# CALIBRATOR
# ===========================================================================


class TestCalibrator:
    def _make_feature_vec(self, slope: float = 0.6) -> list:
        return [slope, slope * 0.9, 0.4, 0.3, 0.8, 0.2, 0.35, 0.5]

    def test_extract_feature_vector_returns_8_components(self):
        fv = {"feature_values": {
            "tf240m_trend_slope": 2.0,
            "tf60m_trend_slope": 1.5,
            "tf15m_compression_ratio": 1.2,
            "tf60m_trend_strength": 0.4,
            "cross_tf_agreement": 0.9,
            "tf240m_trend_strength": 0.3,
            "tf15m_vwap_dist_atr": 0.5,
        }}
        vec = extract_feature_vector(fv, event_risk_score=0.1)
        assert len(vec) == 8
        assert all(0.0 <= v <= 1.0 for v in vec)

    def test_extract_feature_vector_handles_missing_keys(self):
        vec = extract_feature_vector({}, event_risk_score=0.0)
        assert len(vec) == 8

    def test_find_similar_contexts_returns_ordered_by_similarity(self):
        pred_a = _prediction()
        pred_a.context_snapshot["feature_vector"] = self._make_feature_vec(0.6)
        pred_b = _prediction()
        pred_b.context_snapshot["feature_vector"] = self._make_feature_vec(0.1)  # very different

        bars = _make_bars()
        oa = _outcome_after_score(pred_a, bars)
        ob = _outcome_after_score(pred_b, bars)
        history = [(pred_a, oa), (pred_b, ob)]

        current_vec = self._make_feature_vec(0.6)
        results = find_similar_contexts(current_vec, history, n=2, min_similarity=0.0)
        assert len(results) <= 2
        if len(results) >= 2:
            assert results[0].similarity_score >= results[1].similarity_score

    def test_find_similar_contexts_respects_min_similarity(self):
        pred = _prediction()
        pred.context_snapshot["feature_vector"] = [0.0] * 8  # orthogonal to everything
        bars = _make_bars()
        outcome = _outcome_after_score(pred, bars)
        history = [(pred, outcome)]

        current_vec = [1.0] * 8
        results = find_similar_contexts(current_vec, history, n=5, min_similarity=0.99)
        assert len(results) == 0  # orthogonal vectors shouldn't match at 0.99

    def test_find_similar_contexts_empty_history(self):
        vec = self._make_feature_vec()
        results = find_similar_contexts(vec, [], n=5)
        assert results == []

    def test_compute_agent_stats_returns_default_below_min_samples(self):
        stats = compute_agent_stats([], "agent_a", window_days=60)
        assert stats.sample_count == 0
        assert stats.trend_accuracy == 0.0

    def test_compute_agent_stats_accuracy_correct(self):
        pairs = []
        for i in range(10):
            pred = _prediction(
                trend_dir="bullish",
                trend_conf=0.8,
                agent_id="agent_a",
                target_date=f"2026-03-{10+i:02d}",
            )
            bars = _make_bars(trend=0.1)
            outcome = _outcome_after_score(pred, bars)
            # Force correct direction on first 7 predictions
            if i < 7:
                outcome.actual_direction = "bullish"
            else:
                outcome.actual_direction = "bearish"
            pairs.append((pred, outcome))

        stats = compute_agent_stats(pairs, "agent_a")
        assert abs(stats.trend_accuracy - 0.7) < 0.01

    def test_compute_agent_stats_calibration_buckets(self):
        pairs = []
        for i in range(20):
            conf = 0.8 if i < 10 else 0.3
            pred = _prediction(trend_dir="bullish", trend_conf=conf, agent_id="a")
            bars = _make_bars()
            outcome = _outcome_after_score(pred, bars)
            outcome.actual_direction = "bullish" if i < 10 else "bearish"
            pairs.append((pred, outcome))

        stats = compute_agent_stats(pairs, "a")
        assert len(stats.trend_calibration_buckets) > 0
        for bucket in stats.trend_calibration_buckets:
            assert 0.0 <= bucket["fraction_correct"] <= 1.0

    def test_drift_warning_when_recent_worse_than_window(self):
        pairs = []
        # 30 good predictions
        for i in range(30):
            pred = _prediction(target_date=f"2026-01-{i+1:02d}", agent_id="a")
            bars = _make_bars()
            outcome = _outcome_after_score(pred, bars)
            outcome.actual_direction = pred.trend_direction  # all correct
            outcome.composite_score = 0.9
            pairs.append((pred, outcome))
        # 10 terrible recent predictions
        for i in range(10):
            pred = _prediction(target_date=f"2026-02-{i+1:02d}", agent_id="a")
            bars = _make_bars()
            outcome = _outcome_after_score(pred, bars)
            outcome.actual_direction = "bearish" if pred.trend_direction == "bullish" else "bullish"
            outcome.composite_score = 0.1
            pairs.append((pred, outcome))

        stats = compute_agent_stats(pairs, "a")
        assert stats.drift_warning is True


# ===========================================================================
# STORE
# ===========================================================================


class TestPredictionStore:
    def test_save_and_load_prediction(self, tmp_path):
        store = PredictionStore(str(tmp_path), "NQ", "H25")
        pred = _prediction()
        store.save_prediction(pred)

        loaded = store.get_prediction(pred.prediction_id)
        assert loaded is not None
        assert loaded.prediction_id == pred.prediction_id
        assert loaded.trend_direction == pred.trend_direction

    def test_save_prediction_idempotent(self, tmp_path):
        store = PredictionStore(str(tmp_path), "NQ", "H25")
        pred = _prediction()
        store.save_prediction(pred)
        store.save_prediction(pred)  # must not duplicate
        all_preds = store.get_all_predictions()
        assert len(all_preds) == 1

    def test_save_and_load_outcome(self, tmp_path):
        store = PredictionStore(str(tmp_path), "NQ", "H25")
        pred = _prediction()
        bars = _make_bars()
        outcome = _outcome_after_score(pred, bars)
        store.save_prediction(pred)
        store.save_outcome(outcome)

        loaded = store.get_outcome(pred.prediction_id)
        assert loaded is not None
        assert abs(loaded.composite_score - outcome.composite_score) < 1e-6

    def test_duplicate_outcome_raises(self, tmp_path):
        store = PredictionStore(str(tmp_path), "NQ", "H25")
        pred = _prediction()
        bars = _make_bars()
        outcome = _outcome_after_score(pred, bars)
        store.save_prediction(pred)
        store.save_outcome(outcome)
        with pytest.raises(DuplicateOutcomeError):
            store.save_outcome(outcome)

    def test_get_predictions_for_date(self, tmp_path):
        store = PredictionStore(str(tmp_path), "NQ", "H25")
        p1 = _prediction(target_date="2026-04-21", agent_id="agent_a")
        p2 = _prediction(target_date="2026-04-21", agent_id="agent_b")
        p3 = _prediction(target_date="2026-04-22", agent_id="agent_a")
        for p in (p1, p2, p3):
            store.save_prediction(p)
        results = store.get_predictions_for_date("2026-04-21")
        assert len(results) == 2

    def test_get_recent_history_returns_scored_pairs(self, tmp_path):
        store = PredictionStore(str(tmp_path), "NQ", "H25")
        for i in range(5):
            pred = _prediction(target_date=f"2026-04-{15+i:02d}", agent_id="a")
            bars = _make_bars()
            outcome = _outcome_after_score(pred, bars)
            store.save_prediction(pred)
            store.save_outcome(outcome)

        history = store.get_recent_history(10, require_scored=True)
        assert len(history) == 5
        assert all(o.composite_score > 0 for _, o in history)

    def test_get_recent_history_excludes_unscored(self, tmp_path):
        store = PredictionStore(str(tmp_path), "NQ", "H25")
        pred = _prediction()
        outcome = PredictionOutcome(
            prediction_id=pred.prediction_id,
            target_date=pred.target_date,
            instrument=pred.instrument,
            composite_score=0.0,  # unscored
        )
        store.save_prediction(pred)
        store.save_outcome(outcome)
        history = store.get_recent_history(10, require_scored=True)
        assert len(history) == 0

    def test_store_persists_across_instances(self, tmp_path):
        store1 = PredictionStore(str(tmp_path), "NQ", "H25")
        pred = _prediction()
        store1.save_prediction(pred)

        store2 = PredictionStore(str(tmp_path), "NQ", "H25")
        loaded = store2.get_prediction(pred.prediction_id)
        assert loaded is not None
        assert loaded.prediction_id == pred.prediction_id


# ===========================================================================
# ORCHESTRATOR
# ===========================================================================


class TestOrchestrator:
    def test_validate_valid_response(self):
        _validate_agent_response({
            "trend_direction": "bullish",
            "trend_confidence": 0.8,
            "is_trending": True,
            "chop_confidence": 0.7,
            "predicted_high": 19200.0,
            "predicted_low": 18900.0,
            "breakout_probability": 0.3,
            "breakout_direction": "up",
            "event_risk_score": 0.2,
            "key_levels": [],
            "reasoning": "test",
        })

    def test_validate_missing_field_raises(self):
        with pytest.raises(PredictionValidationError, match="missing field"):
            _validate_agent_response({
                "trend_direction": "bullish",
                # missing most fields
            })

    def test_validate_high_low_inverted_raises(self):
        with pytest.raises(PredictionValidationError, match="predicted_high"):
            _validate_agent_response({
                "trend_direction": "bullish",
                "trend_confidence": 0.8,
                "is_trending": True,
                "chop_confidence": 0.7,
                "predicted_high": 18000.0,  # LOWER than predicted_low
                "predicted_low": 19000.0,
                "breakout_probability": 0.3,
                "breakout_direction": "none",
                "event_risk_score": 0.2,
                "key_levels": [],
                "reasoning": "bad",
            })

    def test_validate_invalid_direction_raises(self):
        with pytest.raises(PredictionValidationError):
            _validate_agent_response({
                "trend_direction": "VERY_BULLISH",  # invalid
                "trend_confidence": 0.8,
                "is_trending": True,
                "chop_confidence": 0.7,
                "predicted_high": 19200.0,
                "predicted_low": 18900.0,
                "breakout_probability": 0.3,
                "breakout_direction": "none",
                "event_risk_score": 0.2,
                "key_levels": [],
                "reasoning": "bad",
            })

    def test_measure_and_learn_empty_bars_raises(self, tmp_path):
        store = PredictionStore(str(tmp_path), "NQ", "H25")
        empty = pd.DataFrame(columns=["dt", "open", "high", "low", "close", "volume"])
        with pytest.raises(InsufficientBarsError):
            measure_and_learn(empty, store, "NQ", "H25", "2026-04-21", 20.0)

    def test_measure_and_learn_full_cycle(self, tmp_path):
        store = PredictionStore(str(tmp_path), "NQ", "H25")
        pred = _prediction(target_date="2026-04-21")
        store.save_prediction(pred)

        bars = _make_bars()
        outcomes = measure_and_learn(bars, store, "NQ", "H25", "2026-04-21", prior_atr=20.0)
        assert len(outcomes) == 1
        assert outcomes[0].prediction_id == pred.prediction_id
        assert outcomes[0].composite_score > 0.0

        # Running again should not duplicate
        outcomes2 = measure_and_learn(bars, store, "NQ", "H25", "2026-04-21", prior_atr=20.0)
        assert len(outcomes2) == 1

    def test_statistical_stub_agent_valid_output(self):
        context = {
            "instrument": "NQ",
            "similar_contexts": [
                {
                    "similarity_score": 0.85,
                    "prediction_summary": {"trend_direction": "bullish", "is_trending": True, "breakout_probability": 0.3},
                    "outcome": {
                        "actual_direction": "bullish",
                        "actual_is_trending": True,
                        "breakout_occurred": False,
                        "breakout_direction_actual": "none",
                        "composite_score": 0.75,
                    },
                }
            ],
            "multitf_features": {"feature_values": {"tf15m_atr": 15.0}},
            "daily_levels": {
                "prior_day": {"close": 19050.0, "high": 19100.0, "low": 18950.0},
                "pivots": {"pp": 19033.0, "r1": 19116.0, "s1": 18966.0},
                "camarilla": {"r4": None, "s4": None, "r3": None, "s3": None},
            },
            "event_proximity": {"event_risk_score": 0.1},
            "feature_vector": [0.5] * 8,
        }
        response = statistical_stub_agent(context)
        _validate_agent_response(response)  # must pass schema validation

    def test_statistical_stub_agent_empty_context(self):
        response = statistical_stub_agent({
            "similar_contexts": [],
            "multitf_features": {"feature_values": {}},
            "daily_levels": {},
            "event_proximity": {},
        })
        _validate_agent_response(response)
