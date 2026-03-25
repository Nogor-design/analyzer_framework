from __future__ import annotations

import base64
import io
from typing import Dict, Any, Optional

import matplotlib.pyplot as plt
import pandas as pd


def plot_trade_with_ticks(
    *,
    trade_row: pd.Series,
    ticks: pd.DataFrame,
    sim_rows: pd.DataFrame,
    tick_size: float,
    title: str,
) -> str:
    """
    Returns base64 PNG string of trade debug plot.

    Plots:
      - tick last price
      - entry line
      - actual exit line
      - simulated exits (per policy)
    """

    if ticks is None or ticks.empty:
        return ""

    fig, ax = plt.subplots(figsize=(12, 6))

    # Tick path
    ax.plot(ticks["dt"], ticks["last"], label="Tick Last", linewidth=1)

    entry_price = float(trade_row["_entry_px"])
    entry_dt = trade_row["_entry_dt"]

    ax.axhline(entry_price, linestyle="--", label="Entry")

    # Actual exit
    if pd.notna(trade_row.get("_exit_px")):
        ax.axhline(float(trade_row["_exit_px"]), linestyle=":", label="Actual Exit")

    # Simulated exits
    for _, row in sim_rows.iterrows():
        if pd.isna(row.get("exit_price")):
            continue
        ax.scatter(
            row["exit_dt"],
            row["exit_price"],
            label=f"{row['policy']} ({row['exit_reason']})",
            s=50,
        )

    ax.set_title(title)
    ax.legend(loc="best")
    ax.grid(True)

    buf = io.BytesIO()
    plt.tight_layout()
    plt.savefig(buf, format="png")
    plt.close(fig)

    buf.seek(0)
    return base64.b64encode(buf.read()).decode("utf-8")
