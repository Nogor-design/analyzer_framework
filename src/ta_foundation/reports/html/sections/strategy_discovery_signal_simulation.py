from __future__ import annotations

"""
Strategy Discovery — Signal Corpus P&L Simulation
===================================================
Reads: pkg.metadata["derived"]["strategy_discovery"]["signal_corpus_simulation"]
       (from the __market_discovery__ synthetic package)

Shows:
  1. Combined sim card — all-rules equity curve + KPIs
  2. Per-rule cards — individual equity curve + KPIs + stop/target badge
"""

import html
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------

_CSS = """
<style>
.scs-wrap  { display:flex; flex-direction:column; gap:14px; }
.scs-card  { background:rgba(255,255,255,0.035); border-radius:12px;
             padding:14px 16px; border:1px solid rgba(255,255,255,0.07); }
.scs-card-accent { border-color:rgba(104,211,145,0.3); }

/* KPI row */
.scs-kpis { display:flex; flex-wrap:wrap; gap:18px; padding:6px 0 10px; }
.scs-kpi  { min-width:70px; }
.scs-kpi-val { font-size:1.05rem; font-weight:800; }
.scs-kpi-lbl { font-size:.62rem; opacity:.5; margin-top:2px; text-transform:uppercase; letter-spacing:.04em; }

/* Equity curve SVG */
.scs-chart { margin:6px 0 4px; }

/* Badge row */
.scs-badges { display:flex; gap:8px; flex-wrap:wrap; margin-bottom:6px; }
.scs-badge  { font-size:11px; padding:2px 8px; border-radius:5px;
              font-weight:700; white-space:nowrap; }
.scs-badge-blue   { background:rgba(59,130,246,0.15); color:#93c5fd; }
.scs-badge-green  { background:rgba(72,187,120,0.15); color:#68d391; }
.scs-badge-yellow { background:rgba(237,137,54,0.15); color:#f6ad55; }
.scs-badge-red    { background:rgba(245,101,101,0.15); color:#fc8181; }

/* Per-rule accordion */
.scs-rule-summary { cursor:pointer; font-size:.78rem; padding:6px 0; opacity:.8; }
.scs-muted { opacity:.55; font-size:.82rem; }
.scs-lbl   { font-size:.72rem; font-weight:600; text-transform:uppercase;
             letter-spacing:.05em; opacity:.5; margin:10px 0 5px; }
</style>
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt(v: Any, d: int = 1, default: str = "—") -> str:
    if v is None:
        return default
    try:
        return f"{float(v):.{d}f}"
    except Exception:
        return default


def _fmt_pct(v: Any, default: str = "—") -> str:
    if v is None:
        return default
    try:
        return f"{float(v) * 100:.1f}%"
    except Exception:
        return default


def _pf_color(pf: Optional[float]) -> str:
    if pf is None:
        return "inherit"
    f = float(pf)
    return "#68d391" if f >= 1.5 else ("#f6ad55" if f >= 1.0 else "#fc8181")


def _dd_color(dd: Optional[float]) -> str:
    if dd is None:
        return "inherit"
    f = abs(float(dd))
    return "#68d391" if f <= 20 else ("#f6ad55" if f <= 50 else "#fc8181")


def _wr_badge_cls(wr: Optional[float]) -> str:
    if wr is None:
        return "scs-badge-yellow"
    return "scs-badge-green" if float(wr) >= 0.55 else (
        "scs-badge-yellow" if float(wr) >= 0.50 else "scs-badge-red"
    )


# ---------------------------------------------------------------------------
# Inline SVG equity curve (no matplotlib)
# ---------------------------------------------------------------------------

def _svg_equity_curve(
    curve_pts: List[Dict[str, Any]],
    width: int = 520,
    height: int = 90,
    label: str = "",
) -> str:
    """Render a compact equity curve as inline SVG from equity_curve point list."""
    if not curve_pts:
        return ""

    equities = [float(p.get("equity", 0)) for p in curve_pts]
    n = len(equities)
    if n < 2:
        return ""

    eq_min = min(equities)
    eq_max = max(equities)
    eq_range = max(eq_max - eq_min, 1.0)

    pad_x, pad_y = 32, 10
    chart_w = width - pad_x * 2
    chart_h = height - pad_y * 2

    def to_x(i: int) -> float:
        return pad_x + (i / max(n - 1, 1)) * chart_w

    def to_y(v: float) -> float:
        return pad_y + chart_h - ((v - eq_min) / eq_range) * chart_h

    # Build polyline points
    poly_pts = " ".join(f"{to_x(i):.1f},{to_y(v):.1f}" for i, v in enumerate(equities))

    # Fill polygon (down to baseline)
    baseline_y = to_y(0.0)
    fill_pts = (
        f"{to_x(0):.1f},{baseline_y:.1f} "
        + poly_pts
        + f" {to_x(n - 1):.1f},{baseline_y:.1f}"
    )

    # Zero line
    zero_y = to_y(0.0)
    zero_line = (
        f'<line x1="{pad_x}" y1="{zero_y:.1f}" '
        f'x2="{width - pad_x}" y2="{zero_y:.1f}" '
        f'stroke="rgba(255,255,255,0.15)" stroke-width="1" stroke-dasharray="3,3"/>'
    )

    # Axis labels
    final_eq = equities[-1]
    final_color = "#68d391" if final_eq >= 0 else "#fc8181"
    final_label = (
        f'<text x="{width - pad_x + 2}" y="{to_y(final_eq) + 4:.1f}" '
        f'fill="{final_color}" font-size="9" font-family="monospace">'
        f'{final_eq:+.0f}t</text>'
    )

    fill_color = "rgba(72,187,120,0.08)" if final_eq >= 0 else "rgba(245,101,101,0.08)"
    line_color = "#68d391" if final_eq >= 0 else "#fc8181"

    return (
        f'<svg width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" class="scs-chart" '
        f'style="display:block;max-width:100%;">'
        f'<polygon points="{fill_pts}" fill="{fill_color}"/>'
        f'{zero_line}'
        f'<polyline points="{poly_pts}" fill="none" '
        f'stroke="{line_color}" stroke-width="1.5" stroke-linejoin="round"/>'
        f'{final_label}'
        f'</svg>'
    )


# ---------------------------------------------------------------------------
# KPI row
# ---------------------------------------------------------------------------

def _render_kpis(sim: Dict[str, Any]) -> str:
    n         = sim.get("n", 0)
    wr        = sim.get("win_rate")
    avg_t     = sim.get("avg_ticks")
    total_t   = sim.get("total_ticks")
    pf        = sim.get("profit_factor")
    max_dd    = sim.get("max_drawdown_ticks")
    n_wins    = sim.get("n_wins", 0)
    n_losses  = sim.get("n_losses", 0)

    kpis = [
        (_fmt(total_t, 1),    "Total Ticks"),
        (_fmt_pct(wr),        "Win Rate"),
        (_fmt(pf, 2),         "Prof. Factor"),
        (_fmt(avg_t, 1),      "Avg Ticks"),
        (str(n),              "N Signals"),
        (f"{n_wins}W/{n_losses}L", "Wins/Losses"),
        (_fmt(max_dd, 1),     "Max DD (ticks)"),
    ]

    def _val_style(lbl: str, v: str) -> str:
        if lbl == "Prof. Factor":
            try:
                return f"color:{_pf_color(float(v))}"
            except Exception:
                pass
        if lbl == "Max DD (ticks)":
            try:
                return f"color:{_dd_color(float(v))}"
            except Exception:
                pass
        if lbl == "Total Ticks":
            try:
                f = float(v)
                return f"color:{'#68d391' if f >= 0 else '#fc8181'}"
            except Exception:
                pass
        return ""

    items = "".join(
        f'<div class="scs-kpi">'
        f'<div class="scs-kpi-val" style="{_val_style(lbl, val)}">{html.escape(str(val))}</div>'
        f'<div class="scs-kpi-lbl">{html.escape(lbl)}</div>'
        f'</div>'
        for val, lbl in kpis
    )
    return f'<div class="scs-kpis">{items}</div>'


# ---------------------------------------------------------------------------
# Combined sim card
# ---------------------------------------------------------------------------

def _render_combined(scs: Dict[str, Any]) -> str:
    combined = scs.get("combined_sim") or {}
    if not combined or not combined.get("n"):
        return ""

    stop   = combined.get("stop_ticks", "?")
    target = combined.get("target_ticks", "?")
    rr     = round(int(target) / max(int(stop), 1), 2) if isinstance(stop, int) and isinstance(target, int) else "?"

    badges = (
        f'<span class="scs-badge scs-badge-blue">S:{stop} / T:{target} (RR:{rr})</span>'
        f'<span class="scs-badge scs-badge-yellow">Combined (all rules · deduped)</span>'
    )

    curve_svg = _svg_equity_curve(combined.get("equity_curve") or [], label="combined")

    return (
        f'<div class="scs-card scs-card-accent">'
        f'<div style="font-size:.75rem;font-weight:800;text-transform:uppercase;'
        f'letter-spacing:.06em;color:#68d391;margin-bottom:6px;">Combined Signal Simulation</div>'
        f'<div class="scs-badges">{badges}</div>'
        f'{_render_kpis(combined)}'
        f'{curve_svg}'
        f'</div>'
    )


# ---------------------------------------------------------------------------
# Per-rule cards
# ---------------------------------------------------------------------------

def _render_per_rule(scs: Dict[str, Any]) -> str:
    rules = scs.get("rule_simulations") or []
    if not rules:
        return '<div class="scs-muted">No per-rule simulation results.</div>'

    parts = [f'<div class="scs-lbl">Per-Rule Simulations ({len(rules)} rules)</div>']

    for i, r in enumerate(rules):
        rule_str = html.escape(str(r.get("rule_str", ""))[:80])
        stop     = r.get("stop_ticks", "?")
        target   = r.get("target_ticks", "?")
        rr       = r.get("rr", "?")
        wr       = r.get("win_rate")
        pf       = r.get("profit_factor")
        total_t  = r.get("total_ticks")
        dt_s     = r.get("date_start", "")
        dt_e     = r.get("date_end", "")
        n_sig    = r.get("n", 0)

        wr_cls = _wr_badge_cls(wr)
        total_cls = "scs-badge-green" if (total_t or 0) >= 0 else "scs-badge-red"

        badges = (
            f'<span class="scs-badge scs-badge-blue">S:{stop} / T:{target} (RR:{rr})</span>'
            f'<span class="scs-badge {wr_cls}">WR:{_fmt_pct(wr)}</span>'
            f'<span class="scs-badge scs-badge-yellow">PF:{_fmt(pf, 2)}</span>'
            f'<span class="scs-badge {total_cls}">{_fmt(total_t, 1)}t total</span>'
            f'<span class="scs-badge scs-badge-yellow">{n_sig} signals</span>'
        )

        date_range = (
            f'<span style="font-size:.65rem;opacity:.4;margin-left:6px">'
            f'{html.escape(dt_s)} → {html.escape(dt_e)}'
            f'</span>'
        ) if dt_s else ""

        curve_svg = _svg_equity_curve(r.get("equity_curve") or [])

        parts.append(
            f'<details style="margin:4px 0">'
            f'<summary class="scs-rule-summary">'
            f'<strong>Rule {i + 1}:</strong> {rule_str}{date_range}'
            f'<br><div class="scs-badges" style="margin-top:4px">{badges}</div>'
            f'</summary>'
            f'<div style="padding:6px 4px 4px">'
            f'{_render_kpis(r)}'
            f'{curve_svg}'
            f'</div>'
            f'</details>'
        )

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------

def _render_diagnostics(scs: Dict[str, Any]) -> str:
    diag = scs.get("diagnostics") or {}
    parts = [
        f"Signals: {diag.get('n_signals', '?')}",
        f"Rules: {diag.get('n_rules', '?')}",
        f"Simulated: {diag.get('n_rules_simulated', '?')}",
    ]
    issues = diag.get("issues") or []
    if issues:
        parts += [str(i) for i in issues]
    return f'<div style="font-size:.66rem;opacity:.4;margin-top:8px">{" · ".join(parts)}</div>'


# ---------------------------------------------------------------------------
# Public renderer
# ---------------------------------------------------------------------------

def render_strategy_discovery_signal_simulation(ctx: Dict[str, Any]) -> str:
    """
    Render signal corpus P&L simulation from __market_discovery__ package.

    Shows per-rule simulated equity curves using optimised stop/target from
    signal_exit_sweep. Combined curve shows all rules deduped by signal_id.
    """
    packages = ctx.get("packages") or {}

    parts = [_CSS, '<div class="scs-wrap">']
    found = False

    for run_id, pkg in packages.items():
        if not str(run_id).startswith("__market_discovery__"):
            continue
        meta = getattr(pkg, "metadata", None)
        if not isinstance(meta, dict):
            continue
        sd  = meta.get("derived", {}).get("strategy_discovery", {})
        scs = sd.get("signal_corpus_simulation") if isinstance(sd, dict) else None
        if scs is None:
            continue

        error = scs.get("error")
        if error:
            parts.append(
                f'<div class="scs-card" style="color:#fc8181">'
                f'Simulation error: {html.escape(str(error))}'
                f'</div>'
            )
            found = True
            break

        parts.append('<div class="scs-card">')
        parts.append(
            '<div style="font-size:.75rem;font-weight:800;text-transform:uppercase;'
            'letter-spacing:.06em;color:#68d391;margin-bottom:8px;">'
            'Signal Corpus P&amp;L Simulation'
            '<span style="font-size:.65rem;opacity:.5;font-weight:400;margin-left:8px;">'
            'corpus-only · fixed stop/target · no execution bias'
            '</span></div>'
        )
        parts.append(_render_combined(scs))
        parts.append(_render_per_rule(scs))
        parts.append(_render_diagnostics(scs))
        parts.append('</div>')
        found = True
        break

    if not found:
        parts.append(
            '<div class="scs-card scs-muted">'
            'No simulation data found. '
            'Enable <code>signal_corpus_simulation</code> and run with '
            '<code>market_discovery</code> scope.'
            '</div>'
        )

    parts.append("</div>")
    return "\n".join(parts)
