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
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class TargetPoint:
    target_pct: float       # e.g. 25.0, 50.0, 75.0, 100.0 …
    win_rate: float         # 0.0 – 1.0
    n_events: int           # sample size at this target


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

    # Find the widest consecutive run above threshold
    best_run: List[TargetPoint] = []
    current_run: List[TargetPoint] = []
    for p in pts:
        if p.win_rate >= threshold:
            current_run.append(p)
            if len(current_run) > len(best_run):
                best_run = list(current_run)
        else:
            current_run = []

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

    return TargetCurveMetrics(
        setup_key=setup_key,
        points=pts,
        peak_target_pct=peak_target_pct,
        peak_win_rate=peak_win_rate,
        edge_decay=edge_decay,
        monotonicity=monotonicity,
        plateau_width=plateau_width,
        stable_target_range=stable_target_range,
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

    # Accumulate (n_wins, n_events) per (setup_key, target_pct)
    agg: Dict[Tuple[str, float], Tuple[int, int]] = {}
    setup_keys_ordered: Dict[str, None] = {}   # ordered set

    for row in (trade_combo_results or []):
        key = _make_setup_key(row)
        tpct = float(row.get("target_pct", 0))
        n_wins = int(row.get("n_wins", 0))
        n_events = int(row.get("n_events", 0))

        cell_key = (key, tpct)
        prev_wins, prev_n = agg.get(cell_key, (0, 0))
        agg[cell_key] = (prev_wins + n_wins, prev_n + n_events)
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
            points.append(TargetPoint(target_pct=tpct, win_rate=wr, n_events=n))

        curves[setup_key] = compute_target_curve_metrics(
            points,
            setup_key=setup_key,
            plateau_tolerance=plateau_tolerance,
            plateau_min_width=plateau_min_width,
            scalp_max_target_pct=scalp_max_tgt,
            scalp_min_edge_decay=scalp_min_decay,
            edge_decay_full_penalty_pp=decay_full_pp,
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
            {"target_pct": p.target_pct, "win_rate": round(p.win_rate, 4), "n_events": p.n_events}
            for p in c.points
        ],
        "peak_target_pct": c.peak_target_pct,
        "peak_win_rate": round(c.peak_win_rate, 4),
        "edge_decay": round(c.edge_decay, 4),
        "monotonicity": round(c.monotonicity, 4),
        "plateau_width": c.plateau_width,
        "stable_target_range": list(c.stable_target_range),
        "behavior_type": c.behavior_type,
        "plateau_score": round(c.plateau_score, 4),
        "edge_decay_penalty": round(c.edge_decay_penalty, 4),
    }


def curves_to_dict(curves: Dict[str, TargetCurveMetrics]) -> Dict[str, dict]:
    return {k: curve_to_dict(v) for k, v in curves.items()}
