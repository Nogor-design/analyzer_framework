from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from ta_foundation.reports.html.sections.large_candle_excursion_downstream_common import (
    get_derived_payload,
    hdr,
    cell,
    fmt,
    info_box,
    section_title,
)


def render_large_candle_excursion_regime_findings_explainer(ctx: dict) -> str:
    data = get_derived_payload(ctx, "large_candle_excursion_findings")
    if not data:
        return info_box("Regime findings explainer unavailable: findings data missing.")
    if not data.get("has_source"):
        return info_box(f"Regime findings explainer unavailable: {data.get('message', 'source analytics missing')}.")

    rd = data.get("regime_discovery") or {}
    interactions = rd.get("onset_path_interaction_analysis") or {}
    if not interactions:
        return info_box("Regime findings explainer unavailable: onset x early-path output missing.")

    options = ctx.get("options") or {}
    top_n = int(options.get("top_n", 8))
    ledger = interactions.get("tradeable_regime_candidate_ledger") or []
    matrix = interactions.get("interaction_matrix") or []
    decomposition = interactions.get("edge_decomposition_table") or []
    readiness = interactions.get("live_decision_readiness_screen") or []
    candidates = ledger[:top_n] if ledger else matrix[:top_n]

    html = '<div style="font-family:Arial,sans-serif">'
    html += section_title("Regime Findings Explainer")
    html += _p(
        "This compact section translates the top onset x early-path findings into plain trading language. "
        "It explains what the labels mean, when they are known, what the measured edge was, and what the label does not prove."
    )
    html += _warn(
        "Important: early-path labels such as explosive_start are not known at the signal close. "
        "They use the first post-entry bars and should be treated as management or validation inputs unless separately tested as delayed entries."
    )

    html += _h4("Label Glossary")
    html += _table(
        ["Label", "Plain-English Meaning", "Known When", "Not a Claim That"],
        [
            [
                "first_large_after_failed_continuation",
                "The prior qualifying large-candle continuation attempt looked weak: the existing implementation marks this when the previous signal had weak early reversal behavior and re-broke the signal extreme. It is not a fixed tick threshold.",
                "At the next signal close, assuming the prior signal's early-path state is already known.",
                "It does not mean price failed by exactly N ticks or that the next large candle must continue.",
            ],
            [
                "explosive_start",
                _explosive_definition(data),
                "After the first two post-entry bars.",
                "It is not an entry-time label unless you intentionally wait for confirmation.",
            ],
            [
                "orderly_start",
                _orderly_definition(data),
                "After the first two post-entry bars.",
                "It does not guarantee a runner; it only says the first validation path was cleaner than weak/mixed.",
            ],
            [
                "weak_start / rebreak_yes",
                "The reversal did not reclaim enough ground early, adverse movement was large, or price re-broke the signal candle extreme.",
                "After the first two post-entry bars.",
                "It does not automatically mean flip; it is a warning state that needs continuation evidence.",
            ],
            [
                "MFE percent",
                "Maximum favorable reversal excursion expressed as a percent of the signal candle size. If the candle was 80 ticks and avg MFE is 50%, that is about 40 ticks.",
                "Research outcome metric after the forward window.",
                "It is not known at entry and should not be used as a live trigger.",
            ],
        ],
    )

    html += _h4("Top Findings, Translated")
    if not candidates:
        html += info_box("No top regime candidates were available under the current thresholds.", color="#f8f9fa", border="#dee2e6")
    else:
        for row in candidates:
            key = (str(row.get("onset_condition")), str(row.get("early_path_condition")))
            m = _find_by_key(matrix, key) or row
            d = _find_by_key(decomposition, key) or {}
            r = _find_by_key(readiness, key) or {}
            html += _finding_card(row, m, d, r)

    html += _h4("How to Convert Percent-of-Candle to Ticks")
    html += _p(
        "Most regime movement fields are normalized by signal candle size so they can compare 1m, 2m, 3m, and 5m candles. "
        "Ticks are computed as: "
        + _code("ticks = signal_candle_size_ticks * percent_of_candle / 100")
        + ". Example: if a signal candle is 60 ticks and the row shows avg MFE 75%, the historical average favorable reversal excursion is about 45 ticks."
    )
    html += _warn(
        "The report can show percent directly from the interaction matrix. Exact tick expectations require the actual signal candle size for that event or an added average-size column in a future refinement."
    )

    html += _h4("What To Inspect First")
    html += _table(
        ["Question", "Where To Look", "Decision Use"],
        [
            ["Is this a hold, scalp, flip-watch, or avoid candidate?", "Tradeable Regime Candidate Ledger / Action-Specific Tables", "Choose the next paper-test hypothesis."],
            ["Does the interaction add information beyond early path alone?", "Edge Decomposition", "Avoid over-crediting overlapping onset labels."],
            ["Can I know this live?", "Live-Decision Readiness Screen", "Separate entry filters from post-entry management rules."],
            ["How does it fail?", "Failure Mode Audit", "Define scratch, shutdown, and flip-watch conditions."],
        ],
    )

    html += "</div>"
    return html


