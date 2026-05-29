from __future__ import annotations

from pathlib import Path

import pytest

from ta_foundation.web.optimizer_strategy_catalog import (
    get_strategy_detail,
    list_strategies,
)


PANTHEON_CS = """\
namespace NinjaTrader.NinjaScript.Strategies
{
    public class FakeStrategy : Strategy
    {
        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Name                 = "FakeStrategy";
                averageFast          = 5;
                averageSlow          = 200;
                UseTrend             = true;
                MaxStop              = 100;
                MaxTPRatio           = 1.5;
            }
            else if (State == State.Configure)
            {
            }
        }

        [NinjaScriptProperty]
        [Range(2, int.MaxValue)]
        [Display(Name = "averageFast ", GroupName = "Fast Averages", Order = 1)]
        public int averageFast { get; set; }

        [NinjaScriptProperty]
        [Range(2, int.MaxValue)]
        [Display(Name = "averageSlow", GroupName = "Slow Averages", Order = 2)]
        public int averageSlow { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "UseTrend", GroupName = "Trend Averages", Order = 3)]
        public bool UseTrend { get; set; }

        [NinjaScriptProperty]
        [Range(1, int.MaxValue)]
        [Display(Name = "MaxStop", GroupName = "Risk", Order = 4)]
        public int MaxStop { get; set; }

        [NinjaScriptProperty]
        [Range(.1, double.MaxValue)]
        [Display(Name = "MaxTPRatio", GroupName = "Risk", Order = 5,
                 Description = "Take-profit multiple")]
        public double MaxTPRatio { get; set; }
    }
}
"""

SEED_TEMPLATE_XML = """<?xml version="1.0"?>
<NinjaTrader>
  <StrategyType>NinjaTrader.NinjaScript.Strategies.FakeStrategy</StrategyType>
  <OptimizerType>NinjaTrader.NinjaScript.Optimizers.DefaultOptimizer</OptimizerType>
  <OptimizationFitness>NinjaTrader.NinjaScript.OptimizationFitnesses.NetProfit</OptimizationFitness>
  <BacktestType>Optimize</BacktestType>
  <Strategy>
    <Strategy>
      <Reverse>false</Reverse>
      <Category>Optimize</Category>
      <InstrumentOrInstrumentList>NQ 06-26</InstrumentOrInstrumentList>
      <From>2026-04-14T00:00:00</From>
      <To>2026-05-14T00:00:00</To>
      <StartTimeH>8</StartTimeH>
      <DurationTimeH>4</DurationTimeH>
    </Strategy>
  </Strategy>
  <OptimizationParameters>
    <ArrayOfParameter>
      <Parameter>
        <Name>averageSlow</Name>
        <Min>50</Min>
        <Max>200</Max>
        <Increment>50</Increment>
        <ValueSerializable>50</ValueSerializable>
        <ParameterTypeSerializable>System.Int32</ParameterTypeSerializable>
      </Parameter>
      <Parameter>
        <Name>MaxStop</Name>
        <Min>100</Min>
        <Max>100</Max>
        <Increment>50</Increment>
        <ValueSerializable>100</ValueSerializable>
        <ParameterTypeSerializable>System.Int32</ParameterTypeSerializable>
      </Parameter>
    </ArrayOfParameter>
  </OptimizationParameters>
</NinjaTrader>
"""


@pytest.fixture
def nt_install(tmp_path: Path) -> tuple[Path, Path]:
    source = tmp_path / "Strategies"
    templates = tmp_path / "templates" / "Strategy"
    source.mkdir(parents=True)
    templates.mkdir(parents=True)
    (source / "FakeStrategy.cs").write_text(PANTHEON_CS, encoding="utf-8")
    (source / "@SampleNoise.cs").write_text("// excluded", encoding="utf-8")
    strategy_templates = templates / "FakeStrategy"
    strategy_templates.mkdir()
    (strategy_templates / "Pass1.xml").write_text(SEED_TEMPLATE_XML, encoding="utf-8")
    return source, templates


def test_list_strategies_excludes_sample_files(nt_install):
    source, templates = nt_install
    strategies = list_strategies(source_dir=source, template_dir=templates)
    ids = [s.strategy_id for s in strategies]
    assert ids == ["FakeStrategy"]
    fake = strategies[0]
    assert fake.parameter_count == 5
    assert fake.seed_template_count == 1


def test_get_strategy_detail_extracts_parameter_metadata(nt_install):
    source, templates = nt_install
    detail = get_strategy_detail("FakeStrategy", source_dir=source, template_dir=templates)
    assert detail is not None
    by_name = {p.name: p for p in detail.parameters}

    fast = by_name["averageFast"]
    assert fast.type_name == "int"
    assert fast.default == 5
    assert fast.range_min == 2.0
    assert fast.range_max is None  # int.MaxValue → None
    assert fast.group_name == "Fast Averages"
    assert fast.order == 1

    use_trend = by_name["UseTrend"]
    assert use_trend.type_name == "bool"
    assert use_trend.default is True

    ratio = by_name["MaxTPRatio"]
    assert ratio.type_name == "double"
    assert ratio.default == 1.5
    assert ratio.range_min == 0.1
    assert ratio.description == "Take-profit multiple"


