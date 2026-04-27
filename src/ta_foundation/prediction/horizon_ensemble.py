"""
Ensemble horizon agent + stacking-weight learner.

Phase 4 of the horizon prediction system. The ensemble runs N member
agents (statistical, analogue, regime/session specialists, optionally a
Claude-backed agent) and combines their `CandleHorizonPrediction`s into
one prediction with the same shape — so the scorer, store, and reports
can treat it like any other agent.

Combination logic
-----------------
For each prediction field that is a probability or a point estimate, we
take a per-member weighted average. Direction probabilities and threshold
probabilities are renormalized to sum to 1.0 after averaging. Members
that abstain are excluded from the average; if every member abstains the
ensemble itself abstains.

Stacking weights
----------------
`compute_stacking_weights(pairs)` walks all `(prediction, outcome)` pairs
in the store and produces a `StackingWeightTable`:

    bucket = (timeframe, horizon, session, regime)
    weights[bucket][agent_id] = weight ∈ [floor, 1] with Σ weights = 1

Per-bucket weights are derived from the rolling mean composite score of
each agent in that bucket (default — any positive score field works).
A `floor_weight` keeps a poor agent in the mix at low influence so the
ensemble can recover if a previously-bad agent turns around. When the
exact bucket has no history the table falls back to pooled weights;
when the agent has no history at all, the lookup returns uniform
weights so the ensemble is never silently down-weighting fresh members.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import pandas as pd

from .horizon_models import CandleHorizonOutcome, CandleHorizonPrediction

PredOutPair = Tuple[CandleHorizonPrediction, CandleHorizonOutcome]

DEFAULT_AGENT_ID = "ensemble_v1"
DEFAULT_METHOD = "ensemble_v1"


# ---------------------------------------------------------------------------
# Stacking key + table
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class StackingKey:
    timeframe: str
    horizon_candles: int
    session_label: str
    regime_label: str

    @classmethod
    def from_prediction(cls, pred: CandleHorizonPrediction) -> "StackingKey":
        regime = str((pred.feature_snapshot or {}).get("regime") or "unknown")
        return cls(
            timeframe=pred.timeframe,
            horizon_candles=pred.horizon_candles,
            session_label=pred.session_label,
            regime_label=regime,
        )

    def as_string(self) -> str:
        return (
            f"tf={self.timeframe},horizon={self.horizon_candles},"
            f"session={self.session_label},regime={self.regime_label}"
        )


@dataclass
class StackingWeightTable:
    """
    Per-bucket ensemble weights with a pooled fallback. `weights[key]` and
    `fallback_weights` both sum to 1.0 over their member agent_ids.
    """
    weights: Dict[StackingKey, Dict[str, float]] = field(default_factory=dict)
    fallback_weights: Dict[str, float] = field(default_factory=dict)
    floor_weight: float = 0.05

    def lookup(
        self,
        key: StackingKey,
        agent_ids: Sequence[str],
    ) -> Dict[str, float]:
        """
        Return weights summing to 1.0 over the supplied `agent_ids`.

        Resolution order:
          1. Exact bucket match.
          2. Pooled `fallback_weights`.
          3. Uniform weights — never silently down-weight a fresh member.
        """
        if not agent_ids:
            return {}
        bucket = self.weights.get(key)
        source = bucket if bucket else self.fallback_weights
        if not source:
            n = len(agent_ids)
            return {a: 1.0 / n for a in agent_ids}

        out = {a: float(source.get(a, 0.0)) for a in agent_ids}
        total = sum(out.values())
        if total <= 0.0:
            n = len(agent_ids)
            return {a: 1.0 / n for a in agent_ids}
        return {a: w / total for a, w in out.items()}

    def as_dict(self) -> Dict[str, object]:
        return {
            "weights": {k.as_string(): dict(v) for k, v in self.weights.items()},
            "fallback_weights": dict(self.fallback_weights),
            "floor_weight": self.floor_weight,
        }

    # ------------------------------------------------------------------
    # JSON persistence
    # ------------------------------------------------------------------

    def to_json_dict(self) -> Dict[str, Any]:
        """
        JSON-safe serialization. Keys in `weights` are stored as a list of
        explicit field tuples so the StackingKey dataclass round-trips
        unambiguously regardless of `as_string()` formatting changes.
        """
        return {
            "schema_version": 1,
            "floor_weight": self.floor_weight,
            "fallback_weights": dict(self.fallback_weights),
            "weights": [
                {
                    "key": {
                        "timeframe": k.timeframe,
                        "horizon_candles": k.horizon_candles,
                        "session_label": k.session_label,
                        "regime_label": k.regime_label,
                    },
                    "agent_weights": dict(v),
                }
                for k, v in self.weights.items()
            ],
        }

    @classmethod
    def from_json_dict(cls, d: Dict[str, Any]) -> "StackingWeightTable":
        out = cls(floor_weight=float(d.get("floor_weight", 0.05) or 0.05))
        out.fallback_weights = {
            str(a): float(w) for a, w in (d.get("fallback_weights") or {}).items()
        }
        for entry in d.get("weights") or []:
            key_d = entry.get("key") or {}
            key = StackingKey(
                timeframe=str(key_d.get("timeframe") or ""),
                horizon_candles=int(key_d.get("horizon_candles") or 0),
                session_label=str(key_d.get("session_label") or ""),
                regime_label=str(key_d.get("regime_label") or ""),
            )
            out.weights[key] = {
                str(a): float(w)
                for a, w in (entry.get("agent_weights") or {}).items()
            }
        return out

    def save_to_path(self, path: str | Path) -> None:
        """Write the table as a single-document JSON file. Creates parent dirs."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_json_dict(), indent=2), encoding="utf-8")

    @classmethod
    def load_from_path(cls, path: str | Path) -> "StackingWeightTable":
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"stacking weight table not found: {p}")
        return cls.from_json_dict(json.loads(p.read_text(encoding="utf-8")))


