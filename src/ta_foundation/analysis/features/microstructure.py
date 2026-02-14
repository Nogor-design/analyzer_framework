from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

import pandas as pd


@dataclass(frozen=True)
class MicroWindow:
    seconds_before: int = 5
    seconds_after: int = 5


def slice_ticks_window(
    ticks: pd.DataFrame,
    center: pd.Timestamp,
    w: MicroWindow,
) -> pd.DataFrame:
    if ticks is None or ticks.empty:
        return ticks
    if center is None or pd.isna(center):
        return ticks.iloc[0:0]

    start = center - pd.Timedelta(seconds=int(w.seconds_before))
    end = center + pd.Timedelta(seconds=int(w.seconds_after))
    return ticks[(ticks["dt"] >= start) & (ticks["dt"] <= end)]


def micro_features_for_time(
    ticks: pd.DataFrame,
    center: pd.Timestamp,
    w: MicroWindow,
) -> dict:
    """
    Basic microstructure features around a timestamp.
    Requires ticks cols: dt, bid, ask
    """
    out = {
        "micro_ticks": 0,
        "micro_spread_median": None,
        "micro_spread_p90": None,
        "micro_ticks_per_sec": None,
    }
    if ticks is None or ticks.empty or center is None or pd.isna(center):
        return out

    win = slice_ticks_window(ticks, center, w)
    if win.empty:
        return out

    if "bid" in win.columns and "ask" in win.columns:
        spread = pd.to_numeric(win["ask"], errors="coerce") - pd.to_numeric(win["bid"], errors="coerce")
        spread = spread.dropna()
        if not spread.empty:
            out["micro_spread_median"] = float(spread.median())
            out["micro_spread_p90"] = float(spread.quantile(0.90))

    out["micro_ticks"] = int(len(win))
    dur = (win["dt"].max() - win["dt"].min()).total_seconds()
    if dur and dur > 0:
        out["micro_ticks_per_sec"] = float(len(win) / dur)

    return out
