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
from .validation import (
    run_validation,
    DEFAULT_WF_CONFIG,
    DEFAULT_COST_MODEL,
    apply_cost_model,
    extract_oos_pool,
)
from .holdout import partition_trades


# Default holdout configuration. Holdout is ENABLED by default — discovery
# without a locked holdout produces in-sample-fit metrics that cannot be
# trusted for fund-quality evaluation.
DEFAULT_HOLDOUT_CONFIG: Dict[str, Any] = {
    "enabled": True,
    "is_frac": 0.60,           # IS portion of the development slice
    "val_frac": 0.20,          # VAL portion (IS + VAL = 80% dev)
    "time_col": "entry_time",
    "min_dev_trades": 50,      # below this, fall back to no-holdout (insufficient sample)
    "min_holdout_trades": 20,  # below this, holdout eval is skipped (too small to score)
}


# Default dev-slice split for separating entry-search from exit-search.
# Co-fitting entries and exits on the same trades inflates apparent edge by
# an order of magnitude; chopping the dev slice in half so each search sees
# a disjoint sample is the cheapest way to break that joint optimisation.
DEFAULT_DEV_SPLIT_CONFIG: Dict[str, Any] = {
    "enabled": True,
    "entry_frac": 0.50,        # chronological fraction allocated to entry search
    "mode": "chronological",   # 'chronological' | 'interleaved'
    "min_entry_trades": 30,
    "min_exit_trades": 30,
    "time_col": "entry_time",
}


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
# Holdout partitioning
# ---------------------------------------------------------------------------

def _resolve_holdout(
    original_trades: Optional[pd.DataFrame],
    holdout_config: Dict[str, Any],
) -> tuple:
    """
    Decide whether a locked holdout slice can be carved off, and return
    (holdout_meta, dev_trades, holdout_trades).

    When the holdout is active, ``dev_trades`` contains only the IS+VAL
    portion (everything before the holdout boundary), and ``holdout_trades``
    holds the locked slice. Discovery runs on ``dev_trades`` only; the
    holdout is evaluated exactly once at the end of the per-package loop
    via ``compute_evaluation_metrics``.

    When the holdout cannot be applied (disabled, missing time column,
    insufficient sample), ``dev_trades`` is the original trade set and
    ``holdout_trades`` is None.
    """
    enabled = bool(holdout_config.get("enabled", True))
    is_frac = float(holdout_config.get("is_frac", 0.60))
    val_frac = float(holdout_config.get("val_frac", 0.20))
    time_col = str(holdout_config.get("time_col", "entry_time"))
    min_dev = int(holdout_config.get("min_dev_trades", 50))
    min_holdout = int(holdout_config.get("min_holdout_trades", 20))

    meta: Dict[str, Any] = {
        "enabled": False,
        "is_frac": is_frac,
        "val_frac": val_frac,
    }

    if not enabled:
        meta["reason"] = "holdout disabled in config"
        return meta, original_trades, None

    if original_trades is None or not isinstance(original_trades, pd.DataFrame) or len(original_trades) == 0:
        meta["reason"] = "no trades available"
        return meta, original_trades, None

    if time_col not in original_trades.columns:
        meta["reason"] = f"missing time column '{time_col}'"
        return meta, original_trades, None

    try:
        partition = partition_trades(
            original_trades,
            is_frac=is_frac,
            val_frac=val_frac,
            time_col=time_col,
        )
    except Exception as exc:
        meta["reason"] = f"partition failed: {exc}"
        return meta, original_trades, None

    dev_df = pd.concat([partition.is_df, partition.val_df], ignore_index=True) if (partition.n_is + partition.n_val) > 0 else original_trades.iloc[0:0]
    holdout_df = partition.holdout_df

    if len(dev_df) < min_dev or len(holdout_df) < min_holdout:
        meta.update(partition.to_dict())
        meta["reason"] = (
            f"sample too small for holdout split "
            f"(dev={len(dev_df)} < {min_dev} or holdout={len(holdout_df)} < {min_holdout})"
        )
        return meta, original_trades, None

    meta.update(partition.to_dict())
    meta["enabled"] = True
    meta["dev_trades"] = int(len(dev_df))
    return meta, dev_df, holdout_df


