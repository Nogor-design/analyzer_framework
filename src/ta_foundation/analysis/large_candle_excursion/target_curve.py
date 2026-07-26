from __future__ import annotations

"""
Target Curve Analysis
=====================
Replaces single-target win-rate with a target ladder: win_rate computed at
multiple target percentages for each setup combination.

A "setup" is the grouping key (trade_mode, direction, candle_bucket,
tf_minutes, window_minutes).  For each setup we store a list of TargetPoints
(one per target_pct) and derive curve-level metrics such as edge_decay,
monotonicity, plateau_width, and behavior_type.

Behavior classification
-----------------------
  scalp   — edge decays early; WR drops ≥ 20 pp from peak before 50% target
  runner  — WR is stable across wide range; plateau_width ≥ 3 steps
  mixed   — neither extreme
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class TargetPoint:
    target_pct: float       # e.g. 25.0, 50.0, 75.0, 100.0 …
    win_rate: float         # 0.0 – 1.0
    n_events: int           # sample size at this target
    time_split_win_rates: List[float] = field(default_factory=list)
    time_split_stability: Optional[float] = None
    time_split_max_drop_pp: Optional[float] = None
    time_split_available: bool = False


@dataclass
class TargetCurveMetrics:
    setup_key: str                              # human-readable composite key
    points: List[TargetPoint]                  # sorted by target_pct ascending

    # Peak info
    peak_target_pct: float = 0.0
    peak_win_rate: float = 0.0

    # Curve shape metrics
    edge_decay: float = 0.0                    # WR(first) – WR(last)
    monotonicity: float = 0.0                  # fraction of adjacent pairs where WR declines (0–1)
    plateau_width: int = 0                     # number of consecutive steps within tolerance of peak
    stable_target_range: Tuple[float, float] = (0.0, 0.0)   # (lo_pct, hi_pct) plateau band
    fine_target_curve: List[TargetPoint] = field(default_factory=list)
    fine_plateau_width: int = 0
    fine_stable_target_range: Tuple[float, float] = (0.0, 0.0)
    neighbor_target_stability: float = 0.0
    neighbor_target_drops: Dict[str, dict] = field(default_factory=dict)
    target_time_stability: float = 0.5
    focus_time_split_win_rates: List[float] = field(default_factory=list)
    focus_time_split_max_drop_pp: Optional[float] = None
    time_stability_label: str = "unknown"
    target_stability_label: str = "unknown"
    micro_scalp_artifact: bool = False

    # Derived behavior
    behavior_type: str = "unknown"             # "scalp" | "runner" | "mixed"

    # Scoring components
    plateau_score: float = 0.0                 # 0–1; wider plateau → higher score
    edge_decay_penalty: float = 0.0            # 0–1; faster decay → higher penalty


# ---------------------------------------------------------------------------
# Core metric computation
# ---------------------------------------------------------------------------

def compute_target_curve_metrics(
    points: List[TargetPoint],
    setup_key: str = "",
    plateau_tolerance: float = 3.0,            # pp within peak WR = "plateau"
    plateau_min_width: int = 3,                # min consecutive steps for "runner"
    scalp_max_target_pct: float = 50.0,        # peak target% threshold for "scalp"
    scalp_min_edge_decay: float = 0.20,        # minimum edge decay fraction for "scalp"
    edge_decay_full_penalty_pp: float = 50.0,  # pp decay that gives maximum penalty
    fine_sweep_targets: Optional[List[float]] = None,
    focus_target_pct: float = 20.0,
    stable_neighbor_drop_pp: float = 3.0,
    micro_scalp_max_target_pct: float = 25.0,
    micro_scalp_max_plateau_width: int = 1,
    micro_scalp_min_edge_decay_penalty: float = 0.70,
    stable_time_split_stability: float = 0.65,
    fragile_time_split_stability: float = 0.40,
) -> TargetCurveMetrics:
    """
    Derive curve-level metrics from an ordered list of TargetPoints.

    Parameters
    ----------
    points                   : TargetPoints sorted ascending by target_pct
    setup_key                : label for the setup (used in reporting)
    plateau_tolerance        : win_rate within (peak - plateau_tolerance pp) counts
                               as plateau territory
    plateau_min_width        : consecutive steps required to classify as "runner"
    scalp_max_target_pct     : peak target% at or below which "scalp" applies
    scalp_min_edge_decay     : minimum WR decay fraction to classify as "scalp"
    edge_decay_full_penalty_pp : pp decay that saturates the edge_decay_penalty to 1.0
    """
    if not points:
        return TargetCurveMetrics(setup_key=setup_key, points=[])

    pts = sorted(points, key=lambda p: p.target_pct)

    # --- Peak ---
    peak = max(pts, key=lambda p: p.win_rate)
    peak_target_pct = peak.target_pct
    peak_win_rate = peak.win_rate

    # --- Edge decay: first_wr – last_wr ---
    edge_decay = pts[0].win_rate - pts[-1].win_rate

    # --- Monotonicity: fraction of descending adjacent pairs ---
    if len(pts) > 1:
        descending = sum(
            1 for a, b in zip(pts, pts[1:]) if b.win_rate <= a.win_rate
        )
        monotonicity = descending / (len(pts) - 1)
    else:
        monotonicity = 1.0

    # --- Plateau: consecutive steps near peak ---
    tol = plateau_tolerance / 100.0          # convert pp → fraction
    threshold = peak_win_rate - tol

    peak_idx = pts.index(peak)
    best_run = _connected_plateau_run(pts, peak_idx, threshold)

    plateau_width = len(best_run)
    if best_run:
        stable_target_range = (best_run[0].target_pct, best_run[-1].target_pct)
    else:
        stable_target_range = (peak_target_pct, peak_target_pct)

    # --- Behavior type ---
    # scalp: peak at low target AND WR drops sharply before scalp_max_target_pct
    # runner: plateau spans at least plateau_min_width steps
    # mixed: everything else
    if plateau_width >= plateau_min_width:
        behavior_type = "runner"
    elif peak_target_pct <= scalp_max_target_pct and edge_decay >= scalp_min_edge_decay:
        behavior_type = "scalp"
    else:
        behavior_type = "mixed"

    # --- Scoring components ---
    max_possible_plateau = len(pts)
    plateau_score = plateau_width / max_possible_plateau if max_possible_plateau > 0 else 0.0

    # edge_decay_penalty: normalised to [0,1]; full penalty at edge_decay_full_penalty_pp decay
    full_penalty_frac = max(edge_decay_full_penalty_pp / 100.0, 0.01)
    edge_decay_penalty = min(max(edge_decay, 0.0), full_penalty_frac) / full_penalty_frac

    fine_targets = {round(float(t), 8) for t in (fine_sweep_targets or [])}
    fine_pts = [p for p in pts if round(float(p.target_pct), 8) in fine_targets] if fine_targets else []
    fine_plateau_width = 0
    fine_stable_target_range = (peak_target_pct, peak_target_pct)
    neighbor_drops: Dict[str, dict] = {}
    neighbor_stability = 0.5
    target_time_stability = 0.5
    focus_time_split_win_rates: List[float] = []
    focus_time_split_max_drop_pp: Optional[float] = None
    time_stability_label = "unknown"
    target_label = "stable_plateau" if plateau_width >= plateau_min_width else ("narrow_fragile_optimum" if plateau_width <= 1 else "moderate_plateau")

    if fine_pts:
        fine_peak = max(fine_pts, key=lambda p: p.win_rate)
        fine_threshold = fine_peak.win_rate - tol
        fine_peak_idx = fine_pts.index(fine_peak)
        fine_run = _connected_plateau_run(fine_pts, fine_peak_idx, fine_threshold)
        fine_plateau_width = len(fine_run)
        if fine_run:
            fine_stable_target_range = (fine_run[0].target_pct, fine_run[-1].target_pct)

        center_idx = min(
            range(len(fine_pts)),
            key=lambda i: (abs(fine_pts[i].target_pct - focus_target_pct), -fine_pts[i].win_rate),
        )
        center = fine_pts[center_idx]
        target_time_stability = center.time_split_stability if center.time_split_stability is not None else 0.5
        focus_time_split_win_rates = list(center.time_split_win_rates or [])
        focus_time_split_max_drop_pp = center.time_split_max_drop_pp
        neighbor_refs = []
        if center_idx > 0:
            neighbor_refs.append(("left", fine_pts[center_idx - 1]))
        if center_idx + 1 < len(fine_pts):
            neighbor_refs.append(("right", fine_pts[center_idx + 1]))

        drops = []
        for side, p in neighbor_refs:
            drop_pp = max(0.0, (center.win_rate - p.win_rate) * 100.0)
            drops.append(drop_pp)
            neighbor_drops[side] = {
                "target_pct": p.target_pct,
                "win_rate": round(p.win_rate, 4),
                "drop_from_focus_pp": round(drop_pp, 3),
            }

        if drops:
            avg_drop_pp = sum(drops) / len(drops)
            neighbor_stability = 1.0 - min(avg_drop_pp / max(stable_neighbor_drop_pp * 2.0, 0.01), 1.0)

        if fine_plateau_width >= 3 and neighbor_stability >= 0.70:
            target_label = "stable_plateau"
        elif fine_plateau_width <= 1 or neighbor_stability < 0.45:
            target_label = "narrow_fragile_optimum"
        else:
            target_label = "moderate_plateau"
    else:
        target_time_stability = peak.time_split_stability if peak.time_split_stability is not None else 0.5
        focus_time_split_win_rates = list(peak.time_split_win_rates or [])
        focus_time_split_max_drop_pp = peak.time_split_max_drop_pp
        if plateau_width >= plateau_min_width:
            neighbor_stability = 1.0
        elif plateau_width <= 1:
            neighbor_stability = 0.35
        else:
            neighbor_stability = 0.55

    if target_time_stability >= stable_time_split_stability:
        time_stability_label = "time_stable"
    elif target_time_stability < fragile_time_split_stability:
        time_stability_label = "time_fragile"
    else:
        time_stability_label = "time_moderate"

    active_plateau_width = fine_plateau_width if fine_pts else plateau_width
    micro_scalp_artifact = (
        peak_target_pct <= micro_scalp_max_target_pct
        and active_plateau_width <= micro_scalp_max_plateau_width
        and edge_decay_penalty >= micro_scalp_min_edge_decay_penalty
    )

    return TargetCurveMetrics(
        setup_key=setup_key,
        points=pts,
        peak_target_pct=peak_target_pct,
        peak_win_rate=peak_win_rate,
        edge_decay=edge_decay,
        monotonicity=monotonicity,
        plateau_width=plateau_width,
        stable_target_range=stable_target_range,
        fine_target_curve=fine_pts,
        fine_plateau_width=fine_plateau_width,
        fine_stable_target_range=fine_stable_target_range,
        neighbor_target_stability=neighbor_stability,
        neighbor_target_drops=neighbor_drops,
        target_time_stability=target_time_stability,
        focus_time_split_win_rates=focus_time_split_win_rates,
        focus_time_split_max_drop_pp=focus_time_split_max_drop_pp,
        time_stability_label=time_stability_label,
        target_stability_label=target_label,
        micro_scalp_artifact=micro_scalp_artifact,
        behavior_type=behavior_type,
        plateau_score=plateau_score,
        edge_decay_penalty=edge_decay_penalty,
    )


# ---------------------------------------------------------------------------
# Build curves from trade_combo_results
# ---------------------------------------------------------------------------

def build_target_curves(
    trade_combo_results: List[dict],
    plateau_tolerance: float = 3.0,
    config: Optional[Dict] = None,
) -> Dict[str, TargetCurveMetrics]:
    """
    Group trade_combo_results by setup key and build one TargetCurveMetrics
    per setup.

    Each dict in trade_combo_results is expected to have:
      trade_mode, direction, candle_bucket, tf_minutes, window_minutes,
      target_pct, n_wins, n_events

    Returns a dict keyed by setup_key.
    """
    cfg = config or {}
    plateau_tolerance  = float(cfg.get("plateau_tolerance",           plateau_tolerance))
    plateau_min_width  = int(cfg.get("plateau_min_width",             3))
    scalp_max_tgt      = float(cfg.get("scalp_max_target_pct",        50.0))
    scalp_min_decay    = float(cfg.get("scalp_min_edge_decay",        0.20))
    decay_full_pp      = float(cfg.get("edge_decay_full_penalty_pp",  50.0))
    fine_cfg           = cfg.get("fine_sweep", {}) or {}
    fine_targets       = (fine_cfg.get("target_percents", fine_cfg.get("percents", [])) or []) if fine_cfg.get("enabled", False) else []
    focus_target_pct   = float(fine_cfg.get("focus_target_pct", 20.0))
    stable_drop_pp     = float(fine_cfg.get("stable_neighbor_drop_pp", plateau_tolerance))
    micro_max_tgt      = float(fine_cfg.get("micro_scalp_max_target_pct", 25.0))
    micro_max_width    = int(fine_cfg.get("micro_scalp_max_plateau_width", 1))
    micro_min_decay    = float(fine_cfg.get("micro_scalp_min_edge_decay_penalty", 0.70))
    time_cfg           = cfg.get("time_split_validation", {}) or {}
    stable_time        = float(time_cfg.get("stable_time_split_stability", 0.65))
    fragile_time       = float(time_cfg.get("fragile_time_split_stability", 0.40))
    time_drop_pp       = float(time_cfg.get("stable_drop_pp", 8.0))

    # Accumulate (n_wins, n_events) per (setup_key, target_pct)
    agg: Dict[Tuple[str, float], Tuple[int, int]] = {}
    split_agg: Dict[Tuple[str, float], Dict[int, List[int]]] = {}
    setup_keys_ordered: Dict[str, None] = {}   # ordered set

    for row in (trade_combo_results or []):
        key = _make_setup_key(row)
        tpct = float(row.get("target_pct", 0))
        n_wins = int(row.get("n_wins", 0))
        n_events = int(row.get("n_events", 0))

        cell_key = (key, tpct)
        prev_wins, prev_n = agg.get(cell_key, (0, 0))
        agg[cell_key] = (prev_wins + n_wins, prev_n + n_events)
        for split in row.get("time_split_results") or []:
            idx = int(split.get("split") or 0)
            if idx <= 0:
                continue
            bucket = split_agg.setdefault(cell_key, {}).setdefault(idx, [0, 0])
            bucket[0] += int(split.get("n_wins") or 0)
            bucket[1] += int(split.get("n_events") or 0)
        setup_keys_ordered[key] = None

    curves: Dict[str, TargetCurveMetrics] = {}
    for setup_key in setup_keys_ordered:
        # Collect all target_pcts for this setup
        target_pcts = sorted(
            tpct for (sk, tpct) in agg if sk == setup_key
        )
        points: List[TargetPoint] = []
        for tpct in target_pcts:
            wins, n = agg[(setup_key, tpct)]
            wr = wins / n if n > 0 else 0.0
            split_metrics = _combined_time_split_metrics(split_agg.get((setup_key, tpct), {}), time_drop_pp)
            points.append(TargetPoint(
                target_pct=tpct,
                win_rate=wr,
                n_events=n,
                time_split_win_rates=split_metrics["win_rates"],
                time_split_stability=split_metrics["stability"],
                time_split_max_drop_pp=split_metrics["max_drop_pp"],
                time_split_available=split_metrics["available"],
            ))

        curves[setup_key] = compute_target_curve_metrics(
            points,
            setup_key=setup_key,
            plateau_tolerance=plateau_tolerance,
            plateau_min_width=plateau_min_width,
            scalp_max_target_pct=scalp_max_tgt,
            scalp_min_edge_decay=scalp_min_decay,
            edge_decay_full_penalty_pp=decay_full_pp,
            fine_sweep_targets=[float(t) for t in fine_targets],
            focus_target_pct=focus_target_pct,
            stable_neighbor_drop_pp=stable_drop_pp,
            micro_scalp_max_target_pct=micro_max_tgt,
            micro_scalp_max_plateau_width=micro_max_width,
            micro_scalp_min_edge_decay_penalty=micro_min_decay,
            stable_time_split_stability=stable_time,
            fragile_time_split_stability=fragile_time,
        )

    return curves


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_setup_key(row: dict) -> str:
    tm = row.get("trade_mode", "?")
    dr = row.get("direction", "?")
    cb = row.get("candle_bucket", "?")
    tf = row.get("tf_minutes", "?")
    wm = row.get("window_minutes", "?")
    return f"{tm}|{dr}|{cb}|tf{tf}m|w{wm}m"


def _connected_plateau_run(points: List[TargetPoint], center_idx: int, threshold: float) -> List[TargetPoint]:
    if not points:
        return []
    lo = hi = center_idx
    while lo > 0 and points[lo - 1].win_rate >= threshold:
        lo -= 1
    while hi + 1 < len(points) and points[hi + 1].win_rate >= threshold:
        hi += 1
    return points[lo : hi + 1]


def _combined_time_split_metrics(split_counts: Dict[int, List[int]], stable_drop_pp: float = 8.0) -> Dict[str, Any]:
    if not split_counts:
        return {"available": False, "win_rates": [], "stability": None, "max_drop_pp": None}

    wrs: List[float] = []
    for idx in sorted(split_counts):
        wins, n = split_counts[idx]
        if n <= 0:
            continue
        wrs.append(round((wins / n) * 100.0, 3))

    if len(wrs) < 2:
        return {"available": False, "win_rates": wrs, "stability": None, "max_drop_pp": None}

    max_drop_pp = max(wrs) - min(wrs)
    stability = 1.0 - min(max_drop_pp / max(stable_drop_pp * 2.0, 0.01), 1.0)
    return {
        "available": True,
        "win_rates": wrs,
        "stability": round(stability, 4),
        "max_drop_pp": round(max_drop_pp, 3),
    }


def top_setups_by_peak_wr(
    curves: Dict[str, TargetCurveMetrics],
    min_n: int = 30,
    top_n: int = 20,
) -> List[TargetCurveMetrics]:
    """
    Return up to top_n TargetCurveMetrics sorted by peak_win_rate descending,
    filtered to setups where the peak point has at least min_n events.
    """
    filtered = [
        c for c in curves.values()
        if c.points and max(p.n_events for p in c.points) >= min_n
    ]
    return sorted(filtered, key=lambda c: c.peak_win_rate, reverse=True)[:top_n]


def curve_to_dict(c: TargetCurveMetrics) -> dict:
    """
    Serialize a TargetCurveMetrics to a JSON-safe dict for storage in
    pkg.metadata["derived"].
    """
    return {
        "setup_key": c.setup_key,
        "points": [
            {
                "target_pct": p.target_pct,
                "win_rate": round(p.win_rate, 4),
                "n_events": p.n_events,
                "time_split_win_rates": p.time_split_win_rates,
                "time_split_stability": p.time_split_stability,
                "time_split_max_drop_pp": p.time_split_max_drop_pp,
                "time_split_available": p.time_split_available,
            }
            for p in c.points
        ],
        "peak_target_pct": c.peak_target_pct,
        "peak_win_rate": round(c.peak_win_rate, 4),
        "edge_decay": round(c.edge_decay, 4),
        "monotonicity": round(c.monotonicity, 4),
        "plateau_width": c.plateau_width,
        "stable_target_range": list(c.stable_target_range),
        "fine_target_curve": [
            {
                "target_pct": p.target_pct,
                "win_rate": round(p.win_rate, 4),
                "n_events": p.n_events,
                "time_split_win_rates": p.time_split_win_rates,
                "time_split_stability": p.time_split_stability,
                "time_split_max_drop_pp": p.time_split_max_drop_pp,
                "time_split_available": p.time_split_available,
            }
            for p in c.fine_target_curve
        ],
        "fine_plateau_width": c.fine_plateau_width,
        "fine_stable_target_range": list(c.fine_stable_target_range),
        "neighbor_target_stability": round(c.neighbor_target_stability, 4),
        "neighbor_target_drops": c.neighbor_target_drops,
        "target_time_stability": round(c.target_time_stability, 4),
        "focus_time_split_win_rates": c.focus_time_split_win_rates,
        "focus_time_split_max_drop_pp": c.focus_time_split_max_drop_pp,
        "time_stability_label": c.time_stability_label,
        "target_stability_label": c.target_stability_label,
        "micro_scalp_artifact": c.micro_scalp_artifact,
        "behavior_type": c.behavior_type,
        "plateau_score": round(c.plateau_score, 4),
        "edge_decay_penalty": round(c.edge_decay_penalty, 4),
    }


def curves_to_dict(curves: Dict[str, TargetCurveMetrics]) -> Dict[str, dict]:
    return {k: curve_to_dict(v) for k, v in curves.items()}
