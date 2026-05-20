from __future__ import annotations

from ta_foundation.analysis.entry_strategies.hardening import inject_trial_grid_size
from ta_foundation.analysis.entry_strategies.outcome.simulator import count_outcome_modes
from ta_foundation.analysis.entry_strategies.sweep import (
    DEFAULT_CANDLE_DISCOVERY_CONFIG,
    _compute_trial_grid_size,
    _count_signal_combos,
)
from ta_foundation.analysis.entry_strategies._sweep_base import (
    _compute_trial_grid_size as _base_grid,
)
from ta_foundation.analysis.entry_strategies.orb_sweep import (
    DEFAULT_ORB_DISCOVERY_CONFIG,
    _compute_trial_grid_size as _orb_grid,
)
from ta_foundation.analysis.entry_strategies.ma_sweep import (
    DEFAULT_MA_DISCOVERY_CONFIG,
    _compute_trial_grid_size as _ma_grid,
)
from ta_foundation.analysis.entry_strategies.bb_sweep import (
    DEFAULT_BB_DISCOVERY_CONFIG,
    _compute_trial_grid_size as _bb_grid,
)
from ta_foundation.analysis.entry_strategies.lcr_sweep import (
    _compute_trial_grid_size as _lcr_grid,
)


def _cfg(**overrides):
    base = {
        "timeframes": [1, 5],
        "direction": "both",
        "entry_timing": {
            "next_open": {"enabled": True},
            "break_extreme": {"enabled": True},
            "body_midpoint": {"enabled": False},
        },
        "patterns": {
            "inside_bar": {"enabled": True},
            "large_body": {"enabled": True, "body_multiplier": [1.5, 2.0]},
        },
        "outcome": {
            "atr": {"enabled": True},
            "ticks": {"enabled": True, "take_profit": [30, 60], "stop": [40]},
        },
        "mtf": {
            "confluence": {"enabled": False},
            "hierarchical": {"enabled": False},
        },
    }
    base.update(overrides)
    return base


def test_outcome_modes_count_atr_plus_tick_grid() -> None:
    # atr (1) + ticks 2x1 (2) = 3
    assert count_outcome_modes(_cfg()["outcome"]) == 3


def test_outcome_modes_count_with_ticks_disabled() -> None:
    assert count_outcome_modes({"atr": {"enabled": True}, "ticks": {"enabled": False}}) == 1


def test_outcome_modes_count_full_tick_grid() -> None:
    cfg = {"atr": {"enabled": False}, "ticks": {"enabled": True, "take_profit": [30, 60, 100], "stop": [30, 40, 50]}}
    assert count_outcome_modes(cfg) == 9


def test_signal_combos_multiply_every_axis() -> None:
    # tf 2 x param-combos (inside_bar 1 + large_body 2 = 3) x dir 2 x timings 2
    assert _count_signal_combos(_cfg()) == 24


def test_signal_combos_skip_disabled_and_unknown_patterns() -> None:
    cfg = _cfg(
        patterns={
            "inside_bar": {"enabled": True},
            "large_body": {"enabled": False, "body_multiplier": [1.5, 2.0]},
            "not_a_real_pattern": {"enabled": True},
        }
    )
    # only inside_bar counts: tf 2 x 1 x dir 2 x timings 2
    assert _count_signal_combos(cfg) == 8


def test_grid_size_is_signal_combos_times_outcome_modes() -> None:
    # 24 signal combos x 3 outcome modes, no MTF
    assert _compute_trial_grid_size(_cfg()) == 72


def test_grid_size_adds_mtf_upper_bound() -> None:
    cfg = _cfg(
        mtf={
            "confluence": {"enabled": True},
            "hierarchical": {"enabled": True, "context_tf": 5, "entry_tf": 1},
        }
    )
    # independent 72 + confluence (4 keys x 2 timings x 3 modes = 24)
    #            + hierarchical (24) = 120
    assert _compute_trial_grid_size(cfg) == 120


def test_grid_size_drops_mtf_for_a_single_timeframe() -> None:
    cfg = _cfg(
        timeframes=[1],
        mtf={
            "confluence": {"enabled": True},
            "hierarchical": {"enabled": True, "context_tf": 5, "entry_tf": 1},
        },
    )
    # confluence needs >=2 TFs; hierarchical needs ctx_tf 5 present — both drop.
    # tf 1 x 3 param-combos x dir 2 x timings 2 = 12, x 3 modes = 36
    assert _compute_trial_grid_size(cfg) == 36


def test_grid_size_of_the_default_config_is_a_real_search() -> None:
    # The whole point of the follow-up: the budget is no longer inert at n=1.
    grid = _compute_trial_grid_size(DEFAULT_CANDLE_DISCOVERY_CONFIG)
    assert grid > 1000


def test_inject_populates_within_run_trials_when_absent() -> None:
    out = inject_trial_grid_size({}, 500)
    assert out["trial_budget"]["within_run_trials"] == 500


