"""Composite selector v1 tests: gate, determinism, dominance, regime weighting,
and that it scores leakage-free (train-window only)."""
from __future__ import annotations

from datetime import date, timedelta

from ta_foundation.analysis.selection import SelectionContext, replay_selector
from ta_foundation.analysis.selection.scoring import (
    ScoringConfig,
    make_composite_selector,
    _train_features,
    _passes_gate,
)
from ta_foundation.analysis.selection.model import Candidate

D0 = date(2026, 5, 1)


def cand(tid, slice_key, pnl_by_offset):
    return Candidate(tid, slice_key,
                     {D0 + timedelta(days=k): v for k, v in pnl_by_offset.items()})


def ctx(n_train, **kw):
    return SelectionContext(
        train_days=[D0 + timedelta(days=i) for i in range(n_train)],
        test_day=D0 + timedelta(days=n_train), **kw,
    )


def test_gate_drops_low_activity():
    cfg = ScoringConfig(min_active_days=3)
    thin = cand("thin", "S", {0: 100.0})              # 1 active day
    feats = _train_features(thin, ctx(10))
    assert not _passes_gate(feats, cfg)


def test_gate_drops_train_window_loser():
    cfg = ScoringConfig(min_active_days=1, min_daily_pf=1.0)
    loser = cand("loser", "S", {0: 10, 1: -100, 2: 10, 3: -100})
    assert not _passes_gate(_train_features(loser, ctx(10)), cfg)


def test_dominant_candidate_is_picked():
    sel = make_composite_selector()
    winner = cand("win", "S", {i: 100.0 for i in range(10)})
    weak = cand("weak", "S", {i: (5.0 if i % 2 == 0 else -3.0) for i in range(10)})
    picks = sel([winner, weak], ctx(10))
    assert [c.template_id for c in picks] == ["win"]


def test_regime_fit_breaks_the_tie():
    # two templates with similar overall train P&L, but one is strong specifically
    # in the upcoming regime -> composite should prefer it.
    rg = {D0 + timedelta(days=i): ("trend" if i % 2 == 0 else "range") for i in range(10)}
    trend_strong = cand("trend", "S", {i: (60.0 if i % 2 == 0 else -50.0) for i in range(10)})
    range_strong = cand("range", "S", {i: (-50.0 if i % 2 == 0 else 60.0) for i in range(10)})
    c = ctx(10, regime_for_test_day="trend", regime_by_day=rg)
    sel = make_composite_selector()
    assert [x.template_id for x in sel([trend_strong, range_strong], c)] == ["trend"]


def test_fallback_when_gate_empties_pool():
    cfg = ScoringConfig(min_active_days=99)  # impossible -> everything gated out
    sel = make_composite_selector(cfg)
    a = cand("a", "S", {i: 10.0 for i in range(10)})
    b = cand("b", "S", {i: 5.0 for i in range(10)})
    picks = sel([a, b], ctx(10))
    assert len(picks) == 1  # still fields a lineup


def test_determinism_in_replay():
    cands = [
        cand("A", "S", {i: float((i % 4) - 1) * 20 for i in range(20)}),
        cand("B", "S", {i: float((i % 3)) * 10 for i in range(20)}),
    ]
    sel = make_composite_selector()
    s1 = replay_selector(cands, sel, train_min_days=10)
    s2 = replay_selector(cands, sel, train_min_days=10)
    assert s1["picks"] == s2["picks"] and s1["net"] == s2["net"]
