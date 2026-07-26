"""
Per-bucket calibration tracking for the horizon prediction system.

A "bucket" is the cell defined by `(agent_id, timeframe, horizon_candles,
session_label, regime_label)`. Each bucket accumulates `(prediction, outcome)`
pairs as the live system runs. From those pairs we compute, per bucket:

    - top-label ECE (how close confidence is to empirical hit-rate)
    - direction accuracy
    - threshold Brier
    - mean composite score
    - reliability buckets (confidence band → empirical hit rate)

The scorer can read bucket ECE back into `score_horizon_prediction(...,
calibration_error=...)` so confident-but-miscalibrated agents are penalized.

This module is read-only over the store — it does not mutate predictions or
outcomes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .horizon_models import CandleHorizonOutcome, CandleHorizonPrediction

PredOutPair = Tuple[CandleHorizonPrediction, CandleHorizonOutcome]


# ---------------------------------------------------------------------------
# Bucket key
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class HorizonBucketKey:
    agent_id: str
    timeframe: str
    horizon_candles: int
    session_label: str
    regime_label: str

    @classmethod
    def from_pair(cls, pair: PredOutPair) -> "HorizonBucketKey":
        pred, _ = pair
        regime = str((pred.feature_snapshot or {}).get("regime") or "unknown")
        return cls(
            agent_id=pred.agent_id,
            timeframe=pred.timeframe,
            horizon_candles=pred.horizon_candles,
            session_label=pred.session_label,
            regime_label=regime,
        )

    def as_string(self) -> str:
        return (
            f"agent={self.agent_id},tf={self.timeframe},"
            f"horizon={self.horizon_candles},session={self.session_label},"
            f"regime={self.regime_label}"
        )


# ---------------------------------------------------------------------------
# Bucket stats
# ---------------------------------------------------------------------------

@dataclass
class HorizonBucketStats:
    bucket: HorizonBucketKey
    sample_count: int = 0
    sample_count_non_abstain: int = 0

    direction_accuracy: float = 0.0
    mean_composite_score: float = 0.0
    mean_brier_direction: float = 0.0
    mean_brier_thresholds: float = 0.0

    ece: float = 0.0
    reliability_buckets: List[Dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "bucket": self.bucket.as_string(),
            "agent_id": self.bucket.agent_id,
            "timeframe": self.bucket.timeframe,
            "horizon_candles": self.bucket.horizon_candles,
            "session_label": self.bucket.session_label,
            "regime_label": self.bucket.regime_label,
            "sample_count": self.sample_count,
            "sample_count_non_abstain": self.sample_count_non_abstain,
            "direction_accuracy": self.direction_accuracy,
            "mean_composite_score": self.mean_composite_score,
            "mean_brier_direction": self.mean_brier_direction,
            "mean_brier_thresholds": self.mean_brier_thresholds,
            "ece": self.ece,
            "reliability_buckets": list(self.reliability_buckets),
        }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def group_by_bucket(pairs: Iterable[PredOutPair]) -> Dict[HorizonBucketKey, List[PredOutPair]]:
    out: Dict[HorizonBucketKey, List[PredOutPair]] = {}
    for pair in pairs:
        key = HorizonBucketKey.from_pair(pair)
        out.setdefault(key, []).append(pair)
    return out


def compute_horizon_bucket_stats(
    pairs: Iterable[PredOutPair],
    n_bins: int = 10,
) -> HorizonBucketStats:
    """
    Compute per-bucket calibration statistics for a single bucket.

    Caller is responsible for filtering `pairs` to one bucket — use
    `group_by_bucket` for the dispatch. ECE is "top-label ECE": for each
    non-abstain prediction we use max(direction_probs) as the predicted
    confidence and check whether the predicted argmax-class matches actual.
    """
    pairs = list(pairs)
    if not pairs:
        # Caller should ideally not hand us an empty bucket; return a
        # sentinel with zero counts so downstream reports degrade gracefully.
        return HorizonBucketStats(
            bucket=HorizonBucketKey("", "", 0, "", ""),
        )

    bucket = HorizonBucketKey.from_pair(pairs[0])
    stats = HorizonBucketStats(bucket=bucket, sample_count=len(pairs))

    non_abstain = [(p, o) for p, o in pairs if not p.abstain]
    stats.sample_count_non_abstain = len(non_abstain)

    if not non_abstain:
        return stats

    # Direction accuracy: argmax(p_bull, p_bear, p_neu) == actual_direction
    correct = 0
    composites: List[float] = []
    brier_dir: List[float] = []
    brier_th: List[float] = []
    conf_correct_pairs: List[Tuple[float, bool]] = []

    for pred, out in non_abstain:
        argmax_class, argmax_prob = _argmax_direction(pred)
        is_correct = argmax_class == out.actual_direction
        correct += int(is_correct)

        composites.append(out.composite_score)
        brier_dir.append(out.brier_score_direction)
        brier_th.append(out.brier_score_thresholds)
        conf_correct_pairs.append((argmax_prob, is_correct))

    n_nz = len(non_abstain)
    stats.direction_accuracy = correct / n_nz
    stats.mean_composite_score = sum(composites) / n_nz
    stats.mean_brier_direction = sum(brier_dir) / n_nz
    stats.mean_brier_thresholds = sum(brier_th) / n_nz

    stats.reliability_buckets, stats.ece = _top_label_ece(conf_correct_pairs, n_bins)
    return stats


def compute_per_bucket_ece(
    pairs: Iterable[PredOutPair],
    n_bins: int = 10,
) -> Dict[HorizonBucketKey, float]:
    """
    Convenience: return only `{bucket: ece}` across all buckets in the input
    stream. Intended as the lookup table for `score_horizon_prediction`'s
    `calibration_error` parameter.
    """
    grouped = group_by_bucket(pairs)
    return {
        key: compute_horizon_bucket_stats(pairs_in_bucket, n_bins=n_bins).ece
        for key, pairs_in_bucket in grouped.items()
    }


def compute_all_bucket_stats(
    pairs: Iterable[PredOutPair],
    n_bins: int = 10,
    min_samples: int = 0,
) -> List[HorizonBucketStats]:
    """
    Run `compute_horizon_bucket_stats` for every bucket. Buckets with fewer
    than `min_samples` non-abstain pairs are dropped from the result.
    """
    grouped = group_by_bucket(pairs)
    out: List[HorizonBucketStats] = []
    for key, pairs_in_bucket in grouped.items():
        stats = compute_horizon_bucket_stats(pairs_in_bucket, n_bins=n_bins)
        if stats.sample_count_non_abstain < min_samples:
            continue
        out.append(stats)
    out.sort(key=lambda s: (-s.sample_count_non_abstain, s.bucket.as_string()))
    return out


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _argmax_direction(pred: CandleHorizonPrediction) -> Tuple[str, float]:
    """Return (predicted_class, predicted_prob_of_that_class)."""
    candidates = [
        ("bullish", pred.bullish_probability),
        ("bearish", pred.bearish_probability),
        ("neutral", pred.neutral_probability),
    ]
    candidates.sort(key=lambda t: -t[1])
    return candidates[0]


def _top_label_ece(
    conf_correct: List[Tuple[float, bool]],
    n_bins: int,
) -> Tuple[List[Dict[str, Any]], float]:
    """
    Standard top-label ECE computation:

        ECE = sum_bin (count_bin / total) * |mean_conf_bin - acc_bin|

    Bins are equal-width on [0, 1]. Bins with zero samples are skipped.
    """
    if not conf_correct:
        return [], 0.0

    bins: Dict[int, List[Tuple[float, bool]]] = {i: [] for i in range(n_bins)}
    for conf, correct in conf_correct:
        c = max(0.0, min(0.9999, float(conf)))
        idx = int(c * n_bins)
        bins[idx].append((conf, correct))

    total = len(conf_correct)
    buckets: List[Dict[str, Any]] = []
    ece = 0.0
    for i in range(n_bins):
        items = bins[i]
        if not items:
            continue
        confs = [x[0] for x in items]
        corrects = [1.0 if x[1] else 0.0 for x in items]
        mean_conf = sum(confs) / len(items)
        acc = sum(corrects) / len(items)
        bin_center = (i + 0.5) / n_bins
        buckets.append({
            "bin_center": round(bin_center, 4),
            "mean_confidence": round(mean_conf, 4),
            "fraction_correct": round(acc, 4),
            "count": len(items),
        })
        ece += (len(items) / total) * abs(mean_conf - acc)

    return buckets, round(ece, 4)


def lookup_calibration_error(
    table: Dict[HorizonBucketKey, float],
    pred: CandleHorizonPrediction,
    fallback: float = 0.0,
) -> float:
    """
    Helper: look up the calibration error for the bucket of `pred` in a
    pre-computed table. Returns `fallback` if the bucket has no entry,
    which happens when the agent has never produced a scored prediction
    in that bucket before.
    """
    regime = str((pred.feature_snapshot or {}).get("regime") or "unknown")
    key = HorizonBucketKey(
        agent_id=pred.agent_id,
        timeframe=pred.timeframe,
        horizon_candles=pred.horizon_candles,
        session_label=pred.session_label,
        regime_label=regime,
    )
    return table.get(key, fallback)
