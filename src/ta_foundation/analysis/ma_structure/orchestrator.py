from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

import pandas as pd

from .aggregation import build_summary_by_anchor, build_summary_by_anchor_regime
from .anchors import build_anchors_table, ensure_datetime_index
from .models import AnchorSpec, EngineConfig
from .path_metrics import compute_path_metrics
from .segment_detection import detect_segments
from .regime_context import attach_regime_context
from .settings_extractor import extract_anchors_from_settings
from .tp_sl_engine import score_tp_sl_candidates
from .trade_alignment import build_trade_recommendation_alignment
from .trade_time_tp_sl import score_trade_time_tp_sl


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
    "trade_time_candidates",
    "trade_time_per_trade",
)


# ---------------------------------------------------------------------------
# Anchor resolution
# ---------------------------------------------------------------------------

def resolve_anchors_for_run(
    pkg: Any,
    options: Dict[str, Any],
) -> tuple[List[AnchorSpec], str]:
    """
    Determine the anchor set to use for a specific run.

    Resolution order based on anchor_source:
      "settings" — extract from pkg.settings; fall back to YAML on failure.
      "auto"     — same as "settings" but silently falls back without warning.
      "yaml"     — always use YAML-configured anchors (default).

    Returns (anchor_specs, source_used) where source_used is one of:
      "settings", "yaml_fallback", "yaml".
    """
    anchor_source = str(options.get("anchor_source", "yaml") or "yaml").lower()
    strategy_family = str(options.get("strategy_family", "") or "")

    yaml_anchors = [
        AnchorSpec(
            family=str(a.get("family", "SMA")).upper(),
            length=int(a.get("length", 20)),
            source=str(a.get("source", "close")).lower(),
        )
        for a in (options.get("anchors") or [])
    ]

    if anchor_source in ("settings", "auto"):
        settings_df = getattr(pkg, "settings", None)
        extracted = extract_anchors_from_settings(settings_df, strategy_family)
        if extracted:
            return extracted, "settings"
        # Fall back to YAML anchors
        return yaml_anchors, "yaml_fallback"

    return yaml_anchors, "yaml"


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

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


def _resolve_contract(
    market: Any,
    instrument: Optional[str],
    contract: Optional[str],
) -> Optional[str]:
    """
    When contract is None, auto-select the best available contract for the
    instrument from MarketDataStore.minute_bars (or .ticks).

    Selection: the contract whose bars have the latest max timestamp.
    Returns the resolved contract string, or None if nothing is available.
    """
    if contract is not None:
        return contract

    if instrument is None:
        return None

    # Collect all available contracts for this instrument
    best_contract: Optional[str] = None
    best_ts: Optional[Any] = None

    for attr in ("minute_bars", "ticks"):
        store: dict = getattr(market, attr, {}) or {}
        for (instr, ctract), df in store.items():
            if instr != instrument:
                continue
            if not isinstance(df, pd.DataFrame) or df.empty:
                continue
            # pick the contract with the most recent data
            ts_col = "dt" if "dt" in df.columns else (df.index.name or None)
            try:
                if ts_col and ts_col in df.columns:
                    max_ts = df[ts_col].max()
                else:
                    max_ts = df.index.max()
                if best_ts is None or max_ts > best_ts:
                    best_ts = max_ts
                    best_contract = ctract
            except Exception:
                if best_contract is None:
                    best_contract = ctract

    return best_contract


def _extract_market_bars(
    market: Any,
    *,
    instrument: Optional[str],
    contract: Optional[str],
    timeframe: str,
) -> pd.DataFrame:
    if market is None:
        raise ValueError("anchor_interaction requires shared market data / MarketDataStore")

    # Auto-resolve contract when null/None
    resolved_contract = _resolve_contract(market, instrument, contract)

    candidates = []

    if hasattr(market, "get_bars"):
        try:
            df = market.get_bars(
                instrument_root=instrument,
                contract=resolved_contract,
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
            if instrument and resolved_contract and (instrument, resolved_contract) in mb:
                candidate = _coerce_bars_candidate(mb[(instrument, resolved_contract)])
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
        f"Could not resolve market bars for instrument={instrument!r}, "
        f"contract={resolved_contract!r} (configured: {contract!r}), timeframe={timeframe!r}"
    )


