from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd

from ta_foundation.analysis.economic_calendar import EconomicCalendar, compute_event_proximity_features
from ta_foundation.analysis.regime_recommender import build_multitf_features, classify_regime
from ta_foundation.marketdata.daily_levels import compute_daily_levels_from_store
from ta_foundation.marketdata.store import MarketDataStore

from .calibrator import extract_feature_vector, find_similar_contexts
from .models import AgentStats, SimilarContext
from .store import PredictionStore

DENVER_TZ = "America/Denver"
NY_TZ = "America/New_York"


def build_prediction_context(
    market: MarketDataStore,
    calendar: EconomicCalendar,
    store: PredictionStore,
    instrument: str,
    contract: str,
    asof: pd.Timestamp,
    n_similar: int = 5,
    agent_ids: Optional[List[str]] = None,
    history_lookback_days: int = 90,
) -> Dict[str, Any]:
    """
    Assemble the full prediction context dict for LLM agents.

    asof: the timestamp AFTER session close (e.g. 17:00 Denver).
    The target_date in the returned dict is the next trading day.

    Returns a JSON-serializable dict with keys:
        instrument, contract, context_date, target_date,
        regime, multitf_features, daily_levels, event_proximity,
        had_high_impact_event, feature_vector, regime_label,
        similar_contexts, agent_stats, market_summary
    """
    asof_den = _to_denver(asof)
    target_date = _next_trading_date(asof_den)

    ctx: Dict[str, Any] = {
        "instrument": instrument,
        "contract": contract,
        "context_date": asof_den.isoformat(),
        "target_date": target_date,
    }

    # ------------------------------------------------------------------
    # 1. Multi-timeframe regime features
    # ------------------------------------------------------------------
    multitf_raw = build_multitf_features(market, instrument, contract, asof=asof_den)
    ctx["multitf_features"] = multitf_raw

    # ------------------------------------------------------------------
    # 2. Regime classification
    # ------------------------------------------------------------------
    regime = classify_regime(multitf_raw)
    ctx["regime"] = regime
    ctx["regime_label"] = regime.get("regime_id") or regime.get("primary") or "unknown"

    # ------------------------------------------------------------------
    # 3. Prior-day / week / month levels
    # ------------------------------------------------------------------
    levels = compute_daily_levels_from_store(market, instrument, contract, asof=asof_den)
    ctx["daily_levels"] = levels.as_dict()

    # ------------------------------------------------------------------
    # 4. Economic event proximity
    # ------------------------------------------------------------------
    asof_ny = asof_den.tz_convert(NY_TZ)
    event_features = compute_event_proximity_features(
        calendar, asof_ny, currencies=["USD"], lookahead_hours=36.0
    )
    ctx["event_proximity"] = event_features

    # Determine if the target_date has a high-impact event
    target_dt_start = pd.Timestamp(target_date, tz=NY_TZ)
    target_dt_end = target_dt_start + pd.Timedelta(hours=23, minutes=59)
    next_day_events = calendar.events_in_window(
        target_dt_start, target_dt_end, currencies=["USD"], min_impact="high"
    )
    ctx["had_high_impact_event"] = len(next_day_events) > 0

    # ------------------------------------------------------------------
    # 5. Feature vector for similarity search
    # ------------------------------------------------------------------
    fv = extract_feature_vector(
        multitf_raw,
        event_risk_score=float(event_features.get("event_risk_score") or 0.0),
    )
    ctx["feature_vector"] = fv

    # ------------------------------------------------------------------
    # 6. Similar historical contexts
    # ------------------------------------------------------------------
    history = store.get_recent_history(history_lookback_days, require_scored=True)
    similar: List[SimilarContext] = find_similar_contexts(fv, history, n=n_similar)
    ctx["similar_contexts"] = [s.as_dict() for s in similar]

    # ------------------------------------------------------------------
    # 7. Agent performance stats
    # ------------------------------------------------------------------
    agent_stats_dict: Dict[str, Any] = {}
    if agent_ids:
        for aid in agent_ids:
            stats: AgentStats = store.compute_rolling_agent_stats(aid)
            agent_stats_dict[aid] = stats.as_dict()
    ctx["agent_stats"] = agent_stats_dict

    # ------------------------------------------------------------------
    # 8. Human-readable market summary
    # ------------------------------------------------------------------
    ctx["market_summary"] = _build_market_summary(ctx, instrument, asof_den, similar)

    return ctx