def _compute_holdout_evaluation(
    holdout_trades: Optional[pd.DataFrame],
    cost_model: Dict[str, Any],
    bars_with_regime: Optional[pd.DataFrame],
) -> Optional[Dict[str, Any]]:
    """
    One-shot evaluation on the locked holdout slice. Returns None when no
    holdout is available. Applies the cost model and computes the same
    metric set used for the dev-slice evaluation, plus regime breakdown.
    """
    from .evaluation import compute_evaluation_metrics, compute_regime_breakdown

    if holdout_trades is None or len(holdout_trades) == 0:
        return None

    cost_norm = apply_cost_model(holdout_trades, cost_model)
    metrics = compute_evaluation_metrics(cost_norm)
    if bars_with_regime is not None:
        try:
            metrics["by_regime"] = compute_regime_breakdown(cost_norm, bars_with_regime)
        except Exception:
            pass
    return metrics


def _compute_oos_evaluation(
    cost_normalized_dev: Optional[pd.DataFrame],
    wf_config: Dict[str, Any],
    bars_with_regime: Optional[pd.DataFrame],
) -> Optional[Dict[str, Any]]:
    """
    Build the OOS-fold evaluation on the dev slice. The OOS pool is the
    contiguous tail of the dev slice that the rolling walk-forward folds
    score on — aggregating ``compute_evaluation_metrics`` across that tail
    yields a true OOS view, not an in-sample fit.
    """
    from .evaluation import compute_evaluation_metrics, compute_regime_breakdown

    if cost_normalized_dev is None or len(cost_normalized_dev) == 0:
        return None
    oos_pool = extract_oos_pool(cost_normalized_dev, wf_config=wf_config)
    if oos_pool is None or len(oos_pool) == 0:
        return None
    metrics = compute_evaluation_metrics(oos_pool)
    if bars_with_regime is not None:
        try:
            metrics["by_regime"] = compute_regime_breakdown(oos_pool, bars_with_regime)
        except Exception:
            pass
    return metrics


# ---------------------------------------------------------------------------
# Dev-slice split (entry-search vs exit-search)
# ---------------------------------------------------------------------------

