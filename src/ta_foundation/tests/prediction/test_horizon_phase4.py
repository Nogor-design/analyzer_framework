"""
Phase 4 unit tests for the horizon prediction system.

Covers:
  - Regime-only fallback in AnalogueProbabilityAgent._filter_with_fallback
  - Specialist factories (regime, session)
  - StackingWeightTable lookup behavior
  - compute_stacking_weights end-to-end
  - EnsembleHorizonAgent combination, abstain, error isolation
"""
from __future__ import annotations

from typing import List, Tuple

import numpy as np
import pandas as pd
import pytest

from ta_foundation.prediction.analogue_probability_agent import (
    AnalogueProbabilityAgent,
    AnalogueProbabilityAgentConfig,
)
from ta_foundation.prediction.horizon_ensemble import (
    EnsembleHorizonAgent,
    StackingKey,
    StackingWeightTable,
    compute_stacking_weights,
)
from ta_foundation.prediction.horizon_models import (
    CandleHorizonOutcome,
    CandleHorizonPrediction,
)
from ta_foundation.prediction.horizon_specialists import (
    REGIME_SPECIALIST_AGENT_ID,
    SESSION_SPECIALIST_AGENT_ID,
    make_regime_specialist_agent,
    make_session_specialist_agent,
)
from ta_foundation.prediction.statistical_probability_agent import (
    StatisticalProbabilityAgent,
    StatisticalProbabilityAgentConfig,
)

DENVER = "America/Denver"
NY = "America/New_York"


# ---------------------------------------------------------------------------
# Helpers
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
    composite: float = 0.0,
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
        feature_snapshot={"regime": regime, "prior_atr": 5.0},
        sample_size=50,
        method_used="test_method",
        upside_threshold_points=5.0,
        downside_threshold_points=5.0,
        abstain=abstain,
    )


def _outcome(
    *,
    pred_id: str,
    composite_score: float = 0.0,
    actual_direction: str = "bullish",
    actual_return_atr: float = 0.0,
) -> CandleHorizonOutcome:
    return CandleHorizonOutcome(
        prediction_id=pred_id,
        actual_direction=actual_direction,
        actual_return_atr=actual_return_atr,
        composite_score=composite_score,
    )


# ---------------------------------------------------------------------------
# Regime-only fallback regression
# ---------------------------------------------------------------------------

