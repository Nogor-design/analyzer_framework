"""
Horizon prediction system → HTML report section.

Renders the six structured reports produced by `prediction.horizon_reports`
(leaderboard, timeframe × horizon matrix, session matrix, best-edge,
calibration, drift) into a self-contained HTML block. Pure renderer:
takes a `HorizonReportBundle` (or builds one from a store directory)
and returns an HTML string. No file IO except the optional store read.

Configuration via report.yaml `options:` block:

    sections:
      - id: horizon_overview
        options:
          # Either: directly pass a pre-built bundle in ctx["horizon_bundle"]
          # OR: point at a store directory and let this section build it
          store_dir: ".ta_artifacts/horizon"
          instrument: "NQ"
          contract: "H25"
          min_samples_cell: 5
          min_samples_edge: 20
          min_samples_calibration: 20
          drift_recent_n: 50
          top_n_edge: 20

Drop a `HorizonReportBundle` directly into `ctx["horizon_bundle"]` to
skip the store read entirely (useful for in-memory backtest reports).
"""
from __future__ import annotations

import html
from typing import Any, Dict, Optional, Sequence

from ta_foundation.prediction.horizon_reports import (
    AgentLeaderboardRow,
    BestEdgeCell,
    CalibrationReportEntry,
    DriftReportRow,
    HorizonReportBundle,
    SessionMatrixCell,
    TimeframeHorizonCell,
    build_full_report,
)


# ---------------------------------------------------------------------------
# Public render entry point
# ---------------------------------------------------------------------------

def render_horizon_overview(ctx: Dict[str, Any]) -> str:
    options: Dict[str, Any] = ctx.get("options") or {}

    bundle: Optional[HorizonReportBundle] = ctx.get("horizon_bundle")
    if bundle is None:
        bundle = _load_bundle_from_options(options)

    if bundle is None:
        return _empty_block(
            "No horizon prediction data available. Set "
            "<code>store_dir</code>, <code>instrument</code>, and "
            "<code>contract</code> in this section's <code>options</code>, "
            "or pass a <code>HorizonReportBundle</code> via "
            "<code>ctx['horizon_bundle']</code>."
        )

    parts = [
        '<div class="horizon-overview">',
        _render_leaderboard(bundle.leaderboard),
        _render_timeframe_horizon(bundle.timeframe_horizon),
        _render_session_matrix(bundle.session_matrix),
        _render_best_edge(bundle.best_edge, top_n=int(options.get("top_n_edge", 20))),
        _render_calibration(bundle.calibration),
        _render_drift(bundle.drift),
        "</div>",
    ]
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Bundle loading
# ---------------------------------------------------------------------------

def _load_bundle_from_options(options: Dict[str, Any]) -> Optional[HorizonReportBundle]:
    store_dir = options.get("store_dir")
    instrument = options.get("instrument")
    contract = options.get("contract")
    if not (store_dir and instrument and contract):
        return None

    # Local import — keeps this section's import light when unused.
    from ta_foundation.prediction.horizon_store import HorizonPredictionStore

    store = HorizonPredictionStore(store_dir, str(instrument), str(contract))
    return build_full_report(
        store,
        min_samples_cell=int(options.get("min_samples_cell", 5)),
        min_samples_edge=int(options.get("min_samples_edge", 20)),
        min_samples_calibration=int(options.get("min_samples_calibration", 20)),
        n_bins=int(options.get("n_bins", 10)),
        drift_recent_n=int(options.get("drift_recent_n", 50)),
        drift_threshold=float(options.get("drift_threshold", 0.05)),
        drift_z_threshold=float(options.get("drift_z_threshold", 2.0)),
    )


# ---------------------------------------------------------------------------
# Section renderers — one HTML block per report
# ---------------------------------------------------------------------------

def _render_leaderboard(rows: Sequence[AgentLeaderboardRow]) -> str:
    if not rows:
        return _section_block("Agent Leaderboard", _empty_inline("No agents have produced scored predictions yet."))
    head = ["agent_id", "n", "n_nz", "abst%", "dir_acc", "composite",
            "brier", "ece", "drift Δ", "flag"]
    body = []
    for r in rows:
        body.append([
            r.agent_id,
            f"{r.sample_count:d}",
            f"{r.sample_count_non_abstain:d}",
            f"{r.abstention_rate * 100:.1f}%",
            f"{r.direction_accuracy:.3f}",
            f"{r.mean_composite_score:.4f}",
            f"{r.mean_brier_direction:.3f}",
            f"{r.ece:.3f}",
            f"{r.drift_delta:+.3f}",
            "⚠" if r.drift_flag else "",
        ])
    return _section_block("Agent Leaderboard", _table(head, body))


def _render_timeframe_horizon(cells: Sequence[TimeframeHorizonCell]) -> str:
    if not cells:
        return _section_block("Timeframe × Horizon", _empty_inline("No cells meet the sample-count guard."))
    head = ["agent_id", "tf", "horizon", "n", "dir_acc", "composite", "brier"]
    body = []
    for c in cells:
        body.append([
            c.agent_id, c.timeframe, str(c.horizon_candles),
            f"{c.sample_count:d}",
            f"{c.direction_accuracy:.3f}",
            f"{c.mean_composite_score:.4f}",
            f"{c.mean_brier_direction:.3f}",
        ])
    return _section_block("Timeframe × Horizon", _table(head, body))


