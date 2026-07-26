"""Outcome-ledger + grader tests (Phase-0 accountable lineup record)."""
from __future__ import annotations

from datetime import date

from ta_foundation.analysis.selection.baselines import equal_weight, top_pf
from ta_foundation.analysis.selection.grade import (
    grade_against_baselines,
    track_record,
)
from ta_foundation.analysis.selection.ledger import (
    DayActuals,
    LineupPick,
    LineupRecommendation,
    OutcomeLedger,
)
from ta_foundation.analysis.selection.model import Candidate


def _rec(day, picks, version="composite_v1", regime=None):
    return LineupRecommendation(
        for_day=day, selector_version=version, regime=regime,
        picks=[LineupPick(slice_key=s, template_id=t) for (s, t) in picks],
    )


def test_ledger_id_defaults_and_roundtrip(tmp_path):
    led = OutcomeLedger(tmp_path / "l.jsonl")
    rec = _rec(date(2026, 6, 1), [("ny_open", "T_A")])
    assert rec.ledger_id == "composite_v1:2026-06-01"
    assert led.record_recommendation(rec) is True
    # idempotent: same ledger_id not duplicated
    assert led.record_recommendation(rec) is False
    got = led.recommendations()[rec.ledger_id]
    assert got.for_day == date(2026, 6, 1)
    assert got.picks[0].template_id == "T_A"


def test_actuals_last_write_wins(tmp_path):
    led = OutcomeLedger(tmp_path / "l.jsonl")
    rec = _rec(date(2026, 6, 1), [("ny_open", "T_A")])
    led.record_recommendation(rec)
    led.record_actuals(DayActuals(rec.ledger_id, {"T_A": 100.0}))
    led.record_actuals(DayActuals(rec.ledger_id, {"T_A": 150.0}))  # correction
    assert led.actuals()[rec.ledger_id].realized_by_template["T_A"] == 150.0


def test_track_record_realizes_equal_weight_per_slice(tmp_path):
    led = OutcomeLedger(tmp_path / "l.jsonl")
    # one slice, two picks -> realised = mean of the two templates' P&L
    rec = _rec(date(2026, 6, 1), [("ny_open", "T_A"), ("ny_open", "T_B")])
    led.record_recommendation(rec)
    led.record_actuals(DayActuals(rec.ledger_id, {"T_A": 200.0, "T_B": 0.0}))
    tr = track_record(led)
    assert tr["n_graded"] == 1 and tr["n_pending"] == 0
    assert tr["net"] == 100.0  # (200 + 0) / 2


def test_track_record_counts_pending(tmp_path):
    led = OutcomeLedger(tmp_path / "l.jsonl")
    led.record_recommendation(_rec(date(2026, 6, 1), [("s", "T_A")]))
    led.record_recommendation(_rec(date(2026, 6, 2), [("s", "T_A")]))
    led.record_actuals(DayActuals("composite_v1:2026-06-01", {"T_A": 50.0}))
    tr = track_record(led)
    assert tr["n_graded"] == 1 and tr["n_pending"] == 1


def test_grade_against_baselines_same_days(tmp_path):
    led = OutcomeLedger(tmp_path / "l.jsonl")
    days = [date(2026, 6, d) for d in range(1, 6)]
    # universe: T_A steady winner, T_B noisy. Issue a lineup of just T_A on day 5.
    a = Candidate("T_A", "s", {d: 100.0 for d in days})
    b = Candidate("T_B", "s", {days[0]: 300.0, days[1]: -200.0, days[2]: 10.0,
                               days[3]: -50.0, days[4]: -10.0})
    rec = _rec(days[4], [("s", "T_A")])
    led.record_recommendation(rec)
    led.record_actuals(DayActuals(rec.ledger_id, {"T_A": 100.0, "T_B": -10.0}))

    out = grade_against_baselines(led, [a, b], baselines={"top_pf": top_pf, "equal_weight": equal_weight})
    assert out["as_issued"]["net"] == 100.0          # we ran T_A -> +100
    assert out["top_pf"]["net"] == 100.0             # top_pf also lands on T_A
    assert out["equal_weight"]["net"] == 45.0        # mean(100, -10) on day 5
