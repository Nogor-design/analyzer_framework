from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd

from ta_foundation.core.model import AnalysisPackage  # adjust import if your project differs
from .discovery import compute_market_discovery
from .io import (
    attach_artifact_ref,
    deep_copy_json_safe,
    df_to_parquet,
    pattern_engine_run_dir,
)
from .engine import run_pattern_sweep
from .cluster import build_pattern_clusters
from .robustness_cv import compute_purged_walkforward_cv
from .monte_carlo import run_prop_monte_carlo


# ----------------------------
# Internal helpers
# ----------------------------

def _ensure_pkg_has_metadata(pkg: Any) -> None:
    if getattr(pkg, "metadata", None) is None:
        pkg.metadata = {}
    if "derived" not in pkg.metadata or pkg.metadata["derived"] is None:
        pkg.metadata["derived"] = {}


def _ensure_pkg_assets_store(pkg: Any) -> Dict[str, Any]:
    """
    In-memory artifact store lives in pkg.assets["pattern_engine"].
    This is intentionally NOT JSON-serialized. Sections can read from it.
    """
    if not hasattr(pkg, "assets") or getattr(pkg, "assets") is None:
        pkg.assets = {}
    if "pattern_engine" not in pkg.assets or pkg.assets["pattern_engine"] is None:
        pkg.assets["pattern_engine"] = {}
    store = pkg.assets["pattern_engine"]
    if not isinstance(store, dict):
        pkg.assets["pattern_engine"] = {}
        store = pkg.assets["pattern_engine"]
    return store


def _attach_block(
    *,
    pkg: Any,
    options: Dict[str, Any],
    engine_info: Dict[str, Any],
    artifacts: Dict[str, Any],
    diagnostics: Dict[str, Any],
) -> None:
    _ensure_pkg_has_metadata(pkg)
    pkg.metadata["derived"]["pattern_engine"] = {
        "version": "pe_v1",
        "engine": engine_info,
        "options_snapshot": deep_copy_json_safe(options),
        "artifacts": artifacts,
        "diagnostics": diagnostics,
    }


def _write_and_cache_df(
    *,
    run_dir: Path,
    artifacts_meta: Dict[str, Any],
    asset_store: Dict[str, Any],
    artifact_key: str,
    filename: str,
    df: pd.DataFrame,
) -> None:
    if df is None or not isinstance(df, pd.DataFrame):
        return

    asset_store[artifact_key] = df

    if len(df) == 0:
        return

    path = run_dir / filename
    df_to_parquet(df=df, path=path)
    attach_artifact_ref(artifacts=artifacts_meta, key=artifact_key, path=path)


def _attach_disabled_block(pkg: Any, *, engine_info: Dict[str, Any], options: Dict[str, Any], reason: str) -> None:
    _ensure_pkg_has_metadata(pkg)
    pkg.metadata["derived"]["pattern_engine"] = {
        "version": "pe_v1",
        "engine": engine_info,
        "options_snapshot": deep_copy_json_safe(options),
        "artifacts": {},
        "diagnostics": {"validation": {"ok": False, "issues": [reason]}, "counts": {}},
        "disabled": True,
        "reason": reason,
    }


# ----------------------------
# Public entry point
# ----------------------------

