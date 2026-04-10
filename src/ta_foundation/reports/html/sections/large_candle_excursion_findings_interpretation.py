from __future__ import annotations

"""
Large Candle Excursion — Research Interpretation Guide
=======================================================
Renders a detailed, data-aware guide explaining how to use findings output
to identify strategy candidates, interpret target curves, distinguish trade
types, evaluate robustness, and translate results into testable hypotheses.

Wires explanations to actual metrics from the findings payload where available.
"""

import math
from typing import Any, Dict, List, Optional

from ta_foundation.reports.html.sections.large_candle_excursion_downstream_common import (
    get_derived_payload,
    info_box,
    section_title,
)


# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------

def _h3(text: str) -> str:
    return (
        f'<h3 style="color:#2c3e50;border-bottom:2px solid #3498db;'
        f'padding-bottom:6px;margin-top:28px;margin-bottom:12px">{text}</h3>'
    )


def _p(text: str) -> str:
    return f'<p style="color:#444;font-size:13px;line-height:1.7;margin-bottom:10px">{text}</p>'


def _li(text: str) -> str:
    return f'<li style="color:#444;font-size:13px;line-height:1.7;margin-bottom:4px">{text}</li>'


def _ul(*items: str) -> str:
    return '<ul style="margin:8px 0 12px 20px">' + "".join(_li(i) for i in items) + "</ul>"


def _note(text: str) -> str:
    return (
        f'<div style="background:#e8f4fd;border-left:4px solid #3498db;'
        f'padding:10px 14px;border-radius:0 4px 4px 0;margin:12px 0">'
        f'<p style="color:#2c3e50;font-size:12px;margin:0;line-height:1.6">{text}</p>'
        f'</div>'
    )


def _warn(text: str) -> str:
    return (
        f'<div style="background:#fff3cd;border-left:4px solid #ffc107;'
        f'padding:10px 14px;border-radius:0 4px 4px 0;margin:12px 0">'
        f'<p style="color:#856404;font-size:12px;margin:0;line-height:1.6">{text}</p>'
        f'</div>'
    )


def _code(text: str) -> str:
    return f'<code style="background:#f4f4f4;padding:2px 5px;border-radius:3px;font-size:12px">{text}</code>'


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------

def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        f = float(v)
        return default if math.isnan(f) else f
    except Exception:
        return default


def _fmt(v: Any, digits: int = 1) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):.{digits}f}"
    except Exception:
        return str(v)


def _extract_findings(ctx: dict) -> Optional[Dict[str, Any]]:
    data = get_derived_payload(ctx, "large_candle_excursion_findings")
    if not data or not data.get("has_source"):
        return None
    return data


def _score_thresholds(data: Dict[str, Any]) -> Dict[str, float]:
    discoveries = data.get("top_discoveries") or []
    if not discoveries:
        return {}
    scores = sorted(_safe_float(r.get("composite_score")) for r in discoveries)
    idx_p75 = max(0, int(len(scores) * 0.75) - 1)
    return {"max": scores[-1], "min": scores[0], "p75": scores[idx_p75]}