def _finding_card(row: Dict[str, Any], matrix_row: Dict[str, Any], decomp: Dict[str, Any], readiness: Dict[str, Any]) -> str:
    onset = str(row.get("onset_condition") or "")
    path = str(row.get("early_path_condition") or "")
    action = row.get("candidate_action") or row.get("operational_action") or "review"
    name = row.get("candidate_name") or f"{onset} x {path}"
    html = (
        '<div style="border:1px solid #dbe2ea;border-radius:6px;padding:12px;margin:12px 0;background:#fff">'
        f'<h4 style="margin:0 0 8px;color:#1f2937">{name}</h4>'
    )
    html += _p(_plain_english_pair(onset, path))
    html += _table(
        ["Action", "N", "WR", "Fail", "Runner", "Expectancy", "Avg MFE", "Avg MAE", "Cluster", "Decay", "Confidence"],
        [[
            action,
            row.get("n") or matrix_row.get("n"),
            fmt(row.get("win_rate") or matrix_row.get("win_rate"), 1, "%"),
            fmt(row.get("fail_rate") or matrix_row.get("fail_rate"), 1, "%"),
            fmt(row.get("runner_rate") or matrix_row.get("runner_rate"), 1, "%"),
            fmt(row.get("expectancy_ticks") or matrix_row.get("expectancy_ticks"), 2),
            fmt(matrix_row.get("avg_mfe_pct"), 1, "% of candle"),
            fmt(matrix_row.get("avg_mae_pct"), 1, "% of candle"),
            fmt(row.get("cluster_participation_rate") or matrix_row.get("cluster_participation_rate"), 1, "%"),
            fmt(row.get("median_decay_minutes") or matrix_row.get("median_decay_minutes"), 1, " min"),
            row.get("confidence_label") or "research",
        ]],
    )
    html += _p(
        "<b>What it says:</b> "
        + _interpret_metrics(row, matrix_row)
    )
    html += _p(
        "<b>What it does not say:</b> "
        + "It does not say the next candle must move a fixed number of ticks. It says that, in this historical event pool, this background-plus-validation state had the measured reversal, failure, runner, and cluster profile above."
    )
    if decomp:
        html += _p(
            "<b>Interaction check:</b> "
            + str(decomp.get("interpretation") or "No decomposition interpretation available.")
        )
    if readiness:
        html += _p(
            "<b>Live-use timing:</b> "
            + f"{readiness.get('live_actionability_label', 'review')} - {readiness.get('leakage_warning', '')}"
        )
    note = row.get("practical_rule_note")
    if note:
        html += _note(f"<b>Practical research note:</b> {note}")
    caution = row.get("rejection_or_caution_reason")
    if caution:
        html += _warn(f"<b>Caution:</b> {caution}")
    html += "</div>"
    return html


def _plain_english_pair(onset: str, path: str) -> str:
    onset_text = {
        "first_large_after_failed_continuation": "A prior large-candle continuation attempt showed weak early behavior and re-broke the signal extreme; this row looks at the next qualifying large-candle context.",
        "first_large_after_directional_run": "The signal appears after a directional lead-in or transition from a directional run.",
        "first_large_after_session_range_break": "The signal occurs around a session high/low boundary interaction.",
        "first_large_at_key_level_with_extended_vwap_stretch": "The signal is near a key level while price is extended from VWAP.",
        "first_large_after_compression": "Recent large-candle density was quiet, then a larger expansion candle appeared.",
    }.get(onset, "This onset label describes the background state before the signal.")
    path_text = {
        "explosive_start": "The reversal validated quickly: favorable reversal movement in the first two bars was large relative to candle size, adverse movement was limited, midpoint was reclaimed, and the signal extreme was not re-broken.",
        "orderly_start": "The reversal made a decent early favorable move and reclaimed midpoint without the more aggressive explosive threshold.",
        "weak_start": "The early path was poor or adverse: little favorable movement, too much adverse movement, or a rebreak.",
        "rebreak_yes": "Price re-broke the signal candle extreme during the early validation window.",
        "rebreak_no": "Price avoided re-breaking the signal candle extreme during the early validation window.",
        "midpoint_reclaim_yes": "Price reclaimed the signal candle midpoint during the early validation window.",
        "midpoint_reclaim_no": "Price did not reclaim the signal candle midpoint during the early validation window.",
    }.get(path, "The early-path label describes what happened during the first post-entry bars.")
    return f"<b>Plain English:</b> {onset_text} {path_text}"


