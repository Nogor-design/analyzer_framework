from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import pandas as pd

from ta_foundation.core.model import AnalysisPackage  # adjust if needed

from .cluster import build_pattern_clusters
from .discovery import compute_market_discovery
from .engine import run_pattern_sweep
from .io import attach_artifact_ref, deep_copy_json_safe, df_to_parquet, pattern_engine_run_dir
from .monte_carlo import run_prop_monte_carlo, run_prop_monte_carlo_regime
from .robustness_cv import compute_purged_walkforward_cv
from .trade_pattern_audit import compute_trade_pattern_audit


# ======================================================================================
# Internal helpers
# ======================================================================================

def _ensure_pkg_has_metadata(pkg: Any) -> None:
    if getattr(pkg, "metadata", None) is None:
        pkg.metadata = {}
    if "derived" not in pkg.metadata or pkg.metadata["derived"] is None:
        pkg.metadata["derived"] = {}


def _ensure_pkg_assets_store(pkg: Any) -> Dict[str, Any]:
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

    # always cache in-memory (even empty)
    asset_store[artifact_key] = df

    # only write parquet / attach ref when non-empty
    if len(df) == 0:
        return

    path = run_dir / filename
    df_to_parquet(df=df, path=path)
    attach_artifact_ref(artifacts=artifacts_meta, key=artifact_key, path=path)


# ======================================================================================
# Trade Pattern Audit pass
# ======================================================================================

def _run_trade_pattern_audit_pass(
    *,
    packages: Dict[str, Any],
    market: Any,
    options: Dict[str, Any],
) -> None:
    """Run compute_trade_pattern_audit for every real (non-synthetic) package."""
    for run_id, pkg in (packages or {}).items():
        # Skip synthetic market-discovery packages
        if str(run_id).startswith("__"):
            continue
        try:
            result = compute_trade_pattern_audit(pkg=pkg, market=market, options=options)

            # Attach metadata (JSON-safe)
            _ensure_pkg_has_metadata(pkg)
            pkg.metadata["derived"]["trade_pattern_audit"] = {
                "summary":     deep_copy_json_safe(result.get("summary", {})),
                "diagnostics": deep_copy_json_safe(result.get("diagnostics", {})),
            }

            # Attach in-memory DataFrame to assets (separate key from "pattern_engine")
            if not hasattr(pkg, "assets") or pkg.assets is None:
                pkg.assets = {}
            if "trade_pattern_audit" not in pkg.assets:
                pkg.assets["trade_pattern_audit"] = {}
            pkg.assets["trade_pattern_audit"]["audit_df"] = result.get("audit_df", pd.DataFrame())

        except Exception as e:
            _ensure_pkg_has_metadata(pkg)
            pkg.metadata["derived"]["trade_pattern_audit"] = {
                "diagnostics": {
                    "ok": False,
                    "issues": [f"trade_pattern_audit error: {type(e).__name__}: {e}"],
                }
            }


# ======================================================================================
# Public entry point
# ======================================================================================

