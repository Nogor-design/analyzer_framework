from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from ta_foundation.analysis.regime_recommender.features import build_multitf_features
from ta_foundation.analysis.regime_recommender.classifier import classify_regime
from ta_foundation.analysis.regime_recommender.recommender import (
    recommend_parameters,
    build_confidence_decomposition,
)
from ta_foundation.analysis.regime_recommender.template_export import generate_session_templates
from ta_foundation.analysis.regime_recommender.outcomes import attach_outcome_snapshot
from ta_foundation.analysis.regime_recommender.storage import persist_record_jsonl
from ta_foundation.analysis.strategy_metadata import build_strategy_profile


def _ensure_derived(pkg) -> Dict[str, Any]:
    md = getattr(pkg, "metadata", None)
    if md is None:
        pkg.metadata = {}
        md = pkg.metadata
    if md.get("derived") is None:
        md["derived"] = {}
    return md["derived"]


def compute_and_attach_regime_recommendation(
    pkg,
    market,
    options: Dict[str, Any] | None = None,
) -> None:
    options = options or {}

    derived = _ensure_derived(pkg)
    out: Dict[str, Any] = {
        "version": "rr_v1",
        "enabled": True,
        "warnings": [],
    }

    strategy_id = str(options.get("strategy_id") or "").strip() or str(pkg.run_id)
    instrument = str(options.get("instrument") or "").strip() or "NQ"
    contract = str(options.get("contract") or "").strip() or ""

    if market is None:
        out["enabled"] = False
        out["warnings"].append("market_data_unavailable")
        derived["regime_recommender"] = out
        return

    if not contract:
        out["enabled"] = False
        out["warnings"].append("contract_missing")
        derived["regime_recommender"] = out
        return

    strategy_profile = build_strategy_profile(strategy_id)
    features = build_multitf_features(market, instrument=instrument, contract=contract)
    regime = classify_regime(features=features, cfg=(options.get("classifier") or {}))

    # Collect available evidence components for confidence decomposition
    pkg_assets = getattr(pkg, "assets", {}) or {}
    pe_store = pkg_assets.get("pattern_engine") or {}
    sd_assets = pkg_assets.get("strategy_discovery") or {}
    audit_store = pkg_assets.get("trade_pattern_audit") or {}

    # CV stability: fold sign-consistency from oos_stats
    cv_stability = None
    oos_stats = pe_store.get("oos_stats")
    if isinstance(oos_stats, dict):
        cv_stability = oos_stats.get("sign_consistency") or oos_stats.get("fold_sign_consistency")
    elif hasattr(oos_stats, "get"):
        cv_stability = oos_stats.get("sign_consistency")

    # MC survival rate: fraction of MC simulations that pass
    mc_survival_rate = None
    mc_summary = pe_store.get("mc_summary")
    if isinstance(mc_summary, dict):
        mc_survival_rate = mc_summary.get("survival_rate") or mc_summary.get("pass_rate")

    # Parameter sensitivity class from strategy_discovery metadata
    sensitivity_class = None
    sd_derived = derived.get("strategy_discovery") or {}
    ps_result = sd_derived.get("parameter_sensitivity") or {}
    if isinstance(ps_result, dict):
        sensitivity_class = ps_result.get("overall_class") or ps_result.get("sensitivity_class")

    # Audit confirmation rate: per-pattern audit from trade_pattern_audit
    audit_confirmation_rate = None
    if isinstance(audit_store, dict):
        audit_df = audit_store.get("audit_df")
        if audit_df is not None and hasattr(audit_df, "__len__") and len(audit_df) > 0:
            try:
                import pandas as _pd
                if hasattr(audit_df, "columns") and "confirmed" in audit_df.columns:
                    audit_confirmation_rate = float(
                        _pd.to_numeric(audit_df["confirmed"], errors="coerce").mean()
                    )
            except Exception:
                pass

    confidence_decomp = build_confidence_decomposition(
        regime_classifier_confidence=float((regime or {}).get("confidence", 0.0)),
        cv_stability=cv_stability,
        mc_survival_rate=mc_survival_rate,
        sensitivity_class=sensitivity_class,
        audit_confirmation_rate=audit_confirmation_rate,
    )

    recommendation = recommend_parameters(
        strategy_profile=strategy_profile,
        regime=regime,
        features=features,
        cfg=(options.get("recommender") or {}),
        confidence_decomposition=confidence_decomp,
    )

    out["strategy_id"] = strategy_id
    out["instrument"] = instrument
    out["contract"] = contract
    out["strategy_profile"] = strategy_profile
    out["snapshot"] = features
    out["regime"] = regime
    out["confidence_decomposition"] = confidence_decomp.to_dict()
    out["recommendation"] = recommendation

    templates_enabled = bool(options.get("generate_templates", True))
    if templates_enabled:
        try:
            source_template = str(options.get("source_template") or "sampleTemplate.xml")
            template_path = Path(strategy_profile.get("source_files", {}).get("templates_dir", "")) / source_template
            output_dir = str(options.get("template_output_dir") or "outputs/templates")
            session_windows = options.get("session_windows")

            template_bundle = generate_session_templates(
                strategy_id=strategy_id,
                regime_id=str(regime.get("regime_id") or "unknown_regime"),
                recommended_params=(recommendation.get("recommended_params") or {}),
                base_template_path=str(template_path),
                output_dir=output_dir,
                session_windows=session_windows,
            )
            out["template_bundle"] = template_bundle
        except Exception as e:
            out["warnings"].append(f"template_export_failed:{type(e).__name__}:{e}")

    derived["regime_recommender"] = out

    if bool(options.get("capture_outcomes", True)):
        try:
            baseline_metrics = options.get("baseline_metrics") or {}
            outcome = attach_outcome_snapshot(pkg=pkg, baseline=baseline_metrics)
            out["outcomes"] = outcome
            derived["regime_recommender"] = out
        except Exception as e:
            out["warnings"].append(f"outcome_capture_failed:{type(e).__name__}:{e}")
            derived["regime_recommender"] = out
    persist_path = str(options.get("persist_store_path") or "").strip()
    if persist_path:
        try:
            persist_result = persist_record_jsonl(payload=out, jsonl_path=persist_path)
            out["storage"] = persist_result
            derived["regime_recommender"] = out
        except Exception as e:
            out["warnings"].append(f"storage_persist_failed:{type(e).__name__}:{e}")
            derived["regime_recommender"] = out

