from pathlib import Path
import json

from ta_foundation.optimization.grid_workflow import (
    OptimizationGridConfig,
    create_final_backtest_templates_from_phase3_csv,
    create_next_phase_from_optimization_csv,
    evaluate_optimization_grid,
    load_optimization_grid,
)
from ta_foundation.optimization.nt_template import parse_strategy_optimization_template
from ta_foundation.tests.optimization.test_template_generator import _seed_template


def _write_backtest_seed(path: Path) -> None:
    path.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<StrategyTemplate>
  <StrategyType>NinjaTrader.NinjaScript.Strategies.PantheonMasterBotV01TesterV2</StrategyType>
  <Strategy>
    <PantheonMasterBotV01TesterV2>
      <Category>Backtest</Category>
      <StartTimeH>0</StartTimeH>
      <StartTimeM>0</StartTimeM>
      <DurationTimeH>1</DurationTimeH>
      <DurationTimeM>30</DurationTimeM>
      <From>2026-01-29T00:00:00</From>
      <To>2026-03-21T00:00:00</To>
      <averageFast>50</averageFast>
      <averageSlow>200</averageSlow>
      <UseTrend>true</UseTrend>
      <UseTrendReverse>true</UseTrendReverse>
      <ProfitStop>9999</ProfitStop>
      <LossStop>9999</LossStop>
      <MaxTrades>999</MaxTrades>
      <MaxStop>200</MaxStop>
      <MaxTPRatio>0.5</MaxTPRatio>
      <Long>true</Long>
      <Short>true</Short>
      <Reverse>false</Reverse>
      <BotName>PantheonMasterBotV01TesterV2</BotName>
    </PantheonMasterBotV01TesterV2>
  </Strategy>
