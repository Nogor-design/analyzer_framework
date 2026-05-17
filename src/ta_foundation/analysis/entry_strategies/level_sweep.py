from __future__ import annotations

"""
Level-Based Entry Discovery Sweep
=====================================
YAML config block: ``level_discovery:``

  level_discovery:
    enabled: true
    timeframes: [1, 5]
    min_trades: 15
    atr_period: 14

    signals:
      swing_level_test:
        enabled: true
        pivot_lookback: [5, 8]
        touch_atr_mult: [0.5, 1.0]
        max_pivots: [5]
        direction: [0]

      consolidation_breakout:
        enabled: true
        consol_bars: [8, 12]
        max_range_atr: [0.8, 1.2]
        direction: [0]
        min_breach_atr: [0.3]

      round_number_bounce:
        enabled: true
        level_step: [50.0, 100.0]
        touch_ticks: [4.0, 8.0]
        direction: [0]

      vwap_reclaim_reject:
        enabled: true
        max_dist_ticks: [8.0, 16.0]
        min_pierce_ticks: [1.0, 2.0]
        direction: [0]

      prior_session_reaction:
        enabled: true
        levels: [["prior_high", "prior_low", "overnight_high", "overnight_low"]]
        touch_ticks: [4.0, 8.0]
        min_close_ticks: [1.0, 2.0]
        direction: [0]

      reference_sweep_reclaim:
        enabled: true
        levels: [["prior_high", "prior_low", "overnight_high", "overnight_low"]]
        sweep_ticks: [1.0, 2.0, 4.0]
        close_back_ticks: [0.0, 1.0]
        direction: [0]

      liquidity_sweep_failure:
        enabled: true
        lookback: [10, 20]
        sweep_ticks: [1.0, 2.0]
        close_back_ticks: [1.0]
        direction: [0]

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
from ta_foundation.analysis.entry_strategies.level.signals import LEVEL_SIGNAL_REGISTRY


DEFAULT_LEVEL_DISCOVERY_CONFIG: Dict[str, Any] = {
    "enabled":    True,
    "timeframes": [1, 5],
    "min_trades": 15,
    "atr_period": 14,

    "signals": {
        "swing_level_test": {
            "enabled":        True,
            "pivot_lookback": [5, 8],
            "touch_atr_mult": [0.5, 1.0],
            "max_pivots":     [5],
            "direction":      [0],
        },
        "consolidation_breakout": {
            "enabled":       True,
            "consol_bars":   [8, 12],
            "max_range_atr": [0.8, 1.2],
            "direction":     [0],
            "min_breach_atr": [0.3],
        },
        "round_number_bounce": {
            "enabled":     True,
            "level_step":  [50.0, 100.0],
            "touch_ticks": [4.0, 8.0],
            "direction":   [0],
        },
        "vwap_reclaim_reject": {
            "enabled":          True,
            "max_dist_ticks":   [8.0, 16.0],
            "min_pierce_ticks": [1.0, 2.0],
            "direction":        [0],
        },
        "vwap_distance_fade": {
            "enabled":          False,
            "min_dist_ticks":   [40.0, 60.0],
            "max_dist_ticks":   [150.0],
            "reversal_ticks":   [4.0, 8.0],
            "direction":        [0],
        },
        "vwap_continuation": {
            "enabled":                False,
            "min_continuation_ticks": [3.0, 5.0],
            "max_age_bars":           [4, 8],
            "min_hold_bars":          [1, 2],
            "min_dist_ticks":         [1.0],
            "max_dist_ticks":         [16.0, 24.0],
            "direction":              [0],
        },
        "prior_session_reaction": {
            "enabled":          True,
            "levels":           [["prior_high", "prior_low", "overnight_high", "overnight_low"]],
            "touch_ticks":      [4.0, 8.0],
            "min_close_ticks":  [1.0, 2.0],
            "direction":        [0],
        },
        "reference_sweep_reclaim": {
            "enabled":          True,
            "levels":           [["prior_high", "prior_low", "overnight_high", "overnight_low"]],
            "sweep_ticks":      [1.0, 2.0, 4.0],
            "close_back_ticks": [0.0, 1.0],
            "direction":        [0],
        },
        "failed_reference_breakout": {
            "enabled":            False,
            "levels":             [["prior_high", "prior_low", "overnight_high", "overnight_low"]],
            "confirmation_ticks": [1.0, 2.0],
            "max_fail_bars":      [4, 8, 12],
            "fail_close_ticks":   [1.0, 2.0],
            "direction":          [0],
        },
        "liquidity_sweep_failure": {
            "enabled":          True,
            "lookback":         [10, 20],
            "sweep_ticks":      [1.0, 2.0],
            "close_back_ticks": [1.0],
            "direction":        [0],
        },
        "large_candle_origin_retest": {
            "enabled":         False,
            "avg_lookback":    [20, 40],
            "large_body_mult": [1.6, 2.0, 2.5],
            "min_body_ticks":  [8.0, 12.0],
            "max_retest_bars": [8, 12, 20],
            "touch_ticks":     [3.0, 5.0],
            "min_close_ticks": [2.0, 3.0],
            "direction":       [0],
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


def run_level_discovery(
    bars_1m: pd.DataFrame,
    config: Optional[Dict[str, Any]] = None,
    bars_with_regime: Optional[pd.DataFrame] = None,
) -> Dict[str, Any]:
    """Run the level-based entry discovery sweep."""
    return run_generic_sweep(
        bars_1m=bars_1m,
        strategy_type="level",
        signal_registry=LEVEL_SIGNAL_REGISTRY,
        config=config or {},
        default_config=DEFAULT_LEVEL_DISCOVERY_CONFIG,
        bars_with_regime=bars_with_regime,
    )
