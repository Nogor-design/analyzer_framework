from __future__ import annotations

from .models import DailyPrediction, PredictionOutcome

# Composite weights — must sum to 1.0
_W_TREND = 0.35
_W_CHOP = 0.25
_W_LEVELS = 0.25
_W_BREAKOUT = 0.15

# Minimum number of key levels to avoid a soft penalty on levels_score
MIN_LEVELS_FOR_FULL_SCORE = 3
LEVELS_UNDERCOUNT_PENALTY = 0.90


def score_prediction(prediction: DailyPrediction, outcome: PredictionOutcome) -> None:
    """
    Populate the score fields on outcome in place.

    Scoring uses proper/Brier-style rules where possible so that confident
    correct calls score higher than hedged correct calls, and confident
    wrong calls are penalised more heavily than hedged wrong calls.
    """
    outcome.trend_score = _trend_score(prediction, outcome)
    outcome.chop_score = _chop_score(prediction, outcome)
    outcome.levels_score = _levels_score(prediction, outcome)
    outcome.breakout_score = _breakout_score(prediction, outcome)

    outcome.composite_score = (
        _W_TREND * outcome.trend_score
        + _W_CHOP * outcome.chop_score
        + _W_LEVELS * outcome.levels_score
        + _W_BREAKOUT * outcome.breakout_score
    )


# ---------------------------------------------------------------------------
# Component scorers
# ---------------------------------------------------------------------------

def _trend_score(prediction: DailyPrediction, outcome: PredictionOutcome) -> float:
    """
    Brier-style: penalise confident wrong calls, reward confident correct ones.

    Mapping of (predicted, actual) to base_score:
      same direction           → 1.0
      opposite directions      → 0.0
      one side is "neutral"    → 0.5 (partial credit / partial miss)

    Modulate by confidence:
      score = base * conf + (1 - conf) * 0.5
    This ensures a 50% confidence prediction always scores 0.5 regardless
    of whether it was right — incentivising the agent to be well-calibrated.
    """
    pred_dir = prediction.trend_direction
    act_dir = outcome.actual_direction
    conf = max(0.0, min(1.0, prediction.trend_confidence))

    if pred_dir == act_dir:
        base = 1.0
    elif "neutral" in (pred_dir, act_dir):
        base = 0.5
    else:
        base = 0.0  # bullish vs bearish, or vice versa

    # Linear interpolation: 0.5 conf always scores 0.5; higher conf amplifies the result.
    # score = 0.5 + (conf - 0.5) * (2 * base - 1)
    return 0.5 + (conf - 0.5) * (2.0 * base - 1.0)


def _chop_score(prediction: DailyPrediction, outcome: PredictionOutcome) -> float:
    """
    Same Brier modulation on the trending/chop boolean.

    is_trending=True means the agent predicted a directional trend day.
    actual_is_trending mirrors that on the measured side.
    """
    pred_trending = prediction.is_trending
    act_trending = outcome.actual_is_trending
    conf = max(0.0, min(1.0, prediction.chop_confidence))

    base = 1.0 if pred_trending == act_trending else 0.0
    return 0.5 + (conf - 0.5) * (2.0 * base - 1.0)


def _levels_score(prediction: DailyPrediction, outcome: PredictionOutcome) -> float:
    """
    Score each level based on touch_probability calibration.

    For each predicted level:
      - If touched:     score = touch_probability  (reward high-confidence correct)
      - If not touched: score = 1 - touch_probability  (reward saying 'unlikely')

    Mean across levels. Defaults to 0.5 (no-information) if no levels predicted.
    Applies a soft penalty if fewer than MIN_LEVELS_FOR_FULL_SCORE are predicted.
    """
    if not prediction.key_levels or not outcome.levels_touched:
        return 0.5

    level_probs = {
        str(lv.get("label") or f"{lv.get('price')}"): float(lv.get("touch_probability") or 0.5)
        for lv in prediction.key_levels
    }

    per_level_scores = []
    for lo in outcome.levels_touched:
        prob = level_probs.get(lo.label, 0.5)
        prob = max(0.0, min(1.0, prob))
        per_level_scores.append(prob if lo.was_touched else 1.0 - prob)

    if not per_level_scores:
        return 0.5

    raw = sum(per_level_scores) / len(per_level_scores)

    # Soft penalty for very few levels
    if len(prediction.key_levels) < MIN_LEVELS_FOR_FULL_SCORE:
        raw *= LEVELS_UNDERCOUNT_PENALTY

    return raw


def _breakout_score(prediction: DailyPrediction, outcome: PredictionOutcome) -> float:
    """
    Brier score (proper scoring rule) for breakout occurrence, with a direction
    bonus if the direction was also correct.

    Brier inversion: score = 1 - (p - actual)²
    This ranges 0.75 (random 0.5 prediction) to 1.0 (confident correct).
    """
    p = max(0.0, min(1.0, prediction.breakout_probability))
    actual = 1.0 if outcome.breakout_occurred else 0.0
    score = 1.0 - (p - actual) ** 2

    # Direction bonus (capped at 1.0)
    if (
        outcome.breakout_occurred
        and prediction.breakout_direction not in ("none", "either")
        and prediction.breakout_direction == outcome.breakout_direction_actual
    ):
        score = min(1.0, score + 0.05)

    return score
