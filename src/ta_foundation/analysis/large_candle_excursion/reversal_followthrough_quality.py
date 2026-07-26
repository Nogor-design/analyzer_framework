from __future__ import annotations

"""
Reversal Follow-Through Quality
================================
Second-stage analysis module that evaluates WHAT HAPPENS AFTER a large-candle
reversal entry, not just whether it wins.

Design contract
---------------
  Input  : events_sample list (from sweep._event_to_record), enriched with
           context columns and early-path bar features from forward_window.py.
  Output : structured findings dict containing:
             - per-event dataset (RFQ-enriched rows)
             - outcome class distribution
             - feature importance ranking (which context most predicts expansion)
             - conditional probability matrix
             - session breakdown for expansion setups
             - two strategy profiles: scalp vs expansion

Reversal convention
-------------------
  For a signal candle in direction d:
    Reversal MFE  = adv_ticks  (the market moves AGAINST the signal = favorable for the fade)
    Reversal heat = fav_ticks  (continuation move = adverse for the fade trade)

  Outcome classes (configurable thresholds):
    failed_reversal   adv_pct <  10%  — no meaningful move
    micro_bounce      adv_pct  10–25% — small fade, not enough to trade
    scalp_reversal    adv_pct  25–50% — tradable with tight target
    expansion_reversal adv_pct  50%+  — meaningful follow-through
"""

from typing import Any, Dict, List, Optional, Tuple

import math

# ---------------------------------------------------------------------------
# Default configuration
# ---------------------------------------------------------------------------

DEFAULT_RFQ_CONFIG: Dict[str, Any] = {
    "enabled": False,
    # Which forward window to use for analysis
    "forward_window_minutes": 30,
    # Target levels as % of signal candle size
    "target_percents": [25, 50, 75, 100],
    # Outcome class thresholds (adv_pct = adv_ticks / size_ticks * 100)
    "outcome_thresholds": {
        "failed_reversal":    {"max_adv_pct": 10},
        "micro_bounce":       {"min_adv_pct": 10,  "max_adv_pct": 25},
        "scalp_reversal":     {"min_adv_pct": 25,  "max_adv_pct": 50},
        "expansion_reversal": {"min_adv_pct": 50},
    },
    # Feature columns to include in feature importance analysis
    "importance_features": [
        ("session_bucket",             "Session"),
        ("exhaustion_label",           "Exhaustion Type"),
        ("vwap_signed_bucket",         "VWAP Location"),
        ("directional_context_label",  "Directional Context"),
        ("trend_alignment_label",      "Trend Alignment"),
        ("level_interaction_label",    "Level Interaction"),
        ("nearest_level_type",         "Nearest Level Type"),
        ("candle_bucket",              "Candle Size"),
        ("engulf_bucket",              "Engulf"),
        ("prev_direction_bucket",      "Prev Direction"),
        ("ma100_signed_bucket",        "MA100 Location"),
    ],
    # Minimum sample sizes
    "min_n_overall":    30,
    "min_n_feature":    20,
    # Feature importance: top N to show
    "top_n_importance": 15,
    # Conditional probability targets
    "conditional_pairs": [(50, 75), (50, 100), (25, 50)],
    # Strategy profile promotion criteria
    "strategy": {
        "scalp": {
            "target_pct":             25,
            "stop_pct":               75,   # stop if continuation reaches 75%
            "min_n":                  30,
            "min_scalp_reach_pct":    40.0, # at least 40% reach the 25% target
        },
        "expansion": {
            "target_pct":             50,
            "stop_pct":               40,
            "min_n":                  30,
            "min_expansion_rate":     20.0, # at least 20% are expansion_reversal
            "min_runner_score":       0.40,
        },
    },
    # Max events to include in the per-event dataset output
    "max_dataset_rows": 500,
}

# Order used for display
_OUTCOME_ORDER = ["expansion_reversal", "scalp_reversal", "micro_bounce", "failed_reversal"]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _safe(v: Any, default: Optional[float] = None) -> Optional[float]:
    if v is None:
        return default
    try:
        f = float(v)
        return default if math.isnan(f) else f
    except (TypeError, ValueError):
        return default