def compute_stacking_weights(
    pairs: Iterable[PredOutPair],
    *,
    min_samples_per_agent: int = 10,
    floor_weight: float = 0.05,
    score_field: str = "composite_score",
) -> StackingWeightTable:
    """
    Build a `StackingWeightTable` from historical predictions and outcomes.

    For each (timeframe, horizon, session, regime) bucket, we average the
    chosen `score_field` per agent_id and normalize across agents. A
    `floor_weight` ∈ [0, 1] guarantees no agent drops below that share of
    the mass after normalization, so a transiently-bad agent can still
    contribute when conditions change. Agents with fewer than
    `min_samples_per_agent` observations in a bucket are skipped for that
    bucket but still feed the pooled fallback table.
    """
    pairs = list(pairs)
    by_cell: Dict[StackingKey, Dict[str, List[float]]] = {}
    pooled_scores: Dict[str, List[float]] = {}

    for pred, outcome in pairs:
        if pred.abstain:
            continue
        score = float(getattr(outcome, score_field, 0.0))
        key = StackingKey.from_prediction(pred)
        cell = by_cell.setdefault(key, {})
        cell.setdefault(pred.agent_id, []).append(score)
        pooled_scores.setdefault(pred.agent_id, []).append(score)

    table = StackingWeightTable(floor_weight=float(floor_weight))

    for key, agent_scores in by_cell.items():
        eligible = {
            agent_id: scores
            for agent_id, scores in agent_scores.items()
            if len(scores) >= min_samples_per_agent
        }
        if not eligible:
            continue
        means = {a: sum(s) / len(s) for a, s in eligible.items()}
        table.weights[key] = _normalize_with_floor(means, floor_weight)

    pooled = {
        a: sum(s) / len(s)
        for a, s in pooled_scores.items()
        if len(s) >= min_samples_per_agent
    }
    if pooled:
        table.fallback_weights = _normalize_with_floor(pooled, floor_weight)

    return table


def _normalize_with_floor(
    scores: Dict[str, float],
    floor_weight: float,
) -> Dict[str, float]:
    """
    Normalize raw scores into weights that sum to 1.0 with each weight
    ≥ floor_weight (post-normalization, not pre).

    The floor is enforced by reserving `floor_weight * n` of the total
    mass for the per-agent minimums and distributing the remaining
    `1 - floor_weight * n` proportionally to each agent's positive
    score. Agents with non-positive scores stay at exactly `floor_weight`.

    `floor_weight` is clipped to [0, 1/n] — anything larger is impossible
    to satisfy since the sum would exceed 1.
    """
    if not scores:
        return {}
    n = len(scores)
    floor = max(0.0, min(1.0 / n, float(floor_weight)))
    reserved = floor * n
    remaining = max(0.0, 1.0 - reserved)

    positives = {a: max(0.0, float(s)) for a, s in scores.items()}
    pos_total = sum(positives.values())
    if pos_total <= 0.0:
        return {a: 1.0 / n for a in scores}
    return {a: floor + remaining * (v / pos_total) for a, v in positives.items()}


# ---------------------------------------------------------------------------
# Ensemble agent
# ---------------------------------------------------------------------------

