from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import math


@dataclass(frozen=True)
class RatioRating:
    value: float
    label: str
    css_class: str
    rule: str


def _to_float_or_none(x: Any) -> float | None:
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(v) or math.isinf(v):
        return None
    return v


def classify_mfe_etd(value: float) -> RatioRating:
    """
    MFE/ETD >5.0: Excellent
    3.0-5.0: Good
    1.5-3.0: Fair
    <1.5: Poor
    """
    rule = "MFE/ETD: >5.0 Excellent; 3.0–5.0 Good; 1.5–3.0 Fair; <1.5 Poor"
    if value > 5.0:
        return RatioRating(value=value, label="Excellent", css_class="ta-rating--excellent", rule=rule)
    if value >= 3.0:
        return RatioRating(value=value, label="Good", css_class="ta-rating--good", rule=rule)
    if value >= 1.5:
        return RatioRating(value=value, label="Fair", css_class="ta-rating--fair", rule=rule)
    return RatioRating(value=value, label="Poor", css_class="ta-rating--poor", rule=rule)


def classify_mae_mfe(value: float) -> RatioRating:
    """
    MAE/MFE <0.3: Excellent
    0.3-.6: Good
    0.6-.9: Fair
    >1.0: Poor

    Engineering assumption: 0.9–1.0 is treated as Fair to avoid an unclassified gap.
    """
    rule = "MAE/MFE: <0.3 Excellent; 0.3–0.6 Good; 0.6–0.9 Fair; >1.0 Poor"
    if value < 0.3:
        demonstrate = "ta-rating--excellent"
        return RatioRating(value=value, label="Excellent", css_class=demonstrate, rule=rule)
    if value < 0.6:
        return RatioRating(value=value, label="Good", css_class="ta-rating--good", rule=rule)
    if value < 0.9:
        return RatioRating(value=value, label="Fair", css_class="ta-rating--fair", rule=rule)
    if value > 1.0:
        return RatioRating(value=value, label="Poor", css_class="ta-rating--poor", rule=rule)
    return RatioRating(value=value, label="Fair", css_class="ta-rating--fair", rule=rule)


def derive_exec_ratio_ratings(pkg: Any) -> None:
    """
    Reads ratios from pkg.metadata["derived"] (if present) and writes:
      pkg.metadata["derived"]["avg_ratios"]["mfe_etd"] = {value,label,css_class,rule}
      pkg.metadata["derived"]["avg_ratios"]["mae_mfe"] = {value,label,css_class,rule}
    """
    md = getattr(pkg, "metadata", None)
    if not isinstance(md, dict):
        return

    derived = md.setdefault("derived", {})
    if not isinstance(derived, dict):
        return

    mfe_etd = _to_float_or_none(derived.get("mfe_etd_ratio"))
    mae_mfe = _to_float_or_none(derived.get("mae_mfe_ratio"))

    avg_ratios = derived.setdefault("avg_ratios", {})
    if not isinstance(avg_ratios, dict):
        return

    if mfe_etd is not None:
        r = classify_mfe_etd(mfe_etd)
        avg_ratios["mfe_etd"] = {
            "value": r.value,
            "label": r.label,
            "css_class": r.css_class,
            "rule": r.rule,
        }

    if mae_mfe is not None:
        r = classify_mae_mfe(mae_mfe)
        avg_ratios["mae_mfe"] = {
            "value": r.value,
            "label": r.label,
            "css_class": r.css_class,
            "rule": r.rule,
        }