</StrategyTemplate>
""",
        encoding="utf-8",
    )


def _params(
    *,
    start_hour: int,
    duration_hours: int,
    fast: int,
    slow: int,
    stop: int,
    tp: float,
    long: bool = True,
    short: bool = True,
    reverse: bool = False,
    profit_stop: int = 10000,
    loss_stop: int = 10000,
    max_trades: int = 10000,
    bot_name: str = "Iron Aphrodite Hunter",
) -> str:
    values = [
        True,
        start_hour,
        0,
        duration_hours,
        0,
        fast,
        slow,
        300,
        False,
        False,
        profit_stop,
        loss_stop,
        max_trades,
        1,
        stop,
        True,
        tp,
        True,
        long,
        short,
        reverse,
        False,
        800,
        400,
        False,
        False,
        0.3,
        "panthionIcon4.png",
        "Oden2.png",
        bot_name,
    ]
    names = [
        "Use_Time_Filter",
        "Start_Time_(HH)",
        "Start_Time_(mm)",
        "Duration_Time_(HH)",
        "Duration_Time_(mm)",
        "averageFast",
        "averageSlow",
        "averageTrend",
        "UseTrend",
        "UseTrendReverse",
        "ProfitStop",
        "LossStop",
        "MaxTrades",
        "Contracts",
        "MaxStop",
        "Use_MaxStop",
        "MaxTPRatio",
        "Use_MaxTP",
        "Long",
        "Short",
        "Reverse",
        "Use_Kill",
        "Kill_Profit_Stop",
        "Kill_Loss_Stop",
        "Show_Current_PNL",
        "Show_Stats_Box",
        "Image_Opacity",
        "Corner_Image",
        "Background_Image",
        "Bot_Name",
    ]
    return "/".join(str(value) for value in values) + " (" + " ".join(names) + " )"


def _write_optimization_csv(path: Path) -> None:
    content = "\n".join(
        [
            "Instrument,Performance,Parameters,Total net profit,Gross profit,Gross loss,Profit factor,Max. drawdown,Total # of trades,Percent profitable",
            f'NQ 06-26,99,"{_params(start_hour=4, duration_hours=2, fast=5, slow=200, stop=100, tp=1.4, profit_stop=1001, loss_stop=801, max_trades=3)}",$3000,$4500,($1500),3.0,($500),25,64%',
            f',"88","{_params(start_hour=8, duration_hours=4, fast=8, slow=400, stop=160, tp=1.8, reverse=True, bot_name="Silver Zeus Regression")}",$2500,$4000,($1500),2.6,($900),18,61%',
            f',"10","{_params(start_hour=12, duration_hours=2, fast=5, slow=100, stop=300, tp=0.5, bot_name="Rejected Bot")}",$100,$600,($500),1.1,($3000),3,50%',
        ]
    )
    path.write_text(content, encoding="utf-8")


def test_load_and_evaluate_optimization_grid(tmp_path: Path):
    csv_path = tmp_path / "NinjaTrader Grid sample.csv"
    _write_optimization_csv(csv_path)

    df = load_optimization_grid(tmp_path)
    candidates = evaluate_optimization_grid(df, OptimizationGridConfig(count=2))

    assert len(df) == 3
    assert candidates[0].status == "pass"
    assert candidates[0].start_hour == 4
    assert candidates[0].average_slow == 200
    assert candidates[1].mode == "regression"
    assert candidates[-1].status == "reject"


def test_create_phase2_from_phase1_optimization_csv(tmp_path: Path):
    seed = tmp_path / "seed.xml"
    _seed_template(seed)
    csv_path = tmp_path / "NinjaTrader Grid sample.csv"
    _write_optimization_csv(csv_path)

    generated = create_next_phase_from_optimization_csv(
        seed,
        tmp_path,
        tmp_path / "phase2",
        target_phase="phase2",
        config=OptimizationGridConfig(count=2),
    )

    assert len(generated) == 2
    parsed = parse_strategy_optimization_template(generated[0])
    swept = {parameter.name: parameter for parameter in parsed.swept_parameters}
    assert parsed.start_hour == 4
    assert swept["averageFast"].minimum == "2"
    assert swept["MaxStop"].increment == "20"
    assert (tmp_path / "phase2" / "team_handoff" / "run_plan.csv").exists()
    assert (tmp_path / "phase2" / "optimization_phase_lineage.csv").exists()


def test_create_phase3_from_phase2_optimization_csv(tmp_path: Path):
    seed = tmp_path / "seed.xml"
    _seed_template(seed)
    csv_path = tmp_path / "NinjaTrader Grid sample.csv"
    _write_optimization_csv(csv_path)

    generated = create_next_phase_from_optimization_csv(
        seed,
        tmp_path,
        tmp_path / "phase3",
        target_phase="phase3",
        config=OptimizationGridConfig(count=1),
    )

    assert len(generated) == 1
    parsed = parse_strategy_optimization_template(generated[0])
    assert {parameter.name for parameter in parsed.swept_parameters} == {"ProfitStop", "LossStop", "MaxTrades"}


def test_final_backtest_templates_are_fixed_after_phase3(tmp_path: Path):
    seed = tmp_path / "backtest_seed.xml"
    _write_backtest_seed(seed)
    csv_path = tmp_path / "NinjaTrader Grid sample.csv"
    _write_optimization_csv(csv_path)

    generated = create_final_backtest_templates_from_phase3_csv(
        seed,
        tmp_path,
        tmp_path / "final",
        config=OptimizationGridConfig(count=1),
        from_date="2026-04-14",
        to_date="2026-05-14",
    )

    assert len(generated) == 1
    text = generated[0].read_text(encoding="utf-8")
    assert "<Category>Backtest</Category>" in text
    assert "<From>2026-04-14T00:00:00</From>" in text
    assert "<To>2026-05-14T00:00:00</To>" in text
    assert "<StartTimeH>4</StartTimeH>" in text
    assert "<averageFast>5</averageFast>" in text
    assert "<averageSlow>200</averageSlow>" in text
    assert "<UseTrend>false</UseTrend>" in text
    assert "<UseTrendReverse>false</UseTrendReverse>" in text
    assert "<ProfitStop>1001</ProfitStop>" in text
    assert "<LossStop>801</LossStop>" in text
    assert "<MaxTrades>3</MaxTrades>" in text
    assert "<OptimizerType>" not in text
    assert (tmp_path / "final" / "README_FINAL_BACKTEST_TEMPLATES.md").exists()
    command = json.loads((tmp_path / "final" / "nt8_run_batch_command.json").read_text(encoding="utf-8"))
    assert command["action"] == "RunBatch"
    assert command["sourceFolder"].endswith("named_backtest_templates")
    assert command["destFolder"].endswith("nt8_backtest_results")
    assert (tmp_path / "final" / "RUN_FINAL_BACKTESTS.md").exists()