# ---------------------------------------------------------------------------
# Market summary string
# ---------------------------------------------------------------------------

def _build_market_summary(
    ctx: Dict[str, Any],
    instrument: str,
    asof: pd.Timestamp,
    similar: List[SimilarContext],
) -> str:
    fv = ctx.get("multitf_features", {}).get("feature_values", {})
    levels = ctx.get("daily_levels", {})
    regime = ctx.get("regime", {})
    events = ctx.get("event_proximity", {})

    atr = fv.get("tf15m_atr") or fv.get("tf60m_atr") or 0.0
    pd_levels = levels.get("prior_day", {})
    pdh = pd_levels.get("high")
    pdl = pd_levels.get("low")
    pdc = pd_levels.get("close")
    pivots = levels.get("pivots", {})
    pivot = pivots.get("pp")

    regime_id = regime.get("regime_id") or regime.get("primary") or "unknown"
    regime_conf = regime.get("confidence") or 0.0
    trend_strength = fv.get("tf60m_trend_strength") or 0.0
    compression = fv.get("tf15m_compression_ratio") or 1.0
    agreement = fv.get("cross_tf_agreement") or 0.0

    event_name = events.get("next_event_name") or "none"
    event_impact = events.get("next_event_impact") or "none"
    hours_to = events.get("hours_to_next_event")
    if event_name == "none" or event_name is None:
        event_summary = "No high-impact events in the next 36 hours."
    else:
        impact_flag = "HIGH IMPACT: " if event_impact == "high" else ""
        hrs_str = f"{hours_to:.1f}h away" if hours_to is not None and hours_to != float("inf") else "time TBD"
        event_summary = f"{impact_flag}{event_name} ({hrs_str})"

    had_event = "YES" if ctx.get("had_high_impact_event") else "No"
    target_date = ctx.get("target_date") or ""

    sim_count = len(similar)
    mean_sim_score = (
        sum(s.outcome.composite_score for s in similar if s.outcome)
        / max(1, sum(1 for s in similar if s.outcome))
    ) if similar else 0.0

    lines = [
        f"=== PREDICTION CONTEXT: {instrument} for {target_date} ===",
        f"Context built: {asof.strftime('%Y-%m-%d %H:%M %Z')}",
        "",
        f"REGIME: {regime_id} (confidence {regime_conf:.0%})",
        f"  Trend strength (60m): {trend_strength:.3f} | ATR compression (15m): {compression:.2f}x | Cross-TF agreement: {agreement:.2f}",
        "",
    ]

    if pdh is not None:
        lines += [
            f"KEY LEVELS:",
            f"  PDH: {pdh:.2f} | PDL: {pdl:.2f} | PDC: {pdc:.2f}",
            f"  Classic Pivot: {pivot:.2f}" if pivot else "",
            "",
        ]

    lines += [
        f"EVENT RISK: {event_summary}",
        f"  High-impact event on target date: {had_event}",
        "",
        f"HISTORICAL ANALOGUES: {sim_count} similar past sessions found",
        f"  Average composite score on similar days: {mean_sim_score:.2f}",
    ]

    return "\n".join(l for l in lines if l is not None)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _to_denver(ts: pd.Timestamp) -> pd.Timestamp:
    ts = pd.Timestamp(ts)
    if ts.tzinfo is None:
        return ts.tz_localize(DENVER_TZ)
    return ts.tz_convert(DENVER_TZ)


def _next_trading_date(asof: pd.Timestamp) -> str:
    """
    Return the next calendar day as YYYY-MM-DD, skipping weekends.
    This is a simple heuristic; US market holidays are not accounted for.
    """
    nxt = asof + pd.Timedelta(days=1)
    while nxt.weekday() >= 5:  # Saturday=5, Sunday=6
        nxt += pd.Timedelta(days=1)
    return nxt.strftime("%Y-%m-%d")
