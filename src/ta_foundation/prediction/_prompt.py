"""
Shared prompt-building utilities used by all prediction agents.
Extracted so ClaudeMarketAgent and OllamaMarketAgent stay DRY.
"""
from __future__ import annotations

from typing import Any, Dict, List

# ---------------------------------------------------------------------------
# System prompt (stable — cache-eligible for Claude, reused for Ollama)
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are an expert futures market analyst specializing in end-of-day analysis for the next trading session.

Your role is to analyze structured market data — including multi-timeframe technical features,
market regime, key daily levels, economic event proximity, and historical analogue sessions — and
produce a calibrated prediction for the next trading day.

## Output format

Respond with a single JSON object and nothing else. No markdown, no explanation outside the JSON.

## Required fields

{
  "trend_direction":      "bullish" | "bearish" | "neutral",
  "trend_confidence":     float [0.0, 1.0],
  "is_trending":          true | false,
  "chop_confidence":      float [0.0, 1.0],
  "predicted_high":       float,
  "predicted_low":        float  (must be <= predicted_high),
  "breakout_probability": float [0.0, 1.0],
  "breakout_direction":   "up" | "down" | "either" | "none",
  "event_risk_score":     float [0.0, 1.0],
  "key_levels": [
    {
      "price":             float,
      "label":             string,
      "level_type":        "support" | "resistance" | "pivot" | "magnet",
      "touch_probability": float [0.0, 1.0]
    }
  ],
  "reasoning": "2–4 sentence narrative"
}

## Field guidance

- trend_confidence: 0.5 = pure uncertainty; >0.7 = meaningful conviction; >0.9 = very high.
- chop_confidence: same scale applied to trending vs. chop classification.
- key_levels: include up to 8 levels. Only add a level if you have a specific price.
- touch_probability > 0.6 only when multiple factors align (prior day level + pivot + HVN).
- event_risk_score: derive from the provided event_proximity data; do not invent events.

## Calibration

- A 0.7 confidence call should be right ~70% of the time.
- Prefer 0.5–0.6 when signals conflict; reserve >0.80 for strongly aligned regimes.

## Scoring rules (for your awareness)

Trend/chop:  score = 0.5 + (confidence - 0.5) × (2 × correct - 1)
  → 0.5 confidence always scores 0.5 regardless of outcome (no information).
  → Confident wrong calls are penalised heavily; confident correct calls rewarded.
Breakout:    Brier score = 1 − (predicted_prob − actual)²
Levels:      score = touch_prob if touched, else 1 − touch_prob  (averaged across levels)
"""

# System prompt variant for Claude tool-use (slightly different instruction)
CLAUDE_TOOL_SYSTEM_PROMPT = """\
You are an expert futures market analyst specializing in end-of-day analysis for the next trading session.

Your role is to analyze structured market data provided to you — including multi-timeframe technical features,
market regime, key daily levels, economic event proximity, and historical analogue sessions — and produce a
calibrated prediction for the next trading day.

## Output contract

You MUST call the `submit_market_prediction` tool exactly once with your complete prediction.
Never output free-form JSON or answer without calling the tool.

## Fields

- **trend_direction**: "bullish", "bearish", or "neutral"
- **trend_confidence**: float [0.0, 1.0] — 0.5 = uncertainty; >0.7 = conviction; >0.9 = very high.
- **is_trending**: bool — true = directional day; false = chop/range day.
- **chop_confidence**: float [0.0, 1.0] — confidence in trending vs. chop.
- **predicted_high**: float — estimated session high in instrument points.
- **predicted_low**: float — estimated session low (must be <= predicted_high).
- **breakout_probability**: float [0.0, 1.0] — prob. of opening-range breakout.
- **breakout_direction**: "up", "down", "either", or "none".
- **event_risk_score**: float [0.0, 1.0] — from provided event_proximity data only.
- **key_levels**: list ≤8: { price, label, level_type, touch_probability [0,1] }
- **reasoning**: 2–4 sentence narrative.

## Calibration guidance

- Be well-calibrated: 0.7 confidence → right ~70% of the time.
- Prefer 0.5–0.6 when signals conflict; reserve >0.80 for strongly aligned regimes.
- touch_probability > 0.6 only when prior day level + pivot + HVN all align.

## Scoring rules (for your awareness)

