from __future__ import annotations

"""
Trade Outcome Analyzer
======================
Evaluates simulated trade outcomes from large-candle forward-excursion data.

For each qualifying signal candle the forward window already provides:
  fav_ticks  — max favorable excursion in signal direction
  adv_ticks  — max adverse excursion against signal direction

Trade direction mapping
-----------------------
  continuation:  follow signal direction (bull → long, bear → short)
                 trade_fav = fav_ticks,  trade_adv = adv_ticks

  reverse:       oppose signal direction (bull → short, bear → long)
                 trade_fav = adv_ticks,  trade_adv = fav_ticks

Target / stop are percent of signal candle tick size:
  target_ticks = signal_candle_ticks * target_pct / 100

Outcome rules
-------------
  win   = trade_fav_ticks >= target_ticks  AND  (stop not hit first)
  loss  = stop_hit before target  OR  neither hit

First-hit ordering approximation (bar-level resolution)
  When both target and stop are reachable in the window, first-hit is
  approximated by comparing time_to_max_fav vs time_to_max_adv.
  These are times to the bar that produced the *maximum* — not the
  time the specific threshold was crossed.  Results are accurate in
  the common case; edge cases (target << fav_max) may be misclassified.
  The methodology section documents this limitation.
"""

from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Default config (merged by sweep.py into DEFAULT_CONFIG)
# ---------------------------------------------------------------------------

