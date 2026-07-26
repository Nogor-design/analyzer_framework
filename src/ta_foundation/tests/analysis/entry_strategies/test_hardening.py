from __future__ import annotations

import pandas as pd

from ta_foundation.analysis.entry_strategies.hardening import build_hardening_metadata


def _trades(n: int = 80) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "entry_time": pd.date_range("2026-01-01 09:30", periods=n, freq="min"),
            "profit_net": [50.0 if i % 3 == 0 else -10.0 for i in range(n)],
        }
    )


def test_hardening_holdout_partitions_validation_trades() -> None:
    metadata = build_hardening_metadata(
        _trades(80),
        {"commission_per_side": 0.0, "slippage_ticks": 0.0, "tick_value": 5.0},
        {
            "enabled": True,
            "n_mc_simulations": 10,
            "require_slippage_stress_passed": False,
            "wf_config": {
                "wf_type": "anchored",
                "is_pct": 0.50,
                "min_is_trades": 10,
                "min_oos_trades": 5,
                "degradation_threshold": 1.0,
            },
            "holdout": {
                "enabled": True,
                "is_frac": 0.60,
                "val_frac": 0.20,
                "min_dev_trades": 20,
                "min_holdout_trades": 10,
                "require_holdout_passed": False,
            },
        },
    )

    holdout = metadata["holdout_partition"]
    assert holdout["enabled"] is True
    assert holdout["n_holdout"] == 16
    assert holdout["dev_trades"] == 64

    validation = metadata["validation"]
    assert validation["wf_results"]["is_trades"] + validation["wf_results"]["oos_trades"] == 64

    holdout_eval = metadata["evaluation_holdout"]
    assert holdout_eval["n_trades"] == 16
    assert holdout_eval["passed"] is True


def test_hardening_holdout_required_can_fail_candidate() -> None:
    trades = _trades(80)
    trades.loc[64:, "profit_net"] = -25.0

    metadata = build_hardening_metadata(
        trades,
        {"commission_per_side": 0.0, "slippage_ticks": 0.0, "tick_value": 5.0},
        {
            "enabled": True,
            "n_mc_simulations": 10,
            "require_slippage_stress_passed": False,
            "wf_config": {
                "wf_type": "anchored",
                "is_pct": 0.50,
                "min_is_trades": 10,
                "min_oos_trades": 5,
                "degradation_threshold": 1.0,
            },
            "holdout": {
                "enabled": True,
                "is_frac": 0.60,
                "val_frac": 0.20,
                "min_dev_trades": 20,
                "min_holdout_trades": 10,
                "require_holdout_passed": True,
                "min_profit_factor": 1.0,
            },
        },
    )

    assert metadata["holdout_partition"]["enabled"] is True
    assert metadata["evaluation_holdout"]["passed"] is False
    assert metadata["passed"] is False
    assert "hard gate failed: holdout" in metadata["issues"]


_OUTCOME_CFG = {"commission_per_side": 0.0, "slippage_ticks": 0.0, "tick_value": 5.0}
_WF_CFG = {
    "wf_type": "anchored",
    "is_pct": 0.50,
    "min_is_trades": 10,
    "min_oos_trades": 5,
    "degradation_threshold": 1.0,
}


def _regime_split_trades(up_edge: bool, range_edge: bool):
    """Two regime blocks (trend_up then range) plus the matching regime bars."""

    def block(start: str, edge: bool) -> pd.DataFrame:
        # 36 trades; an edge block clears the honest gate, a non-edge one fails.
        profit = (
            [80.0] * 20 + [-20.0] * 16 if edge else [80.0] * 12 + [-20.0] * 24
        )
        return pd.DataFrame(
            {
                "entry_time": pd.date_range(start, periods=36, freq="min"),
                "profit_net": profit,
            }
        )

    trades = pd.concat(
        [block("2026-01-01 09:30", up_edge), block("2026-01-01 11:00", range_edge)],
        ignore_index=True,
    )
    bars = pd.DataFrame(
        {
            "dt": pd.date_range("2026-01-01 09:00", periods=240, freq="min"),
            "regime": ["trend_up"] * 120 + ["range"] * 120,
        }
    )
    return trades, bars


def test_regime_scoping_threads_through_and_feeds_trial_budget() -> None:
    trades, bars = _regime_split_trades(up_edge=True, range_edge=False)

    metadata = build_hardening_metadata(
        trades,
        _OUTCOME_CFG,
        {
            "enabled": True,
            "n_mc_simulations": 10,
            "require_slippage_stress_passed": False,
            "require_honest_execution_passed": False,
            "wf_config": _WF_CFG,
            "trial_budget": {"within_run_trials": 5},
        },
        bars_with_regime=bars,
    )

    rs = metadata["regime_scoping"]
    assert rs["n_regimes_evaluated"] == 2
    assert rs["track"] == "regime-limited"
    assert rs["edge_regimes"] == ["trend_up"]
    # The regime selection is 2 extra trials on top of the configured 5.
    assert metadata["trial_budget"]["within_run_trials"] == 7


def test_required_regime_scoping_gate_can_fail_a_candidate() -> None:
    trades, bars = _regime_split_trades(up_edge=False, range_edge=False)

    metadata = build_hardening_metadata(
        trades,
        _OUTCOME_CFG,
        {
            "enabled": True,
            "n_mc_simulations": 10,
            "require_slippage_stress_passed": False,
            "require_honest_execution_passed": False,
            "require_regime_scoping_passed": True,
            "wf_config": _WF_CFG,
        },
        bars_with_regime=bars,
    )

    assert metadata["regime_scoping"]["passed"] is False
    assert metadata["passed"] is False
    assert "hard gate failed: regime_scoping" in metadata["issues"]


def test_n_hypotheses_tested_floors_an_auto_injected_trial_budget() -> None:
    # A pinned single-candidate hardening re-run: the sweep auto-injects a tiny
    # within_run_trials (its own 1-cell grid), but the operator set
    # n_hypotheses_tested to the real broad-search size. The floor must win so
    # the selection-bias correction is not silently weakened.
    metadata = build_hardening_metadata(
        _trades(120),
        _OUTCOME_CFG,
        {
            "enabled": True,
            "n_mc_simulations": 10,
            "n_hypotheses_tested": 2592,
            "require_slippage_stress_passed": False,
            "require_honest_execution_passed": False,
            "wf_config": _WF_CFG,
            "trial_budget": {"within_run_trials": 1},
        },
    )

    tb = metadata["trial_budget"]
    assert tb["within_run_trials"] == 1          # honest record of this run
    assert tb["effective_trials"] == 2592        # ...floored by the operator value
    assert tb["n_hypotheses_tested_floor"] == 2592

    t_test = next(
        g for g in metadata["validation"]["gates"] if g["name"] == "t_test"
    )
    assert t_test["threshold"]["n_hypotheses_tested"] == 2592
