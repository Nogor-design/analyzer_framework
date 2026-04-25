from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

import pandas as pd

from ta_foundation.analysis.economic_calendar import EconomicCalendar
from ta_foundation.marketdata.store import MarketDataStore

from .context_builder import build_prediction_context
from .models import DailyPrediction, PredictionOutcome, _new_id, _now_iso
from .outcome_measurer import measure_outcome
from .scorer import score_prediction
from .store import DuplicateOutcomeError, PredictionStore


class PredictionValidationError(ValueError):
    """Raised when an agent's response dict fails schema validation."""


class InsufficientBarsError(ValueError):
    """Raised when no bars are available for outcome measurement."""


# ---------------------------------------------------------------------------
# Required output fields from any agent
# ---------------------------------------------------------------------------

_REQUIRED_FIELDS = {
    "trend_direction": (str, {"bullish", "bearish", "neutral"}),
    "trend_confidence": (float, None),
    "is_trending": (bool, None),
    "chop_confidence": (float, None),
    "predicted_high": (float, None),
    "predicted_low": (float, None),
    "breakout_probability": (float, None),
    "breakout_direction": (str, {"up", "down", "either", "none"}),
    "event_risk_score": (float, None),
    "key_levels": (list, None),
    "reasoning": (str, None),
}


def _validate_agent_response(response: Dict[str, Any]) -> None:
    errors = []

    for field_name, (expected_type, valid_values) in _REQUIRED_FIELDS.items():
        if field_name not in response:
            errors.append(f"missing field: {field_name!r}")
            continue
        val = response[field_name]
        # Coerce numeric types
        if expected_type is float and isinstance(val, int):
            val = float(val)
        if expected_type is bool and isinstance(val, int):
            val = bool(val)
        if not isinstance(val, expected_type):
            errors.append(f"{field_name!r}: expected {expected_type.__name__}, got {type(val).__name__}")
            continue
        if valid_values is not None and val not in valid_values:
            errors.append(f"{field_name!r}: {val!r} not in {valid_values}")

    if "predicted_high" in response and "predicted_low" in response:
        try:
            if float(response["predicted_high"]) < float(response["predicted_low"]):
                errors.append("predicted_high must be >= predicted_low")
        except (TypeError, ValueError):
            pass

    for field_name in ("trend_confidence", "chop_confidence", "breakout_probability", "event_risk_score"):
        if field_name in response:
            try:
                v = float(response[field_name])
                if not (0.0 <= v <= 1.0):
                    errors.append(f"{field_name!r}: must be in [0.0, 1.0], got {v}")
            except (TypeError, ValueError):
                pass

    if errors:
        raise PredictionValidationError(
            f"Agent response failed validation ({len(errors)} error(s)):\n"
            + "\n".join(f"  - {e}" for e in errors)
        )


# ---------------------------------------------------------------------------
# Main prediction workflow
# ---------------------------------------------------------------------------

def predict_next_day(
    market: MarketDataStore,
    calendar: EconomicCalendar,
    store: PredictionStore,
    instrument: str,
    contract: str,
    asof: pd.Timestamp,
    agent_fn: Callable[[Dict[str, Any]], Dict[str, Any]],
    agent_id: str,
    n_similar: int = 5,
) -> DailyPrediction:
    """
    Full end-of-day prediction workflow:
      1. Build rich context (regime, levels, events, historical analogues)
      2. Call agent_fn(context) to get raw prediction dict
      3. Validate response schema
      4. Persist and return DailyPrediction

    agent_fn signature: (context: dict) -> dict
    The returned dict must match the LLM output schema (see _REQUIRED_FIELDS).
    """
    context = build_prediction_context(
        market=market,
        calendar=calendar,
        store=store,
        instrument=instrument,
        contract=contract,
        asof=asof,
        n_similar=n_similar,
        agent_ids=[agent_id],
    )

    raw_response = agent_fn(context)
    _validate_agent_response(raw_response)

    prediction = DailyPrediction(
        prediction_id=_new_id(),
        instrument=instrument,
        contract=contract,
        target_date=context["target_date"],
        created_at=_now_iso(),
        agent_id=agent_id,
        trend_direction=str(raw_response["trend_direction"]),
        trend_confidence=float(raw_response["trend_confidence"]),
        is_trending=bool(raw_response["is_trending"]),
        chop_confidence=float(raw_response["chop_confidence"]),
        predicted_high=float(raw_response["predicted_high"]),
        predicted_low=float(raw_response["predicted_low"]),
        breakout_probability=float(raw_response["breakout_probability"]),
        breakout_direction=str(raw_response["breakout_direction"]),
        event_risk_score=float(raw_response["event_risk_score"]),
        key_levels=list(raw_response.get("key_levels") or []),
        reasoning=str(raw_response.get("reasoning") or ""),
        context_snapshot=context,
    )

    store.save_prediction(prediction)
    return prediction


# ---------------------------------------------------------------------------
# Outcome measurement + learning workflow
# ---------------------------------------------------------------------------

