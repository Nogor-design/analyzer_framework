from __future__ import annotations

"""
Optimization Overview report section.

Renders a rich HTML summary for all OptimizationBatch objects held in the
OptimizationStore.  The section is self-contained: it reads only from
``ctx["optimization_store"]`` and never performs IO, analysis, or YAML parsing.
"""

import html
from typing import Any, Optional

import pandas as pd

from ta_foundation.optimization.model import OptimizationStore, OptimizationBatch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _esc(s: object) -> str:
    return html.escape("" if s is None else str(s))


def _fmt_num(v: Any, decimals: int = 2) -> str:
    if v is None:
        return "—"
    try:
        f = float(v)
        if decimals == 0:
            return f"{int(f):,}"
        return f"{f:,.{decimals}f}"
    except (TypeError, ValueError):
        return str(v)


def _profit_color(v: Any, column: str) -> str:
    """Return a light background color appropriate for the value and column."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "#f5f5f5"

    if column == "profit_factor":
        if f >= 2.0:  return "#c6efce"
        if f >= 1.5:  return "#e2efda"
        if f >= 1.0:  return "#ffeb9c"
        return "#ffc7ce"
    if column == "total_net_profit":
        if f > 0:  return "#e2efda"
        if f < 0:  return "#ffc7ce"
        return "#ffeb9c"
    if column == "max_drawdown":
        if f == 0:  return "#c6efce"
        if f < 500: return "#ffeb9c"
        return "#ffc7ce"
    return "#f5f5f5"


_TH = ('style="background:#1e2a3a;color:#fff;padding:8px 12px;'
       'text-align:left;border:1px solid #2d3f55;font-size:12px"')
_TD = 'style="padding:6px 10px;border:1px solid #ddd;font-size:12px"'


def _th(text: str) -> str:
    return f"<th {_TH}>{_esc(text)}</th>"


def _td(text: str, bg: str = "#fff") -> str:
    style = f'style="padding:6px 10px;border:1px solid #ddd;font-size:12px;background:{bg}"'
    return f"<td {style}>{_esc(text)}</td>"


def _table(headers: list[str], rows: list[list[str]], col_colors: Optional[dict[int, str]] = None) -> str:
    col_colors = col_colors or {}
    hdr = "".join(_th(h) for h in headers)
    body_rows = []
    for row in rows:
        cells = "".join(
            _td(cell, col_colors.get(i, "#fff"))
            for i, cell in enumerate(row)
        )
        body_rows.append(f"<tr>{cells}</tr>")
    return (
        '<div style="overflow-x:auto">'
        '<table style="border-collapse:collapse;width:100%;margin-bottom:16px">'
        f"<thead><tr>{hdr}</tr></thead>"
        f"<tbody>{''.join(body_rows)}</tbody>"
        "</table></div>"
    )


def _stat_pill(label: str, value: str, accent: str = "#3b82f6") -> str:
    return (
        f'<div style="background:#fff;border-left:4px solid {accent};'
        f'padding:8px 14px;border-radius:4px;min-width:90px;display:inline-block;'
        f'margin:4px 6px 4px 0">'
        f'<div style="font-size:10px;color:#888;text-transform:uppercase;letter-spacing:.5px">{_esc(label)}</div>'
        f'<div style="font-size:18px;font-weight:700;color:{accent}">{_esc(value)}</div>'
        f"</div>"
    )


# ---------------------------------------------------------------------------
# Per-batch renderers
# ---------------------------------------------------------------------------

def _render_file_summary(batch: OptimizationBatch) -> str:
    parts = [
        f'<div style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:12px">',
        _stat_pill("Source File",    batch.source_path.name,      "#475569"),
        _stat_pill("Instrument",     batch.instrument or "—",      "#0284c7"),
        _stat_pill("Total Rows",     str(batch.row_count),         "#7c3aed"),
        _stat_pill("Parsed OK",      str(batch.successfully_parsed_rows), "#16a34a"),
        _stat_pill("Parse Failures", str(batch.failed_rows),       "#dc2626" if batch.failed_rows else "#9ca3af"),
        _stat_pill("Parameters",     str(len(batch.parameter_names)), "#b45309"),
        "</div>",
    ]

    if batch.parameter_names:
        pills = " ".join(
            f'<span style="display:inline-block;background:#e0e7ff;color:#3730a3;'
            f'border-radius:3px;padding:2px 7px;font-size:11px;margin:2px">'
            f"{_esc(n)}</span>"
            for n in batch.parameter_names
        )
        parts.append(
            f'<div style="margin-bottom:12px">'
            f'<div style="font-size:11px;color:#888;margin-bottom:4px">DETECTED PARAMETERS</div>'
            f"{pills}</div>"
        )

    return "\n".join(parts)


def _render_performance_stats(batch: OptimizationBatch) -> str:
    """Summary statistics table for each numeric metric."""
    df = batch.results
    if df is None or df.empty:
        return '<p style="color:#888">No results data.</p>'

    metrics = [c for c in batch.metric_columns if c in df.columns]
    if not metrics:
        return '<p style="color:#888">No metric columns available.</p>'

    headers = ["Metric", "Min", "Max", "Mean", "Median", "Count"]
    rows = []
    for col in metrics:
        s = pd.to_numeric(df[col], errors="coerce").dropna()
        if s.empty:
            continue
        rows.append([
            col.replace("_", " ").title(),
            _fmt_num(s.min()),
            _fmt_num(s.max()),
            _fmt_num(s.mean()),
            _fmt_num(s.median()),
            str(len(s)),
        ])

    if not rows:
        return '<p style="color:#888">Metric columns contain no parseable numeric data.</p>'

    return (
        '<h4 style="color:#374151;margin:12px 0 6px">Performance Statistics</h4>'
        + _table(headers, rows)
    )


def _top_n_table(
    batch: OptimizationBatch,
    sort_col: str,
    label: str,
    ascending: bool = False,
    top_n: int = 10,
    guard_col: Optional[str] = None,
    guard_min: int = 1,
) -> str:
    """Render a top-N leaderboard for one metric."""
    df = batch.results
    if df is None or df.empty or sort_col not in df.columns:
        return ""

    working = df.copy()
    working[sort_col] = pd.to_numeric(working[sort_col], errors="coerce")
    if guard_col and guard_col in working.columns:
        working[guard_col] = pd.to_numeric(working[guard_col], errors="coerce")
        working = working[working[guard_col] >= guard_min]

    working = working.dropna(subset=[sort_col])
    if working.empty:
        return ""

    top = working.sort_values(sort_col, ascending=ascending).head(top_n)

    # Show metric cols + key param cols (skip raw_parameters, flags, source_file)
    skip = {"raw_parameters", "parse_ok", "parse_warnings", "source_file", "batch_id"}
    param_cols = [c for c in df.columns if c.startswith("param_")][:8]
    display_cols = [c for c in batch.metric_columns if c in top.columns] + param_cols

    headers = ["Rank"] + [c.replace("_", " ").title() for c in display_cols]
    rows = []
    for rank, (_, row) in enumerate(top.iterrows(), 1):
        cells: list[str] = [str(rank)]
        for col in display_cols:
            cells.append(str(row.get(col, "—")))
        rows.append(cells)

    # Color sort column
    try:
        sort_idx = display_cols.index(sort_col) + 1  # +1 for Rank column
        col_colors = {sort_idx: _profit_color(top[sort_col].iloc[0], sort_col)}
    except ValueError:
        col_colors = {}

    return (
        f'<h4 style="color:#374151;margin:16px 0 6px">{_esc(label)}</h4>'
        + _table(headers, rows, col_colors)
    )


def _render_top_combinations(batch: OptimizationBatch, options: dict) -> str:
    top_n = int(options.get("top_n", 10))
    trade_guard = int(options.get("min_trades", 1))

    parts = ['<h4 style="color:#374151;margin:12px 0 6px">Top Combinations</h4>']

    leaderboards = [
        ("total_net_profit", f"Top {top_n} by Net Profit",      False, None,          0),
        ("profit_factor",    f"Top {top_n} by Profit Factor",   False, "total_trades", trade_guard),
        ("max_drawdown",     f"Top {top_n} by Lowest Drawdown", True,  "total_trades", trade_guard),
    ]

    for sort_col, label, asc, guard, gmin in leaderboards:
        tbl = _top_n_table(
            batch, sort_col, label, ascending=asc,
            top_n=top_n, guard_col=guard, guard_min=gmin,
        )
        if tbl:
            parts.append(tbl)

    return "\n".join(parts) if len(parts) > 1 else '<p style="color:#888">Not enough data for leaderboards.</p>'


def _render_parameter_overview(batch: OptimizationBatch, options: dict) -> str:
    """For every parsed parameter: value distribution + relationship to top results."""
    df = batch.results
    if df is None or df.empty:
        return '<p style="color:#888">No data for parameter analysis.</p>'

    param_cols = [c for c in df.columns if c.startswith("param_")]
    if not param_cols:
        return '<p style="color:#888">No param_* columns found (Parameters column may have failed to parse).</p>'

    top_n = int(options.get("top_n", 10))
    has_pf = "profit_factor" in df.columns

    parts = ['<h4 style="color:#374151;margin:12px 0 6px">Parameter Analysis</h4>']
    parts.append(
        '<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:12px;margin-bottom:16px">'
    )

    # Identify top-N rows by profit_factor for "top representation" analysis
    top_ids: set = set()
    if has_pf and top_n > 0:
        pf_series = pd.to_numeric(df["profit_factor"], errors="coerce")
        top_ids = set(pf_series.nlargest(top_n).index)

    for col in param_cols:
        name = col[len("param_"):]  # strip "param_" prefix for display
        series = df[col].dropna()
        if series.empty:
            continue

        unique_vals = series.unique()
        is_numeric = all(
            isinstance(v, (int, float)) and not isinstance(v, bool)
            for v in unique_vals[:20]
        )

        card = [
            f'<div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:6px;padding:10px">',
            f'<div style="font-weight:600;color:#1e2a3a;font-size:13px;margin-bottom:6px">{_esc(name)}</div>',
        ]

        if is_numeric and len(unique_vals) > 6:
            # Numeric — show min/max/mean
            numeric = pd.to_numeric(series, errors="coerce").dropna()
            card.append(
                f'<div style="font-size:11px;color:#555">'
                f'Range: {_fmt_num(numeric.min())} – {_fmt_num(numeric.max())}'
                f' &nbsp;|&nbsp; Mean: {_fmt_num(numeric.mean())}</div>'
            )
            if has_pf and top_ids:
                top_series = pd.to_numeric(df.loc[df.index.isin(top_ids), col], errors="coerce").dropna()
                if not top_series.empty:
                    card.append(
                        f'<div style="font-size:11px;color:#16a34a;margin-top:3px">'
                        f'In top-{top_n}: {_fmt_num(top_series.mean())} (mean)</div>'
                    )
        else:
            # Categorical — show value counts
            vc = series.value_counts().head(8)
            rows_html = []
            total = len(series)
            for val, cnt in vc.items():
                pct = 100.0 * cnt / total if total > 0 else 0.0
                # How often this value appears in top-N rows
                top_cnt = 0
                if has_pf and top_ids:
                    top_cnt = int((df.loc[df.index.isin(top_ids), col] == val).sum())
                top_badge = (
                    f' <span style="background:#dcfce7;color:#16a34a;border-radius:3px;'
                    f'padding:0 4px;font-size:10px">top:{top_cnt}</span>'
                    if top_ids else ""
                )
                rows_html.append(
                    f'<div style="display:flex;justify-content:space-between;'
                    f'font-size:11px;padding:1px 0">'
                    f'<span style="color:#374151">{_esc(str(val))}{top_badge}</span>'
                    f'<span style="color:#888">{cnt} ({pct:.0f}%)</span>'
                    f"</div>"
                )
            card.extend(rows_html)

        card.append("</div>")
        parts.append("\n".join(card))

    parts.append("</div>")
    return "\n".join(parts)


def _render_parse_failures(batch: OptimizationBatch) -> str:
    """Show rows that failed to parse, if any."""
    df = batch.results
    if df is None or df.empty or "parse_ok" not in df.columns:
        return ""
    bad = df[~df["parse_ok"].astype(bool)]
    if bad.empty:
        return ""

    rows = []
    for _, row in bad.head(20).iterrows():
        warn = str(row.get("parse_warnings", ""))
        raw  = str(row.get("raw_parameters", ""))[:80]
        rows.append([warn, raw + ("…" if len(str(row.get("raw_parameters", ""))) > 80 else "")])

    return (
        f'<details style="margin-top:12px">'
        f'<summary style="cursor:pointer;color:#dc2626;font-size:13px">'
        f'{len(bad)} rows failed to parse (click to expand)</summary>'
        + _table(["Warning", "Raw Parameters (truncated)"], rows)
        + "</details>"
    )


def _render_batch(batch: OptimizationBatch, options: dict) -> str:
    title = _esc(f"{batch.batch_id}  ·  {batch.source_path.name}")

    sections = [
        "<details open>",
        f'<summary style="cursor:pointer;font-size:15px;font-weight:700;'
        f'color:#1e2a3a;padding:8px 0">{title}</summary>',
        '<div style="padding:12px 4px">',
        _render_file_summary(batch),
        _render_performance_stats(batch),
        _render_top_combinations(batch, options),
        _render_parameter_overview(batch, options),
        _render_parse_failures(batch),
        "</div></details>",
        '<hr style="border:none;border-top:1px solid #e2e8f0;margin:16px 0">',
    ]
    return "\n".join(s for s in sections if s)


# ---------------------------------------------------------------------------
# Multi-file aggregate view
# ---------------------------------------------------------------------------

def _render_aggregate_summary(store: OptimizationStore) -> str:
    if len(store.batches) < 2:
        return ""

    combined = store.combined_results()
    if combined is None or combined.empty:
        return ""

    # Per-batch stats
    rows = []
    for bid, batch in store.batches.items():
        df = batch.results
        pf_vals = pd.to_numeric(df.get("profit_factor", pd.Series()), errors="coerce").dropna() \
            if df is not None and "profit_factor" in df.columns else pd.Series()
        rows.append([
            bid,
            batch.instrument or "—",
            str(batch.row_count),
            str(batch.successfully_parsed_rows),
            _fmt_num(pf_vals.max()) if not pf_vals.empty else "—",
            _fmt_num(pf_vals.mean()) if not pf_vals.empty else "—",
        ])

    tbl = _table(
        ["Batch", "Instrument", "Rows", "Parsed OK", "Best PF", "Avg PF"],
        rows,
    )

    return (
        '<h3 style="color:#1e2a3a;border-bottom:2px solid #3b82f6;padding-bottom:6px;margin-bottom:12px">'
        "Multi-File Aggregate Summary</h3>"
        + tbl
    )


# ---------------------------------------------------------------------------
# Public render function
# ---------------------------------------------------------------------------

def render_optimization_overview(ctx: dict) -> str:
    """
    Section renderer for ``optimization_overview``.

    Reads:
      - ``ctx["optimization_store"]`` — :class:`OptimizationStore` or None

    Options (from YAML ``sections[].options``):
      - ``top_n``      (int, default 10) — number of top combinations to show
      - ``min_trades`` (int, default 1)  — guard for PF/drawdown leaderboards
    """
    opt_store: Optional[OptimizationStore] = ctx.get("optimization_store")
    options = ctx.get("options") or {}

    if opt_store is None or opt_store.is_empty:
        return (
            '<div style="padding:20px;background:#fff3cd;border:1px solid #ffc107;'
            'border-radius:4px;color:#856404">'
            "<strong>No optimization data found.</strong> "
            "Import one or more <code>*_Optimization.csv</code> NinjaTrader exports "
            "alongside your backtest files and they will appear here."
            "</div>"
        )

    n_batches = len(opt_store.batches)
    total_rows = sum(b.row_count for b in opt_store.batches.values())

    parts = [
        f'<div style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:16px">',
        _stat_pill("Optimization Files",    str(n_batches),   "#7c3aed"),
        _stat_pill("Total Combinations",    f"{total_rows:,}", "#0284c7"),
        "</div>",
        _render_aggregate_summary(opt_store),
    ]

    for batch in opt_store.batches.values():
        parts.append(_render_batch(batch, options))

    return f'<div style="font-family:Arial,sans-serif">{"".join(parts)}</div>'