def _split_dev_for_search(
    dev_trades: Optional[pd.DataFrame],
    dev_split_config: Dict[str, Any],
) -> tuple:
    """
    Partition the dev slice into disjoint entry-search and exit-search
    samples so that stop/target sweeps cannot be co-fit against the same
    trades that generated the entry rule.

    Returns ``(meta, entry_trades, exit_trades)``. When the split is
    disabled, the column is missing, or the sample is too small, both
    returned slices fall back to the full ``dev_trades`` and ``meta``
    records the reason. Callers should treat that fallback as "split
    inactive" and the caller should still record the meta.
    """
    enabled = bool(dev_split_config.get("enabled", True))
    entry_frac = float(dev_split_config.get("entry_frac", 0.50))
    mode = str(dev_split_config.get("mode", "chronological")).lower()
    min_entry = int(dev_split_config.get("min_entry_trades", 30))
    min_exit = int(dev_split_config.get("min_exit_trades", 30))
    time_col = str(dev_split_config.get("time_col", "entry_time"))

    meta: Dict[str, Any] = {
        "enabled": False,
        "entry_frac": entry_frac,
        "mode": mode,
        "n_entry": 0,
        "n_exit": 0,
    }

    if not enabled:
        meta["reason"] = "dev_split disabled in config"
        return meta, dev_trades, dev_trades

    if dev_trades is None or not isinstance(dev_trades, pd.DataFrame) or len(dev_trades) == 0:
        meta["reason"] = "no dev trades available"
        return meta, dev_trades, dev_trades

    if not (0.0 < entry_frac < 1.0):
        meta["reason"] = f"entry_frac={entry_frac} out of (0,1)"
        return meta, dev_trades, dev_trades

    if mode == "interleaved":
        # Alternate by sorted position. Stride is chosen so that ~entry_frac
        # of rows go into the entry slice. For 0.5 this is exact every-other.
        if time_col in dev_trades.columns:
            sorted_df = dev_trades.sort_values(time_col).reset_index(drop=True)
        else:
            sorted_df = dev_trades.reset_index(drop=True)
        n = len(sorted_df)
        # Use a deterministic position-based mask: a row at index i goes to
        # entry if (i * entry_frac) % 1 wraps. This approximates the target
        # fraction without introducing randomness.
        positions = np.arange(n)
        cum = np.floor((positions + 1) * entry_frac).astype(int)
        prev = np.floor(positions * entry_frac).astype(int)
        entry_mask = cum > prev
        entry_df = sorted_df.loc[entry_mask].copy()
        exit_df = sorted_df.loc[~entry_mask].copy()
    else:
        # Chronological split by date span — same convention as
        # holdout.partition_trades so users find the behaviour familiar.
        if time_col not in dev_trades.columns:
            meta["reason"] = f"missing time column '{time_col}' (mode={mode})"
            return meta, dev_trades, dev_trades
        df = dev_trades.copy()
        df[time_col] = pd.to_datetime(df[time_col], utc=False, errors="coerce")
        df = df.dropna(subset=[time_col]).sort_values(time_col).reset_index(drop=True)
        if len(df) == 0:
            meta["reason"] = "all dev trades have null time column"
            return meta, dev_trades, dev_trades
        t_min = df[time_col].min()
        t_max = df[time_col].max()
        span = t_max - t_min
        boundary = t_min + span * entry_frac
        entry_df = df[df[time_col] < boundary].copy()
        exit_df = df[df[time_col] >= boundary].copy()
        meta["entry_start"] = entry_df[time_col].min().isoformat() if len(entry_df) > 0 else None
        meta["entry_end"] = entry_df[time_col].max().isoformat() if len(entry_df) > 0 else None
        meta["exit_start"] = exit_df[time_col].min().isoformat() if len(exit_df) > 0 else None
        meta["exit_end"] = exit_df[time_col].max().isoformat() if len(exit_df) > 0 else None

    if len(entry_df) < min_entry or len(exit_df) < min_exit:
        meta["n_entry"] = int(len(entry_df))
        meta["n_exit"] = int(len(exit_df))
        meta["reason"] = (
            f"sample too small (entry={len(entry_df)} < {min_entry} or "
            f"exit={len(exit_df)} < {min_exit})"
        )
        return meta, dev_trades, dev_trades

    meta["enabled"] = True
    meta["n_entry"] = int(len(entry_df))
    meta["n_exit"] = int(len(exit_df))
    return meta, entry_df, exit_df


