from __future__ import annotations

"""
Cohort Analysis
===============
Phase 10 — Detects strategy drift and performance decay over time by slicing
the trade history into rolling time windows (cohorts) and tracking how key
metrics (PF, WR, net profit) evolve.

Answers: "Is this strategy still working, or is it decaying?"

Algorithm
---------
1. Sort trades by entry_time.
2. Divide into N cohorts either by:
   - fixed trade count (default: cohort_size trades each), or
   - calendar period (weekly / monthly).
3. Compute PF, WR, avg_profit, net_profit, n_trades per cohort.
4. Optionally join regime mix from bars_with_regime via merge_asof.
5. Fit a least-squares linear trend to the cohort PF series.
6. Classify trend: improving / stable / degrading / volatile.
7. Compare early cohorts (first third) vs late cohorts (last third):
   - ΔPF, ΔWR, Δavg_profit — negative deltas signal decay.
8. Compute decay_score 0–100:
   - Slope component  (40 pts): normalised negative slope
   - WR component     (30 pts): WR decline early→late
   - Volatility       (20 pts): stddev of cohort PF series
   - Recency          (10 pts): last cohort PF vs overall PF

Entry point: run_cohort_analysis(pkg, options, profit_col, bars_with_regime)
Attaches to: pkg.metadata["derived"]["strategy_discovery"]["cohort_analysis"]
"""

from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_OPTIONS: Dict[str, Any] = {
    "enabled":        True,
    "cohort_by":      "count",   # "count" | "week" | "month"
    "cohort_size":    20,        # trades per cohort when cohort_by="count"
    "min_cohorts":    3,         # skip analysis if fewer cohorts result
    "min_trades":     30,        # skip entirely if fewer total trades
    "profit_col":     "profit_net",
}

# Trend classification thresholds (per-cohort PF units)
_SLOPE_STABLE_BAND   = 0.05   # |slope| < this → stable
_VOLATILE_CV_THRESH  = 0.40   # coefficient of variation of PF > this → volatile


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_float(v: Any) -> Optional[float]:
    try:
        f = float(v)
        return None if not np.isfinite(f) else f
    except Exception:
        return None


def _pf(profit: pd.Series) -> Optional[float]:
    gross_win  = float(profit[profit > 0].sum()) if len(profit[profit > 0]) > 0 else 0.0
    gross_loss = float(abs(profit[profit <= 0].sum())) if len(profit[profit <= 0]) > 0 else 0.0
    if gross_loss == 0:
        return None if gross_win == 0 else float("inf")
    return round(gross_win / gross_loss, 4)


def _metrics(profit: pd.Series) -> Dict[str, Any]:
    n = len(profit)
    if n == 0:
        return {"n_trades": 0, "pf": None, "win_rate": None, "avg_profit": None, "net_profit": None}
    winners = profit[profit > 0]
    return {
        "n_trades":   int(n),
        "pf":         _pf(profit),
        "win_rate":   round(float(len(winners) / n), 4),
        "avg_profit": round(float(profit.mean()), 2),
        "net_profit": round(float(profit.sum()), 2),
    }


def _linear_trend(y: np.ndarray) -> Dict[str, Any]:
    """Fit y ~ a*x + b by least squares; return slope, intercept, r_squared."""
    n = len(y)
    if n < 2:
        return {"slope": None, "intercept": None, "r_squared": None}
    x = np.arange(n, dtype=float)
    # Mask out inf / nan
    mask = np.isfinite(y)
    if mask.sum() < 2:
        return {"slope": None, "intercept": None, "r_squared": None}
    xm, ym = x[mask], y[mask]
    try:
        coeffs = np.polyfit(xm, ym, 1)
        slope, intercept = float(coeffs[0]), float(coeffs[1])
        y_pred = slope * xm + intercept
        ss_res = float(np.sum((ym - y_pred) ** 2))
        ss_tot = float(np.sum((ym - ym.mean()) ** 2))
        r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
        return {
            "slope":     round(slope, 6),
            "intercept": round(intercept, 4),
            "r_squared": round(r2, 4),
        }
    except Exception:
        return {"slope": None, "intercept": None, "r_squared": None}


def _classify_trend(
    slope: Optional[float],
    pf_series: np.ndarray,
) -> str:
    """
    Return one of: "improving", "stable", "degrading", "volatile", "unknown".
    """
    if slope is None:
        return "unknown"
    finite = pf_series[np.isfinite(pf_series)]
    if len(finite) >= 2:
        mean_pf = float(np.mean(finite))
        std_pf  = float(np.std(finite))
        cv = std_pf / abs(mean_pf) if abs(mean_pf) > 0 else 0.0
        if cv > _VOLATILE_CV_THRESH:
            return "volatile"
    if abs(slope) < _SLOPE_STABLE_BAND:
        return "stable"
    return "improving" if slope > 0 else "degrading"


