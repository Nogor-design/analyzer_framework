from pathlib import Path

from ta_foundation.optimization.handoff import build_handoff_run_plan, write_handoff_package


def _write_template(path: Path, *, reverse: str = "false") -> None:
    path.write_text(
        f"""<StrategyTemplate>
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
        <Increment>50</Increment>
        <Max xsi:type="xsd:int">350</Max>
        <Min xsi:type="xsd:int">50</Min>
        <Name>MaxStop</Name>
        <ParameterTypeSerializable>System.Int32, mscorlib</ParameterTypeSerializable>
        <ValueSerializable>200</ValueSerializable>
      </Parameter>
      <Parameter>
        <Increment>0.5</Increment>
        <Max xsi:type="xsd:double">2</Max>
        <Min xsi:type="xsd:double">0.5</Min>
        <Name>MaxTPRatio</Name>
        <ParameterTypeSerializable>System.Double, mscorlib</ParameterTypeSerializable>
        <ValueSerializable>0.5</ValueSerializable>
      </Parameter>
    </ArrayOfParameter>
  </OptimizationParameters>
  <Strategy>
    <PantheonMasterBotV01TesterV2>
      <StartTimeH>0</StartTimeH>
      <DurationTimeH>2</DurationTimeH>
      <Reverse>{reverse}</Reverse>
    </PantheonMasterBotV01TesterV2>
  </Strategy>
</StrategyTemplate>
""",
        encoding="utf-8",
    )


def test_build_handoff_run_plan_classifies_pass_mode_and_combinations(tmp_path: Path):
    template = tmp_path / "first.xml"
    _write_template(template)

    rows = build_handoff_run_plan([template])

    assert len(rows) == 1
    row = rows[0]
    assert row.template_name == "first.xml"
    assert row.mode == "breakout"
    assert row.pass_id == "pass_1_broad_discovery"
    assert row.session_bucket == "London Early"
    assert row.estimated_combinations == 560
    assert "averageSlow=50..500 step 50" in row.optimized_parameters


def test_write_handoff_package_outputs_team_artifacts(tmp_path: Path):
    input_dir = tmp_path / "templates"
    output_dir = tmp_path / "handoff"
    input_dir.mkdir()
    _write_template(input_dir / "first.xml", reverse="true")

    rows = write_handoff_package(input_dir, output_dir)

    assert rows[0].mode == "regression"
    assert (output_dir / "run_plan.csv").exists()
    assert (output_dir / "run_plan.json").exists()
    assert (output_dir / "run_plan.md").exists()
    assert (
        output_dir
        / "templates"
        / "regression"
        / "pass_1_broad_discovery"
        / "first.xml"
    ).exists()
    readme = (output_dir / "README_FOR_TEAM.md").read_text(encoding="utf-8")
    assert "Settings.csv" in readme
    assert "Breakout and Regression results separated" in readme
