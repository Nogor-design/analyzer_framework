"""
LCR (Large Candle Region) sweep orchestrator.

Sweeps parameter combinations over a bar dataset, simulates trade outcomes for
each LCR signal type, and returns ranked results with IS/OOS validation.
"""

from __future__ import annotations

import itertools
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from ta_foundation.analysis.entry_strategies.lcr.regions import compute_lcr_regions_and_events
from ta_foundation.analysis.entry_strategies.lcr.signals import (
    compute_region_to_region_stats,
    compute_retrace_stats,
    emit_lcr_entries,
)
from ta_foundation.analysis.entry_strategies.validation import compute_is_oos_degradation


def run_lcr_discovery(
    bars_1m: pd.DataFrame,
    config: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Entry point called by cli/main.py.

    Parameters
    ----------
    bars_1m : DataFrame
        Minute OHLCV with tz-aware DatetimeIndex (America/Denver).
    config : dict
        Section from report YAML, e.g.::

            lcr_discovery:
              size_multipliers: [1.5, 2.0]
              lookbacks: [10, 20]
              zone_types: ["body", "range"]
              tp_ticks: [20, 30]
              sl_ticks: [10, 15]
              tick_size: 0.25
              tick_value: 12.50
              max_bars_timeout: 20
              retrace_window: 20
              atr_period: 14
              signal_types: ["fresh", "touch", "break", "retrace"]
              min_trades: 20

    Returns
    -------
    dict with keys:
        n_combinations_run, n_results, results, r2r_stats, retrace_stats, config
    """
    size_multipliers = config.get("size_multipliers", [1.5, 2.0])
    lookbacks = config.get("lookbacks", [10, 20])
    zone_types = config.get("zone_types", ["body"])
    tp_ticks_list = config.get("tp_ticks", [20])
    sl_ticks_list = config.get("sl_ticks", [10])
    tick_size = float(config.get("tick_size", 0.25))
    tick_value = float(config.get("tick_value", 12.50))
    max_bars_timeout = int(config.get("max_bars_timeout", 20))
    retrace_window = int(config.get("retrace_window", 20))
    atr_period = int(config.get("atr_period", 14))
    signal_types = config.get("signal_types", ["fresh", "touch", "break", "retrace"])
    min_trades = int(config.get("min_trades", 20))
    max_active_regions = int(config.get("max_active_regions", 100))
    max_region_age = int(config.get("max_region_age", 500))

    combos = list(itertools.product(
        size_multipliers, lookbacks, zone_types, tp_ticks_list, sl_ticks_list,
    ))

    results: List[Dict[str, Any]] = []
    global_r2r: Optional[Dict] = None
    global_retrace: Optional[Dict] = None

    for mult, lb, zt, tp, sl in combos:
        regions, events = compute_lcr_regions_and_events(
            bars_1m,
            size_multiplier=mult,
            lookback=lb,
            zone_type=zt,
            tick_size=tick_size,
            retrace_window=retrace_window,
            atr_period=atr_period,
            max_active_regions=max_active_regions,
            max_region_age=max_region_age,
        )

        # Compute aggregate stats once per region set (not per TP/SL combo)
        if global_r2r is None:
            global_r2r = compute_region_to_region_stats(regions, bars_1m, tick_size=tick_size)
            global_retrace = compute_retrace_stats(regions, events)

        for sig_type in signal_types:
            trades = emit_lcr_entries(
                events,
                bars_1m,
                signal_types=[sig_type],
                tp_ticks=tp,
                sl_ticks=sl,
                tick_size=tick_size,
                tick_value=tick_value,
                max_bars_timeout=max_bars_timeout,
            )

            n_trades = len(trades)
            if n_trades < min_trades:
                continue

            wins = trades[trades["profit_net"] > 0]
            losses = trades[trades["profit_net"] < 0]
            gross_profit = wins["profit_net"].sum()
            gross_loss = losses["profit_net"].sum()
            profit_factor = (gross_profit / abs(gross_loss)) if gross_loss != 0 else None
            win_rate = len(wins) / n_trades
            avg_win = wins["profit_net"].mean() if len(wins) else 0.0
            avg_loss = losses["profit_net"].mean() if len(losses) else 0.0
            total_net = trades["profit_net"].sum()

            is_oos = compute_is_oos_degradation(trades)

            # Region-level context for this combo
            n_regions = len(regions)
            n_broken = sum(1 for r in regions if r.broken)
            n_events_of_type = sum(1 for ev in events if ev.event_type == sig_type)

            results.append({
                "size_multiplier": mult,
                "lookback": lb,
                "zone_type": zt,
                "tp_ticks": tp,
                "sl_ticks": sl,
                "signal_type": sig_type,
                "n_trades": n_trades,
                "n_regions": n_regions,
                "n_broken_regions": n_broken,
                "n_events": n_events_of_type,
                "win_rate": round(win_rate, 4),
                "profit_factor": round(profit_factor, 4) if profit_factor is not None else None,
                "total_net": round(total_net, 2),
                "avg_win": round(avg_win, 2),
                "avg_loss": round(avg_loss, 2),
                "is_oos_degradation": round(is_oos, 4) if is_oos is not None else None,
            })

    ranked = _rank_lcr_results(results)

    return {
        "n_combinations_run": len(combos) * len(signal_types),
        "n_results": len(results),
        "results": ranked,
        "r2r_stats": global_r2r,
        "retrace_stats": global_retrace,
        "config": config,
    }


def _rank_lcr_results(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Assign a tier label and sort results by quality score."""
    def _score(r: Dict) -> float:
        pf = r.get("profit_factor") or 0.0
        wr = r.get("win_rate") or 0.0
        nt = r.get("n_trades") or 0
        oos = r.get("is_oos_degradation")
        score = pf * 0.5 + wr * 0.3 + min(nt / 200.0, 1.0) * 0.2
        if oos is not None:
            score -= max(0.0, float(oos)) * 0.3
        return score

    def _tier(r: Dict) -> str:
        pf = r.get("profit_factor") or 0.0
        nt = r.get("n_trades") or 0
        oos = r.get("is_oos_degradation")
        if pf >= 1.5 and nt >= 30 and oos is not None and float(oos) <= 0.1:
            return "Most Robust"
        if pf >= 1.3 and nt >= 20:
            return "High Quality"
        if pf >= 1.1 and nt >= 15:
            return "Solid"
        return "Marginal"

    annotated = []
    for r in results:
        out = dict(r)
        out["tier"] = _tier(r)
        out["_score"] = _score(r)
        annotated.append(out)

    annotated.sort(key=lambda x: x["_score"], reverse=True)
    for r in annotated:
        r.pop("_score", None)
    return annotated
