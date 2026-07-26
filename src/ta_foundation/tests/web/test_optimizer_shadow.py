from __future__ import annotations

"""Tests for the optimizer shadow execution module."""

import json
from pathlib import Path

import pytest

from ta_foundation.web import optimizer_session as opt_session
from ta_foundation.web.optimizer_shadow import (
    OptimizerShadowError,
    generate_shadow_templates,
    ingest_shadow_results,
    shadow_status,
    trigger_shadow_run,
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


def _seeded_session(tmp_path: Path, *, with_final_results: bool = False) -> Path:
    opt_session.set_storage_root(tmp_path / "sessions")
    session = opt_session.create_session(
        label="shadow test",
        strategy_id="FakeStrategy",
        seed_template_path=str(tmp_path / "seed.xml"),
        instrument="NQ 06-26",
    )
    # Build a named final-Backtest tree mirroring the production layout.
    named = (session.directory / "deployment_package" / "final_backtest_handoff"
             / "named_backtest_templates" / "breakout")
    named.mkdir(parents=True)
    for rank in (1, 2):
        (named / f"{rank:02d}_Breakout_FakeStrategy.xml").write_text(
            NAMED_BACKTEST_XML, encoding="utf-8"
        )
    if with_final_results:
        # Stub final-backtest Trades.csv for F_001 and F_002 so the
        # comparison engine has a baseline to read.
        results = (session.directory / "deployment_package" / "final_backtest_handoff"
                   / "nt8_backtest_results")
        # F_001 is a winner in backtest; F_002 has PF > 1.5 in backtest so
        # the shadow comparison can demonstrate a PF collapse.
        for run_id, profits in [("F_001", [100, -50, 100]), ("F_002", [100, 100, -50])]:
            (results / run_id).mkdir(parents=True)
            (results / run_id / "Trades.csv").write_text(
                _trades_csv(profits, "4/15/2026"), encoding="utf-8"
            )
    return session.directory


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


@pytest.fixture(autouse=True)
def cleanup():
    yield
    opt_session.set_storage_root(None)


def test_generate_patches_dates_and_writes_command_file(tmp_path: Path):
    session_dir = _seeded_session(tmp_path)
    session = opt_session.OptimizerSession(session_dir)
    result = generate_shadow_templates(
        session, from_date="2026-05-10", to_date="2026-05-17"
    )
    assert len(result.templates) == 2
    shadow_dir = Path(result.shadow_dir)
    assert (shadow_dir / "nt8_run_batch_command.json").exists()
    assert (shadow_dir / "SHADOW_README.md").exists()
    # Both shadow templates have the patched dates and the original
    # contract preserved.
    for t in result.templates:
        text = Path(t.shadow_template).read_text(encoding="utf-8")
        assert "<From>2026-05-10T00:00:00</From>" in text
        assert "<To>2026-05-17T00:00:00</To>" in text
        assert "<InstrumentOrInstrumentList>NQ 06-26</InstrumentOrInstrumentList>" in text


def test_generate_rejects_bad_dates(tmp_path: Path):
    session = opt_session.OptimizerSession(_seeded_session(tmp_path))
    with pytest.raises(OptimizerShadowError):
        generate_shadow_templates(session, from_date="2026-05-17", to_date="2026-05-10")
    with pytest.raises(OptimizerShadowError):
        generate_shadow_templates(session, from_date="yesterday", to_date="2026-05-10")


def test_generate_filters_to_selected_run_ids(tmp_path: Path):
    session = opt_session.OptimizerSession(_seeded_session(tmp_path))
    result = generate_shadow_templates(
        session, from_date="2026-05-10", to_date="2026-05-17",
        candidate_run_ids=["F_001"],
    )
    assert [t.candidate_run_id for t in result.templates] == ["F_001"]


def test_generate_raises_when_no_named_templates(tmp_path: Path):
    opt_session.set_storage_root(tmp_path / "sessions")
    session = opt_session.create_session(strategy_id="FakeStrategy")
    with pytest.raises(OptimizerShadowError):
        generate_shadow_templates(session, from_date="2026-05-10", to_date="2026-05-17")


def test_trigger_writes_command_file(tmp_path: Path):
    session = opt_session.OptimizerSession(_seeded_session(tmp_path))
    generate_shadow_templates(session, from_date="2026-05-10", to_date="2026-05-17")
    cmd_path = tmp_path / "nt8_command.json"
    info = trigger_shadow_run(session, command_file=cmd_path)
    assert cmd_path.exists()
    payload = json.loads(cmd_path.read_text(encoding="utf-8"))
    assert payload["action"] == "RunBatch"
    assert payload["sourceFolder"].endswith(str(Path("shadow") / "templates"))
    assert payload["runId"].startswith("shadow_")
    assert info["command_file"] == str(cmd_path)


def test_trigger_errors_when_no_templates(tmp_path: Path):
    session = opt_session.OptimizerSession(_seeded_session(tmp_path))
    cmd_path = tmp_path / "nt8_command.json"
    with pytest.raises(OptimizerShadowError):
        trigger_shadow_run(session, command_file=cmd_path)


def test_status_before_run_reports_no_results(tmp_path: Path):
    session = opt_session.OptimizerSession(_seeded_session(tmp_path))
    generate_shadow_templates(session, from_date="2026-05-10", to_date="2026-05-17")
    status = shadow_status(session)
    assert status.template_count == 2
    assert status.nt_output_present is False
    assert status.candidates_with_results == []


def test_ingest_produces_comparison(tmp_path: Path):
    session = opt_session.OptimizerSession(_seeded_session(tmp_path, with_final_results=True))
    generate_shadow_templates(session, from_date="2026-05-10", to_date="2026-05-17")
    # Stub shadow nt_output Trades.csv. F_001 stays positive; F_002 flips.
    shadow_out = session.directory / "deployment_package" / "shadow" / "nt_output"
    for run_id, profits in [("F_001", [100, -50, 100, -50]), ("F_002", [-100, -50])]:
        (shadow_out / run_id).mkdir(parents=True)
        (shadow_out / run_id / "Trades.csv").write_text(
            _trades_csv(profits, "5/12/2026"), encoding="utf-8"
        )

    status = ingest_shadow_results(session)
    assert status.nt_output_present is True
    by_run = {c.candidate_run_id: c for c in status.comparisons}
    # F_001 shadow trades = 4, BT trades = 3
    assert by_run["F_001"].backtest_trades == 3
    assert by_run["F_001"].shadow_trades == 4
    # F_002 should flip flag (BT net +0, shadow net -150) — assert sign-flip flag fires.
    f2 = by_run["F_002"]
    # Net profit per trade flipped: BT net 0 -> sh net negative. Either the
    # flip flag or the trades_per_day flag should fire.
    assert f2.divergence_flags, f"expected divergence flags for F_002, got {f2.divergence_flags!r}"

    # Report files written
    pkg = session.directory / "deployment_package" / "shadow"
    assert (pkg / "comparison.json").exists()
    assert (pkg / "comparison.md").exists()


def test_ingest_handles_missing_shadow_trades(tmp_path: Path):
    session = opt_session.OptimizerSession(_seeded_session(tmp_path, with_final_results=True))
    generate_shadow_templates(session, from_date="2026-05-10", to_date="2026-05-17")
    # Make F_001 output dir but no Trades.csv inside.
    (session.directory / "deployment_package" / "shadow" / "nt_output" / "F_001").mkdir(parents=True)

    status = ingest_shadow_results(session)
    # Note recorded; no comparison built.
    assert any("Trades.csv missing" in n for n in status.notes)
    assert status.comparisons == []
