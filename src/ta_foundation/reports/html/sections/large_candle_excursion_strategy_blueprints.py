from __future__ import annotations

import json
from typing import Any, Dict, List

from ta_foundation.reports.html.sections.large_candle_excursion_downstream_common import (
    cell,
    get_derived_payload,
    hdr,
    info_box,
    section_title,
)


def _fmt(v: Any, nd: int = 1) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):.{nd}f}"
    except Exception:
        return str(v)


def _session_display(session_filter: Dict[str, Any]) -> str:
    mode = str(session_filter.get("mode") or "all_session")
    if mode == "all_session":
        return "all"
    return ", ".join(session_filter.get("allowed_sessions") or []) or "—"


def _direction_display(policy: str) -> str:
    return {
        "counter_to_failed_continuation": "counter (failed cont.)",
        "counter_to_directional_run": "counter (run)",
        "counter_to_level_rejection": "counter (level)",
        "continuation_of_range_break": "with (range break)",
        "continuation_of_compression_breakout": "with (compression)",
        "signal_direction": "signal",
    }.get(policy, policy or "—")


def _summary_row(bp: Dict[str, Any]) -> str:
    prov = bp.get("provenance") or {}
    onset = bp.get("onset_detection") or {}
    candle = onset.get("candle_size") or {}
    ctx_gates = onset.get("context_gates") or {}
    stop = bp.get("stop_rule") or {}
    exit_r = bp.get("exit_rule") or {}
    pem = bp.get("post_entry_management") or {}
    primary_rule = (pem.get("primary_hold_rule") or {}).get("name") or "—"
    dom = candle.get("sweep_dominant") or {}

    wr_pct = prov.get("win_rate_pct")
    bg = "#e8f5e9" if wr_pct and float(wr_pct) >= 70 else ("#fff8e1" if wr_pct and float(wr_pct) >= 55 else "#fff")

    return (
        "<tr>"
        + cell(str(bp.get("blueprint_id") or "—"), bg=bg, bold=True)
        + cell(str(prov.get("onset_condition") or "—"))
        + cell(str(prov.get("early_path_condition") or "—"))
        + cell(str(prov.get("recommended_action") or "—"))
        + cell(_fmt(prov.get("n"), 0))
        + cell(_fmt(prov.get("win_rate_pct"), 1))
        + cell(_fmt(prov.get("runner_rate_pct"), 1))
        + cell(_fmt(prov.get("fail_rate_pct"), 1))
        + cell(_fmt(prov.get("expectancy_ticks"), 2))
        + cell(_fmt(prov.get("median_decay_minutes"), 1))
        + cell(_direction_display(str(bp.get("direction_policy") or "")))
        + cell(f"{candle.get('lookback_bars', '—')}/{candle.get('threshold_value', '—')}")
        + cell(f"{dom.get('lookback_bars', '—')}/{dom.get('threshold_value', '—')} ({dom.get('n_events_matched', 0)})")
        + cell(_fmt(ctx_gates.get("vwap_stretch_atr_min"), 2))
        + cell(_fmt(ctx_gates.get("volume_multiple_min"), 1))
        + cell(str(stop.get("recommended_stop_ticks") or "—"))
        + cell(f"{exit_r.get('scalp_target_pct_of_signal_candle', '—')}/{exit_r.get('runner_target_pct_of_signal_candle', '—')}%")
        + cell(primary_rule)
        + cell(_session_display(bp.get("session_filter") or {}))
        + cell(str(prov.get("validation_label") or "—"))
        + cell(str(prov.get("deployment_bucket") or "—"))
        + "</tr>"
    )