def compute_and_attach_pattern_engine(
    *,
    packages: Dict[str, Any],
    market: Any,
    options: Dict[str, Any],
) -> None:
    pe_enabled = bool(options.get("enabled", False))
    audit_enabled = bool((options.get("trade_pattern_audit") or {}).get("enabled", False))

    if not pe_enabled and not audit_enabled:
        return

    # When only the audit is requested (main sweep disabled), run audit and return early.
    if not pe_enabled:
        _run_trade_pattern_audit_pass(packages=packages, market=market, options=options)
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
        "tz": str(options.get("timezone", "America/Denver")),
    }

    # Constraints used by MC
    prop = options.get("prop", {}) or {}
    constraints = {
        "trailing_drawdown_usd": float(prop.get("trailing_drawdown_usd", 1500)),
        "daily_loss_limit_usd": float(prop.get("daily_loss_limit_usd", 1000)),
        "tick_value_usd": float(prop.get("tick_value_usd", 12.5)),
    }

    mc_options = dict(options.get("monte_carlo", {}) or {})
    if "seed" not in mc_options:
        mc_options["seed"] = 7  # deterministic default

    # Prefer daily-loss evaluation from session open (prop-style). Monte Carlo implementation may ignore if unsupported.
    if "daily_loss_mode" not in mc_options:
        mc_options["daily_loss_mode"] = "from_open"

    # ----------------------------------------------------------------------------------
    # Scope A: run_attached
    # ----------------------------------------------------------------------------------
    if "run_attached" in scopes:
        for run_id, pkg in (packages or {}).items():
            asset_store = _ensure_pkg_assets_store(pkg)
            run_dir = pattern_engine_run_dir(run_id=str(run_id))

            artifacts_meta: Dict[str, Any] = {}
            diagnostics: Dict[str, Any] = {"validation": {"ok": True, "issues": []}, "counts": {}}

            try:
                res = run_pattern_sweep(pkg=pkg, market=market, options=options)

                diag = res.get("diagnostics", {}) or {}
                diagnostics["sweep"] = diag
                diagnostics["validation"]["ok"] = bool(diag.get("ok", True))
                if not diagnostics["validation"]["ok"]:
                    issues = diag.get("issues") or [diag.get("reason", "unknown")]
                    for it in issues:
                        diagnostics["validation"]["issues"].append(str(it))

                patterns_df = res.get("patterns_df", pd.DataFrame())
                signals_df = res.get("signals_df", pd.DataFrame())
                outcomes_df = res.get("outcomes_df", pd.DataFrame())
                pattern_stats_df = res.get("pattern_stats_df", pd.DataFrame())
                events_df = res.get("events_df", pd.DataFrame())
                events_exec_df = res.get("events_exec_df", events_df)

                _write_and_cache_df(run_dir=run_dir, artifacts_meta=artifacts_meta, asset_store=asset_store,
                                    artifact_key="patterns", filename="patterns.parquet", df=patterns_df)
                _write_and_cache_df(run_dir=run_dir, artifacts_meta=artifacts_meta, asset_store=asset_store,
                                    artifact_key="signals", filename="signals.parquet", df=signals_df)
                _write_and_cache_df(run_dir=run_dir, artifacts_meta=artifacts_meta, asset_store=asset_store,
                                    artifact_key="outcomes", filename="outcomes.parquet", df=outcomes_df)
                _write_and_cache_df(run_dir=run_dir, artifacts_meta=artifacts_meta, asset_store=asset_store,
                                    artifact_key="pattern_stats", filename="pattern_stats.parquet", df=pattern_stats_df)
                _write_and_cache_df(run_dir=run_dir, artifacts_meta=artifacts_meta, asset_store=asset_store,
                                    artifact_key="events", filename="events.parquet", df=events_df)

                _write_and_cache_df(run_dir=run_dir, artifacts_meta=artifacts_meta, asset_store=asset_store,
                                    artifact_key="events_exec", filename="events_exec.parquet", df=events_exec_df)

                # Optional: clusters
                try:
                    clusters = build_pattern_clusters(pattern_stats_df=pattern_stats_df, options=options)
                    if isinstance(clusters, dict):
                        _write_and_cache_df(run_dir=run_dir, artifacts_meta=artifacts_meta, asset_store=asset_store,
                                            artifact_key="clusters", filename="clusters.parquet",
                                            df=clusters.get("clusters_df", pd.DataFrame()))
                        _write_and_cache_df(run_dir=run_dir, artifacts_meta=artifacts_meta, asset_store=asset_store,
                                            artifact_key="cluster_members", filename="cluster_members.parquet",
                                            df=clusters.get("cluster_members_df", pd.DataFrame()))
                        _write_and_cache_df(run_dir=run_dir, artifacts_meta=artifacts_meta, asset_store=asset_store,
                                            artifact_key="cluster_stats", filename="cluster_stats.parquet",
                                            df=clusters.get("cluster_stats_df", pd.DataFrame()))
                except Exception:
                    pass

                # Optional: CV robustness
                try:
                    cv = compute_purged_walkforward_cv(events_df=events_df, options=options)
                    if isinstance(cv, dict):
                        _write_and_cache_df(run_dir=run_dir, artifacts_meta=artifacts_meta, asset_store=asset_store,
                                            artifact_key="oos_stats", filename="oos_stats.parquet",
                                            df=cv.get("oos_stats_df", pd.DataFrame()))
                except Exception:
                    pass

                # Monte Carlo (baseline)
                # If trade intake was already applied upstream (events_exec_df),
                # disable MC-side intake to avoid double-filtering while keeping a single config source.
                mc_options_for_mc = dict(mc_options)
                try:
                    if isinstance(mc_options_for_mc.get("intake"), dict) and mc_options_for_mc["intake"].get("enabled", False):
                        mc_options_for_mc["intake"] = dict(mc_options_for_mc["intake"])
                        mc_options_for_mc["intake"]["enabled"] = False
                except Exception:
                    pass

                mc_res = run_prop_monte_carlo(events_df=events_exec_df, constraints=constraints, mc_options=mc_options_for_mc)
                mc_summary_df = mc_res.get("mc_summary_df", pd.DataFrame())
                _write_and_cache_df(run_dir=run_dir, artifacts_meta=artifacts_meta, asset_store=asset_store,
                                    artifact_key="mc_summary", filename="mc_summary.parquet", df=mc_summary_df)

                # Monte Carlo (regime-aware)
                mc2 = run_prop_monte_carlo_regime(events_df=events_exec_df, constraints=constraints, mc_options=mc_options_for_mc)
                mc_regime_summary_df = mc2.get("mc_regime_summary_df", pd.DataFrame())
                _write_and_cache_df(run_dir=run_dir, artifacts_meta=artifacts_meta, asset_store=asset_store,
                                    artifact_key="mc_regime_summary", filename="mc_regime_summary.parquet",
                                    df=mc_regime_summary_df)

                # Optional: slippage surface
                mc_slip_surface_df = mc2.get("mc_slippage_surface_df", pd.DataFrame())
                _write_and_cache_df(run_dir=run_dir, artifacts_meta=artifacts_meta, asset_store=asset_store,
                                    artifact_key="mc_slippage_surface", filename="mc_slippage_surface.parquet",
                                    df=mc_slip_surface_df)

                diagnostics["counts"] = {
                    "n_patterns": int(len(patterns_df)) if isinstance(patterns_df, pd.DataFrame) else 0,
                    "n_signals": int(len(signals_df)) if isinstance(signals_df, pd.DataFrame) else 0,
                    "n_outcomes": int(len(outcomes_df)) if isinstance(outcomes_df, pd.DataFrame) else 0,
                    "n_events": int(len(events_df)) if isinstance(events_df, pd.DataFrame) else 0,
                "n_events_exec": int(len(events_exec_df)) if isinstance(events_exec_df, pd.DataFrame) else 0,
                    "n_events_exec": int(len(events_exec_df)) if isinstance(events_exec_df, pd.DataFrame) else 0,
                    "n_mc": int(len(mc_summary_df)) if isinstance(mc_summary_df, pd.DataFrame) else 0,
                    "n_mc_regime": int(len(mc_regime_summary_df)) if isinstance(mc_regime_summary_df, pd.DataFrame) else 0,
                }

                _attach_block(pkg=pkg, options=options, engine_info=engine_info,
                              artifacts=artifacts_meta, diagnostics=diagnostics)

            except Exception as e:
                reason = f"pattern_engine_exception: {type(e).__name__}: {e}"
                _attach_disabled_block(pkg, engine_info=engine_info, options=options, reason=reason)
                asset_store["__error__"] = reason

    # ----------------------------------------------------------------------------------
    # Scope B: market_discovery (synthetic package)
    # ----------------------------------------------------------------------------------
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
            packages[synth_id] = AnalysisPackage(
                run_id=synth_id,
                trades=None,
                daily=None,
                summary=None,
                settings=None,
                warnings=[],
                metadata={},
                assets={},
            )

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
            if bars is None or len(bars) == 0:
                raise ValueError("no bars found for market_discovery")

            sweep_opts = dict(options)
            sweep_opts["instrument"] = instrument
            sweep_opts["contract"] = contract
            sweep_opts["timeframe"] = timeframe
            sweep_opts["market_discovery"] = md  # so run_pattern_sweep can read md overrides

            res = run_pattern_sweep(pkg=synth_pkg, market=market, options=sweep_opts)

            diag = res.get("diagnostics", {}) or {}
            diagnostics["sweep"] = diag
            diagnostics["validation"]["ok"] = bool(diag.get("ok", True))
            if not diagnostics["validation"]["ok"]:
                issues = diag.get("issues") or [diag.get("reason", "unknown")]
                for it in issues:
                    diagnostics["validation"]["issues"].append(str(it))

            patterns_df = res.get("patterns_df", pd.DataFrame())
            signals_df = res.get("signals_df", pd.DataFrame())
            outcomes_df = res.get("outcomes_df", pd.DataFrame())
            pattern_stats_df = res.get("pattern_stats_df", pd.DataFrame())
            events_df = res.get("events_df", pd.DataFrame())

            # Persist core sweep artifacts (same order as run_attached)
            _write_and_cache_df(run_dir=run_dir, artifacts_meta=artifacts_meta, asset_store=synth_assets,
                                artifact_key="patterns", filename="patterns.parquet", df=patterns_df)
            _write_and_cache_df(run_dir=run_dir, artifacts_meta=artifacts_meta, asset_store=synth_assets,
                                artifact_key="signals", filename="signals.parquet", df=signals_df)
            _write_and_cache_df(run_dir=run_dir, artifacts_meta=artifacts_meta, asset_store=synth_assets,
                                artifact_key="outcomes", filename="outcomes.parquet", df=outcomes_df)
            _write_and_cache_df(run_dir=run_dir, artifacts_meta=artifacts_meta, asset_store=synth_assets,
                                artifact_key="pattern_stats", filename="pattern_stats.parquet", df=pattern_stats_df)
            _write_and_cache_df(run_dir=run_dir, artifacts_meta=artifacts_meta, asset_store=synth_assets,
                                artifact_key="events", filename="events.parquet", df=events_df)

            _write_and_cache_df(run_dir=run_dir, artifacts_meta=artifacts_meta, asset_store=synth_assets,
                                artifact_key="events_exec", filename="events_exec.parquet", df=events_exec_df)

            # Baseline MC (optional, but keeps parity with diagnostics expectations)
            # If trade intake was already applied upstream (events_exec_df),
            # disable MC-side intake to avoid double-filtering while keeping a single config source.
            mc_options_for_mc = dict(mc_options)
            try:
                if isinstance(mc_options_for_mc.get("intake"), dict) and mc_options_for_mc["intake"].get("enabled", False):
                    mc_options_for_mc["intake"] = dict(mc_options_for_mc["intake"])
                    mc_options_for_mc["intake"]["enabled"] = False
            except Exception:
                pass

            mc_res = run_prop_monte_carlo(events_df=events_exec_df, constraints=constraints, mc_options=mc_options_for_mc)
            mc_summary_df = mc_res.get("mc_summary_df", pd.DataFrame())
            _write_and_cache_df(run_dir=run_dir, artifacts_meta=artifacts_meta, asset_store=synth_assets,
                                artifact_key="mc_summary", filename="mc_summary.parquet", df=mc_summary_df)

            # Market discovery analytics
            disc = compute_market_discovery(bars=bars, signals_df=signals_df, options=options)

            _write_and_cache_df(run_dir=run_dir, artifacts_meta=artifacts_meta, asset_store=synth_assets,
                                artifact_key="discovery_events", filename="discovery_events.parquet",
                                df=disc.get("discovery_events_df", pd.DataFrame()))
            _write_and_cache_df(run_dir=run_dir, artifacts_meta=artifacts_meta, asset_store=synth_assets,
                                artifact_key="discovery_stats", filename="discovery_stats.parquet",
                                df=disc.get("discovery_stats_df", pd.DataFrame()))
            _write_and_cache_df(run_dir=run_dir, artifacts_meta=artifacts_meta, asset_store=synth_assets,
                                artifact_key="discovery_regime_stats", filename="discovery_regime_stats.parquet",
                                df=disc.get("discovery_regime_stats_df", pd.DataFrame()))
            _write_and_cache_df(run_dir=run_dir, artifacts_meta=artifacts_meta, asset_store=synth_assets,
                                artifact_key="discovery_stability", filename="discovery_stability.parquet",
                                df=disc.get("discovery_stability_df", pd.DataFrame()))

            # Regime-aware MC
            mc2 = run_prop_monte_carlo_regime(events_df=events_exec_df, constraints=constraints, mc_options=mc_options_for_mc)
            mc_regime_summary_df = mc2.get("mc_regime_summary_df", pd.DataFrame())
            _write_and_cache_df(run_dir=run_dir, artifacts_meta=artifacts_meta, asset_store=synth_assets,
                                artifact_key="mc_regime_summary", filename="mc_regime_summary.parquet",
                                df=mc_regime_summary_df)

            diagnostics["counts"] = {
                "n_patterns": int(len(patterns_df)) if isinstance(patterns_df, pd.DataFrame) else 0,
                "n_signals": int(len(signals_df)) if isinstance(signals_df, pd.DataFrame) else 0,
                "n_outcomes": int(len(outcomes_df)) if isinstance(outcomes_df, pd.DataFrame) else 0,
                "n_events": int(len(events_df)) if isinstance(events_df, pd.DataFrame) else 0,
                "n_mc": int(len(mc_summary_df)) if isinstance(mc_summary_df, pd.DataFrame) else 0,
                "n_mc_regime": int(len(mc_regime_summary_df)) if isinstance(mc_regime_summary_df, pd.DataFrame) else 0,
                "n_discovery_events": int(len(disc.get("discovery_events_df", pd.DataFrame()))),
                "n_discovery_stats": int(len(disc.get("discovery_stats_df", pd.DataFrame()))),
            }

            _attach_block(pkg=synth_pkg, options=options, engine_info=engine_info,
                          artifacts=artifacts_meta, diagnostics=diagnostics)


        except Exception as e:
            reason = f"pattern_engine_exception: {type(e).__name__}: {e}"
            _attach_disabled_block(synth_pkg, engine_info=engine_info, options=options, reason=reason)
            synth_assets["__error__"] = reason

    # ----------------------------------------------------------------------------------
    # Trade Pattern Audit (runs after all sweep scopes when audit is enabled)
    # ----------------------------------------------------------------------------------
    if audit_enabled:
        _run_trade_pattern_audit_pass(packages=packages, market=market, options=options)