class EnsembleHorizonAgent:
    """
    Combine multiple horizon agents into a single weighted prediction.

    Member agents must satisfy the `predict(bars, asof_idx, horizon_candles,
    instrument, contract, timeframe) -> CandleHorizonPrediction` contract.
    Each member is called once per asof; failures from one member are
    isolated and recorded in `feature_snapshot["ensemble_errors"]`.
    """

    def __init__(
        self,
        members: Sequence[object],
        weight_table: Optional[StackingWeightTable] = None,
        agent_id: str = DEFAULT_AGENT_ID,
    ) -> None:
        if not members:
            raise ValueError("EnsembleHorizonAgent: at least one member is required")
        seen: set[str] = set()
        for m in members:
            mid = getattr(m, "agent_id", None)
            if not mid:
                raise ValueError("Each member must expose a non-empty agent_id")
            if mid in seen:
                raise ValueError(f"Duplicate member agent_id: {mid!r}")
            seen.add(mid)
        self.agent_id = agent_id
        self.members: List[object] = list(members)
        self.weight_table = weight_table or StackingWeightTable()

    # ------------------------------------------------------------------
    def predict(
        self,
        bars: pd.DataFrame,
        asof_idx: int,
        horizon_candles: int,
        instrument: str,
        contract: str,
        timeframe: str,
    ) -> CandleHorizonPrediction:
        member_preds: List[CandleHorizonPrediction] = []
        member_errors: List[str] = []

        for m in self.members:
            try:
                p = m.predict(
                    bars=bars,
                    asof_idx=asof_idx,
                    horizon_candles=horizon_candles,
                    instrument=instrument,
                    contract=contract,
                    timeframe=timeframe,
                )
            except Exception as exc:  # noqa: BLE001 — boundary defensive
                member_errors.append(f"{getattr(m, 'agent_id', '?')}: {type(exc).__name__}: {exc}")
                continue
            member_preds.append(p)

        non_abstain = [p for p in member_preds if not p.abstain]

        if not non_abstain:
            return self._build_abstain(
                bars=bars,
                asof_idx=asof_idx,
                horizon_candles=horizon_candles,
                instrument=instrument,
                contract=contract,
                timeframe=timeframe,
                member_preds=member_preds,
                errors=member_errors,
            )

        anchor = non_abstain[0]
        key = StackingKey.from_prediction(anchor)
        agent_ids = [p.agent_id for p in non_abstain]
        weights = self.weight_table.lookup(key, agent_ids)

        return _combine_predictions(
            preds=non_abstain,
            weights=weights,
            anchor=anchor,
            agent_id=self.agent_id,
            errors=member_errors,
            n_abstaining=len(member_preds) - len(non_abstain),
            n_total_members=len(self.members),
        )

    # ------------------------------------------------------------------
    def _build_abstain(
        self,
        bars: pd.DataFrame,
        asof_idx: int,
        horizon_candles: int,
        instrument: str,
        contract: str,
        timeframe: str,
        member_preds: Sequence[CandleHorizonPrediction],
        errors: Sequence[str],
    ) -> CandleHorizonPrediction:
        # Borrow session + regime from the first member that produced any
        # prediction so the abstain has useful diagnostic context. If none
        # produced anything, fall back to bare defaults.
        if member_preds:
            anchor = member_preds[0]
            session = anchor.session_label
            asof_iso = anchor.asof_timestamp
            feature_snapshot = dict(anchor.feature_snapshot or {})
        else:
            session = ""
            try:
                asof_iso = pd.Timestamp(bars.iloc[asof_idx]["dt"]).isoformat()
            except Exception:  # noqa: BLE001
                asof_iso = ""
            feature_snapshot = {}

        feature_snapshot["ensemble_members"] = [getattr(m, "agent_id", "?") for m in self.members]
        feature_snapshot["ensemble_member_outcomes"] = [
            {"agent_id": p.agent_id, "abstain": p.abstain, "abstain_reason": p.abstain_reason}
            for p in member_preds
        ]
        if errors:
            feature_snapshot["ensemble_errors"] = list(errors)

        reason = "all_members_abstained" if member_preds else "all_members_errored"
        return CandleHorizonPrediction(
            agent_id=self.agent_id,
            instrument=instrument,
            contract=contract,
            timeframe=timeframe,
            asof_timestamp=asof_iso,
            session_label=session,
            horizon_candles=int(horizon_candles),
            bullish_probability=0.0,
            bearish_probability=0.0,
            neutral_probability=0.0,
            confidence=0.0,
            sample_size=0,
            effective_sample_size=0.0,
            method_used=DEFAULT_METHOD,
            fallback_level=99,
            feature_snapshot=feature_snapshot,
            reasoning_summary=(
                f"Ensemble abstained: {len(member_preds)} member predictions, "
                f"{len(errors)} member errors, no usable member output."
            ),
            abstain=True,
            abstain_reason="insufficient_samples" if reason == "all_members_abstained" else "configuration_error",
        )


# ---------------------------------------------------------------------------
# Combination
# ---------------------------------------------------------------------------

