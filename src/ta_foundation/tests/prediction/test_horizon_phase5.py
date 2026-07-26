"""
Phase 5 unit tests for the horizon prediction system.

Covers:
  - CostModel             round-trip arithmetic, ATR conversion.
  - evaluate_tradable_zone   verdict shape, rejection reasons, sizing.
  - AbstentionPolicy      rule firing, agent-abstain pass-through.
  - HorizonConfig         YAML loader, end-to-end apply().
  - StackingWeightTable   JSON round-trip persistence (improvement).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ta_foundation.prediction.horizon_abstention import AbstentionPolicy
from ta_foundation.prediction.horizon_config import (
    HorizonConfig,
    HorizonPipelineResult,
    load_horizon_config,
    load_horizon_config_or_default,
)
from ta_foundation.prediction.horizon_costs import CostModel
from ta_foundation.prediction.horizon_ensemble import (
    StackingKey,
    StackingWeightTable,
)
from ta_foundation.prediction.horizon_models import CandleHorizonPrediction
from ta_foundation.prediction.horizon_tradable_zone import (
    TradableZoneConfig,
    evaluate_tradable_zone,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pred(
    *,
    p_bull: float = 0.7,
    p_bear: float = 0.15,
    p_neu: float = 0.15,
    expected_return_points: float = 5.0,
    predicted_volatility: float = 4.0,
    upside_threshold_points: float = 6.0,
    downside_threshold_points: float = 6.0,
    sample_size: int = 50,
    effective_sample_size: float = 50.0,
    fallback_level: int = 0,
    regime: str = "trend_up",
    session: str = "ny_open",
    prior_atr: float = 6.0,
    abstain: bool = False,
) -> CandleHorizonPrediction:
    return CandleHorizonPrediction(
        agent_id="test",
        instrument="NQ",
        contract="H25",
        timeframe="5m",
        asof_timestamp="2026-04-21T10:00:00-06:00",
        session_label=session,
        horizon_candles=3,
        bullish_probability=p_bull,
        bearish_probability=p_bear,
        neutral_probability=p_neu,
        expected_return_points=expected_return_points,
        predicted_volatility=predicted_volatility,
        upside_threshold_points=upside_threshold_points,
        downside_threshold_points=downside_threshold_points,
        sample_size=sample_size,
        effective_sample_size=effective_sample_size,
        fallback_level=fallback_level,
        feature_snapshot={"regime": regime, "prior_atr": prior_atr},
        abstain=abstain,
    )


# ---------------------------------------------------------------------------
# CostModel
# ---------------------------------------------------------------------------

class TestCostModel:
    def test_round_trip_uses_both_components(self):
        cm = CostModel(
            fixed_points_per_side=0.5,
            slippage_atr_per_side=0.05,
            spread_points=0.25,
        )
        # per side = 0.5 + 0.25 + 0.05 * 10 = 1.25; round-trip = 2.5
        assert cm.round_trip_cost_points(prior_atr=10.0) == pytest.approx(2.5)

    def test_negative_atr_clamped_to_zero(self):
        cm = CostModel(fixed_points_per_side=0.5, slippage_atr_per_side=0.05)
        # negative ATR → clamp to 0; round-trip = 2 * 0.5 = 1.0
        assert cm.round_trip_cost_points(prior_atr=-1.0) == pytest.approx(1.0)

    def test_atr_conversion(self):
        cm = CostModel(fixed_points_per_side=1.0)
        # round-trip = 2.0 points; over ATR=4 → 0.5 ATR
        assert cm.round_trip_cost_atr(prior_atr=4.0) == pytest.approx(0.5)

    def test_round_trip_dict(self):
        cm = CostModel(fixed_points_per_side=0.5, slippage_atr_per_side=0.05, spread_points=0.25)
        cm2 = CostModel.from_dict(cm.as_dict())
        assert cm2 == cm


# ---------------------------------------------------------------------------
# TradableZone
# ---------------------------------------------------------------------------

class TestTradableZone:
    def test_high_confidence_positive_edge_is_tradable(self):
        pred = _pred(p_bull=0.7, expected_return_points=5.0, prior_atr=6.0)
        cm = CostModel(fixed_points_per_side=0.25)  # 0.5 round-trip
        verdict = evaluate_tradable_zone(pred, cm)
        assert verdict.is_tradable is True
        assert verdict.recommended_direction == "bullish"
        # net edge = 5.0 - 0.5 = 4.5 points / 6.0 ATR ≈ 0.75
        assert verdict.expected_edge_atr == pytest.approx(0.75)
        assert verdict.cost_round_trip_points == pytest.approx(0.5)
        assert verdict.recommended_size_fraction > 0
        assert verdict.recommended_size_fraction <= 0.25
        assert not verdict.rejection_reasons

    def test_neutral_argmax_rejected_by_default(self):
        pred = _pred(p_bull=0.30, p_bear=0.20, p_neu=0.50)
        verdict = evaluate_tradable_zone(pred)
        assert verdict.is_tradable is False
        assert verdict.recommended_direction == "neutral"
        assert any("neutral" in r for r in verdict.rejection_reasons)

    def test_negative_edge_rejected(self):
        # Tiny expected return; large cost
        pred = _pred(p_bull=0.7, expected_return_points=0.1, prior_atr=6.0)
        cm = CostModel(fixed_points_per_side=2.0)   # 4.0 round-trip
        verdict = evaluate_tradable_zone(pred, cm)
        assert verdict.expected_edge_points < 0
        assert verdict.is_tradable is False
        assert any("edge_atr" in r for r in verdict.rejection_reasons)

    def test_low_confidence_rejected(self):
        pred = _pred(p_bull=0.45, p_bear=0.30, p_neu=0.25)
        verdict = evaluate_tradable_zone(
            pred,
            config=TradableZoneConfig(min_confidence=0.55, min_expected_edge_atr=-1.0,
                                      min_effective_sample_size=0),
        )
        assert any("confidence" in r for r in verdict.rejection_reasons)
        assert verdict.is_tradable is False

    def test_abstain_pred_never_tradable(self):
        pred = _pred(abstain=True)
        verdict = evaluate_tradable_zone(pred)
        assert verdict.is_tradable is False
        assert "prediction_abstained" in verdict.rejection_reasons

    def test_bearish_direction_inverts_stop_target(self):
        # Bearish argmax → target/stop swap relative to upside/downside thresholds
        pred = _pred(
            p_bull=0.10, p_bear=0.80, p_neu=0.10,
            expected_return_points=-5.0,
            upside_threshold_points=8.0,
            downside_threshold_points=6.0,
        )
        verdict = evaluate_tradable_zone(pred, CostModel(fixed_points_per_side=0.25))
        assert verdict.recommended_direction == "bearish"
        # gross edge = -1 * -5.0 = 5.0; cost = 0.5; net = 4.5
        assert verdict.expected_edge_points == pytest.approx(4.5)
        # bearish: target = downside (6.0), stop = upside (8.0) — but vol-floored
        # predicted_volatility=4.0 → vol_floor = 2.0, so floors don't bite.
        assert verdict.recommended_target_points == pytest.approx(6.0)
        assert verdict.recommended_stop_points == pytest.approx(8.0)

    def test_kelly_size_capped(self):
        # Massive expected return / tiny variance should still be capped
        pred = _pred(
            p_bull=0.99, p_bear=0.005, p_neu=0.005,
            expected_return_points=100.0,
            predicted_volatility=1.0,
        )
        verdict = evaluate_tradable_zone(pred, config=TradableZoneConfig(kelly_cap=0.25))
        assert verdict.recommended_size_fraction == pytest.approx(0.25)


# ---------------------------------------------------------------------------
# AbstentionPolicy
# ---------------------------------------------------------------------------

class TestAbstentionPolicy:
    def test_no_op_policy_passes_through(self):
        pred = _pred()
        policy = AbstentionPolicy()
        out = policy.apply(pred)
        assert out is pred  # unchanged reference

    def test_min_sample_size_triggers(self):
        pred = _pred(sample_size=5)
        policy = AbstentionPolicy(min_sample_size=20)
        out = policy.apply(pred)
        assert out is not pred
        assert out.abstain is True
        assert out.abstain_reason == "insufficient_samples"
        # direction probs zeroed
        assert out.bullish_probability == 0.0
        assert out.neither_threshold_probability == 1.0

    def test_max_fallback_level_triggers(self):
        pred = _pred(fallback_level=2)
        policy = AbstentionPolicy(max_fallback_level=1)
        out = policy.apply(pred)
        assert out.abstain is True
        assert out.abstain_reason == "uncalibrated"

    def test_min_confidence_triggers(self):
        pred = _pred(p_bull=0.34, p_bear=0.33, p_neu=0.33)
        policy = AbstentionPolicy(min_confidence=0.5)
        out = policy.apply(pred)
        assert out.abstain is True
        assert out.abstain_reason == "low_confidence"

    def test_regime_blacklist(self):
        pred = _pred(regime="range")
        policy = AbstentionPolicy(regime_blacklist=["range"])
        out = policy.apply(pred)
        assert out.abstain is True
        assert out.abstain_reason == "regime_drift"

    def test_session_blacklist(self):
        pred = _pred(session="overnight")
        policy = AbstentionPolicy(session_blacklist=["overnight"])
        out = policy.apply(pred)
        assert out.abstain is True

    def test_honor_agent_abstain_passes_through_reason(self):
        pred = _pred(abstain=True)
        # set the reason directly so it round-trips
        pred.abstain_reason = "regime_drift"
        policy = AbstentionPolicy(min_sample_size=1, honor_agent_abstain=True)
        out = policy.apply(pred)
        # No new copy needed — already abstaining
        assert out.abstain is True
        assert out.abstain_reason == "regime_drift"

    def test_dict_round_trip(self):
        policy = AbstentionPolicy(
            min_sample_size=20,
            max_fallback_level=1,
            min_confidence=0.5,
            regime_blacklist=["range"],
            session_blacklist=["overnight"],
        )
        out = AbstentionPolicy.from_dict(policy.as_dict())
        assert out == policy


# ---------------------------------------------------------------------------
# HorizonConfig + YAML
# ---------------------------------------------------------------------------

class TestHorizonConfig:
    def test_default_config_apply_passes_through(self):
        pred = _pred()
        cfg = HorizonConfig()
        result = cfg.apply(pred)
        assert isinstance(result, HorizonPipelineResult)
        assert result.policy_changed is False
        assert result.prediction is pred
        # default tradable_zone has min_edge=0; cost model is zero;
        # confidence=0.7 > 0.55 → tradable
        assert result.verdict.is_tradable is True

    def test_apply_runs_abstention_and_tradable_zone(self):
        # Configure abstention to veto on min_confidence > 0.5;
        # prediction is borderline so it's vetoed.
        pred = _pred(p_bull=0.45, p_bear=0.30, p_neu=0.25)
        cfg = HorizonConfig(
            abstention=AbstentionPolicy(min_confidence=0.5),
        )
        result = cfg.apply(pred)
        assert result.policy_changed is True
        assert result.prediction.abstain is True
        # Vetoed → tradable_zone records the abstain rejection
        assert result.verdict.is_tradable is False
        assert "prediction_abstained" in result.verdict.rejection_reasons

    def test_load_horizon_config_round_trips(self, tmp_path: Path):
        text = """
