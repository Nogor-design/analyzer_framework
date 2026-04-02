from __future__ import annotations

"""
Strategy Discovery — Combo Basket
====================================
Pure HTML renderer for portfolio combo selection results.

Reads ``pkg.assets["strategy_discovery"]["combo_basket"]`` which is written
by the combo selection step in strategy_discovery/orchestrator.py.

Renders:
  1. Top k=2 pairs sorted by co-loss rate (table)
  2. Top k=3 trios sorted by co-loss rate (table)
  3. Combined vs individual cumulative P&L chart for the best k=2 combo

Section options (ctx["options"]):
  top_n_k2     int   max k=2 combos to show (default 5)
  top_n_k3     int   max k=3 combos to show (default 3)
  show_chart   bool  show cumulative P&L chart (default true)
"""

import html
from typing import Any, Dict, List, Optional


def _fmt(v: Any, decimals: int = 3, pct: bool = False, dollar: bool = False, default: str = "—") -> str:
    if v is None:
        return default
    try:
        f = float(v)
        if pct:
            return f"{f * 100:.1f}%"
        if dollar:
            return f"${f:,.0f}"
        return f"{f:.{decimals}f}"
    except Exception:
        return str(v) if v != "" else default


def _coloss_color(rate: float) -> str:
    """Traffic-light color for co-loss rate."""
    if rate < 0.15:
        return "#22c55e"
    if rate < 0.30:
        return "#f59e0b"
    return "#ef4444"


def _combo_table(combos: List[Dict[str, Any]], title: str, max_rows: int) -> str:
    if not combos:
        return f"<p style='color:#64748b;font-size:12px'>{html.escape(title)}: no results.</p>"

    rows_html = []
    for i, c in enumerate(combos[:max_rows]):
        run_ids = c.get("run_ids") or []
        run_ids_str = html.escape(", ".join(str(r) for r in run_ids))
        any_cl = c.get("any_coloss_rate")
        all_l = c.get("all_loss_rate")
        traded = c.get("traded_days")
        cum = c.get("combo_cum_end")

        any_cl_f = float(any_cl) if any_cl is not None else None
        cl_color = _coloss_color(any_cl_f) if any_cl_f is not None else "#94a3b8"
        bg = "#1e293b" if i % 2 == 0 else "#172033"

        rows_html.append(f"""
        <tr style="background:{bg}">
          <td style="padding:8px 10px;border-bottom:1px solid #334155;font-size:12px;color:#e2e8f0">{run_ids_str}</td>
          <td style="padding:8px 10px;border-bottom:1px solid #334155;font-size:13px;font-weight:700;color:{cl_color}">{_fmt(any_cl, pct=True)}</td>
          <td style="padding:8px 10px;border-bottom:1px solid #334155;font-size:12px;color:#cbd5e1">{_fmt(all_l, pct=True)}</td>
          <td style="padding:8px 10px;border-bottom:1px solid #334155;font-size:12px;color:#94a3b8">{_fmt(traded, 0)}</td>
          <td style="padding:8px 10px;border-bottom:1px solid #334155;font-size:12px;color:#94a3b8">{_fmt(cum, dollar=True)}</td>
        </tr>""")

    return f"""
    <div style="margin-bottom:20px">
      <h4 style="color:#94a3b8;font-size:13px;margin:0 0 8px;text-transform:uppercase;letter-spacing:.05em">{html.escape(title)}</h4>
      <div style="overflow-x:auto">
        <table style="width:100%;border-collapse:collapse;font-family:monospace">
          <thead>
            <tr style="background:#0f172a">
              <th style="padding:8px 10px;text-align:left;font-size:11px;color:#94a3b8;border-bottom:2px solid #334155">Run IDs</th>
              <th style="padding:8px 10px;text-align:left;font-size:11px;color:#94a3b8;border-bottom:2px solid #334155">Any Co-Loss</th>
              <th style="padding:8px 10px;text-align:left;font-size:11px;color:#94a3b8;border-bottom:2px solid #334155">All-Loss</th>
              <th style="padding:8px 10px;text-align:left;font-size:11px;color:#94a3b8;border-bottom:2px solid #334155">Traded Days</th>
              <th style="padding:8px 10px;text-align:left;font-size:11px;color:#94a3b8;border-bottom:2px solid #334155">Combo Cum P&L</th>
            </tr>
          </thead>
          <tbody>{"".join(rows_html)}</tbody>
        </table>
      </div>
    </div>"""


