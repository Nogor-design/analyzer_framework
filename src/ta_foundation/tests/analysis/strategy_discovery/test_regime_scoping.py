from __future__ import annotations

import pandas as pd

from ta_foundation.analysis.strategy_discovery.regime_scoping import run_regime_scoping

COST_MODEL = {"commission_per_side": 2.09, "tick_value": 5.0, "slippage_ticks": 1}


def _regime_bars(label: str, start: str, n: int = 100) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "dt": pd.date_range(start, periods=n, freq="h"),
            "regime": [label] * n,
        }
    )


def _two_regime_bars() -> pd.DataFrame:
    """trend_up bars in Jan, range bars in Feb — well-separated, no overlap."""
    return pd.concat(
        [
            _regime_bars("trend_up", "2026-01-01"),
            _regime_bars("range", "2026-02-01"),
        ],
        ignore_index=True,
    )


def _regime_trades(
    start: str, *, wins: int, win_gross: float, losses: int, loss_gross: float
) -> pd.DataFrame:
    n = wins + losses
    return pd.DataFrame(
        {
            "entry_time": pd.date_range(start, periods=n, freq="h"),
            "profit": [win_gross] * wins + [loss_gross] * losses,
            "result": ["win"] * wins + ["loss"] * losses,
        }
    )


# A solid edge: clears the honest survival gate comfortably.
def _solid(start: str) -> pd.DataFrame:
    return _regime_trades(start, wins=24, win_gross=200.0, losses=16, loss_gross=-60.0)


# A marginal edge: positive optimistically, negative once fills are honest.
def _losing(start: str) -> pd.DataFrame:
    return _regime_trades(start, wins=20, win_gross=85.0, losses=20, loss_gross=-55.0)


def test_durable_edge_passes_in_every_regime() -> None:
    trades = pd.concat(
        [_solid("2026-01-01 01:00"), _solid("2026-02-01 01:00")], ignore_index=True
    )
    out = run_regime_scoping(
        trades, bars_with_regime=_two_regime_bars(), cost_model=COST_MODEL
    )
    assert out["track"] == "durable"
    assert out["passed"] is True
    assert out["edge_regimes"] == ["range", "trend_up"]
    assert out["non_edge_regimes"] == []
    assert out["n_regimes_evaluated"] == 2
    assert out["scoped_variant"]["regimes"] == ["range", "trend_up"]
    assert out["scoped_variant"]["passed"] is True


def test_regime_limited_edge_emits_a_scoped_variant() -> None:
    # trend_up has a real edge; range bleeds once fills are honest.
    trades = pd.concat(
        [_solid("2026-01-01 01:00"), _losing("2026-02-01 01:00")], ignore_index=True
    )
    out = run_regime_scoping(
        trades, bars_with_regime=_two_regime_bars(), cost_model=COST_MODEL
    )
    assert out["track"] == "regime-limited"
    assert out["edge_regimes"] == ["trend_up"]
    assert out["non_edge_regimes"] == ["range"]
    assert out["passed"] is True
    assert out["scoped_variant"]["regimes"] == ["trend_up"]
    assert out["scoped_variant"]["n_trades"] == 40
    assert out["scoped_variant"]["passed"] is True
    assert any("regime-limited" in m for m in out["issues"])


def test_no_edge_in_any_regime_fails_with_no_variant() -> None:
    trades = pd.concat(
        [_losing("2026-01-01 01:00"), _losing("2026-02-01 01:00")], ignore_index=True
    )
    out = run_regime_scoping(
        trades, bars_with_regime=_two_regime_bars(), cost_model=COST_MODEL
    )
    assert out["track"] == "none"
    assert out["passed"] is False
    assert out["edge_regimes"] == []
    assert out["scoped_variant"] is None
    assert any("honest survival gate" in m for m in out["issues"])


def test_thin_regime_is_skipped_not_judged() -> None:
    # range has only 10 trades — too few to be a real hypothesis.
    thin_range = _regime_trades(
        "2026-02-01 01:00", wins=6, win_gross=200.0, losses=4, loss_gross=-60.0
    )
    trades = pd.concat([_solid("2026-01-01 01:00"), thin_range], ignore_index=True)
    out = run_regime_scoping(
        trades, bars_with_regime=_two_regime_bars(), cost_model=COST_MODEL
    )
    assert "range" in out["skipped_regimes"]
    assert out["n_regimes_evaluated"] == 1
    assert out["edge_regimes"] == ["trend_up"]
    assert "range" not in out["per_regime"]


def test_n_regimes_evaluated_feeds_the_trial_budget() -> None:
    trades = pd.concat(
        [_solid("2026-01-01 01:00"), _solid("2026-02-01 01:00")], ignore_index=True
    )
    out = run_regime_scoping(
        trades, bars_with_regime=_two_regime_bars(), cost_model=COST_MODEL
    )
    # Picking the best of N regimes is N extra trials for the step-2 budget.
    assert out["trial_budget_within_run_trials"] == out["n_regimes_evaluated"] == 2


def test_trades_before_first_classified_bar_are_unlabelled() -> None:
    # Bars start in June; all trades sit in January.
    bars = _regime_bars("trend_up", "2026-06-01")
    out = run_regime_scoping(
        _solid("2026-01-01 01:00"), bars_with_regime=bars, cost_model=COST_MODEL
    )
    assert out["n_unlabeled"] == 40
    assert out["n_regimes_evaluated"] == 0
    assert out["track"] == "none"
    assert out["passed"] is False


def test_custom_regime_column_is_honoured() -> None:
    bars = _two_regime_bars().rename(columns={"regime": "vol_regime"})
    trades = pd.concat(
        [_solid("2026-01-01 01:00"), _solid("2026-02-01 01:00")], ignore_index=True
    )
    out = run_regime_scoping(
        trades,
        bars_with_regime=bars,
        cost_model=COST_MODEL,
        options={"regime_column": "vol_regime"},
    )
    assert out["regime_column"] == "vol_regime"
    assert out["n_regimes_evaluated"] == 2


def test_disabled_returns_unevaluated() -> None:
    out = run_regime_scoping(
        _solid("2026-01-01 01:00"),
        bars_with_regime=_two_regime_bars(),
        cost_model=COST_MODEL,
        options={"enabled": False},
    )
    assert out["passed"] is None
    assert any("disabled" in m for m in out["issues"])


def test_missing_data_is_graceful() -> None:
    bars = _two_regime_bars()
    solid = _solid("2026-01-01 01:00")

    assert run_regime_scoping(None, bars_with_regime=bars, cost_model=COST_MODEL)[
        "passed"
    ] is None

    no_bars = run_regime_scoping(solid, bars_with_regime=None, cost_model=COST_MODEL)
    assert no_bars["passed"] is None
    assert any("bars_with_regime" in m for m in no_bars["issues"])

    bad_col = run_regime_scoping(
        solid,
        bars_with_regime=bars.rename(columns={"regime": "foo"}),
        cost_model=COST_MODEL,
    )
    assert bad_col["passed"] is None
    assert any("regime" in m for m in bad_col["issues"])

    no_profit = solid.drop(columns=["profit"])
    out = run_regime_scoping(
        no_profit, bars_with_regime=bars, cost_model=COST_MODEL
    )
    assert out["passed"] is None
    assert any("profit" in m for m in out["issues"])
