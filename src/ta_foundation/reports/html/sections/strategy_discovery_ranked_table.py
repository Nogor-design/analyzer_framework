from __future__ import annotations

"""
Strategy Discovery — Ranked Table
===================================
Pure HTML renderer for the cross-run ranking produced by ranking.py.

Reads from pkg.metadata["derived"]["strategy_discovery"]["cross_run_ranking"]
(attached to the first package by the orchestrator) and
pkg.metadata["derived"]["strategy_discovery"]["ranking"] (per-run scores).

Shows:
  1. Summary banner (n runs, n passed, n high-confidence, sensitivity badge)
  2. Ranked table with sortable columns and color-coded scores
  3. Weight sensitivity details (collapsed)
  4. Component breakdown accordion per run
"""

import html
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _fmt_num(v: Any, decimals: int = 2, default: str = "—") -> str:
    if v is None:
        return default
    try:
        return f"{float(v):.{decimals}f}"
    except Exception:
        return default


def _fmt_pct(v: Any, decimals: int = 1, default: str = "—") -> str:
    if v is None:
        return default
    try:
        return f"{float(v) * 100:.{decimals}f}%"
    except Exception:
        return default


def _fmt_usd(v: Any, default: str = "—") -> str:
    if v is None:
        return default
    try:
        return f"${float(v):,.0f}"
    except Exception:
        return default


def _score_color(score: Optional[float]) -> str:
    if score is None:
        return "#888"
    if score >= 75:
        return "#2ecc71"
    if score >= 55:
        return "#f39c12"
    if score >= 35:
        return "#e67e22"
    return "#e74c3c"


def _grade_color(grade: str) -> str:
    return {"A": "#2ecc71", "B": "#27ae60", "C": "#f39c12", "D": "#e67e22", "F": "#e74c3c"}.get(grade, "#888")


def _tier_badge(tier: str) -> str:
    colors = {"high": "#2ecc71", "moderate": "#f39c12", "low": "#e67e22", "rejected": "#e74c3c"}
    color = colors.get(tier, "#888")
    return f'<span style="color:{color};font-weight:700;font-size:11px">{html.escape(tier.upper())}</span>'


def _pf_color(pf: Optional[float]) -> str:
    if pf is None:
        return "#888"
    if pf >= 1.5:
        return "#2ecc71"
    if pf >= 1.0:
        return "#f39c12"
    return "#e74c3c"


_CSS = """
<style>
.sdr-wrap { display:flex; flex-direction:column; gap:14px; }
.sdr-banner { display:flex; flex-wrap:wrap; gap:12px; padding:12px 16px;
  background:rgba(255,255,255,0.04); border-radius:12px;
  border:1px solid rgba(255,255,255,0.08); align-items:center; }
.sdr-stat { text-align:center; min-width:90px; }
.sdr-stat-val { font-size:1.5rem; font-weight:900; line-height:1; }
.sdr-stat-lbl { font-size:0.75rem; opacity:0.65; margin-top:3px; }
.sdr-card { background:rgba(255,255,255,0.035); border-radius:12px;
  padding:12px 14px; border:1px solid rgba(255,255,255,0.06); }
.sdr-table { border-collapse:collapse; width:100%; font-size:0.88rem; }
.sdr-table th { background:rgba(255,255,255,0.06); padding:7px 10px;
  text-align:left; font-weight:600; white-space:nowrap; border-bottom:1px solid rgba(255,255,255,0.1); }
.sdr-table td { padding:7px 10px; border-bottom:1px solid rgba(255,255,255,0.04); vertical-align:middle; }
.sdr-table tr:last-child td { border-bottom:none; }
.sdr-table tr:hover td { background:rgba(255,255,255,0.03); }
.sdr-score { font-size:1.1rem; font-weight:900; }
.sdr-grade { display:inline-block; width:22px; height:22px; border-radius:50%;
  text-align:center; line-height:22px; font-weight:900; font-size:0.8rem; }
.sdr-details { margin-top:8px; }
.sdr-details > summary { cursor:pointer; padding:4px 0; opacity:0.8;
  font-size:0.85rem; user-select:none; }
.sdr-details > summary:hover { opacity:1; }
.sdr-comp-grid { display:grid; grid-template-columns: repeat(auto-fill, minmax(160px,1fr));
  gap:6px; margin-top:8px; }
.sdr-comp { background:rgba(255,255,255,0.04); border-radius:8px; padding:6px 10px; }
.sdr-comp-lbl { font-size:0.72rem; opacity:0.65; }
.sdr-comp-val { font-size:0.95rem; font-weight:700; }
.sdr-stable { color:#2ecc71; font-weight:700; }
.sdr-fragile { color:#e74c3c; font-weight:700; }
.sdr-muted { opacity:0.65; font-size:0.85rem; }
.sdr-section-lbl { font-size:0.75rem; font-weight:600; text-transform:uppercase;
  letter-spacing:0.05em; opacity:0.5; margin:10px 0 5px; }
</style>
"""


