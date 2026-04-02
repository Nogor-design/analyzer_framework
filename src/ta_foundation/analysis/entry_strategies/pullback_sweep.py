from __future__ import annotations

"""
Pullback / Continuation Discovery Sweep
==========================================
YAML config block: ``pullback_discovery:``

  pullback_discovery:
    enabled: true
    timeframes: [1, 5]
    min_trades: 20
    atr_period: 14

    signals:
      trend_pullback:
        enabled: true
        trend_bars: [8, 12]
        min_trend_atr: [1.0, 1.5]
        retrace_min: [0.25, 0.38]
        retrace_max: [0.65]
        direction: [0]

      inside_bar_continuation:
        enabled: true
        trend_bars: [8]
        min_trend_atr: [0.8]
        direction: [0]
        inside_bar_lookback: [3]

      failed_breakout:
        enabled: true
        lookback: [20]
        direction: [0]
        min_breach_ticks: [1.0]

    entry_timing:
      next_open:     {enabled: true}
      break_extreme: {enabled: true, buffer_ticks: 1, fill_timeout_bars: 3}
      body_midpoint: {enabled: true, fill_timeout_bars: 5}

    outcome: ...
    filter_discovery: {enabled: true, top_n: 10}
"""

from typing import Any, Dict, Optional

import pandas as pd

from ta_foundation.analysis.entry_strategies._sweep_base import run_generic_sweep
from ta_foundation.analysis.entry_strategies.pullback.signals import PULLBACK_SIGNAL_REGISTRY


DEFAULT_PULLBACK_DISCOVERY_CONFIG: Dict[str, Any] = {
    "enabled":    True,
    "timeframes": [1, 5],
    "min_trades": 20,
    "atr_period": 14,

    "signals": {
        "trend_pullback": {
            "enabled":       True,
            "trend_bars":    [8, 12],
            "min_trend_atr": [1.0, 1.5],
            "retrace_min":   [0.25, 0.38],
            "retrace_max":   [0.65],
            "direction":     [0],
        },
        "inside_bar_continuation": {
            "enabled":              True,
            "trend_bars":           [8],
            "min_trend_atr":        [0.8],
            "direction":            [0],
            "inside_bar_lookback":  [3],
        },
        "failed_breakout": {
            "enabled":          True,
            "lookback":         [20],
            "direction":        [0],
            "min_breach_ticks": [1.0],
        },
    },

    "entry_timing": {
        "next_open":     {"enabled": True},
        "break_extreme": {"enabled": True,  "buffer_ticks": 1, "fill_timeout_bars": 3},
        "body_midpoint": {"enabled": True,  "fill_timeout_bars": 5},
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


def run_pullback_discovery(
    bars_1m: pd.DataFrame,
    config: Optional[Dict[str, Any]] = None,
    bars_with_regime: Optional[pd.DataFrame] = None,
) -> Dict[str, Any]:
    """Run the pullback/continuation discovery sweep."""
    return run_generic_sweep(
        bars_1m=bars_1m,
        strategy_type="pullback",
        signal_registry=PULLBACK_SIGNAL_REGISTRY,
        config=config or {},
        default_config=DEFAULT_PULLBACK_DISCOVERY_CONFIG,
        bars_with_regime=bars_with_regime,
    )
