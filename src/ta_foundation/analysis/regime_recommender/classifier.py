from __future__ import annotations

from typing import Any, Dict

from .models import RegimeClassification


def classify_regime(features: Dict[str, Any], cfg: Dict[str, Any] | None = None) -> Dict[str, Any]:
    cfg = cfg or {}

    fv = (features or {}).get("feature_values", {}) or {}
    warnings = (features or {}).get("warnings", []) or []

    slope240 = float(fv.get("tf240m_trend_slope", 0.0) or 0.0)
    atr240 = float(fv.get("tf240m_atr", 0.0) or 0.0)
    strength60 = float(fv.get("tf60m_trend_strength", 0.0) or 0.0)
    compression15 = float(fv.get("tf15m_compression_ratio", 1.0) or 1.0)
    agreement = float(fv.get("cross_tf_agreement", 0.0) or 0.0)

    strength_threshold = float(cfg.get("trend_strength_threshold", 0.30))

    # Flat band: |slope240| must clear this before the market counts as trending.
    #
    # `trend_flat_threshold` is an absolute price-slope floor and defaults to 0.0,
    # which makes `range` the measure-zero set {slope == 0.0}. That went unnoticed
    # while the 4h lookback was too short for its own EMA(50): the slope fell back
    # to exactly 0.0 on every bar, so the classifier answered `range` 100% of the
    # time. Widening the window (see features.py) fixed the slope but flipped the
    # same defect the other way -- `range` then never occurred at all.
    #
    # `trend_flat_atr_frac` supplies a real band, expressed as a fraction of the
    # 4h ATR so one default holds across instruments. Measured over 360 asof points
    # on NQ/ES/GC/RTY/MNQ/NG, |slope240|/atr240 has a per-instrument median of
    # 0.04-0.22; 0.10 puts ~35% of observations in `range`.
    #
    # The two thresholds combine as a max(), so the band is never narrower than an
    # explicitly configured absolute floor. Setting trend_flat_atr_frac=0.0
    # restores the previous behaviour exactly.
    trend_flat_threshold = float(cfg.get("trend_flat_threshold", 0.0))
    trend_flat_atr_frac = float(cfg.get("trend_flat_atr_frac", 0.10))

    flat_band = trend_flat_threshold
    if atr240 > 0.0 and trend_flat_atr_frac > 0.0:
        flat_band = max(flat_band, trend_flat_atr_frac * atr240)

    if slope240 > flat_band:
        primary = "trend_up"
    elif slope240 < -flat_band:
        primary = "trend_down"
    else:
        primary = "range"

    secondary = []
    if compression15 > 1.15:
        secondary.append("vol_expanding")
    elif compression15 < 0.90:
        secondary.append("vol_compressed")

    if strength60 >= strength_threshold:
        secondary.append("trend_strong")
    else:
        secondary.append("trend_weak")

    regime_id = primary if not secondary else f"{primary}_{'_'.join(secondary)}"

    # confidence decomposition (deterministic, inspectable)
    data_quality = 1.0 if not warnings else max(0.5, 1.0 - 0.1 * len(warnings))
    trend_certainty = min(1.0, abs(slope240) / (abs(slope240) + 1.0))
    strength_certainty = min(1.0, strength60 / (strength60 + 1.0)) if strength60 >= 0 else 0.0
    agreement_certainty = min(1.0, max(0.0, agreement))

    confidence = data_quality * (0.40 * trend_certainty + 0.30 * strength_certainty + 0.30 * agreement_certainty)

    out = RegimeClassification(
        regime_id=regime_id,
        primary=primary,
        secondary=secondary,
        confidence=float(max(0.0, min(1.0, confidence))),
        feature_influences=[
            {"feature": "tf240m_trend_slope", "value": slope240, "impact": 0.40},
            {"feature": "tf60m_trend_strength", "value": strength60, "impact": 0.30},
            {"feature": "cross_tf_agreement", "value": agreement, "impact": 0.30},
        ],
        diagnostics={
            "data_quality": data_quality,
            "trend_certainty": trend_certainty,
            "strength_certainty": strength_certainty,
            "agreement_certainty": agreement_certainty,
            "warnings_count": len(warnings),
            # Surfaced so a `range` verdict can be read as "slope inside the band"
            # rather than "slope was zero" -- the ambiguity that hid the 4h bug.
            "flat_band": float(flat_band),
            "slope_atr_ratio": float(abs(slope240) / atr240) if atr240 > 0.0 else None,
        },
    )

    return out.as_dict()
