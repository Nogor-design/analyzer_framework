from __future__ import annotations

"""Tests for the multi-phase deployment package builder."""

from pathlib import Path

import pytest

from ta_foundation.web import optimizer_session as opt_session
from ta_foundation.web.optimizer_deployment_package import (
    build_deployment_package,
)
from ta_foundation.web.optimizer_session import create_session


# A seed XML that satisfies _has_core_settings + final fixed-Backtest
# generation. Includes all strategy fields the grid_workflow patches.
RICH_SEED_XML = """<?xml version="1.0" encoding="utf-8"?>
<StrategyTemplate xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <StrategyType>NinjaTrader.NinjaScript.Strategies.FakeStrategy</StrategyType>
  <Category>Optimization</Category>
  <OptimizerType>NinjaTrader.NinjaScript.Optimizers.DefaultOptimizer</OptimizerType>
  <OptimizerParameters>
    <ArrayOfParameterWrapper>
      <ParameterWrapper>
        <Name>KeepBestResults</Name>
        <Value xsi:type="xsd:int">500</Value>
      </ParameterWrapper>
    </ArrayOfParameterWrapper>
  </OptimizerParameters>
  <OptimizationFitness>NinjaTrader.NinjaScript.OptimizationFitnesses.MaxProfitFactor</OptimizationFitness>
  <From>2026-04-01T00:00:00</From>
  <To>2026-05-01T00:00:00</To>
  <Strategy>
    <FakeStrategy>
      <StartTimeH>9</StartTimeH>
      <StartTimeM>0</StartTimeM>
      <DurationTimeH>4</DurationTimeH>
      <DurationTimeM>0</DurationTimeM>
      <averageFast>5</averageFast>
      <averageSlow>200</averageSlow>
      <UseTrend>false</UseTrend>
      <UseTrendReverse>false</UseTrendReverse>
      <MaxStop>100</MaxStop>
      <MaxTPRatio>1.0</MaxTPRatio>
      <ProfitStop>1</ProfitStop>
      <LossStop>1</LossStop>
      <MaxTrades>2</MaxTrades>
      <Long>true</Long>
      <Short>true</Short>
      <Reverse>false</Reverse>
      <BotName>FakeBot</BotName>
      <InstrumentOrInstrumentList>NQ 06-26</InstrumentOrInstrumentList>
    </FakeStrategy>
  </Strategy>
  <OptimizationParameters>
    <ArrayOfParameter>
      <Parameter>
        <Increment>1</Increment>
        <Max xsi:type="xsd:int">200</Max>
        <Min xsi:type="xsd:int">200</Min>
        <Name>averageSlow</Name>
        <ValueSerializable>200</ValueSerializable>
      </Parameter>
    </ArrayOfParameter>
  </OptimizationParameters>
</StrategyTemplate>
"""


def _opt_csv(rows: list[dict[str, str]]) -> str:
    """Build a minimal NinjaTrader *_Optimization.csv with the required
    Parameters / Performance / metric columns."""
    header = (
        "Instrument,Performance,Parameters,Total net profit,Gross profit,Gross loss,"
        "Profit factor,Max. drawdown,Total # of trades,Percent profitable,\n"
    )
    body = ""
    for row in rows:
        body += (
            f"{row['instrument']},{row['performance']},{row['parameters']},"
            f"{row['net_profit']},{row['gross_profit']},{row['gross_loss']},"
            f"{row['profit_factor']},{row['drawdown']},{row['trades']},{row['win_pct']},\n"
        )
    return header + body


@pytest.fixture
def session_with_phase1_results(tmp_path: Path):
    opt_session.set_storage_root(tmp_path / "opt_sessions")
    seed_path = tmp_path / "seed.xml"
    seed_path.write_text(RICH_SEED_XML, encoding="utf-8")

    session = create_session(
        label="multi-phase test",
        strategy_id="FakeStrategy",
        seed_template_path=str(seed_path),
        instrument="NQ 06-26",
    )
    # Minimal "generated_templates" so the builder doesn't error.
    (session.directory / "generated_templates").mkdir()
    (session.directory / "generated_templates" / "chunk_001.xml").write_text(
        RICH_SEED_XML, encoding="utf-8"
    )
    # Phase-1 nt_output with passing rows. Parameters column must include
    # StartTimeH, DurationTimeH, averageSlow, MaxStop, MaxTPRatio so
    # _has_core_settings passes.
    phase1_out = session.directory / "nt_output" / "chunk_001"
    phase1_out.mkdir(parents=True)
    csv = _opt_csv(
        [
            {
                "instrument": "NQ 06-26",
                "performance": "2.5",
                "parameters": "9/4/200/100/1.0/FakeBot (StartTimeH DurationTimeH averageSlow MaxStop MaxTPRatio)",
                "net_profit": "8000",
                "gross_profit": "10000",
                "gross_loss": "-2000",
                "profit_factor": "5.0",
                "drawdown": "-1500",
                "trades": "20",
                "win_pct": "65%",
            }
        ]
    )
    (phase1_out / "chunk_001_Optimization.csv").write_text(csv, encoding="utf-8")

    yield session
    opt_session.set_storage_root(None)


