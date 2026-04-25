from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pandas as pd

DENVER_TZ = "America/Denver"

VALID_TREND_DIRECTIONS = {"bullish", "bearish", "neutral"}
VALID_BREAKOUT_DIRECTIONS = {"up", "down", "either", "none"}
VALID_LEVEL_TYPES = {"support", "resistance", "pivot", "magnet"}

SCHEMA_VERSION = "1.0"


def _now_iso() -> str:
    return pd.Timestamp.now(tz=DENVER_TZ).isoformat()


def _new_id() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# LevelOutcome — nested inside PredictionOutcome
# ---------------------------------------------------------------------------

@dataclass
class LevelOutcome:
    price: float
    label: str
    was_touched: bool
    touch_price: Optional[float] = None
    touch_bar_index: Optional[int] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "price": self.price,
            "label": self.label,
            "was_touched": self.was_touched,
            "touch_price": self.touch_price,
            "touch_bar_index": self.touch_bar_index,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "LevelOutcome":
        return cls(
            price=float(d["price"]),
            label=str(d.get("label") or ""),
            was_touched=bool(d.get("was_touched", False)),
            touch_price=float(d["touch_price"]) if d.get("touch_price") is not None else None,
            touch_bar_index=int(d["touch_bar_index"]) if d.get("touch_bar_index") is not None else None,
        )


# ---------------------------------------------------------------------------
# DailyPrediction — forward-looking prediction made after NY close
# ---------------------------------------------------------------------------

