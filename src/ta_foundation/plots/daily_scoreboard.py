from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple

import pandas as pd
import matplotlib.pyplot as plt

from ta_foundation.reports.html.embed import fig_to_base64_png
from ta_foundation.analysis.daily_matrix import DailyMatrix


@dataclass(frozen=True)
class ScoreboardFigures:
    dot_scoreboard: plt.Figure
    combined_daily_pnl: plt.Figure
    equity_curves: plt.Figure
    active_count: plt.Figure


def _outcome_color(pnl: float, traded: bool) -> str:
    if not traded:
        return "0.7"  # gray
    if pnl > 0:
        return "g"
    if pnl < 0:
        return "r"
    return "0.4"  # flat day => darker gray


def make_dot_scoreboard(dm: DailyMatrix, max_runs: Optional[int] = None) -> plt.Figure:
    """
    Dot matrix:
      x = date
      y = run_id
      color = win/loss/not traded
    """
    runs = list(dm.pnl.columns)
    if max_runs is not None:
        runs = runs[:max_runs]

    fig = plt.figure(figsize=(max(10, len(dm.dates) * 0.35), max(4, len(runs) * 0.5)))
    ax = fig.add_subplot(1, 1, 1)

    # Scatter per run
    for yi, run_id in enumerate(runs):
        y = yi
        pnl_s = dm.pnl[run_id].reindex(dm.dates)
        trd_s = dm.traded[run_id].reindex(dm.dates).fillna(False)

        xs = []
        ys = []
        cs = []
        for i, dt in enumerate(dm.dates):
            pnl = pnl_s.iloc[i]
            traded = bool(trd_s.iloc[i])
            if pd.isna(pnl):
                pnl_val = 0.0
            else:
                pnl_val = float(pnl)
            xs.append(dt)
            ys.append(y)
            cs.append(_outcome_color(pnl_val, traded))

        ax.scatter(xs, ys, s=60, c=cs, marker="o")

    ax.set_yticks(range(len(runs)))
    ax.set_yticklabels(runs)
    ax.set_xlabel("Date")
    ax.set_title("Daily Win/Loss Scoreboard (Green=Win, Red=Loss, Gray=No Trade/Flat)")
    ax.grid(True, axis="x", alpha=0.2)

    fig.autofmt_xdate(rotation=45)
    fig.tight_layout()
    return fig


def make_combined_daily_pnl(dm: DailyMatrix) -> plt.Figure:
    fig = plt.figure(figsize=(12, 4))
    ax = fig.add_subplot(1, 1, 1)
    ax.bar(dm.dates, dm.combined_pnl.values)
    ax.set_title("Combined Daily Net PnL (All Bots Summed)")
    ax.set_xlabel("Date")
    ax.set_ylabel("Net PnL")
    ax.grid(True, axis="y", alpha=0.2)
    fig.autofmt_xdate(rotation=45)
    fig.tight_layout()
    return fig


def make_equity_curves(dm: DailyMatrix, show_individual: bool = True) -> plt.Figure:
    fig = plt.figure(figsize=(12, 5))
    ax = fig.add_subplot(1, 1, 1)

    if show_individual:
        for run_id in dm.cum.columns:
            ax.plot(dm.dates, dm.cum[run_id].values, linewidth=1.5, label=run_id)

    ax.plot(dm.dates, dm.combined_cum.values, linewidth=3.0, label="COMBINED")
    ax.set_title("Running Net PnL (Equity Curves)")
    ax.set_xlabel("Date")
    ax.set_ylabel("Cumulative Net PnL")
    ax.grid(True, alpha=0.2)

    # Legend can get big; keep it but let matplotlib handle layout.
    ax.legend(loc="best", fontsize=9)
    fig.autofmt_xdate(rotation=45)
    fig.tight_layout()
    return fig


def make_active_count(dm: DailyMatrix) -> plt.Figure:
    fig = plt.figure(figsize=(12, 3.5))
    ax = fig.add_subplot(1, 1, 1)
    ax.plot(dm.dates, dm.active_count.values, linewidth=2.0)
    ax.set_title("Bots Active Per Day (# traded)")
    ax.set_xlabel("Date")
    ax.set_ylabel("Active bots")
    ax.grid(True, alpha=0.2)
    fig.autofmt_xdate(rotation=45)
    fig.tight_layout()
    return fig


def build_scoreboard_figures(dm: DailyMatrix) -> ScoreboardFigures:
    return ScoreboardFigures(
        dot_scoreboard=make_dot_scoreboard(dm),
        combined_daily_pnl=make_combined_daily_pnl(dm),
        equity_curves=make_equity_curves(dm),
        active_count=make_active_count(dm),
    )


def figures_to_embedded_html(figs: ScoreboardFigures, title: str = "Daily Scoreboard") -> str:
    """
    Self-contained HTML (base64 embedded images) compatible with your HTML contract.
    """
    dot_b64 = fig_to_base64_png(figs.dot_scoreboard)
    pnl_b64 = fig_to_base64_png(figs.combined_daily_pnl)
    eq_b64 = fig_to_base64_png(figs.equity_curves)
    act_b64 = fig_to_base64_png(figs.active_count)

    # Close figures to avoid memory build-up in batch runs
    plt.close(figs.dot_scoreboard)
    plt.close(figs.combined_daily_pnl)
    plt.close(figs.equity_curves)
    plt.close(figs.active_count)

    def img(b64: str, caption: str) -> str:
        return f"""
        <div style="margin: 18px 0;">
          <div style="font-weight:600; margin-bottom:8px;">{caption}</div>
          <img src="data:image/png;base64,{b64}" style="max-width:100%; height:auto; border:1px solid #ddd; border-radius:10px;" />
        </div>
        """

    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>{title}</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>
    body {{ font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif; margin: 24px; color: #111; }}
    .wrap {{ max-width: 1200px; margin: 0 auto; }}
    .sub {{ color: #444; margin-top: 6px; }}
    .card {{ background: #fff; border: 1px solid #e6e6e6; border-radius: 14px; padding: 18px; box-shadow: 0 1px 3px rgba(0,0,0,.04); }}
  </style>
</head>
<body>
  <div class="wrap">
    <h1 style="margin:0;">{title}</h1>
    <div class="sub">Green = win day, Red = loss day, Gray = no trade (or flat)</div>
    <div class="card" style="margin-top: 16px;">
      {img(dot_b64, "1) Per-bot daily outcomes")}
      {img(pnl_b64, "2) Combined daily PnL (sum of all bots)")}
      {img(eq_b64, "3) Running PnL (each bot + combined)")}
      {img(act_b64, "4) Participation (bots active per day)")}
    </div>
  </div>
</body>
</html>
"""
