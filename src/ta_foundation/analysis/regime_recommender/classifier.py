from __future__ import annotations

from typing import Any, Dict

from .models import RegimeClassification


def classify_regime(features: Dict[str, Any], cfg: Dict[str, Any] | None = None) -> Dict[str, Any]:
    cfg = cfg or {}

    fv = (features or {}).get("feature_values", {}) or {}
    warnings = (features or {}).get("warnings", []) or []

    slope240 = float(fv.get("tf240m_trend_slope", 0.0) or 0.0)
    strength60 = float(fv.get("tf60m_trend_strength", 0.0) or 0.0)
    compression15 = float(fv.get("tf15m_compression_ratio", 1.0) or 1.0)
    agreement = float(fv.get("cross_tf_agreement", 0.0) or 0.0)

    trend_flat_threshold = float(cfg.get("trend_flat_threshold", 0.0))
    strength_threshold = float(cfg.get("trend_strength_threshold", 0.30))

    if slope240 > trend_flat_threshold:
        primary = "trend_up"
    elif slope240 < -trend_flat_threshold:
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
        },
    )

    return out.as_dict()
