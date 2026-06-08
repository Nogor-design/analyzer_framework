"""Allocator (2c) table tests — objective branching + budget-bounded sizing."""
from __future__ import annotations

import pytest

from ta_foundation.analysis.risk import (
    RiskReadout,
    allocate_account,
    estimate_per_contract_risk,
)


def _readout(budget, *, violated=False):
    return RiskReadout(
        threshold=0.0, peak_balance=0.0, locked=False, violated=violated,
        remaining_cushion=budget, daily_risk_budget=budget,
    )


def test_estimate_per_contract_risk_robust():
    # worst day -500 but typical loss small -> uses the smaller (typical) tail, floored
    r = estimate_per_contract_risk([100, -50, 80, -500, 60, -40])
    # losers mean = (-50-500-40)/3 = -196.67 -> typ = 393.3; worst = 500 -> min = 393.3
    assert r == pytest.approx(393.333, abs=1e-2)
    assert estimate_per_contract_risk([]) == 1.0          # empty -> floor
    # all-positive (no losing days): no tail to size, falls back to |min day| (=5), floored
    assert estimate_per_contract_risk([10, 20, 5]) == 5.0


def test_no_budget_when_violated_means_do_not_trade():
    plan = allocate_account(
        [("ny", "A")], readout=_readout(0.0, violated=True),
        account_type="PA", account_size="50000",
        per_contract_risk={"A": 100.0})
    assert plan.allocations == [] and plan.daily_loss_cap == 0.0
    assert "do not trade" in plan.note


def test_eval_presses_highest_expectancy_first():
    # budget 1000, eval utilization 0.8 -> cap 800. Two picks at risk 300/contract.
    picks = [("s1", "LOW"), ("s2", "HIGH")]
    plan = allocate_account(
        picks, readout=_readout(1000.0), account_type="evaluation", account_size="50000",
        per_contract_risk={"LOW": 300.0, "HIGH": 300.0},
        expected_return={"LOW": 50.0, "HIGH": 200.0})
    # cap 800 -> HIGH gets floor(800/300)=2 (600), then LOW floor(200/300)=0 -> skipped
    first = plan.allocations[0]
    assert first.template_id == "HIGH" and first.contracts == 2
    assert "LOW" in plan.skipped


def test_pa_fills_safest_first_and_uses_less_budget():
    # same 1000 budget; PA utilization 0.4 -> cap 400. SAFE risk 100, RISKY risk 300.
    picks = [("s1", "RISKY"), ("s2", "SAFE")]
    plan = allocate_account(
        picks, readout=_readout(1000.0), account_type="PA", account_size="50000",
        per_contract_risk={"RISKY": 300.0, "SAFE": 100.0})
    assert plan.daily_loss_cap == pytest.approx(400.0)
    # SAFE first: floor(400/100)=4 (400 used) -> RISKY can't afford -> skipped
    assert plan.allocations[0].template_id == "SAFE" and plan.allocations[0].contracts == 4
    assert "RISKY" in plan.skipped


def test_max_contracts_caps_size():
    plan = allocate_account(
        [("s", "A")], readout=_readout(10000.0), account_type="evaluation",
        account_size="50000", per_contract_risk={"A": 100.0}, max_contracts=3)
    assert plan.allocations[0].contracts == 3       # capped despite ample budget


def test_budget_too_small_for_any_pick():
    plan = allocate_account(
        [("s", "A")], readout=_readout(50.0), account_type="evaluation",
        account_size="50000", per_contract_risk={"A": 100.0})
    assert plan.allocations == []
    assert "too small" in plan.note


def test_allocate_roster_branches_per_account():
    from ta_foundation.analysis.risk import AccountSpec, allocate_roster
    picks = [("s1", "SAFE"), ("s2", "RISKY")]
    pcr = {"SAFE": 200.0, "RISKY": 600.0}
    exp = {"SAFE": 50.0, "RISKY": 300.0}
    roster = [
        AccountSpec("eval-50k", "apex", "50000", "evaluation", 50000),   # flat -> cushion 2500
        AccountSpec("pa-50k", "apex", "50000", "PA", 50000),
        AccountSpec("pa-100k", "apex", "100000", "PA", 100000),          # bigger cushion (3000)
    ]
    plans = allocate_roster(picks, roster, per_contract_risk=pcr, expected_return=exp)
    assert set(plans) == {"eval-50k", "pa-50k", "pa-100k"}
    # eval presses RISKY (highest expectancy) first; PA fills SAFE first
    assert plans["eval-50k"].allocations[0].template_id == "RISKY"
    assert plans["pa-50k"].allocations[0].template_id == "SAFE"
    # eval uses more of its budget than the same-size PA
    assert plans["eval-50k"].daily_loss_cap > plans["pa-50k"].daily_loss_cap
