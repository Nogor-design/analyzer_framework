from pathlib import Path

from ta_foundation.optimization.nt_template import parse_strategy_optimization_template
from ta_foundation.optimization.template_generator import (
    generate_broad_discovery_templates,
    generate_candidate_refinement_template,
    generate_daily_risk_template,
)


def _seed_template(path: Path) -> None:
    path.write_text(
        """<StrategyTemplate>
  <StrategyType>NinjaTrader.NinjaScript.Strategies.PantheonMasterBotV01TesterV2</StrategyType>
  <OptimizerType>NinjaTrader.NinjaScript.Optimizers.DefaultOptimizer</OptimizerType>
  <OptimizationFitness>NinjaTrader.NinjaScript.OptimizationFitnesses.MaxProfitFactor</OptimizationFitness>
  <OptimizationParameters>
    <ArrayOfParameter xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
      <Parameter><Increment>1</Increment><Max xsi:type="xsd:int">0</Max><Min xsi:type="xsd:int">0</Min><Name>StartTimeH</Name><ParameterTypeSerializable>System.Int32, mscorlib</ParameterTypeSerializable><ValueSerializable>0</ValueSerializable></Parameter>
      <Parameter><Increment>1</Increment><Max xsi:type="xsd:int">0</Max><Min xsi:type="xsd:int">0</Min><Name>StartTimeM</Name><ParameterTypeSerializable>System.Int32, mscorlib</ParameterTypeSerializable><ValueSerializable>0</ValueSerializable></Parameter>
      <Parameter><Increment>2</Increment><Max xsi:type="xsd:int">4</Max><Min xsi:type="xsd:int">2</Min><Name>DurationTimeH</Name><ParameterTypeSerializable>System.Int32, mscorlib</ParameterTypeSerializable><ValueSerializable>2</ValueSerializable></Parameter>
      <Parameter><Increment>1</Increment><Max xsi:type="xsd:int">0</Max><Min xsi:type="xsd:int">0</Min><Name>DurationTimeM</Name><ParameterTypeSerializable>System.Int32, mscorlib</ParameterTypeSerializable><ValueSerializable>0</ValueSerializable></Parameter>
      <Parameter><Increment>1</Increment><Max xsi:type="xsd:int">5</Max><Min xsi:type="xsd:int">5</Min><Name>averageFast</Name><ParameterTypeSerializable>System.Int32, mscorlib</ParameterTypeSerializable><ValueSerializable>5</ValueSerializable></Parameter>
      <Parameter><Increment>50</Increment><Max xsi:type="xsd:int">500</Max><Min xsi:type="xsd:int">50</Min><Name>averageSlow</Name><ParameterTypeSerializable>System.Int32, mscorlib</ParameterTypeSerializable><ValueSerializable>200</ValueSerializable></Parameter>
      <Parameter><Increment>50</Increment><Max xsi:type="xsd:int">350</Max><Min xsi:type="xsd:int">50</Min><Name>MaxStop</Name><ParameterTypeSerializable>System.Int32, mscorlib</ParameterTypeSerializable><ValueSerializable>200</ValueSerializable></Parameter>
      <Parameter><Increment>0.5</Increment><Max xsi:type="xsd:double">2</Max><Min xsi:type="xsd:double">0.5</Min><Name>MaxTPRatio</Name><ParameterTypeSerializable>System.Double, mscorlib</ParameterTypeSerializable><ValueSerializable>0.5</ValueSerializable></Parameter>
      <Parameter><Increment>1</Increment><Max xsi:type="xsd:boolean">true</Max><Min xsi:type="xsd:boolean">true</Min><Name>Long</Name><ParameterTypeSerializable>System.Boolean, mscorlib</ParameterTypeSerializable><ValueSerializable>True</ValueSerializable></Parameter>
      <Parameter><Increment>1</Increment><Max xsi:type="xsd:boolean">true</Max><Min xsi:type="xsd:boolean">true</Min><Name>Short</Name><ParameterTypeSerializable>System.Boolean, mscorlib</ParameterTypeSerializable><ValueSerializable>True</ValueSerializable></Parameter>
      <Parameter><Increment>1</Increment><Max xsi:type="xsd:boolean">false</Max><Min xsi:type="xsd:boolean">false</Min><Name>Reverse</Name><ParameterTypeSerializable>System.Boolean, mscorlib</ParameterTypeSerializable><ValueSerializable>False</ValueSerializable></Parameter>
      <Parameter><Increment>1</Increment><Max xsi:type="xsd:double">10000</Max><Min xsi:type="xsd:double">10000</Min><Name>ProfitStop</Name><ParameterTypeSerializable>System.Double, mscorlib</ParameterTypeSerializable><ValueSerializable>10000</ValueSerializable></Parameter>
      <Parameter><Increment>1</Increment><Max xsi:type="xsd:double">10000</Max><Min xsi:type="xsd:double">10000</Min><Name>LossStop</Name><ParameterTypeSerializable>System.Double, mscorlib</ParameterTypeSerializable><ValueSerializable>10000</ValueSerializable></Parameter>
      <Parameter><Increment>1</Increment><Max xsi:type="xsd:double">500</Max><Min xsi:type="xsd:double">500</Min><Name>MaxTrades</Name><ParameterTypeSerializable>System.Double, mscorlib</ParameterTypeSerializable><ValueSerializable>500</ValueSerializable></Parameter>
    </ArrayOfParameter>
  </OptimizationParameters>
  <Strategy>
    <PantheonMasterBotV01TesterV2>
      <StartTimeH>0</StartTimeH>
      <StartTimeM>0</StartTimeM>
      <DurationTimeH>2</DurationTimeH>
      <DurationTimeM>0</DurationTimeM>
      <averageFast>5</averageFast>
      <averageSlow>200</averageSlow>
      <MaxStop>200</MaxStop>
      <MaxTPRatio>0.5</MaxTPRatio>
      <Long>true</Long>
      <Short>true</Short>
      <Reverse>false</Reverse>
      <ProfitStop>10000</ProfitStop>
      <LossStop>10000</LossStop>
      <MaxTrades>500</MaxTrades>
    </PantheonMasterBotV01TesterV2>
  </Strategy>
</StrategyTemplate>
""",
        encoding="utf-8",
    )


