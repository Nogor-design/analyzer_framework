from __future__ import annotations

"""
Strategy Discovery Orchestrator
================================
Entry point for the strategy discovery pipeline.
Called from cli/main.py after pattern engine and MA anchor analysis.

Current implementation: Phase 0 (regime labeling) + MAE/MFE analysis on
existing pkg.trades data.  Entry/exit discovery phases are stubs.

Config lives under top-level 'strategy_discovery:' block in report.yaml:

  strategy_discovery:
    enabled: true
    instrument: "NQ"
    contract:   "H25"
    timeframe:  "5m"

    cost_model:
      commission_per_side: 2.09
      slippage_ticks: 1
      tick_value: 5.00

    walk_forward:
      wf_type: rolling
      is_pct: 0.70
      min_is_trades: 50
      min_oos_trades: 20
      n_folds: 5
      degradation_threshold: 0.20

    regime:
      adx_period: 14
      atr_period: 14
      atr_lookback: 100
      adx_trend_threshold: 25.0
      atr_high_pct: 0.75
      atr_low_pct: 0.25
"""

from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from .regime import compute_bar_regime, summarize_daily_regime
from .mae_mfe import compute_mae_mfe_profile
from .validation import run_validation, DEFAULT_WF_CONFIG, DEFAULT_COST_MODEL


# ---------------------------------------------------------------------------
# JSON-safety helpers
# ---------------------------------------------------------------------------

def _df_to_records(df: pd.DataFrame) -> list:
    """Convert DataFrame to JSON-safe list of dicts."""
    if df is None or df.empty:
        return []
    return df.where(df.notna(), other=None).to_dict(orient="records")