def _artifact_ref(path: Optional[str]) -> Dict[str, Any]:
    return {"type": "parquet", "path": path}


def _empty_artifacts() -> Dict[str, Dict[str, Any]]:
    return {key: _artifact_ref(None) for key in EXPECTED_ARTIFACT_KEYS}


# ---------------------------------------------------------------------------
# Failure stub
# ---------------------------------------------------------------------------

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
            "mode": config.mode,
            "anchor_source": config.anchor_source,
            "strategy_family": config.strategy_family,
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
            "anchor_source_used": config.anchor_source,
            "tp_sl_candidates": 0,
            "recommendations": 0,
            "validation_fold_count": 0,
            "trade_time_tp_sl_run": False,
            "trade_time_candidates": 0,
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


# ---------------------------------------------------------------------------
# Main analysis entry point
# ---------------------------------------------------------------------------

def run_anchor_interaction_analysis(
    *,
    pkg: Any,
    market: Any,
    options: Dict[str, Any],
    persist_df: Optional[Callable[[str, pd.DataFrame], str]] = None,
) -> Dict[str, Any]:
    """
    Run the full MA Anchor interaction analysis for one AnalysisPackage.

    Modes
    -----
    discovery (default)
        Anchors are specified in report.yaml.  Structural segment analysis
        and TP/SL scoring from segments.  No dependency on pkg.trades or
        pkg.settings.

    strategy_aware
        Anchors are derived per-run from pkg.settings (strategy_family
        selects the extractor).  Falls back to YAML anchors if extraction
        fails.  Trade-time TP/SL simulation is run automatically when
        pkg.trades is present.
    """
    if not options or not bool(options.get("enabled", False)):
        return {"ok": True, "reason": "disabled"}

    # --- resolve anchors for this run ---
    resolved_anchors, anchor_source_used = resolve_anchors_for_run(pkg, options)

    if not resolved_anchors:
        return attach_anchor_interaction_failure(
            pkg=pkg,
            reason="No anchors resolved (check anchor_source, strategy_family, and YAML anchors list)",
            options=options,
        )

    # Inject resolved anchors into a working copy of options so EngineConfig
    # picks them up, without mutating the caller's dict.
    effective_options = {
        **options,
        "anchors": [
            {"family": a.family, "length": a.length, "source": a.source}
            for a in resolved_anchors
        ],
    }
    config = EngineConfig.from_options(effective_options)

    run_id = (
        getattr(pkg, "run_id", None)
        or (getattr(pkg, "metadata", {}) or {}).get("run_id")
        or "run"
    )

    # --- bars ---
    try:
        bars = _extract_market_bars(
            market,
            instrument=config.instrument,
            contract=config.contract,
            timeframe=config.timeframe,
        )
    except ValueError as exc:
        return attach_anchor_interaction_failure(pkg=pkg, reason=str(exc), options=effective_options)

    # --- structural analysis ---
    anchors_tbl = build_anchors_table(bars, config.anchors)
    segments = detect_segments(bars, anchors_tbl, config, run_id=run_id)
    segments = attach_regime_context(segments, bars)
    path_stats = compute_path_metrics(bars, anchors_tbl, segments)
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

    # --- structural TP/SL ---
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

    # Best structural recommendation per anchor
    recommendations = (
        tp_sl_candidates.sort_values(
            ["anchor_id", "stability_score", "robust_score", "expectancy_score"],
            ascending=[True, False, False, False],
        )
        .groupby("anchor_id", as_index=False)
        .head(1)
        .reset_index(drop=True)
    ) if not tp_sl_candidates.empty else pd.DataFrame()

    # --- trade-to-recommendation context alignment ---
    trade_recommendation_alignment = build_trade_recommendation_alignment(
        getattr(pkg, "trades", None),
        recommendations,
        bars,
    )

    # --- trade-time TP/SL simulation ---
    trade_time_candidates = pd.DataFrame()
    trade_time_per_trade = pd.DataFrame()
    trade_time_ran = False

    pkg_trades = getattr(pkg, "trades", None)
    if config.trade_time_tp_sl_enabled and isinstance(pkg_trades, pd.DataFrame) and not pkg_trades.empty:
        trade_time_candidates, trade_time_per_trade = score_trade_time_tp_sl(
            trades=pkg_trades,
            bars=bars,
            tp_grid=config.trade_time_tp_grid,
            sl_grid=config.trade_time_sl_grid,
            atr_period=config.trade_time_atr_period,
            max_bars_forward=config.trade_time_max_bars_forward,
        )
        trade_time_ran = True

    # --- assemble assets ---
    assets: Dict[str, pd.DataFrame] = {
        "anchors": anchors_tbl,
        "segments": segments,
        "segment_path_stats": path_stats,
        "summary_by_anchor": summary_by_anchor,
        "summary_by_anchor_regime": summary_by_anchor_regime,
        "tp_sl_candidates": tp_sl_candidates,
        "recommendations": recommendations,
        "validation_folds": validation_folds,
        "trade_recommendation_alignment": trade_recommendation_alignment,
        "trade_time_candidates": trade_time_candidates,
        "trade_time_per_trade": trade_time_per_trade,
    }

    artifact_paths: Dict[str, Optional[str]] = {}
    if persist_df is not None:
        for name, df in assets.items():
            artifact_paths[name] = persist_df(name, df)

    # --- warnings ---
    warnings: list[str] = []
    if config.tp_sl_enabled and tp_sl_candidates.empty:
        warnings.append("tp_sl_candidates_empty")
    if config.tp_sl_enabled and validation_folds.empty:
        warnings.append("validation_folds_empty")
    if trade_time_ran and trade_time_candidates.empty:
        warnings.append("trade_time_candidates_empty")
    if anchor_source_used == "yaml_fallback":
        warnings.append("anchor_source_settings_extraction_failed_used_yaml")

    # --- attach to pkg ---
    pkg.assets = getattr(pkg, "assets", {}) or {}
    pkg.assets["anchor_interaction"] = assets

    pkg.metadata = getattr(pkg, "metadata", {}) or {}
    pkg.metadata.setdefault("derived", {})
    pkg.metadata["derived"]["anchor_interaction"] = {
        "version": "ai_v1",
        "engine": {
            "mode": config.mode,
            "anchor_source_configured": config.anchor_source,
            "anchor_source_used": anchor_source_used,
            "strategy_family": config.strategy_family,
            "anchors_resolved": [a.anchor_id for a in config.anchors],
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
            "trade_time_tp_sl_enabled": config.trade_time_tp_sl_enabled,
            "trade_time_max_bars_forward": config.trade_time_max_bars_forward,
        },
        "artifacts": {
            key: _artifact_ref(artifact_paths.get(key))
            for key in EXPECTED_ARTIFACT_KEYS
        },
        "diagnostics": {
            "ok": True,
            "n_input_bars": int(len(bars)),
            "n_segments": int(len(segments)),
            "n_censored": int(segments["censored"].sum()) if not segments.empty else 0,
            "pct_censored": float(segments["censored"].mean()) if not segments.empty else 0.0,
            "anchors_tested": len(config.anchors),
            "anchor_source_used": anchor_source_used,
            "tp_sl_candidates": int(len(tp_sl_candidates)),
            "recommendations": int(len(recommendations)),
            "validation_fold_count": int(len(validation_folds)),
            "trade_alignment_count": int(len(trade_recommendation_alignment)),
            "trade_time_tp_sl_run": trade_time_ran,
            "trade_time_candidates": int(len(trade_time_candidates)),
            "trade_time_per_trade": int(len(trade_time_per_trade)),
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
