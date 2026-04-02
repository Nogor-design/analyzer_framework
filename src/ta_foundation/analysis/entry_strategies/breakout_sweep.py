from __future__ import annotations

"""
Breakout Discovery Sweep
==========================
YAML config block: ``breakout_discovery:``

  breakout_discovery:
    enabled: true
    timeframes: [1, 5]
    min_trades: 20
    atr_period: 14

    signals:
      n_bar_breakout:
        enabled: true
        lookback: [10, 20, 50]
        direction: [0]
        require_close_beyond: [true]
        min_breach_ticks: [1.0, 2.0]
        squeeze_atr_pct: [0.0]

      volatility_breakout:
        enabled: true
        atr_mult: [1.5, 2.0, 2.5]
        direction: [0]
        body_zone_pct: [0.0, 0.3]
        min_atr_ticks: [4.0]

    entry_timing:
      next_open:     {enabled: true}
      break_extreme: {enabled: true, buffer_ticks: 1, fill_timeout_bars: 3}

    outcome:
      atr:   {enabled: true, target_mult: 1.5, stop_mult: 1.0}
      ticks: {enabled: true, take_profit: [30, 60, 100], stop: [30, 40, 50]}
      ...

    filter_discovery: {enabled: true, top_n: 10}
"""

from typing import Any, Dict, Optional

import pandas as pd

from ta_foundation.analysis.entry_strategies._sweep_base import run_generic_sweep
from ta_foundation.analysis.entry_strategies.breakout.signals import BREAKOUT_SIGNAL_REGISTRY


DEFAULT_BREAKOUT_DISCOVERY_CONFIG: Dict[str, Any] = {
    "enabled":    True,
    "timeframes": [1, 5],
    "min_trades": 20,
    "atr_period": 14,

    "signals": {
        "n_bar_breakout": {
            "enabled":             True,
            "lookback":            [10, 20, 50],
            "direction":           [0],
            "require_close_beyond": [True],
            "min_breach_ticks":    [1.0, 2.0],
            "squeeze_atr_pct":     [0.0],
        },
        "volatility_breakout": {
            "enabled":       True,
            "atr_mult":      [1.5, 2.0, 2.5],
            "direction":     [0],
            "body_zone_pct": [0.0, 0.3],
            "min_atr_ticks": [4.0],
        },
    },

    "entry_timing": {
        "next_open":     {"enabled": True},
        "break_extreme": {"enabled": True, "buffer_ticks": 1, "fill_timeout_bars": 3},
        "body_midpoint": {"enabled": False},
    },

    "outcome": {
        "atr":   {"enabled": True,  "target_mult": 1.5, "stop_mult": 1.0},
        "ticks": {"enabled": True,  "take_profit": [30, 60, 100], "stop": [30, 40, 50]},
        "max_bars_timeout":    20,
        "timeout_result":      "loss",
        "tick_size":           0.25,
        "tick_value":          5.00,
        "commission_per_side": 2.09,
        "slippage_ticks":      1,
    },

    "filter_discovery": {"enabled": True, "top_n": 10},
}


def run_breakout_discovery(
    bars_1m: pd.DataFrame,
    config: Optional[Dict[str, Any]] = None,
    bars_with_regime: Optional[pd.DataFrame] = None,
) -> Dict[str, Any]:
    """Run the breakout discovery sweep."""
    return run_generic_sweep(
        bars_1m=bars_1m,
        strategy_type="breakout",
        signal_registry=BREAKOUT_SIGNAL_REGISTRY,
        config=config or {},
        default_config=DEFAULT_BREAKOUT_DISCOVERY_CONFIG,
        bars_with_regime=bars_with_regime,
    )