Trend/chop:  score = 0.5 + (conf - 0.5) × (2 × correct - 1)
Breakout:    Brier = 1 − (p − actual)²
Levels:      score = touch_prob if touched, else 1 − touch_prob
"""


# ---------------------------------------------------------------------------
# Context → user message
# ---------------------------------------------------------------------------

def build_user_message(context: Dict[str, Any]) -> str:
    instrument = context.get("instrument", "unknown")
    contract   = context.get("contract", "unknown")
    target_date = context.get("target_date", "unknown")

    sections: List[str] = [
        f"# Market Analysis: {instrument} {contract} — Target {target_date}\n"
    ]

    # Regime
    regime = context.get("regime") or {}
    if regime:
        sections.append("## Market Regime")
        sections.append(
            f"- Regime: **{regime.get('regime_label', 'unknown')}**\n"
            f"- ADX: {regime.get('adx', 'n/a')}\n"
            f"- ATR (normalized): {regime.get('atr_normalized', 'n/a')}\n"
            f"- Trend strength: {regime.get('trend_strength', 'n/a')}"
        )

    # Multi-TF features
    mtf = context.get("multitf_features") or {}
    fv = mtf.get("feature_values") or {}
    if fv:
        sections.append("\n## Multi-Timeframe Technical Features")
        lines = []
        for k, v in sorted(fv.items()):
            if v is not None:
                lines.append(f"- {k}: {v:.4f}" if isinstance(v, float) else f"- {k}: {v}")
        sections.append("\n".join(lines))

    # Daily levels
    daily_levels = context.get("daily_levels") or {}
    if daily_levels:
        sections.append("\n## Key Daily Levels")
        pd_lvl = daily_levels.get("prior_day") or {}
        if pd_lvl:
            sections.append(
                f"- Prior Day: H={pd_lvl.get('high')} L={pd_lvl.get('low')} C={pd_lvl.get('close')}"
            )
        pivots = daily_levels.get("pivots") or {}
        if pivots:
            pp_line = f"- Pivots: PP={pivots.get('pp')}"
            for label in ("r1", "r2", "r3", "s1", "s2", "s3"):
                if pivots.get(label):
                    pp_line += f"  {label.upper()}={pivots[label]}"
            sections.append(pp_line)
        cam = daily_levels.get("camarilla") or {}
        if cam:
            sections.append(
                f"- Camarilla: R3={cam.get('r3')}  R4={cam.get('r4')}  "
                f"S3={cam.get('s3')}  S4={cam.get('s4')}"
            )

    # Economic events
    events = context.get("event_proximity") or {}
    if events:
        sections.append("\n## Economic & Event Risk")
        sections.append(
            f"- Event risk score: **{events.get('event_risk_score', 0):.2f}**\n"
            f"- Hours to next event: {events.get('hours_to_next_event', 'n/a')}\n"
            f"- Upcoming events: {events.get('upcoming_count', 0)}"
        )
        for ev in (events.get("upcoming_events") or [])[:5]:
            sections.append(
                f"  • [{ev.get('impact', '?')}] {ev.get('title', '?')} "
                f"@ {ev.get('datetime', '?')} ({ev.get('currency', '?')})"
            )

    # Agent performance stats
    stats = context.get("agent_stats") or {}
    if stats and stats.get("n_predictions", 0) >= 5:
        sections.append("\n## Your Recent Performance")
        sections.append(
            f"- Rolling accuracy ({stats.get('window_days', 60)}d): "
            f"{stats.get('rolling_accuracy', 0):.1%}\n"
            f"- Mean composite score: {stats.get('mean_composite_score', 0):.3f}\n"
            f"- Calibration error (ECE): {stats.get('ece', 0):.3f}\n"
            f"- Predictions tracked: {stats.get('n_predictions', 0)}"
        )
        if stats.get("drift_warning"):
            sections.append(f"  ⚠ DRIFT WARNING: recent performance is below your historical baseline.")

    # Historical analogues
    similar = context.get("similar_contexts") or []
    if similar:
        sections.append("\n## Historical Analogues (most similar sessions)")
        for i, sc in enumerate(similar):
            sections.append(_format_similar_context(i, sc))

    sections.append(
        "\nAnalyse all of the above and produce your prediction."
    )
    return "\n".join(sections)


def _format_similar_context(idx: int, sc: Dict[str, Any]) -> str:
    pred    = sc.get("prediction") or {}
    outcome = sc.get("outcome") or {}
    sim     = sc.get("similarity_score", 0.0)
    date    = sc.get("target_date", "unknown")

    lines = [f"### Analogue {idx + 1} — {date}  (similarity {sim:.2f})"]
    lines.append(
        f"Predicted: {pred.get('trend_direction', '?')} "
        f"(conf {pred.get('trend_confidence', 0):.0%}), "
        f"{'trending' if pred.get('is_trending') else 'chop'}, "
        f"breakout_prob={pred.get('breakout_probability', 0):.0%}"
    )
    if outcome:
        cs = outcome.get("composite_score")
        score_str = f"{cs:.3f}" if cs is not None else "n/a"
        lines.append(
            f"Actual: {outcome.get('actual_direction', '?')}, "
            f"{'trending' if outcome.get('actual_is_trending') else 'chop'}, "
            f"breakout={'yes' if outcome.get('breakout_occurred') else 'no'}, "
            f"score={score_str}"
        )
    return "\n".join(lines)
