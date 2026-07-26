from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from ta_foundation.nt_strategy_loop.seed_template import (
    _normalize_csharp_default,
    extract_strategy_parameters,
    generate_seed_template_from_source,
    render_seed_template,
)
from ta_foundation.optimization.nt_template import parse_strategy_optimization_template


SOURCE = """
using NinjaTrader.NinjaScript;

namespace NinjaTrader.NinjaScript.Strategies
{
    public class SeedSmoke : Strategy
    {
        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                FastPeriod = 9;
                SlowPeriod = 21;
                Reverse = false;
            }
        }

        [NinjaScriptProperty]
        [Range(2, 50)]
        public int FastPeriod { get; set; }

        [NinjaScriptProperty]
        [Range(3, 100)]
        public int SlowPeriod { get; set; }

        [NinjaScriptProperty]
        public bool Reverse { get; set; }
    }
}
"""


def test_extract_strategy_parameters_from_ninjascript_property_source() -> None:
    parameters = extract_strategy_parameters(SOURCE)

    assert [parameter.name for parameter in parameters] == ["FastPeriod", "SlowPeriod", "Reverse"]
    # Every parameter is pinned to its default value. The recipe planner is
    # the sole source of sweep ranges; auto-generated wide ranges multiplied
    # into hundreds of millions of combinations once NT saw the template.
    assert parameters[0].default == "9"
    assert parameters[0].minimum == "9"
    assert parameters[0].maximum == "9"
    assert parameters[2].type_name == "System.Boolean"
    assert parameters[2].minimum == "false"
    assert parameters[2].maximum == "false"


def test_generate_seed_template_is_parseable_by_optimizer_template_parser(tmp_path: Path) -> None:
    source = tmp_path / "SeedSmoke.cs"
    output = tmp_path / "SeedSmoke.xml"
    source.write_text(SOURCE, encoding="utf-8")

    result = generate_seed_template_from_source(source, output, instrument="NQ 06-26")
    parsed = parse_strategy_optimization_template(output)

    assert result.parameter_count == 3
    assert parsed.strategy_type == "NinjaTrader.NinjaScript.Strategies.SeedSmoke"
    assert parsed.instrument_or_instrument_list == "NQ 06-26"
    assert [parameter.name for parameter in parsed.parameters] == ["FastPeriod", "SlowPeriod", "Reverse"]
    assert parsed.strategy_values["From"] == "2026-04-14T00:00:00"
    assert parsed.strategy_values["To"] == "2026-05-14T00:00:00"
    # All parameters are pinned (Min == Max) so the baseline carries zero
    # swept dimensions. The recipe stage planner contributes the sweeps.
    assert parsed.estimated_combinations == 1
    assert all(not parameter.is_swept for parameter in parsed.parameters)


def test_seed_template_uses_the_proven_strategytemplate_format() -> None:
    """Regression guard: the seed must be in the <StrategyTemplate> shape
    NinjaTrader actually accepts, with <Category>Optimize</Category> as a
    strategy property. The legacy <NinjaTrader>-root format with no Category
    made NT reject every optimizer run with "unknown category 'NinjaScript'".
    """
    parameters = extract_strategy_parameters(SOURCE)
    xml = render_seed_template(
        strategy_name="SeedSmoke",
        parameters=parameters,
        instrument="NQ 06-26",
        optimizer_type="NinjaTrader.NinjaScript.Optimizers.DefaultOptimizer",
        optimization_fitness="NinjaTrader.NinjaScript.OptimizationFitnesses.MaxNetProfit",
        keep_best_results=500,
        from_date="2026-04-14T00:00:00",
        to_date="2026-05-14T00:00:00",
    )
    root = ET.fromstring(xml)
    assert root.tag == "StrategyTemplate"
    # No top-level BacktestType — NT's saved optimizer templates omit it.
    assert root.find("BacktestType") is None
    # <Category>Optimize</Category> must be present as a strategy property.
    categories = [e.text for e in root.iter("Category")]
    assert categories == ["Optimize"]
    # The strategy element carries the full NT serialization, not a stub.
    strategy = root.find("Strategy")[0]
    tags = {child.tag for child in strategy}
    assert {"Category", "Calculate", "InstrumentOrInstrumentList", "From", "To"} <= tags


