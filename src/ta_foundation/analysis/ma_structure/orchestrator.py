from __future__ import annotations

from typing import Any, Callable, Dict, Optional

import pandas as pd

from .aggregation import build_summary_by_anchor, build_summary_by_anchor_regime
from .anchors import build_anchors_table, ensure_datetime_index
from .models import EngineConfig
from .path_metrics import compute_path_metrics
from .segment_detection import detect_segments
from .tp_sl_engine import score_tp_sl_candidates


def _extract_market_bars(market: Any, *, instrument: Optional[str], timeframe: str) -> pd.DataFrame:
    """
    Duck-typed accessor for existing MarketDataStore variants.
    """
    if market is None:
        raise ValueError("anchor_interaction requires shared market data / MarketDataStore")

    candidates = []

    if hasattr(market, "market_minute_bars"):
        mb = getattr(market, "market_minute_bars")
        if isinstance(mb, pd.DataFrame):
            candidates.append(mb)
        elif isinstance(mb, dict):
            if instrument and timeframe and (instrument, timeframe) in mb:
                candidates.append(mb[(instrument, timeframe)])
            elif instrument and instrument in mb:
                candidates.append(mb[instrument])

    if hasattr(market, "get_market_bars"):
        try:
            candidates.append(market.get_market_bars(instrument=instrument, timeframe=timeframe))
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
    bars = _extract_market_bars(market, instrument=config.instrument, timeframe=config.timeframe)

    anchors = build_anchors_table(bars, config.anchors)
    segments = detect_segments(bars, anchors, config, run_id=run_id)
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
    tp_sl_candidates = score_tp_sl_candidates(
        segments,
        path_stats,
        tp_grid=config.tp_grid,
        sl_grid=config.sl_grid,
    ) if config.tp_sl_enabled else pd.DataFrame()

    # MVP: recommendations = top row per anchor
    recommendations = (
        tp_sl_candidates.sort_values(["anchor_id", "expectancy_score"], ascending=[True, False])
        .groupby("anchor_id", as_index=False)
        .head(1)
        .reset_index(drop=True)
    ) if not tp_sl_candidates.empty else pd.DataFrame()

    assets = {
        "anchors": anchors,
        "segments": segments,
        "segment_path_stats": path_stats,
        "summary_by_anchor": summary_by_anchor,
        "summary_by_anchor_regime": summary_by_anchor_regime,
        "tp_sl_candidates": tp_sl_candidates,
        "recommendations": recommendations,
    }

    artifact_paths = {}
    if persist_df is not None:
        for name, df in assets.items():
            artifact_paths[name] = persist_df(name, df)

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
        },
        "artifacts": {
            "anchors": _artifact_ref(artifact_paths.get("anchors")),
            "segments": _artifact_ref(artifact_paths.get("segments")),
            "segment_path_stats": _artifact_ref(artifact_paths.get("segment_path_stats")),
            "summary_by_anchor": _artifact_ref(artifact_paths.get("summary_by_anchor")),
            "summary_by_anchor_regime": _artifact_ref(artifact_paths.get("summary_by_anchor_regime")),
            "tp_sl_candidates": _artifact_ref(artifact_paths.get("tp_sl_candidates")),
            "recommendations": _artifact_ref(artifact_paths.get("recommendations")),
        },
        "diagnostics": {
        "n_input_bars": int(len(bars)),
        "n_segments": int(len(segments)),
        "n_censored": int(segments["censored"].sum()) if not segments.empty else 0,
        "pct_censored": float(segments["censored"].mean()) if not segments.empty else 0.0,

        "anchors_tested": len(config.anchors),

        "tp_sl_candidates": int(len(tp_sl_candidates)),
        "recommendations": int(len(recommendations)),

        "timezone": config.timezone,

        "warnings": [],
        },
    }

    return {
        "ok": True,
        "assets": assets,
        "metadata": pkg.metadata["derived"]["anchor_interaction"],
    }