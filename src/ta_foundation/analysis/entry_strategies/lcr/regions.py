"""
Large Candle Region (LCR) detection and event scanning.

A region is created when a candle's body >= size_multiplier * rolling_mean(body, lookback).
The region extends as a horizontal zone (like an FVG) with either body or range boundaries.

Stateful forward scan tracks:
  fresh   — region just formed
  touch   — intact region is re-entered then closes back outside (bounce signal)
  break   — price closes beyond the zone's far boundary (continuation signal)
  retrace — price re-enters zone within N bars after a break (test of broken level)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional, Tuple

import numpy as np
import pandas as pd


@dataclass
class LCRRegion:
    region_id: int
    creation_bar: int          # iloc index in bars DataFrame
    creation_dt: Any
    direction: int             # +1 = bull candle, -1 = bear candle
    zone_high: float
    zone_low: float
    candle_size_ratio: float   # body / rolling_avg at creation
    atr_at_creation: float
    region_size: float = 0.0        # zone_high - zone_low in points
    region_size_ticks: float = 0.0  # region_size / tick_size
    touch_count: int = 0
    broken: bool = False
    broken_bar: int = -1
    broken_dt: Any = None
    age_at_break: int = -1
    # set post-creation when next region in break direction is found
    next_region_dist_ticks: float = 0.0


@dataclass
class LCREvent:
    event_type: str       # "fresh" | "touch" | "break" | "retrace"
    bar_idx: int          # iloc index of the triggering bar
    dt: Any               # timestamp of triggering bar
    region: LCRRegion
    direction: int        # trade direction: +1 long, -1 short
    touch_count: int = 0
    age_bars: int = 0
    # distance to nearest region in the break direction (break/retrace only)
    next_region_dist_ticks: float = 0.0


def _compute_atr(bars: pd.DataFrame, period: int = 14) -> pd.Series:
    high = bars["high"]
    low = bars["low"]
    prev_close = bars["close"].shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return tr.rolling(period, min_periods=1).mean()


def compute_lcr_regions_and_events(
    bars: pd.DataFrame,
    size_multiplier: float = 1.5,
    lookback: int = 20,
    zone_type: str = "body",        # "body" | "range"
    tick_size: float = 0.25,
    retrace_window: int = 20,       # bars after break to watch for retrace
    atr_period: int = 14,
    min_region_size_ticks: float = 0.0,
    max_active_regions: int = 100,  # cap to keep inner loop O(1) on long datasets
    max_region_age: int = 500,      # evict regions older than this (bars)
) -> Tuple[List[LCRRegion], List[LCREvent]]:
    """
    Single-pass stateful scan that produces LCRRegion objects and LCREvent list.

    Parameters
    ----------
    bars : DataFrame
        OHLCV with DatetimeIndex (tz-aware).  Must have open/high/low/close columns.
    size_multiplier : float
        Body must be >= size_multiplier * rolling_mean(body, lookback) to create a region.
    lookback : int
        Rolling window for the mean body size comparison.
    zone_type : str
        "body" — zone = (min(open,close), max(open,close))
        "range" — zone = (low, high)
    tick_size : float
        Instrument tick size for tick-distance calculations.
    retrace_window : int
        Number of bars after a break to watch for retrace back into the zone.
    atr_period : int
        ATR period used to normalise sizes.
    min_region_size_ticks : float
        Skip regions smaller than this (filters tiny floating-point zones).

    Returns
    -------
    regions : List[LCRRegion]
    events  : List[LCREvent]
    """
    if bars.empty:
        return [], []

    o = bars["open"].to_numpy(dtype=float)
    h = bars["high"].to_numpy(dtype=float)
    lo = bars["low"].to_numpy(dtype=float)
    c = bars["close"].to_numpy(dtype=float)
    n = len(bars)

    body = np.abs(c - o)
    rolling_body = pd.Series(body).rolling(lookback, min_periods=1).mean().to_numpy()
    atr = _compute_atr(bars, atr_period).to_numpy()
    # Use dt column for timestamps when available (bars may have integer RangeIndex
    # after reset_index); fall back to index only when dt column is absent.
    idx = bars["dt"].values if "dt" in bars.columns else bars.index

    regions: List[LCRRegion] = []
    events: List[LCREvent] = []
    active_regions: List[LCRRegion] = []   # not yet broken
    # Track broken regions awaiting retrace: {region_id: break_bar}
    retrace_watch: dict = {}
    region_counter = 0

    for i in range(lookback, n):
        bar_open = o[i]
        bar_high = h[i]
        bar_low = lo[i]
        bar_close = c[i]
        bar_body = body[i]
        rolling_avg = rolling_body[i]
        bar_atr = atr[i]
        bar_dt = idx[i]

        # --- 0. Evict stale active regions to keep inner loop bounded ---
        if active_regions:
            # Remove regions that are too old (price has long moved away)
            active_regions = [r for r in active_regions if i - r.creation_bar <= max_region_age]
            # If still too many, keep only the most recent max_active_regions
            if len(active_regions) > max_active_regions:
                active_regions = active_regions[-max_active_regions:]

        # --- 1. Check retrace events for broken regions ---
        expired = []
        for rid, (brk_bar, brk_region) in list(retrace_watch.items()):
            if i - brk_bar > retrace_window:
                expired.append(rid)
                continue
            # retrace = price re-enters the zone
            in_zone = bar_low <= brk_region.zone_high and bar_high >= brk_region.zone_low
            if in_zone:
                age = i - brk_region.creation_bar
                events.append(LCREvent(
                    event_type="retrace",
                    bar_idx=i,
                    dt=bar_dt,
                    region=brk_region,
                    direction=-brk_region.direction,  # fade the original direction
                    age_bars=age,
                    next_region_dist_ticks=brk_region.next_region_dist_ticks,
                ))
                expired.append(rid)
        for rid in expired:
            retrace_watch.pop(rid, None)

        # --- 2. Check active (intact) regions for touch / break ---
        still_active = []
        for region in active_regions:
            zh = region.zone_high
            zl = region.zone_low
            age = i - region.creation_bar

            # Break: close beyond the far side of the zone
            if region.direction == 1:
                # Bull region — break = close below zone_low
                broke = bar_close < zl
            else:
                # Bear region — break = close above zone_high
                broke = bar_close > zh

            if broke:
                region.broken = True
                region.broken_bar = i
                region.broken_dt = bar_dt
                region.age_at_break = age

                # Find nearest region in the break direction for region-to-region stat
                break_dir = -region.direction
                nearest_dist = _nearest_region_dist(region, active_regions, break_dir, tick_size)
                region.next_region_dist_ticks = nearest_dist

                events.append(LCREvent(
                    event_type="break",
                    bar_idx=i,
                    dt=bar_dt,
                    region=region,
                    direction=break_dir,
                    age_bars=age,
                    next_region_dist_ticks=nearest_dist,
                ))
                retrace_watch[region.region_id] = (i, region)
                # do NOT add to still_active
                continue

            # Touch: bar overlaps zone but closes back outside (intact bounce)
            in_zone = bar_low <= zh and bar_high >= zl
            if in_zone:
                # Closes back outside?
                closed_outside = (
                    (region.direction == 1 and bar_close > zh)
                    or (region.direction == -1 and bar_close < zl)
                )
                if closed_outside:
                    region.touch_count += 1
                    events.append(LCREvent(
                        event_type="touch",
                        bar_idx=i,
                        dt=bar_dt,
                        region=region,
                        direction=region.direction,
                        touch_count=region.touch_count,
                        age_bars=age,
                    ))

            still_active.append(region)

        active_regions = still_active

        # --- 3. Check if this bar creates a new region ---
        if rolling_avg > 0 and bar_body >= size_multiplier * rolling_avg:
            direction = 1 if bar_close >= bar_open else -1

            if zone_type == "range":
                zh = bar_high
                zl = bar_low
            else:  # "body"
                zh = max(bar_open, bar_close)
                zl = min(bar_open, bar_close)

            region_size = zh - zl
            region_size_ticks = region_size / tick_size if tick_size > 0 else 0.0

            if region_size_ticks < min_region_size_ticks:
                continue

            region = LCRRegion(
                region_id=region_counter,
                creation_bar=i,
                creation_dt=bar_dt,
                direction=direction,
                zone_high=zh,
                zone_low=zl,
                candle_size_ratio=bar_body / rolling_avg,
                atr_at_creation=bar_atr,
                region_size=region_size,
                region_size_ticks=region_size_ticks,
            )
            region_counter += 1
            regions.append(region)
            active_regions.append(region)

            events.append(LCREvent(
                event_type="fresh",
                bar_idx=i,
                dt=bar_dt,
                region=region,
                direction=direction,
                age_bars=0,
            ))

    return regions, events


def _nearest_region_dist(
    broken_region: LCRRegion,
    active_regions: List[LCRRegion],
    break_direction: int,
    tick_size: float,
) -> float:
    """Return tick distance to the nearest active region in the break direction."""
    best = float("inf")
    ref = broken_region.zone_low if break_direction == -1 else broken_region.zone_high

    for r in active_regions:
        if r.region_id == broken_region.region_id:
            continue
        if break_direction == -1:
            # looking below
            if r.zone_high < ref:
                dist = (ref - r.zone_high) / tick_size if tick_size > 0 else 0.0
                best = min(best, dist)
        else:
            # looking above
            if r.zone_low > ref:
                dist = (r.zone_low - ref) / tick_size if tick_size > 0 else 0.0
                best = min(best, dist)

    return best if best != float("inf") else 0.0