DEFAULT_TRADE_CONFIG: Dict[str, Any] = {
    "enabled": False,
    "trade_modes": {
        "continuation": True,
        "reverse": True,
    },
    "signal_candle_basis": "range",   # "range" | "body"
    "target": {
        "mode": "percent_of_signal_candle",
        "percents": [25, 50, 75, 100, 125],
    },
    "stop": {
        "enabled": False,
        "mode": "percent_of_signal_candle",
        "percents": [50],
    },
    "candle_size_buckets_ticks": [
        [0, 25],
        [25, 50],
        [50, 75],
        [75, 100],
        [100, 150],
        [150, None],
    ],
    "output": {
        "include_trade_summary":      True,
        "include_trade_comparison":   True,
        "include_trade_event_table":  True,
        "max_trade_events_sample":    2000,
    },
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_f(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        fv = float(v)
        return None if (fv != fv) else fv   # NaN guard via self-inequality
    except Exception:
        return None


def _sr(v: Any, digits: int = 2) -> Optional[float]:
    """Safe round."""
    f = _safe_f(v)
    return None if f is None else round(f, digits)


def assign_candle_bucket(size_ticks: float, buckets: List) -> str:
    """
    Assign signal candle size (in ticks) to a human-readable bucket label.

    Parameters
    ----------
    size_ticks : signal candle size in ticks
    buckets    : list of [lo, hi] pairs; hi=None means open-ended top bucket

    Returns
    -------
    str label e.g. "50-75", "150+"
    """
    for bucket in buckets:
        lo = float(bucket[0])
        hi = bucket[1]
        if hi is None:
            if size_ticks >= lo:
                return f"{int(lo)}+"
        else:
            if lo <= size_ticks < float(hi):
                return f"{int(lo)}-{int(hi)}"
    return "other"


def trade_direction(signal_direction: int, trade_mode: str) -> str:
    """
    Return the trade direction label given signal direction and trade mode.

    Parameters
    ----------
    signal_direction : 1 (bullish candle) | -1 (bearish candle)
    trade_mode       : "continuation" | "reverse"

    Returns
    -------
    "long" | "short"
    """
    if trade_mode == "continuation":
        return "long" if signal_direction == 1 else "short"
    else:  # reverse
        return "short" if signal_direction == 1 else "long"


# ---------------------------------------------------------------------------
# Core computation
# ---------------------------------------------------------------------------

def compute_trade_outcomes(
    fwd_df: pd.DataFrame,
    trade_cfg: Dict[str, Any],
    tick_size: float = 0.25,
) -> Dict[str, Any]:
    """
    Compute trade outcomes from forward excursion data.

    Parameters
    ----------
    fwd_df    : output of compute_forward_excursion() — must include
                fav_ticks, adv_ticks, direction, size_ticks, body_ticks,
                time_to_max_fav_min, time_to_max_adv_min, tf_minutes,
                window_minutes, lookback, basis, threshold_mode,
                threshold_value columns.
    trade_cfg : trade_analysis config dict (already merged with defaults).
    tick_size : instrument tick size (informational; not used in calculation).

    Returns
    -------
    JSON-safe dict:
      enabled              True
      trade_combo_results  list[dict] — per (tf × lookback × basis × threshold
                           × window × direction × trade_mode × target_pct
                           × candle_bucket) aggregated stats
      trade_events_sample  list[dict] — per-event records (capped)
      config               effective trade_cfg
    """
    if fwd_df is None or fwd_df.empty:
        return {
            "enabled": False,
            "trade_combo_results": [],
            "trade_events_sample": [],
            "config": trade_cfg,
        }

    modes_cfg    = trade_cfg.get("trade_modes", {"continuation": True, "reverse": True})
    active_modes = [m for m in ("continuation", "reverse") if modes_cfg.get(m, True)]
    if not active_modes:
        return {
            "enabled": False,
            "trade_combo_results": [],
            "trade_events_sample": [],
            "config": trade_cfg,
        }

    signal_basis     = trade_cfg.get("signal_candle_basis", "range")
    target_percents  = [float(p) for p in trade_cfg.get("target", {}).get("percents", [25, 50, 75, 100, 125])]
    stop_cfg         = trade_cfg.get("stop", {})
    stop_enabled     = bool(stop_cfg.get("enabled", False))
    stop_percents    = [float(p) for p in stop_cfg.get("percents", [50])] if stop_enabled else [None]
    buckets          = trade_cfg.get("candle_size_buckets_ticks",
                                     [[0,25],[25,50],[50,75],[75,100],[100,150],[150,None]])
    max_sample       = int(trade_cfg.get("output", {}).get("max_trade_events_sample", 2000))

    sig_col = "size_ticks" if signal_basis == "range" else "body_ticks"
    if sig_col not in fwd_df.columns:
        sig_col = "size_ticks"

    # Combo grouping columns carried from detector / forward_window
    combo_cols = ["tf_minutes", "lookback", "basis", "threshold_mode",
                  "threshold_value", "window_minutes"]

    event_records: List[Dict] = []

    for _, row in fwd_df.iterrows():
        fav_t    = _safe_f(row.get("fav_ticks"))
        adv_t    = _safe_f(row.get("adv_ticks"))
        sig_t    = _safe_f(row.get(sig_col))

        if fav_t is None or adv_t is None or sig_t is None or sig_t <= 0:
            continue

        time_fav  = _safe_f(row.get("time_to_max_fav_min"))
        time_adv  = _safe_f(row.get("time_to_max_adv_min"))
        direction = int(row.get("direction", 1))

        candle_bucket = assign_candle_bucket(sig_t, buckets)

        base: Dict[str, Any] = {}
        for c in combo_cols:
            v = row.get(c)
            if v is not None:
                base[c] = int(v) if c in ("tf_minutes", "lookback", "window_minutes") else v
        dt_val = row.get("dt")
        base["dt"]           = dt_val.isoformat() if isinstance(dt_val, pd.Timestamp) else dt_val
        base["direction"]    = direction
        base["signal_candle_ticks"]       = _sr(sig_t)
        base["signal_candle_range_ticks"] = _sr(row.get("size_ticks"))
        base["signal_candle_body_ticks"]  = _sr(row.get("body_ticks"))
        base["candle_bucket"] = candle_bucket

        for trade_mode in active_modes:
            if trade_mode == "continuation":
                trade_fav  = fav_t
                trade_adv  = adv_t
                t_fav_time = time_fav
                t_adv_time = time_adv
            else:  # reverse
                trade_fav  = adv_t
                trade_adv  = fav_t
                t_fav_time = time_adv
                t_adv_time = time_fav

            for tgt_pct in target_percents:
                target_ticks = sig_t * tgt_pct / 100.0
                tgt_reached  = trade_fav >= target_ticks

                for stp_pct in stop_percents:
                    if stp_pct is not None:
                        stop_ticks  = sig_t * stp_pct / 100.0
                        stp_reached = trade_adv >= stop_ticks
                    else:
                        stop_ticks  = None
                        stp_reached = False

                    # Determine outcome
                    if not stop_enabled or stop_ticks is None:
                        win       = tgt_reached
                        first_hit = "target" if tgt_reached else "neither"
                        stop_hit  = None
                    elif tgt_reached and not stp_reached:
                        win = True;  first_hit = "target";  stop_hit = False
                    elif not tgt_reached and stp_reached:
                        win = False; first_hit = "stop";    stop_hit = True
                    elif tgt_reached and stp_reached:
                        # Both reachable — approximate by time-to-max
                        if t_fav_time is not None and t_adv_time is not None:
                            win       = t_fav_time <= t_adv_time
                            first_hit = "target" if win else "stop"
                        else:
                            win       = True    # favour target if no timing info
                            first_hit = "target"
                        stop_hit = True
                    else:
                        win = False; first_hit = "neither"; stop_hit = False

                    rec = dict(base)
                    rec.update({
                        "trade_mode":      trade_mode,
                        "trade_direction": trade_direction(direction, trade_mode),
                        "target_percent":  tgt_pct,
                        "target_ticks":    round(target_ticks, 2),
                        "trade_fav_ticks": round(trade_fav, 2),
                        "trade_adv_ticks": round(trade_adv, 2),
                        "win":             win,
                        "stop_percent":    stp_pct,
                        "stop_ticks":      round(stop_ticks, 2) if stop_ticks is not None else None,
                        "stop_hit":        stop_hit,
                        "first_hit":       first_hit,
                    })
                    event_records.append(rec)

    if not event_records:
        return {
            "enabled": True,
            "trade_combo_results": [],
            "trade_events_sample": [],
            "config": trade_cfg,
        }

    df = pd.DataFrame(event_records)

    # Aggregate
    group_cols = ["tf_minutes", "lookback", "basis", "threshold_mode", "threshold_value",
                  "window_minutes", "direction", "trade_mode", "target_percent", "candle_bucket"]
    if stop_enabled:
        group_cols.append("stop_percent")

    present = [c for c in group_cols if c in df.columns]
    combo_results: List[Dict] = []

    for keys, grp in df.groupby(present, sort=False):
        key_dict = dict(zip(present, keys if isinstance(keys, tuple) else (keys,)))
        n       = len(grp)
        n_wins  = int(grp["win"].sum())
        win_rate = round(n_wins / n * 100, 1) if n > 0 else 0.0

        rec: Dict[str, Any] = {
            **key_dict,
            "n_events":              n,
            "n_wins":                n_wins,
            "n_losses":              n - n_wins,
            "win_rate":              win_rate,
            "avg_signal_ticks":      _sr(grp["signal_candle_ticks"].mean()),
            "median_signal_ticks":   _sr(grp["signal_candle_ticks"].median()),
            "avg_target_ticks":      _sr(grp["target_ticks"].mean()),
            "median_target_ticks":   _sr(grp["target_ticks"].median()),
            "avg_trade_fav_ticks":   _sr(grp["trade_fav_ticks"].mean()),
            "median_trade_fav_ticks":_sr(grp["trade_fav_ticks"].median()),
            "avg_trade_adv_ticks":   _sr(grp["trade_adv_ticks"].mean()),
            "median_trade_adv_ticks":_sr(grp["trade_adv_ticks"].median()),
        }
        if stop_enabled and "first_hit" in grp.columns:
            rec["n_target_first"] = int((grp["first_hit"] == "target").sum())
            rec["n_stop_first"]   = int((grp["first_hit"] == "stop").sum())
            rec["n_neither"]      = int((grp["first_hit"] == "neither").sum())
            if "stop_hit" in grp.columns:
                rec["n_stop_hit"] = int(grp["stop_hit"].fillna(False).astype(bool).sum())

        combo_results.append(rec)

    # Sanitise event sample
    sample: List[Dict] = []
    for i, rec in enumerate(event_records):
        if i >= max_sample:
            break
        s: Dict[str, Any] = {}
        for k, v in rec.items():
            if isinstance(v, (np.integer,)):
                s[k] = int(v)
            elif isinstance(v, (np.floating,)):
                s[k] = None if v != v else float(v)
            elif isinstance(v, pd.Timestamp):
                s[k] = v.isoformat()
            else:
                s[k] = v
        sample.append(s)

    return {
        "enabled":              True,
        "trade_combo_results":  combo_results,
        "trade_events_sample":  sample,
        "config":               trade_cfg,
    }
