from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional, Tuple

import pandas as pd

from .models import AgentStats, DailyPrediction, PredictionOutcome

DENVER_TZ = "America/Denver"


class DuplicateOutcomeError(Exception):
    pass


class PredictionStore:
    """
    Persists DailyPrediction and PredictionOutcome records to JSONL files.

    Storage layout:
        {base_dir}/{instrument}_{contract}/
            predictions.jsonl   — one DailyPrediction.as_dict() per line
            outcomes.jsonl      — one PredictionOutcome.as_dict() per line

    Records are append-only. Cache is rebuilt on every write so in-process
    reads always reflect the latest state.
    """

    def __init__(self, base_dir: str, instrument: str, contract: str) -> None:
        self.instrument = instrument
        self.contract = contract
        key = f"{instrument}_{contract}".replace("/", "-")
        self._dir = Path(base_dir) / key
        self._dir.mkdir(parents=True, exist_ok=True)
        self._pred_path = self._dir / "predictions.jsonl"
        self._outcome_path = self._dir / "outcomes.jsonl"
        self._predictions: Optional[List[DailyPrediction]] = None
        self._outcomes: Optional[List[PredictionOutcome]] = None

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def save_prediction(self, prediction: DailyPrediction) -> None:
        self._ensure_loaded()
        existing_ids = {p.prediction_id for p in self._predictions}  # type: ignore[union-attr]
        if prediction.prediction_id in existing_ids:
            return  # idempotent; don't duplicate
        with self._pred_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(prediction.as_dict(), default=str) + "\n")
        self._invalidate()

    def save_outcome(self, outcome: PredictionOutcome) -> None:
        self._ensure_loaded()
        existing_ids = {o.prediction_id for o in self._outcomes}  # type: ignore[union-attr]
        if outcome.prediction_id in existing_ids:
            raise DuplicateOutcomeError(
                f"Outcome for prediction_id {outcome.prediction_id!r} already exists. "
                "Call get_outcome() first to check."
            )
        with self._outcome_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(outcome.as_dict(), default=str) + "\n")
        self._invalidate()

    # ------------------------------------------------------------------
    # Read — predictions
    # ------------------------------------------------------------------

    def get_prediction(self, prediction_id: str) -> Optional[DailyPrediction]:
        self._ensure_loaded()
        for p in self._predictions:  # type: ignore[union-attr]
            if p.prediction_id == prediction_id:
                return p
        return None

    def get_predictions_for_date(self, target_date: str) -> List[DailyPrediction]:
        self._ensure_loaded()
        return [p for p in self._predictions if p.target_date == target_date]  # type: ignore[union-attr]

    def get_all_predictions(self) -> List[DailyPrediction]:
        self._ensure_loaded()
        return list(self._predictions)  # type: ignore[union-attr]

    # ------------------------------------------------------------------
    # Read — outcomes
    # ------------------------------------------------------------------

    def get_outcome(self, prediction_id: str) -> Optional[PredictionOutcome]:
        self._ensure_loaded()
        for o in self._outcomes:  # type: ignore[union-attr]
            if o.prediction_id == prediction_id:
                return o
        return None

    def get_all_outcomes(self) -> List[PredictionOutcome]:
        self._ensure_loaded()
        return list(self._outcomes)  # type: ignore[union-attr]

    # ------------------------------------------------------------------
    # Read — joined history
    # ------------------------------------------------------------------

    def get_recent_history(
        self,
        n_days: int,
        require_scored: bool = True,
    ) -> List[Tuple[DailyPrediction, PredictionOutcome]]:
        """
        Return up to n_days of (prediction, outcome) pairs, most recent first.
        If require_scored=True, only includes pairs where composite_score > 0.
        """
        self._ensure_loaded()
        outcome_index = {o.prediction_id: o for o in self._outcomes}  # type: ignore[union-attr]

        pairs: List[Tuple[DailyPrediction, PredictionOutcome]] = []
        for pred in sorted(self._predictions, key=lambda p: p.target_date, reverse=True):  # type: ignore[union-attr]
            outcome = outcome_index.get(pred.prediction_id)
            if outcome is None:
                continue
            if require_scored and outcome.composite_score == 0.0:
                continue
            pairs.append((pred, outcome))

        # Keep only unique dates (take best-scored agent per date if multiple)
        seen_dates: dict = {}
        for pred, outcome in pairs:
            date = pred.target_date
            if date not in seen_dates or outcome.composite_score > seen_dates[date][1].composite_score:
                seen_dates[date] = (pred, outcome)

        sorted_pairs = sorted(seen_dates.values(), key=lambda t: t[0].target_date, reverse=True)
        return sorted_pairs[:n_days]

    def get_agent_history(
        self,
        agent_id: str,
        n_days: int,
    ) -> List[Tuple[DailyPrediction, PredictionOutcome]]:
        self._ensure_loaded()
        outcome_index = {o.prediction_id: o for o in self._outcomes}  # type: ignore[union-attr]
        pairs = []
        for pred in sorted(self._predictions, key=lambda p: p.target_date, reverse=True):  # type: ignore[union-attr]
            if pred.agent_id != agent_id:
                continue
            outcome = outcome_index.get(pred.prediction_id)
            if outcome is None:
                continue
            pairs.append((pred, outcome))
        return pairs[:n_days]

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def compute_rolling_agent_stats(
        self, agent_id: str, window_days: int = 60
    ) -> AgentStats:
        from .calibrator import compute_agent_stats
        history = self.get_agent_history(agent_id, n_days=window_days)
        return compute_agent_stats(history, agent_id, window_days)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _ensure_loaded(self) -> None:
        if self._predictions is None or self._outcomes is None:
            self._load()

    def _load(self) -> None:
        self._predictions = _load_jsonl(self._pred_path, DailyPrediction.from_dict)
        self._outcomes = _load_jsonl(self._outcome_path, PredictionOutcome.from_dict)

    def _invalidate(self) -> None:
        self._predictions = None
        self._outcomes = None


def _load_jsonl(path: Path, factory) -> list:
    records = []
    if not path.exists():
        return records
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                records.append(factory(d))
            except Exception:
                pass  # skip malformed lines
    return records
