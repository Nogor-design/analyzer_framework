from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class ConfidenceDecomposition:
    """
    Explainable confidence model for parameter recommendations.

    Each component contributes to ``composite`` (0–1 weighted blend).
    Individual components are 0–1 floats except ``sensitivity_class`` (string).
    A component value of ``None`` means it was not available at recommendation time.
    """
    regime_classifier: float = 0.0          # from upstream regime classifier
    cv_stability: Optional[float] = None    # from oos_stats fold sign-consistency
    mc_survival_rate: Optional[float] = None  # fraction of MC simulations that pass
    sensitivity_class: Optional[str] = None  # fragile / moderate / robust
    audit_confirmation_rate: Optional[float] = None  # from trade_pattern_audit
    composite: float = 0.0                  # final blended confidence (replaces bare float)

    # Weights used for the composite blend (stored for transparency)
    weights: Dict[str, float] = field(default_factory=lambda: {
        "regime_classifier": 0.35,
        "cv_stability": 0.25,
        "mc_survival_rate": 0.20,
        "sensitivity_class": 0.10,
        "audit_confirmation_rate": 0.10,
    })

    def to_dict(self) -> Dict[str, Any]:
        return {
            "regime_classifier": self.regime_classifier,
            "cv_stability": self.cv_stability,
            "mc_survival_rate": self.mc_survival_rate,
            "sensitivity_class": self.sensitivity_class,
            "audit_confirmation_rate": self.audit_confirmation_rate,
            "composite": self.composite,
            "weights": self.weights,
        }


_SENSITIVITY_SCORE: Dict[str, float] = {
    "robust": 1.0,
    "moderate": 0.6,
    "fragile": 0.2,
}


def build_confidence_decomposition(
    regime_classifier_confidence: float,
    cv_stability: Optional[float] = None,
    mc_survival_rate: Optional[float] = None,
    sensitivity_class: Optional[str] = None,
    audit_confirmation_rate: Optional[float] = None,
) -> ConfidenceDecomposition:
    """
    Build a ``ConfidenceDecomposition`` from individual component values.
    Missing components fall back to ``regime_classifier_confidence`` so that
    callers who only have the basic classifier output get a valid composite.
    """
    base = float(regime_classifier_confidence)

    weights = {
        "regime_classifier": 0.35,
        "cv_stability": 0.25,
        "mc_survival_rate": 0.20,
        "sensitivity_class": 0.10,
        "audit_confirmation_rate": 0.10,
    }

    components: Dict[str, float] = {"regime_classifier": base}

    if cv_stability is not None:
        components["cv_stability"] = float(cv_stability)
    if mc_survival_rate is not None:
        components["mc_survival_rate"] = float(mc_survival_rate)
    if sensitivity_class is not None:
        components["sensitivity_class"] = _SENSITIVITY_SCORE.get(
            str(sensitivity_class).lower(), 0.5
        )
    if audit_confirmation_rate is not None:
        components["audit_confirmation_rate"] = float(audit_confirmation_rate)

    # Weighted blend — components that are absent redistribute their weight to regime_classifier
    total_w = 0.0
    composite = 0.0
    for key, w in weights.items():
        if key in components:
            composite += w * components[key]
            total_w += w

    # Normalise if not all components present
    if total_w > 0:
        composite = composite / total_w
    else:
        composite = base

    return ConfidenceDecomposition(
        regime_classifier=base,
        cv_stability=cv_stability,
        mc_survival_rate=mc_survival_rate,
        sensitivity_class=sensitivity_class,
        audit_confirmation_rate=audit_confirmation_rate,
        composite=float(min(max(composite, 0.0), 1.0)),
        weights=weights,
    )


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
    confidence_decomposition: Optional[ConfidenceDecomposition] = None,
) -> Dict[str, Any]:
    """
    Recommend strategy parameters given regime, features, and optional
    pre-built ``ConfidenceDecomposition``.

    If ``confidence_decomposition`` is provided its ``composite`` value is
    used for gating; otherwise the bare ``regime["confidence"]`` float is used
    (backwards-compatible path).
    """
    cfg = cfg or {}

    defaults = (strategy_profile or {}).get("defaults", {}) or {}
    params_meta = (strategy_profile or {}).get("parameters", {}) or {}

    # Resolve effective confidence
    if confidence_decomposition is not None:
        confidence = confidence_decomposition.composite
    else:
        confidence = _safe_float((regime or {}).get("confidence"), default=0.0)

    min_conf = _safe_float(cfg.get("min_confidence", 0.55), default=0.55)
    if confidence < min_conf:
        result = {
            "decision": "NO_TRADE",
            "confidence": confidence,
            "baseline_params": defaults,
            "recommended_params": defaults,
            "reasons": [f"confidence_below_threshold:{confidence:.3f}<{min_conf:.3f}"],
            "parameter_reasons": [],
        }
        if confidence_decomposition is not None:
            result["confidence_decomposition"] = confidence_decomposition.to_dict()
        return result

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

    result = {
        "decision": decision,
        "confidence": confidence,
        "baseline_params": defaults,
        "recommended_params": recommended,
        "changed_params": changed,
        "reasons": reasons,
        "parameter_reasons": parameter_reasons,
    }
    if confidence_decomposition is not None:
        result["confidence_decomposition"] = confidence_decomposition.to_dict()
    return result
