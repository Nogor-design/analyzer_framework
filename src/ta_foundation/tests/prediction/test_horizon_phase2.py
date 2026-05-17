"""
Phase 2 unit tests for the horizon prediction system.

Covers:
  - HorizonPredictionStore   (round-trip, idempotent saves, filtered queries)
  - AnalogueProbabilityAgent (distance weighting, leakage check, abstain)
  - horizon_calibrator       (bucket grouping, top-label ECE, scorer integration)
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from ta_foundation.prediction.analogue_probability_agent import (
    AnalogueProbabilityAgent,
    AnalogueProbabilityAgentConfig,
)
from ta_foundation.prediction.horizon_calibrator import (
    HorizonBucketKey,
    compute_all_bucket_stats,
    compute_horizon_bucket_stats,
    compute_per_bucket_ece,
    group_by_bucket,
    lookup_calibration_error,
)
from ta_foundation.prediction.horizon_models import (
    CandleHorizonOutcome,
    CandleHorizonPrediction,
)
from ta_foundation.prediction.horizon_outcome_measurer import measure_horizon_outcome
from ta_foundation.prediction.horizon_scorer import score_horizon_prediction
from ta_foundation.prediction.horizon_store import (
    DuplicateHorizonOutcomeError,
    HorizonPredictionStore,
)
from ta_foundation.prediction.statistical_probability_agent import (
    StatisticalProbabilityAgent,
)

DENVER = "America/Denver"
NY = "America/New_York"


# ---------------------------------------------------------------------------
# Fixtures / helpers
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


def _pred(
    *,
    agent_id: str = "stat",
    timeframe: str = "5m",
    horizon: int = 3,
    session: str = "ny_open",
    regime: str = "trend_up",
    p_bull: float = 0.6,
    p_bear: float = 0.2,
    p_neu: float = 0.2,
    asof_iso: str = "2026-04-21T10:00:00-06:00",
    abstain: bool = False,
) -> CandleHorizonPrediction:
    return CandleHorizonPrediction(
        agent_id=agent_id,
        instrument="NQ",
        contract="H25",
        timeframe=timeframe,
        asof_timestamp=asof_iso,
        session_label=session,
        horizon_candles=horizon,
        bullish_probability=p_bull,
        bearish_probability=p_bear,
        neutral_probability=p_neu,
        feature_snapshot={"regime": regime},
        sample_size=50,
        method_used="test_method",
        abstain=abstain,
    )


def _outcome(
    *,
    pred_id: str,
    actual_direction: str,
    composite_score: float = 0.0,
    brier_dir: float = 0.0,
    brier_th: float = 0.0,
    timeframe: str = "5m",
    horizon: int = 3,
) -> CandleHorizonOutcome:
    return CandleHorizonOutcome(
        prediction_id=pred_id,
        timeframe=timeframe,
        horizon_candles=horizon,
        actual_direction=actual_direction,
        composite_score=composite_score,
        brier_score_direction=brier_dir,
        brier_score_thresholds=brier_th,
    )


# ---------------------------------------------------------------------------
# HorizonPredictionStore
# ---------------------------------------------------------------------------

class TestHorizonPredictionStore:
    def test_round_trip(self, tmp_path: Path):
        store = HorizonPredictionStore(tmp_path, "NQ", "H25")
        p = _pred()
        store.save_prediction(p)
        loaded = store.get_prediction(p.prediction_id)
        assert loaded is not None
        assert loaded.prediction_id == p.prediction_id
        assert loaded.bullish_probability == pytest.approx(0.6)

    def test_save_prediction_is_idempotent(self, tmp_path: Path):
        store = HorizonPredictionStore(tmp_path, "NQ", "H25")
        p = _pred()
        store.save_prediction(p)
        store.save_prediction(p)
        store.save_prediction(p)
        all_preds = store.get_all_predictions()
        assert len(all_preds) == 1

    def test_save_outcome_rejects_duplicates(self, tmp_path: Path):
        store = HorizonPredictionStore(tmp_path, "NQ", "H25")
        p = _pred()
        store.save_prediction(p)
        o = _outcome(pred_id=p.prediction_id, actual_direction="bullish")
        store.save_outcome(o)
        with pytest.raises(DuplicateHorizonOutcomeError):
            store.save_outcome(o)

    def test_save_outcome_requires_prediction_id(self, tmp_path: Path):
        store = HorizonPredictionStore(tmp_path, "NQ", "H25")
        o = CandleHorizonOutcome()  # blank prediction_id
        with pytest.raises(ValueError, match="prediction_id"):
            store.save_outcome(o)

    def test_filter_queries(self, tmp_path: Path):
        store = HorizonPredictionStore(tmp_path, "NQ", "H25")
        store.save_prediction(_pred(timeframe="5m", horizon=3, session="ny_open"))
        store.save_prediction(_pred(timeframe="5m", horizon=3, session="london"))
        store.save_prediction(_pred(timeframe="15m", horizon=3, session="ny_open"))
        store.save_prediction(_pred(timeframe="5m", horizon=5, session="ny_open"))

        assert len(store.get_predictions_for(timeframe="5m")) == 3
        assert len(store.get_predictions_for(timeframe="5m", horizon_candles=3)) == 2
        assert len(store.get_predictions_for(session_label="london")) == 1
        assert len(store.get_predictions_for(timeframe="5m", horizon_candles=3,
                                             session_label="ny_open")) == 1

    def test_get_pairs_filters_abstain_and_dates(self, tmp_path: Path):
        store = HorizonPredictionStore(tmp_path, "NQ", "H25")
        # one good pair, one abstaining, one outside the date window
        good = _pred(asof_iso="2026-04-21T10:00:00-06:00")
        abstain = _pred(asof_iso="2026-04-22T10:00:00-06:00", abstain=True)
        early = _pred(asof_iso="2026-01-01T10:00:00-06:00")
        for p in (good, abstain, early):
            store.save_prediction(p)
            store.save_outcome(_outcome(pred_id=p.prediction_id, actual_direction="bullish"))

        pairs = store.get_pairs(
            asof_after=pd.Timestamp("2026-04-01", tz=DENVER),
            asof_before=pd.Timestamp("2026-04-30", tz=DENVER),
        )
        assert len(pairs) == 1
        assert pairs[0][0].prediction_id == good.prediction_id

    def test_persistence_across_instances(self, tmp_path: Path):
        s1 = HorizonPredictionStore(tmp_path, "NQ", "H25")
        p = _pred()
        s1.save_prediction(p)
        s1.save_outcome(_outcome(pred_id=p.prediction_id, actual_direction="bullish"))

        # Fresh instance reads the same files
        s2 = HorizonPredictionStore(tmp_path, "NQ", "H25")
        assert len(s2.get_all_predictions()) == 1
        assert s2.get_outcome(p.prediction_id) is not None

    def test_jsonl_files_are_well_formed(self, tmp_path: Path):
        store = HorizonPredictionStore(tmp_path, "NQ", "H25")
        store.save_prediction(_pred())
        # Each non-empty line is valid JSON
        text = (tmp_path / "NQ_H25" / "horizon_predictions.jsonl").read_text(encoding="utf-8")
        lines = [ln for ln in text.splitlines() if ln.strip()]
        assert len(lines) == 1
        parsed = json.loads(lines[0])
        assert parsed["timeframe"] == "5m"


# ---------------------------------------------------------------------------
# AnalogueProbabilityAgent
# ---------------------------------------------------------------------------

class TestAnalogueProbabilityAgent:
    def test_predicts_with_enough_history(self):
        bars = _make_bars(2000, seed=11)
        agent = AnalogueProbabilityAgent(
            config=AnalogueProbabilityAgentConfig(
                k=30, bandwidth=1.0,
                min_k_local=8, min_k_global=20,
                history_lookback_bars=1500,
            ),
        )
        pred = agent.predict(
            bars=bars, asof_idx=1900, horizon_candles=3,
            instrument="NQ", contract="H25", timeframe="5m",
        )
        assert pred.abstain is False
        assert pred.method_used == "weighted_knn_v1"
        assert pred.fallback_level in (0, 1, 2)
        total = pred.bullish_probability + pred.bearish_probability + pred.neutral_probability
        assert total == pytest.approx(1.0, abs=1e-6)
        assert pred.upside_threshold_probability + pred.downside_threshold_probability <= 1.0 + 1e-6
        assert "feature_vec" in pred.feature_snapshot
        assert len(pred.feature_snapshot["feature_vec"]) == 4

    def test_abstains_on_tiny_history(self):
        bars = _make_bars(80, seed=3)
        agent = AnalogueProbabilityAgent(
            config=AnalogueProbabilityAgentConfig(
                k=30,
                min_k_local=8,
                min_k_global=200,   # impossible for 80 bars
            ),
        )
        pred = agent.predict(
            bars=bars, asof_idx=70, horizon_candles=5,
            instrument="NQ", contract="H25", timeframe="5m",
        )
        assert pred.abstain is True
        assert pred.abstain_reason == "insufficient_samples"

    def test_walk_forward_no_leakage(self):
        # Identical asof on a series with vs without future bars must yield
        # an identical prediction.
        bars_full = _make_bars(2000, seed=21)
        asof = 1500
        horizon = 3
        bars_truncated = bars_full.iloc[: asof + horizon + 1].reset_index(drop=True)

        agent = AnalogueProbabilityAgent()

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
        assert pred_full.expected_return_points == pytest.approx(pred_trunc.expected_return_points)

    def test_distance_weighting_concentrates_probability(self):
        # Build bars with two regimes: first half drifts up, second half drifts
        # down. Predict at the end → analogues from the recent (down) half
        # should dominate, biasing toward bearish.
        rng = np.random.default_rng(33)
        n = 1500
        times = pd.date_range("2026-01-02 09:30", periods=n, freq="5min", tz=NY).tz_convert(DENVER)
        sigma = 1.0
        increments = np.empty(n)
        increments[: n // 2] = rng.normal(0.5, sigma, size=n // 2)        # uptrend
        increments[n // 2 :] = rng.normal(-0.5, sigma, size=n - n // 2)   # downtrend
        closes = np.cumsum(increments) + 20000
        bodies = rng.normal(0, sigma * 0.4, n)
        opens = closes - bodies
        highs = np.maximum(opens, closes) + np.abs(rng.normal(0, sigma * 0.3, n))
        lows = np.minimum(opens, closes) - np.abs(rng.normal(0, sigma * 0.3, n))
        bars = pd.DataFrame({
            "dt": times, "open": opens, "high": highs, "low": lows,
            "close": closes, "volume": rng.integers(100, 1000, n),
        })

        agent = AnalogueProbabilityAgent(
            config=AnalogueProbabilityAgentConfig(
                k=50, bandwidth=0.5,
                require_session_match=False,    # focus the test on regime/feature similarity
                require_regime_match=False,
                min_k_local=5, min_k_global=10,
            ),
        )
        pred = agent.predict(
            bars=bars, asof_idx=n - 5, horizon_candles=3,
            instrument="NQ", contract="H25", timeframe="5m",
        )
        # Bearish probability should beat bullish when the local regime is downtrend
        assert pred.abstain is False
        assert pred.bearish_probability > pred.bullish_probability

    def test_full_lifecycle_end_to_end(self):
        bars = _make_bars(2000, seed=42)
        agent = AnalogueProbabilityAgent()
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
        score_horizon_prediction(pred, out)
        assert np.isfinite(out.composite_score)
        assert 0.0 <= out.composite_score <= 1.0


# ---------------------------------------------------------------------------
# horizon_calibrator
# ---------------------------------------------------------------------------

class TestHorizonCalibrator:
    def test_group_by_bucket(self):
        p1 = _pred(agent_id="stat", session="ny_open", regime="trend_up")
        p2 = _pred(agent_id="stat", session="ny_open", regime="trend_up")
        p3 = _pred(agent_id="stat", session="london", regime="trend_up")
        p4 = _pred(agent_id="knn",  session="ny_open", regime="trend_up")
        pairs = [(p, _outcome(pred_id=p.prediction_id, actual_direction="bullish"))
                 for p in (p1, p2, p3, p4)]
        groups = group_by_bucket(pairs)
        assert len(groups) == 3
        # The two stat/ny_open/trend_up predictions land in the same bucket
        target = HorizonBucketKey(
            agent_id="stat", timeframe="5m", horizon_candles=3,
            session_label="ny_open", regime_label="trend_up",
        )
        assert target in groups
        assert len(groups[target]) == 2

    def test_well_calibrated_stream_has_low_ece(self):
        # 100 predictions at 0.7 confidence; 70% are correct → ECE ≈ 0
        rng = np.random.default_rng(101)
        pairs = []
        for i in range(100):
            p = _pred(p_bull=0.7, p_bear=0.15, p_neu=0.15)
            actual = "bullish" if rng.random() < 0.70 else "bearish"
            pairs.append((p, _outcome(pred_id=p.prediction_id, actual_direction=actual)))

        stats = compute_horizon_bucket_stats(pairs)
        assert stats.sample_count == 100
        assert stats.sample_count_non_abstain == 100
        # Loose bound — 100-sample binomial slop
        assert stats.ece < 0.10
        # ~70% direction accuracy
        assert 0.55 < stats.direction_accuracy < 0.85

    def test_overconfident_stream_has_high_ece(self):
        # 100 predictions at 0.95 confidence; only 30% correct → ECE >> 0
        rng = np.random.default_rng(202)
        pairs = []
        for i in range(100):
            p = _pred(p_bull=0.95, p_bear=0.025, p_neu=0.025)
            actual = "bullish" if rng.random() < 0.30 else "bearish"
            pairs.append((p, _outcome(pred_id=p.prediction_id, actual_direction=actual)))

        stats = compute_horizon_bucket_stats(pairs)
        assert stats.ece > 0.50

    def test_abstain_predictions_excluded_from_ece(self):
        # Half of the predictions abstain — they should not affect ECE
        rng = np.random.default_rng(303)
        pairs = []
        for i in range(50):
            p = _pred(p_bull=0.7, p_bear=0.15, p_neu=0.15)
            actual = "bullish" if rng.random() < 0.70 else "bearish"
            pairs.append((p, _outcome(pred_id=p.prediction_id, actual_direction=actual)))
        for i in range(50):
            p = _pred(abstain=True)
            pairs.append((p, _outcome(pred_id=p.prediction_id, actual_direction="bullish")))

        stats = compute_horizon_bucket_stats(pairs)
        assert stats.sample_count == 100
        assert stats.sample_count_non_abstain == 50

    def test_compute_per_bucket_ece_dispatches_correctly(self):
        rng = np.random.default_rng(404)
        pairs = []
        # Bucket A: well calibrated
        for _ in range(80):
            p = _pred(agent_id="A", p_bull=0.7, p_bear=0.15, p_neu=0.15)
            actual = "bullish" if rng.random() < 0.70 else "bearish"
            pairs.append((p, _outcome(pred_id=p.prediction_id, actual_direction=actual)))
        # Bucket B: overconfident
        for _ in range(80):
            p = _pred(agent_id="B", p_bull=0.95, p_bear=0.025, p_neu=0.025)
            actual = "bullish" if rng.random() < 0.30 else "bearish"
            pairs.append((p, _outcome(pred_id=p.prediction_id, actual_direction=actual)))

        table = compute_per_bucket_ece(pairs)
        ece_a = next(v for k, v in table.items() if k.agent_id == "A")
        ece_b = next(v for k, v in table.items() if k.agent_id == "B")
        assert ece_a < ece_b

    def test_lookup_calibration_error_falls_back(self):
        table = {
            HorizonBucketKey("A", "5m", 3, "ny_open", "trend_up"): 0.12,
        }
        p = _pred(agent_id="A", session="ny_open", regime="trend_up")
        assert lookup_calibration_error(table, p) == pytest.approx(0.12)

        # Missing bucket returns fallback
        p_other = _pred(agent_id="A", session="london", regime="trend_up")
        assert lookup_calibration_error(table, p_other, fallback=0.5) == pytest.approx(0.5)

    def test_compute_all_bucket_stats_min_samples_filter(self):
        rng = np.random.default_rng(505)
        pairs = []
        # Bucket with 50 samples
        for _ in range(50):
            p = _pred(agent_id="A", session="ny_open")
            actual = "bullish" if rng.random() < 0.5 else "bearish"
            pairs.append((p, _outcome(pred_id=p.prediction_id, actual_direction=actual)))
        # Tiny bucket with 3 samples
        for _ in range(3):
            p = _pred(agent_id="A", session="london")
            pairs.append((p, _outcome(pred_id=p.prediction_id, actual_direction="bullish")))

        all_stats = compute_all_bucket_stats(pairs, min_samples=10)
        assert len(all_stats) == 1
        assert all_stats[0].sample_count_non_abstain == 50
        assert all_stats[0].bucket.session_label == "ny_open"

    def test_calibration_error_feeds_scorer(self):
        # Smoke test: a real scoring pass that uses a non-zero calibration_error
        pred = _pred(p_bull=0.7, p_bear=0.15, p_neu=0.15)
        out = CandleHorizonOutcome(
            prediction_id=pred.prediction_id,
            actual_direction="bullish",
            prior_atr=2.0,
        )
        score_horizon_prediction(pred, out, calibration_error=0.4)
        assert out.calibration_error == pytest.approx(0.4)
        # composite reflects the 0.6 (1-0.4) calibration component at default weight 0.10
        # not testing exact value — just that it's finite and below 1.0
        assert 0.0 <= out.composite_score <= 1.0
