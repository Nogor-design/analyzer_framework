"""
Persistence for the horizon prediction system.

Mirror of `store.PredictionStore` but for `CandleHorizonPrediction` /
`CandleHorizonOutcome`. Storage is JSONL append-only, partitioned by
`(instrument, contract)` — daily and horizon records live side-by-side under
the same instrument directory but in distinct files.

Storage layout:

    {base_dir}/{instrument}_{contract}/
        horizon_predictions.jsonl   — one CandleHorizonPrediction.as_dict() per line
        horizon_outcomes.jsonl      — one CandleHorizonOutcome.as_dict() per line
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, List, Optional, Tuple

import pandas as pd

from .horizon_models import CandleHorizonOutcome, CandleHorizonPrediction


class DuplicateHorizonOutcomeError(Exception):
    """Raised when save_outcome is called for a prediction_id that already has an outcome."""


class HorizonPredictionStore:
    """
    JSONL-backed store for horizon predictions and outcomes. Append-only;
    cache invalidates on every write so in-process reads always see the
    latest state.
    """

    PRED_FILENAME = "horizon_predictions.jsonl"
    OUTCOME_FILENAME = "horizon_outcomes.jsonl"

    def __init__(self, base_dir: str | Path, instrument: str, contract: str) -> None:
        self.instrument = instrument
        self.contract = contract
        key = f"{instrument}_{contract}".replace("/", "-")
        self._dir = Path(base_dir) / key
        self._dir.mkdir(parents=True, exist_ok=True)
        self._pred_path = self._dir / self.PRED_FILENAME
        self._outcome_path = self._dir / self.OUTCOME_FILENAME
        self._predictions: Optional[List[CandleHorizonPrediction]] = None
        self._outcomes: Optional[List[CandleHorizonOutcome]] = None

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def save_prediction(self, prediction: CandleHorizonPrediction) -> None:
        """Append to predictions.jsonl. Idempotent on prediction_id."""
        self._ensure_loaded()
        existing_ids = {p.prediction_id for p in self._predictions}  # type: ignore[union-attr]
        if prediction.prediction_id in existing_ids:
            return
        with self._pred_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(prediction.as_dict(), default=str) + "\n")
        self._invalidate()

    def save_outcome(self, outcome: CandleHorizonOutcome) -> None:
        """
        Append to outcomes.jsonl. Raises DuplicateHorizonOutcomeError if an
        outcome for this prediction_id already exists. Callers expecting
        idempotency should guard via get_outcome() first.
        """
        if not outcome.prediction_id:
            raise ValueError("save_outcome: outcome.prediction_id must be set")
        self._ensure_loaded()
        existing_ids = {o.prediction_id for o in self._outcomes}  # type: ignore[union-attr]
        if outcome.prediction_id in existing_ids:
            raise DuplicateHorizonOutcomeError(
                f"Outcome for prediction_id {outcome.prediction_id!r} already exists. "
                "Call get_outcome() first to check."
            )
        with self._outcome_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(outcome.as_dict(), default=str) + "\n")
        self._invalidate()

    # ------------------------------------------------------------------
    # Read — predictions
    # ------------------------------------------------------------------

    def get_prediction(self, prediction_id: str) -> Optional[CandleHorizonPrediction]:
        self._ensure_loaded()
        for p in self._predictions:  # type: ignore[union-attr]
            if p.prediction_id == prediction_id:
                return p
        return None

    def get_all_predictions(self) -> List[CandleHorizonPrediction]:
        self._ensure_loaded()
        return list(self._predictions)  # type: ignore[union-attr]

    def get_predictions_for(
        self,
        timeframe: Optional[str] = None,
        horizon_candles: Optional[int] = None,
        session_label: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> List[CandleHorizonPrediction]:
        """
        Filter predictions by any combination of timeframe / horizon / session / agent.
        None means no filter on that field.
        """
        self._ensure_loaded()
        out: List[CandleHorizonPrediction] = []
        for p in self._predictions:  # type: ignore[union-attr]
            if timeframe is not None and p.timeframe != timeframe:
                continue
            if horizon_candles is not None and p.horizon_candles != horizon_candles:
                continue
            if session_label is not None and p.session_label != session_label:
                continue
            if agent_id is not None and p.agent_id != agent_id:
                continue
            out.append(p)
        return out

    # ------------------------------------------------------------------
    # Read — outcomes
    # ------------------------------------------------------------------

    def get_outcome(self, prediction_id: str) -> Optional[CandleHorizonOutcome]:
        self._ensure_loaded()
        for o in self._outcomes:  # type: ignore[union-attr]
            if o.prediction_id == prediction_id:
                return o
        return None

    def get_all_outcomes(self) -> List[CandleHorizonOutcome]:
        self._ensure_loaded()
        return list(self._outcomes)  # type: ignore[union-attr]

    # ------------------------------------------------------------------
    # Read — joined pairs
    # ------------------------------------------------------------------

    def get_pairs(
        self,
        timeframe: Optional[str] = None,
        horizon_candles: Optional[int] = None,
        agent_id: Optional[str] = None,
        session_label: Optional[str] = None,
        regime_label: Optional[str] = None,
        require_non_abstain: bool = True,
        asof_after: Optional[pd.Timestamp] = None,
        asof_before: Optional[pd.Timestamp] = None,
    ) -> List[Tuple[CandleHorizonPrediction, CandleHorizonOutcome]]:
        """
        Return all (prediction, outcome) pairs that match every supplied filter
        (or no filter when the argument is None).

        Use `regime_label` to filter on the agent-recorded regime in
        `feature_snapshot["regime"]`. Use `asof_after` / `asof_before` to
        bracket the prediction timestamp window (tz-aware comparisons).
        """
        self._ensure_loaded()
        outcome_index = {o.prediction_id: o for o in self._outcomes}  # type: ignore[union-attr]

        pairs: List[Tuple[CandleHorizonPrediction, CandleHorizonOutcome]] = []
        for p in self._predictions:  # type: ignore[union-attr]
            if timeframe is not None and p.timeframe != timeframe:
                continue
            if horizon_candles is not None and p.horizon_candles != horizon_candles:
                continue
            if agent_id is not None and p.agent_id != agent_id:
                continue
            if session_label is not None and p.session_label != session_label:
                continue
            if regime_label is not None:
                pred_regime = (p.feature_snapshot or {}).get("regime")
                if pred_regime != regime_label:
                    continue
            if require_non_abstain and p.abstain:
                continue
            if asof_after is not None or asof_before is not None:
                ts = _parse_ts(p.asof_timestamp)
                if ts is None:
                    continue
                if asof_after is not None and ts < asof_after:
                    continue
                if asof_before is not None and ts > asof_before:
                    continue

            outcome = outcome_index.get(p.prediction_id)
            if outcome is None:
                continue
            pairs.append((p, outcome))
        return pairs

    # ------------------------------------------------------------------
    # Iteration
    # ------------------------------------------------------------------

    def iter_predictions(self) -> List[CandleHorizonPrediction]:
        """Convenience: cached snapshot of all predictions."""
        return self.get_all_predictions()

    def reload(self) -> None:
        """Force a fresh read from disk. Useful in long-running processes."""
        self._invalidate()
        self._ensure_loaded()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _ensure_loaded(self) -> None:
        if self._predictions is None or self._outcomes is None:
            self._load()

    def _load(self) -> None:
        self._predictions = _load_jsonl(self._pred_path, CandleHorizonPrediction.from_dict)
        self._outcomes = _load_jsonl(self._outcome_path, CandleHorizonOutcome.from_dict)

    def _invalidate(self) -> None:
        self._predictions = None
        self._outcomes = None


def _load_jsonl(path: Path, factory: Callable[[dict], object]) -> list:
    records: list = []
    if not path.exists():
        return records
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(factory(json.loads(line)))
            except Exception:
                # Skip malformed lines rather than crash; storage should never
                # be a single point of failure for the prediction loop.
                continue
    return records


def _parse_ts(s: str) -> Optional[pd.Timestamp]:
    if not s:
        return None
    try:
        ts = pd.Timestamp(s)
    except (TypeError, ValueError):
        return None
    if ts.tzinfo is None:
        return None
    return ts