@dataclass
class DailyPrediction:
    """
    One agent's structured prediction for a single trading day.
    Made after the prior session closes; target_date is the day being predicted.
    """
    prediction_id: str = field(default_factory=_new_id)
    instrument: str = ""
    contract: str = ""
    target_date: str = ""          # YYYY-MM-DD of the day being predicted
    created_at: str = field(default_factory=_now_iso)
    agent_id: str = ""

    trend_direction: str = "neutral"     # "bullish" | "bearish" | "neutral"
    trend_confidence: float = 0.5
    is_trending: bool = False
    chop_confidence: float = 0.5

    predicted_high: float = 0.0
    predicted_low: float = 0.0

    breakout_probability: float = 0.0
    breakout_direction: str = "none"    # "up" | "down" | "either" | "none"
    event_risk_score: float = 0.0

    # key_levels: list of {"price": float, "label": str, "level_type": str, "touch_probability": float}
    key_levels: List[Dict[str, Any]] = field(default_factory=list)

    reasoning: str = ""
    # Full context dict passed to the agent — stored for reproducibility and similarity search
    context_snapshot: Dict[str, Any] = field(default_factory=dict)

    schema_version: str = SCHEMA_VERSION

    def as_dict(self) -> Dict[str, Any]:
        return {
            "prediction_id": self.prediction_id,
            "instrument": self.instrument,
            "contract": self.contract,
            "target_date": self.target_date,
            "created_at": self.created_at,
            "agent_id": self.agent_id,
            "trend_direction": self.trend_direction,
            "trend_confidence": self.trend_confidence,
            "is_trending": self.is_trending,
            "chop_confidence": self.chop_confidence,
            "predicted_high": self.predicted_high,
            "predicted_low": self.predicted_low,
            "breakout_probability": self.breakout_probability,
            "breakout_direction": self.breakout_direction,
            "event_risk_score": self.event_risk_score,
            "key_levels": list(self.key_levels),
            "reasoning": self.reasoning,
            "context_snapshot": dict(self.context_snapshot),
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "DailyPrediction":
        return cls(
            prediction_id=str(d.get("prediction_id") or _new_id()),
            instrument=str(d.get("instrument") or ""),
            contract=str(d.get("contract") or ""),
            target_date=str(d.get("target_date") or ""),
            created_at=str(d.get("created_at") or _now_iso()),
            agent_id=str(d.get("agent_id") or ""),
            trend_direction=str(d.get("trend_direction") or "neutral"),
            trend_confidence=float(d.get("trend_confidence") or 0.5),
            is_trending=bool(d.get("is_trending", False)),
            chop_confidence=float(d.get("chop_confidence") or 0.5),
            predicted_high=float(d.get("predicted_high") or 0.0),
            predicted_low=float(d.get("predicted_low") or 0.0),
            breakout_probability=float(d.get("breakout_probability") or 0.0),
            breakout_direction=str(d.get("breakout_direction") or "none"),
            event_risk_score=float(d.get("event_risk_score") or 0.0),
            key_levels=list(d.get("key_levels") or []),
            reasoning=str(d.get("reasoning") or ""),
            context_snapshot=dict(d.get("context_snapshot") or {}),
            schema_version=str(d.get("schema_version") or SCHEMA_VERSION),
        )


# ---------------------------------------------------------------------------
# PredictionOutcome — measured after the target day closes
# ---------------------------------------------------------------------------

@dataclass
class PredictionOutcome:
    """
    Measured outcomes for a day, scored against the paired DailyPrediction.
    score_prediction() populates the score fields after measure_outcome() creates this.
    """
    prediction_id: str = ""
    agent_id: str = ""
    target_date: str = ""
    instrument: str = ""
    measured_at: str = field(default_factory=_now_iso)

    actual_open: float = 0.0
    actual_high: float = 0.0
    actual_low: float = 0.0
    actual_close: float = 0.0
    actual_range: float = 0.0

    actual_direction: str = "neutral"
    actual_is_trending: bool = False
    efficiency_ratio: float = 0.0

    levels_touched: List[LevelOutcome] = field(default_factory=list)

    breakout_occurred: bool = False
    breakout_direction_actual: str = "none"
    breakout_magnitude: float = 0.0

    prior_atr: float = 0.0

    # Populated by scorer
    trend_score: float = 0.0
    chop_score: float = 0.0
    levels_score: float = 0.0
    breakout_score: float = 0.0
    composite_score: float = 0.0

    # Copied from context_snapshot for conditional stats
    regime_label: str = "unknown"
    had_high_impact_event: bool = False

    schema_version: str = SCHEMA_VERSION

    def as_dict(self) -> Dict[str, Any]:
        return {
            "prediction_id": self.prediction_id,
            "target_date": self.target_date,
            "instrument": self.instrument,
            "measured_at": self.measured_at,
            "actual_open": self.actual_open,
            "actual_high": self.actual_high,
            "actual_low": self.actual_low,
            "actual_close": self.actual_close,
            "actual_range": self.actual_range,
            "actual_direction": self.actual_direction,
            "actual_is_trending": self.actual_is_trending,
            "efficiency_ratio": self.efficiency_ratio,
            "levels_touched": [lv.as_dict() for lv in self.levels_touched],
            "breakout_occurred": self.breakout_occurred,
            "breakout_direction_actual": self.breakout_direction_actual,
            "breakout_magnitude": self.breakout_magnitude,
            "prior_atr": self.prior_atr,
            "trend_score": self.trend_score,
            "chop_score": self.chop_score,
            "levels_score": self.levels_score,
            "breakout_score": self.breakout_score,
            "composite_score": self.composite_score,
            "regime_label": self.regime_label,
            "had_high_impact_event": self.had_high_impact_event,
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "PredictionOutcome":
        raw_levels = d.get("levels_touched") or []
        return cls(
            prediction_id=str(d.get("prediction_id") or ""),
            target_date=str(d.get("target_date") or ""),
            instrument=str(d.get("instrument") or ""),
            measured_at=str(d.get("measured_at") or _now_iso()),
            actual_open=float(d.get("actual_open") or 0.0),
            actual_high=float(d.get("actual_high") or 0.0),
            actual_low=float(d.get("actual_low") or 0.0),
            actual_close=float(d.get("actual_close") or 0.0),
            actual_range=float(d.get("actual_range") or 0.0),
            actual_direction=str(d.get("actual_direction") or "neutral"),
            actual_is_trending=bool(d.get("actual_is_trending", False)),
            efficiency_ratio=float(d.get("efficiency_ratio") or 0.0),
            levels_touched=[LevelOutcome.from_dict(lv) for lv in raw_levels],
            breakout_occurred=bool(d.get("breakout_occurred", False)),
            breakout_direction_actual=str(d.get("breakout_direction_actual") or "none"),
            breakout_magnitude=float(d.get("breakout_magnitude") or 0.0),
            prior_atr=float(d.get("prior_atr") or 0.0),
            trend_score=float(d.get("trend_score") or 0.0),
            chop_score=float(d.get("chop_score") or 0.0),
            levels_score=float(d.get("levels_score") or 0.0),
            breakout_score=float(d.get("breakout_score") or 0.0),
            composite_score=float(d.get("composite_score") or 0.0),
            regime_label=str(d.get("regime_label") or "unknown"),
            had_high_impact_event=bool(d.get("had_high_impact_event", False)),
            schema_version=str(d.get("schema_version") or SCHEMA_VERSION),
        )


# ---------------------------------------------------------------------------
# AgentStats — rolling performance record per agent
# ---------------------------------------------------------------------------

@dataclass
class AgentStats:
    agent_id: str = ""
    computed_at: str = field(default_factory=_now_iso)
    window_days: int = 60
    sample_count: int = 0

    mean_composite_score: float = 0.0
    trend_accuracy: float = 0.0
    chop_accuracy: float = 0.0
    breakout_accuracy: float = 0.0
    mean_levels_score: float = 0.0
    ece: float = 0.0

    # {"trend_up": 0.72, "range": 0.55, ...}
    regime_accuracy: Dict[str, float] = field(default_factory=dict)
    event_day_accuracy: float = 0.0
    normal_day_accuracy: float = 0.0

    # list of {"bin_center": float, "mean_confidence": float, "fraction_correct": float, "count": int}
    trend_calibration_buckets: List[Dict[str, Any]] = field(default_factory=list)

    drift_warning: bool = False
    short_window_score: float = 0.0   # last 10 days
    long_window_score: float = 0.0    # full window

    def as_dict(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "computed_at": self.computed_at,
            "window_days": self.window_days,
            "sample_count": self.sample_count,
            "mean_composite_score": self.mean_composite_score,
            "trend_accuracy": self.trend_accuracy,
            "chop_accuracy": self.chop_accuracy,
            "breakout_accuracy": self.breakout_accuracy,
            "mean_levels_score": self.mean_levels_score,
            "ece": self.ece,
            "regime_accuracy": dict(self.regime_accuracy),
            "event_day_accuracy": self.event_day_accuracy,
            "normal_day_accuracy": self.normal_day_accuracy,
            "trend_calibration_buckets": list(self.trend_calibration_buckets),
            "drift_warning": self.drift_warning,
            "short_window_score": self.short_window_score,
            "long_window_score": self.long_window_score,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "AgentStats":
        return cls(
            agent_id=str(d.get("agent_id") or ""),
            computed_at=str(d.get("computed_at") or _now_iso()),
            window_days=int(d.get("window_days") or 60),
            sample_count=int(d.get("sample_count") or 0),
            mean_composite_score=float(d.get("mean_composite_score") or 0.0),
            trend_accuracy=float(d.get("trend_accuracy") or 0.0),
            chop_accuracy=float(d.get("chop_accuracy") or 0.0),
            breakout_accuracy=float(d.get("breakout_accuracy") or 0.0),
            mean_levels_score=float(d.get("mean_levels_score") or 0.0),
            ece=float(d.get("ece") or 0.0),
            regime_accuracy=dict(d.get("regime_accuracy") or {}),
            event_day_accuracy=float(d.get("event_day_accuracy") or 0.0),
            normal_day_accuracy=float(d.get("normal_day_accuracy") or 0.0),
            trend_calibration_buckets=list(d.get("trend_calibration_buckets") or []),
            drift_warning=bool(d.get("drift_warning", False)),
            short_window_score=float(d.get("short_window_score") or 0.0),
            long_window_score=float(d.get("long_window_score") or 0.0),
        )


# ---------------------------------------------------------------------------
# SimilarContext — a historical day surfaced by the calibrator
# ---------------------------------------------------------------------------

@dataclass
class SimilarContext:
    target_date: str
    similarity_score: float
    prediction: DailyPrediction
    outcome: Optional[PredictionOutcome]
    feature_vector: List[float] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        """Lightweight serialization — omits nested context_snapshot to prevent recursion."""
        pred = self.prediction
        pred_summary: Dict[str, Any] = {
            "prediction_id": pred.prediction_id,
            "agent_id": pred.agent_id,
            "trend_direction": pred.trend_direction,
            "trend_confidence": pred.trend_confidence,
            "is_trending": pred.is_trending,
            "chop_confidence": pred.chop_confidence,
            "breakout_probability": pred.breakout_probability,
            "breakout_direction": pred.breakout_direction,
        }
        outcome_summary: Optional[Dict[str, Any]] = None
        if self.outcome is not None:
            o = self.outcome
            outcome_summary = {
                "actual_direction": o.actual_direction,
                "actual_is_trending": o.actual_is_trending,
                "efficiency_ratio": round(o.efficiency_ratio, 4),
                "breakout_occurred": o.breakout_occurred,
                "breakout_direction_actual": o.breakout_direction_actual,
                "composite_score": round(o.composite_score, 4),
                "trend_score": round(o.trend_score, 4),
                "chop_score": round(o.chop_score, 4),
                "levels_score": round(o.levels_score, 4),
                "breakout_score": round(o.breakout_score, 4),
                "regime_label": o.regime_label,
            }
        return {
            "target_date": self.target_date,
            "similarity_score": round(self.similarity_score, 4),
            "prediction_summary": pred_summary,
            "outcome": outcome_summary,
            "feature_vector": [round(v, 6) for v in self.feature_vector],
        }