def test_get_strategy_detail_extracts_multiline_nt_property_declarations(tmp_path: Path):
    source = tmp_path / "Strategies"
    templates = tmp_path / "templates" / "Strategy"
    source.mkdir(parents=True)
    templates.mkdir(parents=True)
    (source / "MaStyle.cs").write_text(
        """
namespace NinjaTrader.NinjaScript.Strategies
{
    public class MaStyle : Strategy
    {
        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                FastMa = 1;
                StartTime = DateTime.Parse("07:00", System.Globalization.CultureInfo.InvariantCulture);
            }
        }

        [NinjaScriptProperty]
        [Range(1, int.MaxValue)]
        [Display(Name="FastMa", Order=1, GroupName="Parameters")]
        public int FastMa
        { get; set; }

        [NinjaScriptProperty]
        [PropertyEditor("NinjaTrader.Gui.Tools.TimeEditorKey")]
        [Display(Name="StartTime", Order=2, GroupName="Parameters")]
        public DateTime StartTime
        { get; set; }
    }
}
""",
        encoding="utf-8",
    )

    detail = get_strategy_detail("MaStyle", source_dir=source, template_dir=templates)

    assert detail is not None
    by_name = {p.name: p for p in detail.parameters}
    assert by_name["FastMa"].type_name == "int"
    assert by_name["FastMa"].range_min == 1.0
    assert by_name["StartTime"].type_name == "DateTime"
    assert by_name["StartTime"].default == "07:00"


def test_get_strategy_detail_summarizes_seed_templates(nt_install):
    source, templates = nt_install
    detail = get_strategy_detail("FakeStrategy", source_dir=source, template_dir=templates)
    assert detail is not None
    assert len(detail.seed_templates) == 1
    seed = detail.seed_templates[0]
    assert seed.name == "Pass1"
    assert seed.mode == "breakout"
    assert seed.instrument_or_instrument_list == "NQ 06-26"
    assert seed.start_hour == 8
    assert seed.duration_hours == 4
    assert seed.backtest_type == "Optimize"
    assert seed.category == "Optimize"
    assert seed.from_date == "2026-04-14T00:00:00"
    assert seed.to_date == "2026-05-14T00:00:00"
    assert seed.is_optimizer_seed is True
    assert seed.seed_issues == []
    assert "averageSlow" in seed.swept_parameter_names
    # 50,100,150,200 → 4 combinations; MaxStop is not swept (min==max)
    assert seed.estimated_combinations == 4


def test_get_strategy_detail_marks_regular_ninjascript_template_invalid(nt_install):
    source, templates = nt_install
    bad = templates / "FakeStrategy" / "RegularSettings.xml"
    bad.write_text(
        SEED_TEMPLATE_XML
        .replace("<BacktestType>Optimize</BacktestType>", "")
        .replace("<Category>Optimize</Category>", "<Category>NinjaScript</Category>")
        .replace("<InstrumentOrInstrumentList>NQ 06-26</InstrumentOrInstrumentList>", ""),
        encoding="utf-8",
    )

    detail = get_strategy_detail("FakeStrategy", source_dir=source, template_dir=templates)
    assert detail is not None
    seeds = {seed.name: seed for seed in detail.seed_templates}
    bad_seed = seeds["RegularSettings"]
    assert bad_seed.is_optimizer_seed is False
    assert "not_optimize_template:NinjaScript" in bad_seed.seed_issues
    assert "missing_instrument" in bad_seed.seed_issues


def test_get_strategy_detail_returns_none_for_unknown_id(nt_install):
    source, templates = nt_install
    assert get_strategy_detail("NoSuchStrategy", source_dir=source, template_dir=templates) is None


def test_get_strategy_detail_handles_split_brace_property_declaration(tmp_path: Path):
    """NinjaTrader's own strategy templates write the property body on a
    separate line (``public T Name`` then ``{ get; set; }``). Recipe Setup must
    extract those parameters as well — otherwise CandleBenderV5ReverseV2 reports
    5 parameters when it actually exposes 38.
    """
    source = tmp_path / "src"
    templates = tmp_path / "tpl"
    source.mkdir()
    templates.mkdir()
    (source / "SplitBrace.cs").write_text(
        """
namespace NinjaTrader.NinjaScript.Strategies
{
    public class SplitBrace : Strategy
    {
        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                MaxLoss = 200;
                PLRatio = 0.8;
                UseTrend = true;
            }
        }

        [NinjaScriptProperty]
        [Range(1, double.MaxValue)]
        [Display(Name="Max Loss", Order=1, GroupName="Parameters")]
        public double MaxLoss
        { get; set; }

        [NinjaScriptProperty]
        [Range(0, double.MaxValue)]
        public double PLRatio
        { get; set; }

        [NinjaScriptProperty]
        public bool UseTrend { get; set; }
    }
}
""",
        encoding="utf-8",
    )

    detail = get_strategy_detail("SplitBrace", source_dir=source, template_dir=templates)
    assert detail is not None
    by_name = {p.name: p for p in detail.parameters}
    assert set(by_name) == {"MaxLoss", "PLRatio", "UseTrend"}
    assert by_name["MaxLoss"].type_name == "double"
    assert by_name["MaxLoss"].default == 200
    assert by_name["PLRatio"].default == 0.8
    assert by_name["UseTrend"].type_name == "bool"
    assert by_name["UseTrend"].default is True