def test_phase1_only_emits_phase2_templates(session_with_phase1_results):
    session = session_with_phase1_results
    pkg = build_deployment_package(session, top_n=5)
    assert pkg.decision_state == "needs_phase2_run"
    assert pkg.phase2_refinement_template_count >= 1
    assert pkg.phase3_risk_template_count == 0
    assert pkg.final_backtest_template_count == 0
    assert pkg.final_review_dir is None
    # END_USER_DECISION.md must exist
    assert Path(pkg.end_user_decision_path).exists()


def test_phase2_results_advance_to_phase3(session_with_phase1_results):
    session = session_with_phase1_results
    # First build emits phase-2 templates.
    pkg = build_deployment_package(session, top_n=5)
    assert pkg.phase2_refinement_template_count >= 1
    package_dir = Path(pkg.package_dir)
    phase2_out = package_dir / "phase2_refinement_handoff" / "nt_output" / "Pass2"
    phase2_out.mkdir(parents=True)
    # Phase-2 rows must additionally carry averageFast for phase-3 generation.
    csv = _opt_csv(
        [
            {
                "instrument": "NQ 06-26",
                "performance": "3.0",
                "parameters": "9/4/3/200/100/1.0/true/true/FakeBot (StartTimeH DurationTimeH averageFast averageSlow MaxStop MaxTPRatio Long Short)",
                "net_profit": "12000",
                "gross_profit": "14000",
                "gross_loss": "-2000",
                "profit_factor": "7.0",
                "drawdown": "-1200",
                "trades": "25",
                "win_pct": "70%",
            }
        ]
    )
    (phase2_out / "Pass2_Optimization.csv").write_text(csv, encoding="utf-8")

    pkg = build_deployment_package(session, top_n=5)
    assert pkg.phase3_risk_template_count >= 1
    # No OOS dates yet so we should be parked at the OOS-dates-needed gate.
    assert pkg.decision_state == "needs_phase3_run_then_oos_dates"


def test_phase3_results_with_oos_dates_emit_final_templates(session_with_phase1_results):
    session = session_with_phase1_results
    # Bootstrap phase-1 -> phase-2 templates.
    build_deployment_package(session, top_n=5)
    package_dir = session.directory / "deployment_package"
    phase2_out = package_dir / "phase2_refinement_handoff" / "nt_output" / "Pass2"
    phase2_out.mkdir(parents=True)
    (phase2_out / "Pass2_Optimization.csv").write_text(
        _opt_csv(
            [
                {
                    "instrument": "NQ 06-26",
                    "performance": "3.0",
                    "parameters": "9/4/3/200/100/1.0/true/true/FakeBot (StartTimeH DurationTimeH averageFast averageSlow MaxStop MaxTPRatio Long Short)",
                    "net_profit": "12000",
                    "gross_profit": "14000",
                    "gross_loss": "-2000",
                    "profit_factor": "7.0",
                    "drawdown": "-1200",
                    "trades": "25",
                    "win_pct": "70%",
                }
            ]
        ),
        encoding="utf-8",
    )
    # Bootstrap phase-2 -> phase-3 templates.
    build_deployment_package(session, top_n=5)
    phase3_out = package_dir / "phase3_risk_handoff" / "nt_output" / "Pass3"
    phase3_out.mkdir(parents=True)
    # Phase-3 rows carry ProfitStop/LossStop/MaxTrades.
    (phase3_out / "Pass3_Optimization.csv").write_text(
        _opt_csv(
            [
                {
                    "instrument": "NQ 06-26",
                    "performance": "4.0",
                    "parameters": "9/4/3/200/100/1.0/1/1/2/true/true/FakeBot (StartTimeH DurationTimeH averageFast averageSlow MaxStop MaxTPRatio ProfitStop LossStop MaxTrades Long Short)",
                    "net_profit": "20000",
                    "gross_profit": "22000",
                    "gross_loss": "-2000",
                    "profit_factor": "9.0",
                    "drawdown": "-1000",
                    "trades": "18",
                    "win_pct": "72%",
                }
            ]
        ),
        encoding="utf-8",
    )

    pkg = build_deployment_package(
        session,
        top_n=5,
        oos_from_date="2026-04-14",
        oos_to_date="2026-05-14",
    )
    assert pkg.final_backtest_template_count >= 1
    assert pkg.decision_state == "needs_final_backtest_run"
    # Generated final templates must NOT contain optimizer sections.
    named_dir = package_dir / "final_backtest_handoff" / "named_backtest_templates"
    xml_files = list(named_dir.rglob("*.xml"))
    assert xml_files, "expected at least one named backtest template"
    text = xml_files[0].read_text(encoding="utf-8")
    assert "<OptimizerType>" not in text
    assert "<OptimizationParameters>" not in text
    assert "<UseTrend>false</UseTrend>" in text
    assert "2026-04-14T00:00:00" in text