# ---------------------------------------------------------------------------
# Sub-renderers
# ---------------------------------------------------------------------------

def _render_banner(cross_run: Dict[str, Any]) -> str:
    diag = cross_run.get("diagnostics") or {}
    sensitivity = cross_run.get("sensitivity") or {}
    clustering  = cross_run.get("clustering") or {}
    cdiag       = clustering.get("diagnostics") or {}

    n_runs    = diag.get("n_runs", 0)
    n_passed  = diag.get("n_passed_validation", 0)
    n_high    = diag.get("n_high_confidence", 0)
    n_clusters = cdiag.get("n_clusters")

    if sensitivity.get("ranking_stable"):
        sens_html = '<span class="sdr-stable">&#9650; Stable</span>'
    elif sensitivity.get("ranking_fragile"):
        n_fragile = len(sensitivity.get("fragile_runs") or [])
        sens_html = f'<span class="sdr-fragile">&#9660; Fragile ({n_fragile} run{"s" if n_fragile != 1 else ""})</span>'
    else:
        sens_html = '<span class="sdr-muted">N/A</span>'

    stats = [
        (str(n_runs), "Runs Ranked"),
        (str(n_passed), "Passed Validation"),
        (str(n_high), "High Confidence"),
        (str(n_clusters) if n_clusters is not None else "—", "Behaviour Clusters"),
        (sens_html, "Sensitivity (±20%)"),
    ]
    stat_items = "".join(
        f'<div class="sdr-stat"><div class="sdr-stat-val">{v}</div>'
        f'<div class="sdr-stat-lbl">{html.escape(label)}</div></div>'
        for v, label in stats
    )
    return f'<div class="sdr-banner">{stat_items}</div>'


def _cluster_badge(row: Dict[str, Any]) -> str:
    """Small badge showing cluster membership. ★ = representative."""
    cid   = row.get("cluster_id")
    csize = row.get("cluster_size", 1)
    is_rep = row.get("is_cluster_representative", True)
    if cid is None:
        return "—"
    star = "&#9733;" if is_rep else "&#9702;"
    color = "#f1c40f" if is_rep else "#888"
    tip = "Cluster representative" if is_rep else f"Cluster #{cid} member"
    return (
        f'<span title="{tip}" style="color:{color};font-size:11px;white-space:nowrap">'
        f'{star} C{cid}'
        f'{"" if csize <= 1 else f" <span style=\'opacity:.6\'>({csize})</span>"}'
        f'</span>'
    )


