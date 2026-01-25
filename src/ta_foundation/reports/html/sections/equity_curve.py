from __future__ import annotations

import pandas as pd
import matplotlib.pyplot as plt

from ta_foundation.reports.html.embed import fig_to_base64_png
from ta_foundation.core.model import AnalysisPackage


def render_equity_curve_all_runs(ctx: dict) -> str:
    """
    Prefers daily equity (cum_net_profit) if present; falls back to cumulative trades profit.
    Produces one combined plot for quick comparison.
    """
    packages: dict[str, AnalysisPackage] = ctx["packages"]

    fig = plt.figure()
    ax = fig.add_subplot(111)

    plotted = 0
    for run_id, pkg in packages.items():
        series = None
        x = None

        if pkg.daily is not None and len(pkg.daily) > 0:
            d = pkg.daily
            if "date" in d.columns and "cum_net_profit" in d.columns:
                x = d["date"]
                series = pd.to_numeric(d["cum_net_profit"], errors="coerce")
        if series is None and pkg.trades is not None and len(pkg.trades) > 0:
            t = pkg.trades.sort_values("exit_time") if "exit_time" in pkg.trades.columns else pkg.trades
            if "exit_time" in t.columns and "profit" in t.columns:
                x = t["exit_time"]
                series = pd.to_numeric(t["profit"], errors="coerce").cumsum()

        if series is None or x is None:
            continue

        ax.plot(x, series, label=run_id)
        plotted += 1

    ax.set_title("Equity Curves (Comparison)")
    ax.set_xlabel("Time (America/Denver)")
    ax.set_ylabel("Cumulative Net Profit")
    if plotted > 0:
        ax.legend(loc="best")

    uri = fig_to_base64_png(fig)
    if plotted == 0:
        return "<div class='muted'>No equity data available to plot.</div>"

    return f"""
      <div class="muted">Combined comparison plot. Each line is one run_id.</div>
      <img alt="Equity Curve Comparison" src="{uri}"/>
    """
