from __future__ import annotations

from typing import Any, Callable, Dict, Optional

import pandas as pd

from .aggregation import build_summary_by_anchor, build_summary_by_anchor_regime
from .anchors import build_anchors_table, ensure_datetime_index
from .models import EngineConfig
from .path_metrics import compute_path_metrics
from .segment_detection import detect_segments
from .regime_context import attach_regime_context
from .tp_sl_engine import score_tp_sl_candidates
from .trade_alignment import build_trade_recommendation_alignment


EXPECTED_ARTIFACT_KEYS = (
    "anchors",
    "segments",
    "segment_path_stats",
    "summary_by_anchor",
    "summary_by_anchor_regime",
    "tp_sl_candidates",
    "recommendations",
    "validation_folds",
    "trade_recommendation_alignment",
)

EXPECTED_ARTIFACT_KEYS = (
    "anchors",
    "segments",
    "segment_path_stats",
    "summary_by_anchor",
    "summary_by_anchor_regime",
    "tp_sl_candidates",
    "recommendations",
    "validation_folds",
)



def _coerce_bars_candidate(obj: Any) -> Optional[pd.DataFrame]:
    if isinstance(obj, pd.DataFrame):
        return obj

    if isinstance(obj, dict):
        for item in obj.values():
            candidate = _coerce_bars_candidate(item)
            if isinstance(candidate, pd.DataFrame):
                return candidate
        return None

    if isinstance(obj, (list, tuple)):
        for item in obj:
            candidate = _coerce_bars_candidate(item)
            if isinstance(candidate, pd.DataFrame):
                return candidate

    return None


def _extract_market_bars(
    market: Any,
    *,
    instrument: Optional[str],
    contract: Optional[str],
    timeframe: str,
) -> pd.DataFrame:
    """
    Duck-typed accessor for existing MarketDataStore variants.
    """
    if market is None:
        raise ValueError("anchor_interaction requires shared market data / MarketDataStore")

    candidates = []

    if hasattr(market, "get_bars"):
        try:
            df = market.get_bars(
                instrument_root=instrument,
                contract=contract,
                timeframe=timeframe,
            )
            candidate = _coerce_bars_candidate(df)
            if isinstance(candidate, pd.DataFrame):
                candidates.append(candidate)
        except Exception:
            pass

    if hasattr(market, "minute_bars"):
        mb = getattr(market, "minute_bars")
        if isinstance(mb, dict):
            if instrument and contract and (instrument, contract) in mb:
                candidate = _coerce_bars_candidate(mb[(instrument, contract)])
                if isinstance(candidate, pd.DataFrame):
                    candidates.append(candidate)

    if hasattr(market, "get_market_bars"):
        try:
            candidate = _coerce_bars_candidate(
                market.get_market_bars(instrument=instrument, timeframe=timeframe)
            )
            if isinstance(candidate, pd.DataFrame):
                candidates.append(candidate)
        except Exception:
            pass

    for df in candidates:
        if isinstance(df, pd.DataFrame) and not df.empty:
            return ensure_datetime_index(df)

    raise ValueError(
        f"Could not resolve market bars for instrument={instrument!r}, timeframe={timeframe!r}"
    )


def _artifact_ref(path: Optional[str]) -> Dict[str, Any]:
    return {"type": "parquet", "path": path}

def _empty_artifacts() -> Dict[str, Dict[str, Any]]:
    return {key: _artifact_ref(None) for key in EXPECTED_ARTIFACT_KEYS}


