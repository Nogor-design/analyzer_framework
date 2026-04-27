"""
Reports for the horizon prediction system.

All reports are pure read-only consumers of `HorizonPredictionStore`. They
return structured dataclasses (so callers can serialize, plot, or feed
them back into a strategy). Plain-text formatters live alongside each
report builder for quick CLI inspection.

Reports:
  1. Agent leaderboard       — composite, n, ECE, drift flag.
  2. Timeframe × horizon     — composite + Brier per cell.
  3. Session × (tf, horizon) — per-agent strength by session.
  4. Best-edge finder        — sorted by realized edge with min-n guard.
  5. Calibration report      — confidence band → empirical hit rate.
  6. Drift report            — recent vs long-window composite delta.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import pandas as pd

from .horizon_calibrator import (
    HorizonBucketKey,
    HorizonBucketStats,
    compute_all_bucket_stats,
    compute_horizon_bucket_stats,
    group_by_bucket,
)
from .horizon_models import CandleHorizonOutcome, CandleHorizonPrediction
from .horizon_store import HorizonPredictionStore

PredOutPair = Tuple[CandleHorizonPrediction, CandleHorizonOutcome]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _argmax_direction_sign(pred: CandleHorizonPrediction) -> int:
    """+1 for bullish argmax, -1 for bearish, 0 for neutral / abstain."""
    if pred.abstain:
        return 0
    if (
        pred.bullish_probability >= pred.bearish_probability
        and pred.bullish_probability >= pred.neutral_probability
    ):
        if pred.bullish_probability > pred.neutral_probability:
            return 1
        return 0
    if pred.bearish_probability >= pred.neutral_probability:
        return -1
    return 0


def _mean(values: Sequence[float]) -> float:
    return float(sum(values) / len(values)) if values else 0.0


def _safe_stdev(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    try:
        return float(statistics.pstdev(values))
    except statistics.StatisticsError:
        return 0.0


def _parse_ts_safe(s: str) -> Optional[pd.Timestamp]:
    if not s:
        return None
    try:
        ts = pd.Timestamp(s)
    except (TypeError, ValueError):
        return None
    if ts.tzinfo is None:
        return None
    return ts


def _load_pairs(store: HorizonPredictionStore) -> List[PredOutPair]:
    return store.get_pairs(require_non_abstain=False)


# ===========================================================================
# 1. Agent leaderboard
# ===========================================================================

@dataclass
class AgentLeaderboardRow:
    agent_id: str
    sample_count: int
    sample_count_non_abstain: int
    abstention_rate: float
    direction_accuracy: float
    mean_composite_score: float
    mean_brier_direction: float
    ece: float
    drift_delta: float           # recent_score - long_score
    drift_flag: bool

    def as_dict(self) -> Dict[str, object]:
        return {
            "agent_id": self.agent_id,
            "sample_count": self.sample_count,
            "sample_count_non_abstain": self.sample_count_non_abstain,
            "abstention_rate": self.abstention_rate,
            "direction_accuracy": self.direction_accuracy,
            "mean_composite_score": self.mean_composite_score,
            "mean_brier_direction": self.mean_brier_direction,
            "ece": self.ece,
            "drift_delta": self.drift_delta,
            "drift_flag": self.drift_flag,
        }


def build_agent_leaderboard(
    pairs: Iterable[PredOutPair],
    *,
    n_bins: int = 10,
    drift_recent_n: int = 50,
    drift_threshold: float = 0.05,
) -> List[AgentLeaderboardRow]:
    """
    Aggregate per-agent stats over all stored (prediction, outcome) pairs.

    `drift_delta` compares the most recent `drift_recent_n` non-abstain
    predictions' mean composite to the long-window mean. If the absolute
    delta exceeds `drift_threshold` AND the long sample is large enough
    (> 2 * drift_recent_n), `drift_flag` is True.
    """
    pairs = list(pairs)
    by_agent: Dict[str, List[PredOutPair]] = {}
    for p, o in pairs:
        by_agent.setdefault(p.agent_id, []).append((p, o))

    rows: List[AgentLeaderboardRow] = []
    for agent_id, agent_pairs in by_agent.items():
        n_total = len(agent_pairs)
        non_abstain = [(p, o) for p, o in agent_pairs if not p.abstain]
        n_non = len(non_abstain)
        abstention_rate = 1.0 - (n_non / n_total) if n_total > 0 else 0.0

        if not non_abstain:
            rows.append(AgentLeaderboardRow(
                agent_id=agent_id,
                sample_count=n_total,
                sample_count_non_abstain=0,
                abstention_rate=abstention_rate,
                direction_accuracy=0.0,
                mean_composite_score=0.0,
                mean_brier_direction=0.0,
                ece=0.0,
                drift_delta=0.0,
                drift_flag=False,
            ))
            continue

        composites = [o.composite_score for _, o in non_abstain]
        briers = [o.brier_score_direction for _, o in non_abstain]

        # Direction accuracy via top-label match
        correct = 0
        for pred, out in non_abstain:
            sign = _argmax_direction_sign(pred)
            if sign == 1 and out.actual_direction == "bullish":
                correct += 1
            elif sign == -1 and out.actual_direction == "bearish":
                correct += 1
            elif sign == 0 and out.actual_direction == "neutral":
                correct += 1
        accuracy = correct / n_non

        # ECE: aggregate top-label across all buckets for this agent
        ece = compute_horizon_bucket_stats(non_abstain, n_bins=n_bins).ece

        # Drift: order non_abstain by asof_timestamp, compare tail vs full
        ordered = sorted(
            non_abstain,
            key=lambda po: _parse_ts_safe(po[0].asof_timestamp) or pd.Timestamp.min.tz_localize("UTC"),
        )
        ordered_composites = [o.composite_score for _, o in ordered]
        long_mean = _mean(ordered_composites)
        recent = ordered_composites[-drift_recent_n:] if len(ordered_composites) >= drift_recent_n else ordered_composites
        recent_mean = _mean(recent) if recent else long_mean
        drift_delta = recent_mean - long_mean
        drift_flag = (
            abs(drift_delta) > drift_threshold
            and len(ordered_composites) > 2 * drift_recent_n
        )

        rows.append(AgentLeaderboardRow(
            agent_id=agent_id,
            sample_count=n_total,
            sample_count_non_abstain=n_non,
            abstention_rate=abstention_rate,
            direction_accuracy=accuracy,
            mean_composite_score=_mean(composites),
            mean_brier_direction=_mean(briers),
            ece=ece,
            drift_delta=drift_delta,
            drift_flag=drift_flag,
        ))

    rows.sort(key=lambda r: -r.mean_composite_score)
    return rows


def format_agent_leaderboard(rows: Sequence[AgentLeaderboardRow]) -> str:
    if not rows:
        return "(no agents)"
    header = (
        f"{'agent_id':<32} {'n':>5} {'n_nz':>5} {'abst%':>6} "
        f"{'dir_acc':>7} {'composite':>9} {'brier':>6} {'ece':>5} "
        f"{'drift':>7} {'flag':>4}"
    )
    lines = [header, "-" * len(header)]
    for r in rows:
        lines.append(
            f"{r.agent_id:<32} {r.sample_count:>5d} {r.sample_count_non_abstain:>5d} "
            f"{r.abstention_rate * 100:>5.1f}% "
            f"{r.direction_accuracy:>7.3f} {r.mean_composite_score:>9.4f} "
            f"{r.mean_brier_direction:>6.3f} {r.ece:>5.3f} "
            f"{r.drift_delta:>+7.3f} {'!' if r.drift_flag else ' ':>4}"
        )
    return "\n".join(lines)


# ===========================================================================
# 2. Timeframe × horizon matrix
# ===========================================================================

@dataclass
class TimeframeHorizonCell:
    agent_id: str
    timeframe: str
    horizon_candles: int
    sample_count: int
    direction_accuracy: float
    mean_composite_score: float
    mean_brier_direction: float

    def as_dict(self) -> Dict[str, object]:
        return {
            "agent_id": self.agent_id,
            "timeframe": self.timeframe,
            "horizon_candles": self.horizon_candles,
            "sample_count": self.sample_count,
            "direction_accuracy": self.direction_accuracy,
            "mean_composite_score": self.mean_composite_score,
            "mean_brier_direction": self.mean_brier_direction,
        }


def build_timeframe_horizon_matrix(
    pairs: Iterable[PredOutPair],
    *,
    min_samples: int = 0,
) -> List[TimeframeHorizonCell]:
    pairs = list(pairs)
    by_cell: Dict[Tuple[str, str, int], List[PredOutPair]] = {}
    for p, o in pairs:
        if p.abstain:
            continue
        key = (p.agent_id, p.timeframe, p.horizon_candles)
        by_cell.setdefault(key, []).append((p, o))

    cells: List[TimeframeHorizonCell] = []
    for (agent_id, tf, horizon), bucket in by_cell.items():
        if len(bucket) < min_samples:
            continue
        composites = [o.composite_score for _, o in bucket]
        briers = [o.brier_score_direction for _, o in bucket]
        correct = 0
        for pred, out in bucket:
            sign = _argmax_direction_sign(pred)
            if sign == 1 and out.actual_direction == "bullish":
                correct += 1
            elif sign == -1 and out.actual_direction == "bearish":
                correct += 1
            elif sign == 0 and out.actual_direction == "neutral":
                correct += 1
        cells.append(TimeframeHorizonCell(
            agent_id=agent_id,
            timeframe=tf,
            horizon_candles=horizon,
            sample_count=len(bucket),
            direction_accuracy=correct / len(bucket),
            mean_composite_score=_mean(composites),
            mean_brier_direction=_mean(briers),
        ))

    cells.sort(key=lambda c: (c.agent_id, c.timeframe, c.horizon_candles))
    return cells


def format_timeframe_horizon_matrix(cells: Sequence[TimeframeHorizonCell]) -> str:
    if not cells:
        return "(no cells)"
    header = (
        f"{'agent_id':<32} {'tf':>4} {'h':>3} {'n':>5} "
        f"{'dir_acc':>7} {'composite':>9} {'brier':>6}"
    )
    lines = [header, "-" * len(header)]
    for c in cells:
        lines.append(
            f"{c.agent_id:<32} {c.timeframe:>4} {c.horizon_candles:>3d} "
            f"{c.sample_count:>5d} {c.direction_accuracy:>7.3f} "
            f"{c.mean_composite_score:>9.4f} {c.mean_brier_direction:>6.3f}"
        )
    return "\n".join(lines)


# ===========================================================================
# 3. Session × (timeframe + horizon) matrix
# ===========================================================================

@dataclass
class SessionMatrixCell:
    agent_id: str
    session_label: str
    timeframe: str
    horizon_candles: int
    sample_count: int
    mean_composite_score: float

    def as_dict(self) -> Dict[str, object]:
        return {
            "agent_id": self.agent_id,
            "session_label": self.session_label,
            "timeframe": self.timeframe,
            "horizon_candles": self.horizon_candles,
            "sample_count": self.sample_count,
            "mean_composite_score": self.mean_composite_score,
        }


def build_session_matrix(
    pairs: Iterable[PredOutPair],
    *,
    min_samples: int = 0,
) -> List[SessionMatrixCell]:
    pairs = list(pairs)
    by_cell: Dict[Tuple[str, str, str, int], List[PredOutPair]] = {}
    for p, o in pairs:
        if p.abstain:
            continue
        key = (p.agent_id, p.session_label, p.timeframe, p.horizon_candles)
        by_cell.setdefault(key, []).append((p, o))

    cells: List[SessionMatrixCell] = []
    for (agent_id, session, tf, horizon), bucket in by_cell.items():
        if len(bucket) < min_samples:
            continue
        composites = [o.composite_score for _, o in bucket]
        cells.append(SessionMatrixCell(
            agent_id=agent_id,
            session_label=session,
            timeframe=tf,
            horizon_candles=horizon,
            sample_count=len(bucket),
            mean_composite_score=_mean(composites),
        ))
    cells.sort(key=lambda c: (-c.mean_composite_score, c.agent_id, c.session_label))
    return cells


def format_session_matrix(cells: Sequence[SessionMatrixCell]) -> str:
    if not cells:
        return "(no cells)"
    header = (
        f"{'agent_id':<32} {'session':<10} {'tf':>4} {'h':>3} "
        f"{'n':>5} {'composite':>9}"
    )
    lines = [header, "-" * len(header)]
    for c in cells:
        lines.append(
            f"{c.agent_id:<32} {c.session_label:<10} {c.timeframe:>4} "
            f"{c.horizon_candles:>3d} {c.sample_count:>5d} "
            f"{c.mean_composite_score:>9.4f}"
        )
    return "\n".join(lines)


# ===========================================================================
# 4. Best-edge finder
# ===========================================================================

@dataclass
class BestEdgeCell:
    agent_id: str
    timeframe: str
    horizon_candles: int
    session_label: str
    regime_label: str
    sample_count: int
    realized_edge_atr: float          # mean of (sign(argmax) * actual_return_atr)
    mean_composite_score: float

    def as_dict(self) -> Dict[str, object]:
        return {
            "agent_id": self.agent_id,
            "timeframe": self.timeframe,
            "horizon_candles": self.horizon_candles,
            "session_label": self.session_label,
            "regime_label": self.regime_label,
            "sample_count": self.sample_count,
            "realized_edge_atr": self.realized_edge_atr,
            "mean_composite_score": self.mean_composite_score,
        }


def build_best_edge_cells(
    pairs: Iterable[PredOutPair],
    *,
    min_samples: int = 20,
) -> List[BestEdgeCell]:
    """
    Group by (agent, timeframe, horizon, session, regime) and compute the
    realized edge in ATR units when following the agent's argmax direction.
    Cells with fewer than `min_samples` non-abstain pairs are dropped.
    """
    pairs = list(pairs)
    by_cell: Dict[Tuple[str, str, int, str, str], List[PredOutPair]] = {}
    for p, o in pairs:
        if p.abstain:
            continue
        regime = str((p.feature_snapshot or {}).get("regime") or "unknown")
        key = (p.agent_id, p.timeframe, p.horizon_candles, p.session_label, regime)
        by_cell.setdefault(key, []).append((p, o))

    cells: List[BestEdgeCell] = []
    for (agent_id, tf, horizon, session, regime), bucket in by_cell.items():
        if len(bucket) < min_samples:
            continue
        edges: List[float] = []
        composites: List[float] = []
        for pred, out in bucket:
            sign = _argmax_direction_sign(pred)
            ret_atr = float(out.actual_return_atr)
            edges.append(sign * ret_atr)
            composites.append(out.composite_score)

        cells.append(BestEdgeCell(
            agent_id=agent_id,
            timeframe=tf,
            horizon_candles=horizon,
            session_label=session,
            regime_label=regime,
            sample_count=len(bucket),
            realized_edge_atr=_mean(edges),
            mean_composite_score=_mean(composites),
        ))

    cells.sort(key=lambda c: -c.realized_edge_atr)
    return cells


def format_best_edge_cells(
    cells: Sequence[BestEdgeCell],
    *,
    top_n: int = 20,
) -> str:
    if not cells:
        return "(no cells meet the min-samples guard)"
    cells = list(cells)[: max(1, top_n)]
    header = (
        f"{'agent_id':<32} {'tf':>4} {'h':>3} {'session':<10} {'regime':<10} "
        f"{'n':>5} {'edge_atr':>9} {'composite':>9}"
    )
    lines = [header, "-" * len(header)]
    for c in cells:
        lines.append(
            f"{c.agent_id:<32} {c.timeframe:>4} {c.horizon_candles:>3d} "
            f"{c.session_label:<10} {c.regime_label:<10} {c.sample_count:>5d} "
            f"{c.realized_edge_atr:>+9.4f} {c.mean_composite_score:>9.4f}"
        )
    return "\n".join(lines)


# ===========================================================================
# 5. Calibration report
# ===========================================================================

@dataclass
class CalibrationReportEntry:
    """
    A single bucket's calibration breakdown — the per-confidence-band rows
    are stored in `reliability_buckets`.
    """
    bucket: HorizonBucketKey
    sample_count: int
    sample_count_non_abstain: int
    direction_accuracy: float
    ece: float
    reliability_buckets: List[Dict[str, object]] = field(default_factory=list)

    def as_dict(self) -> Dict[str, object]:
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
            "ece": self.ece,
            "reliability_buckets": list(self.reliability_buckets),
        }


def build_calibration_report(
    pairs: Iterable[PredOutPair],
    *,
    min_samples: int = 20,
    n_bins: int = 10,
) -> List[CalibrationReportEntry]:
    """
    For every bucket with at least `min_samples` non-abstain pairs, return a
    `CalibrationReportEntry` carrying ECE + reliability bands. Sorted by ECE
    descending so the worst-calibrated buckets surface first.
    """
    stats_list = compute_all_bucket_stats(pairs, n_bins=n_bins, min_samples=min_samples)
    out: List[CalibrationReportEntry] = []
    for s in stats_list:
        out.append(CalibrationReportEntry(
            bucket=s.bucket,
            sample_count=s.sample_count,
            sample_count_non_abstain=s.sample_count_non_abstain,
            direction_accuracy=s.direction_accuracy,
            ece=s.ece,
            reliability_buckets=list(s.reliability_buckets),
        ))
    out.sort(key=lambda e: -e.ece)
    return out


def format_calibration_report(entries: Sequence[CalibrationReportEntry]) -> str:
    if not entries:
        return "(no buckets meet min-samples guard)"
    lines: List[str] = []
    for e in entries:
        lines.append(
            f"{e.bucket.as_string()} | n_nz={e.sample_count_non_abstain} | "
            f"acc={e.direction_accuracy:.3f} | ece={e.ece:.3f}"
        )
        if e.reliability_buckets:
            lines.append(
                "    "
                + "  ".join(
                    f"[{rb['bin_center']:.2f}: conf={rb['mean_confidence']:.2f} "
                    f"acc={rb['fraction_correct']:.2f} n={rb['count']}]"
                    for rb in e.reliability_buckets
                )
            )
    return "\n".join(lines)


# ===========================================================================
# 6. Drift report
# ===========================================================================

@dataclass
class DriftReportRow:
    agent_id: str
    sample_count_non_abstain: int
    long_window_score: float
    recent_window_score: float
    delta: float
    long_window_stdev: float
    z_score: float
    drift_flag: bool

    def as_dict(self) -> Dict[str, object]:
        return {
            "agent_id": self.agent_id,
            "sample_count_non_abstain": self.sample_count_non_abstain,
            "long_window_score": self.long_window_score,
            "recent_window_score": self.recent_window_score,
            "delta": self.delta,
            "long_window_stdev": self.long_window_stdev,
            "z_score": self.z_score,
            "drift_flag": self.drift_flag,
        }


def build_drift_report(
    pairs: Iterable[PredOutPair],
    *,
    recent_window: int = 50,
    z_threshold: float = 2.0,
) -> List[DriftReportRow]:
    """
    For each agent, compare recent N composite scores to the long-window
    distribution. Reports z-score and a flag when |z| > z_threshold AND the
    long window is at least 2 * recent_window samples.
    """
    pairs = list(pairs)
    by_agent: Dict[str, List[PredOutPair]] = {}
    for p, o in pairs:
        if p.abstain:
            continue
        by_agent.setdefault(p.agent_id, []).append((p, o))

    rows: List[DriftReportRow] = []
    for agent_id, agent_pairs in by_agent.items():
        ordered = sorted(
            agent_pairs,
            key=lambda po: _parse_ts_safe(po[0].asof_timestamp) or pd.Timestamp.min.tz_localize("UTC"),
        )
        composites = [o.composite_score for _, o in ordered]
        n = len(composites)
        if n == 0:
            continue
        long_mean = _mean(composites)
        long_std = _safe_stdev(composites)
        recent = composites[-recent_window:] if n >= recent_window else composites
        recent_mean = _mean(recent)
        delta = recent_mean - long_mean

        if long_std > 1e-9 and len(recent) > 0:
            z = delta / (long_std / math.sqrt(len(recent)))
        else:
            z = 0.0
        flag = abs(z) > z_threshold and n > 2 * recent_window

        rows.append(DriftReportRow(
            agent_id=agent_id,
            sample_count_non_abstain=n,
            long_window_score=long_mean,
            recent_window_score=recent_mean,
            delta=delta,
            long_window_stdev=long_std,
            z_score=z,
            drift_flag=flag,
        ))
    rows.sort(key=lambda r: -abs(r.z_score))
    return rows


def format_drift_report(rows: Sequence[DriftReportRow]) -> str:
    if not rows:
        return "(no agents)"
    header = (
        f"{'agent_id':<32} {'n_nz':>5} {'long':>7} {'recent':>7} "
        f"{'delta':>7} {'std':>5} {'z':>6} {'flag':>4}"
    )
    lines = [header, "-" * len(header)]
    for r in rows:
        lines.append(
            f"{r.agent_id:<32} {r.sample_count_non_abstain:>5d} "
            f"{r.long_window_score:>7.4f} {r.recent_window_score:>7.4f} "
            f"{r.delta:>+7.4f} {r.long_window_stdev:>5.3f} "
            f"{r.z_score:>+6.2f} {'!' if r.drift_flag else ' ':>4}"
        )
    return "\n".join(lines)


# ===========================================================================
# Bundle
# ===========================================================================

@dataclass
class HorizonReportBundle:
    leaderboard: List[AgentLeaderboardRow] = field(default_factory=list)
    timeframe_horizon: List[TimeframeHorizonCell] = field(default_factory=list)
    session_matrix: List[SessionMatrixCell] = field(default_factory=list)
    best_edge: List[BestEdgeCell] = field(default_factory=list)
    calibration: List[CalibrationReportEntry] = field(default_factory=list)
    drift: List[DriftReportRow] = field(default_factory=list)

    def as_dict(self) -> Dict[str, object]:
        return {
            "leaderboard": [r.as_dict() for r in self.leaderboard],
            "timeframe_horizon": [c.as_dict() for c in self.timeframe_horizon],
            "session_matrix": [c.as_dict() for c in self.session_matrix],
            "best_edge": [c.as_dict() for c in self.best_edge],
            "calibration": [c.as_dict() for c in self.calibration],
            "drift": [r.as_dict() for r in self.drift],
        }


def build_full_report(
    store: HorizonPredictionStore,
    *,
    min_samples_cell: int = 5,
    min_samples_edge: int = 20,
    min_samples_calibration: int = 20,
    n_bins: int = 10,
    drift_recent_n: int = 50,
    drift_threshold: float = 0.05,
    drift_z_threshold: float = 2.0,
) -> HorizonReportBundle:
    """
    One-shot convenience: load all stored pairs and build every report.
    """
    pairs = _load_pairs(store)
    return HorizonReportBundle(
        leaderboard=build_agent_leaderboard(
            pairs,
            n_bins=n_bins,
            drift_recent_n=drift_recent_n,
            drift_threshold=drift_threshold,
        ),
        timeframe_horizon=build_timeframe_horizon_matrix(pairs, min_samples=min_samples_cell),
        session_matrix=build_session_matrix(pairs, min_samples=min_samples_cell),
        best_edge=build_best_edge_cells(pairs, min_samples=min_samples_edge),
        calibration=build_calibration_report(pairs, min_samples=min_samples_calibration, n_bins=n_bins),
        drift=build_drift_report(pairs, recent_window=drift_recent_n, z_threshold=drift_z_threshold),
    )


def format_full_report(bundle: HorizonReportBundle) -> str:
    parts: List[str] = []
    parts.append("== Agent Leaderboard ==")
    parts.append(format_agent_leaderboard(bundle.leaderboard))
    parts.append("")
    parts.append("== Timeframe x Horizon ==")
    parts.append(format_timeframe_horizon_matrix(bundle.timeframe_horizon))
    parts.append("")
    parts.append("== Session Matrix ==")
    parts.append(format_session_matrix(bundle.session_matrix))
    parts.append("")
    parts.append("== Best-Edge ==")
    parts.append(format_best_edge_cells(bundle.best_edge))
    parts.append("")
    parts.append("== Calibration ==")
    parts.append(format_calibration_report(bundle.calibration))
    parts.append("")
    parts.append("== Drift ==")
    parts.append(format_drift_report(bundle.drift))
    return "\n".join(parts)
