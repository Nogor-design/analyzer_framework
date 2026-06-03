from __future__ import annotations

"""Final Template Bundle Basket.

Ranks runnable bundles that contain exactly one final template from each
time bucket. This is similar in spirit to the strategy-discovery combo basket,
but it computes directly from the final Backtest packages already ingested by
the optimizer final report.
"""

from dataclasses import dataclass
from itertools import product
import html
import re
from typing import Any

import pandas as pd

from ta_foundation.analysis.combo_selection import ComboScore, score_combo
from ta_foundation.analysis.daily_matrix import build_daily_matrix


@dataclass(frozen=True)
class BundleScore:
    combo: ComboScore
    bucket_to_run: dict[str, str]


def render_final_template_bundle_basket(ctx: dict) -> str:
    packages = {
        str(run_id): pkg
        for run_id, pkg in (ctx.get("packages") or {}).items()
        if not str(run_id).startswith("__")
    }
    options = ctx.get("options") or {}
    bucket_param = str(options.get("bucket_param") or "StartTimeH")
    top_n = int(options.get("top_n", 12))
    max_per_bucket = int(options.get("max_per_bucket", 12))
    show_chart = bool(options.get("show_chart", True))

    if len(packages) < 2:
        return _empty("Need at least two final candidate packages to build bundles.")

    groups = _group_runs_by_bucket(packages, bucket_param=bucket_param)
    groups = {bucket: runs[:max_per_bucket] for bucket, runs in groups.items() if runs}
    if len(groups) < 2:
        return _empty(
            f"Could not find at least two {html.escape(bucket_param)} buckets. "
            "Make sure final candidate Settings.csv files include the bucket parameter."
        )

    matrix = build_daily_matrix(packages)
    if matrix.pnl.empty:
        return _empty("No daily P&L matrix could be built from the final candidates.")

    ranked = _rank_one_per_bucket_bundles(matrix.pnl, matrix.traded, groups)
    if not ranked:
        return _empty("No valid one-per-bucket bundles could be scored.")

    buckets = list(groups.keys())
    total_combos = 1
    for runs in groups.values():
        total_combos *= max(1, len(runs))

    best = ranked[0]
    chart = _bundle_chart(matrix.pnl, best.combo.run_ids) if show_chart else ""

    return f"""
    <div style="padding:16px;background:#0f172a;min-height:220px;color:#cbd5e1">
      <div style="display:flex;justify-content:space-between;gap:16px;align-items:flex-start;flex-wrap:wrap;margin-bottom:14px">
        <div>
          <div style="font-size:12px;color:#94a3b8;text-transform:uppercase;letter-spacing:.08em;font-weight:700">Runnable Bundle Basket</div>
          <div style="font-size:13px;color:#e2e8f0;margin-top:4px">
            One candidate from each <b>{html.escape(bucket_param)}</b> bucket. Sorted by shared losing-day risk, then combined P&amp;L.
          </div>
        </div>
        <div style="font-size:12px;color:#94a3b8;text-align:right">
          {len(packages)} candidates &nbsp;|&nbsp; {len(buckets)} buckets &nbsp;|&nbsp; {total_combos:,} bundles scored
        </div>
      </div>

      {_coverage_table(groups)}
      {_bundle_table(ranked[:top_n], buckets)}
      {chart}

      <div style="margin-top:12px;padding:8px 12px;background:#172033;border-radius:6px;font-size:11px;color:#64748b">
        <b style="color:#94a3b8">any co-loss</b>: fraction of traded days where two or more templates in the bundle lost.
        &nbsp;|&nbsp;
        <b style="color:#94a3b8">all-loss</b>: fraction of traded days where every template in the bundle lost.
        &nbsp;|&nbsp;
        Lower shared-loss rates are better; use the bundle run IDs when exporting templates from the Decision Dashboard.
      </div>
    </div>
    """


def _rank_one_per_bucket_bundles(
    pnl: pd.DataFrame,
    traded: pd.DataFrame,
    groups: dict[str, list[str]],
) -> list[BundleScore]:
    buckets = list(groups.keys())
    out: list[BundleScore] = []
    for chosen in product(*(groups[bucket] for bucket in buckets)):
        if len(set(chosen)) != len(chosen):
            continue
        combo = score_combo(pnl, traded, tuple(chosen))
        out.append(BundleScore(
            combo=combo,
            bucket_to_run={bucket: run_id for bucket, run_id in zip(buckets, chosen)},
        ))
    out.sort(key=lambda item: (
        item.combo.any_coloss_rate,
        item.combo.all_loss_rate,
        -item.combo.combo_cum_end,
        -item.combo.traded_days,
        item.combo.run_ids,
    ))
    return out