def _split_signal_feature_df(
    signal_feature_df: Optional[pd.DataFrame],
    dev_split_meta: Dict[str, Any],
    entry_frac: float,
    mode: str,
) -> tuple:
    """
    Split the per-signal feature matrix into entry-search and exit-search
    halves using the same chronological convention as ``_split_dev_for_search``.

    When the dev_split is inactive (``meta["enabled"] is False``), or the
    matrix has no ``dt`` column, both returned halves are the full matrix.
    Returns ``(entry_sfdf, exit_sfdf)``.
    """
    if signal_feature_df is None or not isinstance(signal_feature_df, pd.DataFrame) or len(signal_feature_df) == 0:
        return signal_feature_df, signal_feature_df

    if not dev_split_meta.get("enabled"):
        return signal_feature_df, signal_feature_df

    if "dt" not in signal_feature_df.columns:
        return signal_feature_df, signal_feature_df

    df = signal_feature_df.copy()
    try:
        df["__dt_naive"] = pd.to_datetime(df["dt"], utc=False, errors="coerce")
    except Exception:
        return signal_feature_df, signal_feature_df

    df = df.dropna(subset=["__dt_naive"]).sort_values("__dt_naive").reset_index(drop=True)
    if len(df) == 0:
        return signal_feature_df, signal_feature_df

    if mode == "interleaved":
        n = len(df)
        positions = np.arange(n)
        cum = np.floor((positions + 1) * entry_frac).astype(int)
        prev = np.floor(positions * entry_frac).astype(int)
        entry_mask = cum > prev
        entry_df = df.loc[entry_mask].drop(columns="__dt_naive").copy()
        exit_df = df.loc[~entry_mask].drop(columns="__dt_naive").copy()
    else:
        t_min = df["__dt_naive"].min()
        t_max = df["__dt_naive"].max()
        span = t_max - t_min
        boundary = t_min + span * entry_frac
        entry_df = df[df["__dt_naive"] < boundary].drop(columns="__dt_naive").copy()
        exit_df = df[df["__dt_naive"] >= boundary].drop(columns="__dt_naive").copy()

    return entry_df, exit_df


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
        "vol_mode": ("vol_mode", str),
        "vol_period": ("vol_period", int),
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
    db_path: Optional[str] = None,
    output_dir: Optional[str | Path] = None,
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
    holdout_config = {**DEFAULT_HOLDOUT_CONFIG, **dict(options.get("holdout") or {})}
    dev_split_config = {**DEFAULT_DEV_SPLIT_CONFIG, **dict(options.get("dev_split") or {})}
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

        # ---- Locked holdout partition --------------------------------------
        # Discovery (validation, entry/exit search, importance, sensitivity,
        # …) runs on the IS+VAL development slice only. The holdout slice
        # is preserved untouched and evaluated exactly once at the end of
        # this iteration. We mutate pkg.trades for the duration of the
        # per-package work and restore the original frame at the bottom of
        # the loop body so report rendering still sees the full trade set.
        # Every inner analysis block already has its own try/except, so an
        # un-restored mutation requires an exception path none of them
        # tolerate — acceptable for keeping the body readable.
        original_trades = getattr(pkg, "trades", None)
        holdout_meta, dev_trades, holdout_trades = _resolve_holdout(
            original_trades, holdout_config
        )
        discovery_block["holdout_partition"] = _make_json_safe(holdout_meta)
        if dev_trades is not original_trades:
            pkg.trades = dev_trades

        # ---- Entry-search vs exit-search dev split -------------------------
        # Carve the dev slice into two disjoint chronological halves so that
        # exit grids cannot be co-fit on the same trades that produced the
        # entry rule. When the split is active, ``pkg.trades`` is swapped
        # to the entry slice around entry_discovery / signal_entry_discovery
        # / entry_pattern_bridge calls and to the exit slice around
        # exit_discovery / signal_exit_sweep calls; everything else continues
        # to see the full ``dev_trades`` baseline.
        dev_split_meta, entry_trades, exit_trades = _split_dev_for_search(
            dev_trades, dev_split_config
        )
        discovery_block["dev_split"] = _make_json_safe(dev_split_meta)

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

        # Pre-compute regime breakdown so dispersion_count can gate validation
        from .evaluation import compute_evaluation_metrics, compute_regime_breakdown
        from .validation import apply_cost_model as _apply_cost_model
        pre_regime_breakdown: Dict[str, Any] = {}
        pre_regime_dispersion: Optional[int] = None
        if bars_with_regime is not None and trades is not None and len(trades) > 0:
            try:
                cost_norm_pre = _apply_cost_model(trades, cost_model)
                pre_regime_breakdown = compute_regime_breakdown(cost_norm_pre, bars_with_regime)
                
                # Dispersion count should reflect the primary 'regime' dimension
                # for the hard gate.
                primary_bk = pre_regime_breakdown.get("by_regime", pre_regime_breakdown)
                pre_regime_dispersion = len(primary_bk)
            except Exception:
                pass

        # Validation (regime dispersion auto-wired from pre-computed breakdown)
        validation_result = None
        try:
            validation_result = run_validation(
                trades if trades is not None else pd.DataFrame(),
                wf_config=wf_config,
                cost_model=cost_model,
                db_path=db_path,
                regime_dispersion_count=pre_regime_dispersion,
                regime_breakdown=pre_regime_breakdown if pre_regime_breakdown else None,
            )
            # Store JSON-safe summary in metadata; keep DataFrame in assets
            validation_safe = _make_json_safe(validation_result.to_dict())
            discovery_block["validation"] = validation_safe
        except Exception as exc:
            discovery_block["validation"] = {"error": str(exc), "passed": False}

        # Store bars_with_regime in pkg.assets (DataFrame is allowed here)
        if bars_with_regime is not None:
            assets = getattr(pkg, "assets", None)
            if isinstance(assets, dict):
                assets.setdefault("strategy_discovery", {})
                assets["strategy_discovery"]["bars_with_regime"] = bars_with_regime

        # Evaluation views.
        #
        #   evaluation          — dev slice (IS+VAL), cost-normalized.
        #                         Diagnostic / reference only; this is what
        #                         discovery actually saw.
        #
        #   evaluation_oos      — concatenation of all rolling-WF OOS folds
        #                         on the dev slice. This is the unbiased
        #                         metric set; ranking should rank on this.
        #
        #   evaluation_holdout  — locked-holdout slice, evaluated once and
        #                         only once after discovery is complete.
        #                         Promotion gate, not a fitting target.
        try:
            cost_norm_trades = validation_result.cost_normalized if validation_result is not None else None
            if cost_norm_trades is not None and len(cost_norm_trades) > 0:
                eval_metrics = compute_evaluation_metrics(cost_norm_trades)
                if pre_regime_breakdown:
                    eval_metrics["by_regime"] = pre_regime_breakdown
                elif bars_with_regime is not None:
                    eval_metrics["by_regime"] = compute_regime_breakdown(cost_norm_trades, bars_with_regime)
                discovery_block["evaluation"] = _make_json_safe(eval_metrics)
            else:
                discovery_block["evaluation"] = {"error": "no cost-normalized trades available"}
        except Exception as exc:
            discovery_block["evaluation"] = {"error": str(exc)}

        try:
            oos_metrics = _compute_oos_evaluation(
                validation_result.cost_normalized if validation_result is not None else None,
                wf_config=wf_config,
                bars_with_regime=bars_with_regime,
            )
            if oos_metrics is not None:
                discovery_block["evaluation_oos"] = _make_json_safe(oos_metrics)
            else:
                discovery_block["evaluation_oos"] = {
                    "skipped": True,
                    "reason": "insufficient OOS pool",
                }
        except Exception as exc:
            discovery_block["evaluation_oos"] = {"error": str(exc)}

        try:
            if holdout_trades is not None and len(holdout_trades) > 0:
                holdout_metrics = _compute_holdout_evaluation(
                    holdout_trades,
                    cost_model=cost_model,
                    bars_with_regime=bars_with_regime,
                )
                discovery_block["evaluation_holdout"] = _make_json_safe(holdout_metrics) if holdout_metrics else {
                    "skipped": True,
                    "reason": "holdout evaluation produced no metrics",
                }
            else:
                discovery_block["evaluation_holdout"] = {
                    "skipped": True,
                    "reason": "no locked holdout slice",
                }
        except Exception as exc:
            discovery_block["evaluation_holdout"] = {"error": str(exc)}

        # Slippage / latency stress sweep (T8). Re-prices the dev trades under
        # a grid of (slippage_ticks, entry_delay_bars) regimes so reviewers can
        # see how much of the apparent edge survives realistic execution. The
        # gate cell defaults to (slip=2, delay=1); candidates that lose more
        # than ``max_expectancy_loss_pct`` of expectancy at that cell are
        # flagged for the ranking-time hard gate.
        try:
            from .slippage_stress import run_slippage_stress
            ss_options = dict(options.get("slippage_stress") or {})
            stress_trades = (
                validation_result.cost_normalized
                if validation_result is not None
                and isinstance(getattr(validation_result, "cost_normalized", None), pd.DataFrame)
                and len(validation_result.cost_normalized) > 0
                else (dev_trades if isinstance(dev_trades, pd.DataFrame) else None)
            )
            discovery_block["slippage_stress"] = _make_json_safe(
                run_slippage_stress(
                    stress_trades,
                    baseline_cost_model=cost_model,
                    options=ss_options,
                )
            )
        except Exception as exc:
            discovery_block["slippage_stress"] = {"error": str(exc), "passed": False}

        # Exit Policy Discovery — runs on the exit-search slice when the
        # dev_split is active so stop/target grids can't be co-fit against
        # the trades that generated the entry rule.
        try:
            from .exit_discovery import run_exit_discovery
            ed_options = dict(options.get("exit_discovery") or {})
            ed_options.setdefault("enabled", True)
            ed_options.setdefault("tick_size", tick_size)
            ed_options.setdefault("tick_value", tick_value)
            ed_options.setdefault("atr_tf", timeframe)
            if exit_trades is not dev_trades:
                pkg.trades = exit_trades
            try:
                exit_result = run_exit_discovery(
                    pkg=pkg,
                    market=market,
                    options=ed_options,
                    bars_with_regime=bars_with_regime,
                )
            finally:
                if exit_trades is not dev_trades:
                    pkg.trades = dev_trades
            discovery_block["exit_discovery"] = exit_result
        except Exception as exc:
            # Defensive restore in case the swap above raised before the
            # try/finally above was reached.
            if getattr(pkg, "trades", None) is exit_trades and exit_trades is not dev_trades:
                pkg.trades = dev_trades
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

        # Session risk summary — hour-level edge/danger scores from market_regime_store
        session_risk_df: Optional[pd.DataFrame] = None
        try:
            from ta_foundation.analysis.market_regime_store import (
                summarize_entry_hour_risk,
                optimize_entry_hour_window,
            )
            if trades is not None and len(trades) > 0:
                session_risk_df = summarize_entry_hour_risk(trades)
                if isinstance(session_risk_df, pd.DataFrame) and len(session_risk_df) > 0:
                    session_window_df = optimize_entry_hour_window(session_risk_df)
                    pkg_assets_sr = getattr(pkg, "assets", {}) or {}
                    pkg_assets_sr.setdefault("strategy_discovery", {})
                    pkg_assets_sr["strategy_discovery"]["session_risk"] = session_risk_df
                    pkg_assets_sr["strategy_discovery"]["session_window"] = session_window_df
                    discovery_block["session_risk_hours"] = int(len(session_risk_df))
        except Exception as exc:
            discovery_block["session_risk"] = {"error": str(exc)}

        # Candidate scorecard — join all pattern-engine evidence + session risk into one table
        try:
            from .entry_pattern_bridge import build_candidate_scorecard
            scorecard_df = build_candidate_scorecard(pkg, session_risk_df=session_risk_df)
            if isinstance(scorecard_df, pd.DataFrame) and len(scorecard_df) > 0:
                pkg_assets_sc = getattr(pkg, "assets", {}) or {}
                pkg_assets_sc.setdefault("strategy_discovery", {})
                pkg_assets_sc["strategy_discovery"]["candidate_scorecard"] = scorecard_df
                discovery_block["n_candidates"] = int(len(scorecard_df))
        except Exception as exc:
            discovery_block["candidate_scorecard"] = {"error": str(exc)}

        # Signal Entry Discovery (pure corpus-based, no executed trades required).
        # When the dev_split is active, only the entry-search slice of the
        # signal_feature_matrix is fed to discovery — the exit slice is
        # reserved for signal_exit_sweep downstream.
        try:
            pkg_assets_sed = getattr(pkg, "assets", {}) or {}
            sd_assets_sed = pkg_assets_sed.get("strategy_discovery") or {}
            sfdf = sd_assets_sed.get("signal_feature_matrix") if isinstance(sd_assets_sed, dict) else None
            if sfdf is not None and isinstance(sfdf, pd.DataFrame) and len(sfdf) > 0:
                from .signal_entry_discovery import run_signal_entry_discovery
                sed_options = dict(options.get("signal_entry_discovery") or {})
                sed_options.setdefault("enabled", True)
                sfdf_entry, _sfdf_exit = _split_signal_feature_df(
                    sfdf,
                    dev_split_meta,
                    entry_frac=float(dev_split_config.get("entry_frac", 0.50)),
                    mode=str(dev_split_config.get("mode", "chronological")).lower(),
                )
                discovery_block["signal_entry_discovery"] = run_signal_entry_discovery(
                    signal_feature_df=sfdf_entry,
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

            cost_norm = validation_result.cost_normalized if validation_result is not None else None
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
            _cn = validation_result.cost_normalized if validation_result is not None else None
            entry_options.setdefault("profit_col", "profit_net" if (
                _cn is not None and hasattr(_cn, "columns") and "profit_net" in _cn.columns
            ) else "profit")
            # Use cost-normalized feature_df if available (has profit_net column)
            cost_norm = _cn
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
            if entry_trades is not dev_trades:
                pkg.trades = entry_trades
            try:
                discovery_block["entry_discovery"] = run_entry_discovery(
                    pkg=pkg,
                    options=entry_options,
                    feature_df=feat_df_ed,
                    bars_with_regime=bars_with_regime,
                )
            finally:
                if entry_trades is not dev_trades:
                    pkg.trades = dev_trades
        except Exception as exc:
            if getattr(pkg, "trades", None) is entry_trades and entry_trades is not dev_trades:
                pkg.trades = dev_trades
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

        # Restore the full trade set so post-discovery consumers (report
        # rendering, downstream analysis) see all trades — discovery only
        # ever saw the dev slice.
        if dev_trades is not original_trades:
            pkg.trades = original_trades

    # -----------------------------------------------------------------------
    # Ranking — runs after all per-package analysis is complete
    # -----------------------------------------------------------------------
    try:
        from .ranking import run_ranking
        gates_config = options.get("ranking_gates")
        run_ranking(packages, gates_config=gates_config)  # attaches per-run scores + cross_run_ranking to packages internally
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
    # Portfolio combo selection — find low co-loss strategy baskets
    # Only runs when there are 2+ non-system packages.
    # -----------------------------------------------------------------------
    real_packages = {k: v for k, v in (packages or {}).items() if not str(k).startswith("__")}
    if len(real_packages) >= 2:
        try:
            from ta_foundation.analysis.daily_matrix import build_daily_matrix
            from ta_foundation.analysis.combo_selection import top_combos

            matrix = build_daily_matrix(real_packages)
            run_id_list = list(matrix.pnl.columns)

            basket_k2 = top_combos(matrix.pnl, matrix.traded, run_id_list, k=2, top_n=5)
            basket_k3 = (
                top_combos(matrix.pnl, matrix.traded, run_id_list, k=3, top_n=3)
                if len(run_id_list) >= 3
                else []
            )

            def _combo_score_to_dict(cs) -> Dict[str, Any]:
                return {
                    "run_ids": list(cs.run_ids),
                    "k": cs.k,
                    "any_coloss_rate": cs.any_coloss_rate,
                    "all_loss_rate": cs.all_loss_rate,
                    "traded_days": cs.traded_days,
                    "combo_cum_end": cs.combo_cum_end,
                }

            basket_summary = {
                "k2": [_combo_score_to_dict(c) for c in basket_k2],
                "k3": [_combo_score_to_dict(c) for c in basket_k3],
            }

            # Store in each real package: JSON-safe summary in metadata, full objects in assets
            for run_id, pkg in real_packages.items():
                derived = (getattr(pkg, "metadata", None) or {}).get("derived", {})
                sd_block = derived.get("strategy_discovery")
                if isinstance(sd_block, dict):
                    sd_block["combo_basket"] = basket_summary

                pkg_assets = getattr(pkg, "assets", None) or {}
                if isinstance(pkg_assets, dict):
                    pkg_assets.setdefault("strategy_discovery", {})
                    pkg_assets["strategy_discovery"]["combo_basket"] = {
                        "k2": basket_k2,
                        "k3": basket_k3,
                        "matrix": matrix,
                        "summary": basket_summary,
                    }
        except Exception as exc:
            import traceback as _tb
            print(f"[ta_foundation] WARNING combo_selection failed: {exc}")
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

            # Split the corpus into entry-search and exit-search halves so
            # rule discovery and stop/target sweeps see disjoint signals.
            # The market_discovery package has no executed trades, so use a
            # synthetic "active" meta and let _split_signal_feature_df do
            # the work via the dt column.
            ms_split_meta: Dict[str, Any] = {
                "enabled": bool(dev_split_config.get("enabled", True)),
                "entry_frac": float(dev_split_config.get("entry_frac", 0.50)),
                "mode": str(dev_split_config.get("mode", "chronological")).lower(),
            }
            sfdf_entry, sfdf_exit = _split_signal_feature_df(
                signal_feature_df,
                ms_split_meta,
                entry_frac=ms_split_meta["entry_frac"],
                mode=ms_split_meta["mode"],
            )
            ms_split_meta["n_entry"] = int(len(sfdf_entry)) if isinstance(sfdf_entry, pd.DataFrame) else 0
            ms_split_meta["n_exit"] = int(len(sfdf_exit)) if isinstance(sfdf_exit, pd.DataFrame) else 0
            corpus_block["dev_split"] = _make_json_safe(ms_split_meta)

            corpus_block["signal_entry_discovery"] = run_signal_entry_discovery(
                signal_feature_df=sfdf_entry,
                options=sed_options,
            )

            # Signal rule walk-forward validation
            sv_options = dict(options.get("signal_validation") or {})
            sv_options.setdefault("enabled", True)
            sv_options.setdefault("profit_col", "ret_ticks")
            try:
                from .signal_validation import run_signal_validation
                corpus_block["signal_validation"] = run_signal_validation(
                    signal_feature_df=sfdf_entry,
                    options=sv_options,
                )
            except Exception as sv_exc:
                corpus_block["signal_validation"] = {"error": str(sv_exc)}

            # Signal corpus exit sweep — find best stop/target for each
            # signal rule, evaluated on the EXIT slice so the optimal
            # parameters are not co-fit with the trades that produced the
            # rule itself.
            ses_options = dict(options.get("signal_exit_sweep") or {})
            ses_options.setdefault("enabled", True)
            exit_sweep_result: Dict[str, Any] = {}
            try:
                from .signal_exit_sweep import run_signal_exit_sweep
                sed_result = corpus_block.get("signal_entry_discovery") or {}
                signal_rules = sed_result.get("top_signal_rules") or []
                exit_sweep_result = run_signal_exit_sweep(
                    signal_feature_df=sfdf_exit,
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

    # -----------------------------------------------------------------------
    # Research Ledger Integration (P1)
    # -----------------------------------------------------------------------
    if db_path and output_dir:
        try:
            from ta_foundation.research_ledger import get_repository
            from ta_foundation.research_ledger.backfill import backfill_from_outputs
            from pathlib import Path

            repo = get_repository(db_path)
            out_path = Path(output_dir)
            if out_path.exists():
                # Note: We rely on the fact that the CLI caller writes sidecars
                # to this directory AFTER this function returns. 
                # This automation only works if the CLI caller triggers 
                # a final sweep or if we call this from the CLI.
                # To ensure it runs, we'll suggest calling it from cli/main.py.
                report = backfill_from_outputs(repo, [out_path], registered_by="orchestrator")
                if report.runs_inserted > 0 or report.candidates_inserted > 0:
                    print(f"[ta_foundation] Ledger updated: +{report.runs_inserted} runs, +{report.candidates_inserted} candidates.")
        except Exception as exc:
            print(f"[ta_foundation] WARNING ledger automation failed: {exc}")
