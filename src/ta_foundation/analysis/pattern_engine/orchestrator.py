# src/ta_foundation/analysis/pattern_engine/orchestrator.py
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd

from ta_foundation.marketdata.store import MarketDataStore
from ta_foundation.analysis.indicators.registry import IndicatorSpec, DEFAULT_INDICATORS
from ta_foundation.analysis.pattern_engine.engine import run_pattern_sweep
from ta_foundation.analysis.pattern_engine.cluster import build_pattern_clusters
from ta_foundation.analysis.pattern_engine.robustness_cv import compute_purged_walkforward_cv
from ta_foundation.analysis.pattern_engine.monte_carlo import run_prop_monte_carlo


def _ensure_derived_bucket(pkg) -> dict:
    if getattr(pkg, "metadata", None) is None:
        pkg.metadata = {}
    if "derived" not in pkg.metadata or pkg.metadata["derived"] is None:
        pkg.metadata["derived"] = {}
    return pkg.metadata["derived"]


def _default_output_dir(pkg, *, subdir: str = "pattern_engine") -> str:
    """
    Prefer a run-local output dir adjacent to the source folder.
    Keeps artifacts colocated with run inputs without needing pipeline changes.
    """
    src = (pkg.metadata or {}).get("source_folder")
    base = Path(src) if src else Path(".")
    out = base / ".ta_artifacts" / subdir / str(getattr(pkg, "run_id", "run"))
    out.mkdir(parents=True, exist_ok=True)
    return str(out)


def _load_indicator_specs(opts: Dict[str, Any]) -> list[IndicatorSpec]:
    """
    options:
      indicators:
        - name: opening_range
          params: { minutes: 15, session_start: "07:30", globex_start: "16:00" }
        - name: atr
          params: { period: 14, out: atr_14 }
    """
    out: list[IndicatorSpec] = []
    for item in (opts.get("indicators") or []):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        params = item.get("params") or {}
        out.append(IndicatorSpec(name=name, params=dict(params)))
    return out