def measure_and_learn(
    bars_next_day: pd.DataFrame,
    store: PredictionStore,
    instrument: str,
    contract: str,
    target_date: str,
    prior_atr: float,
    touch_tolerance: float = 0.25,
) -> List[PredictionOutcome]:
    """
    Post-session learning workflow:
      1. Load all predictions made for target_date
      2. Measure actual outcomes from bars
      3. Score each prediction against outcomes
      4. Persist outcomes (skipping already-scored ones)
      5. Return list of PredictionOutcome

    Intended to run once after each session closes.
    """
    if bars_next_day is None or bars_next_day.empty:
        raise InsufficientBarsError(f"No bars provided for target_date={target_date!r}")

    predictions = store.get_predictions_for_date(target_date)
    outcomes: List[PredictionOutcome] = []

    for prediction in predictions:
        # Skip if outcome already recorded (idempotency)
        existing = store.get_outcome(prediction.prediction_id)
        if existing is not None:
            existing.agent_id = prediction.agent_id
            outcomes.append(existing)
            continue

        outcome = measure_outcome(prediction, bars_next_day, prior_atr, touch_tolerance)
        outcome.agent_id = prediction.agent_id
        score_prediction(prediction, outcome)

        try:
            store.save_outcome(outcome)
        except DuplicateOutcomeError:
            pass  # concurrent write guard — use whatever is on disk

        outcomes.append(outcome)

    return outcomes


# ---------------------------------------------------------------------------
# Statistical stub agent (baseline / offline testing)
# ---------------------------------------------------------------------------

def statistical_stub_agent(context: Dict[str, Any]) -> Dict[str, Any]:
    """
    Deterministic agent that derives predictions purely from similar historical
    contexts and base rates. Used for baseline comparison and offline testing.
    No LLM calls are made.
    """
    similar = context.get("similar_contexts") or []
    scored_similar = [s for s in similar if s.get("outcome")]

    # Trend direction: plurality vote from similar outcomes
    direction_votes: Dict[str, int] = {}
    for s in scored_similar:
        d = s["outcome"]["actual_direction"]
        direction_votes[d] = direction_votes.get(d, 0) + 1
    if direction_votes:
        trend_dir = max(direction_votes, key=lambda d: direction_votes[d])
        agreement = direction_votes[trend_dir] / len(scored_similar)
    else:
        trend_dir = "neutral"
        agreement = 0.5

    # Trending vs chop
    trending_count = sum(1 for s in scored_similar if s["outcome"].get("actual_is_trending"))
    is_trending = trending_count > len(scored_similar) / 2 if scored_similar else False
    chop_conf = trending_count / len(scored_similar) if scored_similar else 0.5

    # Predicted range: use similar outcomes if available, else ATR-based
    fv = context.get("multitf_features", {}).get("feature_values", {})
    atr = float(fv.get("tf15m_atr") or fv.get("tf60m_atr") or 10.0)
    levels = context.get("daily_levels", {})
    pdc = (levels.get("prior_day") or {}).get("close") or 0.0

    pred_high = pdc + 0.5 * atr if pdc else atr
    pred_low = pdc - 0.5 * atr if pdc else 0.0

    # Breakout probability: fraction of similar days with breakout
    breakout_count = sum(1 for s in scored_similar if s["outcome"].get("breakout_occurred"))
    breakout_prob = breakout_count / len(scored_similar) if scored_similar else 0.3

    breakout_dirs: Dict[str, int] = {}
    for s in scored_similar:
        bd = s["outcome"].get("breakout_direction_actual") or "none"
        if bd != "none":
            breakout_dirs[bd] = breakout_dirs.get(bd, 0) + 1
    breakout_dir = max(breakout_dirs, key=lambda d: breakout_dirs[d]) if breakout_dirs else "none"

    # Key levels: pass through daily levels with default touch_probability
    key_levels = []
    dl = context.get("daily_levels") or {}
    for label, price in _extract_level_prices(dl):
        if price is not None:
            key_levels.append({
                "price": price,
                "label": label,
                "level_type": "pivot" if "pivot" in label.lower() or label in ("pp",) else "support",
                "touch_probability": 0.5,
            })

    n_similar = len(scored_similar)
    mean_sim = (
        sum(s.get("similarity_score", 0) for s in similar) / len(similar) if similar else 0.0
    )

    return {
        "trend_direction": trend_dir,
        "trend_confidence": float(agreement),
        "is_trending": is_trending,
        "chop_confidence": float(chop_conf),
        "predicted_high": float(pred_high),
        "predicted_low": float(pred_low),
        "breakout_probability": float(breakout_prob),
        "breakout_direction": breakout_dir if breakout_dir in ("up", "down", "either", "none") else "none",
        "event_risk_score": float(context.get("event_proximity", {}).get("event_risk_score") or 0.0),
        "key_levels": key_levels[:8],  # limit to top 8
        "reasoning": (
            f"Statistical stub: based on {n_similar} similar historical contexts "
            f"(mean similarity {mean_sim:.2f}). "
            f"Trend vote: {trend_dir} ({agreement:.0%}), "
            f"{'trending' if is_trending else 'chop'} expected, "
            f"breakout prob {breakout_prob:.0%}."
        ),
    }


def _extract_level_prices(daily_levels: Dict[str, Any]):
    """Yield (label, price) pairs from a DailyLevels.as_dict() output."""
    pd_levels = daily_levels.get("prior_day") or {}
    for k in ("high", "low", "close"):
        v = pd_levels.get(k)
        if v:
            yield f"pd_{k}", float(v)

    pivots = daily_levels.get("pivots") or {}
    for k in ("pp", "r1", "s1", "r2", "s2"):
        v = pivots.get(k)
        if v:
            yield k.upper(), float(v)

    cam = daily_levels.get("camarilla") or {}
    for k in ("r4", "s4", "r3", "s3"):
        v = cam.get(k)
        if v:
            yield f"cam_{k.upper()}", float(v)
