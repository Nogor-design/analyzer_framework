from __future__ import annotations

"""Tests for the walk-forward web engine."""

import json
from pathlib import Path

import pytest

from ta_foundation.web import optimizer_session as opt_session
from ta_foundation.web.optimizer_walkforward import (
    OptimizerWalkForwardError,
    generate_walk_forward_templates,
    ingest_walk_forward_results,
    trigger_walk_forward_run,
    walk_forward_status,
)


NAMED_BACKTEST_XML = """<?xml version="1.0" encoding="utf-8"?>
<StrategyTemplate>
  <StrategyType>NinjaTrader.NinjaScript.Strategies.FakeStrategy</StrategyType>
  <From>2026-04-01T00:00:00</From>
  <To>2026-04-30T00:00:00</To>
  <Strategy>
    <FakeStrategy>
      <InstrumentOrInstrumentList>NQ 06-26</InstrumentOrInstrumentList>
    </FakeStrategy>
  </Strategy>
</StrategyTemplate>
"""


@pytest.fixture(autouse=True)
def isolate_storage(tmp_path: Path):
    opt_session.set_storage_root(tmp_path / "sessions")
    yield
    opt_session.set_storage_root(None)


def _seeded(tmp_path: Path, *, with_final_results: bool = False):
    session = opt_session.create_session(
        label="wf test",
        strategy_id="FakeStrategy",
        seed_template_path=str(tmp_path / "seed.xml"),
        instrument="NQ 06-26",
    )
    session.update(oos_from_date="2026-04-14", oos_to_date="2026-05-14")
    named = (session.directory / "deployment_package" / "final_backtest_handoff"
             / "named_backtest_templates" / "breakout")
    named.mkdir(parents=True)
    for rank in (1, 2):
        (named / f"{rank:02d}_Breakout_FakeStrategy.xml").write_text(
            NAMED_BACKTEST_XML, encoding="utf-8"
        )
    if with_final_results:
        results = (session.directory / "deployment_package" / "final_backtest_handoff"
                   / "nt8_backtest_results")
        for run_id, profits in [("F_001", [100, 100, -50]), ("F_002", [50, 50, 50])]:
            (results / run_id).mkdir(parents=True)
            (results / run_id / "Trades.csv").write_text(
                _trades_csv(profits, "4/15/2026"), encoding="utf-8",
            )
    return session


def _trades_csv(profits, date_str):
    header = (
        "Trade number,Instrument,Account,Strategy,Market pos.,Qty,Entry price,Exit price,"
        "Entry time,Exit time,Entry name,Exit name,Profit,Cum. net profit,Commission,"
        "Clearing Fee,Exchange Fee,IP Fee,NFA Fee,MAE,MFE,ETD,Bars,\n"
    )
    lines = []
    cum = 0
    for i, p in enumerate(profits):
        cum += p
        profit_str = f"(${abs(p):.2f})" if p < 0 else f"${p:.2f}"
        cum_str = f"(${abs(cum):.2f})" if cum < 0 else f"${cum:.2f}"
        lines.append(
            f"{i},NQ 06-26,Backtest,Strat,Long,1,100,101,"
            f"{date_str} 9:00:00 AM,{date_str} 10:00:00 AM,Buy,Profit,"
            f"{profit_str},{cum_str},$0.00,$0.00,$0.00,$0.00,$0.00,$0.00,$0.00,$0.00,60,"
        )
    return header + "\n".join(lines) + "\n"


def _summary_csv(start_date, end_date):
    return (
        f"Performance,All trades,Long trades,Short trades,\n"
        f"Start date,{start_date},,,\n"
        f"End date,{end_date},,,\n"
        f"Total # of trades,0,0,0,\n"
    )


def test_generate_creates_one_template_per_candidate_per_window(tmp_path: Path):
    session = _seeded(tmp_path)
    result = generate_walk_forward_templates(
        session,
        anchor_date="2026-04-01",  # well before the OOS window, no overlap to skip
        window_days=10,
        count=3,
        gap_days=0,
        skip_is_window=False,
    )
    assert len(result.windows) == 3
    # 2 candidates * 3 windows
    assert len(result.templates) == 6
    # Each template has both From/To patched
    for t in result.templates:
        xml = Path(t.output_template).read_text(encoding="utf-8")
        assert f"<From>{t.from_date}T00:00:00</From>" in xml
        assert f"<To>{t.to_date}T00:00:00</To>" in xml


def test_generate_skips_windows_overlapping_oos(tmp_path: Path):
    session = _seeded(tmp_path)  # oos = 2026-04-14 to 2026-05-14
    result = generate_walk_forward_templates(
        session,
        anchor_date="2026-05-01",
        window_days=10,
        count=3,
        gap_days=0,
        skip_is_window=True,  # filter overlap with oos
    )
    # Anchor 2026-05-01, window=10, count=3 (chronological order):
    #   W0: 2026-04-01..2026-04-11 — no overlap with OOS
    #   W1: 2026-04-11..2026-04-21 — overlaps OOS, dropped
    #   W2: 2026-04-21..2026-05-01 — overlaps OOS, dropped
    # Planner returns the single surviving (non-overlapping) window.
    assert len(result.windows) == 1
    assert (result.windows[0].from_date, result.windows[0].to_date) == \
        ("2026-04-01", "2026-04-11")


