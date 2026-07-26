from __future__ import annotations

from ta_foundation.analysis.strategy_discovery.trial_budget import (
    compute_trial_budget,
)


def test_default_is_one_trial() -> None:
    # Unconfigured budget = no correction (a valid Bonferroni divisor of 1).
    budget = compute_trial_budget()
    assert budget.effective_trials == 1


def test_within_run_only() -> None:
    budget = compute_trial_budget({"within_run_trials": 5000})
    assert budget.effective_trials == 5000


def test_prior_program_trials_are_decayed() -> None:
    budget = compute_trial_budget(
        {"within_run_trials": 10, "prior_program_trials": 1000, "prior_decay": 0.5}
    )
    # 10 + round(1000 * 0.5)
    assert budget.effective_trials == 510


def test_decay_is_clamped_to_unit_interval() -> None:
    high = compute_trial_budget(
        {"within_run_trials": 1, "prior_program_trials": 100, "prior_decay": 9.0}
    )
    assert high.prior_decay == 1.0
    assert high.effective_trials == 101  # 1 + 100*1.0

    low = compute_trial_budget(
        {"within_run_trials": 1, "prior_program_trials": 100, "prior_decay": -3.0}
    )
    assert low.prior_decay == 0.0
    assert low.effective_trials == 1  # 1 + 100*0.0


def test_within_run_floors_at_one() -> None:
    assert compute_trial_budget({"within_run_trials": 0}).effective_trials == 1
    assert compute_trial_budget({"within_run_trials": -50}).within_run_trials == 1


def test_prior_floors_at_zero() -> None:
    budget = compute_trial_budget(
        {"within_run_trials": 7, "prior_program_trials": -999}
    )
    assert budget.prior_program_trials == 0
    assert budget.effective_trials == 7


def test_max_effective_trials_caps_runaway_corrections() -> None:
    budget = compute_trial_budget(
        {
            "within_run_trials": 10_000_000,
            "max_effective_trials": 50_000,
        }
    )
    assert budget.effective_trials == 50_000


def test_to_dict_is_json_safe() -> None:
    d = compute_trial_budget(
        {"within_run_trials": 200, "prior_program_trials": 80, "prior_decay": 0.25}
    ).to_dict()
    assert d == {
        "within_run_trials": 200,
        "prior_program_trials": 80,
        "prior_decay": 0.25,
        "effective_trials": 220,  # 200 + round(80 * 0.25)
    }