def _safe_int(v: Any, default: int = 0) -> int:
    if v is None:
        return default
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _percentile(vals: List[float], p: float) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    idx = min(int(len(s) * p), len(s) - 1)
    return s[idx]


def _mean(vals: List[float]) -> Optional[float]:
    return round(sum(vals) / len(vals), 2) if vals else None


def _classify_outcome(adv_pct: float, thresholds: Dict[str, Dict]) -> str:
    """Classify reversal outcome by adv_pct."""
    if adv_pct < float(thresholds.get("failed_reversal",   {}).get("max_adv_pct", 10)):
        return "failed_reversal"
    if adv_pct < float(thresholds.get("micro_bounce",      {}).get("max_adv_pct", 25)):
        return "micro_bounce"
    if adv_pct < float(thresholds.get("scalp_reversal",    {}).get("max_adv_pct", 50)):
        return "scalp_reversal"
    return "expansion_reversal"


def _runner_score(r25: int, r50: int, r75: int, r100: int) -> float:
    p50_25  = r50  / r25  if r25  > 0 else 0.0
    p75_50  = r75  / r50  if r50  > 0 else 0.0
    p100_50 = r100 / r50  if r50  > 0 else 0.0
    return round(0.35 * p50_25 + 0.40 * p75_50 + 0.25 * p100_50, 3)


