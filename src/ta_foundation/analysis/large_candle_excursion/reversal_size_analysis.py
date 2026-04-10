from __future__ import annotations

"""
Reversal Size Analysis
======================
Classifies large-candle reversal setups by the SIZE of the post-entry move.

Motivation
----------
The continuation-vs-reversal binary answer is insufficient for trading decisions.
A setup that "reverses" 10% of the signal candle is a scalp.
A setup that reverses 75%+ with strong conditional continuation is a runner.
This module separates them.

Key convention
--------------
  Signal direction  = the large candle's direction (1 bull, -1 bear).
  Reversal trade    = fading the signal (entering against signal direction).
  Reversal MFE      = adv_ticks  (the "adverse" excursion for the signal is
                      the favorable excursion for a reversal trade).
  Reversal heat     = fav_ticks  (continuation excursion = adversity for fade).

All reversal percentages are normalized by size_ticks (the signal candle size).
"""

from typing import Any, Dict, List, Optional, Tuple

import math

# ---------------------------------------------------------------------------
# Default configuration
# ---------------------------------------------------------------------------

DEFAULT_REVERSAL_SIZE_CONFIG: Dict[str, Any] = {
    "enabled": False,
    # Which forward window to use for analysis (largest gives most room to develop)
    "forward_window_minutes": 30,
    # Target tiers as % of signal candle size
    "target_percents": [10, 25, 50, 75, 100, 150],
    # Move-size tier labels (contiguous, based on adv_pct)
    "move_size_tiers": [
        {"label": "failed",   "max": 10},
        {"label": "micro",    "min": 10,  "max": 25},
        {"label": "standard", "min": 25,  "max": 50},
        {"label": "large",    "min": 50,  "max": 100},
        {"label": "extended", "min": 100},
    ],
    # Weights for runner_potential_score
    "runner_potential_weights": {
        "p50_given_25": 0.35,
        "p75_given_50": 0.40,
        "p100_given_50": 0.25,
    },
    # Minimum N for each level of grouping
    "min_n_setup":   30,
    "min_n_session": 20,
    "min_n_context": 25,
    # Large-move candidate promotion criteria
    "large_move_candidate": {
        "min_n":           40,
        "min_p50_pct":     20.0,   # at least 20% reach 50% of candle
        "min_runner_score": 0.40,
    },
    # Context feature columns to analyse for large-move conditioning
    "context_features": [
        ("session_bucket",           "Session"),
        ("exhaustion_label",         "Exhaustion Type"),
        ("vwap_signed_bucket",       "VWAP Location"),
        ("directional_context_label","Directional Context"),
        ("trend_alignment_label",    "Trend Alignment"),
        ("level_interaction_label",  "Level Interaction"),
        ("candle_bucket",            "Candle Size"),
    ],
    "top_n_setups":  10,
    "top_n_context": 12,
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _safe_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def _classify_tier(adv_pct: float, tiers: List[Dict]) -> str:
    for t in tiers:
        lo = float(t.get("min", float("-inf")))
        hi = float(t.get("max", float("inf")))
        if lo <= adv_pct < hi:
            return t["label"]
    return "unknown"


def _percentile(sorted_vals: List[float], p: float) -> float:
    """p in [0, 1]. Returns percentile value from pre-sorted list."""
    if not sorted_vals:
        return 0.0
    idx = int(len(sorted_vals) * p)
    idx = min(idx, len(sorted_vals) - 1)
    return sorted_vals[idx]


def _event_metrics(ev: Dict[str, Any], targets_pct: List[int]) -> Optional[Dict[str, Any]]:
    """Compute per-event reversal metrics. Returns None if data missing."""
    adv  = _safe_float(ev.get("adv_ticks"))
    fav  = _safe_float(ev.get("fav_ticks"))
    size = _safe_float(ev.get("size_ticks"))
    ttm  = _safe_float(ev.get("time_to_max_adv_min"))   # time to max reversal

    if adv is None or fav is None or size is None or size <= 0:
        return None

    adv_pct = adv / size * 100.0
    fav_pct = fav / size * 100.0
    mfe_mae = adv_pct / fav_pct if fav_pct > 0 else None

    reaches: Dict[int, bool] = {t: adv >= size * t / 100.0 for t in targets_pct}

    return {
        "adv_pct":   adv_pct,
        "fav_pct":   fav_pct,
        "mfe_mae":   mfe_mae,
        "time_to_max_rev_min": ttm,
        "reaches":   reaches,
    }


def _aggregate(
    metrics_list: List[Dict[str, Any]],
    targets_pct: List[int],
    tiers: List[Dict],
    runner_weights: Dict[str, float],
    pop_reach_50: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """Aggregate a list of per-event metrics into summary statistics."""
    if not metrics_list:
        return None
    n = len(metrics_list)

    adv_pcts    = sorted([m["adv_pct"]  for m in metrics_list])
    fav_pcts    = sorted([m["fav_pct"]  for m in metrics_list])
    ttm_vals    = [m["time_to_max_rev_min"] for m in metrics_list if m["time_to_max_rev_min"] is not None]

    # Reach rates
    reach: Dict[int, int] = {t: 0 for t in targets_pct}
    for m in metrics_list:
        for t in targets_pct:
            if m["reaches"].get(t, False):
                reach[t] += 1

    reach_pct: Dict[int, float] = {t: round(cnt / n * 100, 1) for t, cnt in reach.items()}

    # Runner potential (conditional probabilities)
    r25  = reach.get(25, 0)
    r50  = reach.get(50, 0)
    r75  = reach.get(75, 0)
    r100 = reach.get(100, 0)

    p50_given_25  = round(r50  / r25  * 100, 1) if r25  > 0 else 0.0
    p75_given_50  = round(r75  / r50  * 100, 1) if r50  > 0 else 0.0
    p100_given_50 = round(r100 / r50  * 100, 1) if r50  > 0 else 0.0

    wts = runner_weights
    runner_score = round(
        wts.get("p50_given_25",  0.35) * p50_given_25  / 100 +
        wts.get("p75_given_50",  0.40) * p75_given_50  / 100 +
        wts.get("p100_given_50", 0.25) * p100_given_50 / 100,
        3,
    )

    # MFE / heat distribution
    mean_adv = round(sum(adv_pcts) / n, 1)
    mean_fav = round(sum(fav_pcts) / n, 1)
    mfe_mae_ratio = round(mean_adv / mean_fav, 2) if mean_fav > 0 else None

    # Tier distribution
    tier_counts: Dict[str, int] = {}
    for m in metrics_list:
        lbl = _classify_tier(m["adv_pct"], tiers)
        tier_counts[lbl] = tier_counts.get(lbl, 0) + 1
    tier_pcts = {k: round(v / n * 100, 1) for k, v in tier_counts.items()}

    # Time metrics
    ttm_sorted = sorted(ttm_vals)
    ttm_p50 = round(_percentile(ttm_sorted, 0.50), 1) if ttm_sorted else None
    ttm_p75 = round(_percentile(ttm_sorted, 0.75), 1) if ttm_sorted else None

    # Lift vs population for large-move targeting
    lift_vs_pop_50 = None
    if pop_reach_50 is not None:
        lift_vs_pop_50 = round(reach_pct.get(50, 0) - pop_reach_50, 1)

    return {
        "n":                  n,
        "reach_pct":          reach_pct,        # {10: 71.2, 25: 52.3, 50: 28.4, ...}
        "p50_given_25":       p50_given_25,
        "p75_given_50":       p75_given_50,
        "p100_given_50":      p100_given_50,
        "runner_score":       runner_score,
        "mfe_p50_pct":        round(_percentile(adv_pcts, 0.50), 1),
        "mfe_p75_pct":        round(_percentile(adv_pcts, 0.75), 1),
        "mfe_p90_pct":        round(_percentile(adv_pcts, 0.90), 1),
        "mfe_mean_pct":       mean_adv,
        "mae_mean_pct":       mean_fav,         # heat = continuation excursion
        "mfe_mae_ratio":      mfe_mae_ratio,
        "ttm_p50_min":        ttm_p50,
        "ttm_p75_min":        ttm_p75,
        "tier_distribution":  tier_pcts,
        "lift_50_vs_pop_pp":  lift_vs_pop_50,
    }


def _is_large_move_candidate(agg: Dict, lmc_cfg: Dict) -> bool:
    return (
        agg["n"] >= int(lmc_cfg.get("min_n", 40)) and
        agg["reach_pct"].get(50, 0) >= float(lmc_cfg.get("min_p50_pct", 20.0)) and
        agg["runner_score"] >= float(lmc_cfg.get("min_runner_score", 0.40))
    )


def _dominant_value(vals: List[Any]) -> Optional[str]:
    if not vals:
        return None
    counts: Dict[Any, int] = {}
    for v in vals:
        if v:
            counts[str(v)] = counts.get(str(v), 0) + 1
    return max(counts, key=lambda k: counts[k]) if counts else None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def compute_reversal_size_analysis(
    events_sample: List[Dict[str, Any]],
    cfg: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Compute all reversal-size metrics from the enriched events sample.

    Parameters
    ----------
    events_sample : list of event record dicts (from sweep._event_to_record)
    cfg           : reversal_size_analysis config (merged with DEFAULT)

    Returns
    -------
    Rich dict with population stats, setup rankings, session analysis, and
    context-conditioned large-move rankings.
    """
    merged = _deep_merge(DEFAULT_REVERSAL_SIZE_CONFIG, cfg or {})
    if not merged.get("enabled", False):
        return {"enabled": False}
    if not events_sample:
        return {"enabled": True, "n_events": 0, "message": "no events in sample"}

    target_pcts: List[int]   = [int(t) for t in merged.get("target_percents", [10, 25, 50, 75, 100, 150])]
    tiers:       List[Dict]  = merged.get("move_size_tiers", DEFAULT_REVERSAL_SIZE_CONFIG["move_size_tiers"])
    rw:          Dict        = merged.get("runner_potential_weights", DEFAULT_REVERSAL_SIZE_CONFIG["runner_potential_weights"])
    lmc_cfg:     Dict        = merged.get("large_move_candidate", DEFAULT_REVERSAL_SIZE_CONFIG["large_move_candidate"])
    ctx_feats:   List        = merged.get("context_features", DEFAULT_REVERSAL_SIZE_CONFIG["context_features"])
    desired_win: int         = int(merged.get("forward_window_minutes", 30))
    min_n_setup: int         = int(merged.get("min_n_setup", 30))
    min_n_sess:  int         = int(merged.get("min_n_session", 20))
    min_n_ctx:   int         = int(merged.get("min_n_context", 25))
    top_n_setups:int         = int(merged.get("top_n_setups", 10))
    top_n_ctx:   int         = int(merged.get("top_n_context", 12))

    # -------------------------------------------------------------------------
    # Filter to desired window; fall back to next largest if insufficient
    # -------------------------------------------------------------------------
    def _filter_to_window(events: List[Dict], win: int) -> List[Dict]:
        return [ev for ev in events if ev.get("window_minutes") == win]

    filtered = _filter_to_window(events_sample, desired_win)
    if len(filtered) < min_n_setup:
        # Try largest available window
        all_windows = sorted({ev.get("window_minutes") for ev in events_sample if ev.get("window_minutes")}, reverse=True)
        for w in all_windows:
            cands = _filter_to_window(events_sample, w)
            if len(cands) >= min_n_setup:
                filtered = cands
                desired_win = w
                break
        else:
            filtered = events_sample   # last resort: all events

    n_total = len(filtered)

    # -------------------------------------------------------------------------
    # Compute per-event metrics
    # -------------------------------------------------------------------------
    ev_metrics: List[Optional[Dict]] = [_event_metrics(ev, target_pcts) for ev in filtered]
    valid_pairs = [(ev, m) for ev, m in zip(filtered, ev_metrics) if m is not None]
    valid_events = [p[0] for p in valid_pairs]
    valid_mets   = [p[1] for p in valid_pairs]

    if not valid_mets:
        return {"enabled": True, "n_events": n_total, "message": "insufficient adv/fav data in events"}

    # -------------------------------------------------------------------------
    # Population aggregation
    # -------------------------------------------------------------------------
    pop_agg = _aggregate(valid_mets, target_pcts, tiers, rw)
    pop_reach_50 = pop_agg["reach_pct"].get(50, 0) if pop_agg else None

    # -------------------------------------------------------------------------
    # Setup family rankings
    # -------------------------------------------------------------------------
    family_groups: Dict[str, Tuple[List[Dict], List[Dict]]] = {}
    for ev, m in zip(valid_events, valid_mets):
        direction = int(ev.get("direction", 0))
        tf        = ev.get("tf_minutes")
        cb        = ev.get("candle_bucket", "unknown")
        label     = f"{'Bull' if direction == 1 else 'Bear'} / {tf}m / {cb}"
        if label not in family_groups:
            family_groups[label] = ([], [])
        family_groups[label][0].append(ev)
        family_groups[label][1].append(m)

    setup_rankings: List[Dict] = []
    for fam_label, (fam_evs, fam_mets) in family_groups.items():
        if len(fam_mets) < min_n_setup:
            continue
        agg = _aggregate(fam_mets, target_pcts, tiers, rw, pop_reach_50=pop_reach_50)
        if agg is None:
            continue

        dominant_session = _dominant_value([ev.get("session_bucket") for ev in fam_evs])
        dominant_exhaustion = _dominant_value([ev.get("exhaustion_label") for ev in fam_evs if ev.get("exhaustion_label") not in (None, "none", "unknown")])
        is_lmc = _is_large_move_candidate(agg, lmc_cfg)

        entry = {
            "family_label":          fam_label,
            "dominant_session":      dominant_session,
            "dominant_exhaustion":   dominant_exhaustion,
            "is_large_move_candidate": is_lmc,
        }
        entry.update(agg)
        setup_rankings.append(entry)

    setup_rankings.sort(
        key=lambda r: (-(r.get("reach_pct", {}).get(50, 0)), -r.get("runner_score", 0))
    )
    setup_rankings = setup_rankings[:top_n_setups]

    # -------------------------------------------------------------------------
    # Session analysis
    # -------------------------------------------------------------------------
    sess_groups: Dict[str, Tuple[List, List]] = {}
    for ev, m in zip(valid_events, valid_mets):
        sess = ev.get("session_bucket") or "unknown"
        if sess not in sess_groups:
            sess_groups[sess] = ([], [])
        sess_groups[sess][0].append(ev)
        sess_groups[sess][1].append(m)

    session_analysis: Dict[str, Dict] = {}
    for sess, (sess_evs, sess_mets) in sess_groups.items():
        if len(sess_mets) < min_n_sess:
            continue
        agg = _aggregate(sess_mets, target_pcts, tiers, rw, pop_reach_50=pop_reach_50)
        if agg:
            session_analysis[sess] = agg

    sessions_ranked = sorted(
        session_analysis.items(),
        key=lambda kv: (-(kv[1].get("reach_pct", {}).get(50, 0)), -kv[1].get("runner_score", 0)),
    )

    # -------------------------------------------------------------------------
    # Context-conditioned large-move rankings
    # -------------------------------------------------------------------------
    context_rankings: List[Dict] = []

    for feat_col, feat_label in ctx_feats:
        # Group events by this feature bucket
        bucket_groups: Dict[str, Tuple[List, List]] = {}
        for ev, m in zip(valid_events, valid_mets):
            val = ev.get(feat_col)
            if val is None or str(val) in ("unknown", "none", "other", "nan"):
                continue
            key = str(val)
            if key not in bucket_groups:
                bucket_groups[key] = ([], [])
            bucket_groups[key][0].append(ev)
            bucket_groups[key][1].append(m)

        for bucket_val, (b_evs, b_mets) in bucket_groups.items():
            if len(b_mets) < min_n_ctx:
                continue
            agg = _aggregate(b_mets, target_pcts, tiers, rw, pop_reach_50=pop_reach_50)
            if agg is None:
                continue
            lift_50 = agg.get("lift_50_vs_pop_pp", 0) or 0
            if abs(lift_50) < 3.0:
                continue

            context_rankings.append({
                "context_feature":  feat_label,
                "context_col":      feat_col,
                "context_bucket":   bucket_val,
                "lift_50_pp":       round(lift_50, 1),
            } | {k: v for k, v in agg.items() if k != "lift_50_vs_pop_pp"})

    context_rankings.sort(
        key=lambda r: (-(r.get("reach_pct", {}).get(50, 0)), -r.get("runner_score", 0))
    )
    context_rankings = context_rankings[:top_n_ctx]

    return {
        "enabled":           True,
        "n_events":          n_total,
        "n_valid":           len(valid_mets),
        "forward_window_min": desired_win,
        "targets_pct":       target_pcts,
        "population":        pop_agg,
        "setup_rankings":    setup_rankings,
        "session_analysis":  session_analysis,
        "sessions_ranked":   [{"session": k, **v} for k, v in sessions_ranked],
        "context_rankings":  context_rankings,
        "config":            merged,
    }


def _deep_merge(base: Dict, override: Dict) -> Dict:
    result = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(result.get(k), dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result
