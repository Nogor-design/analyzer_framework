from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from .models import AgentStats, DailyPrediction, PredictionOutcome, SimilarContext

DENVER_TZ = "America/Denver"
MIN_SAMPLES_FOR_STATS = 5
MIN_SIMILARITY_THRESHOLD = 0.60
DRIFT_SHORT_WINDOW_DAYS = 10
DRIFT_STDEV_THRESHOLD = 1.0


# ---------------------------------------------------------------------------
# Feature vector extraction and normalization
# ---------------------------------------------------------------------------

_FEATURE_NAMES = [
    "tf240m_trend_slope",   # normalized to [0,1] via clip [-5,5]
    "tf60m_trend_slope",    # same
    "tf15m_compression_ratio",  # clip [0,3] / 3
    "tf60m_trend_strength",     # already small positive float; clip [0,2] / 2
    "cross_tf_agreement",       # already [0,1]
    "event_risk_score",         # [0,1]
    "tf240m_trend_strength",    # clip [0,2] / 2
    "tf15m_vwap_dist_atr",      # clip [-3,3], map to [0,1]
]


def extract_feature_vector(
    multitf_features: Dict[str, Any],
    event_risk_score: float = 0.0,
) -> List[float]:
    """
    Build the 8-component normalized feature vector used for cosine similarity.

    multitf_features: the dict returned by build_multitf_features()["feature_values"]
    event_risk_score: from compute_event_proximity_features()["event_risk_score"]
    """
    fv = multitf_features.get("feature_values", multitf_features)

    def _get(key: str, default: float = 0.0) -> float:
        v = fv.get(key)
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return default
        return float(v)

    slope240 = _clip(_get("tf240m_trend_slope"), -5.0, 5.0)
    slope60 = _clip(_get("tf60m_trend_slope"), -5.0, 5.0)
    compress15 = _clip(_get("tf15m_compression_ratio", 1.0), 0.0, 3.0)
    strength60 = _clip(_get("tf60m_trend_strength"), 0.0, 2.0)
    agreement = _clip(_get("cross_tf_agreement"), 0.0, 1.0)
    risk = _clip(event_risk_score, 0.0, 1.0)
    strength240 = _clip(_get("tf240m_trend_strength"), 0.0, 2.0)
    vwap_dist = _clip(_get("tf15m_vwap_dist_atr"), -3.0, 3.0)

    vec = [
        (slope240 + 5.0) / 10.0,          # [-5,5] -> [0,1]
        (slope60 + 5.0) / 10.0,
        compress15 / 3.0,                  # [0,3] -> [0,1]
        strength60 / 2.0,                  # [0,2] -> [0,1]
        agreement,                         # already [0,1]
        risk,
        strength240 / 2.0,
        (vwap_dist + 3.0) / 6.0,          # [-3,3] -> [0,1]
    ]
    return vec