def _enrich_event(ev: Dict[str, Any], targets: List[int], thresholds: Dict) -> Optional[Dict]:
    """
    Enrich a single event dict with RFQ outcome fields.
    Returns None if the event lacks sufficient data.
    """
    adv  = _safe(ev.get("adv_ticks"))
    fav  = _safe(ev.get("fav_ticks"))
    size = _safe(ev.get("size_ticks"))

    if adv is None or fav is None or size is None or size <= 0:
        return None

    adv_pct = adv / size * 100.0
    fav_pct = fav / size * 100.0   # heat for reversal trade
    mfe_mae = adv_pct / fav_pct if fav_pct > 0 else None

    # Target reach labels
    hit: Dict[int, bool] = {t: adv >= size * t / 100.0 for t in targets}

    # Conditional follow-through
    continued_75_given_50  = bool(hit.get(75,  False)) if hit.get(50, False) else None
    continued_100_given_50 = bool(hit.get(100, False)) if hit.get(50, False) else None

    # Outcome class
    outcome_class = _classify_outcome(adv_pct, thresholds)

    # Derived feature: early momentum (1-bar reversal as % of candle)
    early_fav_1 = _safe(ev.get("early_fav_1bar_ticks"))
    early_adv_1 = _safe(ev.get("early_adv_1bar_ticks"))
    early_fav_3 = _safe(ev.get("early_fav_3bar_ticks"))
    early_adv_3 = _safe(ev.get("early_adv_3bar_ticks"))

    early_momentum_1bar_pct = round(early_fav_1 / size * 100.0, 1) if early_fav_1 is not None else None
    early_heat_1bar_pct     = round(early_adv_1 / size * 100.0, 1) if early_adv_1 is not None else None
    early_momentum_3bar_pct = round(early_fav_3 / size * 100.0, 1) if early_fav_3 is not None else None

    # Distance from VWAP in candle-size units (signed)
    dist_vwap_atr  = _safe(ev.get("dist_vwap_atr"))
    atr_val        = _safe(ev.get("atr"))
    dist_vwap_candle_pct = None
    if dist_vwap_atr is not None and atr_val is not None and size > 0:
        dist_vwap_candle_pct = round(dist_vwap_atr * atr_val / size * 100.0, 1)

    # Signal structure features (already computed by context_enricher)
    body_pct   = _safe(ev.get("body_to_range_ratio"))
    uw_pct     = _safe(ev.get("upper_wick_to_range_ratio"))
    lw_pct     = _safe(ev.get("lower_wick_to_range_ratio"))
    close_pos  = _safe(ev.get("close_position_in_range"))

    # Session microstructure
    direction  = int(ev.get("direction", 0))
    session    = ev.get("session_bucket", "unknown")

    # is_at_key_level shorthand
    level_int  = ev.get("level_interaction_label", "unknown")
    is_at_level = level_int in ("at_level", "approaching")

    rfq = {
        # — Core identity —
        "dt":               ev.get("dt"),
        "direction":        direction,
        "tf_minutes":       ev.get("tf_minutes"),
        "window_minutes":   ev.get("window_minutes"),
        "session_bucket":   session,
        "candle_bucket":    ev.get("candle_bucket"),

        # — Outcome labels —
        "adv_pct":          round(adv_pct,  1),
        "fav_pct":          round(fav_pct,  1),   # heat
        "mfe_mae_ratio":    round(mfe_mae,  2) if mfe_mae is not None else None,
        "outcome_class":    outcome_class,
        **{f"hit_{t}_pct": hit.get(t, False) for t in targets},
        "continued_to_75_given_50":  continued_75_given_50,
        "continued_to_100_given_50": continued_100_given_50,
        "time_to_max_rev_min":       ev.get("time_to_max_adv_min"),

        # — Early-path features —
        "early_momentum_1bar_pct":    early_momentum_1bar_pct,
        "early_heat_1bar_pct":        early_heat_1bar_pct,
        "early_momentum_3bar_pct":    early_momentum_3bar_pct,
        "did_price_reclaim_midpoint": ev.get("did_price_reclaim_signal_midpoint"),
        "did_break_extreme_again":    ev.get("did_price_break_signal_extreme_again"),
        "time_to_first_pullback_bars":ev.get("time_to_first_pullback_bars"),
        "first_pullback_size_ticks":  ev.get("first_pullback_size_ticks"),

        # — Pre-signal extension features —
        "dist_vwap_atr":              dist_vwap_atr,
        "dist_vwap_candle_pct":       dist_vwap_candle_pct,
        "dist_ma100_atr":             _safe(ev.get("dist_ma100_atr")),
        "vwap_signed_bucket":         ev.get("vwap_signed_bucket"),
        "ma100_signed_bucket":        ev.get("ma100_signed_bucket"),
        "directional_streak":         ev.get("direction_streak"),
        "directional_context_label":  ev.get("directional_context_label"),
        "exhaustion_label":           ev.get("exhaustion_label"),

        # — Signal candle structure —
        "body_pct_of_range":          round(body_pct * 100, 1) if body_pct is not None else None,
        "upper_wick_pct":             round(uw_pct   * 100, 1) if uw_pct   is not None else None,
        "lower_wick_pct":             round(lw_pct   * 100, 1) if lw_pct   is not None else None,
        "close_position_in_range":    round(close_pos * 100, 1) if close_pos is not None else None,
        "range_vs_atr":               _safe(ev.get("range_as_atr")),
        "relative_volume":            _safe(ev.get("relative_volume")),
        "volume_spike":               bool(_safe(ev.get("relative_volume"), 0) >= 2.0),

        # — Location features —
        "nearest_level_type":         ev.get("nearest_level_type"),
        "level_interaction_label":    level_int,
        "is_at_or_approaching_level": is_at_level,
        "dist_nearest_level_atr":     _safe(ev.get("dist_nearest_level_atr")),

        # — Trend state —
        "trend_alignment_label":      ev.get("trend_alignment_label"),
        "stacked_above_mas":          ev.get("stacked_above_mas"),
        "stacked_below_mas":          ev.get("stacked_below_mas"),
        "prev_direction_bucket":      ev.get("prev_direction_bucket"),
        "engulf_bucket":              ev.get("engulf_bucket"),
    }
    return rfq


# ---------------------------------------------------------------------------
# Aggregate helpers
# ---------------------------------------------------------------------------

