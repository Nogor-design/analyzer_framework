"""
Measure the realized outcome of a CandleHorizonPrediction.

Reads only bars *strictly after* the asof index — it never peeks at the asof
bar's own future fields, and never re-uses bars from before asof. This keeps
the function leakage-safe for walk-forward replay.
"""
from __future__ import annotations

from typing import Optional

import pandas as pd

from .horizon_models import CandleHorizonOutcome, CandleHorizonPrediction

# Net displacement beyond ATR fraction needed to call a horizon "directional".
# Match the existing daily outcome_measurer.TREND_DIRECTION_ATR_THRESHOLD so
# the horizon and daily systems agree on what "neutral" means.
NEUTRAL_ATR_THRESHOLD = 0.30


def measure_horizon_outcome(
    bars: pd.DataFrame,
    asof_idx: int,
    horizon_candles: int,
    prior_atr: float,
    upside_threshold_points: float = 0.0,
    downside_threshold_points: float = 0.0,
    prediction: Optional[CandleHorizonPrediction] = None,
) -> CandleHorizonOutcome:
    """
    Compute the realized outcome over the next `horizon_candles` bars after
    `asof_idx`.

    Args:
        bars: tz-aware OHLCV DataFrame (must contain `dt`, `open`, `high`,
            `low`, `close` columns; index does not matter).
        asof_idx: integer position of the bar at "now" in `bars`. The
            measurement uses bars[asof_idx+1 : asof_idx+1+horizon_candles].
        horizon_candles: positive int N.
        prior_atr: ATR computed strictly from bars at or before asof. Used to
            normalize returns and to set the "neutral" threshold. Must be
            positive; if not, a fallback range is used.
        upside_threshold_points / downside_threshold_points: optional
            absolute-points thresholds measured from the asof close. If both
            are 0.0, threshold flags remain False.
        prediction: optional source CandleHorizonPrediction; if provided, the
            outcome inherits its prediction_id, agent_id, instrument, contract,
            timeframe, and asof_timestamp. Otherwise these fields stay blank
            and the caller should populate them.

    Returns:
        CandleHorizonOutcome with the realized fields populated. The score
        fields stay zero — call horizon_scorer.score_horizon_prediction to
        fill those.
    """
    if bars is None or len(bars) == 0:
        raise ValueError("measure_horizon_outcome: bars is empty")
    if horizon_candles <= 0:
        raise ValueError(f"horizon_candles must be > 0, got {horizon_candles}")
    if asof_idx < 0 or asof_idx >= len(bars):
        raise ValueError(
            f"asof_idx={asof_idx} out of range for bars of length {len(bars)}"
        )

    outcome = CandleHorizonOutcome(
        horizon_candles=int(horizon_candles),
        asof_bar_index=int(asof_idx),
        upside_threshold_points=float(upside_threshold_points),
        downside_threshold_points=float(downside_threshold_points),
    )

    if prediction is not None:
        outcome.prediction_id = prediction.prediction_id
        outcome.agent_id = prediction.agent_id
        outcome.instrument = prediction.instrument
        outcome.contract = prediction.contract
        outcome.timeframe = prediction.timeframe
        outcome.asof_timestamp = prediction.asof_timestamp

    asof_close = float(bars.iloc[asof_idx]["close"])

    end_idx = min(asof_idx + horizon_candles, len(bars) - 1)
    horizon = bars.iloc[asof_idx + 1 : end_idx + 1]

    if horizon.empty:
        # No future bars available — return outcome with zero-fields and let
        # the caller decide whether to discard.
        outcome.horizon_bars_observed = 0
        outcome.actual_open = asof_close
        outcome.actual_close = asof_close
        outcome.actual_high = asof_close
        outcome.actual_low = asof_close
        return outcome

    outcome.horizon_bars_observed = int(len(horizon))
    outcome.actual_open = float(horizon.iloc[0]["open"])
    outcome.actual_close = float(horizon.iloc[-1]["close"])
    outcome.actual_high = float(horizon["high"].max())
    outcome.actual_low = float(horizon["low"].min())

    if "dt" in horizon.columns:
        outcome.horizon_end_timestamp = pd.Timestamp(horizon.iloc[-1]["dt"]).isoformat()

    # Net return (close-to-close from asof_close → final horizon close)
    net = outcome.actual_close - asof_close
    outcome.actual_return_points = float(net)

    # ATR fallback
    if prior_atr <= 0.0:
        rng = max(outcome.actual_high - outcome.actual_low, 1e-9)
        prior_atr = rng * 0.5
        outcome.prior_atr = -1.0   # sentinel: fallback ATR was used
    else:
        outcome.prior_atr = float(prior_atr)

    outcome.actual_return_atr = float(net / prior_atr) if prior_atr > 0 else 0.0

    threshold = NEUTRAL_ATR_THRESHOLD * prior_atr
    if net > threshold:
        outcome.actual_direction = "bullish"
    elif net < -threshold:
        outcome.actual_direction = "bearish"
    else:
        outcome.actual_direction = "neutral"

    outcome.actual_mfe_points = float(max(0.0, outcome.actual_high - asof_close))
    outcome.actual_mae_points = float(max(0.0, asof_close - outcome.actual_low))

    # Efficiency ratio over the horizon (close-to-close path)
    closes = [asof_close] + horizon["close"].astype(float).tolist()
    total_path = sum(abs(closes[i] - closes[i - 1]) for i in range(1, len(closes)))
    if total_path > 0:
        outcome.actual_efficiency_ratio = abs(net) / total_path
    else:
        outcome.actual_efficiency_ratio = 0.0

    # Threshold first-hit detection
    if upside_threshold_points > 0.0 or downside_threshold_points > 0.0:
        outcome.threshold_hit_order, outcome.upside_threshold_hit, outcome.downside_threshold_hit = (
            _detect_threshold_first_hit(
                horizon,
                asof_close,
                upside_threshold_points,
                downside_threshold_points,
            )
        )
    else:
        outcome.threshold_hit_order = "neither"

    return outcome


def _detect_threshold_first_hit(
    horizon: pd.DataFrame,
    asof_close: float,
    upside_pts: float,
    downside_pts: float,
) -> tuple[str, bool, bool]:
    """
    Walk the horizon bar-by-bar and return which threshold (if any) was hit
    first.

    A bar that touches both thresholds in the same bar is reported as
    'both_same_bar' — bar resolution does not let us decide which came first.
    Both flags become True in that case.

    Threshold of 0.0 disables that side.
    """
    upside_level = asof_close + upside_pts if upside_pts > 0.0 else None
    downside_level = asof_close - downside_pts if downside_pts > 0.0 else None

    if upside_level is None and downside_level is None:
        return "neither", False, False

    upside_hit = False
    downside_hit = False

    for _, row in horizon.iterrows():
        bar_high = float(row["high"])
        bar_low = float(row["low"])

        bar_up = upside_level is not None and bar_high >= upside_level
        bar_down = downside_level is not None and bar_low <= downside_level

        if bar_up and bar_down:
            return "both_same_bar", True, True
        if bar_up:
            return "upside_first", True, False
        if bar_down:
            return "downside_first", False, True

    return "neither", False, False