def _render_ranked_table(ranked: List[Dict[str, Any]]) -> str:
    if not ranked:
        return '<div class="sdr-muted">No ranking data available.</div>'

    rows = ""
    for row in ranked:
        rank = row.get("rank", "—")
        run_id = str(row.get("run_id", "—"))
        final  = row.get("final_score")
        qual   = row.get("quality_score")
        dep    = row.get("deploy_score")
        grade  = str(row.get("grade", "—"))
        tier   = str(row.get("confidence_tier", "—"))
        passed = row.get("validation_passed", False)
        pf     = row.get("profit_factor")
        wr     = row.get("win_rate")
        n_tr   = row.get("n_trades")
        net_p  = row.get("net_profit")
        oos_d  = row.get("oos_degradation")
        mc_pct = row.get("mc_dd_percentile")
        p_val  = row.get("t_test_p_value")

        final_color = _score_color(final)
        grade_color = _grade_color(grade)
        pass_badge  = (
            '<span style="color:#2ecc71;font-size:10px">&#10004; PASS</span>'
            if passed else
            '<span style="color:#e74c3c;font-size:10px">&#10008; FAIL</span>'
        )
        clust_badge = _cluster_badge(row)

        # Dim non-representative rows slightly to de-emphasise duplicates
        row_style = "" if row.get("is_cluster_representative", True) else "opacity:0.7"

        rows += f"""
        <tr style="{row_style}">
          <td style="font-weight:900;text-align:center">{rank}</td>
          <td style="font-size:11px;max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"
              title="{html.escape(run_id)}">{html.escape(run_id[:40])}</td>
          <td style="text-align:center">
            <span class="sdr-score" style="color:{final_color}">{_fmt_num(final, 1)}</span>
          </td>
          <td style="text-align:center">
            <span class="sdr-grade" style="background:{grade_color};color:#fff">{html.escape(grade)}</span>
          </td>
          <td style="text-align:center">{_tier_badge(tier)}</td>
          <td style="text-align:center">{pass_badge}</td>
          <td style="text-align:center;color:{_score_color(qual)};font-weight:700">{_fmt_num(qual, 1)}</td>
          <td style="text-align:center;color:{_score_color(dep)};font-weight:700">{_fmt_num(dep, 1)}</td>
          <td style="color:{_pf_color(pf)};font-weight:700">{_fmt_num(pf)}</td>
          <td>{_fmt_pct(wr)}</td>
          <td>{n_tr if n_tr is not None else "—"}</td>
          <td>{_fmt_usd(net_p)}</td>
          <td>{_fmt_pct(oos_d) if oos_d is not None else "—"}</td>
          <td style="font-size:11px">{_fmt_num(mc_pct, 0) if mc_pct is not None else "—"}</td>
          <td style="font-size:11px">{_fmt_num(p_val, 3) if p_val is not None else "—"}</td>
          <td style="text-align:center">{clust_badge}</td>
        </tr>"""

    return f"""
    <div class="sdr-section-lbl">Ranked Strategies</div>
    <div style="overflow-x:auto">
    <table class="sdr-table">
      <thead><tr>
        <th>#</th><th>Run ID</th><th>Final ▼</th><th>Grade</th><th>Tier</th>
        <th>Val.</th><th>Quality</th><th>Deploy</th>
        <th>PF</th><th>WR</th><th>Trades</th><th>Net P&amp;L</th>
        <th>OOS Deg.</th><th>MC%ile</th><th>p-value</th><th>Cluster</th>
      </tr></thead>
      <tbody>{rows}</tbody>
    </table>
    </div>"""


def _render_sensitivity_details(sensitivity: Dict[str, Any]) -> str:
    if not sensitivity or sensitivity.get("note"):
        note = sensitivity.get("note", "Not available.")
        return f'<div class="sdr-muted">{html.escape(str(note))}</div>'

    stable   = sensitivity.get("ranking_stable", True)
    fragile  = sensitivity.get("fragile_runs") or []
    details  = sensitivity.get("perturbation_details") or []
    pct      = sensitivity.get("perturbation_pct", 20)

    if stable:
        header = f'<div class="sdr-stable">&#9650; Rankings are stable under ±{pct:.0f}% weight perturbation</div>'
    else:
        header = (
            f'<div class="sdr-fragile">&#9660; Rankings are fragile — '
            f'{len(fragile)} run{"s" if len(fragile) != 1 else ""} '
            f'changed top-5 position under ±{pct:.0f}% weight perturbation'
            f'</div>'
        )

    if not details:
        return header

    detail_rows = "".join(
        f'<tr><td>{html.escape(str(d.get("weight","")))} {html.escape(str(d.get("direction","")))} </td>'
        f'<td style="color:#e74c3c">{", ".join(html.escape(r) for r in (d.get("moved_out_of_top5") or []))}</td>'
        f'<td style="color:#2ecc71">{", ".join(html.escape(r) for r in (d.get("moved_into_top5") or []))}</td>'
        f'</tr>'
        for d in details[:20]
    )
    table = f"""
    <table class="sdr-table" style="margin-top:8px;font-size:11px">
      <thead><tr><th>Weight / Direction</th><th>Moved OUT of top-5</th><th>Moved INTO top-5</th></tr></thead>
      <tbody>{detail_rows}</tbody>
    </table>"""

    return header + table