def _agg_stats(rows: List[Dict], targets: List[int]) -> Optional[Dict]:
    """Aggregate outcome metrics over a group of enriched RFQ rows."""
    if not rows:
        return None
    n = len(rows)

    reach  = {t: sum(1 for r in rows if r.get(f"hit_{t}_pct", False)) for t in targets}
    r_pcts = {t: round(reach[t] / n * 100, 1) for t in targets}

    r25, r50, r75, r100 = (reach.get(t, 0) for t in [25, 50, 75, 100])
    rscore = _runner_score(r25, r50, r75, r100)

    p50_25  = round(r50  / r25  * 100, 1) if r25  > 0 else 0.0
    p75_50  = round(r75  / r50  * 100, 1) if r50  > 0 else 0.0
    p100_50 = round(r100 / r50  * 100, 1) if r50  > 0 else 0.0

    adv_pcts  = [r["adv_pct"]  for r in rows if r.get("adv_pct")  is not None]
    fav_pcts  = [r["fav_pct"]  for r in rows if r.get("fav_pct")  is not None]
    ttm_vals  = [float(r["time_to_max_rev_min"]) for r in rows
                 if r.get("time_to_max_rev_min") is not None]

    # Outcome distribution
    dist: Dict[str, int] = {}
    for r in rows:
        oc = r.get("outcome_class", "unknown")
        dist[oc] = dist.get(oc, 0) + 1
    dist_pct = {k: round(v / n * 100, 1) for k, v in dist.items()}
    expansion_rate = dist_pct.get("expansion_reversal", 0.0)

    return {
        "n":                  n,
        "reach_pct":          r_pcts,
        "p50_given_25":       p50_25,
        "p75_given_50":       p75_50,
        "p100_given_50":      p100_50,
        "runner_score":       rscore,
        "expansion_rate":     expansion_rate,
        "mfe_mean_pct":       round(_mean(adv_pcts) or 0, 1),
        "mfe_p50_pct":        round(_percentile(adv_pcts, 0.50), 1),
        "mfe_p75_pct":        round(_percentile(adv_pcts, 0.75), 1),
        "mfe_p90_pct":        round(_percentile(adv_pcts, 0.90), 1),
        "mae_mean_pct":       round(_mean(fav_pcts) or 0, 1),
        "ttm_p50_min":        round(_percentile(ttm_vals, 0.50), 1) if ttm_vals else None,
        "outcome_distribution": dist_pct,
    }


def _compute_feature_importance(
    dataset: List[Dict],
    features: List[Tuple[str, str]],
    pop_expansion_rate: float,
    min_n: int,
) -> List[Dict]:
    """
    For each categorical feature, compute edge = expansion_rate_bucket - population_rate.
    Returns list of dicts sorted by absolute edge, descending.
    """
    results: List[Dict] = []

    for feat_col, feat_label in features:
        bucket_groups: Dict[str, List[Dict]] = {}
        for r in dataset:
            val = r.get(feat_col)
            if val is None or str(val) in ("unknown", "none", "nan", "other"):
                continue
            bucket_groups.setdefault(str(val), []).append(r)

        for bval, brows in bucket_groups.items():
            if len(brows) < min_n:
                continue
            n = len(brows)
            exp_n = sum(1 for r in brows if r.get("outcome_class") == "expansion_reversal")
            exp_rate = round(exp_n / n * 100, 1)
            lift = round(exp_rate - pop_expansion_rate, 1)

            results.append({
                "feature_col":     feat_col,
                "feature_label":   feat_label,
                "bucket":          bval,
                "n":               n,
                "expansion_rate":  exp_rate,
                "lift_pp":         lift,
                "direction":       "positive" if lift >= 0 else "negative",
            })

    results.sort(key=lambda r: -abs(r["lift_pp"]))
    return results


def _compute_conditional_probabilities(
    dataset: List[Dict],
    pairs: List[Tuple[int, int]],
    group_cols: Optional[List[str]] = None,
) -> List[Dict]:
    """
    Compute P(reach target_b | reached target_a) for configured pairs.
    If group_cols provided, also compute by group.
    """
    results: List[Dict] = []

    for ta, tb in pairs:
        col_a = f"hit_{ta}_pct"
        col_b = f"hit_{tb}_pct"
        reached_a = [r for r in dataset if r.get(col_a, False)]
        if not reached_a:
            continue
        reached_b = sum(1 for r in reached_a if r.get(col_b, False))
        results.append({
            "given":       ta,
            "target":      tb,
            "n_given":     len(reached_a),
            "n_reached":   reached_b,
            "probability": round(reached_b / len(reached_a) * 100, 1),
        })

    return results


