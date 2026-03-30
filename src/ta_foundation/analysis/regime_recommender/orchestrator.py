from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from ta_foundation.analysis.regime_recommender.features import build_multitf_features
from ta_foundation.analysis.regime_recommender.classifier import classify_regime
from ta_foundation.analysis.regime_recommender.recommender import recommend_parameters
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
    recommendation = recommend_parameters(
        strategy_profile=strategy_profile,
        regime=regime,
        features=features,
        cfg=(options.get("recommender") or {}),
    )

    out["strategy_id"] = strategy_id
    out["instrument"] = instrument
    out["contract"] = contract
    out["strategy_profile"] = strategy_profile
    out["snapshot"] = features
    out["regime"] = regime
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

