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
