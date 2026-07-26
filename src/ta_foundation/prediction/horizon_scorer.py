"""
Score a CandleHorizonPrediction against its measured CandleHorizonOutcome.

Uses proper scoring rules (Brier for probabilities, MAE for point predictions).
All component scores are mapped to [0, 1] where higher is better. The composite
weights are configurable; defaults are documented at module level.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from .horizon_models import CandleHorizonOutcome, CandleHorizonPrediction

# Maximum Brier score for a 3-class probability vector is 2.0 in the
# worst-case "all probability on the wrong class" scenario, but for a
# valid probability simplex the realistic max is 2.0 as well. We map by
# `1 - brier/2` to keep direction_score in [0, 1].
_MAX_BRIER_3CLASS = 2.0
# Eps to keep log-loss finite if a probability is 0.0 exactly.
_LOG_LOSS_EPS = 1e-12


@dataclass
class HorizonCompositeWeights:
    """
    Weights for the composite horizon score. Must sum to 1.0; if they don't,
    they are normalized in `validated()` and a warning is no-op.
    """
    direction: float = 0.35
    thresholds: float = 0.25
    return_: float = 0.20      # `return` is a reserved word; field is `return_`
    path: float = 0.10
    calibration: float = 0.10

    def validated(self) -> "HorizonCompositeWeights":
        total = self.direction + self.thresholds + self.return_ + self.path + self.calibration
        if total <= 0.0:
            raise ValueError("HorizonCompositeWeights total must be > 0")
        if abs(total - 1.0) < 1e-9:
            return self
        return HorizonCompositeWeights(
            direction=self.direction / total,
            thresholds=self.thresholds / total,
            return_=self.return_ / total,
            path=self.path / total,
            calibration=self.calibration / total,
        )


def score_horizon_prediction(
    prediction: CandleHorizonPrediction,
    outcome: CandleHorizonOutcome,
    weights: Optional[HorizonCompositeWeights] = None,
    calibration_error: float = 0.0,
) -> None:
    """
    Populate score fields on `outcome` in place.

    Args:
        prediction: forecast.
        outcome: realized outcome (already measured).
        weights: composite weights; defaults to HorizonCompositeWeights().
        calibration_error: optional pre-computed bucket calibration error
            (ECE) for the agent / bucket combination. The scorer treats it
            as the calibration component directly: calibration_score =
            1 - calibration_error. Defaults to 0.0 (perfectly calibrated).
            Phase 2 will surface a real per-bucket tracker; Phase 1 simply
            accepts whatever the caller passes.
    """
    if prediction.abstain:
        # An abstaining prediction does not earn or lose points. Its scores
        # stay at 0 so leaderboards can filter on abstain=True.
        outcome.calibration_error = float(calibration_error)
        return

    w = (weights or HorizonCompositeWeights()).validated()

    # ------------------------------------------------------------------
    # Direction (Brier on 3-class one-hot)
    # ------------------------------------------------------------------
    actual_one_hot = _direction_to_one_hot(outcome.actual_direction)
    pred_vec = _direction_vec(prediction)
    brier = sum((p - a) ** 2 for p, a in zip(pred_vec, actual_one_hot))
    outcome.brier_score_direction = float(brier)
    outcome.direction_score = max(0.0, 1.0 - brier / _MAX_BRIER_3CLASS)

    # Log loss on the actual class (with eps guard)
    actual_idx = actual_one_hot.index(1.0)
    p_actual = max(_LOG_LOSS_EPS, pred_vec[actual_idx])
    outcome.log_loss_direction = float(-math.log(p_actual))

    # ------------------------------------------------------------------
    # Threshold (Brier on the 3 mutually-exclusive outcomes)
    # ------------------------------------------------------------------
    threshold_brier, threshold_score = _score_thresholds(prediction, outcome)
    outcome.brier_score_thresholds = float(threshold_brier)
    outcome.threshold_score = float(threshold_score)

    # ------------------------------------------------------------------
    # Return (MAE in ATR units, mapped to [0,1])
    # ------------------------------------------------------------------
    return_err = abs(prediction.expected_return_points - outcome.actual_return_points)
    outcome.return_mae = float(return_err)
    if outcome.prior_atr > 0:
        # Within 1 ATR of error → score >= 0
        outcome.return_score = max(0.0, 1.0 - return_err / outcome.prior_atr)
    else:
        outcome.return_score = 0.5  # uninformative when ATR is unknown

    # ------------------------------------------------------------------
    # Path (MFE / MAE error)
    # ------------------------------------------------------------------
    mfe_err = abs(prediction.expected_mfe_points - outcome.actual_mfe_points)
    mae_err = abs(prediction.expected_mae_points - outcome.actual_mae_points)
    outcome.mfe_error = float(mfe_err)
    outcome.mae_error = float(mae_err)

    # Efficiency ratio diagnostics: predictions don't currently expose an
    # expected efficiency ratio, so we leave it 0.0 in Phase 1. The field
    # is reserved for Phase 3 distribution forecasts.
    outcome.efficiency_ratio_error = 0.0

    if outcome.prior_atr > 0:
        path_err_norm = (mfe_err + mae_err) / (2.0 * outcome.prior_atr)
        outcome.path_score = max(0.0, 1.0 - path_err_norm)
    else:
        outcome.path_score = 0.5

    # ------------------------------------------------------------------
    # Calibration (caller-supplied ECE)
    # ------------------------------------------------------------------
    cal_err = max(0.0, min(1.0, float(calibration_error)))
    outcome.calibration_error = cal_err
    calibration_score = 1.0 - cal_err

    # ------------------------------------------------------------------
    # Composite
    # ------------------------------------------------------------------
    outcome.composite_score = float(
        w.direction * outcome.direction_score
        + w.thresholds * outcome.threshold_score
        + w.return_ * outcome.return_score
        + w.path * outcome.path_score
        + w.calibration * calibration_score
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _direction_to_one_hot(direction: str) -> list[float]:
    """Order: bullish, bearish, neutral."""
    if direction == "bullish":
        return [1.0, 0.0, 0.0]
    if direction == "bearish":
        return [0.0, 1.0, 0.0]
    return [0.0, 0.0, 1.0]


def _direction_vec(p: CandleHorizonPrediction) -> list[float]:
    return [p.bullish_probability, p.bearish_probability, p.neutral_probability]


def _score_thresholds(
    prediction: CandleHorizonPrediction,
    outcome: CandleHorizonOutcome,
) -> tuple[float, float]:
    """
    Brier score over the 3-way (upside_first, downside_first, neither)
    outcome. Returns (brier, normalized_score in [0,1]).
    """
    if outcome.threshold_hit_order == "upside_first":
        actual = (1.0, 0.0, 0.0)
    elif outcome.threshold_hit_order == "downside_first":
        actual = (0.0, 1.0, 0.0)
    elif outcome.threshold_hit_order == "both_same_bar":
        # Ambiguous resolution → split credit half-half between up/down
        actual = (0.5, 0.5, 0.0)
    else:
        actual = (0.0, 0.0, 1.0)

    pred = (
        max(0.0, min(1.0, prediction.upside_threshold_probability)),
        max(0.0, min(1.0, prediction.downside_threshold_probability)),
        max(0.0, min(1.0, prediction.neither_threshold_probability)),
    )

    brier = sum((p - a) ** 2 for p, a in zip(pred, actual))
    score = max(0.0, 1.0 - brier / _MAX_BRIER_3CLASS)
    return brier, score
