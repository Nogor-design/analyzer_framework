"""Selector spine tests. The non-negotiable one is leakage: the walk-forward
replay must rank picks on train days only, never on the test day it scores."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from ta_foundation.analysis.selection import (
    Candidate,
    SelectionContext,
    compare_selectors,
    daily_metrics,
    replay_selector,
)
from ta_foundation.analysis.selection.baselines import (
    DEFAULT_BASELINES,
    equal_weight,
    regime_matched,
    top_pf,
)

D0 = date(2026, 5, 1)


def days(n: int) -> list[date]:
    return [D0 + timedelta(days=i) for i in range(n)]


def cand(tid: str, slice_key: str, pnl_by_offset: dict[int, float]) -> Candidate:
    return Candidate(
        template_id=tid,
        slice_key=slice_key,
        daily_pnl={D0 + timedelta(days=k): v for k, v in pnl_by_offset.items()},
    )


# ---- daily_metrics --------------------------------------------------------

def test_daily_metrics_basic():
    m = daily_metrics([100.0, -50.0, 0.0, 100.0])
    assert m["net"] == 150.0
    assert m["mean_daily"] == pytest.approx(37.5)
    assert m["daily_pf"] == pytest.approx(200.0 / 50.0)
    assert m["worst_day"] == -50.0
    assert m["n_active_days"] == 3


def test_daily_metrics_max_drawdown():
    # cum: 100, 60, -40, 60 -> deepest trough from peak 100 is -140
    m = daily_metrics([100.0, -40.0, -100.0, 100.0])
    assert m["max_drawdown"] == pytest.approx(-140.0)


def test_daily_metrics_no_losses_pf_inf():
    assert daily_metrics([10.0, 20.0])["daily_pf"] == float("inf")
    assert daily_metrics([])["daily_pf"] is None


# ---- baselines ------------------------------------------------------------

def test_top_pf_ranks_on_train_window():
    good = cand("good", "S", {0: 100, 1: 100, 2: 90})
    bad = cand("bad", "S", {0: -100, 1: 50, 2: -80})
    ctx = SelectionContext(train_days=days(3), test_day=D0 + timedelta(days=3))
    assert [c.template_id for c in top_pf([good, bad], ctx)] == ["good"]


def test_equal_weight_returns_all():
    a = cand("a", "S", {0: 1})
    b = cand("b", "S", {0: 2})
    ctx = SelectionContext(train_days=days(1), test_day=D0 + timedelta(days=1))
    assert {c.template_id for c in equal_weight([a, b], ctx)} == {"a", "b"}


def test_regime_matched_picks_regime_fit_template():
    # trend lover wins on trend days; range lover wins on range days.
    trend = cand("trend", "S", {0: 100, 1: 100, 2: -10, 3: -10})
    rng = cand("range", "S", {0: -10, 1: -10, 2: 100, 3: 100})
    regime_by_day = {D0: "trend", D0 + timedelta(days=1): "trend",
                     D0 + timedelta(days=2): "range", D0 + timedelta(days=3): "range"}
    ctx = SelectionContext(
        train_days=days(4), test_day=D0 + timedelta(days=4),
        regime_for_test_day="range", regime_by_day=regime_by_day,
    )
    assert [c.template_id for c in regime_matched([trend, rng], ctx)] == ["range"]


# ---- replay (leakage is the headline) -------------------------------------

def test_replay_no_leakage():
    # A: positive on train days, NEGATIVE on the test day.
    # B: negative on train days, POSITIVE on the test day.
    # A leak-free top_pf must pick A (great on train) and eat A's bad test day.
    # A leaky harness would pick B and look brilliant -> this asserts honesty.
    train = {i: 100.0 for i in range(10)}
    a = cand("A", "S", {**train, 10: -500.0})
    b = cand("B", "S", {**{i: -100.0 for i in range(10)}, 10: 500.0})
    summary = replay_selector([a, b], top_pf, train_min_days=10)
    assert summary["n_test_days"] == 1
    assert summary["net"] == -500.0  # picked A, realised A's test day
    assert summary["picks"][0]["picks"] == ["A"]


def test_replay_determinism():
    a = cand("A", "S", {i: float(i % 3) for i in range(20)})
    b = cand("B", "S", {i: float((i + 1) % 4) for i in range(20)})
    s1 = replay_selector([a, b], top_pf, train_min_days=10)
    s2 = replay_selector([a, b], top_pf, train_min_days=10)
    assert s1["net"] == s2["net"] and s1["picks"] == s2["picks"]


def test_replay_equal_weight_averages_slice():
    a = cand("A", "S", {i: 10.0 for i in range(12)})
    b = cand("B", "S", {i: 30.0 for i in range(12)})
    summary = replay_selector([a, b], equal_weight, train_min_days=10)
    # each test day realises mean(10, 30) = 20 across the 2 test days
    assert summary["expectancy_daily"] == pytest.approx(20.0)
    assert summary["n_test_days"] == 2


def test_replay_sums_across_slices():
    a = cand("A", "morning", {i: 10.0 for i in range(12)})
    b = cand("B", "afternoon", {i: 5.0 for i in range(12)})
    summary = replay_selector([a, b], top_pf, train_min_days=10)
    # one pick per slice per day -> 10 + 5 = 15 each test day
    assert summary["expectancy_daily"] == pytest.approx(15.0)


def test_compare_selectors_runs_all_baselines():
    cands = [
        cand("A", "S", {i: float((i % 5) - 2) * 10 for i in range(20)}),
        cand("B", "S", {i: float((i % 3) - 1) * 15 for i in range(20)}),
    ]
    table = compare_selectors(cands, DEFAULT_BASELINES, train_min_days=10)
    assert set(table) == set(DEFAULT_BASELINES)
    for summary in table.values():
        assert summary["n_test_days"] == 10