def compute_and_attach_pattern_engine_for_package(
    pkg: Any,
    market: MarketDataStore,
    *,
    options: Dict[str, Any],
) -> None:
    """
    Analysis-layer orchestrator. Mutates pkg.metadata["derived"]["pattern_engine"].
    Does not reload files. Uses MarketDataStore for shared data.
    """

    derived = _ensure_derived_bucket(pkg)

    # Allow disabling per report.yaml
    if not bool(options.get("enabled", True)):
        derived["pattern_engine"] = {
            "version": "pe_v1",
            "disabled": True,
            "reason": "options.enabled=false",
        }
        return

    instrument = str(options.get("instrument") or "").strip().upper()
    contract = str(options.get("contract") or "").strip()
    if not instrument or not contract:
        # You can alternatively infer from pkg.settings if you standardize it; keep strict for now.
        derived["pattern_engine"] = {
            "version": "pe_v1",
            "disabled": True,
            "reason": "missing instrument/contract in options",
        }
        return

    timeframe = str(options.get("timeframe") or "1m")
    bars_source = str(options.get("bars_source") or "auto")  # auto|minute|ticks

    tick_size = float(options.get("tick_size") or 0.0)
    if tick_size <= 0:
        # NQ/ES tick sizes could be inferred, but keep explicit to avoid wrong assumptions.
        raise ValueError("pattern_engine: tick_size must be provided and > 0")

    out_dir = str(options.get("output_dir") or "").strip() or _default_output_dir(pkg)

    # Pull bars/ticks through MarketDataStore (shared, cached, tz-safe)
    bars_df = market.get_bars(
        instrument_root=instrument,
        contract=contract,
        timeframe=timeframe,
        source=bars_source,  # type: ignore[arg-type]
    )
    if bars_df is None or bars_df.empty:
        derived["pattern_engine"] = {
            "version": "pe_v1",
            "disabled": True,
            "reason": f"no bars available for {instrument} {contract} tf={timeframe} source={bars_source}",
        }
        return

    ticks_df = market.get_ticks(instrument, contract)

    # Optional indicator enrichment (pure, deterministic)
    ind_specs = _load_indicator_specs(options)
    if ind_specs:
        bars_df = DEFAULT_INDICATORS.apply(bars_df, ind_specs)

    # -----------------------
    # 1) Sweep (signals/outcomes/pattern_stats)
    # -----------------------
    sweep_opts = dict(options)
    sweep_opts["output_dir"] = out_dir
    sweep_opts["instrument"] = instrument
    sweep_opts["contract"] = contract
    sweep_opts["tick_size"] = tick_size
    sweep_opts["bars_df_override"] = bars_df
    sweep_opts.setdefault("bar_tf", timeframe)
    sweep_opts.setdefault("tz", (pkg.metadata or {}).get("timezone", "America/Denver"))

    # Provide market ctx dict to engine without breaking your conventions:
    # - bars come from MarketDataStore
    # - ticks are optional
    market_ctx = {
        "bars": bars_df,
        "ticks": ticks_df,
        "market_store": market,
    }

    pe_meta = run_pattern_sweep(pkg=pkg, market=market_ctx, options=sweep_opts)

    # -----------------------
    # 2) Clustering
    # -----------------------
    # Read back the artifacts (keeps memory bounded; consistent with your parquet-first approach)
    patterns_df = pd.read_parquet(pe_meta["artifacts"]["patterns"]["path"])
    outcomes_df = pd.read_parquet(pe_meta["artifacts"]["outcomes"]["path"])
    pattern_stats_df = pd.read_parquet(pe_meta["artifacts"]["pattern_stats"]["path"])

    cluster_cfg = (options.get("clusters") or {})
    cl = build_pattern_clusters(
        patterns_df=patterns_df,
        outcomes_df=outcomes_df,
        pattern_stats_df=pattern_stats_df,
        options=dict(cluster_cfg),
    )

    # persist cluster artifacts
    for key, df in cl.items():
        p = os.path.join(out_dir, f"{key}.parquet")
        df.to_parquet(p, index=False)
        # align keys to the metadata artifact naming you want
        if key == "embeddings_df":
            pe_meta["artifacts"]["embeddings"] = {"type": "parquet", "path": p}
        elif key == "clusters_df":
            pe_meta["artifacts"]["clusters"] = {"type": "parquet", "path": p}
        elif key == "cluster_members_df":
            pe_meta["artifacts"]["cluster_members"] = {"type": "parquet", "path": p}
        elif key == "cluster_stats_df":
            pe_meta["artifacts"]["cluster_stats"] = {"type": "parquet", "path": p}

    # -----------------------
    # 3) CV (purged walk-forward)
    # -----------------------
    # Build an events_df suitable for CV: one row per signal per horizon (pattern + cluster)
    signals_df = pd.read_parquet(pe_meta["artifacts"]["signals"]["path"])
    outcomes_df = pd.read_parquet(pe_meta["artifacts"]["outcomes"]["path"])
    events = outcomes_df.merge(
        signals_df[["signal_id", "day_id", "session_id", "regime"]],
        on="signal_id",
        how="left",
    )
    # pattern entity rows
    pat_events = events.copy()
    pat_events["entity_type"] = "pattern"
    pat_events["entity_id"] = pat_events["pattern_id"]

    # cluster entity rows (map pattern_id -> cluster_id)
    cm_path = pe_meta["artifacts"]["cluster_members"]["path"]
    cluster_members = pd.read_parquet(cm_path)[["cluster_id", "pattern_id"]]
    cl_events = events.merge(cluster_members, on="pattern_id", how="inner")
    cl_events["entity_type"] = "cluster"
    cl_events["entity_id"] = cl_events["cluster_id"]

    cv_events = pd.concat([pat_events, cl_events], ignore_index=True)
    cv_cfg = dict(options.get("cv") or {})
    cv_out = compute_purged_walkforward_cv(events_df=cv_events, options=cv_cfg)

    cv_fold_stats_df = cv_out["cv_fold_stats_df"]
    oos_stats_df = cv_out["oos_stats_df"]

    cv_fold_path = os.path.join(out_dir, "cv_fold_stats.parquet")
    oos_path = os.path.join(out_dir, "oos_stats.parquet")
    cv_fold_stats_df.to_parquet(cv_fold_path, index=False)
    oos_stats_df.to_parquet(oos_path, index=False)

    pe_meta["artifacts"]["cv_fold_stats"] = {"type": "parquet", "path": cv_fold_path}
    pe_meta["artifacts"]["oos_stats"] = {"type": "parquet", "path": oos_path}

    # -----------------------
    # 4) Monte Carlo (prop constraints)
    # -----------------------
    prop_cfg = dict(options.get("prop") or {})
    mc_cfg = dict(options.get("monte_carlo") or {})

    # Monte Carlo wants an equity-events stream.
    # For now: use cluster-level events (better multiple-testing control).
    horizon_pick = options.get("mc_horizon")
    mc_events = cl_events.copy()
    if horizon_pick is not None:
        mc_events = mc_events[mc_events["horizon"] == int(horizon_pick)]

    mc_events = mc_events.rename(columns={"ret_ticks": "pnl_ticks"})[
        ["dt", "entity_type", "entity_id", "pnl_ticks", "day_id", "session_id", "regime"]
    ].sort_values("dt")

    mc_out = run_prop_monte_carlo(
        equity_events_df=mc_events,
        constraints=prop_cfg,
        mc_options=mc_cfg,
    )
    mc_summary_df = mc_out["mc_summary_df"]
    mc_path = os.path.join(out_dir, "mc_summary.parquet")
    mc_summary_df.to_parquet(mc_path, index=False)
    pe_meta["artifacts"]["mc_summary"] = {"type": "parquet", "path": mc_path}

    # -----------------------
    # 5) Diagnostics + attach to pkg metadata
    # -----------------------
    counts = pe_meta.get("diagnostics", {}).get("counts", {}) or {}
    counts["n_clusters"] = int(len(cl.get("clusters_df", pd.DataFrame())))
    pe_meta.setdefault("diagnostics", {}).setdefault("counts", {}).update(counts)

    # keep a stable snapshot of the pattern_engine block
    derived["pattern_engine"] = pe_meta


def compute_and_attach_pattern_engine(
    packages: Dict[str, Any],
    market: Optional[MarketDataStore],
    *,
    options: Dict[str, Any],
) -> None:
    """
    Run pattern engine for all packages. Safe no-op if market is missing.
    """
    if market is None:
        return
    for _, pkg in (packages or {}).items():
        compute_and_attach_pattern_engine_for_package(pkg, market, options=options)