def _render_cluster_map(clustering: Dict[str, Any]) -> str:
    """Collapsible cluster map showing groups of behaviorally similar strategies."""
    if not clustering:
        return '<div class="sdr-muted">No clustering data available.</div>'

    cdiag      = clustering.get("diagnostics") or {}
    cluster_map = clustering.get("cluster_map") or []

    if not cluster_map:
        return '<div class="sdr-muted">No clusters formed.</div>'

    n_clusters  = cdiag.get("n_clusters", len(cluster_map))
    n_multi     = cdiag.get("n_multi_run_clusters", 0)
    threshold   = cdiag.get("threshold", "—")
    largest     = cdiag.get("largest_cluster_size", "—")
    note        = cdiag.get("note", "")

    summary_line = (
        f'{n_clusters} cluster{"s" if n_clusters != 1 else ""}'
        f' &nbsp;·&nbsp; {n_multi} multi-member'
        f' &nbsp;·&nbsp; largest: {largest}'
        f' &nbsp;·&nbsp; threshold: {threshold:.2f}' if isinstance(threshold, float)
        else f' &nbsp;·&nbsp; threshold: {threshold}'
    )
    if note:
        summary_line += f' &nbsp;·&nbsp; <em>{html.escape(str(note))}</em>'

    cards = ""
    for cluster in cluster_map:
        cid      = cluster.get("cluster_id", "?")
        rep      = cluster.get("representative_run_id", "")
        members  = cluster.get("members") or []
        csize    = cluster.get("cluster_size", len(members))

        if csize == 1:
            # Singleton — just a small pill
            cards += (
                f'<div style="display:inline-block;margin:3px 4px;padding:3px 8px;'
                f'border-radius:12px;background:rgba(255,255,255,0.04);'
                f'border:1px solid rgba(255,255,255,0.08);font-size:11px;opacity:.7">'
                f'C{cid}: {html.escape(str(rep)[:35])}'
                f'</div>'
            )
            continue

        member_rows = "".join(
            f'<div style="padding:2px 0;font-size:11px;'
            f'{"font-weight:700;color:#f1c40f" if m == rep else "opacity:.75"}">'
            f'{"&#9733; " if m == rep else "&nbsp;&nbsp; "}'
            f'{html.escape(str(m)[:60])}'
            f'</div>'
            for m in members
        )
        cards += f"""
        <div style="background:rgba(255,255,255,0.04);border-radius:8px;padding:8px 12px;
                    border:1px solid rgba(255,255,255,0.08);margin:4px 0">
          <div style="font-size:12px;font-weight:700;margin-bottom:4px">
            Cluster {cid}
            <span style="font-size:10px;opacity:.6;font-weight:400;margin-left:6px">{csize} members</span>
          </div>
          {member_rows}
        </div>"""

    return f"""
    <div style="margin-bottom:6px;font-size:12px;opacity:.7">{summary_line}</div>
    <div style="display:flex;flex-wrap:wrap;gap:4px;margin-bottom:8px">
    </div>
    {cards}
    <div style="font-size:11px;opacity:.5;margin-top:6px">
      &#9733; = cluster representative (highest-ranked member).
      Non-representative rows are dimmed in the table above.
    </div>"""


