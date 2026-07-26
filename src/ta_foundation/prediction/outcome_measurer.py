from __future__ import annotations

from typing import List

import pandas as pd

from .models import DailyPrediction, LevelOutcome, PredictionOutcome

# Fraction of ATR used as the touch zone radius around a predicted level
DEFAULT_TOUCH_TOLERANCE = 0.25

# Efficiency ratio threshold: above this = trending day
TREND_ER_THRESHOLD = 0.35

# ATR multiplier for net displacement to qualify as directional (vs neutral)
TREND_DIRECTION_ATR_THRESHOLD = 0.30

# Fraction of session bars defining the "early range" for breakout detection
EARLY_RANGE_FRACTION = 0.30

# Net displacement beyond early range needed to confirm breakout (ATR fraction)
BREAKOUT_EXTENSION_ATR = 0.15


def measure_outcome(
    prediction: DailyPrediction,
    bars: pd.DataFrame,
    prior_atr: float,
    touch_tolerance: float = DEFAULT_TOUCH_TOLERANCE,
) -> PredictionOutcome:
    """
    Measure what actually happened on prediction.target_date.

    bars: intraday OHLCV bars for the target day (columns: open, high, low, close)
    prior_atr: 14-period ATR from the day before target_date (used for thresholds)
    touch_tolerance: fraction of prior_atr used as the touch zone radius

    Returns a PredictionOutcome with score fields zeroed (call score_prediction to fill them).
    """
    outcome = PredictionOutcome(
        prediction_id=prediction.prediction_id,
        target_date=prediction.target_date,
        instrument=prediction.instrument,
    )

    # Carry forward contextual flags from the prediction's context snapshot
    snap = prediction.context_snapshot or {}
    outcome.regime_label = str(snap.get("regime_label") or "unknown")
    outcome.had_high_impact_event = bool(snap.get("had_high_impact_event", False))

    if bars is None or bars.empty:
        outcome.regime_label = str(snap.get("regime_label") or "unknown")
        return outcome

    # Guard: ATR fallback when prior_atr is unavailable
    if prior_atr <= 0.0:
        day_range = float(bars["high"].max() - bars["low"].min())
        prior_atr = day_range * 0.5 if day_range > 0 else 1.0
        outcome.prior_atr = -1.0  # sentinel: fallback was used
    else:
        outcome.prior_atr = prior_atr

    # ------------------------------------------------------------------
    # Actual OHLC
    # ------------------------------------------------------------------
    outcome.actual_open = float(bars.iloc[0]["open"])
    outcome.actual_high = float(bars["high"].max())
    outcome.actual_low = float(bars["low"].min())
    outcome.actual_close = float(bars.iloc[-1]["close"])
    outcome.actual_range = outcome.actual_high - outcome.actual_low

    # ------------------------------------------------------------------
    # Trend direction
    # ------------------------------------------------------------------
    net_displacement = outcome.actual_close - outcome.actual_open
    threshold = TREND_DIRECTION_ATR_THRESHOLD * prior_atr
    if net_displacement > threshold:
        outcome.actual_direction = "bullish"
    elif net_displacement < -threshold:
        outcome.actual_direction = "bearish"
    else:
        outcome.actual_direction = "neutral"

    # ------------------------------------------------------------------
    # Efficiency ratio (chop measure)
    # ------------------------------------------------------------------
    closes = bars["close"].values
    if len(closes) >= 2:
        diffs = [abs(float(closes[i]) - float(closes[i - 1])) for i in range(1, len(closes))]
        total_path = sum(diffs)
        if total_path > 0:
            outcome.efficiency_ratio = abs(net_displacement) / total_path
        else:
            outcome.efficiency_ratio = 0.0
    else:
        outcome.efficiency_ratio = 0.0

    outcome.actual_is_trending = outcome.efficiency_ratio >= TREND_ER_THRESHOLD

    # ------------------------------------------------------------------
    # Level touch detection
    # ------------------------------------------------------------------
    outcome.levels_touched = _measure_level_touches(
        prediction.key_levels, bars, prior_atr, touch_tolerance
    )

    # ------------------------------------------------------------------
    # Breakout detection (ORB-style: early range → extension)
    # ------------------------------------------------------------------
    n_early = max(1, int(len(bars) * EARLY_RANGE_FRACTION))
    early_bars = bars.iloc[:n_early]
    early_high = float(early_bars["high"].max())
    early_low = float(early_bars["low"].min())
    extension = BREAKOUT_EXTENSION_ATR * prior_atr

    remaining = bars.iloc[n_early:]
    breakout_up = False
    breakout_down = False
    if not remaining.empty:
        breakout_up = bool((remaining["close"] > early_high + extension).any())
        breakout_down = bool((remaining["close"] < early_low - extension).any())

    if breakout_up and not breakout_down:
        outcome.breakout_occurred = True
        outcome.breakout_direction_actual = "up"
        magnitude_pts = float(remaining["close"].max()) - (early_high + extension)
        outcome.breakout_magnitude = magnitude_pts / prior_atr if prior_atr > 0 else 0.0
    elif breakout_down and not breakout_up:
        outcome.breakout_occurred = True
        outcome.breakout_direction_actual = "down"
        magnitude_pts = (early_low - extension) - float(remaining["close"].min())
        outcome.breakout_magnitude = magnitude_pts / prior_atr if prior_atr > 0 else 0.0
    elif breakout_up and breakout_down:
        # Both directions — still a breakout, pick larger
        outcome.breakout_occurred = True
        mag_up = float(remaining["close"].max()) - (early_high + extension)
        mag_down = (early_low - extension) - float(remaining["close"].min())
        if mag_up >= mag_down:
            outcome.breakout_direction_actual = "up"
            outcome.breakout_magnitude = mag_up / prior_atr
        else:
            outcome.breakout_direction_actual = "down"
            outcome.breakout_magnitude = mag_down / prior_atr
    else:
        outcome.breakout_occurred = False
        outcome.breakout_direction_actual = "none"
        outcome.breakout_magnitude = 0.0

    return outcome


def _measure_level_touches(
    key_levels: list,
    bars: pd.DataFrame,
    prior_atr: float,
    touch_tolerance: float,
) -> List[LevelOutcome]:
    touch_zone = touch_tolerance * prior_atr
    results = []
    for level in key_levels:
        price = float(level.get("price") or 0.0)
        label = str(level.get("label") or "")
        if price == 0.0:
            results.append(LevelOutcome(price=price, label=label, was_touched=False))
            continue

        lo_bound = price - touch_zone
        hi_bound = price + touch_zone

        touched = False
        touch_price: float | None = None
        touch_idx: int | None = None

        for idx, row in enumerate(bars.itertuples(index=False)):
            bar_low = float(getattr(row, "low", 0))
            bar_high = float(getattr(row, "high", 0))
            if bar_low <= hi_bound and bar_high >= lo_bound:
                touched = True
                touch_idx = idx
                # Record whichever of high/low was closer to the predicted level
                touch_price = bar_high if abs(bar_high - price) <= abs(bar_low - price) else bar_low
                break

        results.append(LevelOutcome(
            price=price,
            label=label,
            was_touched=touched,
            touch_price=touch_price,
            touch_bar_index=touch_idx,
        ))

    return results
