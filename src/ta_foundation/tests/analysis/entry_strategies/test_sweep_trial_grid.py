from __future__ import annotations

from ta_foundation.analysis.entry_strategies.sweep import (
    DEFAULT_CANDLE_DISCOVERY_CONFIG,
    _compute_trial_grid_size,
    _count_outcome_modes,
    _count_signal_combos,
    _inject_grid_size_into_hardening,
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
    assert _count_outcome_modes(_cfg()["outcome"]) == 3


def test_outcome_modes_count_with_ticks_disabled() -> None:
    assert _count_outcome_modes({"atr": {"enabled": True}, "ticks": {"enabled": False}}) == 1


def test_outcome_modes_count_full_tick_grid() -> None:
    cfg = {"atr": {"enabled": False}, "ticks": {"enabled": True, "take_profit": [30, 60, 100], "stop": [30, 40, 50]}}
    assert _count_outcome_modes(cfg) == 9


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
    out = _inject_grid_size_into_hardening({}, 500)
    assert out["trial_budget"]["within_run_trials"] == 500


def test_inject_leaves_an_explicit_within_run_trials_untouched() -> None:
    out = _inject_grid_size_into_hardening(
        {"trial_budget": {"within_run_trials": 9}}, 500
    )
    assert out["trial_budget"]["within_run_trials"] == 9


def test_inject_preserves_other_trial_budget_keys() -> None:
    out = _inject_grid_size_into_hardening(
        {"trial_budget": {"prior_program_trials": 100, "prior_decay": 0.25}}, 500
    )
    assert out["trial_budget"]["within_run_trials"] == 500
    assert out["trial_budget"]["prior_program_trials"] == 100
    assert out["trial_budget"]["prior_decay"] == 0.25
