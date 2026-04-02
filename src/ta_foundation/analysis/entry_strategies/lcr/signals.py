"""
LCR signal emission and aggregate statistics.

emit_lcr_entries()         — converts LCREvent list to a signal DataFrame
compute_region_to_region_stats() — did price reach the next region after a break?
compute_retrace_stats()    — what % of breaks retrace to the zone?
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from ta_foundation.analysis.entry_strategies.lcr.regions import LCREvent, LCRRegion


# ---------------------------------------------------------------------------
# Signal emission
# ---------------------------------------------------------------------------

def emit_lcr_entries(
    events: List[LCREvent],
    bars: pd.DataFrame,
    signal_types: Optional[List[str]] = None,
    tp_ticks: float = 20.0,
    sl_ticks: float = 10.0,
    tick_size: float = 0.25,
    tick_value: float = 12.50,
    max_bars_timeout: int = 20,
) -> pd.DataFrame:
    """
    Convert LCREvent list to a trades DataFrame by simulating bar-by-bar outcomes.

    Parameters
    ----------
    events : list of LCREvent
        Produced by compute_lcr_regions_and_events().
    bars : DataFrame
        Same OHLCV bars used to generate events.
    signal_types : list of str, optional
        Which event types to trade: any subset of
        ["fresh", "touch", "break", "retrace"].
        Defaults to all four.
    tp_ticks / sl_ticks : float
        Take-profit and stop-loss in ticks.
    tick_size : float
        Points per tick.
    tick_value : float
        $ value per tick.
    max_bars_timeout : int
        Exit at next bar's close if neither TP nor SL hit within this many bars.

    Returns
    -------
    DataFrame with columns:
        entry_time, exit_time, direction, entry_price, exit_price,
        profit_ticks, profit_net, exit_reason,
        event_type, region_id, touch_count, age_bars,
        candle_size_ratio, region_size_ticks, next_region_dist_ticks
    """
    if signal_types is None:
        signal_types = ["fresh", "touch", "break", "retrace"]

    tp_pts = tp_ticks * tick_size
    sl_pts = sl_ticks * tick_size

    h = bars["high"].to_numpy(dtype=float)
    lo = bars["low"].to_numpy(dtype=float)
    c = bars["close"].to_numpy(dtype=float)
    idx = bars.index
    n = len(bars)

    records = []
    for ev in events:
        if ev.event_type not in signal_types:
            continue

        entry_bar = ev.bar_idx + 1   # enter on next bar open (lag safety)
        if entry_bar >= n:
            continue

        entry_price = c[entry_bar - 1]   # approx: prior bar close
        direction = ev.direction          # +1 long, -1 short
        tp_level = entry_price + direction * tp_pts
        sl_level = entry_price - direction * sl_pts

        exit_bar = None
        exit_price = None
        exit_reason = "timeout"

        for j in range(entry_bar, min(entry_bar + max_bars_timeout, n)):
            bar_high = h[j]
            bar_low = lo[j]

            if direction == 1:
                if bar_low <= sl_level:
                    exit_price = sl_level
                    exit_reason = "sl"
                    exit_bar = j
                    break
                if bar_high >= tp_level:
                    exit_price = tp_level
                    exit_reason = "tp"
                    exit_bar = j
                    break
            else:
                if bar_high >= sl_level:
                    exit_price = sl_level
                    exit_reason = "sl"
                    exit_bar = j
                    break
                if bar_low <= tp_level:
                    exit_price = tp_level
                    exit_reason = "tp"
                    exit_bar = j
                    break

        if exit_bar is None:
            exit_bar = min(entry_bar + max_bars_timeout - 1, n - 1)
            exit_price = c[exit_bar]

        profit_pts = direction * (exit_price - entry_price)
        profit_ticks = profit_pts / tick_size if tick_size > 0 else 0.0
        profit_net = profit_ticks * tick_value

        records.append({
            "entry_time": idx[entry_bar],
            "exit_time": idx[exit_bar],
            "direction": direction,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "profit_ticks": round(profit_ticks, 4),
            "profit_net": round(profit_net, 4),
            "exit_reason": exit_reason,
            "event_type": ev.event_type,
            "region_id": ev.region.region_id,
            "touch_count": ev.touch_count,
            "age_bars": ev.age_bars,
            "candle_size_ratio": round(ev.region.candle_size_ratio, 4),
            "region_size_ticks": round(ev.region.region_size_ticks, 4),
            "next_region_dist_ticks": round(ev.next_region_dist_ticks, 4),
        })

    if not records:
        return pd.DataFrame(columns=[
            "entry_time", "exit_time", "direction", "entry_price", "exit_price",
            "profit_ticks", "profit_net", "exit_reason",
            "event_type", "region_id", "touch_count", "age_bars",
            "candle_size_ratio", "region_size_ticks", "next_region_dist_ticks",
        ])

    return pd.DataFrame(records)


# ---------------------------------------------------------------------------
# Aggregate analysis helpers
# ---------------------------------------------------------------------------

def compute_region_to_region_stats(
    regions: List[LCRRegion],
    bars: pd.DataFrame,
    tick_size: float = 0.25,
    max_bars_forward: int = 100,
) -> Dict[str, Any]:
    """
    For each broken region, scan forward to measure whether price reached the
    next region in the break direction.

    Returns a dict with:
        n_breaks          — total broken regions
        n_reached         — breaks where price reached the next region
        reach_rate        — n_reached / n_breaks
        avg_bars_to_reach — average bars taken (for successful reaches)
        avg_dist_ticks    — average next_region_dist_ticks (for successful reaches)
        by_direction      — same stats split by direction (+1 / -1)
    """
    broken = [r for r in regions if r.broken and r.next_region_dist_ticks > 0]
    if not broken:
        return {
            "n_breaks": 0, "n_reached": 0, "reach_rate": None,
            "avg_bars_to_reach": None, "avg_dist_ticks": None,
            "by_direction": {},
        }

    h = bars["high"].to_numpy(dtype=float)
    lo = bars["low"].to_numpy(dtype=float)
    n = len(bars)

    reached_bars: List[int] = []
    reached_dists: List[float] = []
    not_reached = 0
    by_dir: Dict[int, Dict[str, Any]] = {1: {"r": 0, "nr": 0, "b": [], "d": []},
                                         -1: {"r": 0, "nr": 0, "b": [], "d": []}}

    for reg in broken:
        start = reg.broken_bar + 1
        break_dir = -reg.direction
        # price target: the far edge of the zone toward the break direction
        if break_dir == 1:
            target = reg.zone_high + reg.next_region_dist_ticks * tick_size
        else:
            target = reg.zone_low - reg.next_region_dist_ticks * tick_size

        hit = False
        for j in range(start, min(start + max_bars_forward, n)):
            if break_dir == 1 and h[j] >= target:
                hit = True
                reached_bars.append(j - start)
                reached_dists.append(reg.next_region_dist_ticks)
                by_dir[break_dir]["r"] += 1
                by_dir[break_dir]["b"].append(j - start)
                by_dir[break_dir]["d"].append(reg.next_region_dist_ticks)
                break
            if break_dir == -1 and lo[j] <= target:
                hit = True
                reached_bars.append(j - start)
                reached_dists.append(reg.next_region_dist_ticks)
                by_dir[break_dir]["r"] += 1
                by_dir[break_dir]["b"].append(j - start)
                by_dir[break_dir]["d"].append(reg.next_region_dist_ticks)
                break

        if not hit:
            not_reached += 1
            d = by_dir.get(break_dir, by_dir[1])
            d["nr"] += 1

    n_breaks = len(broken)
    n_reached = len(reached_bars)

    def _dir_summary(d: Dict) -> Dict:
        tot = d["r"] + d["nr"]
        return {
            "n": tot,
            "n_reached": d["r"],
            "reach_rate": d["r"] / tot if tot else None,
            "avg_bars": float(np.mean(d["b"])) if d["b"] else None,
            "avg_dist_ticks": float(np.mean(d["d"])) if d["d"] else None,
        }

    return {
        "n_breaks": n_breaks,
        "n_reached": n_reached,
        "reach_rate": n_reached / n_breaks if n_breaks else None,
        "avg_bars_to_reach": float(np.mean(reached_bars)) if reached_bars else None,
        "avg_dist_ticks": float(np.mean(reached_dists)) if reached_dists else None,
        "by_direction": {
            "bull_break": _dir_summary(by_dir[1]),
            "bear_break": _dir_summary(by_dir[-1]),
        },
    }


def compute_retrace_stats(
    regions: List[LCRRegion],
    events: List[LCREvent],
) -> Dict[str, Any]:
    """
    For each broken region, measure what fraction produced a retrace event.

    Returns:
        n_breaks          — total broken regions
        n_retraces        — breaks that had at least one retrace event
        retrace_rate      — n_retraces / n_breaks
        avg_age_at_break  — avg age (bars) of region at time of break
        by_direction      — same stats split by direction
    """
    broken = [r for r in regions if r.broken]
    if not broken:
        return {
            "n_breaks": 0, "n_retraces": 0, "retrace_rate": None,
            "avg_age_at_break": None, "by_direction": {},
        }

    retrace_region_ids = {ev.region.region_id for ev in events if ev.event_type == "retrace"}

    ages: List[int] = []
    by_dir: Dict[int, Dict[str, Any]] = {
        1:  {"breaks": 0, "retraces": 0},
        -1: {"breaks": 0, "retraces": 0},
    }

    for reg in broken:
        ages.append(reg.age_at_break)
        d = by_dir[reg.direction]
        d["breaks"] += 1
        if reg.region_id in retrace_region_ids:
            d["retraces"] += 1

    n_breaks = len(broken)
    n_retraces = sum(1 for r in broken if r.region_id in retrace_region_ids)

    def _dir_summary(d: Dict) -> Dict:
        b = d["breaks"]
        return {
            "n_breaks": b,
            "n_retraces": d["retraces"],
            "retrace_rate": d["retraces"] / b if b else None,
        }

    return {
        "n_breaks": n_breaks,
        "n_retraces": n_retraces,
        "retrace_rate": n_retraces / n_breaks if n_breaks else None,
        "avg_age_at_break": float(np.mean(ages)) if ages else None,
        "by_direction": {
            "bull_regions": _dir_summary(by_dir[1]),
            "bear_regions": _dir_summary(by_dir[-1]),
        },
    }