def _render_component_breakdown(ranked: List[Dict[str, Any]]) -> str:
    if not ranked:
        return ""

    component_labels = {
        "risk_adj":          "Risk-Adj",
        "stability":         "Stability",
        "robustness":        "Robustness",
        "regime_fit":        "Regime Fit",
        "execution_fit":     "Exec Fit",
        "automatability":    "Automatability",
        "simplicity":        "Simplicity",
        "operational_risk":  "Op. Risk",
        "monitoring_ease":   "Monitoring",
        "overfit_penalty":   "Overfit Penalty",
        "complexity_penalty":"Complexity Penalty",
    }

    cards = ""
    for row in ranked[:15]:  # cap at 15 runs to keep the section manageable
        run_id = str(row.get("run_id", ""))
        comps  = row.get("components") or {}
        items  = ""
        for key, label in component_labels.items():
            val = comps.get(key)
            if val is not None:
                if key in ("overfit_penalty", "complexity_penalty"):
                    color = "#e74c3c" if val > 0 else "#888"
                else:
                    color = _score_color(val)
                items += (
                    f'<div class="sdr-comp">'
                    f'<div class="sdr-comp-lbl">{html.escape(label)}</div>'
                    f'<div class="sdr-comp-val" style="color:{color}">'
                    f'{_fmt_num(val, 1)}'
                    f'</div></div>'
                )
        cards += f"""
        <details class="sdr-details" style="margin-top:6px">
          <summary>#{row.get("rank","?")} — {html.escape(run_id[:60])}</summary>
          <div class="sdr-comp-grid" style="margin-top:6px">{items}</div>
        </details>"""

    return f"""
    <div class="sdr-section-lbl">Component Breakdown</div>
    {cards}"""


# ---------------------------------------------------------------------------
# Public renderer
# ---------------------------------------------------------------------------

def render_strategy_discovery_ranked_table(ctx: Dict[str, Any]) -> str:
    packages = ctx.get("packages") or {}
    options  = ctx.get("options") or {}

    show_sensitivity  = bool(options.get("show_sensitivity", True))
    show_components   = bool(options.get("show_components", True))
    show_clusters     = bool(options.get("show_clusters", True))

    # Find cross_run_ranking — attached to first package by orchestrator
    cross_run: Optional[Dict[str, Any]] = None
    ranked: List[Dict[str, Any]] = []

    for run_id, pkg in packages.items():
        if str(run_id).startswith("__"):
            continue
        meta = getattr(pkg, "metadata", None)
        if not isinstance(meta, dict):
            continue
        sd = meta.get("derived", {}).get("strategy_discovery", {})
        if not isinstance(sd, dict):
            continue
        if "cross_run_ranking" in sd:
            cross_run = sd["cross_run_ranking"]
            break

    if cross_run is None:
        return (
            _CSS
            + "<div class='sdr-wrap'><div class='sdr-card sdr-muted'>"
            + "No cross-run ranking data found. Ensure strategy_discovery is enabled "
            + "and ranking ran without errors.</div></div>"
        )

    ranked = cross_run.get("ranked") or []
    sensitivity = cross_run.get("sensitivity") or {}

    parts = [_CSS, "<div class='sdr-wrap'>"]

    # Banner
    parts.append(f"<div class='sdr-card'>{_render_banner(cross_run)}</div>")

    # Ranked table
    parts.append(f"<div class='sdr-card'>{_render_ranked_table(ranked)}</div>")

    # Weight sensitivity
    if show_sensitivity:
        sens_html = _render_sensitivity_details(sensitivity)
        parts.append(f"""
        <div class="sdr-card">
          <details class="sdr-details" open>
            <summary><b>Ranking Weight Sensitivity (±20%)</b></summary>
            <div style="margin-top:8px">{sens_html}</div>
          </details>
        </div>""")

    # Cluster map
    clustering = cross_run.get("clustering") or {}
    if show_clusters and clustering:
        cluster_html = _render_cluster_map(clustering)
        parts.append(f"""
        <div class="sdr-card">
          <details class="sdr-details">
            <summary><b>Behaviour Clusters</b></summary>
            <div style="margin-top:8px">{cluster_html}</div>
          </details>
        </div>""")

    # Component breakdown
    if show_components and ranked:
        parts.append(f"<div class='sdr-card'>{_render_component_breakdown(ranked)}</div>")

    parts.append("</div>")
    return "\n".join(parts)