@pytest.mark.parametrize(
    "raw, expected",
    [
        # Primitive cast + leading-dot float — the bug that crashes NT when
        # emitted verbatim. Pantheon strategies use ``IOpacity = (float).3;``.
        ("(float).3", "0.3"),
        ("(double).5", "0.5"),
        ("(int)5", "5"),
        ("( float ) .3", "0.3"),
        # Leading-dot floats without a cast.
        (".3", "0.3"),
        ("-.5", "-0.5"),
        ("+.25", "+0.25"),
        # Numeric suffixes.
        ("0.3f", "0.3"),
        ("1.5F", "1.5"),
        ("1.5m", "1.5"),
        ("100L", "100"),
        ("100ul", "100"),
        # Digit separators.
        ("1_000", "1000"),
        ("1_000.5", "1000.5"),
        # Wrapping parens.
        ("(0.5)", "0.5"),
        ("((0.5))", "0.5"),
        # Trailing line comments.
        ("0.5 // default opacity", "0.5"),
        ("5 // bars to load", "5"),
        # Quoted strings (drop the quotes).
        ('"FakeStrategy"', "FakeStrategy"),
        # Booleans normalize to lowercase.
        ("True", "true"),
        ("FALSE", "false"),
        # Already-clean values pass through unchanged.
        ("5", "5"),
        ("0.5", "0.5"),
        ("-1", "-1"),
        ("1e3", "1e3"),
        # Unparseable values fall back to "" so callers use the type default.
        # NT crashes on any of these if emitted verbatim into XML.
        ("null", ""),
        ("int.MaxValue", ""),
        ("double.MaxValue", ""),
        ("Math.PI", ""),
        ("Brushes.Yellow", ""),
        ("Calculate.OnBarClose", ""),
        ("2 * 60", ""),
        ("", ""),
        ("   ", ""),
    ],
)
def test_normalize_csharp_default(raw: str, expected: str) -> None:
    assert _normalize_csharp_default(raw) == expected


def test_seed_omits_non_optimizable_parameter_types(tmp_path: Path) -> None:
    """Regression: ``System.String``, ``System.DateTime``, ``System.Single``,
    enum and Brush ``[NinjaScriptProperty]`` declarations crash NinjaTrader's
    Strategy Analyzer at ``Optimizer.WaitForIterationsCompleted`` with a
    ``NullReferenceException`` when they appear in ``<OptimizationParameters>``.
    They must be emitted as ``<Strategy>`` defaults only.
    """
    source = tmp_path / "MixedTypes.cs"
    output = tmp_path / "MixedTypes.xml"
    source.write_text(
        """
namespace NinjaTrader.NinjaScript.Strategies
{
    public class MixedTypes : Strategy
    {
        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                FastMa = 5;
                ProfitStop = 100;
                IOpacity = (float).3;
                BotName = "MixedTypes";
                UseTrend = true;
            }
        }

        [NinjaScriptProperty]
        [Range(1, 200)]
        public int FastMa { get; set; }

        [NinjaScriptProperty]
        public double ProfitStop { get; set; }

        [NinjaScriptProperty]
        public float IOpacity { get; set; }

        [NinjaScriptProperty]
        public string BotName { get; set; }

        [NinjaScriptProperty]
        public bool UseTrend { get; set; }
    }
}
""",
        encoding="utf-8",
    )

    generate_seed_template_from_source(source, output, instrument="NQ 06-26")
    parsed = parse_strategy_optimization_template(output)
    opt_names = {p.name for p in parsed.parameters}
    # Only numeric/bool params appear in OptimizationParameters.
    assert opt_names == {"FastMa", "ProfitStop", "UseTrend"}
    assert "IOpacity" not in opt_names  # System.Single — strategy-only
    assert "BotName" not in opt_names   # System.String — strategy-only
    # Strategy section still carries the property defaults.
    assert parsed.strategy_values["IOpacity"] == "0.3"
    assert parsed.strategy_values["BotName"] == "MixedTypes"


def test_state_setdefaults_normalizes_pantheon_iopacity_cast(tmp_path: Path) -> None:
    """Regression: ``IOpacity = (float).3;`` in PantheonMaster* sources used to
    leak through as the literal string ``(float).3`` into the generated seed
    XML, which crashed NinjaTrader's Strategy Analyzer XML loader.
    """
    source = tmp_path / "PantheonMini.cs"
    output = tmp_path / "PantheonMini.xml"
    source.write_text(
        """
namespace NinjaTrader.NinjaScript.Strategies
{
    public class PantheonMini : Strategy
    {
        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                IOpacity = (float).3;
                MaxTPRatio = .5;
                MaxStop = 100;
                UseTrend = true;
            }
        }

        [NinjaScriptProperty]
        [Range(0, 1)]
        public double IOpacity { get; set; }

        [NinjaScriptProperty]
        public double MaxTPRatio { get; set; }

        [NinjaScriptProperty]
        public int MaxStop { get; set; }

        [NinjaScriptProperty]
        public bool UseTrend { get; set; }
    }
}
""",
        encoding="utf-8",
    )

    generate_seed_template_from_source(source, output, instrument="NQ 06-26")
    xml_text = output.read_text(encoding="utf-8")
    # No raw C# expression must survive into the XML.
    assert "(float)" not in xml_text
    assert "(double)" not in xml_text
    assert ">.3<" not in xml_text  # leading-dot float not emitted
    assert ">.5<" not in xml_text

    parsed = parse_strategy_optimization_template(output)
    by_name = {p.name: p for p in parsed.parameters}
    assert by_name["IOpacity"].value == "0.3"
    assert by_name["MaxTPRatio"].value == "0.5"
    assert by_name["MaxStop"].value == "100"
    assert by_name["UseTrend"].value.lower() in {"true", "false"}