def _render_session_matrix(cells: Sequence[SessionMatrixCell]) -> str:
    if not cells:
        return _section_block("Session Matrix", _empty_inline("No cells meet the sample-count guard."))
    head = ["agent_id", "session", "tf", "horizon", "n", "composite"]
    body = []
    for c in cells:
        body.append([
            c.agent_id, c.session_label, c.timeframe, str(c.horizon_candles),
            f"{c.sample_count:d}", f"{c.mean_composite_score:.4f}",
        ])
    return _section_block("Session Matrix", _table(head, body))


def _render_best_edge(cells: Sequence[BestEdgeCell], top_n: int) -> str:
    if not cells:
        return _section_block(
            "Best-Edge Cells",
            _empty_inline("No cells meet the min-samples guard."),
        )
    head = ["agent_id", "tf", "horizon", "session", "regime", "n",
            "edge (ATR)", "composite"]
    body = []
    for c in list(cells)[: max(1, int(top_n))]:
        body.append([
            c.agent_id, c.timeframe, str(c.horizon_candles),
            c.session_label, c.regime_label,
            f"{c.sample_count:d}",
            f"{c.realized_edge_atr:+.4f}",
            f"{c.mean_composite_score:.4f}",
        ])
    return _section_block(
        "Best-Edge Cells",
        '<div style="font-size:0.9em; color:#666; margin-bottom:6px;">'
        "Edge = mean of <code>sign(argmax) × actual_return_atr</code>; "
        "positive means the agent's argmax direction was paying off."
        "</div>"
        + _table(head, body),
    )


def _render_calibration(entries: Sequence[CalibrationReportEntry]) -> str:
    if not entries:
        return _section_block(
            "Calibration",
            _empty_inline("No buckets meet the min-samples guard."),
        )
    parts: list[str] = []
    parts.append(
        '<div style="font-size:0.9em; color:#666; margin-bottom:6px;">'
        "Top-label ECE per (agent, tf, horizon, session, regime) bucket. "
        "Reliability bands show <code>[bin_center: confidence acc n]</code>."
        "</div>"
    )
    head = ["bucket", "n_nz", "dir_acc", "ECE"]
    body = []
    bucket_details: list[str] = []
    for e in entries:
        body.append([
            e.bucket.as_string(),
            f"{e.sample_count_non_abstain:d}",
            f"{e.direction_accuracy:.3f}",
            f"{e.ece:.3f}",
        ])
        if e.reliability_buckets:
            bands = "  ".join(
                f"[{rb['bin_center']:.2f}: conf={rb['mean_confidence']:.2f} "
                f"acc={rb['fraction_correct']:.2f} n={rb['count']}]"
                for rb in e.reliability_buckets
            )
            bucket_details.append(
                f'<div style="font-size:0.85em; color:#444; margin:4px 0 8px 0;">'
                f"<strong>{html.escape(e.bucket.as_string())}</strong>: "
                f"{html.escape(bands)}</div>"
            )
    parts.append(_table(head, body))
    parts.extend(bucket_details)
    return _section_block("Calibration", "\n".join(parts))


def _render_drift(rows: Sequence[DriftReportRow]) -> str:
    if not rows:
        return _section_block("Drift", _empty_inline("No drift signal yet."))
    head = ["agent_id", "n_nz", "long score", "recent score", "Δ", "stdev",
            "z", "flag"]
    body = []
    for r in rows:
        body.append([
            r.agent_id,
            f"{r.sample_count_non_abstain:d}",
            f"{r.long_window_score:.4f}",
            f"{r.recent_window_score:.4f}",
            f"{r.delta:+.4f}",
            f"{r.long_window_stdev:.3f}",
            f"{r.z_score:+.2f}",
            "⚠" if r.drift_flag else "",
        ])
    return _section_block("Drift", _table(head, body))


# ---------------------------------------------------------------------------
# HTML primitives — kept inline so this module is dependency-free
# ---------------------------------------------------------------------------

def _section_block(title: str, body_html: str) -> str:
    return (
        f'<div class="horizon-section" style="margin:18px 0;">'
        f'<h3 style="margin:0 0 8px 0; font-size:1.05em;">{html.escape(title)}</h3>'
        f"{body_html}"
        f"</div>"
    )


def _table(head: Sequence[str], body: Sequence[Sequence[str]]) -> str:
    head_html = "".join(
        f'<th style="text-align:left; padding:4px 10px; '
        f'border-bottom:1px solid #d0d0d0; font-weight:600;">{html.escape(h)}</th>'
        for h in head
    )
    rows_html = []
    for row in body:
        cells = "".join(
            f'<td style="padding:4px 10px; border-bottom:1px solid #eee; '
            f'font-variant-numeric: tabular-nums;">{html.escape(c)}</td>'
            for c in row
        )
        rows_html.append(f"<tr>{cells}</tr>")
    return (
        '<table style="border-collapse:collapse; font-size:0.9em; '
        'font-family: ui-monospace, SFMono-Regular, Menlo, monospace;">'
        f"<thead><tr>{head_html}</tr></thead>"
        f'<tbody>{"".join(rows_html)}</tbody>'
        "</table>"
    )


def _empty_block(message_html: str) -> str:
    return _section_block("Horizon Overview", _empty_inline(message_html))


def _empty_inline(message_html: str) -> str:
    return f'<div style="color:#888; font-style:italic;">{message_html}</div>'
