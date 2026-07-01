"""
Tests for the entry-signal parameterization in nt_template_generator.

These lock in the Path A fix: a discovered entry *structure* (engulfing, pin
bar, large body, breakout...) must be written into the generated NinjaTrader
template as the EntrySignal enum + matching timeframe + pattern thresholds —
not dropped into a comment while the strategy silently trades EMA crossover.
"""
from __future__ import annotations

import pytest

from ta_foundation.analysis.strategy_discovery.nt_template_generator import (
    generate_nt_template,
    generate_per_rule_templates,
    _STRUCTURE_TO_ENTRY_SIGNAL,
)


def _sd_with_structure(structure: str, extra_conditions=None):
    conditions = [{"column": "structure", "op": "eq", "value": structure}]
    conditions += extra_conditions or []
    return {
        "signal_entry_discovery": {
            "top_signal_rules": [
                {
                    "rule_str": f"structure == {structure}",
                    "conditions": conditions,
                    "mae_p50": 10,
                    "mfe_p50": 20,
                    "win_rate": 0.55,
                    "n_signals": 120,
                }
            ],
            "baseline": {"n_signals": 500, "win_rate": 0.5, "avg_ticks": 3.0},
        }
    }


@pytest.mark.parametrize("structure,expected_enum", sorted(_STRUCTURE_TO_ENTRY_SIGNAL.items()))
def test_structure_maps_to_entry_signal(structure, expected_enum):
    sd = _sd_with_structure(structure)
    tmpl = generate_nt_template(sd, run_id="t")
    assert tmpl.parameters["EntrySignal"] == expected_enum
    assert f"<EntrySignal>{expected_enum}</EntrySignal>" in tmpl.xml_str


def test_timeframe_drives_bars_period():
    sd = _sd_with_structure("engulfing_bullish")
    tmpl = generate_nt_template(sd, run_id="t", options={"timeframe_minutes": 5})
    assert "<BaseBarsPeriodValue>5</BaseBarsPeriodValue>" in tmpl.xml_str
    assert "<Value>5</Value>" in tmpl.xml_str


def test_default_timeframe_is_one_minute():
    sd = _sd_with_structure("large_body")
    tmpl = generate_nt_template(sd, run_id="t")
    assert "<BaseBarsPeriodValue>1</BaseBarsPeriodValue>" in tmpl.xml_str


def test_pattern_param_defaults_match_patterns_py():
    """Unsupplied thresholds default to the candle patterns.py defaults, which
    are exactly what StrategyDiscoveryFilter.cs SetDefaults uses — so a
    default-threshold discovery matches with no params passed."""
    sd = _sd_with_structure("large_body")
    p = generate_nt_template(sd, run_id="t").parameters
    assert p["BodyMultiplier"] == 1.5
    assert p["WickToBodyMax"] == 0.5
    assert p["PinWickToBodyMin"] == 1.5
    assert p["PinBodyToRangeMax"] == 0.35
    assert p["EngulfRatio"] == 1.0
    assert p["DojiBodyToRangeMax"] == 0.15
    assert p["CleanAtrMult"] == 1.5
    assert p["CleanBodyToRangeMin"] == 0.60
    assert p["BodyRollLookback"] == 20
    assert p["ExtremeLookback"] == 20
    assert p["EntryMinSizeTicks"] == 4
    assert p["EntryMaxSizeTicks"] == 200
    assert p["TimingMode"] == "NextOpen"


def test_entry_params_override_flows_into_template():
    sd = _sd_with_structure("large_body")
    opts = {
        "timeframe_minutes": 5,
        "entry_params": {
            "body_multiplier": 2.0,
            "wick_to_body_max": 0.3,
            "lookback": 10,
            "min_size_ticks": 6,
            "timing_mode": "break_extreme",
            "buffer_ticks": 2.0,
        },
    }
    tmpl = generate_nt_template(sd, run_id="t", options=opts)
    p = tmpl.parameters
    assert p["BodyMultiplier"] == 2.0
    assert p["WickToBodyMax"] == 0.3
    assert p["BodyRollLookback"] == 10
    assert p["EntryMinSizeTicks"] == 6
    assert p["TimingMode"] == "BreakExtreme"
    assert p["BufferTicks"] == 2.0
    assert "<BodyMultiplier>2</BodyMultiplier>" in tmpl.xml_str
    assert "<TimingMode>BreakExtreme</TimingMode>" in tmpl.xml_str


def test_explicit_entry_signal_override():
    sd = _sd_with_structure("doji")
    tmpl = generate_nt_template(sd, run_id="t", options={"entry_signal": "NbarBreakout"})
    assert tmpl.parameters["EntrySignal"] == "NbarBreakout"


def test_ma_cross_structure_defaults_to_sma_cross():
    """A discovered 'ma_cross' structure auto-pins SmaCross (the on-disk
    PantheonMasterBotV01TesterV2 results are SMA crosses)."""
    sd = _sd_with_structure("ma_cross")
    tmpl = generate_nt_template(sd, run_id="t")
    assert tmpl.parameters["EntrySignal"] == "SmaCross"
    assert "<EntrySignal>SmaCross</EntrySignal>" in tmpl.xml_str


def test_ma_cross_with_ema_ma_type_pins_ema_cross():
    sd = _sd_with_structure("ma_cross")
    tmpl = generate_nt_template(
        sd, run_id="t", options={"entry_params": {"ma_type": "ema"}}
    )
    assert tmpl.parameters["EntrySignal"] == "EmaCross"


def test_ma_cross_with_sma_ma_type_pins_sma_cross():
    sd = _sd_with_structure("ma_cross")
    tmpl = generate_nt_template(
        sd, run_id="t", options={"entry_params": {"ma_type": "sma"}}
    )
    assert tmpl.parameters["EntrySignal"] == "SmaCross"


def test_no_structure_falls_back_to_ema_cross_with_warning():
    sd = {"signal_entry_discovery": {"top_signal_rules": [], "baseline": {}}}
    tmpl = generate_nt_template(sd, run_id="t")
    assert tmpl.parameters["EntrySignal"] == "EmaCross"
    assert any("EmaCross" in w for w in tmpl.warnings)


def test_per_rule_templates_carry_entry_signal():
    sd = {
        "signal_entry_discovery": {
            "top_signal_rules": [
                {
                    "rule_str": "structure == pin_bar_bullish",
                    "conditions": [
                        {"column": "structure", "op": "eq", "value": "pin_bar_bullish"}
                    ],
                    "mae_p50": 8,
                    "mfe_p50": 16,
                    "win_rate": 0.6,
                    "n_signals": 80,
                }
            ],
            "baseline": {"n_signals": 300, "win_rate": 0.5, "avg_ticks": 2.5},
        },
        "signal_exit_sweep": {
            "per_rule_sweep": [
                {
                    "rule_str": "structure == pin_bar_bullish",
                    "n_signals": 80,
                    "best_stop": 12,
                    "best_target": 18,
                    "best_rr": 1.5,
                    "best_win_rate": 0.6,
                    "best_pf": 1.4,
                }
            ]
        },
    }
    templates = generate_per_rule_templates(sd, run_id="t", max_rules=4)
    assert templates, "expected at least one per-rule template"
    assert templates[0].template.parameters["EntrySignal"] == "PinBarBullish"
    assert "<EntrySignal>PinBarBullish</EntrySignal>" in templates[0].template.xml_str
