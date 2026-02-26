from __future__ import annotations

from typing import Any, Dict

import pandas as pd

from .registry import PatternTemplate, TemplateRegistry


def _orb_break_retest_detect(
    *,
    bars: pd.DataFrame,
    orb_minutes: int,
    retest_bars: int,
    direction: int,
    **kwargs,
) -> pd.Series:
    """
    Minimal ORB break + retest detector over 1m bars.
    bars expected columns: ["dt","open","high","low","close","volume","session_id","day_id"]
    Returns boolean mask aligned to bars.index (True when signal fires).
    """
    # Guard: require expected columns
    for c in ("dt", "high", "low", "close"):
        if c not in bars.columns:
            return pd.Series([False] * len(bars), index=bars.index)

    if "day_id" in bars.columns:
        g = bars.groupby("day_id", sort=False)
    else:
        g = bars.groupby(bars["dt"].dt.date, sort=False)

    # compute ORH/ORL per day_id for first orb_minutes rows of each day
    # NOTE: this assumes bars are already filtered to a session (e.g., RTH),
    # or session_id is stable per dt for grouping.
    g = bars.groupby("day_id", sort=False)

    # pre-allocate
    orh = pd.Series(index=bars.index, dtype="float64")
    orl = pd.Series(index=bars.index, dtype="float64")

    for day, idx in g.indices.items():
        day_idx = bars.index[idx]
        first_n = day_idx[:orb_minutes] if len(day_idx) >= orb_minutes else day_idx
        orh.loc[day_idx] = bars.loc[first_n, "high"].max()
        orl.loc[day_idx] = bars.loc[first_n, "low"].min()

    close = bars["close"]

    if direction > 0:
        broke = close > orh
        # retest: within retest_bars after break, close returns near orh (<= orh)
        # minimal: signal when broke now AND any of last retest_bars closes <= orh
        past_retest = (close.shift(1) <= orh.shift(1))
        for k in range(2, retest_bars + 1):
            past_retest = past_retest | (close.shift(k) <= orh.shift(k))
        return broke & past_retest.fillna(False)
    else:
        broke = close < orl
        past_retest = (close.shift(1) >= orl.shift(1))
        for k in range(2, retest_bars + 1):
            past_retest = past_retest | (close.shift(k) >= orl.shift(k))
        return broke & past_retest.fillna(False)


def register_builtin_templates(r: TemplateRegistry) -> None:
    r.register(
        PatternTemplate(
            family="ORB",
            structure="orb_break_retest",
            detect_fn=_orb_break_retest_detect,
            requires_ticks=False,
        )
    )


def default_template_registry() -> TemplateRegistry:
    r = TemplateRegistry()
    register_builtin_templates(r)
    return r