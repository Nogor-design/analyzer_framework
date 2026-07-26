from pathlib import Path

from ta_foundation.optimization.nt_template import parse_strategy_optimization_template


def test_parse_strategy_optimization_template_identifies_swept_parameters(tmp_path: Path):
    template = tmp_path / "OptimizeFirstRunBreakout.xml"
    template.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<StrategyTemplate>
  <StrategyType>NinjaTrader.NinjaScript.Strategies.PantheonMasterBotV01TesterV2</StrategyType>
  <OptimizerType>NinjaTrader.NinjaScript.Optimizers.DefaultOptimizer</OptimizerType>
  <OptimizationFitness>NinjaTrader.NinjaScript.OptimizationFitnesses.MaxProfitFactor</OptimizationFitness>
  <OptimizationParameters>
    <ArrayOfParameter xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
      <Parameter>
        <Increment>2</Increment>
        <Max xsi:type="xsd:int">4</Max>
        <Min xsi:type="xsd:int">2</Min>
        <Name>DurationTimeH</Name>
        <ParameterTypeSerializable>System.Int32, mscorlib</ParameterTypeSerializable>
        <ValueSerializable>2</ValueSerializable>
      </Parameter>
      <Parameter>
        <Increment>50</Increment>
        <Max xsi:type="xsd:int">500</Max>
        <Min xsi:type="xsd:int">50</Min>
        <Name>averageSlow</Name>
        <ParameterTypeSerializable>System.Int32, mscorlib</ParameterTypeSerializable>
        <ValueSerializable>200</ValueSerializable>
      </Parameter>
      <Parameter>
        <Increment>1</Increment>
        <Max xsi:type="xsd:boolean">false</Max>
        <Min xsi:type="xsd:boolean">false</Min>
        <Name>Reverse</Name>
        <ParameterTypeSerializable>System.Boolean, mscorlib</ParameterTypeSerializable>
        <ValueSerializable>False</ValueSerializable>
      </Parameter>
    </ArrayOfParameter>
  </OptimizationParameters>
  <Strategy>
    <PantheonMasterBotV01TesterV2>
      <InstrumentOrInstrumentList>NQ 06-26</InstrumentOrInstrumentList>
      <StartTimeH>0</StartTimeH>
      <DurationTimeH>2</DurationTimeH>
      <Reverse>false</Reverse>
    </PantheonMasterBotV01TesterV2>
  </Strategy>
</StrategyTemplate>
""",
        encoding="utf-8",
    )

    parsed = parse_strategy_optimization_template(template)

    assert parsed.strategy_type.endswith("PantheonMasterBotV01TesterV2")
    assert parsed.optimization_fitness.endswith("MaxProfitFactor")
    assert parsed.mode == "breakout"
    assert parsed.instrument_or_instrument_list == "NQ 06-26"
    assert parsed.start_hour == 0
    assert parsed.duration_hours == 2
    assert [parameter.name for parameter in parsed.swept_parameters] == [
        "DurationTimeH",
        "averageSlow",
    ]
    assert parsed.estimated_combinations == 20


def test_parse_strategy_optimization_template_classifies_regression(tmp_path: Path):
    template = tmp_path / "OptimizeFirstRunRegression.xml"
    template.write_text(
        """<StrategyTemplate>
  <StrategyType>NinjaTrader.NinjaScript.Strategies.PantheonMasterBotV01TesterV2</StrategyType>
  <OptimizerType>NinjaTrader.NinjaScript.Optimizers.DefaultOptimizer</OptimizerType>
  <OptimizationFitness>NinjaTrader.NinjaScript.OptimizationFitnesses.MaxProfitFactor</OptimizationFitness>
  <OptimizationParameters><ArrayOfParameter /></OptimizationParameters>
  <Strategy>
    <PantheonMasterBotV01TesterV2>
      <StartTimeH>4</StartTimeH>
      <DurationTimeH>4</DurationTimeH>
      <Reverse>true</Reverse>
    </PantheonMasterBotV01TesterV2>
  </Strategy>
</StrategyTemplate>
""",
        encoding="utf-8",
    )

    parsed = parse_strategy_optimization_template(template)

    assert parsed.mode == "regression"
    assert parsed.start_hour == 4
    assert parsed.duration_hours == 4
    assert parsed.estimated_combinations == 1


def test_decimal_ranges_count_inclusive_steps(tmp_path: Path):
    template = tmp_path / "decimal_range.xml"
    template.write_text(
        """<StrategyTemplate>
  <StrategyType>NinjaTrader.NinjaScript.Strategies.PantheonMasterBotV01TesterV2</StrategyType>
  <OptimizerType>NinjaTrader.NinjaScript.Optimizers.DefaultOptimizer</OptimizerType>
  <OptimizationFitness>NinjaTrader.NinjaScript.OptimizationFitnesses.MaxProfitFactor</OptimizationFitness>
  <OptimizationParameters>
    <ArrayOfParameter xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
      <Parameter>
        <Increment>0.1</Increment>
        <Max xsi:type="xsd:double">1</Max>
        <Min xsi:type="xsd:double">0.4</Min>
        <Name>MaxTPRatio</Name>
        <ParameterTypeSerializable>System.Double, mscorlib</ParameterTypeSerializable>
        <ValueSerializable>0.5</ValueSerializable>
      </Parameter>
    </ArrayOfParameter>
  </OptimizationParameters>
</StrategyTemplate>
""",
        encoding="utf-8",
    )

    parsed = parse_strategy_optimization_template(template)

    assert parsed.swept_parameters[0].estimated_steps == 7
    assert parsed.estimated_combinations == 7