def _blueprint_details(bp: Dict[str, Any]) -> str:
    prov = bp.get("provenance") or {}
    warnings = bp.get("warnings") or []
    warn_html = ""
    if warnings:
        items = "".join(f"<li>{w}</li>" for w in warnings)
        warn_html = info_box(f"<b>Warnings</b><ul style='margin:6px 0 0 18px'>{items}</ul>", color="#fff3cd", border="#ffc107")

    pretty = json.dumps(bp, indent=2, default=str)
    title = f"{bp.get('blueprint_id', 'blueprint')} — {prov.get('candidate_name') or ''}"
    return (
        f"<details style='margin-bottom:10px'>"
        f"<summary style='cursor:pointer;font-weight:600;color:#2c3e50;padding:6px 0'>{title}</summary>"
        f"{warn_html}"
        f"<pre style='background:#f8f9fa;border:1px solid #dee2e6;border-radius:4px;padding:12px;font-size:11px;overflow-x:auto'>{pretty}</pre>"
        f"</details>"
    )


def render_large_candle_excursion_strategy_blueprints(ctx: dict) -> str:
    findings = get_derived_payload(ctx, "large_candle_excursion_findings") or {}
    exporter = findings.get("strategy_blueprint_exporter") or {}
    blueprints: List[Dict[str, Any]] = exporter.get("blueprints") or []
    diagnostics = exporter.get("diagnostics") or {}

    intro = (
        "<p style='color:#555;font-size:13px;margin:0 0 14px'>"
        "Machine-readable strategy blueprints.  Every numeric field a scalar or list of scalars — "
        "a strategy template can load these as inputs and swap candidates by swapping blueprints.  "
        "The sidecar <code>*_blueprints.json</code> file contains the same data for automation."
        "</p>"
    )

    if not exporter:
        return (
            section_title("Strategy Blueprints")
            + info_box("Strategy blueprint exporter did not run or produced no output.")
        )
    if not blueprints:
        skipped = (diagnostics.get("skipped") or [])[:10]
        reasons = "<ul style='margin:6px 0 0 18px'>" + "".join(
            f"<li>{s.get('candidate_name', '—')}: {s.get('reason', '—')}</li>" for s in skipped
        ) + "</ul>" if skipped else ""
        return (
            section_title("Strategy Blueprints")
            + intro
            + info_box(
                f"No blueprints passed the exporter's gating thresholds "
                f"(ledger rows: {diagnostics.get('n_ledger_rows', 0)}, "
                f"skipped: {diagnostics.get('n_skipped', 0)}). {reasons}"
            )
        )

    # Summary table.
    head = (
        "<tr>"
        + hdr("Blueprint ID")
        + hdr("Onset")
        + hdr("Early Path")
        + hdr("Action")
        + hdr("N")
        + hdr("WR%")
        + hdr("Runner%")
        + hdr("Fail%")
        + hdr("Exp (t)")
        + hdr("Decay min")
        + hdr("Direction")
        + hdr("LB/Mult (cfg)")
        + hdr("LB/Mult (sweep)")
        + hdr("VWAP ATR")
        + hdr("Vol×")
        + hdr("Stop (t)")
        + hdr("Scalp/Runner %")
        + hdr("Primary Hold")
        + hdr("Session")
        + hdr("Validation")
        + hdr("Deployment")
        + "</tr>"
    )
    body = "".join(_summary_row(bp) for bp in blueprints)
    table = (
        f"<div style='overflow-x:auto'><table style='width:100%;border-collapse:collapse;border:1px solid #ddd;font-size:11px'>"
        f"{head}{body}</table></div>"
    )

    detail_blocks = "".join(_blueprint_details(bp) for bp in blueprints)

    diag = (
        f"<p style='color:#888;font-size:11px;margin-top:10px'>"
        f"Ledger rows scanned: {diagnostics.get('n_ledger_rows', 0)} — "
        f"Emitted: {diagnostics.get('n_blueprints', 0)} — "
        f"Skipped: {diagnostics.get('n_skipped', 0)} — "
        f"Events sampled: {diagnostics.get('events_sample_size', 0)}"
        f"</p>"
    )

    return (
        section_title("Strategy Blueprints")
        + intro
        + table
        + diag
        + section_title("Blueprint Details (expand to view JSON)")
        + detail_blocks
    )
