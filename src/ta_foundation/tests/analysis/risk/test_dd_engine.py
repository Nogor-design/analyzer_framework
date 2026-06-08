"""Trailing-drawdown engine table tests (Phase 2b core math)."""
from __future__ import annotations

import pytest

from ta_foundation.analysis.risk import (
    DdEngine,
    FirmProfile,
    SizeRules,
    load_firm_profile,
)


# ---- APEX intraday profile (the live case) ----------------------------------

def _apex_engine():
    return DdEngine(load_firm_profile("apex"))


def test_apex_profile_numbers_loaded():
    p = load_firm_profile("apex")
    assert p.drawdown_type == "intraday_trailing" and p.includes_unrealized is True
    assert p.lock_buffer == 100
    assert p.size_rules(50000).max_drawdown == 2500
    assert p.size_rules(100000).max_drawdown == 3000
    assert p.size_rules(150000).max_drawdown == 5000
    assert p.daily_loss_limit is None


def test_intraday_init_and_trail():
    eng = _apex_engine()
    st = eng.init_account(starting_balance=50000, account_type="evaluation", account_size=50000)
    assert st.current_threshold == 47500          # start - max_dd
    r = eng.on_value(st, 50200)
    assert r.threshold == 47700                    # trails up: 50200 - 2500
    assert r.remaining_cushion == pytest.approx(2500)
    assert r.distance_to_lock == pytest.approx(2400)   # (50000+2500+100) - 50200
    assert r.locked is False


def test_intraday_locks_at_start_plus_buffer_and_stays():
    eng = _apex_engine()
    st = eng.init_account(starting_balance=50000, account_type="PA", account_size=50000)
    r = eng.on_value(st, 52600)                    # peak hits start+max_dd+buffer
    assert r.locked is True
    assert r.threshold == 50100                    # start + lock_buffer, frozen
    assert r.distance_to_lock is None
    # pull back hard: threshold must NOT move back down
    r2 = eng.on_value(st, 50500)
    assert r2.locked is True and r2.threshold == 50100


def test_intraday_threshold_never_loosens_before_lock():
    eng = _apex_engine()
    st = eng.init_account(starting_balance=50000, account_type="evaluation", account_size=50000)
    eng.on_value(st, 51000)                        # threshold -> 48500
    r = eng.on_value(st, 50300)                    # lower equity, not a new peak
    assert r.threshold == 48500                    # ratchet held


def test_intraday_violation_on_touch():
    eng = _apex_engine()
    st = eng.init_account(starting_balance=50000, account_type="evaluation", account_size=50000)
    r = eng.on_value(st, 47500)                    # equity touches initial threshold
    assert r.violated is True


def test_evaluation_progress_vs_pa_none():
    eng = _apex_engine()
    ev = eng.init_account(starting_balance=50000, account_type="evaluation", account_size=50000)
    r = eng.on_value(ev, 51500)                    # +1500 toward 3000 target
    assert r.progress_to_target == pytest.approx(0.5)
    pa = eng.init_account(starting_balance=50000, account_type="PA", account_size=50000)
    assert eng.on_value(pa, 51500).progress_to_target is None


def test_daily_risk_budget_is_cushion_minus_margin():
    eng = _apex_engine()
    st = eng.init_account(starting_balance=50000, account_type="PA", account_size=50000)
    eng.on_value(st, 50200)                        # cushion 2500
    assert eng.readout(st, safety_margin=500).daily_risk_budget == pytest.approx(2000)


# ---- synthetic EOD profile (daily recalc + daily-loss limit) ----------------

def _eod_engine():
    profile = FirmProfile(
        firm="TestEOD", version="1", drawdown_type="eod_trailing",
        includes_unrealized=False, lock_buffer=0,
        account_sizes={"50000": SizeRules(max_drawdown=2000, profit_target=3000, max_contracts=5)},
        daily_loss_limit=1000,
    )
    return DdEngine(profile)


def test_eod_threshold_only_trails_on_close():
    eng = _eod_engine()
    st = eng.init_account(starting_balance=50000, account_type="evaluation", account_size=50000)
    assert st.current_threshold == 48000
    r_intraday = eng.on_value(st, 50500)           # intraday spike does NOT trail an EOD account
    assert r_intraday.threshold == 48000
    assert r_intraday.distance_to_lock is None      # not an intraday-trailing account
    r_close = eng.on_session_close(st, 50500)       # close trails: 50500 - 2000
    assert r_close.threshold == 48500


def test_eod_daily_loss_limit_remaining_and_budget_cap():
    eng = _eod_engine()
    st = eng.init_account(starting_balance=50000, account_type="evaluation", account_size=50000)
    eng.on_value(st, 50500)                         # cushion 2500
    eng.add_realized(st, -600)                      # lost 600 today
    r = eng.readout(st)
    assert r.daily_loss_limit_remaining == pytest.approx(400)   # 1000 - 600
    assert r.daily_risk_budget == pytest.approx(400)            # cushion capped by DLL
    eng.on_session_close(st, 50500)                 # close resets the daily tally
    assert eng.readout(st).daily_loss_limit_remaining == pytest.approx(1000)
