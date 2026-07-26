from __future__ import annotations

"""Tests for the parameter-neighborhood web engine."""

import json
from pathlib import Path

import pytest

from ta_foundation.web import optimizer_session as opt_session
from ta_foundation.web.optimizer_neighborhood import (
    OptimizerNeighborhoodError,
    generate_neighborhood_templates,
    ingest_neighborhood_results,
    neighborhood_status,
    trigger_neighborhood_run,
)


NAMED_BACKTEST_XML = """<?xml version="1.0" encoding="utf-8"?>
<StrategyTemplate>
  <StrategyType>NinjaTrader.NinjaScript.Strategies.FakeStrategy</StrategyType>
  <From>2026-04-01T00:00:00</From>
  <To>2026-04-30T00:00:00</To>
  <Strategy>
    <FakeStrategy>
      <InstrumentOrInstrumentList>NQ 06-26</InstrumentOrInstrumentList>
      <averageSlow>100</averageSlow>
      <MaxTPRatio>2.0</MaxTPRatio>
      <Reverse>false</Reverse>
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
        label="nb test",
        strategy_id="FakeStrategy",
        seed_template_path=str(tmp_path / "seed.xml"),
        instrument="NQ 06-26",
    )
    session.update(parameters=[
        {"name": "averageSlow", "type_name": "int", "mode": "optimize",
         "minimum": 50, "maximum": 300, "increment": 10},
        {"name": "MaxTPRatio", "type_name": "float", "mode": "optimize",
         "minimum": 0.5, "maximum": 5.0, "increment": 0.1},
        {"name": "Reverse", "type_name": "bool", "mode": "optimize",
         "minimum": False, "maximum": True, "increment": 1},
        {"name": "Contracts", "type_name": "int", "mode": "fixed",
         "fixed_value": 1},
    ])
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
                _trades_csv(profits), encoding="utf-8",
            )
    return session


def _trades_csv(profits):
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
            f"4/15/2026 9:00:00 AM,4/15/2026 10:00:00 AM,Buy,Profit,"
            f"{profit_str},{cum_str},$0.00,$0.00,$0.00,$0.00,$0.00,$0.00,$0.00,$0.00,60,"
        )
    return header + "\n".join(lines) + "\n"


def test_generate_creates_one_template_per_cell(tmp_path: Path):
    session = _seeded(tmp_path)
    # pct=0.20 keeps all four offsets on-grid for both params
    # (averageSlow inc=10: 80/90/110/120; MaxTPRatio inc=0.1: 1.6/1.8/2.2/2.4)
    result = generate_neighborhood_templates(session, pct=0.20, steps=4, mode="one_at_a_time")
    # 2 candidates × 2 numeric params × 4 cells each = 16 templates
    assert len(result.templates) == 16
    assert result.mode == "one_at_a_time"
    assert result.pct == 0.20
    # Each template overrides exactly the swept param
    for t in result.templates:
        assert len(t.overrides) == 1
        name = next(iter(t.overrides))
        assert name in {"averageSlow", "MaxTPRatio"}
        xml = Path(t.output_template).read_text(encoding="utf-8")
        # The patched value should appear in the XML
        formatted = str(t.overrides[name]) if isinstance(t.overrides[name], int) else \
            (str(int(t.overrides[name])) if t.overrides[name] == int(t.overrides[name])
             else repr(t.overrides[name]))
        assert f"<{name}>{formatted}</{name}>" in xml


def test_generate_skips_bool_and_fixed_params(tmp_path: Path):
    session = _seeded(tmp_path)
    result = generate_neighborhood_templates(session, pct=0.10, steps=4)
    names = {next(iter(t.overrides)) for t in result.templates}
    assert names == {"averageSlow", "MaxTPRatio"}
    assert "Reverse" not in names
    assert "Contracts" not in names


def test_generate_with_run_id_filter(tmp_path: Path):
    session = _seeded(tmp_path)
    result = generate_neighborhood_templates(
        session, pct=0.10, steps=2, candidate_run_ids=["F_001"],
    )
    runs = {t.candidate_run_id for t in result.templates}
    assert runs == {"F_001"}
    # 1 candidate × 2 numeric params × 2 cells = 4
    assert len(result.templates) == 4


def test_generate_errors_when_no_numeric_optimized_params(tmp_path: Path):
    session = _seeded(tmp_path)
    session.update(parameters=[
        {"name": "Reverse", "type_name": "bool", "mode": "optimize",
         "minimum": False, "maximum": True, "increment": 1},
    ])
    with pytest.raises(OptimizerNeighborhoodError):
        generate_neighborhood_templates(session, pct=0.10, steps=4)


def test_trigger_writes_command_file_with_instrument(tmp_path: Path):
    session = _seeded(tmp_path)
    generate_neighborhood_templates(session, pct=0.10, steps=2)
    cmd_path = tmp_path / "nt8_command.json"
    info = trigger_neighborhood_run(session, command_file=cmd_path)
    payload = json.loads(cmd_path.read_text(encoding="utf-8"))
    assert payload["action"] == "RunBatch"
    assert payload["instrument"] == "NQ 06-26"
    assert payload["runId"].startswith("nb_")
    assert info["command_file"] == str(cmd_path)


def test_trigger_errors_when_no_templates(tmp_path: Path):
    session = _seeded(tmp_path)
    cmd_path = tmp_path / "nt8_command.json"
    with pytest.raises(OptimizerNeighborhoodError):
        trigger_neighborhood_run(session, command_file=cmd_path)


def test_status_before_run_reports_no_results(tmp_path: Path):
    session = _seeded(tmp_path)
    generate_neighborhood_templates(session, pct=0.10, steps=2)
    status = neighborhood_status(session)
    # 2 candidates × 2 numeric params × 2 cells = 8
    assert status.template_count == 8
    assert status.nt_output_present is False
    assert status.candidate_stability == []


def test_ingest_produces_per_cell_stability(tmp_path: Path):
    session = _seeded(tmp_path, with_final_results=True)
    gen = generate_neighborhood_templates(
        session, pct=0.10, steps=2, candidate_run_ids=["F_001"],
    )
    nb_out = session.directory / "deployment_package" / "neighborhood" / "nt_output"
    # Simulate NT having produced results for every generated cell.
    for cell in [
        (gen.templates[i].cell_index, gen.templates[i].candidate_run_id)
        for i in range(len(gen.templates))
    ]:
        idx, run_id = cell
        out = nb_out / f"{run_id}__C{idx:02d}"
        out.mkdir(parents=True)
        # First half profitable, second half losing — mimics a needle peak.
        # Include both winners and losers so PF is finite.
        profits = [100, -20, 50] if idx < 2 else [-100, 30, -75]
        (out / "Trades.csv").write_text(_trades_csv(profits), encoding="utf-8")

    status = ingest_neighborhood_results(session)
    assert status.nt_output_present is True
    assert len(status.per_cell_results) == 4
    assert len(status.candidate_stability) == 1
    s = status.candidate_stability[0]
    assert s.candidate_run_id == "F_001"
    assert s.cells_run == 4
    assert s.cells_with_trades == 4
    # Two profitable cells, two losing — pf>1 should be 2.
    assert s.cells_with_pf_above_1 == 2
    # Per-parameter summary buckets exist (one for each swept name).
    param_names = {p["parameter"] for p in s.per_param_summaries}
    assert param_names == {"averageSlow", "MaxTPRatio"}

    pkg = session.directory / "deployment_package" / "neighborhood"
    assert (pkg / "stability.json").exists()
    assert (pkg / "stability.md").exists()


def test_ingest_handles_missing_results(tmp_path: Path):
    session = _seeded(tmp_path, with_final_results=True)
    gen = generate_neighborhood_templates(
        session, pct=0.10, steps=2, candidate_run_ids=["F_001"],
    )
    nb_out = session.directory / "deployment_package" / "neighborhood" / "nt_output"
    # Populate only one cell's results; create the rest as empty dirs.
    first = gen.templates[0]
    out = nb_out / f"{first.candidate_run_id}__C{first.cell_index:02d}"
    out.mkdir(parents=True)
    (out / "Trades.csv").write_text(_trades_csv([100, 50, 100]), encoding="utf-8")
    for t in gen.templates[1:]:
        (nb_out / f"{t.candidate_run_id}__C{t.cell_index:02d}").mkdir(parents=True)

    status = ingest_neighborhood_results(session)
    assert len(status.per_cell_results) == 1
    assert any("Trades.csv missing" in n for n in status.notes)