def test_generate_broad_discovery_templates_creates_modes_and_start_hours(tmp_path: Path):
    seed = tmp_path / "seed.xml"
    _seed_template(seed)

    generated = generate_broad_discovery_templates(
        seed,
        tmp_path / "generated",
        start_hours=(0, 4),
        modes=("breakout", "regression"),
    )

    assert len(generated) == 4
    breakout = parse_strategy_optimization_template(tmp_path / "generated" / "breakout" / "Pass1_Breakout_Start04.xml")
    regression = parse_strategy_optimization_template(tmp_path / "generated" / "regression" / "Pass1_Regression_Start04.xml")
    assert breakout.mode == "breakout"
    assert breakout.start_hour == 4
    assert breakout.estimated_combinations == 560
    assert {parameter.name for parameter in breakout.parameters} == {
        "DurationTimeH",
        "averageSlow",
        "MaxStop",
        "MaxTPRatio",
    }
    assert regression.mode == "regression"
    assert regression.start_hour == 4


def test_generate_broad_discovery_templates_can_target_custom_optimizer(tmp_path: Path):
    seed = tmp_path / "seed.xml"
    _seed_template(seed)

    generated = generate_broad_discovery_templates(
        seed,
        tmp_path / "generated",
        start_hours=(0,),
        modes=("breakout",),
        optimizer_type="NinjaTrader.NinjaScript.Optimizers.CustomMultiObjectiveOptimizer",
        optimization_fitness="NinjaTrader.NinjaScript.OptimizationFitnesses.CustomMultiObjectiveFitness",
    )

    text = generated[0].read_text(encoding="utf-8")
    assert "<OptimizerType>NinjaTrader.NinjaScript.Optimizers.CustomMultiObjectiveOptimizer</OptimizerType>" in text
    assert "<OptimizationFitness>NinjaTrader.NinjaScript.OptimizationFitnesses.CustomMultiObjectiveFitness</OptimizationFitness>" in text


def test_generate_candidate_refinement_template_tightens_candidate_ranges(tmp_path: Path):
    seed = tmp_path / "seed.xml"
    _seed_template(seed)

    output = generate_candidate_refinement_template(
        seed,
        tmp_path / "Pass2_Breakout_BotA.xml",
        start_hour=4,
        duration_hours=2,
        reverse=False,
        average_slow=200,
        max_stop=100,
        max_tp_ratio=1.4,
    )

    parsed = parse_strategy_optimization_template(output)
    swept = {parameter.name: parameter for parameter in parsed.swept_parameters}
    assert parsed.mode == "breakout"
    assert parsed.start_hour == 4
    assert parsed.duration_hours == 2
    assert swept["averageFast"].minimum == "2"
    assert swept["averageFast"].maximum == "10"
    assert swept["averageSlow"].minimum == "180"
    assert swept["averageSlow"].maximum == "220"
    assert swept["MaxStop"].minimum == "80"
    assert swept["MaxStop"].maximum == "120"
    assert swept["MaxStop"].increment == "20"
    assert swept["MaxTPRatio"].minimum == "1.1"
    assert swept["MaxTPRatio"].maximum == "1.7"
    assert swept["Long"].minimum == "false"
    assert swept["Long"].maximum == "true"
    assert {parameter.name for parameter in parsed.parameters} == {
        "averageFast",
        "averageSlow",
        "MaxStop",
        "MaxTPRatio",
        "Long",
        "Short",
    }


def test_generate_daily_risk_template_sweeps_daily_controls(tmp_path: Path):
    seed = tmp_path / "seed.xml"
    _seed_template(seed)

    output = generate_daily_risk_template(
        seed,
        tmp_path / "Pass3_Breakout_BotA.xml",
        start_hour=4,
        duration_hours=2,
        reverse=False,
        average_fast=5,
        average_slow=200,
        max_stop=100,
        max_tp_ratio=1.4,
        long_enabled=True,
        short_enabled=False,
    )

    parsed = parse_strategy_optimization_template(output)
    swept = {parameter.name: parameter for parameter in parsed.swept_parameters}
    assert parsed.start_hour == 4
    assert {parameter.name for parameter in parsed.parameters} == {"ProfitStop", "LossStop", "MaxTrades"}
    assert set(swept) == {"ProfitStop", "LossStop", "MaxTrades"}
    assert swept["ProfitStop"].minimum == "1"
    assert swept["ProfitStop"].maximum == "1001"
    assert swept["LossStop"].minimum == "1"
    assert swept["LossStop"].maximum == "801"
    assert swept["MaxTrades"].minimum == "1"
    assert swept["MaxTrades"].maximum == "20"
