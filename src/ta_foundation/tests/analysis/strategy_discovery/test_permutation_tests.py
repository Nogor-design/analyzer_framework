from __future__ import annotations

import numpy as np
import pandas as pd


def test_noise_returns_are_not_significant_on_average():
    from ta_foundation.analysis.strategy_discovery.permutation_tests import (
        permutation_test_returns,
    )

    p_values = []
    for seed in (11, 23, 37):
        rng = np.random.default_rng(seed)
        magnitudes = rng.lognormal(mean=2.0, sigma=0.35, size=120)
        signs = np.array([1.0, -1.0] * 60)
        rng.shuffle(signs)
        returns = magnitudes * signs

        result = permutation_test_returns(returns, metric="expectancy", n=400, seed=seed)
        p_values.append(result.p_value)

    assert float(np.mean(p_values)) > 0.2


def test_strong_positive_edge_is_significant():
    from ta_foundation.analysis.strategy_discovery.permutation_tests import (
        permutation_test_returns,
    )

    returns = np.array([12.0] * 95 + [-12.0] * 5)
    result = permutation_test_returns(returns, metric="expectancy", n=500, seed=17)

    assert result.observed > 0
    assert result.p_value < 0.05


def test_same_seed_is_deterministic():
    from ta_foundation.analysis.strategy_discovery.permutation_tests import (
        permutation_test_returns,
    )

    returns = np.array([15.0, -4.0, 11.0, -7.0, 13.0, 9.0, -5.0, 12.0])
    first = permutation_test_returns(returns, metric="profit_factor", n=250, seed=29)
    second = permutation_test_returns(returns, metric="profit_factor", n=250, seed=29)

    assert first.to_dict() == second.to_dict()


def test_discovery_helper_uses_oos_pool_from_full_trades():
    from ta_foundation.analysis.strategy_discovery.permutation_tests import (
        permutation_test_for_discovery,
    )

    trades = pd.DataFrame(
        {
            "entry_time": pd.date_range("2025-01-01", periods=70, freq="1h"),
            "profit_net": [100.0] * 10 + [8.0] * 54 + [-8.0] * 6,
        }
    )
    sd = {
        "cost_normalized_trades": trades,
        "wf_config": {"wf_type": "rolling", "n_folds": 6},
    }

    result = permutation_test_for_discovery(
        sd,
        n=200,
        seed=17,
        metrics=("expectancy",),
    )

    assert set(result) == {"expectancy"}
    assert np.isclose(result["expectancy"]["observed"], np.mean([8.0] * 54 + [-8.0] * 6))
    assert result["expectancy"]["p_value"] < 0.05


def test_ranking_permutation_gate_is_opt_in():
    from ta_foundation.analysis.strategy_discovery.ranking import evaluate_hard_gates

    sd = {
        "evaluation_oos": {"n_trades": 60, "profit_factor": 1.5},
        "permutation_tests": {
            "status": "ok",
            "expectancy": {
                "metric": "expectancy",
                "observed": 0.1,
                "p_value": 0.42,
                "n": 100,
                "null_mean": 0.0,
                "null_std": 1.0,
                "null_quantiles": {"0.5": 0.0, "0.95": 1.0, "0.99": 2.0},
            },
        },
    }

    off_failures = evaluate_hard_gates(sd)
    assert all(f["gate"] != "require_permutation_passed" for f in off_failures)

    on_failures = evaluate_hard_gates(
        sd,
        gates_config={"require_permutation_passed": True, "max_permutation_p": 0.05},
    )
    perm_failure = next(
        f for f in on_failures if f["gate"] == "require_permutation_passed"
    )
    assert perm_failure["reason"] == "permutation_p>0.05"
    assert perm_failure["value"]["p_value"] == 0.42
