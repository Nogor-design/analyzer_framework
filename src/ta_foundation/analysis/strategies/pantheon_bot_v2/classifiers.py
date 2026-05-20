"""
Python mirrors of PantheonBotV2's C# RegimeClassifier methods.

These exist so that test_classifier_parity can confirm the Python regime
labels match what NinjaTrader emits at runtime. The existing
market_regime_store helpers operate on a trade frame and bucket volatility
with qcut — they are NOT a byte-for-byte mirror of PantheonBotV2 and
intentionally are not reused here.

Source: src/ta_foundation/strategies/PantheonBotV2/PantheonBotV2.cs
        RegimeClassifier.ClassifyTrend / ClassifyVwap /
        ClassifyVolatilityFromPercentile
"""

from __future__ import annotations

import math
from typing import Optional


TrendLabel = str  # "up" | "down" | "flat"
VwapLabel = str   # "above" | "below" | "near"
VolLabel = str    # "low" | "mid" | "high"


def classify_trend(slope: Optional[float], flat_threshold: float) -> TrendLabel:
    if slope is None or (isinstance(slope, float) and math.isnan(slope)):
        return "flat"
    if slope > flat_threshold:
        return "up"
    if slope < -flat_threshold:
        return "down"
    return "flat"


def classify_vwap(dist_atr: Optional[float], near_threshold: float) -> VwapLabel:
    if dist_atr is None or (isinstance(dist_atr, float) and math.isnan(dist_atr)):
        return "near"
    if dist_atr > near_threshold:
        return "above"
    if dist_atr < -near_threshold:
        return "below"
    return "near"


def classify_volatility_from_percentile(
    percentile_rank: Optional[float],
    low_threshold: float,
    high_threshold: float,
) -> VolLabel:
    # Mirrors C# ordering: low check first, so when thresholds overlap the
    # tie goes to "low". Matches PantheonBotV2.cs:128-134.
    if percentile_rank is None or (isinstance(percentile_rank, float) and math.isnan(percentile_rank)):
        return "mid"
    if percentile_rank <= low_threshold:
        return "low"
    if percentile_rank >= high_threshold:
        return "high"
    return "mid"