def _group_runs_by_bucket(packages: dict[str, Any], *, bucket_param: str) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    for run_id, pkg in packages.items():
        value = _bucket_value(pkg, bucket_param=bucket_param)
        bucket = _bucket_label(bucket_param, value)
        groups.setdefault(bucket, []).append(run_id)
    return {bucket: sorted(runs, key=_run_sort_key) for bucket, runs in sorted(groups.items(), key=lambda kv: _bucket_sort_key(kv[0]))}


def _bucket_value(pkg: Any, *, bucket_param: str) -> str:
    settings = getattr(pkg, "settings", None)
    if isinstance(settings, pd.DataFrame) and not settings.empty:
        item_col = _find_col(settings.columns, "item", "name", "parameter")
        value_col = _find_col(settings.columns, "value", "setting")
        if item_col and value_col:
            mask = settings[item_col].astype(str).str.strip().str.lower().eq(bucket_param.lower())
            if mask.any():
                return str(settings.loc[mask, value_col].iloc[0]).strip()

    derived = (getattr(pkg, "metadata", {}) or {}).get("derived", {}) or {}
    for key in (bucket_param, bucket_param.lower(), _snake(bucket_param), "start_hour"):
        if key in derived and derived[key] not in (None, ""):
            return str(derived[key]).strip()

    text = " ".join(
        str(derived.get(key) or "")
        for key in ("template_path", "display_name", "display_name_spaced")
    )
    match = re.search(r"StartTimeH[_\s-]*(\d{1,2})", text, flags=re.IGNORECASE)
    if match:
        return match.group(1)
    return "unknown"


def _find_col(cols: Any, *candidates: str) -> str | None:
    norm = {_snake(str(col)): str(col) for col in cols}
    for candidate in candidates:
        key = _snake(candidate)
        if key in norm:
            return norm[key]
    return None