def _wavg(values: Sequence[float], weights: Sequence[float]) -> float:
    total_w = sum(weights)
    if total_w <= 0.0:
        if not values:
            return 0.0
        return float(sum(values) / len(values))
    return float(sum(v * w for v, w in zip(values, weights)) / total_w)


def _combine_predictions(
    preds: Sequence[CandleHorizonPrediction],
    weights: Dict[str, float],
    anchor: CandleHorizonPrediction,
    agent_id: str,
    errors: Sequence[str],
    n_abstaining: int,
    n_total_members: int,
) -> CandleHorizonPrediction:
    w_list = [float(weights.get(p.agent_id, 0.0)) for p in preds]

    bull = _wavg([p.bullish_probability for p in preds], w_list)
    bear = _wavg([p.bearish_probability for p in preds], w_list)
    neu = _wavg([p.neutral_probability for p in preds], w_list)
    dir_total = bull + bear + neu
    if dir_total > 0.0:
        bull /= dir_total
        bear /= dir_total
        neu /= dir_total

    up_first = _wavg([p.upside_threshold_probability for p in preds], w_list)
    down_first = _wavg([p.downside_threshold_probability for p in preds], w_list)
    neither = _wavg([p.neither_threshold_probability for p in preds], w_list)
    th_total = up_first + down_first + neither
    if th_total > 0.0:
        up_first /= th_total
        down_first /= th_total
        neither /= th_total

    expected_return = _wavg([p.expected_return_points for p in preds], w_list)
    expected_return_atr = _wavg([p.expected_return_atr for p in preds], w_list)
    median_return = _wavg([p.median_return_points for p in preds], w_list)
    p10 = _wavg([p.p10_return_points for p in preds], w_list)
    p25 = _wavg([p.p25_return_points for p in preds], w_list)
    p75 = _wavg([p.p75_return_points for p in preds], w_list)
    p90 = _wavg([p.p90_return_points for p in preds], w_list)

    expected_mfe = _wavg([p.expected_mfe_points for p in preds], w_list)
    expected_mae = _wavg([p.expected_mae_points for p in preds], w_list)
    predicted_volatility = _wavg([p.predicted_volatility for p in preds], w_list)

    sample_size = int(sum(p.sample_size for p in preds))
    eff_n = float(sum(p.effective_sample_size * float(weights.get(p.agent_id, 0.0)) for p in preds))

    feature_snapshot = {
        "regime": str((anchor.feature_snapshot or {}).get("regime") or "unknown"),
        "session": anchor.session_label,
        "prior_atr": float((anchor.feature_snapshot or {}).get("prior_atr") or 0.0),
        "asof_close": float((anchor.feature_snapshot or {}).get("asof_close") or 0.0),
        "ensemble_members": [p.agent_id for p in preds],
        "ensemble_weights": {p.agent_id: float(weights.get(p.agent_id, 0.0)) for p in preds},
        "ensemble_n_abstaining": int(n_abstaining),
        "ensemble_n_total_members": int(n_total_members),
    }
    if errors:
        feature_snapshot["ensemble_errors"] = list(errors)

    weight_summary = ", ".join(
        f"{p.agent_id}={float(weights.get(p.agent_id, 0.0)):.2f}" for p in preds
    )

    return CandleHorizonPrediction(
        agent_id=agent_id,
        instrument=anchor.instrument,
        contract=anchor.contract,
        timeframe=anchor.timeframe,
        asof_timestamp=anchor.asof_timestamp,
        session_label=anchor.session_label,
        horizon_candles=anchor.horizon_candles,
        bullish_probability=bull,
        bearish_probability=bear,
        neutral_probability=neu,
        confidence=max(bull, bear, neu),
        expected_return_points=expected_return,
        expected_return_atr=expected_return_atr,
        median_return_points=median_return,
        p10_return_points=p10,
        p25_return_points=p25,
        p75_return_points=p75,
        p90_return_points=p90,
        expected_mfe_points=expected_mfe,
        expected_mae_points=expected_mae,
        upside_threshold_points=anchor.upside_threshold_points,
        downside_threshold_points=anchor.downside_threshold_points,
        upside_threshold_probability=up_first,
        downside_threshold_probability=down_first,
        neither_threshold_probability=neither,
        predicted_volatility=predicted_volatility,
        sample_size=sample_size,
        effective_sample_size=eff_n,
        method_used=DEFAULT_METHOD,
        fallback_level=max(p.fallback_level for p in preds),
        calibration_bucket=anchor.calibration_bucket,
        feature_snapshot=feature_snapshot,
        reasoning_summary=(
            f"Ensemble of {len(preds)}/{n_total_members} members "
            f"({n_abstaining} abstained); weights [{weight_summary}]."
        ),
    )