def compute_and_attach_pattern_engine(
    *,
    packages: Dict[str, Any],
    market: Any,
    options: Dict[str, Any],
) -> None:
    if not options.get("enabled", False):
        return

    scopes = options.get("scopes") or ["run_attached"]
    if isinstance(scopes, str):
        scopes = [scopes]

    for _, pkg in (packages or {}).items():
        store = _ensure_pkg_assets_store(pkg)
        store["__scopes__"] = list(scopes)

    engine_info = {
        "tick_size": float(options.get("tick_size", 0.25)),
        "instrument": str(options.get("instrument", "")),
        "contract": str(options.get("contract", "")),
        "bar_tf": str(options.get("timeframe", "1m")),
        "tz": "America/Denver",
    }

    # ----------------------------
    # Scope A: run_attached
    # ----------------------------
    if "run_attached" in scopes:
        for run_id, pkg in (packages or {}).items():
            asset_store = _ensure_pkg_assets_store(pkg)
            run_dir = pattern_engine_run_dir(run_id=str(run_id))

            artifacts_meta: Dict[str, Any] = {}
            diagnostics: Dict[str, Any] = {"validation": {"ok": True, "issues": []}, "counts": {}}

            try:
                res = run_pattern_sweep(pkg=pkg, market=market, options=options)
                diag = res.get("diagnostics", {}) or {}
                diagnostics["validation"]["ok"] = bool(diag.get("ok", True))
                if not diagnostics["validation"]["ok"]:
                    diagnostics["validation"]["issues"].append(str(diag.get("reason", "unknown")))

                patterns_df = res.get("patterns_df", pd.DataFrame())
                signals_df = res.get("signals_df", pd.DataFrame())
                outcomes_df = res.get("outcomes_df", pd.DataFrame())
                pattern_stats_df = res.get("pattern_stats_df", pd.DataFrame())

                _write_and_cache_df(run_dir=run_dir, artifacts_meta=artifacts_meta, asset_store=asset_store,
                                    artifact_key="patterns", filename="patterns.parquet", df=patterns_df)
                _write_and_cache_df(run_dir=run_dir, artifacts_meta=artifacts_meta, asset_store=asset_store,
                                    artifact_key="signals", filename="signals.parquet", df=signals_df)
                _write_and_cache_df(run_dir=run_dir, artifacts_meta=artifacts_meta, asset_store=asset_store,
                                    artifact_key="outcomes", filename="outcomes.parquet", df=outcomes_df)
                _write_and_cache_df(run_dir=run_dir, artifacts_meta=artifacts_meta, asset_store=asset_store,
                                    artifact_key="pattern_stats", filename="pattern_stats.parquet", df=pattern_stats_df)

                # (… keep the rest of your run_attached logic unchanged …)

                diagnostics["counts"] = {
                    "n_patterns": int(len(patterns_df)) if isinstance(patterns_df, pd.DataFrame) else 0,
                    "n_signals": int(len(signals_df)) if isinstance(signals_df, pd.DataFrame) else 0,
                    "n_outcomes": int(len(outcomes_df)) if isinstance(outcomes_df, pd.DataFrame) else 0,
                    "n_clusters": int(len(asset_store.get("clusters", pd.DataFrame())))
                    if isinstance(asset_store.get("clusters"), pd.DataFrame) else 0,
                }

                _attach_block(pkg=pkg, options=options, engine_info=engine_info, artifacts=artifacts_meta, diagnostics=diagnostics)

            except Exception as e:
                reason = f"pattern_engine_exception: {type(e).__name__}: {e}"
                _attach_disabled_block(pkg, engine_info=engine_info, options=options, reason=reason)
                asset_store["__error__"] = reason

    # ----------------------------
    # Scope B: market_discovery (synthetic package)
    # ----------------------------
    if "market_discovery" in scopes:
        md = options.get("market_discovery", {}) or {}

        instrument = str(md.get("instrument", options.get("instrument", ""))).strip()
        contract = str(md.get("contract", options.get("contract", ""))).strip()
        timeframe = str(md.get("timeframe", options.get("timeframe", "1m"))).strip()
        start = md.get("start", None)
        end = md.get("end", None)
        bars_source = md.get("bars_source", options.get("bars_source", "auto"))

        synth_id = str(md.get("run_id") or f"__market_discovery__::{instrument}::{contract}::{timeframe}")

        if synth_id not in packages:
            synth = AnalysisPackage(
                run_id=synth_id,
                trades=None,
                daily=None,
                summary=None,
                settings=None,
                warnings=[],
                metadata={},
                assets={},
            )
            packages[synth_id] = synth

        synth_pkg = packages[synth_id]
        synth_assets = _ensure_pkg_assets_store(synth_pkg)
        run_dir = pattern_engine_run_dir(run_id=str(synth_id))

        artifacts_meta: Dict[str, Any] = {}
        diagnostics: Dict[str, Any] = {"validation": {"ok": True, "issues": []}, "counts": {}}

        try:
            if not instrument or not contract:
                raise ValueError("market_discovery requires instrument and contract.")

            bars = market.get_bars(
                instrument_root=instrument,
                contract=contract,
                timeframe=timeframe,
                start=start,
                end=end,
                source=bars_source,
            )
            if bars is None or bars.empty:
                raise ValueError("No bars returned for market_discovery.")

            sweep_opts = dict(options)
            sweep_opts["instrument"] = instrument
            sweep_opts["contract"] = contract
            sweep_opts["timeframe"] = timeframe

            res = run_pattern_sweep(pkg=synth_pkg, market=market, options=sweep_opts)

            # ✅ NEW: propagate engine diagnostics to report diagnostics (like run_attached)
            diag = res.get("diagnostics", {}) or {}
            diagnostics["validation"]["ok"] = bool(diag.get("ok", True))
            if not diagnostics["validation"]["ok"]:
                diagnostics["validation"]["issues"].append(str(diag.get("reason", "unknown")))

            patterns_df = res.get("patterns_df", pd.DataFrame())
            signals_df = res.get("signals_df", pd.DataFrame())

            _write_and_cache_df(run_dir=run_dir, artifacts_meta=artifacts_meta, asset_store=synth_assets,
                                artifact_key="patterns", filename="patterns.parquet", df=patterns_df)
            _write_and_cache_df(run_dir=run_dir, artifacts_meta=artifacts_meta, asset_store=synth_assets,
                                artifact_key="signals", filename="signals.parquet", df=signals_df)

            disc = compute_market_discovery(bars=bars, signals_df=signals_df, options=options)

            _write_and_cache_df(run_dir=run_dir, artifacts_meta=artifacts_meta, asset_store=synth_assets,
                                artifact_key="discovery_events", filename="discovery_events.parquet", df=disc["discovery_events_df"])
            _write_and_cache_df(run_dir=run_dir, artifacts_meta=artifacts_meta, asset_store=synth_assets,
                                artifact_key="discovery_stats", filename="discovery_stats.parquet", df=disc["discovery_stats_df"])
            _write_and_cache_df(run_dir=run_dir, artifacts_meta=artifacts_meta, asset_store=synth_assets,
                                artifact_key="discovery_regime_stats", filename="discovery_regime_stats.parquet", df=disc["discovery_regime_stats_df"])
            _write_and_cache_df(run_dir=run_dir, artifacts_meta=artifacts_meta, asset_store=synth_assets,
                                artifact_key="discovery_stability", filename="discovery_stability.parquet", df=disc["discovery_stability_df"])

            diagnostics["counts"] = {
                "n_patterns": int(len(patterns_df)) if isinstance(patterns_df, pd.DataFrame) else 0,
                "n_signals": int(len(signals_df)) if isinstance(signals_df, pd.DataFrame) else 0,
                "n_discovery_events": int(len(disc["discovery_events_df"])) if isinstance(disc["discovery_events_df"], pd.DataFrame) else 0,
                "n_discovery_stats": int(len(disc["discovery_stats_df"])) if isinstance(disc["discovery_stats_df"], pd.DataFrame) else 0,
            }

            _attach_block(pkg=synth_pkg, options=options, engine_info=engine_info, artifacts=artifacts_meta, diagnostics=diagnostics)

        except Exception as e:
            reason = f"pattern_engine_exception: {type(e).__name__}: {e}"
            _attach_disabled_block(synth_pkg, engine_info=engine_info, options=options, reason=reason)
            synth_assets["__error__"] = reason