def _clip(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _l2_normalize(vec: List[float]) -> List[float]:
    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0.0:
        return [0.0] * len(vec)
    return [v / norm for v in vec]


def _cosine_similarity(a: List[float], b: List[float]) -> float:
    if len(a) != len(b):
        return 0.0
    na = _l2_normalize(a)
    nb = _l2_normalize(b)
    return sum(x * y for x, y in zip(na, nb))


# ---------------------------------------------------------------------------
# Similar context search
# ---------------------------------------------------------------------------

def find_similar_contexts(
    current_vec: List[float],
    history: List[Tuple[DailyPrediction, PredictionOutcome]],
    n: int = 5,
    min_similarity: float = MIN_SIMILARITY_THRESHOLD,
) -> List[SimilarContext]:
    """
    Find the N most similar historical (prediction, outcome) pairs using
    cosine similarity on the 8-component feature vector.

    Only records with feature_vector stored in context_snapshot are eligible.
    Only returns records with similarity >= min_similarity.
    """
    scored = []
    for pred, outcome in history:
        hist_vec = (pred.context_snapshot or {}).get("feature_vector")
        if not hist_vec or len(hist_vec) != len(current_vec):
            continue
        sim = _cosine_similarity(current_vec, hist_vec)
        if sim >= min_similarity:
            scored.append((sim, pred, outcome))

    scored.sort(key=lambda t: -t[0])

    return [
        SimilarContext(
            target_date=pred.target_date,
            similarity_score=sim,
            prediction=pred,
            outcome=outcome,
            feature_vector=hist_vec if hist_vec else [],
        )
        for sim, pred, outcome in scored[:n]
        if (hist_vec := (pred.context_snapshot or {}).get("feature_vector")) is not None
    ]


# ---------------------------------------------------------------------------
# Agent stats computation
# ---------------------------------------------------------------------------

def compute_agent_stats(
    history: List[Tuple[DailyPrediction, PredictionOutcome]],
    agent_id: str,
    window_days: int = 60,
) -> AgentStats:
    """
    Compute rolling accuracy and calibration statistics for one agent.
    Returns a zeroed AgentStats if fewer than MIN_SAMPLES_FOR_STATS records.
    """
    stats = AgentStats(
        agent_id=agent_id,
        window_days=window_days,
        computed_at=pd.Timestamp.now(tz=DENVER_TZ).isoformat(),
    )

    if len(history) < MIN_SAMPLES_FOR_STATS:
        return stats

    stats.sample_count = len(history)

    composite_scores = [o.composite_score for _, o in history]
    stats.mean_composite_score = _mean(composite_scores)

    # Trend accuracy: direction match rate
    trend_matches = [
        1.0 if p.trend_direction == o.actual_direction else 0.0
        for p, o in history
    ]
    stats.trend_accuracy = _mean(trend_matches)

    # Chop accuracy: trending/chop match rate
    chop_matches = [
        1.0 if p.is_trending == o.actual_is_trending else 0.0
        for p, o in history
    ]
    stats.chop_accuracy = _mean(chop_matches)

    # Breakout accuracy: predicted ≥ 0.5 matches occurred
    breakout_matches = [
        1.0 if (p.breakout_probability >= 0.5) == o.breakout_occurred else 0.0
        for p, o in history
    ]
    stats.breakout_accuracy = _mean(breakout_matches)

    stats.mean_levels_score = _mean([o.levels_score for _, o in history])

    # ECE and calibration buckets
    stats.trend_calibration_buckets, stats.ece = _compute_calibration(history)

    # Regime-conditional accuracy
    regime_groups: Dict[str, List[float]] = {}
    for _, o in history:
        regime_groups.setdefault(o.regime_label, []).append(o.composite_score)
    stats.regime_accuracy = {r: _mean(scores) for r, scores in regime_groups.items()}

    # Event vs normal day
    event_scores = [o.composite_score for _, o in history if o.had_high_impact_event]
    normal_scores = [o.composite_score for _, o in history if not o.had_high_impact_event]
    stats.event_day_accuracy = _mean(event_scores) if event_scores else 0.0
    stats.normal_day_accuracy = _mean(normal_scores) if normal_scores else 0.0

    # Concept drift: recent 10-day mean vs window mean
    short = composite_scores[-DRIFT_SHORT_WINDOW_DAYS:]
    stats.short_window_score = _mean(short)
    stats.long_window_score = stats.mean_composite_score
    if len(composite_scores) > DRIFT_SHORT_WINDOW_DAYS:
        stdev = _stdev(composite_scores)
        if stdev > 0 and (stats.long_window_score - stats.short_window_score) > DRIFT_STDEV_THRESHOLD * stdev:
            stats.drift_warning = True

    return stats


# ---------------------------------------------------------------------------
# Calibration (ECE)
# ---------------------------------------------------------------------------

def _compute_calibration(
    history: List[Tuple[DailyPrediction, PredictionOutcome]],
    n_bins: int = 10,
) -> Tuple[List[Dict], float]:
    """
    Compute per-bin calibration statistics and Expected Calibration Error (ECE).

    Bins trend_confidence into n_bins equal-width intervals [0,1].
    Returns (bucket_list, ece).
    """
    bins: Dict[int, List[Tuple[float, bool]]] = {i: [] for i in range(n_bins)}

    for pred, outcome in history:
        conf = max(0.0, min(0.9999, pred.trend_confidence))
        bucket = int(conf * n_bins)
        correct = pred.trend_direction == outcome.actual_direction
        bins[bucket].append((conf, correct))

    total = sum(len(v) for v in bins.values())
    buckets = []
    ece = 0.0

    for i in range(n_bins):
        items = bins[i]
        if not items:
            continue
        mean_conf = _mean([x[0] for x in items])
        frac_correct = _mean([1.0 if x[1] else 0.0 for x in items])
        count = len(items)
        bin_center = (i + 0.5) / n_bins
        buckets.append({
            "bin_center": round(bin_center, 3),
            "mean_confidence": round(mean_conf, 4),
            "fraction_correct": round(frac_correct, 4),
            "count": count,
        })
        if count >= 2:
            ece += (count / total) * abs(mean_conf - frac_correct)

    return buckets, round(ece, 4)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mean(values: List[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def _stdev(values: List[float]) -> float:
    if len(values) < 2:
        return 0.0
    mu = _mean(values)
    variance = sum((v - mu) ** 2 for v in values) / (len(values) - 1)
    return math.sqrt(variance)
