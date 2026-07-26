"""
Tradable-zone filter for horizon predictions.

Given a `CandleHorizonPrediction` and a `CostModel`, decide:

  - Should the strategy take this trade at all?
  - In which direction?
  - What stop / target makes sense (from the prediction's tail percentiles)?
  - What size fraction is justified by the predicted edge?

The filter is a *decision layer*, not a risk model: it does not change
the prediction itself. It produces a `TradableZoneVerdict` that the
caller (live executor or report renderer) can act on or display.

The default sizing rule is a Kelly-lite fraction `edge / variance`,
capped at `kelly_cap` (0.25 by default). This is conservative — most
practitioners use a quarter-Kelly to absorb model error.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .horizon_costs import CostModel
from .horizon_models import CandleHorizonPrediction


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class TradableZoneConfig:
    """Thresholds for the `is_tradable` decision."""
    min_confidence: float = 0.55
    min_expected_edge_atr: float = 0.0
    min_effective_sample_size: float = 8.0
    allow_neutral_argmax: bool = False
    kelly_cap: float = 0.25
    min_size_fraction: float = 0.0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "min_confidence": self.min_confidence,
            "min_expected_edge_atr": self.min_expected_edge_atr,
            "min_effective_sample_size": self.min_effective_sample_size,
            "allow_neutral_argmax": self.allow_neutral_argmax,
            "kelly_cap": self.kelly_cap,
            "min_size_fraction": self.min_size_fraction,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any] | None) -> "TradableZoneConfig":
        d = dict(d or {})
        return cls(
            min_confidence=float(d.get("min_confidence", 0.55)),
            min_expected_edge_atr=float(d.get("min_expected_edge_atr", 0.0)),
            min_effective_sample_size=float(d.get("min_effective_sample_size", 8.0)),
            allow_neutral_argmax=bool(d.get("allow_neutral_argmax", False)),
            kelly_cap=float(d.get("kelly_cap", 0.25)),
            min_size_fraction=float(d.get("min_size_fraction", 0.0)),
        )


# ---------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------

@dataclass
class TradableZoneVerdict:
    is_tradable: bool
    recommended_direction: str          # "bullish" | "bearish" | "neutral"
    confidence: float
    expected_edge_points: float          # net of cost
    expected_edge_atr: float
    cost_round_trip_points: float
    cost_round_trip_atr: float
    recommended_stop_points: float
    recommended_target_points: float
    recommended_size_fraction: float     # 0..kelly_cap
    rejection_reasons: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "is_tradable": self.is_tradable,
            "recommended_direction": self.recommended_direction,
            "confidence": self.confidence,
            "expected_edge_points": self.expected_edge_points,
            "expected_edge_atr": self.expected_edge_atr,
            "cost_round_trip_points": self.cost_round_trip_points,
            "cost_round_trip_atr": self.cost_round_trip_atr,
            "recommended_stop_points": self.recommended_stop_points,
            "recommended_target_points": self.recommended_target_points,
            "recommended_size_fraction": self.recommended_size_fraction,
            "rejection_reasons": list(self.rejection_reasons),
        }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def evaluate_tradable_zone(
    prediction: CandleHorizonPrediction,
    cost_model: Optional[CostModel] = None,
    config: Optional[TradableZoneConfig] = None,
) -> TradableZoneVerdict:
    """
    Compute the tradable-zone verdict for a single prediction.

    A non-tradable verdict returns `is_tradable=False` and at least one
    `rejection_reasons` entry. Recommended sizing/levels are still set on
    the verdict so the caller can introspect what the filter saw.
    """
    cost = cost_model or CostModel()
    cfg = config or TradableZoneConfig()

    direction, sign = _argmax_direction(prediction)
    confidence = max(
        prediction.bullish_probability,
        prediction.bearish_probability,
        prediction.neutral_probability,
    )
    prior_atr = float((prediction.feature_snapshot or {}).get("prior_atr") or 0.0)

    cost_pts = cost.round_trip_cost_points(prior_atr)
    cost_atr = cost.round_trip_cost_atr(prior_atr) if prior_atr > 0 else 0.0

    gross_edge_pts = sign * float(prediction.expected_return_points)
    net_edge_pts = gross_edge_pts - cost_pts
    net_edge_atr = (net_edge_pts / prior_atr) if prior_atr > 0 else 0.0

    stop_pts, target_pts = _stops_and_targets(prediction, sign)
    size = _kelly_fraction(prediction, net_edge_pts, cap=cfg.kelly_cap)

    rejections: List[str] = []
    if prediction.abstain:
        rejections.append("prediction_abstained")
    if not cfg.allow_neutral_argmax and direction == "neutral":
        rejections.append("neutral_argmax")
    if confidence < cfg.min_confidence:
        rejections.append(f"confidence<{cfg.min_confidence:.2f}")
    if net_edge_atr < cfg.min_expected_edge_atr:
        rejections.append(f"edge_atr<{cfg.min_expected_edge_atr:.3f}")
    if prediction.effective_sample_size < cfg.min_effective_sample_size:
        rejections.append(f"effective_sample_size<{cfg.min_effective_sample_size:.0f}")
    if size < cfg.min_size_fraction:
        rejections.append(f"size<{cfg.min_size_fraction:.3f}")

    return TradableZoneVerdict(
        is_tradable=not rejections,
        recommended_direction=direction,
        confidence=float(confidence),
        expected_edge_points=float(net_edge_pts),
        expected_edge_atr=float(net_edge_atr),
        cost_round_trip_points=float(cost_pts),
        cost_round_trip_atr=float(cost_atr),
        recommended_stop_points=float(stop_pts),
        recommended_target_points=float(target_pts),
        recommended_size_fraction=float(size),
        rejection_reasons=rejections,
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _argmax_direction(p: CandleHorizonPrediction) -> Tuple[str, int]:
    """Return (label, sign). Sign is +1/0/-1; tie → neutral."""
    if p.abstain:
        return "neutral", 0
    bull = p.bullish_probability
    bear = p.bearish_probability
    neu = p.neutral_probability
    if bull > bear and bull > neu:
        return "bullish", 1
    if bear > bull and bear > neu:
        return "bearish", -1
    return "neutral", 0


def _stops_and_targets(
    pred: CandleHorizonPrediction,
    sign: int,
) -> Tuple[float, float]:
    """
    Default stop/target levels in points:
      - bullish: target = upside_threshold_points, stop = downside_threshold_points
      - bearish: roles reversed
      - neutral: zeros (no trade)

    Falls back to half the predicted volatility if the threshold fields are
    zero (e.g., an early prediction with no thresholds set).
    """
    up = max(0.0, float(pred.upside_threshold_points))
    down = max(0.0, float(pred.downside_threshold_points))
    vol_floor = max(0.0, 0.5 * float(pred.predicted_volatility))

    up = max(up, vol_floor)
    down = max(down, vol_floor)

    if sign > 0:
        return down, up
    if sign < 0:
        return up, down
    return 0.0, 0.0


def _kelly_fraction(
    pred: CandleHorizonPrediction,
    net_edge_pts: float,
    cap: float,
) -> float:
    """
    Kelly-lite size fraction = net_edge / variance, capped at `cap`.

    Variance is approximated with `predicted_volatility ** 2` because that
    field is already populated by every concrete agent. Negative edge → 0.
    """
    if net_edge_pts <= 0.0:
        return 0.0
    vol = max(1e-6, float(pred.predicted_volatility))
    variance = vol * vol
    f = net_edge_pts / variance
    return float(max(0.0, min(cap, f)))