def _interpret_metrics(row: Dict[str, Any], matrix_row: Dict[str, Any]) -> str:
    mfe = matrix_row.get("avg_mfe_pct")
    mae = matrix_row.get("avg_mae_pct")
    exp = row.get("expectancy_ticks") or matrix_row.get("expectancy_ticks")
    cluster = row.get("cluster_participation_rate") or matrix_row.get("cluster_participation_rate")
    parts = [
        f"average favorable reversal excursion was {fmt(mfe, 1, '% of the signal candle')}",
        f"average adverse excursion was {fmt(mae, 1, '% of the signal candle')}",
        f"expectancy was {fmt(exp, 2)} ticks",
        f"cluster participation was {fmt(cluster, 1, '%')}",
    ]
    return "; ".join(parts) + "."


def _find_by_key(rows: List[Dict[str, Any]], key: Tuple[str, str]) -> Optional[Dict[str, Any]]:
    for r in rows:
        if (str(r.get("onset_condition")), str(r.get("early_path_condition"))) == key:
            return r
    return None


def _explosive_definition(data: Dict[str, Any]) -> str:
    cfg = (((data.get("config") or {}).get("regime_discovery") or {}).get("onset_path_interactions") or {})
    decision_cfg = (((data.get("config") or {}).get("reversal_decision_engine") or {}).get("early_path") or {})
    fav = decision_cfg.get("explosive_min_fav_2bar_pct", 45.0)
    adv = decision_cfg.get("explosive_max_adv_2bar_pct", 20.0)
    return f"Favorable reversal movement in the first two bars is at least {fav}% of the signal candle, adverse movement is no more than {adv}%, midpoint is reclaimed, and the signal extreme is not re-broken."


def _orderly_definition(data: Dict[str, Any]) -> str:
    decision_cfg = (((data.get("config") or {}).get("reversal_decision_engine") or {}).get("early_path") or {})
    fav = decision_cfg.get("orderly_min_fav_2bar_pct", 25.0)
    adv = decision_cfg.get("orderly_max_adv_2bar_pct", 35.0)
    return f"Favorable reversal movement in the first two bars is at least {fav}% of the signal candle, adverse movement is no more than {adv}%, and midpoint is reclaimed."


def _table(headers: List[str], rows: List[List[Any]], empty: str = "No rows available.") -> str:
    if not rows:
        return info_box(empty, color="#f8f9fa", border="#dee2e6")
    head = "<tr>" + "".join(hdr(h) for h in headers) + "</tr>"
    body = ""
    for row in rows:
        body += "<tr>" + "".join(cell(str(v if v is not None else "-")) for v in row) + "</tr>"
    return (
        '<div style="overflow-x:auto;margin-bottom:12px">'
        '<table style="width:100%;border-collapse:collapse">'
        f"<thead>{head}</thead><tbody>{body}</tbody></table></div>"
    )


def _h4(title: str) -> str:
    return f'<h4 style="margin:16px 0 8px;color:#2c3e50">{title}</h4>'


def _p(text: str) -> str:
    return f'<p style="font-size:12px;color:#444;line-height:1.55;margin:7px 0">{text}</p>'


def _code(text: str) -> str:
    return f'<code style="background:#f4f4f4;padding:2px 5px;border-radius:3px;font-size:12px">{text}</code>'


def _note(text: str) -> str:
    return (
        '<div style="background:#e8f4fd;border-left:4px solid #3498db;padding:10px 14px;border-radius:0 4px 4px 0;margin:10px 0">'
        f'<p style="color:#2c3e50;font-size:12px;margin:0;line-height:1.6">{text}</p></div>'
    )


def _warn(text: str) -> str:
    return (
        '<div style="background:#fff3cd;border-left:4px solid #ffc107;padding:10px 14px;border-radius:0 4px 4px 0;margin:10px 0">'
        f'<p style="color:#856404;font-size:12px;margin:0;line-height:1.6">{text}</p></div>'
    )