def _cum_pnl_chart(matrix, best_k2_run_ids: List[str]) -> str:
    """Render a base64 PNG of combined vs individual cumulative P&L."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np
        from ta_foundation.reports.html.embed import fig_to_base64_png

        pnl_df = matrix.pnl
        cum_df = pnl_df.fillna(0).cumsum()

        fig, ax = plt.subplots(figsize=(10, 4))
        fig.patch.set_facecolor("#0f172a")
        ax.set_facecolor("#1e293b")

        colors = ["#3b82f6", "#10b981", "#f59e0b", "#8b5cf6", "#ef4444"]

        # Individual lines for the combo members
        for i, rid in enumerate(best_k2_run_ids):
            if rid in cum_df.columns:
                ax.plot(
                    cum_df.index,
                    cum_df[rid].values,
                    label=html.unescape(str(rid)),
                    color=colors[i % len(colors)],
                    alpha=0.7,
                    linewidth=1.2,
                )

        # Combined cumulative P&L
        if best_k2_run_ids:
                combo_cols = [r for r in best_k2_run_ids if r in pnl_df.columns]
                if combo_cols:
                    combined = pnl_df[combo_cols].fillna(0).sum(axis=1).cumsum()
                    ax.plot(
                        combined.index,
                        combined.values,
                        label="Combined",
                        color="#ffffff",
                        linewidth=2.0,
                        linestyle="--",
                    )

        ax.axhline(0, color="#475569", linewidth=0.8, linestyle=":")
        ax.set_title("Best k=2 Combo: Cumulative P&L", color="#e2e8f0", fontsize=11)
        ax.tick_params(colors="#94a3b8", labelsize=9)
        for spine in ax.spines.values():
            spine.set_color("#334155")
        ax.legend(fontsize=9, facecolor="#1e293b", edgecolor="#334155", labelcolor="#e2e8f0")

        img_b64 = fig_to_base64_png(fig)
        plt.close(fig)

        return f'<img src="data:image/png;base64,{img_b64}" style="max-width:100%;border-radius:6px;margin-top:12px">'
    except Exception as exc:
        return f"<p style='color:#64748b;font-size:11px'>Chart unavailable: {html.escape(str(exc))}</p>"


def render_strategy_discovery_combo_basket(ctx: dict) -> str:
    packages = ctx.get("packages") or {}
    options = ctx.get("options") or {}
    diag = ctx.get("pipeline_diagnostics") or {}

    top_n_k2 = int(options.get("top_n_k2", 5))
    top_n_k3 = int(options.get("top_n_k3", 3))
    show_chart = bool(options.get("show_chart", True))

    # Find the first package with a combo_basket
    basket_data: Optional[Dict[str, Any]] = None
    matrix = None
    for run_id, pkg in packages.items():
        if str(run_id).startswith("__"):
            continue
        pkg_assets = getattr(pkg, "assets", {}) or {}
        sd_assets = pkg_assets.get("strategy_discovery") or {}
        basket = sd_assets.get("combo_basket") if isinstance(sd_assets, dict) else None
        if basket is not None:
            basket_data = basket
            matrix = basket.get("matrix")
            break

    if basket_data is None:
        missing_keys = []
        for run_id, pkg in packages.items():
            if not str(run_id).startswith("__"):
                pkg_assets = getattr(pkg, "assets", {}) or {}
                missing_keys = list(pkg_assets.keys())
                break
        return (
            f"<div style='padding:16px;color:#64748b'>"
            f"No combo basket found. Strategy discovery must run on 2+ packages first. "
            f"(Available asset keys: {html.escape(str(missing_keys))})</div>"
        )

    summary = basket_data.get("summary") or {}
    k2_combos = summary.get("k2") or []
    k3_combos = summary.get("k3") or []

    best_k2_run_ids: List[str] = []
    if k2_combos:
        best_k2_run_ids = list(k2_combos[0].get("run_ids") or [])

    chart_html = ""
    if show_chart and matrix is not None and best_k2_run_ids:
        chart_html = _cum_pnl_chart(matrix, best_k2_run_ids)

    n_runs = len([k for k in packages if not str(k).startswith("__")])
    note = (
        f"{n_runs} strategies evaluated &nbsp;|&nbsp; "
        f"{len(k2_combos)} k=2 combos &nbsp;|&nbsp; "
        f"{len(k3_combos)} k=3 combos"
    )

    return f"""
    <div style="padding:16px;background:#0f172a;min-height:200px">
      <p style="color:#94a3b8;font-size:12px;margin:0 0 16px">{html.escape(note)}</p>

      {_combo_table(k2_combos, "Best Pairs (k=2) — lowest co-loss day rate", top_n_k2)}
      {_combo_table(k3_combos, "Best Trios (k=3) — lowest co-loss day rate", top_n_k3)}

      {chart_html}

      <div style="margin-top:12px;padding:8px 12px;background:#172033;border-radius:6px;font-size:11px;color:#64748b">
        <b style="color:#94a3b8">any_coloss_rate</b>: fraction of traded days where ≥2 strategies in the combo lose.
        &nbsp;|&nbsp;
        <b style="color:#94a3b8">all_loss_rate</b>: fraction of traded days where all strategies lose.
        &nbsp;|&nbsp;
        Lower is better. Sort priority: any_coloss_rate → all_loss_rate → cum P&L.
      </div>
    </div>"""