def _decay_score(
    slope: Optional[float],
    pf_series: np.ndarray,
    wr_early: Optional[float],
    wr_late: Optional[float],
    overall_pf: Optional[float],
    last_pf: Optional[float],
) -> int:
    """
    Blended 0–100 decay score.  0 = healthy, 100 = fully decayed.
    Components:
      slope     40 pts  negative slope (normalised by median PF)
      wr_drop   30 pts  WR decline early→late
      volatility 20 pts stddev / mean of PF series
      recency   10 pts  last cohort PF vs overall PF
    """
    finite = pf_series[np.isfinite(pf_series)]

    # --- slope component ---
    slope_pts = 0.0
    if slope is not None and len(finite) > 0:
        med_pf = float(np.median(finite)) if len(finite) > 0 else 1.0
        if med_pf > 0:
            norm_slope = slope / med_pf   # normalised: how many PF units per cohort, relative to median
            # negative slope → decay; cap at ±0.5 range
            slope_pts = float(np.clip(-norm_slope / 0.5, 0.0, 1.0)) * 40.0

    # --- WR drop component ---
    wr_pts = 0.0
    if wr_early is not None and wr_late is not None:
        drop = wr_early - wr_late   # positive = WR dropped → decay
        wr_pts = float(np.clip(drop / 0.20, 0.0, 1.0)) * 30.0   # 20pp drop = full 30pts

    # --- volatility component ---
    vol_pts = 0.0
    if len(finite) >= 2:
        mean_pf = float(np.mean(finite))
        std_pf  = float(np.std(finite))
        cv = std_pf / abs(mean_pf) if abs(mean_pf) > 0 else 0.0
        vol_pts = float(np.clip(cv / 0.80, 0.0, 1.0)) * 20.0   # CV of 0.8 = full 20pts

    # --- recency component ---
    rec_pts = 0.0
    if overall_pf is not None and last_pf is not None and np.isfinite(overall_pf) and np.isfinite(last_pf):
        if overall_pf > 0:
            recency_ratio = last_pf / overall_pf   # < 1 = recent underperformance
            rec_pts = float(np.clip((1.0 - recency_ratio) / 0.50, 0.0, 1.0)) * 10.0

    return int(round(slope_pts + wr_pts + vol_pts + rec_pts))


# ---------------------------------------------------------------------------
# Cohort splitting
# ---------------------------------------------------------------------------

def _split_by_count(
    trades: pd.DataFrame,
    cohort_size: int,
    profit_col: str,
) -> List[Dict[str, Any]]:
    """Split sorted trades into fixed-count windows."""
    cohorts = []
    n = len(trades)
    for start in range(0, n, cohort_size):
        chunk = trades.iloc[start: start + cohort_size]
        if len(chunk) == 0:
            continue
        entry_times = pd.to_datetime(chunk["entry_time"])
        m = _metrics(chunk[profit_col])
        m["period_start"] = str(entry_times.min().date()) if hasattr(entry_times.min(), "date") else str(entry_times.min())
        m["period_end"]   = str(entry_times.max().date()) if hasattr(entry_times.max(), "date") else str(entry_times.max())
        m["cohort_index"] = len(cohorts)
        cohorts.append(m)
    return cohorts


def _split_by_period(
    trades: pd.DataFrame,
    period: str,       # "week" | "month"
    profit_col: str,
) -> List[Dict[str, Any]]:
    """Split by calendar week or month."""
    freq_map = {"week": "W-MON", "month": "MS"}
    freq = freq_map.get(period, "MS")
    dt = pd.to_datetime(trades["entry_time"])
    if dt.dt.tz is not None:
        dt = dt.dt.tz_localize(None)
    period_key = dt.dt.to_period({"week": "W", "month": "M"}.get(period, "M"))
    cohorts = []
    for pkey, idx in sorted(trades.groupby(period_key).groups.items()):
        chunk = trades.loc[idx]
        if len(chunk) == 0:
            continue
        m = _metrics(chunk[profit_col])
        m["period_start"]  = str(pkey.start_time.date()) if hasattr(pkey, "start_time") else str(pkey)
        m["period_end"]    = str(pkey.end_time.date()) if hasattr(pkey, "end_time") else str(pkey)
        m["cohort_index"]  = len(cohorts)
        cohorts.append(m)
    return cohorts


