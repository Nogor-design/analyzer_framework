from __future__ import annotations

"""
Recipe Parameter Trend ("Next Runs") report section.

Purpose
-------
``optimization_overview`` renders one block *per optimization file*. A recipe
matrix optimizer produces hundreds of stage templates, so that section degrades
into hundreds of near-identical per-template blocks. This section does the
opposite: it **pools every swept row across all batches** and shows, for each
parameter that actually varies, how a chosen metric (default total net profit)
trends across its values.

It also flags **boundary winners** — a parameter whose best value sits at the
min or max of the swept range is evidence the search was truncated, so the box
at the top recommends the next ranges to run.

Reads only ``ctx["optimization_store"]``; no IO, analysis, or YAML parsing.
"""

import html
from typing import Any, Optional

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from ta_foundation.optimization.model import OptimizationStore
from ta_foundation.reports.html.embed import fig_to_base64_png


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _esc(s: object) -> str:
    return html.escape("" if s is None else str(s))


def _money(v: float) -> str:
    try:
        return f"${v:,.0f}"
    except (TypeError, ValueError):
        return "—"


def _is_number(v: Any) -> bool:
    if isinstance(v, bool):
        return False
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


# ---------------------------------------------------------------------------
# Aggregation per parameter
# ---------------------------------------------------------------------------

class _ParamTrend:
    """Aggregated metric-by-value for one parameter."""

    def __init__(self, name: str, df: pd.DataFrame, metric: str):
        self.name = name
        self.metric = metric
        self.numeric = False
        self.boundary: Optional[str] = None         # "lower" | "upper" | None
        self.best_value: Any = None
        self.best_median: Optional[float] = None
        self.rows: list[tuple[Any, float, float, int]] = []  # (value, median, mean, n)
        self._build(df)

    def _build(self, df: pd.DataFrame) -> None:
        sub = df[[self.name, self.metric]].copy()
        sub[self.metric] = pd.to_numeric(sub[self.metric], errors="coerce")
        sub = sub.dropna(subset=[self.metric])
        if sub.empty:
            return

        values = sub[self.name].dropna().unique().tolist()
        self.numeric = bool(values) and all(_is_number(v) for v in values)

        grouped: dict[Any, pd.Series] = {}
        for val, g in sub.groupby(self.name):
            grouped[val] = g[self.metric]

        items = list(grouped.items())
        if self.numeric:
            items.sort(key=lambda kv: float(kv[0]))
        else:
            items.sort(key=lambda kv: -float(kv[1].median()))

        for val, series in items:
            self.rows.append(
                (val, float(series.median()), float(series.mean()), int(len(series)))
            )

        if not self.rows:
            return

        # Best value by median metric (higher is better for net profit / PF).
        best_idx = max(range(len(self.rows)), key=lambda i: self.rows[i][1])
        self.best_value = self.rows[best_idx][0]
        self.best_median = self.rows[best_idx][1]

        # Boundary detection only makes sense for ordered numeric sweeps with
        # at least 3 sampled values.
        if self.numeric and len(self.rows) >= 3:
            if best_idx == 0:
                self.boundary = "lower"
            elif best_idx == len(self.rows) - 1:
                self.boundary = "upper"

    @property
    def varies(self) -> bool:
        return len(self.rows) >= 2

    def chart(self) -> str:
        fig, ax = plt.subplots(figsize=(4.2, 2.6))
        labels = [str(r[0]) for r in self.rows]
        medians = [r[1] for r in self.rows]
        colors = ["#16a34a" if m >= 0 else "#dc2626" for m in medians]
        ax.bar(range(len(labels)), medians, color=colors)
        ax.set_xticks(range(len(labels)))
        ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=7)
        ax.set_ylabel(f"median {self.metric.replace('_', ' ')}", fontsize=8)
        ax.set_title(self.name, fontsize=10, fontweight="bold")
        ax.tick_params(axis="y", labelsize=7)
        ax.axhline(0, color="#94a3b8", linewidth=0.6)
        ax.grid(axis="y", linewidth=0.3, alpha=0.5)
        return fig_to_base64_png(fig)


# ---------------------------------------------------------------------------
# Recommendation text
# ---------------------------------------------------------------------------

def _boundary_recommendation(t: _ParamTrend) -> str:
    lo = t.rows[0][0]
    hi = t.rows[-1][0]
    if t.boundary == "upper":
        return (
            f"<b>{_esc(t.name)}</b> — best median {_money(t.best_median)} at "
            f"<b>{_esc(t.best_value)}</b>, the <b>top</b> of the swept range "
            f"({_esc(lo)}–{_esc(hi)}). Likely truncated → run a stage above {_esc(hi)}."
        )
    return (
        f"<b>{_esc(t.name)}</b> — best median {_money(t.best_median)} at "
        f"<b>{_esc(t.best_value)}</b>, the <b>bottom</b> of the swept range "
        f"({_esc(lo)}–{_esc(hi)}). Likely truncated → run a stage below {_esc(lo)}."
    )


# ---------------------------------------------------------------------------
# Public renderer
# ---------------------------------------------------------------------------

