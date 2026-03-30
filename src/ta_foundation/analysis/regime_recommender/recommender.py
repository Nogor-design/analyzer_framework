from __future__ import annotations

from typing import Any, Dict


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return float(default)


def _clamp_numeric(value: Any, fallback: Any, p: Dict[str, Any]) -> Any:
    if not isinstance(value, (int, float)):
        return fallback
    lo = p.get("min")
    hi = p.get("max")
    out = value
    if isinstance(lo, (int, float)):
        out = max(out, lo)
    if isinstance(hi, (int, float)):
        out = min(out, hi)
    return out


def recommend_parameters(
    strategy_profile: Dict[str, Any],
    regime: Dict[str, Any],
    features: Dict[str, Any],
    cfg: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    cfg = cfg or {}

    defaults = (strategy_profile or {}).get("defaults", {}) or {}
    params_meta = (strategy_profile or {}).get("parameters", {}) or {}
    confidence = _safe_float((regime or {}).get("confidence"), default=0.0)

    min_conf = _safe_float(cfg.get("min_confidence", 0.55), default=0.55)
    if confidence < min_conf:
        return {
            "decision": "NO_TRADE",
            "confidence": confidence,
            "baseline_params": defaults,
            "recommended_params": defaults,
            "reasons": [f"confidence_below_threshold:{confidence:.3f}<{min_conf:.3f}"],
            "parameter_reasons": [],
        }

    recommended = dict(defaults)
    fv = (features or {}).get("feature_values", {}) or {}
    primary = (regime or {}).get("primary", "range")
    secondary = set((regime or {}).get("secondary", []) or [])

    reasons: list[str] = []
    parameter_reasons: list[Dict[str, Any]] = []

    def _maybe_adjust(name: str, new_val: Any, because: list[str]) -> None:
        if name not in recommended:
            return
        old = recommended.get(name)
        meta = params_meta.get(name, {})
        adj = _clamp_numeric(new_val, old, meta)
        if adj != old:
            recommended[name] = adj
            parameter_reasons.append(
                {
                    "name": name,
                    "baseline": old,
                    "recommended": adj,
                    "because": because,
                }
            )

    # trend-period tuning
    if primary == "trend_up" or primary == "trend_down":
        _maybe_adjust("averageFast", _safe_float(defaults.get("averageFast", 0)) * 0.9, ["trend_primary"])
        _maybe_adjust("averageSlow", _safe_float(defaults.get("averageSlow", 0)) * 0.95, ["trend_primary"])
        reasons.append("trend_regime_detected")
    else:
        _maybe_adjust("averageFast", _safe_float(defaults.get("averageFast", 0)) * 1.1, ["range_primary"])
        reasons.append("range_regime_detected")

    # volatility-aware stop sizing
    atr60 = _safe_float(fv.get("tf60m_atr"), 0.0)
    atr15 = _safe_float(fv.get("tf15m_atr"), 0.0)
    compression = _safe_float(fv.get("tf15m_compression_ratio"), 1.0)

    if "vol_expanding" in secondary or compression > 1.15:
        _maybe_adjust("MaxStop", _safe_float(defaults.get("MaxStop", 0)) * 1.15, ["vol_expanding", f"atr60={atr60:.3f}"])
        reasons.append("volatility_expansion")
    elif "vol_compressed" in secondary:
        _maybe_adjust("MaxStop", _safe_float(defaults.get("MaxStop", 0)) * 0.90, ["vol_compressed", f"atr15={atr15:.3f}"])
        reasons.append("volatility_compression")

    # round integer fields that should remain integer
    for key in ["averageFast", "averageSlow", "averageTrend", "MaxStop", "Contracts", "StartTimeH", "StartTimeM", "DurationTimeH", "DurationTimeM"]:
        if key in recommended and isinstance(recommended[key], float):
            recommended[key] = int(round(recommended[key]))

    changed = {k: v for k, v in recommended.items() if defaults.get(k) != v}
    decision = "RECOMMEND_PARAMS" if changed else "RECOMMEND_BASELINE"

    return {
        "decision": decision,
        "confidence": confidence,
        "baseline_params": defaults,
        "recommended_params": recommended,
        "changed_params": changed,
        "reasons": reasons,
        "parameter_reasons": parameter_reasons,
    }