# ---------------------------------------------------------------------------
# Regime mix per cohort
# ---------------------------------------------------------------------------

def _add_regime_mix(
    cohorts: List[Dict[str, Any]],
    trades: pd.DataFrame,
    bars_with_regime: pd.DataFrame,
    cohort_size: int,
    cohort_by: str,
    profit_col: str,
) -> None:
    """
    Attach regime_mix dict to each cohort in-place.
    Uses the same trade split logic to recover the trade subset per cohort.
    """
    if bars_with_regime is None or "regime" not in bars_with_regime.columns:
        return
    if "entry_time" not in trades.columns:
        return

    try:
        # Join regime onto trades via merge_asof
        t = trades.copy()
        b = bars_with_regime.copy()

        # Normalise timestamps to tz-naive for merge_asof
        def _to_naive(s: pd.Series) -> pd.Series:
            s = pd.to_datetime(s)
            if s.dt.tz is not None:
                s = s.dt.tz_convert("UTC").dt.tz_localize(None)
            return s

        t["_entry_naive"] = _to_naive(t["entry_time"])
        b["_dt_naive"]    = _to_naive(b["dt"])
        t_sorted = t.sort_values("_entry_naive")
        b_sorted = b.sort_values("_dt_naive")
        merged = pd.merge_asof(
            t_sorted[["_entry_naive"]],
            b_sorted[["_dt_naive", "regime"]],
            left_on="_entry_naive",
            right_on="_dt_naive",
            direction="backward",
        )
        t = t.sort_values("_entry_naive").copy()
        t["regime"] = merged["regime"].values

        # Re-split using same scheme
        if cohort_by == "count":
            for i, cohort_dict in enumerate(cohorts):
                start = i * cohort_size
                chunk = t.iloc[start: start + cohort_size]
                if "regime" in chunk.columns:
                    vc = chunk["regime"].value_counts(normalize=True)
                    cohort_dict["regime_mix"] = {k: round(float(v), 3) for k, v in vc.items()}
        else:
            period_map = {"week": "W", "month": "M"}
            p_freq = period_map.get(cohort_by, "M")
            dt = pd.to_datetime(t["entry_time"])
            if dt.dt.tz is not None:
                dt = dt.dt.tz_localize(None)
            period_key = dt.dt.to_period(p_freq)
            t["_period"] = period_key
            for i, (pkey, idx) in enumerate(sorted(t.groupby("_period").groups.items())):
                if i >= len(cohorts):
                    break
                chunk = t.loc[idx]
                if "regime" in chunk.columns:
                    vc = chunk["regime"].value_counts(normalize=True)
                    cohorts[i]["regime_mix"] = {k: round(float(v), 3) for k, v in vc.items()}
    except Exception:
        pass  # regime mix is optional


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_cohort_analysis(
    pkg: Any,
    options: Dict[str, Any],
    *,
    profit_col: str = "profit_net",
    bars_with_regime: Optional[pd.DataFrame] = None,
) -> Dict[str, Any]:
    """
    Compute cohort (time-series) analysis for a strategy package.

    Returns JSON-safe dict:
      cohorts        : [{period_start, period_end, cohort_index, n_trades,
                         pf, win_rate, avg_profit, net_profit, regime_mix?}]
      trend          : {slope, intercept, r_squared, classification}
      decay_score    : int 0–100
      early_vs_late  : {pf_delta, wr_delta, avg_profit_delta,
                         early_pf, late_pf, early_wr, late_wr}
      overall        : {n_trades, pf, win_rate, avg_profit, net_profit}
      diagnostics    : {n_cohorts, cohort_by, cohort_size, profit_col_used, issues}
    """
    opts: Dict[str, Any] = {**DEFAULT_OPTIONS, **(options or {})}

    if not opts.get("enabled", True):
        return {"skipped": True}

    cohort_by   = str(opts.get("cohort_by", "count"))
    cohort_size = int(opts.get("cohort_size", 20))
    min_cohorts = int(opts.get("min_cohorts", 3))
    min_trades  = int(opts.get("min_trades", 30))
    profit_col  = str(opts.get("profit_col", profit_col))

    diag: Dict[str, Any] = {
        "cohort_by":       cohort_by,
        "cohort_size":     cohort_size,
        "n_cohorts":       0,
        "profit_col_used": profit_col,
        "issues":          [],
    }

    # ---- resolve trades ----
    trades = getattr(pkg, "trades", None)
    if trades is None or not isinstance(trades, pd.DataFrame) or len(trades) == 0:
        diag["issues"].append("no trade data available")
        return _empty(diag)

    if "entry_time" not in trades.columns:
        diag["issues"].append("entry_time column missing")
        return _empty(diag)

    if profit_col not in trades.columns:
        if "profit" in trades.columns:
            profit_col = "profit"
            diag["profit_col_used"] = profit_col
        else:
            diag["issues"].append(f"profit column '{profit_col}' not found")
            return _empty(diag)

    t = trades.copy()
    t[profit_col] = pd.to_numeric(t[profit_col], errors="coerce")
    t = t.dropna(subset=[profit_col, "entry_time"])
    t = t.sort_values("entry_time").reset_index(drop=True)
    n_total = len(t)

    if n_total < min_trades:
        diag["issues"].append(f"insufficient trades: {n_total} < {min_trades}")
        return _empty(diag)

    # ---- split into cohorts ----
    if cohort_by == "count":
        cohorts = _split_by_count(t, cohort_size, profit_col)
    elif cohort_by in ("week", "month"):
        cohorts = _split_by_period(t, cohort_by, profit_col)
    else:
        diag["issues"].append(f"unknown cohort_by='{cohort_by}'; using count")
        cohorts = _split_by_count(t, cohort_size, profit_col)

    diag["n_cohorts"] = len(cohorts)

    if len(cohorts) < min_cohorts:
        diag["issues"].append(
            f"only {len(cohorts)} cohorts (need ≥{min_cohorts}); "
            "consider smaller cohort_size or more trades"
        )
        return _empty(diag, cohorts=cohorts)

    # ---- add regime mix ----
    _add_regime_mix(cohorts, t, bars_with_regime, cohort_size, cohort_by, profit_col)

    # ---- trend analysis ----
    pf_series = np.array([c.get("pf") or np.nan for c in cohorts], dtype=float)
    wr_series = np.array([c.get("win_rate") or np.nan for c in cohorts], dtype=float)

    trend_fit = _linear_trend(pf_series)
    trend_cls = _classify_trend(trend_fit.get("slope"), pf_series)
    trend = {**trend_fit, "classification": trend_cls}

    # ---- early vs late ----
    n_coh = len(cohorts)
    third = max(1, n_coh // 3)
    early_cohorts = cohorts[:third]
    late_cohorts  = cohorts[-third:]

    def _avg(lst: List[Dict], key: str) -> Optional[float]:
        vals = [v for c in lst if (v := c.get(key)) is not None and np.isfinite(v)]
        return round(float(np.mean(vals)), 4) if vals else None

    early_pf  = _avg(early_cohorts, "pf")
    late_pf   = _avg(late_cohorts,  "pf")
    early_wr  = _avg(early_cohorts, "win_rate")
    late_wr   = _avg(late_cohorts,  "win_rate")
    early_avg = _avg(early_cohorts, "avg_profit")
    late_avg  = _avg(late_cohorts,  "avg_profit")

    early_vs_late = {
        "early_pf":          early_pf,
        "late_pf":           late_pf,
        "pf_delta":          round(late_pf - early_pf, 4) if (late_pf is not None and early_pf is not None) else None,
        "early_wr":          early_wr,
        "late_wr":           late_wr,
        "wr_delta":          round(late_wr - early_wr, 4) if (late_wr is not None and early_wr is not None) else None,
        "avg_profit_delta":  round(late_avg - early_avg, 2) if (late_avg is not None and early_avg is not None) else None,
    }

    # ---- overall metrics ----
    overall = _metrics(t[profit_col])

    # ---- decay score ----
    last_pf = cohorts[-1].get("pf") if cohorts else None
    score = _decay_score(
        slope=trend_fit.get("slope"),
        pf_series=pf_series,
        wr_early=early_wr,
        wr_late=late_wr,
        overall_pf=_safe_float(overall.get("pf")),
        last_pf=_safe_float(last_pf),
    )

    return _make_json_safe({
        "cohorts":       cohorts,
        "trend":         trend,
        "decay_score":   score,
        "early_vs_late": early_vs_late,
        "overall":       overall,
        "diagnostics":   diag,
    })


def _empty(diag: Dict[str, Any], cohorts: Optional[List] = None) -> Dict[str, Any]:
    return {
        "cohorts":       cohorts or [],
        "trend":         {"slope": None, "classification": "unknown"},
        "decay_score":   None,
        "early_vs_late": {},
        "overall":       {},
        "diagnostics":   diag,
    }


# ---------------------------------------------------------------------------
# JSON safety
# ---------------------------------------------------------------------------

def _make_json_safe(obj: Any) -> Any:
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