def test_inject_leaves_an_explicit_within_run_trials_untouched() -> None:
    out = inject_trial_grid_size(
        {"trial_budget": {"within_run_trials": 9}}, 500
    )
    assert out["trial_budget"]["within_run_trials"] == 9


def test_inject_preserves_other_trial_budget_keys() -> None:
    out = inject_trial_grid_size(
        {"trial_budget": {"prior_program_trials": 100, "prior_decay": 0.25}}, 500
    )
    assert out["trial_budget"]["within_run_trials"] == 500
    assert out["trial_budget"]["prior_program_trials"] == 100
    assert out["trial_budget"]["prior_decay"] == 0.25


# ---------------------------------------------------------------------------
# Other hardening sweep families — same trial-budget auto-population
# ---------------------------------------------------------------------------

def test_orb_grid_size_of_default_config() -> None:
    # 12 orb-param combos x 2 timings x 10 outcome modes
    assert _orb_grid(DEFAULT_ORB_DISCOVERY_CONFIG) == 240


def test_orb_grid_size_controlled() -> None:
    cfg = {
        "orb": {"orb_minutes": [15, 30], "min_range_ticks": [4]},
        "entry_timing": {"next_open": {"enabled": True}},
        "outcome": {"atr": {"enabled": True}, "ticks": {"enabled": False}},
    }
    # 2 orb-param combos x 1 timing x 1 outcome mode
    assert _orb_grid(cfg) == 2


def test_ma_grid_size_of_default_config_is_a_real_search() -> None:
    assert _ma_grid(DEFAULT_MA_DISCOVERY_CONFIG) > 1000


def test_ma_grid_size_controlled() -> None:
    cfg = {
        "timeframes": [1, 5],
        "signals": {"ma_cross": {"enabled": True, "period": [9, 20]}},
        "entry_timing": {"next_open": {"enabled": True}, "break_extreme": {"enabled": False}},
        "outcome": {"atr": {"enabled": True}, "ticks": {"enabled": False}},
    }
    # 2 tf x 2 param combos x 1 timing x 1 outcome mode
    assert _ma_grid(cfg) == 4


def test_ma_grid_size_skips_unknown_signal() -> None:
    cfg = {
        "timeframes": [1],
        "signals": {"not_a_real_signal": {"enabled": True, "period": [9, 20]}},
        "entry_timing": {"next_open": {"enabled": True}},
        "outcome": {"atr": {"enabled": True}, "ticks": {"enabled": False}},
    }
    assert _ma_grid(cfg) == 1


def test_bb_grid_size_of_default_config_is_a_real_search() -> None:
    assert _bb_grid(DEFAULT_BB_DISCOVERY_CONFIG) > 100


def test_bb_grid_size_controlled() -> None:
    cfg = {
        "timeframes": [1],
        "signals": {"bb_mean_reversion": {"enabled": True, "min_z_extreme": [1.5, 2.0]}},
        "entry_timing": {"next_open": {"enabled": True}, "break_extreme": {"enabled": True}},
        "outcome": {"atr": {"enabled": True}, "ticks": {"enabled": False}},
    }
    # 1 tf x 2 param combos x 2 timings x 1 outcome mode
    assert _bb_grid(cfg) == 4


def test_lcr_grid_size_of_default_config() -> None:
    # size_mult 2 x lookback 2 x zone 1 x tp 1 x sl 1 x signal_types 4
    assert _lcr_grid({}) == 16


def test_lcr_grid_size_controlled() -> None:
    cfg = {
        "size_multipliers": [1.5, 2.0],
        "lookbacks": [10],
        "zone_types": ["body", "range"],
        "tp_ticks": [20, 30],
        "sl_ticks": [10],
        "signal_types": ["fresh", "break"],
    }
    # 2 x 1 x 2 x 2 x 1 x 2
    assert _lcr_grid(cfg) == 16


def test_sweep_base_grid_size_controlled() -> None:
    registry = {"sig_a": lambda bars, params: None}
    cfg = {
        "timeframes": [1, 5],
        "signals": {"sig_a": {"enabled": True, "lookback": [5, 10, 20]}},
        "entry_timing": {"next_open": {"enabled": True}},
        "outcome": {"atr": {"enabled": True}, "ticks": {"enabled": False}},
    }
    # 2 tf x 3 param combos x 1 timing x 1 outcome mode
    assert _base_grid(cfg, registry) == 6


def test_sweep_base_grid_size_skips_signal_absent_from_registry() -> None:
    cfg = {
        "timeframes": [1],
        "signals": {"sig_a": {"enabled": True, "lookback": [5, 10]}},
        "entry_timing": {"next_open": {"enabled": True}},
        "outcome": {"atr": {"enabled": True}, "ticks": {"enabled": False}},
    }
    assert _base_grid(cfg, {}) == 1
