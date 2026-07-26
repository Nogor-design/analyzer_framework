"""Deterministic markdown emitter for the daily shadow-health report (D.2).

Renders a ``ShadowHealthReport`` from ``shadow.health`` into a stable,
diff-friendly markdown document. The emitter is intentionally
LLM-free: it is the document the Scribe consumes for prose generation,
and the document operators read directly when no LLM is in the loop.

Every number rendered here is sourced from the ledger; numerical-claim
linting (B.3) on top of this artifact should be trivially clean because
no prose is interpolated.
"""

from __future__ import annotations

from typing import Optional

from ta_foundation.shadow.health import (
    AnomalyFlag,
    CandidateShadowHealth,
    ShadowHealthReport,
)


def render_health_markdown(report: ShadowHealthReport) -> str:
    lines: list[str] = []
    lines.append(f"# Shadow Health — {report.report_date}")
    lines.append("")
    lines.append(_render_summary(report))
    lines.append("")
    lines.append("## Candidates")
    if not report.candidates:
        lines.append("")
        lines.append("_No candidates currently enrolled in shadow._")
    else:
        lines.append("")
        lines.append(_render_candidate_table(report.candidates))
        lines.append("")
        for c in report.candidates:
            lines.append(_render_candidate_section(c))
            lines.append("")
    lines.append("## Anomalies")
    lines.append("")
    if not report.anomalies:
        lines.append("_None._")
    else:
        for a in report.anomalies:
            lines.append(_render_anomaly_line(a))
    return "\n".join(lines).rstrip() + "\n"


def _render_summary(report: ShadowHealthReport) -> str:
    rows = [
        ("candidates_active", report.candidates_active),
        ("total_signals_today", report.total_signals_today),
        ("total_resolved_today", report.total_resolved_today),
        ("total_no_fill_today", report.total_no_fill_today),
        ("total_open_positions", report.total_open_positions),
        ("total_net_pnl_today", _fmt_money(report.total_net_pnl_today)),
        ("trailing_window", report.trailing_window),
        ("anomalies", len(report.anomalies)),
    ]
    out = ["| metric | value |", "| --- | --- |"]
    out.extend(f"| {k} | {v} |" for k, v in rows)
    return "\n".join(out)


def _render_candidate_table(candidates: list[CandidateShadowHealth]) -> str:
    header = (
        "| candidate_id | family | instrument | signals_today | "
        "resolved_today | open | net_pnl_today | trail_pf | trail_win | trail_exp |"
    )
    divider = "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"
    rows = [header, divider]
    for c in candidates:
        rows.append(
            "| {cid} | {fam} | {ins} | {st} | {rt} | {op} | {pnl} | {pf} | {wr} | {ex} |".format(
                cid=c.candidate_id,
                fam=c.family or "—",
                ins=c.instrument or "—",
                st=c.n_signals_today,
                rt=c.n_resolved_today,
                op=c.n_open_positions,
                pnl=_fmt_money(c.net_pnl_today),
                pf=_fmt_num(c.trailing_pf),
                wr=_fmt_pct(c.trailing_win_rate),
                ex=_fmt_money(c.trailing_expectancy_net),
            )
        )
    return "\n".join(rows)


def _render_candidate_section(c: CandidateShadowHealth) -> str:
    lines = [
        f"### {c.candidate_id}",
        "",
        f"- family: {c.family or '—'}",
        f"- instrument: {c.instrument or '—'}",
        f"- signals_today: {c.n_signals_today}",
        f"- resolved_today: {c.n_resolved_today} (no_fill_today: {c.n_no_fill_today})",
        f"- open_positions: {c.n_open_positions}",
        f"- net_pnl_today: {_fmt_money(c.net_pnl_today)}",
        f"- trailing_window: last {c.trailing_window_n} resolved trades",
        f"- trailing_pf: {_fmt_num(c.trailing_pf)}",
        f"- trailing_win_rate: {_fmt_pct(c.trailing_win_rate)}",
        f"- trailing_expectancy_net: {_fmt_money(c.trailing_expectancy_net)}",
    ]
    if c.first_signal_ts_denver:
        lines.append(f"- first_signal_ts_denver: {c.first_signal_ts_denver}")
    if c.last_signal_ts_denver:
        lines.append(f"- last_signal_ts_denver: {c.last_signal_ts_denver}")
    return "\n".join(lines)


def _render_anomaly_line(a: AnomalyFlag) -> str:
    return f"- **{a.code}** [{a.candidate_id}]: {a.message}"


def _fmt_num(v: Optional[float]) -> str:
    if v is None:
        return "—"
    return f"{v:.3f}".rstrip("0").rstrip(".")


def _fmt_pct(v: Optional[float]) -> str:
    if v is None:
        return "—"
    return f"{v * 100:.1f}%"


def _fmt_money(v: Optional[float]) -> str:
    if v is None:
        return "—"
    sign = "-" if v < 0 else ""
    return f"{sign}${abs(v):,.2f}"