def _compute_strategy_profiles(
    dataset: List[Dict],
    feature_importance: List[Dict],
    pop_stats: Dict,
    targets: List[int],
    cfg: Dict,
) -> Dict[str, Any]:
    """
    Build two strategy profile dicts: scalp_reversal and expansion_reversal.
    Each profile includes:
      - top entry conditions (context filters with positive lift)
      - expected outcome distribution
      - target and stop logic
      - session constraints
    """
    strategy_cfg = cfg.get("strategy", DEFAULT_RFQ_CONFIG["strategy"])

    # Top entry conditions for expansion
    exp_conditions = [f for f in feature_importance if f["lift_pp"] >= 5.0][:6]
    exp_avoids     = [f for f in feature_importance if f["lift_pp"] <= -5.0][:4]

    # Scalp conditions: high reach_25% but low expansion rate
    scalp_conditions = [f for f in feature_importance if f["lift_pp"] >= 3.0][:6]

    # Session breakdown (expansion rate by session)
    sess_groups: Dict[str, List] = {}
    for r in dataset:
        s = r.get("session_bucket", "unknown")
        sess_groups.setdefault(s, []).append(r)

    sess_stats = {}
    for s, rows in sorted(sess_groups.items()):
        if len(rows) < 15:
            continue
        exp_n = sum(1 for r in rows if r.get("outcome_class") == "expansion_reversal")
        r25   = sum(1 for r in rows if r.get("hit_25_pct", False))
        r50   = sum(1 for r in rows if r.get("hit_50_pct", False))
        n     = len(rows)
        sess_stats[s] = {
            "n": n,
            "expansion_rate": round(exp_n / n * 100, 1),
            "reach_25_pct":   round(r25 / n * 100, 1),
            "reach_50_pct":   round(r50 / n * 100, 1),
        }

    best_expansion_sessions = sorted(
        [(s, v["expansion_rate"]) for s, v in sess_stats.items()],
        key=lambda kv: -kv[1]
    )[:3]

    return {
        "expansion_reversal": {
            "description":      "Fade the large candle when context signals exhaustion and market structure supports reversal.",
            "target_pct":       strategy_cfg.get("expansion", {}).get("target_pct", 50),
            "stop_pct":         strategy_cfg.get("expansion", {}).get("stop_pct",   40),
            "expected_reach_50_pct": pop_stats.get("reach_pct", {}).get(50, 0),
            "expected_expansion_rate": pop_stats.get("expansion_rate", 0),
            "runner_score":     pop_stats.get("runner_score", 0),
            "entry_conditions": [{"feature": f["feature_label"], "bucket": f["bucket"],
                                  "expansion_rate": f["expansion_rate"], "lift_pp": f["lift_pp"]}
                                 for f in exp_conditions],
            "avoid_conditions": [{"feature": f["feature_label"], "bucket": f["bucket"],
                                  "expansion_rate": f["expansion_rate"], "lift_pp": f["lift_pp"]}
                                 for f in exp_avoids],
            "best_sessions":    [{"session": s, "expansion_rate": r} for s, r in best_expansion_sessions],
            "session_stats":    sess_stats,
            "mae_guidance":     f"Average heat {pop_stats.get('mae_mean_pct', 0):.1f}% of candle. "
                                f"Stop beyond signal extreme to give room.",
        },
        "scalp_reversal": {
            "description":      "Quick fade targeting 25% of candle. Use when context is mixed or expansion unlikely.",
            "target_pct":       strategy_cfg.get("scalp", {}).get("target_pct", 25),
            "stop_pct":         strategy_cfg.get("scalp", {}).get("stop_pct",   75),
            "expected_reach_25_pct": pop_stats.get("reach_pct", {}).get(25, 0),
            "entry_conditions": [{"feature": f["feature_label"], "bucket": f["bucket"],
                                  "expansion_rate": f["expansion_rate"], "lift_pp": f["lift_pp"]}
                                 for f in scalp_conditions],
            "session_stats":    sess_stats,
        },
    }


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def compute_reversal_followthrough_quality(
    events_sample: List[Dict[str, Any]],
    cfg: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Compute the Reversal Follow-Through Quality analysis.

    Parameters
    ----------
    events_sample : list of event record dicts (from sweep._event_to_record).
                    Must include adv_ticks, fav_ticks, size_ticks.
                    Optionally includes context columns and early-path features.
    cfg           : RFQ config (merged with DEFAULT_RFQ_CONFIG).

    Returns
    -------
    Dict containing:
      enabled, n_events, n_valid
      dataset         — per-event enriched rows (capped at max_dataset_rows)
      population      — overall aggregate stats
      feature_importance — ranked by |lift| from expansion rate
      conditional_probabilities — P(target_b | reached target_a)
      session_breakdown — per-session expansion rate and metrics
      strategy_profiles — scalp and expansion strategy dicts
      outcome_distribution — class counts and percentages
    """
    merged = _deep_merge(DEFAULT_RFQ_CONFIG, cfg or {})
    if not merged.get("enabled", False):
        return {"enabled": False}
    if not events_sample:
        return {"enabled": True, "n_events": 0, "message": "no events in sample"}

    desired_win: int        = int(merged.get("forward_window_minutes", 30))
    targets: List[int]      = [int(t) for t in merged.get("target_percents", [25, 50, 75, 100])]
    thresholds: Dict        = merged.get("outcome_thresholds", DEFAULT_RFQ_CONFIG["outcome_thresholds"])
    imp_features: List      = merged.get("importance_features", DEFAULT_RFQ_CONFIG["importance_features"])
    min_n_feat: int         = int(merged.get("min_n_feature", 20))
    top_n_imp: int          = int(merged.get("top_n_importance", 15))
    cond_pairs: List        = [tuple(p) for p in merged.get("conditional_pairs", [(50, 75), (50, 100), (25, 50)])]
    max_rows: int           = int(merged.get("max_dataset_rows", 500))

    # -------------------------------------------------------------------------
    # Filter to desired window
    # -------------------------------------------------------------------------
    filtered = [ev for ev in events_sample if ev.get("window_minutes") == desired_win]
    if len(filtered) < int(merged.get("min_n_overall", 30)):
        all_wins = sorted({ev.get("window_minutes") for ev in events_sample
                          if ev.get("window_minutes")}, reverse=True)
        for w in all_wins:
            cands = [ev for ev in events_sample if ev.get("window_minutes") == w]
            if len(cands) >= int(merged.get("min_n_overall", 30)):
                filtered = cands
                desired_win = w
                break
        else:
            filtered = events_sample

    # -------------------------------------------------------------------------
    # Per-event enrichment
    # -------------------------------------------------------------------------
    dataset: List[Dict] = []
    for ev in filtered:
        rfq = _enrich_event(ev, targets, thresholds)
        if rfq is not None:
            dataset.append(rfq)

    if not dataset:
        return {"enabled": True, "n_events": len(filtered),
                "message": "no valid events (missing adv/fav/size_ticks)"}

    # -------------------------------------------------------------------------
    # Population aggregate stats
    # -------------------------------------------------------------------------
    pop_stats = _agg_stats(dataset, targets) or {}
    pop_expansion_rate = pop_stats.get("expansion_rate", 0.0)

    # -------------------------------------------------------------------------
    # Outcome distribution
    # -------------------------------------------------------------------------
    outcome_dist: Dict[str, int] = {}
    for r in dataset:
        oc = r.get("outcome_class", "unknown")
        outcome_dist[oc] = outcome_dist.get(oc, 0) + 1
    n_total = len(dataset)
    outcome_dist_pct = {k: round(v / n_total * 100, 1) for k, v in outcome_dist.items()}

    # -------------------------------------------------------------------------
    # Feature importance
    # -------------------------------------------------------------------------
    fi = _compute_feature_importance(dataset, imp_features, pop_expansion_rate, min_n_feat)
    fi_top = fi[:top_n_imp]

    # -------------------------------------------------------------------------
    # Conditional probabilities
    # -------------------------------------------------------------------------
    cond_probs = _compute_conditional_probabilities(dataset, cond_pairs)

    # -------------------------------------------------------------------------
    # Session breakdown
    # -------------------------------------------------------------------------
    sess_breakdown: Dict[str, Dict] = {}
    sess_groups: Dict[str, List] = {}
    for r in dataset:
        s = r.get("session_bucket", "unknown")
        sess_groups.setdefault(s, []).append(r)
    for s, rows in sorted(sess_groups.items()):
        if len(rows) < min_n_feat:
            continue
        agg = _agg_stats(rows, targets)
        if agg:
            sess_breakdown[s] = agg

    # -------------------------------------------------------------------------
    # Early-path statistics (momentum at 1 and 3 bars)
    # -------------------------------------------------------------------------
    early1_vals = [r["early_momentum_1bar_pct"] for r in dataset if r.get("early_momentum_1bar_pct") is not None]
    early3_vals = [r["early_momentum_3bar_pct"] for r in dataset if r.get("early_momentum_3bar_pct") is not None]
    midpoint_reclaim = sum(1 for r in dataset if r.get("did_price_reclaim_midpoint") is True)

    # Early path by outcome class
    early_by_class: Dict[str, Dict] = {}
    for oc in _OUTCOME_ORDER:
        oc_rows = [r for r in dataset if r.get("outcome_class") == oc]
        if len(oc_rows) < 5:
            continue
        e1 = [r["early_momentum_1bar_pct"] for r in oc_rows if r.get("early_momentum_1bar_pct") is not None]
        e3 = [r["early_momentum_3bar_pct"] for r in oc_rows if r.get("early_momentum_3bar_pct") is not None]
        mp = sum(1 for r in oc_rows if r.get("did_price_reclaim_midpoint") is True)
        be = sum(1 for r in oc_rows if r.get("did_break_extreme_again") is True)
        early_by_class[oc] = {
            "n":                    len(oc_rows),
            "early_momentum_1bar":  round(_mean(e1) or 0, 1),
            "early_momentum_3bar":  round(_mean(e3) or 0, 1),
            "midpoint_reclaim_pct": round(mp / len(oc_rows) * 100, 1),
            "break_extreme_pct":    round(be / len(oc_rows) * 100, 1),
        }

    # -------------------------------------------------------------------------
    # Strategy profiles
    # -------------------------------------------------------------------------
    strategy_profiles = _compute_strategy_profiles(dataset, fi, pop_stats, targets, merged)

    # -------------------------------------------------------------------------
    # Top context combinations for expansion
    # -------------------------------------------------------------------------
    top_expansion_contexts = [f for f in fi if f["lift_pp"] >= 5.0][:10]

    return {
        "enabled":              True,
        "n_events":             len(filtered),
        "n_valid":              n_total,
        "forward_window_min":   desired_win,
        "targets_pct":          targets,
        "population":           pop_stats,
        "outcome_distribution": outcome_dist_pct,
        "outcome_counts":       outcome_dist,
        "feature_importance":   fi_top,
        "conditional_probabilities": cond_probs,
        "session_breakdown":    sess_breakdown,
        "early_path_stats":     {
            "early_momentum_1bar_mean_pct": round(_mean(early1_vals) or 0, 1),
            "early_momentum_3bar_mean_pct": round(_mean(early3_vals) or 0, 1),
            "midpoint_reclaim_pct":         round(midpoint_reclaim / n_total * 100, 1),
            "by_outcome_class":             early_by_class,
        },
        "top_expansion_contexts":  top_expansion_contexts,
        "strategy_profiles":       strategy_profiles,
        "dataset":                 dataset[:max_rows],
        "config":                  merged,
    }


def _deep_merge(base: Dict, override: Dict) -> Dict:
    result = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(result.get(k), dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result