def attach_anchor_interaction_failure(
    *,
    pkg: Any,
    reason: str,
    options: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    config = EngineConfig.from_options(options or {})

    pkg.metadata = getattr(pkg, "metadata", {}) or {}
    pkg.metadata.setdefault("derived", {})
    pkg.metadata["derived"]["anchor_interaction"] = {
        "version": "ai_v1",
        "disabled": True,
        "reason": reason,
        "engine": {
            "instrument": config.instrument,
            "contract": config.contract,
            "timeframe": config.timeframe,
            "timezone": config.timezone,
            "cross_mode": config.cross_mode,
            "exit_mode": config.exit_mode,
            "recross_policy": config.recross_policy,
            "tp_sl_fold_mode": config.tp_sl_fold_mode,
            "tp_sl_min_train_segments": config.tp_sl_min_train_segments,
            "tp_sl_min_test_segments": config.tp_sl_min_test_segments,
        },
        "artifacts": _empty_artifacts(),
        "diagnostics": {
            "ok": False,
            "reason": reason,
            "n_input_bars": 0,
            "n_segments": 0,
            "n_censored": 0,
            "pct_censored": 0.0,
            "anchors_tested": len(config.anchors),
            "tp_sl_candidates": 0,
            "recommendations": 0,
            "validation_fold_count": 0,
            "timezone": config.timezone,
            "warnings": [reason],
            "validation": {
                "fold_mode": config.tp_sl_fold_mode,
                "min_train_segments": int(config.tp_sl_min_train_segments),
                "min_test_segments": int(config.tp_sl_min_test_segments),
            },
        },
    }
    return pkg.metadata["derived"]["anchor_interaction"]

def _empty_artifacts() -> Dict[str, Dict[str, Any]]:
    return {key: _artifact_ref(None) for key in EXPECTED_ARTIFACT_KEYS}


def attach_anchor_interaction_failure(
    *,
    pkg: Any,
    reason: str,
    options: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    config = EngineConfig.from_options(options or {})

    pkg.metadata = getattr(pkg, "metadata", {}) or {}
    pkg.metadata.setdefault("derived", {})
    pkg.metadata["derived"]["anchor_interaction"] = {
        "version": "ai_v1",
        "disabled": True,
        "reason": reason,
        "engine": {
            "instrument": config.instrument,
            "contract": config.contract,
            "timeframe": config.timeframe,
            "timezone": config.timezone,
            "cross_mode": config.cross_mode,
            "exit_mode": config.exit_mode,
            "recross_policy": config.recross_policy,
            "tp_sl_fold_mode": config.tp_sl_fold_mode,
            "tp_sl_min_train_segments": config.tp_sl_min_train_segments,
            "tp_sl_min_test_segments": config.tp_sl_min_test_segments,
        },
        "artifacts": _empty_artifacts(),
        "diagnostics": {
            "ok": False,
            "reason": reason,
            "n_input_bars": 0,
            "n_segments": 0,
            "n_censored": 0,
            "pct_censored": 0.0,
            "anchors_tested": len(config.anchors),
            "tp_sl_candidates": 0,
            "recommendations": 0,
            "validation_fold_count": 0,
            "timezone": config.timezone,
            "warnings": [reason],
            "validation": {
                "fold_mode": config.tp_sl_fold_mode,
                "min_train_segments": int(config.tp_sl_min_train_segments),
                "min_test_segments": int(config.tp_sl_min_test_segments),
            },
        },
    }
    return pkg.metadata["derived"]["anchor_interaction"]


def run_anchor_interaction_analysis(
    *,
    pkg: Any,
    market: Any,
    options: Dict[str, Any],
    persist_df: Optional[Callable[[str, pd.DataFrame], str]] = None,
) -> Dict[str, Any]:
    if not options or not bool(options.get("enabled", False)):
        return {"ok": True, "reason": "disabled"}

    config = EngineConfig.from_options(options)
    if not config.anchors:
        return {"ok": False, "reason": "No anchors configured"}

    run_id = getattr(pkg, "run_id", None) or (getattr(pkg, "metadata", {}) or {}).get("run_id") or "run"

    bars = _extract_market_bars(
        market,
        instrument=config.instrument,
        contract=config.contract,
        timeframe=config.timeframe,
    )
    anchors = build_anchors_table(bars, config.anchors)
    segments = detect_segments(bars, anchors, config, run_id=run_id)
    segments = attach_regime_context(segments, bars)
    path_stats = compute_path_metrics(bars, anchors, segments)
    summary_by_anchor = build_summary_by_anchor(
        segments,
        path_stats,
        sample_floor=config.descriptive_sample_floor,
    )
    summary_by_anchor_regime = build_summary_by_anchor_regime(
        segments,
        path_stats,
        regime_sample_floor=config.regime_sample_floor,
    )

    if config.tp_sl_enabled:
        tp_sl_candidates, validation_folds = score_tp_sl_candidates(
            segments,
            path_stats,
            tp_grid=config.tp_grid,
            sl_grid=config.sl_grid,
            fold_mode=config.tp_sl_fold_mode,
            min_train_segments=config.tp_sl_min_train_segments,
            min_test_segments=config.tp_sl_min_test_segments,
        )
    else:
        tp_sl_candidates = pd.DataFrame()
        validation_folds = pd.DataFrame()


    recommendations = (
        tp_sl_candidates.sort_values(
            ["anchor_id", "stability_score", "robust_score", "expectancy_score"],
            ascending=[True, False, False, False],
        )
        .groupby("anchor_id", as_index=False)
        .head(1)
        .reset_index(drop=True)
    ) if not tp_sl_candidates.empty else pd.DataFrame()

    trade_recommendation_alignment = build_trade_recommendation_alignment(
        getattr(pkg, "trades", None),
        recommendations,
        bars,
    )

    assets = {
        "anchors": anchors,
        "segments": segments,
        "segment_path_stats": path_stats,
        "summary_by_anchor": summary_by_anchor,
        "summary_by_anchor_regime": summary_by_anchor_regime,
        "tp_sl_candidates": tp_sl_candidates,
        "recommendations": recommendations,
        "validation_folds": validation_folds,
        "trade_recommendation_alignment": trade_recommendation_alignment,
    }

    artifact_paths = {}
    if persist_df is not None:
        for name, df in assets.items():
            artifact_paths[name] = persist_df(name, df)

    warnings: list[str] = []
    if config.tp_sl_enabled and tp_sl_candidates.empty:
        warnings.append("tp_sl_candidates_empty")
    if config.tp_sl_enabled and validation_folds.empty:
        warnings.append("validation_folds_empty")

    pkg.assets = getattr(pkg, "assets", {}) or {}
    pkg.assets["anchor_interaction"] = assets

    pkg.metadata = getattr(pkg, "metadata", {}) or {}
    pkg.metadata.setdefault("derived", {})
    pkg.metadata["derived"]["anchor_interaction"] = {
        "version": "ai_v1",
        "engine": {
            "instrument": config.instrument,
            "contract": config.contract,
            "timeframe": config.timeframe,
            "timezone": config.timezone,
            "cross_mode": config.cross_mode,
            "exit_mode": config.exit_mode,
            "recross_policy": config.recross_policy,
            "tp_sl_fold_mode": config.tp_sl_fold_mode,
            "tp_sl_min_train_segments": config.tp_sl_min_train_segments,
            "tp_sl_min_test_segments": config.tp_sl_min_test_segments,
        },
        "artifacts": {
            "anchors": _artifact_ref(artifact_paths.get("anchors")),
            "segments": _artifact_ref(artifact_paths.get("segments")),
            "segment_path_stats": _artifact_ref(artifact_paths.get("segment_path_stats")),
            "summary_by_anchor": _artifact_ref(artifact_paths.get("summary_by_anchor")),
            "summary_by_anchor_regime": _artifact_ref(artifact_paths.get("summary_by_anchor_regime")),
            "tp_sl_candidates": _artifact_ref(artifact_paths.get("tp_sl_candidates")),
            "recommendations": _artifact_ref(artifact_paths.get("recommendations")),
            "validation_folds": _artifact_ref(artifact_paths.get("validation_folds")),
            "trade_recommendation_alignment": _artifact_ref(artifact_paths.get("trade_recommendation_alignment")),
        },
        "diagnostics": {
            "ok": True,
            "n_input_bars": int(len(bars)),
            "n_segments": int(len(segments)),
            "n_censored": int(segments["censored"].sum()) if not segments.empty else 0,
            "pct_censored": float(segments["censored"].mean()) if not segments.empty else 0.0,
            "anchors_tested": len(config.anchors),
            "tp_sl_candidates": int(len(tp_sl_candidates)),
            "recommendations": int(len(recommendations)),
            "validation_fold_count": int(len(validation_folds)),
            "timezone": config.timezone,
            "warnings": warnings,
            "validation": {
                "fold_mode": config.tp_sl_fold_mode,
                "min_train_segments": int(config.tp_sl_min_train_segments),
                "min_test_segments": int(config.tp_sl_min_test_segments),
            },
        },
    }

    return {
        "ok": True,
        "assets": assets,
        "metadata": pkg.metadata["derived"]["anchor_interaction"],
    }
