from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd
import matplotlib.pyplot as plt

from ta_foundation.analysis.drawdown import (
    compute_drawdown_curve,
    get_equity_series_from_package,
    max_drawdown_and_recovery,
)
from ta_foundation.reports.html.embed import fig_to_base64_png


def _fmt_money(x: Optional[float]) -> str:
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return "--"
    return f"${x:,.2f}"


def _fmt_dt(ts: Optional[pd.Timestamp]) -> str:
    if ts is None or (isinstance(ts, pd.Timestamp) and pd.isna(ts)):
        return "--"
    # Contract: tz-aware America/Denver on ingest
    # Display with date+time for trades-based series; daily series will be midnight.
    return ts.strftime("%Y-%m-%d %H:%M")


def _fmt_days(td: Optional[pd.Timedelta]) -> str:
    if td is None or (isinstance(td, pd.Timedelta) and pd.isna(td)):
        return "--"
    # show whole days, but keep sub-day recovery meaningful if needed
    days = td.total_seconds() / 86400.0
    if days < 2:
        hours = td.total_seconds() / 3600.0
        return f"{hours:.1f} hours"
    return f"{days:.1f} days"

def _resolve_section_options(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """
    Builder/config implementations vary. Support:
      - ctx["section_options"]
      - ctx["options"]
      - ctx["section"]["options"]
    """
    if isinstance(ctx.get("section_options"), dict):
        return ctx["section_options"] or {}
    if isinstance(ctx.get("options"), dict):
        return ctx["options"] or {}
    section = ctx.get("section")
    if isinstance(section, dict) and isinstance(section.get("options"), dict):
        return section.get("options") or {}
    return {}

def render_drawdown_curve(ctx: Dict[str, Any]) -> str:
    """
    Drawdown curve section:
      - Plot per-run drawdown curves (equity - peak) over time (<= 0)
      - Mark each run's max drawdown trough
      - Table of max drawdown + recovery duration

    ctx:
      ctx["packages"]: dict[str, AnalysisPackage]
      ctx.get("section_options", {}): optional settings
    """
    packages = ctx.get("packages", {}) or {}
    # opts = ctx.get("section_options", {}) or {}

    opts = _resolve_section_options(ctx)

    # Options (safe defaults)
    max_runs_plot = int(opts.get("max_runs_plot", 30))  # avoid unreadable plots
    show_recovery_lines = bool(opts.get("show_recovery_lines", True))
    title = opts.get("title_override", "Drawdown Curve Comparison")

    run_ids = sorted(packages.keys())
    if not run_ids:
        return "<div class='muted'>No runs found.</div>"

    series_by_run: List[Dict[str, Any]] = []
    rows: List[Dict[str, Any]] = []

    for run_id in run_ids:
        pkg = packages[run_id]
        equity = get_equity_series_from_package(pkg)
        if equity is None or equity.empty:
            rows.append(
                {
                    "run_id": run_id,
                    "status": "missing equity series",
                    "max_dd": None,
                    "peak_time": None,
                    "trough_time": None,
                    "recovery_time": None,
                    "recovery_duration": None,
                    "recovered": False,
                }
            )
            continue

        dd = compute_drawdown_curve(equity)
        info = max_drawdown_and_recovery(run_id, dd)

        if info is None:
            rows.append(
                {
                    "run_id": run_id,
                    "status": "drawdown compute failed",
                    "max_dd": None,
                    "peak_time": None,
                    "trough_time": None,
                    "recovery_time": None,
                    "recovery_duration": None,
                    "recovered": False,
                }
            )
            continue

        series_by_run.append({"run_id": run_id, "dd": dd, "info": info})
        rows.append(
            {
                "run_id": run_id,
                "status": "ok",
                "max_dd": info.max_drawdown,
                "peak_time": info.peak_time,
                "trough_time": info.trough_time,
                "recovery_time": info.recovery_time,
                "recovery_duration": info.recovery_duration,
                "recovered": info.recovered,
            }
        )

    # Plot (cap run count for readability)
    plot_items = series_by_run[:max_runs_plot]

    fig = plt.figure(figsize=(12, 5))
    ax = fig.add_subplot(1, 1, 1)

    for item in plot_items:
        run_id = item["run_id"]
        dd: pd.DataFrame = item["dd"]
        info = item["info"]

        ax.plot(dd.index, dd["drawdown"].values, label=run_id)

        # Mark max drawdown trough
        trough_t = info.trough_time
        trough_dd = -info.max_drawdown  # stored positive; plot negative
        ax.scatter([trough_t], [trough_dd])

        # Optional vertical lines: peak / trough / recovery
        if show_recovery_lines:
            ax.axvline(info.peak_time, linewidth=0.8, linestyle=":")
            ax.axvline(info.trough_time, linewidth=0.8, linestyle="--")
            if info.recovery_time is not None:
                ax.axvline(info.recovery_time, linewidth=0.8, linestyle="-.")

    ax.set_title(title)
    ax.set_ylabel("Drawdown (Equity - Peak)")
    ax.grid(True, alpha=0.3)

    # Legend can get huge; keep but allow wrapping by location
    if len(plot_items) <= 12:
        ax.legend(loc="best")
    else:
        ax.legend(loc="upper left", fontsize=8, ncol=2)

    img_uri = fig_to_base64_png(fig)
    plt.close(fig)

    # Table HTML
    table_rows_html = []
    for r in rows:
        recovered_txt = "Yes" if r["recovered"] else "No"
        status = r["status"]
        table_rows_html.append(
            "<tr>"
            f"<td><code>{r['run_id']}</code></td>"
            f"<td>{status}</td>"
            f"<td>{_fmt_money(r['max_dd'])}</td>"
            f"<td>{_fmt_dt(r['peak_time'])}</td>"
            f"<td>{_fmt_dt(r['trough_time'])}</td>"
            f"<td>{recovered_txt}</td>"
            f"<td>{_fmt_dt(r['recovery_time'])}</td>"
            f"<td>{_fmt_days(r['recovery_duration'])}</td>"
            "</tr>"
        )

    html = f"""
    <div class="section">
      <div class="card">
        <div class="card-body">
          <img alt="Drawdown Curve Comparison" style="max-width: 100%; height: auto;" src="{img_uri}" />
        </div>
      </div>

      <div class="card" style="margin-top: 12px;">
        <div class="card-body">
          <h3 style="margin-top: 0;">Max Drawdown and Recovery</h3>
          <div class="muted" style="margin-bottom: 8px;">
            Recovery time is the first timestamp where equity returns to (or exceeds) the peak immediately preceding the max drawdown trough.
          </div>
          <table class="table">
            <thead>
              <tr>
                <th>run_id</th>
                <th>status</th>
                <th>max drawdown</th>
                <th>peak time</th>
                <th>trough time</th>
                <th>recovered</th>
                <th>recovery time</th>
                <th>recovery duration</th>
              </tr>
            </thead>
            <tbody>
              {''.join(table_rows_html)}
            </tbody>
          </table>
        </div>
      </div>
    </div>
    """
    return html
