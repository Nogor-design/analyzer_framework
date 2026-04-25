from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional


def export_large_candle_regime_summary(packages: Dict[str, Any], output_path: Path, title: str = "Large Candle Regime Summary") -> bool:
    payload = _find_summary_payload(packages)
    if not payload:
        return False
    output_path.write_text(render_large_candle_regime_summary_markdown(payload, title=title), encoding="utf-8")
    return True


def render_large_candle_regime_summary_markdown(payload: Dict[str, Any], title: str = "Large Candle Regime Summary") -> str:
    generated = datetime.now().isoformat(timespec="seconds")
    lines: list[str] = []

    def ln(text: str = "") -> None:
        lines.append(text)

    ln(f"# {title} - Executive Summary")
    ln()
    ln(f"Generated: {generated}")
    ln()

    no_candidate = payload.get("no_candidate_reason")
    if no_candidate:
        ln("## Bottom Line")
        ln(no_candidate)
        ln()

    ln("## Top Tradeable Regime Candidates")
    top = payload.get("top_tradeable_candidates") or []
    if top:
        for i, row in enumerate(top[:3], start=1):
            ln(f"{i}. {_candidate_line(row)}")
            note = row.get("practical_rule_note") or row.get("action_rationale")
            if note:
                ln(f"   - Use: {note}")
    else:
        ln("- No candidate passed the conservative tradeability screen.")
    ln()

    for heading, key in [
        ("Best Hold Candidate", "best_hold_candidate"),
        ("Best Scalp Candidate", "best_scalp_candidate"),
        ("Best Flip-Watch Candidate", "best_flip_watch_candidate"),
        ("Strongest Regime-Shutdown Warning", "strongest_regime_shutdown_warning"),
    ]:
        ln(f"## {heading}")
        row = payload.get(key)
        ln(f"- {_candidate_line(row) if row else 'None identified under current thresholds.'}")
        ln()

    ln("## Top Fragility Warnings")
    warnings = payload.get("top_fragility_warnings") or []
    if warnings:
        for row in warnings[:5]:
            modes = ", ".join(row.get("dominant_failure_modes") or [])
            ln(f"- {row.get('candidate_name', 'candidate')}: {row.get('recommended_interpretation', 'review')} ({modes or 'no dominant failure mode flagged'})")
    else:
        ln("- No fragility warnings were available.")
    ln()

    ln("## Next Recommended Tests")
    for item in payload.get("next_recommended_tests") or []:
        ln(f"- {item}")
    ln()

    ln("## What This Does NOT Prove")
    for item in payload.get("what_this_does_not_prove") or []:
        ln(f"- {item}")
    ln()

    return "\n".join(lines).rstrip() + "\n"


def _find_summary_payload(packages: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    for pkg in (packages or {}).values():
        derived = (getattr(pkg, "metadata", {}) or {}).get("derived", {}) or {}
        findings = derived.get("large_candle_excursion_findings") or {}
        regime = (findings.get("regime_discovery") or {}) if isinstance(findings, dict) else {}
        interactions = (regime.get("onset_path_interaction_analysis") or {}) if isinstance(regime, dict) else {}
        payload = interactions.get("executive_summary_payload") if isinstance(interactions, dict) else None
        if isinstance(payload, dict) and payload:
            return payload
    return None


def _candidate_line(row: Optional[Dict[str, Any]]) -> str:
    if not row:
        return "None"
    name = row.get("candidate_name") or row.get("onset_condition") or "candidate"
    action = row.get("candidate_action") or row.get("action") or "review"
    return (
        f"{name} [{action}] - "
        f"N={_fmt(row.get('n'), 0)}, WR={_fmt(row.get('win_rate'), 1)}%, "
        f"Exp={_fmt(row.get('expectancy_ticks'), 2)} ticks, "
        f"Fail={_fmt(row.get('fail_rate'), 1)}%, Runner={_fmt(row.get('runner_rate'), 1)}%, "
        f"Cluster={_fmt(row.get('cluster_participation_rate'), 1)}%, "
        f"Decay={_fmt(row.get('median_decay_minutes'), 1)} min, "
        f"Confidence={row.get('confidence_label', 'unknown')}"
    )


def _fmt(v: Any, digits: int = 1) -> str:
    if v is None:
        return "-"
    try:
        if digits == 0:
            return str(int(round(float(v))))
        return f"{float(v):.{digits}f}"
    except Exception:
        return str(v)