def _make_json_safe(obj: Any) -> Any:
    """
    Recursively convert an object to a JSON-safe form:
    - DataFrames → list of records
    - numpy scalars → Python scalars
    - nan/inf → None
    - dicts and lists are traversed recursively
    """
    if isinstance(obj, pd.DataFrame):
        return _df_to_records(obj)
    if isinstance(obj, pd.Series):
        return _df_to_records(obj.to_frame())
    if isinstance(obj, dict):
        return {k: _make_json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_make_json_safe(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        v = float(obj)
        return None if not np.isfinite(v) else v
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, float):
        return None if not np.isfinite(obj) else obj
    return obj


# ---------------------------------------------------------------------------
# Helpers for extracting regime config
# ---------------------------------------------------------------------------

def _resolve_regime_kwargs(regime_cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Extract keyword arguments for compute_bar_regime from config dict."""
    kwargs: Dict[str, Any] = {}
    mapping = {
        "adx_period": ("adx_period", int),
        "atr_period": ("atr_period", int),
        "atr_lookback": ("atr_lookback", int),
        "adx_trend_threshold": ("adx_trend_threshold", float),
        "atr_high_pct": ("atr_high_pct", float),
        "atr_low_pct": ("atr_low_pct", float),
    }
    for cfg_key, (kwarg_name, cast) in mapping.items():
        if cfg_key in regime_cfg:
            try:
                kwargs[kwarg_name] = cast(regime_cfg[cfg_key])
            except Exception:
                pass
    return kwargs


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_strategy_discovery(
    packages: Dict[str, Any],
    market: Any,
    options: Dict[str, Any],
) -> None:
    """
    Main entry point. Called by cli/main.py.

    options = the 'strategy_discovery:' block from report.yaml.

    Attaches results to:
      pkg.metadata["derived"]["strategy_discovery"]["regime_summary"]   — daily regime table (JSON-safe)
      pkg.metadata["derived"]["strategy_discovery"]["mae_mfe_profile"]  — MAE/MFE bounds (JSON-safe)
      pkg.metadata["derived"]["strategy_discovery"]["validation"]       — validation results (JSON-safe)
      pkg.assets["strategy_discovery"]["bars_with_regime"]              — bars DataFrame with regime columns
    """
    # Guard: only run when explicitly enabled
    if not options.get("enabled"):
        return

    instrument = str(options.get("instrument") or "").strip()
    contract = str(options.get("contract") or "").strip()
    timeframe = str(options.get("timeframe") or "5m").strip()
    regime_cfg = dict(options.get("regime") or {})
    wf_config = {**DEFAULT_WF_CONFIG, **dict(options.get("walk_forward") or {})}
    cost_model = {**DEFAULT_COST_MODEL, **dict(options.get("cost_model") or {})}
    tick_value = float(cost_model.get("tick_value", 5.0))
    # tick_size is not in cost_model spec but is needed for mae_mfe — default to 0.25 (NQ)
    tick_size = float(options.get("tick_size", 0.25))

    # Phase 0: Load bars and compute regime labels
    bars_with_regime: Optional[pd.DataFrame] = None
    regime_summary_records: list = []
    regime_issues: List[str] = []

    if instrument and contract:
        try:
            raw_bars = market.get_bars(
                instrument_root=instrument,
                contract=contract,
                timeframe=timeframe,
            )
        except Exception as exc:
            raw_bars = None
            regime_issues.append(f"market.get_bars raised: {exc}")

        if raw_bars is not None and len(raw_bars) > 0:
            try:
                bars = raw_bars.sort_values("dt").reset_index(drop=True)
                if "day_id" not in bars.columns:
                    bars["day_id"] = bars["dt"].dt.date.astype(str)

                regime_kwargs = _resolve_regime_kwargs(regime_cfg)
                bars_with_regime = compute_bar_regime(bars, **regime_kwargs)

                regime_summary_df = summarize_daily_regime(bars_with_regime)
                regime_summary_records = _df_to_records(regime_summary_df)
            except Exception as exc:
                regime_issues.append(f"regime computation failed: {exc}")
                bars_with_regime = None
        else:
            regime_issues.append(
                f"no bars returned for {instrument} {contract} @ {timeframe}"
            )
    else:
        regime_issues.append("instrument or contract not configured — regime skipped")

    # Per-package analysis
    for run_id, pkg in (packages or {}).items():
        # Skip internal/system packages
        if str(run_id).startswith("__"):
            continue

        # Ensure derived dict exists
        if not isinstance(getattr(pkg, "metadata", None), dict):
            continue

        derived = pkg.metadata.setdefault("derived", {})
        discovery_block = derived.setdefault("strategy_discovery", {})

        # Regime summary (shared — same bars for all packages)
        discovery_block["regime_summary"] = regime_summary_records
        discovery_block["regime_issues"] = regime_issues

        # MAE/MFE profile
        try:
            trades = getattr(pkg, "trades", None)
            mae_mfe_profile = compute_mae_mfe_profile(
                trades if trades is not None else pd.DataFrame(),
                tick_value=tick_value,
                tick_size=tick_size,
            )
            discovery_block["mae_mfe_profile"] = _make_json_safe(mae_mfe_profile)
        except Exception as exc:
            discovery_block["mae_mfe_profile"] = {"error": str(exc)}

        # Validation
        validation_result: Dict[str, Any] = {}
        try:
            validation_result = run_validation(
                trades if trades is not None else pd.DataFrame(),
                wf_config=wf_config,
                cost_model=cost_model,
            )
            # Strip the cost_normalized DataFrame before storing to metadata
            # (DataFrames must not live in metadata — store only JSON-safe summary)
            validation_safe = {
                k: _make_json_safe(v)
                for k, v in validation_result.items()
                if k != "cost_normalized"
            }
            validation_safe["passed"] = bool(validation_result.get("passed", False))
            discovery_block["validation"] = validation_safe
        except Exception as exc:
            discovery_block["validation"] = {"error": str(exc), "passed": False}

        # Store bars_with_regime in pkg.assets (DataFrame is allowed here)
        if bars_with_regime is not None:
            assets = getattr(pkg, "assets", None)
            if isinstance(assets, dict):
                assets.setdefault("strategy_discovery", {})
                assets["strategy_discovery"]["bars_with_regime"] = bars_with_regime

        # Evaluation
        try:
            from .evaluation import compute_evaluation_metrics, compute_regime_breakdown
            cost_norm_trades = validation_result.get("cost_normalized")
            if cost_norm_trades is not None and len(cost_norm_trades) > 0:
                eval_metrics = compute_evaluation_metrics(cost_norm_trades)
                # regime breakdown requires bars_with_regime
                if bars_with_regime is not None:
                    eval_metrics["by_regime"] = compute_regime_breakdown(cost_norm_trades, bars_with_regime)
                discovery_block["evaluation"] = _make_json_safe(eval_metrics)
            else:
                discovery_block["evaluation"] = {"error": "no cost-normalized trades available"}
        except Exception as exc:
            discovery_block["evaluation"] = {"error": str(exc)}

        # Exit Policy Discovery
        try:
            from .exit_discovery import run_exit_discovery
            ed_options = dict(options.get("exit_discovery") or {})
            ed_options.setdefault("enabled", True)
            ed_options.setdefault("tick_size", tick_size)
            ed_options.setdefault("tick_value", tick_value)
            ed_options.setdefault("atr_tf", timeframe)
            exit_result = run_exit_discovery(
                pkg=pkg,
                market=market,
                options=ed_options,
                bars_with_regime=bars_with_regime,
            )
            discovery_block["exit_discovery"] = exit_result
        except Exception as exc:
            discovery_block["exit_discovery"] = {"error": str(exc)}

        # Feature matrix (store in assets, not metadata — it's a DataFrame)
        try:
            from .features import build_feature_matrix
            audit_df = None
            pkg_assets = getattr(pkg, "assets", {}) or {}
            audit_assets = pkg_assets.get("trade_pattern_audit") or {}
            if isinstance(audit_assets, dict):
                audit_df = audit_assets.get("audit_df")

            # Phase 2b: build signal_feature_df from pattern engine artifacts
            signal_feature_df: Optional[pd.DataFrame] = None
            bridge_options = dict(options.get("entry_pattern_bridge") or {})
            bridge_options.setdefault("enabled", True)
            if bridge_options.get("enabled"):
                try:
                    from .entry_pattern_bridge import build_signal_feature_matrix
                    pe_store = pkg_assets.get("pattern_engine") or {}
                    if isinstance(pe_store, dict):
                        pe_signals = pe_store.get("signals")
                        pe_outcomes = pe_store.get("outcomes")
                        pe_stats = pe_store.get("pattern_stats")
                        pe_patterns = pe_store.get("patterns")
                        if (
                            isinstance(pe_signals, pd.DataFrame)
                            and len(pe_signals) > 0
                        ):
                            signal_feature_df = build_signal_feature_matrix(
                                signals_df=pe_signals,
                                outcomes_df=pe_outcomes if isinstance(pe_outcomes, pd.DataFrame) else pd.DataFrame(),
                                pattern_stats_df=pe_stats if isinstance(pe_stats, pd.DataFrame) else pd.DataFrame(),
                                bars_with_regime=bars_with_regime,
                                options=bridge_options,
                                patterns_df=pe_patterns if isinstance(pe_patterns, pd.DataFrame) else None,
                            )
                            discovery_block["n_signal_corpus"] = int(len(pe_signals))
                except Exception as exc:
                    discovery_block["entry_pattern_bridge"] = {"error": str(exc)}

            feature_df = build_feature_matrix(
                trades if trades is not None else pd.DataFrame(),
                bars_with_regime=bars_with_regime,
                audit_df=audit_df if isinstance(audit_df, pd.DataFrame) else None,
                signal_feature_df=signal_feature_df,
            )
            if isinstance(pkg_assets, dict):
                pkg_assets.setdefault("strategy_discovery", {})
                pkg_assets["strategy_discovery"]["feature_matrix"] = feature_df
                if signal_feature_df is not None:
                    pkg_assets["strategy_discovery"]["signal_feature_matrix"] = signal_feature_df
        except Exception:
            pass  # feature matrix is optional — don't fail the whole pipeline

        # Signal Entry Discovery (pure corpus-based, no executed trades required)
        try:
            pkg_assets_sed = getattr(pkg, "assets", {}) or {}
            sd_assets_sed = pkg_assets_sed.get("strategy_discovery") or {}
            sfdf = sd_assets_sed.get("signal_feature_matrix") if isinstance(sd_assets_sed, dict) else None
            if sfdf is not None and isinstance(sfdf, pd.DataFrame) and len(sfdf) > 0:
                from .signal_entry_discovery import run_signal_entry_discovery
                sed_options = dict(options.get("signal_entry_discovery") or {})
                sed_options.setdefault("enabled", True)
                discovery_block["signal_entry_discovery"] = run_signal_entry_discovery(
                    signal_feature_df=sfdf,
                    options=sed_options,
                )
        except Exception as exc:
            discovery_block["signal_entry_discovery"] = {"error": str(exc)}

        # Feature importance (reads feature_matrix from assets)
        try:
            from .importance import compute_feature_importance
            feature_df = None
            pkg_assets2 = getattr(pkg, "assets", {}) or {}
            sd_assets = pkg_assets2.get("strategy_discovery") or {}
            if isinstance(sd_assets, dict):
                feature_df = sd_assets.get("feature_matrix")

            cost_norm = validation_result.get("cost_normalized") if isinstance(validation_result, dict) else None
            source_df = (
                cost_norm if cost_norm is not None and len(cost_norm) > 0
                else (trades if trades is not None else pd.DataFrame())
            )
            profit_col = "profit_net" if (cost_norm is not None and "profit_net" in (cost_norm.columns if hasattr(cost_norm, "columns") else [])) else "profit"

            if feature_df is not None and isinstance(feature_df, pd.DataFrame) and len(feature_df) > 0:
                importance_df = feature_df
            else:
                importance_df = source_df

            imp_result = compute_feature_importance(importance_df, profit_col=profit_col)
            discovery_block["importance"] = imp_result
        except Exception as exc:
            discovery_block["importance"] = {"error": str(exc)}

        # Classification
        try:
            from .classification import classify_strategy
            discovery_block["classification"] = classify_strategy(pkg)
        except Exception as exc:
            discovery_block["classification"] = {"error": str(exc)}

        # Entry Discovery
        try:
            from .entry_discovery import run_entry_discovery
            entry_options = dict(options.get("entry_discovery") or {})
            entry_options.setdefault("enabled", True)
            entry_options.setdefault("profit_col", "profit_net" if "profit_net" in (
                (validation_result.get("cost_normalized").columns
                 if isinstance(validation_result, dict)
                 and validation_result.get("cost_normalized") is not None
                 and hasattr(validation_result.get("cost_normalized"), "columns")
                 else [])
            ) else "profit")
            # Use cost-normalized feature_df if available (has profit_net column)
            cost_norm = validation_result.get("cost_normalized") if isinstance(validation_result, dict) else None
            pkg_assets_ed = getattr(pkg, "assets", {}) or {}
            sd_assets_ed = pkg_assets_ed.get("strategy_discovery") or {}
            feat_df_ed = sd_assets_ed.get("feature_matrix") if isinstance(sd_assets_ed, dict) else None
            # Merge profit_net onto feature_df if both exist
            if (
                feat_df_ed is not None
                and isinstance(feat_df_ed, pd.DataFrame)
                and cost_norm is not None
                and isinstance(cost_norm, pd.DataFrame)
                and "profit_net" in cost_norm.columns
            ):
                try:
                    join_col = next(
                        (c for c in ["trade_id"] if c in feat_df_ed.columns and c in cost_norm.columns),
                        None
                    )
                    if join_col:
                        feat_df_ed = feat_df_ed.merge(
                            cost_norm[[join_col, "profit_net"]], on=join_col, how="left", suffixes=("", "_cn")
                        )
                    elif len(feat_df_ed) == len(cost_norm):
                        feat_df_ed = feat_df_ed.copy()
                        feat_df_ed["profit_net"] = cost_norm["profit_net"].values
                except Exception:
                    pass
            discovery_block["entry_discovery"] = run_entry_discovery(
                pkg=pkg,
                options=entry_options,
                feature_df=feat_df_ed,
                bars_with_regime=bars_with_regime,
            )
        except Exception as exc:
            discovery_block["entry_discovery"] = {"error": str(exc)}

        # Filter Discovery
        try:
            from .filter_discovery import run_filter_discovery
            fd_options = dict(options.get("filter_discovery") or {})
            fd_options.setdefault("enabled", True)
            fd_options.setdefault("profit_col", entry_options.get("profit_col", "profit_net"))
            discovery_block["filter_discovery"] = run_filter_discovery(
                pkg=pkg,
                options=fd_options,
                feature_df=feat_df_ed,
                bars_with_regime=bars_with_regime,
            )
        except Exception as exc:
            discovery_block["filter_discovery"] = {"error": str(exc)}

        # Position Sizing
        try:
            from .position_sizing import run_position_sizing
            ps_options = dict(options.get("position_sizing") or {})
            ps_options.setdefault("enabled", True)
            ps_options.setdefault("profit_col", entry_options.get("profit_col", "profit_net"))
            discovery_block["position_sizing"] = run_position_sizing(
                pkg=pkg,
                options=ps_options,
                feature_df=feat_df_ed,
            )
        except Exception as exc:
            discovery_block["position_sizing"] = {"error": str(exc)}

        # Cohort Analysis
        try:
            from .cohort_analysis import run_cohort_analysis
            ca_options = dict(options.get("cohort_analysis") or {})
            ca_options.setdefault("enabled", True)
            ca_options.setdefault("profit_col", entry_options.get("profit_col", "profit_net"))
            discovery_block["cohort_analysis"] = run_cohort_analysis(
                pkg=pkg,
                options=ca_options,
                profit_col=ca_options["profit_col"],
                bars_with_regime=bars_with_regime,
            )
        except Exception as exc:
            discovery_block["cohort_analysis"] = {"error": str(exc)}

        # Drawdown Analysis
        try:
            from .drawdown_analysis import run_drawdown_analysis
            da_options = dict(options.get("drawdown_analysis") or {})
            da_options.setdefault("enabled", True)
            da_options.setdefault("profit_col", entry_options.get("profit_col", "profit_net"))
            discovery_block["drawdown_analysis"] = run_drawdown_analysis(
                pkg=pkg,
                options=da_options,
                profit_col=da_options["profit_col"],
                bars_with_regime=bars_with_regime,
            )
        except Exception as exc:
            discovery_block["drawdown_analysis"] = {"error": str(exc)}

        # Risk Metrics
        try:
            from .risk_metrics import run_risk_metrics
            rm_options = dict(options.get("risk_metrics") or {})
            rm_options.setdefault("enabled", True)
            rm_options.setdefault("profit_col", entry_options.get("profit_col", "profit_net"))
            discovery_block["risk_metrics"] = run_risk_metrics(
                pkg=pkg,
                options=rm_options,
                profit_col=rm_options["profit_col"],
            )
        except Exception as exc:
            discovery_block["risk_metrics"] = {"error": str(exc)}

        # Pure Discovery (market-data-first candidate generation)
        try:
            from .pure_discovery import run_pure_discovery
            pd_options = dict(options.get("pure_discovery") or {})
            pd_options.setdefault("enabled", False)
            discovery_block["pure_discovery"] = run_pure_discovery(
                pkg=pkg,
                bars_with_regime=bars_with_regime,
                options=pd_options,
                wf_config=wf_config,
                cost_model=cost_model,
                tick_size=tick_size,
            )
        except Exception as exc:
            discovery_block["pure_discovery"] = {"error": str(exc), "enabled": False}

        # Parameter Sensitivity
        try:
            from .parameter_sensitivity import run_parameter_sensitivity
            ps_options = dict(options.get("parameter_sensitivity") or {})
            ps_options.setdefault("enabled", True)
            ps_options.setdefault("profit_col", entry_options.get("profit_col", "profit_net"))
            discovery_block["parameter_sensitivity"] = run_parameter_sensitivity(
                pkg=pkg,
                options=ps_options,
                feature_df=feat_df_ed,
            )
        except Exception as exc:
            discovery_block["parameter_sensitivity"] = {"error": str(exc)}

    # -----------------------------------------------------------------------
    # Ranking — runs after all per-package analysis is complete
    # -----------------------------------------------------------------------
    try:
        from .ranking import run_ranking
        run_ranking(packages)  # attaches per-run scores + cross_run_ranking to packages internally
    except Exception as exc:
        import traceback as _tb
        print(f"[ta_foundation] WARNING ranking failed: {exc}")
        _tb.print_exc()

    # -----------------------------------------------------------------------
    # Clustering — groups behaviorally similar strategies after ranking
    # -----------------------------------------------------------------------
    try:
        from .clustering import run_clustering
        cluster_threshold = float(options.get("cluster_distance_threshold", 1.5))
        run_clustering(packages, distance_threshold=cluster_threshold)
    except Exception as exc:
        import traceback as _tb
        print(f"[ta_foundation] WARNING clustering failed: {exc}")
        _tb.print_exc()

    # -----------------------------------------------------------------------
    # Market Corpus Signal Entry Discovery
    # Processes synthetic __market_discovery__ packages from the pattern engine.
    # These packages have no trade data — the entire point is to find NEW entry
    # signals from the raw market signal corpus, completely independent of any
    # existing strategy's executed trades.
    # -----------------------------------------------------------------------
    sed_options = dict(options.get("signal_entry_discovery") or {})
    sed_options.setdefault("enabled", True)
    bridge_options = dict(options.get("entry_pattern_bridge") or {})
    bridge_options.setdefault("enabled", True)

    for run_id, pkg in (packages or {}).items():
        if not str(run_id).startswith("__market_discovery__"):
            continue
        if not isinstance(getattr(pkg, "metadata", None), dict):
            continue

        try:
            derived = pkg.metadata.setdefault("derived", {})
            corpus_block = derived.setdefault("strategy_discovery", {})

            pe_store = (getattr(pkg, "assets", {}) or {}).get("pattern_engine") or {}
            pe_signals = pe_store.get("signals")
            pe_outcomes = pe_store.get("outcomes")
            pe_stats = pe_store.get("pattern_stats")
            pe_patterns = pe_store.get("patterns")

            if not (isinstance(pe_signals, pd.DataFrame) and len(pe_signals) > 0):
                corpus_block["signal_entry_discovery"] = {
                    "skipped": True, "reason": "no signal corpus in market_discovery package",
                }
                continue

            from .entry_pattern_bridge import build_signal_feature_matrix
            from .signal_entry_discovery import run_signal_entry_discovery

            signal_feature_df = build_signal_feature_matrix(
                signals_df=pe_signals,
                outcomes_df=pe_outcomes if isinstance(pe_outcomes, pd.DataFrame) else pd.DataFrame(),
                pattern_stats_df=pe_stats if isinstance(pe_stats, pd.DataFrame) else pd.DataFrame(),
                bars_with_regime=bars_with_regime,
                options=bridge_options,
                patterns_df=pe_patterns if isinstance(pe_patterns, pd.DataFrame) else None,
            )

            corpus_block["signal_entry_discovery"] = run_signal_entry_discovery(
                signal_feature_df=signal_feature_df,
                options=sed_options,
            )

            # Signal rule walk-forward validation
            sv_options = dict(options.get("signal_validation") or {})
            sv_options.setdefault("enabled", True)
            sv_options.setdefault("profit_col", "ret_ticks")
            try:
                from .signal_validation import run_signal_validation
                corpus_block["signal_validation"] = run_signal_validation(
                    signal_feature_df=signal_feature_df,
                    options=sv_options,
                )
            except Exception as sv_exc:
                corpus_block["signal_validation"] = {"error": str(sv_exc)}

            # Signal corpus exit sweep — find best stop/target for each signal rule
            ses_options = dict(options.get("signal_exit_sweep") or {})
            ses_options.setdefault("enabled", True)
            exit_sweep_result: Dict[str, Any] = {}
            try:
                from .signal_exit_sweep import run_signal_exit_sweep
                sed_result = corpus_block.get("signal_entry_discovery") or {}
                signal_rules = sed_result.get("top_signal_rules") or []
                exit_sweep_result = run_signal_exit_sweep(
                    signal_feature_df=signal_feature_df,
                    signal_rules=signal_rules,
                    options=ses_options,
                )
                corpus_block["signal_exit_sweep"] = exit_sweep_result
            except Exception as ses_exc:
                corpus_block["signal_exit_sweep"] = {"error": str(ses_exc)}

            # Signal corpus simulation — equity curve per rule using optimised exits
            scs_options = dict(options.get("signal_corpus_simulation") or {})
            scs_options.setdefault("enabled", True)
            try:
                from .signal_corpus_simulation import run_signal_corpus_simulation
                sed_result2 = corpus_block.get("signal_entry_discovery") or {}
                sim_rules = sed_result2.get("top_signal_rules") or []
                corpus_block["signal_corpus_simulation"] = run_signal_corpus_simulation(
                    signal_feature_df=signal_feature_df,
                    signal_rules=sim_rules,
                    exit_sweep=exit_sweep_result,
                    options=scs_options,
                )
            except Exception as scs_exc:
                corpus_block["signal_corpus_simulation"] = {"error": str(scs_exc)}

            # Cache feature matrix for downstream use (NT template, etc.)
            if not hasattr(pkg, "assets") or pkg.assets is None:
                pkg.assets = {}
            pkg.assets.setdefault("strategy_discovery", {})
            pkg.assets["strategy_discovery"]["signal_feature_matrix"] = signal_feature_df

        except Exception as exc:
            import traceback as _tb
            pkg.metadata.setdefault("derived", {}).setdefault("strategy_discovery", {})[
                "signal_entry_discovery"
            ] = {"error": str(exc)}
            _tb.print_exc()
