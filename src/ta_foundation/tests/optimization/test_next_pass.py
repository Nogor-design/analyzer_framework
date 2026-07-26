from pathlib import Path

from ta_foundation.optimization.evaluator import EvaluationConfig
from ta_foundation.optimization.next_pass import (
    create_pass2_refinement_handoff,
    create_pass3_risk_handoff,
)
from ta_foundation.optimization.nt_template import parse_strategy_optimization_template
from ta_foundation.tests.optimization.test_template_generator import _seed_template


def _write_result_files(path: Path) -> None:
    path.mkdir()
    (path / "BotA_Summary.csv").write_text(
        """Performance,All trades,Long trades,Short trades
Start date,01/01/2026,,
Start time,12:00 AM,,
End date,01/10/2026,,
End time,11:59 PM,,
Total net profit,$1200.00,,
Profit factor,2.50,,
Max. drawdown,($300.00),,
Total # of trades,12,,
Percent profitable,66.67%,,
Avg. trade,$100.00,,
""",
        encoding="utf-8",
    )
    (path / "BotA_Settings.csv").write_text(
        """Item,Value
Strategy parameters,
Start_Time_(HH) ,4
Duration_Time_(HH) ,2
averageSlow ,200
averageFast ,5
UseTrend ,False
UseTrendReverse ,False
MaxStop ,100
MaxTPRatio ,1.4
ProfitStop ,1000
LossStop ,800
MaxTrades ,3
Long ,True
Short ,True
Reverse ,False
""",
        encoding="utf-8",
    )
    (path / "BotA_Trades.csv").write_text(
        """Trade number,Instrument,Account,Strategy,Market pos.,Qty,Entry price,Exit price,Entry time,Exit time,Entry name,Exit name,Profit,Cum. net profit,Commission,Clearing fee,Exchange fee,IP fee,NFA fee,MAE,MFE,ETD,Bars
1,NQ,Backtest,BotA,Long,1,1,2,01/01/2026 01:00,01/01/2026 01:10,E,X,$100.00,$100.00,$0,$0,$0,$0,$0,$10,$100,$0,1
""",
        encoding="utf-8",
    )


def test_create_pass2_refinement_handoff_from_result_folder(tmp_path: Path):
    seed = tmp_path / "seed.xml"
    _seed_template(seed)
    results = tmp_path / "results"
    _write_result_files(results)

    generated = create_pass2_refinement_handoff(
        seed,
        results,
        tmp_path / "pass2",
        count=1,
        average_fast_min=15,
        average_fast_max=25,
        config=EvaluationConfig(min_percent_days_traded=0),
    )

    assert len(generated) == 1
    parsed = parse_strategy_optimization_template(generated[0])
    swept = {parameter.name: parameter for parameter in parsed.swept_parameters}
    assert parsed.start_hour == 4
    assert swept["averageFast"].minimum == "15"
    assert swept["averageFast"].maximum == "25"
    assert swept["averageSlow"].minimum == "180"
    assert (tmp_path / "pass2" / "team_handoff" / "run_plan.csv").exists()
    assert (tmp_path / "pass2" / "team_handoff" / "next_pass_lineage.csv").exists()
    assert (tmp_path / "pass2" / "team_handoff" / "NEXT_PASS_SUMMARY.md").exists()
    lineage = (tmp_path / "pass2" / "team_handoff" / "next_pass_lineage.csv").read_text(encoding="utf-8")
    assert "BotA" in lineage
    assert "pass2" in lineage


def test_create_pass3_risk_handoff_from_result_folder(tmp_path: Path):
    seed = tmp_path / "seed.xml"
    _seed_template(seed)
    results = tmp_path / "results"
    _write_result_files(results)

    generated = create_pass3_risk_handoff(
        seed,
        results,
        tmp_path / "pass3",
        count=1,
        config=EvaluationConfig(min_percent_days_traded=0),
    )

    assert len(generated) == 1
    parsed = parse_strategy_optimization_template(generated[0])
    swept = {parameter.name for parameter in parsed.swept_parameters}
    assert swept == {"ProfitStop", "LossStop", "MaxTrades"}
    assert (tmp_path / "pass3" / "team_handoff" / "run_plan.csv").exists()
    assert (tmp_path / "pass3" / "team_handoff" / "next_pass_lineage.json").exists()
    summary = (tmp_path / "pass3" / "team_handoff" / "NEXT_PASS_SUMMARY.md").read_text(encoding="utf-8")
    assert "BotA" in summary


def test_create_next_pass_can_filter_to_included_run_ids(tmp_path: Path):
    seed = tmp_path / "seed.xml"
    _seed_template(seed)
    results = tmp_path / "results"
    _write_result_files(results)

    generated = create_pass2_refinement_handoff(
        seed,
        results,
        tmp_path / "pass2",
        count=1,
        include_run_ids=("MissingBot",),
        config=EvaluationConfig(min_percent_days_traded=0),
    )

    assert generated == []
    lineage = (tmp_path / "pass2" / "team_handoff" / "next_pass_lineage.csv").read_text(encoding="utf-8")
    assert "BotA" not in lineage