def test_generate_with_run_id_filter(tmp_path: Path):
    session = _seeded(tmp_path)
    result = generate_walk_forward_templates(
        session,
        anchor_date="2026-04-01", window_days=10, count=2,
        candidate_run_ids=["F_001"],
        skip_is_window=False,
    )
    runs = {t.candidate_run_id for t in result.templates}
    assert runs == {"F_001"}
    assert len(result.templates) == 2


def test_trigger_writes_command_file_with_instrument(tmp_path: Path):
    session = _seeded(tmp_path)
    generate_walk_forward_templates(
        session, anchor_date="2026-04-01", window_days=10, count=2,
        skip_is_window=False,
    )
    cmd_path = tmp_path / "nt8_command.json"
    info = trigger_walk_forward_run(session, command_file=cmd_path)
    payload = json.loads(cmd_path.read_text(encoding="utf-8"))
    assert payload["action"] == "RunBatch"
    assert payload["instrument"] == "NQ 06-26"
    assert payload["runId"].startswith("wf_")
    assert info["command_file"] == str(cmd_path)


def test_trigger_errors_when_no_templates(tmp_path: Path):
    session = _seeded(tmp_path)
    cmd_path = tmp_path / "nt8_command.json"
    with pytest.raises(OptimizerWalkForwardError):
        trigger_walk_forward_run(session, command_file=cmd_path)


def test_status_before_run_reports_no_results(tmp_path: Path):
    session = _seeded(tmp_path)
    generate_walk_forward_templates(
        session, anchor_date="2026-04-01", window_days=10, count=2,
        skip_is_window=False,
    )
    status = walk_forward_status(session)
    assert status.template_count == 4  # 2 candidates * 2 windows
    assert status.nt_output_present is False
    assert status.candidate_stability == []


def test_ingest_produces_per_window_stability(tmp_path: Path):
    session = _seeded(tmp_path, with_final_results=True)
    gen = generate_walk_forward_templates(
        session, anchor_date="2026-04-01", window_days=10, count=2,
        candidate_run_ids=["F_001"], skip_is_window=False,
    )
    # Simulate NT having dispatched both windows for F_001
    wf_out = session.directory / "deployment_package" / "walkforward" / "nt_output"
    for w in gen.windows:
        out = wf_out / f"F_001__W{w.index:02d}"
        out.mkdir(parents=True)
        # Window 0 produces a winning trade set, window 1 a losing one.
        profits = [100, -50, 100] if w.index == 0 else [-100, 50, -75]
        (out / "Trades.csv").write_text(_trades_csv(profits, "3/15/2026"), encoding="utf-8")
        (out / "Summary.csv").write_text(_summary_csv(w.from_date, w.to_date), encoding="utf-8")

    status = ingest_walk_forward_results(session)
    assert status.nt_output_present is True
    assert len(status.per_window_results) == 2
    assert len(status.candidate_stability) == 1
    f1 = status.candidate_stability[0]
    assert f1.candidate_run_id == "F_001"
    assert f1.windows_run == 2
    assert f1.windows_with_trades == 2
    # Window 0 PF = 200/50 = 4.0 > 1; window 1 PF = 50/175 ≈ 0.29 < 1.
    assert f1.windows_with_pf_above_1 == 1
    assert any("only 1/2 windows" in flag for flag in f1.stability_flags)

    # Stability files produced
    pkg = session.directory / "deployment_package" / "walkforward"
    assert (pkg / "stability.json").exists()
    assert (pkg / "stability.md").exists()


def test_ingest_handles_missing_results(tmp_path: Path):
    session = _seeded(tmp_path, with_final_results=True)
    gen = generate_walk_forward_templates(
        session, anchor_date="2026-04-01", window_days=10, count=2,
        candidate_run_ids=["F_001"], skip_is_window=False,
    )
    wf_out = session.directory / "deployment_package" / "walkforward" / "nt_output"
    # Only window 0 has results; window 1 dir exists but no Trades.csv.
    (wf_out / "F_001__W00").mkdir(parents=True)
    (wf_out / "F_001__W00" / "Trades.csv").write_text(
        _trades_csv([100, 100, -50], "3/15/2026"), encoding="utf-8"
    )
    (wf_out / "F_001__W00" / "Summary.csv").write_text(
        _summary_csv("2026-03-21", "2026-03-31"), encoding="utf-8"
    )
    (wf_out / "F_001__W01").mkdir(parents=True)

    status = ingest_walk_forward_results(session)
    assert len(status.per_window_results) == 1
    assert any("Trades.csv missing" in n for n in status.notes)
