from __future__ import annotations

"""
Entry Pattern Bridge
====================
Integrates Pattern Engine signals into Strategy Discovery as first-class entry candidates.

Produces a ``signal_feature_df`` (one row per pattern signal firing) that enables
the signal entry discovery module to discover entry rules from the full signal corpus —
including the unexecuted signals that trade-anchored discovery never sees.

Public API
----------
  build_signal_feature_matrix(signals_df, outcomes_df, pattern_stats_df, bars_with_regime, options, patterns_df)
      -> signal_feature_df  (one row per signal firing, enriched with market context)

  match_trades_to_signals(trades, signals_df, options, bars_with_regime)
      -> trades with matched_signal_id / corpus columns added

  discover_entry_patterns(signal_feature_df, trades_feature_df, options)
      -> {top_rules, corpus_rules, execution_rules, hybrid_rules, baseline, diagnostics}
"""

from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from ta_foundation.analysis.session_constants import session_label_from_hour


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_BRIDGE_OPTIONS: Dict[str, Any] = {
    "enabled": True,
    "primary_horizon": 20,          # outcome horizon bars to use as primary target
    "match_window_bars": 3,         # trade matched to signal if within N bars of entry
    "mode": "hybrid",               # trade_anchored | corpus | hybrid
    "hybrid_weights": {
        "corpus_win_rate": 0.45,
        "execution_agreement": 0.35,
        "selectivity": 0.20,
    },
    "min_signals": 20,              # min signal firings for a rule to qualify
    "bar_duration_minutes": 5,      # assumed bar duration for time-proximity matching
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

# session_label_from_hour imported from session_constants


def _tz_to_naive_utc(s: pd.Series) -> pd.Series:
    s = pd.to_datetime(s)
    if s.dt.tz is not None:
        s = s.dt.tz_convert("UTC").dt.tz_localize(None)
    return s


# ---------------------------------------------------------------------------
# build_signal_feature_matrix
# ---------------------------------------------------------------------------

def build_signal_feature_matrix(
    signals_df: pd.DataFrame,
    outcomes_df: pd.DataFrame,
    pattern_stats_df: pd.DataFrame,
    bars_with_regime: Optional[pd.DataFrame],
    options: Dict[str, Any],
    patterns_df: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """
    Produce a feature matrix where EACH ROW IS A PATTERN SIGNAL FIRING.

    Unlike the existing feature_df (one row per executed trade), this has one
    row per signal — including signals that had no corresponding executed trade.

    Parameters
    ----------
    signals_df        : output of pattern engine sweep (engine.py signals_df)
    outcomes_df       : forward outcome rows (signal_id, horizon, ret_ticks, mfe_ticks, mae_ticks)
    pattern_stats_df  : per-pattern aggregated stats (pattern_id, horizon, n_signals, win_rate, avg_ticks)
    bars_with_regime  : bars DataFrame with regime/adx/atr from strategy_discovery.regime
    options           : entry_pattern_bridge config block
    patterns_df       : optional patterns DataFrame with family/structure per pattern_id

    Returns
    -------
    pd.DataFrame with one row per signal firing and market-context columns.
    """
    opts: Dict[str, Any] = {**DEFAULT_BRIDGE_OPTIONS, **(options or {})}
    primary_horizon = int(opts.get("primary_horizon", 20))

    if signals_df is None or not isinstance(signals_df, pd.DataFrame) or len(signals_df) == 0:
        return pd.DataFrame()

    result = signals_df.copy()

    # ------------------------------------------------------------------
    # Step 1: Add family / structure from patterns_df (lookup by pattern_id)
    # ------------------------------------------------------------------
    if patterns_df is not None and isinstance(patterns_df, pd.DataFrame) and len(patterns_df) > 0:
        if "pattern_id" in patterns_df.columns:
            pat_add_cols = [c for c in ["family", "structure"] if c in patterns_df.columns]
            if pat_add_cols:
                # Drop any pre-existing family/structure to avoid merge conflicts
                for c in pat_add_cols:
                    if c in result.columns:
                        result = result.drop(columns=[c])
                merge_cols = ["pattern_id"] + pat_add_cols
                pat_lookup = patterns_df[merge_cols].drop_duplicates("pattern_id")
                result = result.merge(pat_lookup, on="pattern_id", how="left")

    if "family" not in result.columns:
        result["family"] = "UNKNOWN"
    if "structure" not in result.columns:
        result["structure"] = "UNKNOWN"

    # ------------------------------------------------------------------
    # Step 2: Join outcomes for the primary horizon
    # ------------------------------------------------------------------
    if (
        outcomes_df is not None
        and isinstance(outcomes_df, pd.DataFrame)
        and len(outcomes_df) > 0
        and "signal_id" in outcomes_df.columns
        and "horizon" in outcomes_df.columns
    ):
        oc_primary = outcomes_df[outcomes_df["horizon"] == primary_horizon].copy()
        if len(oc_primary) == 0:
            # Fallback: use the horizon closest to primary_horizon
            available_horizons = outcomes_df["horizon"].dropna().unique()
            if len(available_horizons) > 0:
                best_h = min(available_horizons, key=lambda h: abs(int(h) - primary_horizon))
                oc_primary = outcomes_df[outcomes_df["horizon"] == best_h].copy()

        if len(oc_primary) > 0:
            # Engine stores outcome as pnl_ticks; normalise to ret_ticks
            if "pnl_ticks" in oc_primary.columns and "ret_ticks" not in oc_primary.columns:
                oc_primary = oc_primary.rename(columns={"pnl_ticks": "ret_ticks"})

            oc_cols = ["signal_id"]
            for c in ["ret_ticks", "mfe_ticks", "mae_ticks", "vol_regime", "horizon"]:
                if c in oc_primary.columns:
                    oc_cols.append(c)
            oc_primary = oc_primary[oc_cols].drop_duplicates(subset=["signal_id"])
            # Drop any of these columns already in result to avoid conflicts
            for c in oc_cols:
                if c != "signal_id" and c in result.columns:
                    result = result.drop(columns=[c])
            result = result.merge(oc_primary, on="signal_id", how="left")

    for c in ["ret_ticks", "mfe_ticks", "mae_ticks"]:
        if c not in result.columns:
            result[c] = np.nan
    if "horizon" not in result.columns:
        result["horizon"] = primary_horizon

    result["is_winner"] = pd.to_numeric(result["ret_ticks"], errors="coerce") > 0

    # ------------------------------------------------------------------
    # Step 3: Join ADX / ATR from bars_with_regime via backward merge_asof
    # ------------------------------------------------------------------
    if (
        bars_with_regime is not None
        and isinstance(bars_with_regime, pd.DataFrame)
        and len(bars_with_regime) > 0
        and "dt" in bars_with_regime.columns
        and "dt" in result.columns
    ):
        try:
            bar_cols_to_add = [
                c for c in ["adx", "atr", "trend_direction"]
                if c in bars_with_regime.columns and c not in result.columns
            ]
            if bar_cols_to_add:
                signals_dt_utc = _tz_to_naive_utc(pd.to_datetime(result["dt"]))
                bars_dt_utc = _tz_to_naive_utc(pd.to_datetime(bars_with_regime["dt"]))

                bars_for_merge = bars_with_regime[bar_cols_to_add].copy()
                bars_for_merge["_dt_utc"] = bars_dt_utc.values
                bars_for_merge = bars_for_merge.sort_values("_dt_utc").reset_index(drop=True)

                sig_for_merge = pd.DataFrame({"_sig_utc": signals_dt_utc.values}, index=result.index)
                sig_for_merge = sig_for_merge.sort_values("_sig_utc")

                merged = pd.merge_asof(
                    sig_for_merge,
                    bars_for_merge,
                    left_on="_sig_utc",
                    right_on="_dt_utc",
                    direction="backward",
                )
                merged.index = sig_for_merge.index
                merged = merged.reindex(result.index)

                for col in bar_cols_to_add:
                    if col in merged.columns:
                        result[col] = merged[col].values
        except Exception:
            pass  # Best-effort

    for c in ["adx", "atr"]:
        if c not in result.columns:
            result[c] = np.nan

    # ------------------------------------------------------------------
    # Step 4: Add corpus stats from pattern_stats_df
    # ------------------------------------------------------------------
    if (
        pattern_stats_df is not None
        and isinstance(pattern_stats_df, pd.DataFrame)
        and len(pattern_stats_df) > 0
        and "pattern_id" in pattern_stats_df.columns
    ):
        try:
            if "horizon" in pattern_stats_df.columns:
                stats_primary = pattern_stats_df[
                    pattern_stats_df["horizon"] == primary_horizon
                ].copy()
                if len(stats_primary) == 0:
                    stats_primary = (
                        pattern_stats_df
                        .sort_values("horizon")
                        .drop_duplicates("pattern_id")
                        .copy()
                    )
            else:
                stats_primary = pattern_stats_df.copy()

            col_rename: Dict[str, str] = {
                "n_signals": "corpus_n",
                "win_rate": "corpus_win_rate",
                "avg_ticks": "corpus_avg_ticks",
                "p50": "corpus_mfe_p50",
            }
            stat_add = ["pattern_id"]
            for orig_c in col_rename:
                if orig_c in stats_primary.columns:
                    stat_add.append(orig_c)

            if len(stat_add) > 1:
                stats_subset = (
                    stats_primary[stat_add]
                    .drop_duplicates("pattern_id")
                    .rename(columns=col_rename)
                )
                # Drop any corpus columns already in result
                for c in stats_subset.columns:
                    if c != "pattern_id" and c in result.columns:
                        result = result.drop(columns=[c])
                result = result.merge(stats_subset, on="pattern_id", how="left")
        except Exception:
            pass

    for c in ["corpus_n", "corpus_win_rate", "corpus_avg_ticks"]:
        if c not in result.columns:
            result[c] = np.nan

    # ------------------------------------------------------------------
    # Step 5: Compute session_label from dt
    # ------------------------------------------------------------------
    if "session_label" not in result.columns and "dt" in result.columns:
        try:
            dt_series = pd.to_datetime(result["dt"])
            if dt_series.dt.tz is not None:
                local_dt = dt_series.dt.tz_convert("America/Denver")
            else:
                local_dt = dt_series
            result["session_label"] = local_dt.dt.hour.apply(session_label_from_hour)
        except Exception:
            result["session_label"] = "other"

    # ------------------------------------------------------------------
    # Step 6: Ensure vol_regime present
    # ------------------------------------------------------------------
    if "vol_regime" not in result.columns:
        result["vol_regime"] = "normal_vol"

    # ------------------------------------------------------------------
    # Step 7: has_executed_trade placeholder (populated by match step later)
    # ------------------------------------------------------------------
    if "has_executed_trade" not in result.columns:
        result["has_executed_trade"] = False
    if "trade_profit" not in result.columns:
        result["trade_profit"] = np.nan

    return result


# ---------------------------------------------------------------------------
# build_candidate_scorecard
# ---------------------------------------------------------------------------

def build_candidate_scorecard(
    pkg,
    session_risk_df: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """
    Join all pattern-engine evidence into one candidate row per
    (pattern_id, horizon).

    Sources consumed from ``pkg.assets["pattern_engine"]``:
      pattern_stats       base metrics (n_signals, win_rate, avg_ticks, …)
      discovery_stability fold stability (stability_score, sign_consistency, …)
      oos_stats           cross-validation OOS stability
      mc_summary          Monte Carlo summary (survival_rate, pass_rate, …)
      mc_regime_summary   regime-sliced MC results
      trade_pattern_audit per-pattern audit confirmation rates

    Optional ``session_risk_df`` (from market_regime_store.summarize_entry_hour_risk)
    adds session-level edge and danger scores as penalty/bonus columns.

    Returns a DataFrame with one row per (pattern_id, horizon).
    Returns an empty DataFrame if no pattern_stats are available.
    """
    pkg_assets = getattr(pkg, "assets", {}) or {}
    pe_store = pkg_assets.get("pattern_engine") or {}

    # ---- Base: pattern_stats ----
    pattern_stats = pe_store.get("pattern_stats")
    if not isinstance(pattern_stats, pd.DataFrame) or len(pattern_stats) == 0:
        return pd.DataFrame()

    scorecard = pattern_stats.copy()

    # Ensure we have the key join columns
    if "pattern_id" not in scorecard.columns:
        return pd.DataFrame()

    # ---- discovery_stability: fold sign-consistency ----
    discovery_stability = pe_store.get("discovery_stability")
    if isinstance(discovery_stability, pd.DataFrame) and len(discovery_stability) > 0:
        try:
            stab_cols = ["pattern_id"] + [
                c for c in discovery_stability.columns
                if c not in ("pattern_id",) and c not in scorecard.columns
            ]
            stab_cols = [c for c in stab_cols if c in discovery_stability.columns]
            if len(stab_cols) > 1:
                stab_sub = discovery_stability[stab_cols].drop_duplicates("pattern_id")
                scorecard = scorecard.merge(stab_sub, on="pattern_id", how="left")
        except Exception:
            pass

    # ---- oos_stats: OOS cross-validation metrics ----
    oos_stats = pe_store.get("oos_stats")
    if isinstance(oos_stats, pd.DataFrame) and len(oos_stats) > 0:
        try:
            oos_cols = ["pattern_id"] + [
                c for c in oos_stats.columns
                if c != "pattern_id" and c not in scorecard.columns
            ]
            oos_cols = [c for c in oos_cols if c in oos_stats.columns]
            if len(oos_cols) > 1:
                oos_sub = oos_stats[oos_cols].drop_duplicates("pattern_id")
                scorecard = scorecard.merge(oos_sub, on="pattern_id", how="left")
        except Exception:
            pass
    elif isinstance(oos_stats, dict) and oos_stats:
        # Scalar summary — broadcast to all rows
        for k, v in oos_stats.items():
            col = f"oos_{k}"
            if col not in scorecard.columns:
                try:
                    scorecard[col] = v
                except Exception:
                    pass

    # ---- mc_summary: baseline MC survival rate ----
    mc_summary = pe_store.get("mc_summary")
    if isinstance(mc_summary, pd.DataFrame) and len(mc_summary) > 0:
        try:
            mc_cols = ["pattern_id"] + [
                c for c in mc_summary.columns
                if c != "pattern_id" and c not in scorecard.columns
            ]
            mc_cols = [c for c in mc_cols if c in mc_summary.columns]
            if len(mc_cols) > 1:
                mc_sub = mc_summary[mc_cols].drop_duplicates("pattern_id")
                scorecard = scorecard.merge(mc_sub, on="pattern_id", how="left")
        except Exception:
            pass
    elif isinstance(mc_summary, dict) and mc_summary:
        for k, v in mc_summary.items():
            col = f"mc_{k}"
            if col not in scorecard.columns:
                try:
                    scorecard[col] = v
                except Exception:
                    pass

    # ---- mc_regime_summary: regime-sliced MC ----
    mc_regime = pe_store.get("mc_regime_summary")
    if isinstance(mc_regime, pd.DataFrame) and len(mc_regime) > 0:
        try:
            regime_cols = ["pattern_id"] + [
                c for c in mc_regime.columns
                if c != "pattern_id" and c not in scorecard.columns
            ]
            regime_cols = [c for c in regime_cols if c in mc_regime.columns]
            if len(regime_cols) > 1:
                # Aggregate by pattern_id (mean across regimes)
                mc_r_agg = mc_regime[regime_cols].groupby("pattern_id").mean().reset_index()
                mc_r_agg.columns = [
                    f"mc_regime_{c}" if c != "pattern_id" else c
                    for c in mc_r_agg.columns
                ]
                scorecard = scorecard.merge(mc_r_agg, on="pattern_id", how="left")
        except Exception:
            pass

    # ---- trade_pattern_audit: per-pattern confirmation rate ----
    audit_store = pkg_assets.get("trade_pattern_audit") or {}
    audit_df = audit_store.get("audit_df") if isinstance(audit_store, dict) else None
    if isinstance(audit_df, pd.DataFrame) and len(audit_df) > 0:
        try:
            if "pattern_id" in audit_df.columns:
                agg_cols: Dict[str, Any] = {}
                if "confirmed" in audit_df.columns:
                    agg_cols["audit_confirmation_rate"] = pd.NamedAgg(
                        column="confirmed", aggfunc=lambda x: pd.to_numeric(x, errors="coerce").mean()
                    )
                if "confirming_score" in audit_df.columns:
                    agg_cols["audit_avg_score"] = pd.NamedAgg(
                        column="confirming_score", aggfunc=lambda x: pd.to_numeric(x, errors="coerce").mean()
                    )
                if "n_signals" in audit_df.columns:
                    agg_cols["audit_n_signals"] = pd.NamedAgg(
                        column="n_signals", aggfunc="sum"
                    )
                if agg_cols:
                    audit_agg = audit_df.groupby("pattern_id").agg(**agg_cols).reset_index()
                    scorecard = scorecard.merge(audit_agg, on="pattern_id", how="left")
        except Exception:
            pass

    # ---- session_risk_df: session edge/danger scores ----
    if isinstance(session_risk_df, pd.DataFrame) and len(session_risk_df) > 0:
        try:
            # Compute a single session edge score and concentration metric
            # from the hour-level risk summary
            if "entry_hour" in session_risk_df.columns:
                pnl_col = next(
                    (c for c in ["shrunk_avg_pnl", "avg_pnl", "pnl"] if c in session_risk_df.columns),
                    None,
                )
                if pnl_col:
                    positive_hours = (
                        pd.to_numeric(session_risk_df[pnl_col], errors="coerce") > 0
                    ).sum()
                    total_hours = len(session_risk_df)
                    edge_score = float(positive_hours / total_hours) if total_hours > 0 else np.nan
                    scorecard["session_edge_score"] = edge_score

                n_col = next(
                    (c for c in ["n_trades", "n"] if c in session_risk_df.columns),
                    None,
                )
                if n_col:
                    counts = pd.to_numeric(session_risk_df[n_col], errors="coerce").fillna(0)
                    total = counts.sum()
                    if total > 0:
                        session_concentration = float(counts.max() / total)
                        scorecard["session_concentration"] = session_concentration
        except Exception:
            pass

    # Guarantee a clean index
    scorecard = scorecard.reset_index(drop=True)
    return scorecard


# ---------------------------------------------------------------------------
# match_trades_to_signals
# ---------------------------------------------------------------------------

def match_trades_to_signals(
    trades: pd.DataFrame,
    signals_df: pd.DataFrame,
    options: Dict[str, Any],
    bars_with_regime: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """
    Match executed trades to pattern signals by time proximity.

    A trade at time T matches a signal at time S if:
      |T - S| <= match_window_bars * bar_duration_minutes
      direction matches (if both have direction columns)

    Returns trades with new columns:
      matched_signal_id          : closest matching signal_id, or NaN
      matched_pattern_id         : pattern_id of matched signal
      matched_family             : family of matched pattern
      matched_structure          : structure of matched pattern
      signal_corpus_win_rate     : pattern's overall corpus win_rate
      signal_corpus_avg_ticks    : pattern's overall corpus avg_ticks
      match_quality              : "exact" / "close" / "none"
    """
    opts: Dict[str, Any] = {**DEFAULT_BRIDGE_OPTIONS, **(options or {})}
    match_window_bars = int(opts.get("match_window_bars", 3))
    bar_duration_minutes = int(opts.get("bar_duration_minutes", 5))
    window_td = pd.Timedelta(minutes=match_window_bars * bar_duration_minutes)

    result = trades.copy()

    # Initialize match columns with NaN / sentinel
    new_cols = [
        "matched_signal_id", "matched_pattern_id", "matched_family", "matched_structure",
        "signal_corpus_win_rate", "signal_corpus_avg_ticks", "match_quality",
    ]
    for c in new_cols:
        if c not in result.columns:
            result[c] = np.nan

    if (
        signals_df is None
        or not isinstance(signals_df, pd.DataFrame)
        or len(signals_df) == 0
        or "dt" not in signals_df.columns
        or "entry_time" not in trades.columns
    ):
        return result

    try:
        trade_dt = pd.to_datetime(result["entry_time"])
        signal_dt = pd.to_datetime(signals_df["dt"])

        trade_dt_utc = _tz_to_naive_utc(trade_dt)
        signal_dt_utc = _tz_to_naive_utc(signal_dt)

        sig = signals_df.copy()
        sig["_dt_utc"] = signal_dt_utc.values
        sig = sig.sort_values("_dt_utc").reset_index(drop=True)

        for idx in result.index:
            t_dt = trade_dt_utc.loc[idx]
            if pd.isna(t_dt):
                continue

            t_min = t_dt - window_td
            t_max = t_dt + window_td
            nearby = sig[(sig["_dt_utc"] >= t_min) & (sig["_dt_utc"] <= t_max)]

            # Direction filter
            if "direction" in result.columns and "direction" in sig.columns:
                trade_dir = result.loc[idx, "direction"]
                if pd.notna(trade_dir):
                    nearby = nearby[nearby["direction"] == float(trade_dir)]

            if len(nearby) == 0:
                result.at[idx, "match_quality"] = "none"
                continue

            # Pick closest by time
            diffs = (nearby["_dt_utc"] - t_dt).abs()
            best_i = diffs.idxmin()
            best = nearby.loc[best_i]

            result.at[idx, "matched_signal_id"] = str(best.get("signal_id", ""))
            result.at[idx, "matched_pattern_id"] = str(best.get("pattern_id", ""))
            result.at[idx, "matched_family"] = str(best.get("family", ""))
            result.at[idx, "matched_structure"] = str(best.get("structure", ""))

            if "corpus_win_rate" in best.index and pd.notna(best.get("corpus_win_rate")):
                result.at[idx, "signal_corpus_win_rate"] = float(best["corpus_win_rate"])
            if "corpus_avg_ticks" in best.index and pd.notna(best.get("corpus_avg_ticks")):
                result.at[idx, "signal_corpus_avg_ticks"] = float(best["corpus_avg_ticks"])

            diff_min = float(diffs.min().total_seconds() / 60)
            result.at[idx, "match_quality"] = "exact" if diff_min <= bar_duration_minutes else "close"

    except Exception:
        pass

    return result


# ---------------------------------------------------------------------------
# discover_entry_patterns
# ---------------------------------------------------------------------------

def discover_entry_patterns(
    signal_feature_df: pd.DataFrame,
    trades_feature_df: pd.DataFrame,
    options: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Three-mode entry pattern discovery.

    Mode A — trade-anchored:  uses trades_feature_df (existing entry_discovery logic)
    Mode B — corpus:          uses signal_feature_df (all pattern firings)
    Mode C — hybrid:          runs both and merges by hybrid_score
    """
    opts: Dict[str, Any] = {**DEFAULT_BRIDGE_OPTIONS, **(options or {})}
    mode = str(opts.get("mode", "hybrid")).lower()

    result: Dict[str, Any] = {
        "mode": mode,
        "corpus_rules": [],
        "execution_rules": [],
        "hybrid_rules": [],
        "top_rules": [],
        "baseline": {},
        "diagnostics": {"mode": mode},
    }

    # ---- Mode A: trade-anchored ----
    if mode in ("trade_anchored", "hybrid"):
        try:
            from .entry_discovery import run_entry_discovery

            class _StubPkg:
                trades = None
                assets: Dict[str, Any] = {}
                metadata: Dict[str, Any] = {}

            ed_opts = dict(opts.get("entry_discovery") or {})
            ed_opts.setdefault("enabled", True)
            exec_result = run_entry_discovery(
                pkg=_StubPkg(),
                options=ed_opts,
                feature_df=trades_feature_df if isinstance(trades_feature_df, pd.DataFrame) else None,
            )
            result["execution_rules"] = exec_result.get("top_rules") or []
        except Exception as exc:
            result["diagnostics"]["execution_rules_error"] = str(exc)

    # ---- Mode B: signal-corpus ----
    if mode in ("corpus", "hybrid"):
        try:
            from .signal_entry_discovery import run_signal_entry_discovery
            sed_opts = dict(opts.get("signal_entry_discovery") or {})
            sed_opts.setdefault("enabled", True)
            corpus_result = run_signal_entry_discovery(
                signal_feature_df=signal_feature_df,
                options=sed_opts,
            )
            result["corpus_rules"] = corpus_result.get("top_signal_rules") or []
            result["baseline"] = corpus_result.get("baseline") or {}
            result["signal_corpus"] = corpus_result.get("signal_corpus") or {}
        except Exception as exc:
            result["diagnostics"]["corpus_rules_error"] = str(exc)

    # ---- Mode C: hybrid merge ----
    if mode == "hybrid":
        weights = dict(opts.get("hybrid_weights") or DEFAULT_BRIDGE_OPTIONS["hybrid_weights"])
        alpha = float(weights.get("corpus_win_rate", 0.45))
        beta = float(weights.get("execution_agreement", 0.35))
        gamma = float(weights.get("selectivity", 0.20))

        exec_tagged = [{"_source": "execution", **r} for r in result["execution_rules"]]
        corp_tagged = [{"_source": "corpus", **r} for r in result["corpus_rules"]]

        for r in exec_tagged + corp_tagged:
            wr_lift = float(r.get("win_rate_lift") or r.get("wr_lift") or 0.0)
            exec_agree = float(r.get("execution_agreement") or 0.5)
            selectivity = float(r.get("selectivity") or 0.0)
            r["hybrid_score"] = (
                alpha * max(0.0, wr_lift)
                + beta * exec_agree
                + gamma * selectivity
            ) * 100.0

        all_rules = exec_tagged + corp_tagged
        all_rules.sort(key=lambda r: r.get("hybrid_score", 0.0), reverse=True)
        result["hybrid_rules"] = all_rules[:25]
        result["top_rules"] = result["hybrid_rules"]
    elif mode == "trade_anchored":
        result["top_rules"] = result["execution_rules"]
    else:
        result["top_rules"] = result["corpus_rules"]

    return result