class TestRegimeOnlyFallback:
    def test_regime_specialist_finds_regime_neighbors(self):
        # Build a long synthetic series with a clear regime split. The
        # regime specialist should be able to produce a non-abstain
        # prediction even when session match is disabled.
        bars = _make_bars(2000, seed=11)
        agent = make_regime_specialist_agent(
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
        # require_regime_match=True, require_session_match=False:
        # so L0 (full = session+regime) is not attempted, L1 takes regime-only
        assert pred.fallback_level in (1, 2)
        assert pred.agent_id == REGIME_SPECIALIST_AGENT_ID

    def test_session_specialist_uses_session_filter(self):
        bars = _make_bars(2000, seed=13)
        agent = make_session_specialist_agent()
        pred = agent.predict(
            bars=bars, asof_idx=1900, horizon_candles=3,
            instrument="NQ", contract="H25", timeframe="5m",
        )
        assert pred.agent_id == SESSION_SPECIALIST_AGENT_ID
        if not pred.abstain:
            # Either session-only filter (1) or unfiltered (2)
            assert pred.fallback_level in (1, 2)

    def test_default_analogue_unaffected_by_phase4(self):
        """
        Sanity: the default analogue agent (both flags True) should still
        report fallback_level ∈ {0, 1, 2}, matching Phase 2 contract.
        """
        bars = _make_bars(2000, seed=21)
        agent = AnalogueProbabilityAgent()
        pred = agent.predict(
            bars=bars, asof_idx=1900, horizon_candles=3,
            instrument="NQ", contract="H25", timeframe="5m",
        )
        assert pred.fallback_level in (0, 1, 2)


# ---------------------------------------------------------------------------
# StackingWeightTable.lookup
# ---------------------------------------------------------------------------

class TestStackingWeightTable:
    def test_uniform_when_table_empty(self):
        table = StackingWeightTable()
        weights = table.lookup(
            StackingKey("5m", 3, "ny_open", "trend_up"),
            ["a", "b", "c"],
        )
        assert weights == {"a": pytest.approx(1 / 3),
                           "b": pytest.approx(1 / 3),
                           "c": pytest.approx(1 / 3)}

    def test_exact_bucket_match_returns_table_weights(self):
        key = StackingKey("5m", 3, "ny_open", "trend_up")
        table = StackingWeightTable(weights={key: {"a": 0.7, "b": 0.3}})
        out = table.lookup(key, ["a", "b"])
        assert out["a"] == pytest.approx(0.7)
        assert out["b"] == pytest.approx(0.3)

    def test_falls_back_to_pooled_when_bucket_missing(self):
        table = StackingWeightTable(
            weights={StackingKey("5m", 3, "ny_open", "trend_up"): {"a": 0.9, "b": 0.1}},
            fallback_weights={"a": 0.5, "b": 0.5},
        )
        out = table.lookup(
            StackingKey("5m", 3, "london", "trend_down"), ["a", "b"]
        )
        assert out["a"] == pytest.approx(0.5)
        assert out["b"] == pytest.approx(0.5)

    def test_uniform_when_lookup_agent_has_no_history(self):
        # Bucket exists but agent c is unknown — should not silently zero c
        key = StackingKey("5m", 3, "ny_open", "trend_up")
        table = StackingWeightTable(weights={key: {"a": 0.6, "b": 0.4}})
        out = table.lookup(key, ["c"])
        # Sole agent gets full weight
        assert out["c"] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# compute_stacking_weights
# ---------------------------------------------------------------------------

class TestComputeStackingWeights:
    def _make_pairs(
        self,
        agent_id: str,
        scores: List[float],
        *,
        session: str = "ny_open",
        regime: str = "trend_up",
        timeframe: str = "5m",
        horizon: int = 3,
    ) -> List[Tuple[CandleHorizonPrediction, CandleHorizonOutcome]]:
        pairs = []
        for i, s in enumerate(scores):
            p = _pred(
                agent_id=agent_id,
                session=session, regime=regime,
                timeframe=timeframe, horizon=horizon,
                asof_iso=f"2026-04-{(i % 28) + 1:02d}T10:00:00-06:00",
            )
            o = _outcome(pred_id=p.prediction_id, composite_score=s)
            pairs.append((p, o))
        return pairs

    def test_better_agent_gets_higher_weight(self):
        pairs = (
            self._make_pairs("a", [0.7] * 30)
            + self._make_pairs("b", [0.3] * 30)
        )
        table = compute_stacking_weights(pairs, min_samples_per_agent=10, floor_weight=0.0)
        key = StackingKey("5m", 3, "ny_open", "trend_up")
        w = table.weights[key]
        assert w["a"] > w["b"]
        assert sum(w.values()) == pytest.approx(1.0)

    def test_floor_weight_keeps_bad_agent_in_mix(self):
        # Agent b never scores anything, agent a is good — without a floor,
        # b's weight would be 0; with a floor, it stays at >= floor.
        pairs = (
            self._make_pairs("a", [0.8] * 40)
            + self._make_pairs("b", [0.0] * 40)
        )
        table = compute_stacking_weights(pairs, floor_weight=0.10)
        key = StackingKey("5m", 3, "ny_open", "trend_up")
        w = table.weights[key]
        assert w["b"] >= 0.10 - 1e-9
        assert sum(w.values()) == pytest.approx(1.0)

    def test_min_samples_skips_bucket_when_no_eligible_agent(self):
        # Both agents have only 5 samples in this bucket → bucket is empty
        pairs = (
            self._make_pairs("a", [0.7] * 5)
            + self._make_pairs("b", [0.3] * 5)
        )
        table = compute_stacking_weights(pairs, min_samples_per_agent=10)
        key = StackingKey("5m", 3, "ny_open", "trend_up")
        assert key not in table.weights

    def test_pooled_fallback_built_from_all_buckets(self):
        # Two buckets, each with enough samples in one agent only.
        pairs = (
            self._make_pairs("a", [0.7] * 30, session="ny_open")
            + self._make_pairs("b", [0.3] * 30, session="london")
        )
        table = compute_stacking_weights(pairs, min_samples_per_agent=10, floor_weight=0.0)
        # Pooled fallback knows both agents
        assert set(table.fallback_weights.keys()) == {"a", "b"}
        assert sum(table.fallback_weights.values()) == pytest.approx(1.0)

    def test_abstain_predictions_excluded(self):
        good = self._make_pairs("a", [0.7] * 20)
        # abstaining predictions for "b" should not enter the table
        bad = []
        for i in range(20):
            p = _pred(agent_id="b", abstain=True,
                     asof_iso=f"2026-04-{(i % 28) + 1:02d}T10:00:00-06:00")
            o = _outcome(pred_id=p.prediction_id, composite_score=0.9)
            bad.append((p, o))

        table = compute_stacking_weights(good + bad, min_samples_per_agent=10)
        key = StackingKey("5m", 3, "ny_open", "trend_up")
        w = table.weights[key]
        assert "b" not in w
        assert "a" in w


# ---------------------------------------------------------------------------
# EnsembleHorizonAgent
# ---------------------------------------------------------------------------

class _StubAgent:
    """Returns a pre-baked prediction; optionally raises or abstains."""

    def __init__(
        self,
        agent_id: str,
        prediction: CandleHorizonPrediction | None = None,
        raise_exc: bool = False,
    ) -> None:
        self.agent_id = agent_id
        self._prediction = prediction
        self._raise_exc = raise_exc

    def predict(self, bars, asof_idx, horizon_candles, instrument, contract, timeframe):
        if self._raise_exc:
            raise RuntimeError("boom")
        if self._prediction is not None:
            # Ensure agent_id and bookkeeping fields are aligned
            p = self._prediction
            p.agent_id = self.agent_id
            return p
        return _pred(
            agent_id=self.agent_id,
            timeframe=timeframe,
            horizon=horizon_candles,
            session="ny_open",
            regime="trend_up",
        )


class TestEnsembleHorizonAgent:
    def test_requires_at_least_one_member(self):
        with pytest.raises(ValueError):
            EnsembleHorizonAgent(members=[])

    def test_rejects_duplicate_agent_ids(self):
        with pytest.raises(ValueError, match="Duplicate"):
            EnsembleHorizonAgent(members=[_StubAgent("a"), _StubAgent("a")])

    def test_uniform_weights_when_table_empty(self):
        # Two members, p_bull = 0.8 and 0.2 → uniform avg should be 0.5
        a = _StubAgent("a", _pred(agent_id="a", p_bull=0.8, p_bear=0.1, p_neu=0.1))
        b = _StubAgent("b", _pred(agent_id="b", p_bull=0.2, p_bear=0.6, p_neu=0.2))
        ens = EnsembleHorizonAgent(members=[a, b])

        bars = _make_bars(50)
        pred = ens.predict(bars, asof_idx=10, horizon_candles=3,
                           instrument="NQ", contract="H25", timeframe="5m")
        assert pred.abstain is False
        assert pred.bullish_probability == pytest.approx(0.5, abs=1e-6)
        assert pred.bullish_probability + pred.bearish_probability + pred.neutral_probability \
            == pytest.approx(1.0)
        assert pred.method_used == "ensemble_v1"
        assert pred.feature_snapshot["ensemble_members"] == ["a", "b"]
        assert pred.feature_snapshot["ensemble_weights"] == {"a": 0.5, "b": 0.5}

    def test_weighted_average_uses_table(self):
        a = _StubAgent("a", _pred(agent_id="a", p_bull=0.9, p_bear=0.05, p_neu=0.05))
        b = _StubAgent("b", _pred(agent_id="b", p_bull=0.1, p_bear=0.45, p_neu=0.45))
        # Weight 0.8 / 0.2 → expected p_bull ≈ 0.74
        key = StackingKey("5m", 3, "ny_open", "trend_up")
        table = StackingWeightTable(weights={key: {"a": 0.8, "b": 0.2}})
        ens = EnsembleHorizonAgent(members=[a, b], weight_table=table)
        bars = _make_bars(50)
        pred = ens.predict(bars, 10, 3, "NQ", "H25", "5m")
        assert pred.bullish_probability == pytest.approx(0.74, abs=1e-6)

    def test_abstaining_member_excluded(self):
        a = _StubAgent("a", _pred(agent_id="a", p_bull=0.8, p_bear=0.1, p_neu=0.1))
        b = _StubAgent("b", _pred(agent_id="b", abstain=True))
        ens = EnsembleHorizonAgent(members=[a, b])
        bars = _make_bars(50)
        pred = ens.predict(bars, 10, 3, "NQ", "H25", "5m")
        assert pred.abstain is False
        # Output is exactly agent a's distribution since b dropped out
        assert pred.bullish_probability == pytest.approx(0.8)
        assert pred.feature_snapshot["ensemble_n_abstaining"] == 1
        assert pred.feature_snapshot["ensemble_members"] == ["a"]

    def test_all_abstaining_gives_ensemble_abstain(self):
        a = _StubAgent("a", _pred(agent_id="a", abstain=True))
        b = _StubAgent("b", _pred(agent_id="b", abstain=True))
        ens = EnsembleHorizonAgent(members=[a, b])
        bars = _make_bars(50)
        pred = ens.predict(bars, 10, 3, "NQ", "H25", "5m")
        assert pred.abstain is True
        assert pred.abstain_reason == "insufficient_samples"
        assert pred.confidence == 0.0

    def test_member_exception_isolated(self):
        a = _StubAgent("a", _pred(agent_id="a", p_bull=0.7, p_bear=0.2, p_neu=0.1))
        b = _StubAgent("b", raise_exc=True)
        ens = EnsembleHorizonAgent(members=[a, b])
        bars = _make_bars(50)
        pred = ens.predict(bars, 10, 3, "NQ", "H25", "5m")
        # Ensemble survives — only agent a contributed
        assert pred.abstain is False
        assert pred.bullish_probability == pytest.approx(0.7)
        errors = pred.feature_snapshot.get("ensemble_errors") or []
        assert any("b" in e for e in errors)

    def test_threshold_probabilities_are_renormalized(self):
        a_pred = _pred(agent_id="a")
        a_pred.upside_threshold_probability = 0.4
        a_pred.downside_threshold_probability = 0.4
        a_pred.neither_threshold_probability = 0.2
        b_pred = _pred(agent_id="b")
        b_pred.upside_threshold_probability = 0.2
        b_pred.downside_threshold_probability = 0.2
        b_pred.neither_threshold_probability = 0.6

        a = _StubAgent("a", a_pred)
        b = _StubAgent("b", b_pred)
        ens = EnsembleHorizonAgent(members=[a, b])
        bars = _make_bars(50)
        pred = ens.predict(bars, 10, 3, "NQ", "H25", "5m")
        s = (pred.upside_threshold_probability + pred.downside_threshold_probability
             + pred.neither_threshold_probability)
        assert s == pytest.approx(1.0, abs=1e-6)

    def test_full_lifecycle_with_real_agents(self):
        """End-to-end: real agents → ensemble → outcome → score."""
        from ta_foundation.prediction.horizon_outcome_measurer import measure_horizon_outcome
        from ta_foundation.prediction.horizon_scorer import score_horizon_prediction

        bars = _make_bars(2000, seed=42)
        members = [
            StatisticalProbabilityAgent(),
            AnalogueProbabilityAgent(),
            make_regime_specialist_agent(),
            make_session_specialist_agent(),
        ]
        ens = EnsembleHorizonAgent(members=members)
        asof = 1900
        pred = ens.predict(bars, asof, 3, "NQ", "H25", "5m")
        if pred.abstain:
            pytest.skip("Ensemble abstained on this seed; lifecycle test wants a non-abstain")

        # Direction probabilities sum to 1 and are in [0, 1]
        s = pred.bullish_probability + pred.bearish_probability + pred.neutral_probability
        assert s == pytest.approx(1.0, abs=1e-6)

        out = measure_horizon_outcome(
            bars=bars,
            asof_idx=asof,
            horizon_candles=3,
            prior_atr=float(pred.feature_snapshot.get("prior_atr") or 0.0),
            upside_threshold_points=pred.upside_threshold_points,
            downside_threshold_points=pred.downside_threshold_points,
            prediction=pred,
        )
        score_horizon_prediction(pred, out)
        assert 0.0 <= out.composite_score <= 1.0