def render_recipe_parameter_trend(ctx: dict) -> str:
    """Section renderer for ``recipe_parameter_trend``.

    Options (YAML ``sections[].options``):
      - ``metric``     (str, default ``total_net_profit``) — KPI to trend on.
      - ``min_trades`` (int, default 1) — drop swept rows below this trade count.
      - ``max_params`` (int, default 12) — cap on charts rendered.
    """
    store: Optional[OptimizationStore] = ctx.get("optimization_store")
    options = ctx.get("options") or {}
    metric = str(options.get("metric") or "total_net_profit")
    min_trades = int(options.get("min_trades", 1))
    max_params = int(options.get("max_params", 12))

    if store is None or store.is_empty:
        return (
            '<div style="padding:20px;background:#fff3cd;border:1px solid #ffc107;'
            'border-radius:4px;color:#856404"><strong>No optimization data found.</strong> '
            "This section pools <code>*_Optimization.csv</code> sweep rows across every "
            "stage template; import them via <code>--input</code>.</div>"
        )

    combined = store.combined_results()
    if combined is None or combined.empty or metric not in combined.columns:
        return (
            '<div style="padding:20px;background:#fff3cd;border:1px solid #ffc107;'
            f'border-radius:4px;color:#856404">No pooled rows with metric '
            f"<code>{_esc(metric)}</code>.</div>"
        )

    df = combined.copy()
    if min_trades > 0 and "total_trades" in df.columns:
        tr = pd.to_numeric(df["total_trades"], errors="coerce")
        df = df[tr.fillna(0) >= min_trades]

    n_rows = len(df)
    n_batches = len(store.batches)

    param_cols = [c for c in df.columns if c.startswith("param_")]
    trends: list[_ParamTrend] = []
    for col in param_cols:
        if df[col].dropna().nunique() < 2:
            continue  # pinned / constant — nothing to trend
        t = _ParamTrend(col[len("param_"):], df.rename(columns={col: col[len("param_"):]}), metric)
        if t.varies:
            trends.append(t)

    if not trends:
        return (
            '<div style="padding:20px;background:#e0f2fe;border:1px solid #0284c7;'
            'border-radius:4px;color:#075985">Every parameter is pinned to a single '
            "value across the pooled sweep — nothing varies to trend.</div>"
        )

    # Strongest movers first: range of median metric across values.
    trends.sort(key=lambda t: (t.rows[-1][1] if not t.numeric else 0,
                               max(r[1] for r in t.rows) - min(r[1] for r in t.rows)),
                reverse=True)
    trends = trends[:max_params]

    parts: list[str] = ['<div style="font-family:Arial,sans-serif">']

    # Header pills
    parts.append(
        '<div style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:14px">'
        f'<div style="background:#fff;border-left:4px solid #7c3aed;padding:8px 14px;border-radius:4px">'
        f'<div style="font-size:10px;color:#888;text-transform:uppercase">Pooled Rows</div>'
        f'<div style="font-size:18px;font-weight:700;color:#7c3aed">{n_rows:,}</div></div>'
        f'<div style="background:#fff;border-left:4px solid #0284c7;padding:8px 14px;border-radius:4px">'
        f'<div style="font-size:10px;color:#888;text-transform:uppercase">Stage Templates</div>'
        f'<div style="font-size:18px;font-weight:700;color:#0284c7">{n_batches:,}</div></div>'
        f'<div style="background:#fff;border-left:4px solid #475569;padding:8px 14px;border-radius:4px">'
        f'<div style="font-size:10px;color:#888;text-transform:uppercase">Metric</div>'
        f'<div style="font-size:18px;font-weight:700;color:#475569">{_esc(metric)}</div></div>'
        "</div>"
    )

    # Next-runs / boundary box
    flags = [t for t in trends if t.boundary]
    if flags:
        items = "".join(f"<li style='margin:4px 0'>{_boundary_recommendation(t)}</li>" for t in flags)
        parts.append(
            '<div style="background:#fff7ed;border:1px solid #fb923c;border-radius:6px;'
            'padding:12px 16px;margin-bottom:16px">'
            '<div style="font-weight:700;color:#9a3412;margin-bottom:6px">'
            "⚑ Suggested Next Runs (boundary winners)</div>"
            f"<ul style='margin:0;padding-left:18px;font-size:13px;color:#7c2d12'>{items}</ul></div>"
        )
    else:
        parts.append(
            '<div style="background:#ecfdf5;border:1px solid #34d399;border-radius:6px;'
            'padding:10px 16px;margin-bottom:16px;font-size:13px;color:#065f46">'
            "No boundary winners — every parameter's best value sits inside its swept "
            "range, so the search bounds look adequate.</div>"
        )

    # Chart grid
    parts.append(
        '<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:14px">'
    )
    for t in trends:
        best = (
            f'<span style="color:#16a34a;font-weight:600">best: {_esc(t.best_value)} '
            f"({_money(t.best_median)})</span>"
        )
        rng = (
            f' &nbsp;|&nbsp; range {_money(min(r[1] for r in t.rows))}–'
            f"{_money(max(r[1] for r in t.rows))}"
        )
        flag = (
            f' &nbsp;<span style="background:#fed7aa;color:#9a3412;border-radius:3px;'
            f'padding:0 5px;font-size:11px">{t.boundary} boundary</span>'
            if t.boundary else ""
        )
        parts.append(
            '<div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:6px;padding:10px">'
            f'<img src="{t.chart()}" style="width:100%;display:block;margin-bottom:6px"/>'
            f'<div style="font-size:11px;color:#475569">{best}{rng}{flag}</div></div>'
        )
    parts.append("</div>")

    parts.append("</div>")
    return "".join(parts)