def _behavior_counts(data: Dict[str, Any]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for c in data.get("strategy_cards") or []:
        bt = (c.get("metrics") or {}).get("behavior_type", "unknown")
        counts[bt] = counts.get(bt, 0) + 1
    return counts


def _target_range_summary(data: Dict[str, Any]) -> Optional[str]:
    ranges = []
    for c in data.get("strategy_cards") or []:
        rng = (c.get("targets") or {}).get("raw_stable_range")
        if rng and rng[0] is not None and rng[1] is not None:
            ranges.append((float(rng[0]), float(rng[1])))
    if not ranges:
        return None
    lo = min(r[0] for r in ranges)
    hi = max(r[1] for r in ranges)
    return f"{lo:.0f}%\u2013{hi:.0f}% of signal candle size"


def _session_leaders(data: Dict[str, Any]) -> List[str]:
    tally: Dict[str, int] = {}
    for c in data.get("strategy_cards") or []:
        for s in (c.get("session_context") or {}).get("best_sessions") or []:
            tally[s] = tally.get(s, 0) + 1
    return sorted(tally, key=lambda k: -tally[k])[:3]


# ---------------------------------------------------------------------------
# Public renderer
# ---------------------------------------------------------------------------

def render_large_candle_excursion_findings_interpretation(ctx: dict) -> str:
    data = _extract_findings(ctx)

    html = '<div style="font-family:Arial,sans-serif;max-width:900px">'
    html += section_title("Research Interpretation Guide")
    html += _p(
        "This guide explains how to use the findings output as a research workflow — "
        "from identifying strong initial candidates to translating top results into "
        "testable strategy hypotheses. Where findings data is available, "
        "examples from the actual results are included inline."
    )

    # -----------------------------------------------------------------------
    # 1. Composite score
    # -----------------------------------------------------------------------
    html += _h3("1. Composite Score — What It Measures")
    html += _p(
        "Every setup is ranked by a " + _code("composite_score") + " that combines five weighted components. "
        "Weights are configurable via " + _code("scoring_weights") + " in the YAML config."
    )

    score_method = (data.get("scoring_methodology") or {}) if data else {}
    formula = score_method.get(
        "formula",
        "0.40*win_rate + 0.20*sample_score + 0.20*expectancy_score + 0.15*stability + 0.05*simplicity - sparse_penalty",
    )
    weights = score_method.get("weights") or {}
    w_wr   = float(weights.get("win_rate",          0.40))
    w_samp = float(weights.get("sample_score",      0.20))
    w_exp  = float(weights.get("expectancy_score",  0.20))
    w_stab = float(weights.get("stability",         0.15))
    w_simp = float(weights.get("simplicity",        0.05))

    html += _ul(
        f"<b>Win rate ({int(w_wr*100)}%)</b> — fraction of trades reaching the target. "
        "The dominant factor. Setups below 50% are excluded before scoring.",
        f"<b>Sample score ({int(w_samp*100)}%)</b> — log-scaled event count. "
        "Penalizes low-N setups even when win rate looks high.",
        f"<b>Expectancy score ({int(w_exp*100)}%)</b> — normalised "
        + _code("avg_fav_ticks − avg_adv_ticks")
        + ". Rewards setups where favorable moves exceed adverse moves.",
        f"<b>Neighbor stability ({int(w_stab*100)}%)</b> — how stable win rate is when target % shifts "
        "by ±10pp. Cliff-edges score near 0; smooth plateaus score near 1.",
        f"<b>Simplicity ({int(w_simp*100)}%)</b> — small penalty for complex filter conditions.",
    )
    html += _p("Active formula: " + _code(formula))

    if data:
        thr  = _score_thresholds(data)
        top3 = (data.get("top_discoveries") or [])[:3]
        if thr:
            html += _note(
                f"In this dataset, scores range from <b>{_fmt(thr['min'], 4)}</b> to "
                f"<b>{_fmt(thr['max'], 4)}</b>. "
                f"The top-quartile threshold is approximately <b>{_fmt(thr['p75'], 4)}</b>. "
                f"Focus investigation on setups scoring above this value."
            )
        if top3:
            lines = [
                f"#{r.get('setup_definition','?')} — WR={_fmt(r.get('win_rate'))}%, "
                f"N={r.get('n_events')}, score={_fmt(r.get('composite_score'), 4)}"
                for r in top3
            ]
            html += _note("Top 3 setups: " + " &nbsp;|&nbsp; ".join(lines))

    # -----------------------------------------------------------------------
    # 2. Identifying strongest setups
    # -----------------------------------------------------------------------
    html += _h3("2. Identifying the Strongest Setups")
    html += _ul(
        "<b>Score &gt; 0.55</b> — well above median. Combines good win rate, reasonable sample, positive expectancy.",
        "<b>N &ge; 80</b> — enough events to reduce random-variation risk. Below 60 is tentative regardless of score.",
        "<b>Win rate &ge; 55%</b> — minimum viable edge above random. 58–65% is typical for robust setups.",
        "<b>Neighbor stability &ge; 0.50</b> — win rate does not collapse when target % shifts ±10pp.",
        "<b>Expectancy &gt; 0 ticks</b> — favorable moves average more ticks than adverse moves.",
    )
    html += _warn(
        "Do not select candidates by score alone. A score of 0.60 on 40 events is weaker than "
        "0.55 on 200 events. Always check N and neighbor stability before proceeding."
    )

    # -----------------------------------------------------------------------
    # 3. Target curves
    # -----------------------------------------------------------------------
    html += _h3("3. Interpreting Target Curves")
    html += _p(
        "A target curve plots win rate at each tested target percentage (e.g., 25%, 50%, 75% of signal "
        "candle size). The shape reveals where the edge lives and how tolerant it is to target choice."
    )
    html += _ul(
        "<b>Peak target %</b> — the target level with the highest win rate. "
        "This is the optimal take-profit expressed as a percentage of the signal candle's size.",
        "<b>Stable target range (plateau)</b> — the band of consecutive target values where win rate "
        "stays within the configured tolerance (default 3pp) of the peak. "
        "Wider plateau = more flexibility = safer in live trading.",
        "<b>Edge decay</b> — win rate drop from first to last tested target. "
        "Large decay (&gt;20pp) means the edge is concentrated at tight targets.",
        "<b>Monotonicity</b> — fraction of adjacent target steps where win rate declines. "
        "High monotonicity with broad plateau = clean runner.",
    )
    html += _note(
        "Translate target % to ticks: if the signal candle averages 20 ticks and peak target is 75%, "
        "the raw take-profit is 15 ticks from entry."
    )

    # -----------------------------------------------------------------------
    # 4. Scalp vs Runner vs Mixed
    # -----------------------------------------------------------------------
    html += _h3("4. Scalp vs Runner vs Mixed")

    if data:
        bcounts = _behavior_counts(data)
        if bcounts:
            desc = ", ".join(f"<b>{k}</b>: {v}" for k, v in sorted(bcounts.items()))
            html += _note(f"Behavior counts in this dataset — {desc}.")

    html += _ul(
        "<b>SCALP</b> — Peak target &le; 50% of candle size AND edge decays sharply at higher targets. "
        "Take profit immediately. Holding for larger targets destroys the edge.",
        "<b>RUNNER</b> — Win rate stable across &ge;3 consecutive target steps. "
        "Can hold for 75–125%+ of candle. Rare and valuable — prioritise for strategy development.",
        "<b>MIXED</b> — Neither extreme. Still tradeable; use the stable target range to bound take-profit.",
    )
    html += _warn(
        "The behavior classification is computed from a sample. A 'runner' label based on 3 points "
        "is weaker evidence than one based on 8. Inspect the actual target curve points in the "
        "Target Curves section before relying on the label."
    )

    # -----------------------------------------------------------------------
    # 5. Stable target range
    # -----------------------------------------------------------------------
    html += _h3("5. Using the Stable Target Range")
    html += _p(
        "The " + _code("stable_target_range")
        + " is the band of target percentages where win rate stays within tolerance of the peak. "
        "Use this range to:"
    )
    html += _ul(
        "Set take-profit anywhere inside the range without materially affecting win rate.",
        "Proxy robustness: a range spanning 50%–100% indicates flexible edge; a single-step range means parameter sensitivity.",
        "Flag setups for re-testing if the range is a single point — almost certainly overfitted.",
    )

    if data:
        rng = _target_range_summary(data)
        if rng:
            html += _note(f"In this dataset, stable target ranges approximately span {rng}.")

    # -----------------------------------------------------------------------
    # 6. Session dominance
    # -----------------------------------------------------------------------
    html += _h3("6. Dominant Session")
    html += _p(
        "Session context measures whether edge is evenly distributed or concentrated in specific "
        "sessions. Classifications use America/New_York time boundaries."
    )
    html += _ul(
        "<b>ny_open (09:30–10:30)</b> — high volatility, large candles common, continuation often strong.",
        "<b>mid_ny (10:30–14:00)</b> — lower volatility; stable but smaller average excursions.",
        "<b>power_hour (14:00–16:00)</b> — end-of-day positioning, often directional.",
        "<b>london_ny_overlap (03:00–06:30)</b> — strong European institutional flow.",
    )
    html += _warn(
        "A setup with 70%+ win rate where 90% of events come from ny_open is misleading for "
        "trades taken outside that session. Always check session concentration before live use."
    )

    if data:
        leaders = _session_leaders(data)
        if leaders:
            html += _note(f"Sessions most frequently identified as 'best' in this dataset: {', '.join(leaders)}.")

    # -----------------------------------------------------------------------
    # 7. Fragile setups
    # -----------------------------------------------------------------------
    html += _h3("7. Identifying Fragile Setups")
    html += _p(
        "A setup is fragile when its apparent edge may not survive live trading or out-of-sample "
        "testing. Watch for these warning signs (shown as badges on strategy cards):"
    )
    html += _ul(
        "<b>LOW N</b> — fewer than the configured "
        + _code("fragility_low")
        + " qualifying events. Any win rate is unreliable at this sample size.",
        "<b>SUSPECT WR</b> — win rate &ge;70% with fewer than 80 events. High win rates on small "
        "samples almost always revert to the mean.",
        "<b>UNSTABLE</b> — neighbor stability &lt;0.45. Win rate collapses when target % shifts "
        "by ±10pp. This indicates parameter over-fitting, not real edge.",
        "<b>EDGE DECAY</b> — edge decay penalty &gt;0.70. Works only at a very specific tight target. "
        "Any slippage in execution destroys the result.",
        "<b>Time-split instability</b> — win rate drops &gt;10pp between early and late periods. "
        "Setups that degraded over time may already have faded.",
    )

    if data:
        fc = len(data.get("fragility_warnings") or [])
        total = len(data.get("top_discoveries") or [])
        if fc > 0:
            html += _warn(
                f"{fc} fragility warning(s) flagged across the top {total} discoveries. "
                f"Review before any live testing."
            )
        else:
            html += _note("No fragility warnings flagged — top discoveries passed basic robustness checks.")

    # -----------------------------------------------------------------------
    # 8. Best candidate selection — tradeoffs
    # -----------------------------------------------------------------------
    html += _h3("8. Best Candidate Selection — Tradeoffs")
    html += _p(
        "The composite score is a relative ranking, not an absolute quality gate. "
        "When selecting candidates for deeper investigation, weigh these factors:"
    )

    html += '<table style="border-collapse:collapse;width:100%;margin-bottom:14px">'
    html += '<thead><tr>'
    for col in ["Factor", "Prefer", "Red flag"]:
        html += (
            f'<th style="background:#2c3e50;color:#fff;padding:7px 10px;'
            f'border:1px solid #ddd;font-size:12px;text-align:left">{col}</th>'
        )
    html += '</tr></thead><tbody>'

    rows = [
        ("Score",                 "&ge; 0.55 with N &ge; 100",          "0.70 with N &lt; 40 — likely noise"),
        ("Sample size (N)",       "N &ge; 100 for high confidence",     "N &lt; 60 regardless of win rate"),
        ("Plateau width",         "&ge; 3 steps (runner profile)",      "1 step only — parameter-sensitive"),
        ("Edge decay penalty",    "&lt; 0.30 — durable edge",           "&gt; 0.70 — collapses at higher targets"),
        ("Session concentration", "Works in 2+ sessions",               "95%+ of events in one session"),
        ("Neighbor stability",    "&ge; 0.60 — robust to param shifts", "&lt; 0.40 — cliff-edge"),
        ("Time-split robustness", "Consistent across early/mid/late",   "&gt;15pp win rate drop late-period"),
    ]
    for i, (r, pref, flag) in enumerate(rows):
        bg = "#f9f9f9" if i % 2 == 0 else "#fff"
        html += (
            f'<tr style="background:{bg}">'
            f'<td style="padding:6px 10px;border:1px solid #ddd;font-size:12px;font-weight:600">{r}</td>'
            f'<td style="padding:6px 10px;border:1px solid #ddd;font-size:12px;color:#155724">{pref}</td>'
            f'<td style="padding:6px 10px;border:1px solid #ddd;font-size:12px;color:#721c24">{flag}</td>'
            f'</tr>'
        )
    html += '</tbody></table>'

    # -----------------------------------------------------------------------
    # 9. Translating findings to strategy hypotheses
    # -----------------------------------------------------------------------
    html += _h3("9. Translating Findings into Strategy Hypotheses")
    html += _p(
        "A finding is an observation about historical price behaviour, not yet a strategy. "
        "To convert a top discovery into a testable hypothesis, add:"
    )
    html += _ul(
        "<b>Entry trigger</b> — What caused the large candle? (Range breakout vs trend candle vs news "
        "spike behave differently.) Use candle structure context — close position and wick ratio — "
        "to narrow the definition.",
        "<b>Directional filter</b> — Does the setup perform better in bull vs bear trend? "
        "Adding a 200-EMA filter or ATR-regime check can significantly improve consistency.",
        "<b>Session restriction</b> — If the dominant session is ny_open, restrict entries to "
        "09:30–10:30 ET only and re-measure out of sample.",
        "<b>Exit rule</b> — Translate peak target % to ticks. "
        "Add a stop at the P25 adverse excursion or at 50% of target size.",
        "<b>OOS test</b> — Use the first 70% of data as in-sample; test the hypothesis on the remaining 30%.",
    )
    html += _p(
        'Example hypothesis: <em>"When a Bull candle on the 1m chart exceeds 2&times; the 20-bar '
        "average body size, with close in the upper 60% of the candle range, enter long at close. "
        "Target = 75% of body size in ticks. Session = NY Open only (09:30–10:30 ET). "
        'Stop = 50% of target below entry."</em>'
    )

    # -----------------------------------------------------------------------
    # 10. Suggested next tests (from findings data)
    # -----------------------------------------------------------------------
    if data:
        next_tests = data.get("next_tests") or []
        if next_tests:
            html += _h3("10. Suggested Follow-up Tests")
            html += _p("Generated automatically from the findings analysis:")
            html += '<ol style="margin:8px 0 12px 20px">'
            for t in next_tests:
                html += f'<li style="color:#444;font-size:13px;line-height:1.7;margin-bottom:4px">{t}</li>'
            html += '</ol>'

    html += '</div>'
    return html