horizon:
  cost_model:
    fixed_points_per_side: 0.50
    slippage_atr_per_side: 0.05
  abstention:
    min_sample_size: 20
    max_fallback_level: 1
    min_confidence: 0.40
    regime_blacklist: ["range"]
  tradable_zone:
    min_confidence: 0.55
    min_expected_edge_atr: 0.05
    kelly_cap: 0.20
"""
        p = tmp_path / "horizon.yaml"
        p.write_text(text, encoding="utf-8")

        cfg = load_horizon_config(p)
        assert cfg.cost_model.fixed_points_per_side == pytest.approx(0.5)
        assert cfg.abstention.min_sample_size == 20
        assert cfg.abstention.regime_blacklist == ["range"]
        assert cfg.tradable_zone.kelly_cap == pytest.approx(0.20)

    def test_load_horizon_config_accepts_flat_root(self, tmp_path: Path):
        text = """
cost_model:
  fixed_points_per_side: 0.75
abstention:
  min_confidence: 0.50
"""
        p = tmp_path / "flat.yaml"
        p.write_text(text, encoding="utf-8")
        cfg = load_horizon_config(p)
        assert cfg.cost_model.fixed_points_per_side == pytest.approx(0.75)
        assert cfg.abstention.min_confidence == pytest.approx(0.50)

    def test_load_horizon_config_unknown_keys_ignored(self, tmp_path: Path):
        text = """