def _snake(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def _bucket_label(bucket_param: str, value: str) -> str:
    value = str(value or "unknown").strip()
    try:
        if bucket_param.lower() == "starttimeh":
            return f"{bucket_param}_{int(float(value)):02d}"
    except ValueError:
        pass
    return f"{bucket_param}_{value}"


def _bucket_sort_key(bucket: str) -> tuple[int, str]:
    match = re.search(r"(-?\d+(?:\.\d+)?)", bucket)
    if match:
        return (0, f"{float(match.group(1)):08.3f}")
    return (1, bucket)


def _run_sort_key(run_id: str) -> tuple[int, str]:
    match = re.search(r"(\d+)", run_id)
    return (int(match.group(1)) if match else 999999, run_id)


def _fmt(value: Any, *, pct: bool = False, dollars: bool = False, decimals: int = 2) -> str:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return "&mdash;"
    if pct:
        return f"{num * 100:.1f}%"
    if dollars:
        return f"${num:,.0f}"
    return f"{num:.{decimals}f}"


def _coverage_table(groups: dict[str, list[str]]) -> str:
    cells = []
    for bucket, runs in groups.items():
        cells.append(f"""
        <div style="background:#172033;border:1px solid #334155;border-radius:8px;padding:10px">
          <div style="font-size:11px;color:#94a3b8;text-transform:uppercase;letter-spacing:.05em">{html.escape(bucket)}</div>
          <div style="font-size:20px;font-weight:800;color:#f8fafc">{len(runs)}</div>
          <div style="font-size:11px;color:#64748b">{html.escape(", ".join(runs[:8]))}</div>
        </div>
        """)
    return f"<div style='display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px;margin-bottom:16px'>{''.join(cells)}</div>"


def _bundle_table(rows: list[BundleScore], buckets: list[str]) -> str:
    header_cells = "".join(
        f"<th style='padding:8px 10px;text-align:left;font-size:11px;color:#94a3b8;border-bottom:2px solid #334155'>{html.escape(bucket)}</th>"
        for bucket in buckets
    )
    body = []
    for idx, row in enumerate(rows, start=1):
        combo = row.combo
        bucket_cells = "".join(
            f"<td style='padding:8px 10px;border-bottom:1px solid #334155;font-size:12px;color:#e2e8f0;font-family:monospace'>{html.escape(row.bucket_to_run.get(bucket, ''))}</td>"
            for bucket in buckets
        )
        body.append(f"""
        <tr style="background:{'#1e293b' if idx % 2 else '#172033'}">
          <td style="padding:8px 10px;border-bottom:1px solid #334155;font-size:12px;color:#94a3b8">#{idx}</td>
          {bucket_cells}
          <td style="padding:8px 10px;border-bottom:1px solid #334155;font-size:13px;font-weight:800;color:{_risk_color(combo.any_coloss_rate)}">{_fmt(combo.any_coloss_rate, pct=True)}</td>
          <td style="padding:8px 10px;border-bottom:1px solid #334155;font-size:12px;color:#cbd5e1">{_fmt(combo.all_loss_rate, pct=True)}</td>
          <td style="padding:8px 10px;border-bottom:1px solid #334155;font-size:12px;color:#94a3b8">{combo.traded_days}</td>
          <td style="padding:8px 10px;border-bottom:1px solid #334155;font-size:12px;color:#94a3b8">{_fmt(combo.combo_cum_end, dollars=True)}</td>
          <td style="padding:8px 10px;border-bottom:1px solid #334155;font-size:11px;color:#64748b;font-family:monospace">{html.escape(','.join(combo.run_ids))}</td>
        </tr>
        """)
    return f"""
    <div style="overflow-x:auto">
      <table style="width:100%;border-collapse:collapse">
        <thead>
          <tr style="background:#020617">
            <th style="padding:8px 10px;text-align:left;font-size:11px;color:#94a3b8;border-bottom:2px solid #334155">Rank</th>
            {header_cells}
            <th style="padding:8px 10px;text-align:left;font-size:11px;color:#94a3b8;border-bottom:2px solid #334155">Any Co-Loss</th>
            <th style="padding:8px 10px;text-align:left;font-size:11px;color:#94a3b8;border-bottom:2px solid #334155">All-Loss</th>
            <th style="padding:8px 10px;text-align:left;font-size:11px;color:#94a3b8;border-bottom:2px solid #334155">Traded Days</th>
            <th style="padding:8px 10px;text-align:left;font-size:11px;color:#94a3b8;border-bottom:2px solid #334155">Bundle P&amp;L</th>
            <th style="padding:8px 10px;text-align:left;font-size:11px;color:#94a3b8;border-bottom:2px solid #334155">Export IDs</th>
          </tr>
        </thead>
        <tbody>{''.join(body)}</tbody>
      </table>
    </div>
    """


def _risk_color(rate: float) -> str:
    if rate < 0.15:
        return "#22c55e"
    if rate < 0.30:
        return "#f59e0b"
    return "#ef4444"


def _bundle_chart(pnl: pd.DataFrame, run_ids: tuple[str, ...]) -> str:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from ta_foundation.reports.html.embed import fig_to_base64_png

        cols = [run_id for run_id in run_ids if run_id in pnl.columns]
        if not cols:
            return ""
        cum = pnl[cols].fillna(0).cumsum()
        combined = pnl[cols].fillna(0).sum(axis=1).cumsum()
        fig, ax = plt.subplots(figsize=(10, 4), facecolor="#0f172a")
        ax.set_facecolor("#172033")
        colors = ["#38bdf8", "#34d399", "#fbbf24", "#a78bfa", "#fb7185", "#f97316"]
        for idx, run_id in enumerate(cols):
            ax.plot(cum.index, cum[run_id].values, label=run_id, color=colors[idx % len(colors)], alpha=0.7, linewidth=1.1)
        ax.plot(combined.index, combined.values, label="Bundle", color="#ffffff", linewidth=2.2, linestyle="--")
        ax.axhline(0, color="#64748b", linewidth=0.8, linestyle=":")
        ax.set_title("Best Bundle: Cumulative P&L", color="#e2e8f0", fontsize=11)
        ax.tick_params(colors="#94a3b8", labelsize=8)
        for spine in ax.spines.values():
            spine.set_color("#334155")
        ax.legend(fontsize=8, facecolor="#172033", edgecolor="#334155", labelcolor="#e2e8f0")
        uri = fig_to_base64_png(fig)
        plt.close(fig)
        return f'<img src="data:image/png;base64,{uri}" style="max-width:100%;border-radius:8px;margin-top:14px;border:1px solid #334155">'
    except Exception as exc:
        return f"<div style='color:#64748b;font-size:11px;margin-top:10px'>Chart unavailable: {html.escape(str(exc))}</div>"


def _empty(message: str) -> str:
    return f"<div style='padding:16px;color:#64748b;background:#0f172a'>{html.escape(message)}</div>"
