"""
Data models for the horizon prediction system.

These are kept deliberately separate from `models.py` (which holds the daily
DailyPrediction / PredictionOutcome) so that the daily flow is not affected.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pandas as pd

DENVER_TZ = "America/Denver"
HORIZON_SCHEMA_VERSION = "horizon-1.0"

VALID_DIRECTIONS = {"bullish", "bearish", "neutral"}
VALID_THRESHOLD_ORDER = {"upside_first", "downside_first", "neither", "both_same_bar"}
VALID_TIMEFRAMES = {"1m", "5m", "15m", "30m", "1h", "4h", "1d"}
VALID_SESSIONS = {
    "asia",
    "london",
    "ny_open",
    "ny_midday",
    "ny_close",
    "ny_full",
    "overnight",
}
VALID_ABSTAIN_REASONS = {
    "insufficient_samples",
    "uncalibrated",
    "regime_drift",
    "low_confidence",
    "no_edge",
    "configuration_error",
    "unknown",
}


def _now_iso() -> str:
    return pd.Timestamp.now(tz=DENVER_TZ).isoformat()


def _new_id() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# CandleHorizonPrediction
# ---------------------------------------------------------------------------

@dataclass
class CandleHorizonPrediction:
    """
    One agent's full probability distribution for the next-N candles on a
    specific timeframe.

    The shape is uniform across all agent types (statistical, Claude, ensemble)
    so the scorer and ranking reports treat them interchangeably.
    """
    prediction_id: str = field(default_factory=_new_id)
    agent_id: str = ""
    instrument: str = ""
    contract: str = ""
    timeframe: str = ""           # one of VALID_TIMEFRAMES
    asof_timestamp: str = ""      # ISO 8601, tz-aware (Denver canonical)
    session_label: str = ""       # one of VALID_SESSIONS
    horizon_candles: int = 1
    created_at: str = field(default_factory=_now_iso)

    # Direction distribution (must approximately sum to 1.0)
    bullish_probability: float = 1.0 / 3.0
    bearish_probability: float = 1.0 / 3.0
    neutral_probability: float = 1.0 / 3.0

    # Convenience confidence — interpretive, not used for scoring
    confidence: float = 0.0

    # Return distribution
    expected_return_points: float = 0.0
    expected_return_atr: float = 0.0
    median_return_points: float = 0.0
    p10_return_points: float = 0.0
    p25_return_points: float = 0.0
    p75_return_points: float = 0.0
    p90_return_points: float = 0.0

    # Path statistics
    expected_mfe_points: float = 0.0
    expected_mae_points: float = 0.0
    upside_threshold_points: float = 0.0
    downside_threshold_points: float = 0.0
    upside_threshold_probability: float = 0.0
    downside_threshold_probability: float = 0.0
    neither_threshold_probability: float = 1.0

    # Diagnostics
    predicted_volatility: float = 0.0
    sample_size: int = 0
    effective_sample_size: float = 0.0
    method_used: str = ""
    fallback_level: int = 0       # 0 = full conditioning, increases as we widen
    calibration_bucket: str = ""  # e.g. "tf=5m,session=ny_open,horizon=3,regime=trend_up"
    feature_snapshot: Dict[str, Any] = field(default_factory=dict)
    reasoning_summary: str = ""

    # Abstention
    abstain: bool = False
    abstain_reason: str = ""

    schema_version: str = HORIZON_SCHEMA_VERSION

    # ------------------------------------------------------------------
    def normalize_direction_probs(self) -> None:
        """
        Renormalize bullish/bearish/neutral so they sum to 1.0. No-op if all
        three are zero (the abstain case).
        """
        total = (
            self.bullish_probability
            + self.bearish_probability
            + self.neutral_probability
        )
        if total <= 0.0:
            return
        self.bullish_probability /= total
        self.bearish_probability /= total
        self.neutral_probability /= total

    def as_dict(self) -> Dict[str, Any]:
        return {
            "prediction_id": self.prediction_id,
            "agent_id": self.agent_id,
            "instrument": self.instrument,
            "contract": self.contract,
            "timeframe": self.timeframe,
            "asof_timestamp": self.asof_timestamp,
            "session_label": self.session_label,
            "horizon_candles": self.horizon_candles,
            "created_at": self.created_at,
            "bullish_probability": self.bullish_probability,
            "bearish_probability": self.bearish_probability,
            "neutral_probability": self.neutral_probability,
            "confidence": self.confidence,
            "expected_return_points": self.expected_return_points,
            "expected_return_atr": self.expected_return_atr,
            "median_return_points": self.median_return_points,
            "p10_return_points": self.p10_return_points,
            "p25_return_points": self.p25_return_points,
            "p75_return_points": self.p75_return_points,
            "p90_return_points": self.p90_return_points,
            "expected_mfe_points": self.expected_mfe_points,
            "expected_mae_points": self.expected_mae_points,
            "upside_threshold_points": self.upside_threshold_points,
            "downside_threshold_points": self.downside_threshold_points,
            "upside_threshold_probability": self.upside_threshold_probability,
            "downside_threshold_probability": self.downside_threshold_probability,
            "neither_threshold_probability": self.neither_threshold_probability,
            "predicted_volatility": self.predicted_volatility,
            "sample_size": self.sample_size,
            "effective_sample_size": self.effective_sample_size,
            "method_used": self.method_used,
            "fallback_level": self.fallback_level,
            "calibration_bucket": self.calibration_bucket,
            "feature_snapshot": dict(self.feature_snapshot),
            "reasoning_summary": self.reasoning_summary,
            "abstain": self.abstain,
            "abstain_reason": self.abstain_reason,
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "CandleHorizonPrediction":
        return cls(
            prediction_id=str(d.get("prediction_id") or _new_id()),
            agent_id=str(d.get("agent_id") or ""),
            instrument=str(d.get("instrument") or ""),
            contract=str(d.get("contract") or ""),
            timeframe=str(d.get("timeframe") or ""),
            asof_timestamp=str(d.get("asof_timestamp") or ""),
            session_label=str(d.get("session_label") or ""),
            horizon_candles=int(d.get("horizon_candles") or 1),
            created_at=str(d.get("created_at") or _now_iso()),
            bullish_probability=float(d.get("bullish_probability") or 0.0),
            bearish_probability=float(d.get("bearish_probability") or 0.0),
            neutral_probability=float(d.get("neutral_probability") or 0.0),
            confidence=float(d.get("confidence") or 0.0),
            expected_return_points=float(d.get("expected_return_points") or 0.0),
            expected_return_atr=float(d.get("expected_return_atr") or 0.0),
            median_return_points=float(d.get("median_return_points") or 0.0),
            p10_return_points=float(d.get("p10_return_points") or 0.0),
            p25_return_points=float(d.get("p25_return_points") or 0.0),
            p75_return_points=float(d.get("p75_return_points") or 0.0),
            p90_return_points=float(d.get("p90_return_points") or 0.0),
            expected_mfe_points=float(d.get("expected_mfe_points") or 0.0),
            expected_mae_points=float(d.get("expected_mae_points") or 0.0),
            upside_threshold_points=float(d.get("upside_threshold_points") or 0.0),
            downside_threshold_points=float(d.get("downside_threshold_points") or 0.0),
            upside_threshold_probability=float(d.get("upside_threshold_probability") or 0.0),
            downside_threshold_probability=float(d.get("downside_threshold_probability") or 0.0),
            neither_threshold_probability=float(d.get("neither_threshold_probability") or 1.0),
            predicted_volatility=float(d.get("predicted_volatility") or 0.0),
            sample_size=int(d.get("sample_size") or 0),
            effective_sample_size=float(d.get("effective_sample_size") or 0.0),
            method_used=str(d.get("method_used") or ""),
            fallback_level=int(d.get("fallback_level") or 0),
            calibration_bucket=str(d.get("calibration_bucket") or ""),
            feature_snapshot=dict(d.get("feature_snapshot") or {}),
            reasoning_summary=str(d.get("reasoning_summary") or ""),
            abstain=bool(d.get("abstain", False)),
            abstain_reason=str(d.get("abstain_reason") or ""),
            schema_version=str(d.get("schema_version") or HORIZON_SCHEMA_VERSION),
        )


# ---------------------------------------------------------------------------
# CandleHorizonOutcome
# ---------------------------------------------------------------------------

@dataclass
class CandleHorizonOutcome:
    """
    Measured outcome of a `CandleHorizonPrediction`. Score fields are populated
    by `horizon_scorer.score_horizon_prediction`.
    """
    prediction_id: str = ""
    agent_id: str = ""
    instrument: str = ""
    contract: str = ""
    timeframe: str = ""
    asof_timestamp: str = ""
    horizon_candles: int = 1
    measured_at: str = field(default_factory=_now_iso)

    asof_bar_index: int = -1
    horizon_end_timestamp: str = ""
    horizon_bars_observed: int = 0   # may be < horizon_candles if data ran out

    actual_open: float = 0.0
    actual_close: float = 0.0
    actual_high: float = 0.0
    actual_low: float = 0.0

    actual_return_points: float = 0.0
    actual_return_atr: float = 0.0
    actual_direction: str = "neutral"
    actual_mfe_points: float = 0.0
    actual_mae_points: float = 0.0
    actual_efficiency_ratio: float = 0.0

    upside_threshold_points: float = 0.0
    downside_threshold_points: float = 0.0
    upside_threshold_hit: bool = False
    downside_threshold_hit: bool = False
    threshold_hit_order: str = "neither"   # one of VALID_THRESHOLD_ORDER

    prior_atr: float = 0.0

    # Filled by scorer
    brier_score_direction: float = 0.0
    log_loss_direction: float = 0.0
    brier_score_thresholds: float = 0.0
    return_mae: float = 0.0
    mfe_error: float = 0.0
    mae_error: float = 0.0
    efficiency_ratio_error: float = 0.0
    calibration_error: float = 0.0

    direction_score: float = 0.0
    threshold_score: float = 0.0
    return_score: float = 0.0
    path_score: float = 0.0
    composite_score: float = 0.0

    schema_version: str = HORIZON_SCHEMA_VERSION

    def as_dict(self) -> Dict[str, Any]:
        return {
            "prediction_id": self.prediction_id,
            "agent_id": self.agent_id,
            "instrument": self.instrument,
            "contract": self.contract,
            "timeframe": self.timeframe,
            "asof_timestamp": self.asof_timestamp,
            "horizon_candles": self.horizon_candles,
            "measured_at": self.measured_at,
            "asof_bar_index": self.asof_bar_index,
            "horizon_end_timestamp": self.horizon_end_timestamp,
            "horizon_bars_observed": self.horizon_bars_observed,
            "actual_open": self.actual_open,
            "actual_close": self.actual_close,
            "actual_high": self.actual_high,
            "actual_low": self.actual_low,
            "actual_return_points": self.actual_return_points,
            "actual_return_atr": self.actual_return_atr,
            "actual_direction": self.actual_direction,
            "actual_mfe_points": self.actual_mfe_points,
            "actual_mae_points": self.actual_mae_points,
            "actual_efficiency_ratio": self.actual_efficiency_ratio,
            "upside_threshold_points": self.upside_threshold_points,
            "downside_threshold_points": self.downside_threshold_points,
            "upside_threshold_hit": self.upside_threshold_hit,
            "downside_threshold_hit": self.downside_threshold_hit,
            "threshold_hit_order": self.threshold_hit_order,
            "prior_atr": self.prior_atr,
            "brier_score_direction": self.brier_score_direction,
            "log_loss_direction": self.log_loss_direction,
            "brier_score_thresholds": self.brier_score_thresholds,
            "return_mae": self.return_mae,
            "mfe_error": self.mfe_error,
            "mae_error": self.mae_error,
            "efficiency_ratio_error": self.efficiency_ratio_error,
            "calibration_error": self.calibration_error,
            "direction_score": self.direction_score,
            "threshold_score": self.threshold_score,
            "return_score": self.return_score,
            "path_score": self.path_score,
            "composite_score": self.composite_score,
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "CandleHorizonOutcome":
        return cls(
            prediction_id=str(d.get("prediction_id") or ""),
            agent_id=str(d.get("agent_id") or ""),
            instrument=str(d.get("instrument") or ""),
            contract=str(d.get("contract") or ""),
            timeframe=str(d.get("timeframe") or ""),
            asof_timestamp=str(d.get("asof_timestamp") or ""),
            horizon_candles=int(d.get("horizon_candles") or 1),
            measured_at=str(d.get("measured_at") or _now_iso()),
            asof_bar_index=int(d.get("asof_bar_index") or -1),
            horizon_end_timestamp=str(d.get("horizon_end_timestamp") or ""),
            horizon_bars_observed=int(d.get("horizon_bars_observed") or 0),
            actual_open=float(d.get("actual_open") or 0.0),
            actual_close=float(d.get("actual_close") or 0.0),
            actual_high=float(d.get("actual_high") or 0.0),
            actual_low=float(d.get("actual_low") or 0.0),
            actual_return_points=float(d.get("actual_return_points") or 0.0),
            actual_return_atr=float(d.get("actual_return_atr") or 0.0),
            actual_direction=str(d.get("actual_direction") or "neutral"),
            actual_mfe_points=float(d.get("actual_mfe_points") or 0.0),
            actual_mae_points=float(d.get("actual_mae_points") or 0.0),
            actual_efficiency_ratio=float(d.get("actual_efficiency_ratio") or 0.0),
            upside_threshold_points=float(d.get("upside_threshold_points") or 0.0),
            downside_threshold_points=float(d.get("downside_threshold_points") or 0.0),
            upside_threshold_hit=bool(d.get("upside_threshold_hit", False)),
            downside_threshold_hit=bool(d.get("downside_threshold_hit", False)),
            threshold_hit_order=str(d.get("threshold_hit_order") or "neither"),
            prior_atr=float(d.get("prior_atr") or 0.0),
            brier_score_direction=float(d.get("brier_score_direction") or 0.0),
            log_loss_direction=float(d.get("log_loss_direction") or 0.0),
            brier_score_thresholds=float(d.get("brier_score_thresholds") or 0.0),
            return_mae=float(d.get("return_mae") or 0.0),
            mfe_error=float(d.get("mfe_error") or 0.0),
            mae_error=float(d.get("mae_error") or 0.0),
            efficiency_ratio_error=float(d.get("efficiency_ratio_error") or 0.0),
            calibration_error=float(d.get("calibration_error") or 0.0),
            direction_score=float(d.get("direction_score") or 0.0),
            threshold_score=float(d.get("threshold_score") or 0.0),
            return_score=float(d.get("return_score") or 0.0),
            path_score=float(d.get("path_score") or 0.0),
            composite_score=float(d.get("composite_score") or 0.0),
            schema_version=str(d.get("schema_version") or HORIZON_SCHEMA_VERSION),
        )
