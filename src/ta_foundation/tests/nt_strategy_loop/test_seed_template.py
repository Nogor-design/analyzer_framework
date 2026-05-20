from __future__ import annotations

from pathlib import Path

from ta_foundation.nt_strategy_loop.seed_template import (
    extract_strategy_parameters,
    generate_seed_template_from_source,
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
    assert parameters[0].default == "9"
    assert parameters[0].minimum == "8"
    assert parameters[0].maximum == "10"
    assert parameters[2].type_name == "System.Boolean"
    assert parameters[2].minimum == "false"
    assert parameters[2].maximum == "true"


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
    assert parsed.estimated_combinations == 18