horizon:
  cost_model:
    fixed_points_per_side: 0.10
    NEW_FUTURE_KEY: 42
"""
        p = tmp_path / "future.yaml"
        p.write_text(text, encoding="utf-8")
        cfg = load_horizon_config(p)
        assert cfg.cost_model.fixed_points_per_side == pytest.approx(0.10)

    def test_load_horizon_config_or_default_handles_missing(self, tmp_path: Path):
        cfg = load_horizon_config_or_default(tmp_path / "does_not_exist.yaml")
        assert cfg == HorizonConfig()
        cfg2 = load_horizon_config_or_default(None)
        assert cfg2 == HorizonConfig()

    def test_load_horizon_config_missing_file_raises(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            load_horizon_config(tmp_path / "nope.yaml")

    def test_default_yaml_file_loads(self):
        """Sanity: the shipped default file parses to dataclass-compatible values."""
        from ta_foundation.prediction import horizon_config as hc_mod
        default_path = Path(hc_mod.__file__).with_name("prediction.yaml")
        assert default_path.exists(), "shipped prediction.yaml is missing"
        cfg = load_horizon_config(default_path)
        # Sanity-check a couple of fields
        assert cfg.cost_model.fixed_points_per_side >= 0.0
        assert 0.0 <= cfg.tradable_zone.kelly_cap <= 1.0


# ---------------------------------------------------------------------------
# StackingWeightTable persistence (improvement)
# ---------------------------------------------------------------------------

class TestStackingWeightTablePersistence:
    def test_json_round_trip_preserves_weights(self, tmp_path: Path):
        key = StackingKey("5m", 3, "ny_open", "trend_up")
        table = StackingWeightTable(
            weights={key: {"a": 0.7, "b": 0.3}},
            fallback_weights={"a": 0.5, "b": 0.5},
            floor_weight=0.10,
        )
        path = tmp_path / "weights.json"
        table.save_to_path(path)
        loaded = StackingWeightTable.load_from_path(path)

        assert loaded.floor_weight == pytest.approx(0.10)
        assert loaded.fallback_weights == {"a": 0.5, "b": 0.5}
        assert loaded.weights[key] == {"a": pytest.approx(0.7), "b": pytest.approx(0.3)}

    def test_load_missing_path_raises(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            StackingWeightTable.load_from_path(tmp_path / "nope.json")

    def test_save_creates_parent_dirs(self, tmp_path: Path):
        key = StackingKey("5m", 3, "ny_open", "trend_up")
        table = StackingWeightTable(weights={key: {"a": 1.0}})
        path = tmp_path / "deep" / "subdir" / "weights.json"
        table.save_to_path(path)
        assert path.exists()
        # JSON is valid
        json.loads(path.read_text(encoding="utf-8"))
