"""Tests for the account-survival simulator (Phase 2 validation).

Uses a hermetic APEX-50k-shaped profile (start 50000, $2,500 intraday trail, lock
at +$100) plus one integration check against the shipped apex.yaml.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from ta_foundation.analysis.risk.account_state import FirmProfile, SizeRules, load_firm_profile
from ta_foundation.analysis.risk.survival import (
    lineup_survival,
    per_template_survival,
    simulate_survival,
)
from ta_foundation.analysis.risk.trade_loader import Trade

_BASE = datetime(2026, 5, 1, 9, 30)


def _profile() -> FirmProfile:
    return FirmProfile(
        firm="APEX", version="test", drawdown_type="intraday_trailing",
        includes_unrealized=True, lock_buffer=100.0,
        account_sizes={"50000": SizeRules(max_drawdown=2500.0, profit_target=3000.0, max_contracts=10)},
    )


def _t(i: int, profit: float, mae: float, mfe: float) -> Trade:
    e = _BASE + timedelta(hours=i)
    return Trade("F_001", "all", e, e + timedelta(minutes=10), profit, mae, mfe)


def _sim(trades, **kw):
    defaults = dict(
        profile=_profile(), account_type="evaluation",
        account_size="50000", starting_balance=50000.0,
    )
    defaults.update(kw)
    return simulate_survival(trades, **defaults)


def test_benign_sequence_survives():
    trades = [_t(i, profit=100.0, mae=50.0, mfe=200.0) for i in range(10)]
    r = _sim(trades)
    assert r.survived
    assert r.violated_dt is None
    assert r.min_cushion > 0
    assert r.final_equity == pytest.approx(51000.0)


def test_initial_drawdown_breach():
    # MAE of 2600 from the 50000 start drops equity to 47400 < threshold 47500 -> dead.
    r = _sim([_t(0, profit=0.0, mae=2600.0, mfe=0.0)])
    assert not r.survived
    assert r.violated_dt is not None
    assert r.n_trades_taken == 1


def test_trailing_threshold_breach_above_start():
    # Win raises peak to ~52000 (threshold trails up to 49500), then a later trade's
    # MAE pulls equity to 49400 < 49500 -> violated even though still above 50000 start.
    win = _t(0, profit=2000.0, mae=0.0, mfe=2000.0)         # peak 52000, threshold 49500, then settle 52000
    drop = Trade("F_001", "all", _BASE + timedelta(hours=1),
                 _BASE + timedelta(hours=1, minutes=10), profit=0.0, mae=2600.0, mfe=0.0)
    r = _sim([win, drop])
    assert not r.survived                      # trailing nature, not absolute
    assert r.peak_equity >= 52000.0


def test_reaches_profit_target():
    # +3000 net clears the 53000 target.
    trades = [_t(i, profit=1000.0, mae=10.0, mfe=1200.0) for i in range(3)]
    r = _sim(trades)
    assert r.survived
    assert r.reached_target
    assert r.reached_target_dt is not None


def test_pass_at_target_stops_before_later_blowup():
    # Hit +3000 realized (passes the challenge, stops), then a would-be fatal trade
    # that is never taken because the eval already cleared.
    win = _t(0, profit=3000.0, mae=100.0, mfe=3200.0)            # realized 53000 -> passed
    fatal = Trade("F_001", "all", _BASE + timedelta(hours=1),
                  _BASE + timedelta(hours=1, minutes=10), profit=0.0, mae=9000.0, mfe=0.0)
    r = _sim([win, fatal], stop_at_target=True)
    assert r.passed
    assert r.survived                       # passed evals count as survived
    assert r.n_trades_taken == 1            # stopped after clearing the target
    # Without the stop, the second trade blows it up.
    r2 = _sim([win, fatal], stop_at_target=False)
    assert not r2.survived


def test_lock_freezes_threshold():
    # Peak to 52600 locks the threshold at start+100 = 50100; a later 2000 drawdown
    # (equity 50600) stays above the locked floor -> survives.
    win = _t(0, profit=2700.0, mae=0.0, mfe=2700.0)        # peak/settle 52700 -> locks
    drop = Trade("F_001", "all", _BASE + timedelta(hours=1),
                 _BASE + timedelta(hours=1, minutes=10), profit=-2000.0, mae=2100.0, mfe=0.0)
    r = _sim([win, drop])
    assert r.locked
    assert r.survived                          # 50700 low > 50100 locked floor


def test_dollar_scale_mnq_survives_where_nq_dies():
    # A single NQ trade with a 2600 adverse excursion kills the account at full scale
    # but survives at MNQ (0.1x -> 260 excursion).
    trades = [_t(0, profit=300.0, mae=2600.0, mfe=400.0)]
    assert not _sim(trades, dollar_scale=1.0).survived
    assert _sim(trades, dollar_scale=0.1).survived


def test_contracts_multiply_risk():
    trades = [_t(0, profit=100.0, mae=1300.0, mfe=200.0)]  # 1300 ok, 2x=2600 breaches
    assert _sim(trades, contracts=1).survived
    assert not _sim(trades, contracts=2).survived


def test_halt_on_violation_stops_trading():
    trades = [_t(0, profit=0.0, mae=2600.0, mfe=0.0)] + [_t(i, 100.0, 10.0, 200.0) for i in range(1, 5)]
    r = _sim(trades, halt_on_violation=True)
    assert not r.survived
    assert r.n_trades_taken == 1               # stopped after the blow-up
    assert r.n_trades == 5


def test_per_template_sweep_counts():
    tt = {
        "F_safe": [_t(i, 100.0, 50.0, 200.0) for i in range(5)],
        "F_dead": [_t(0, 0.0, 2600.0, 0.0)],
    }
    sweep = per_template_survival(
        tt, profile=_profile(), account_type="evaluation",
        account_size="50000", starting_balance=50000.0,
    )
    assert sweep.n_templates == 2
    assert sweep.n_survived == 1
    assert sweep.results["F_safe"].survived
    assert not sweep.results["F_dead"].survived


def test_lineup_merges_chronologically():
    a = [_t(0, 100.0, 50.0, 200.0), _t(4, 100.0, 50.0, 200.0)]
    b = [_t(2, -50.0, 80.0, 100.0)]
    r = lineup_survival(
        a + b, profile=_profile(), account_type="evaluation",
        account_size="50000", starting_balance=50000.0,
    )
    assert r.n_trades == 3
    assert r.survived


def test_integration_real_apex_profile():
    prof = load_firm_profile("APEX")
    trades = [_t(i, 200.0, 100.0, 400.0) for i in range(8)]
    r = simulate_survival(
        trades, profile=prof, account_type="evaluation",
        account_size="50000", starting_balance=50000.0, dollar_scale=0.1,
    )
    assert r.survived
    assert r.starting_balance == 50000.0
