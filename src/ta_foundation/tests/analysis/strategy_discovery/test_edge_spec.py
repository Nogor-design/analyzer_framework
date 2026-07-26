"""
Tests for EdgeSpec extraction and the confirmation verdict.
"""
from __future__ import annotations

import pytest

from ta_foundation.analysis.strategy_discovery.edge_spec import (
    EdgeSpec,
    compare_to_discovery,
    edge_spec_from_discovery,
    edge_spec_from_rule,
)


def _sd(structure="engulfing_bullish", pf=1.62, stop=24, target=36):
    return {
        "signal_entry_discovery": {
            "top_signal_rules": [
                {
                    "rule_str": f"structure == {structure}",
                    "conditions": [{"column": "structure", "op": "eq", "value": structure}],
                    "win_rate": 0.58,
                    "n_signals": 140,
                }
            ]
        },
        "signal_exit_sweep": {
            "overall_best": {"stop": stop, "target": target, "avg_profit_factor": pf}
        },
    }


def test_edge_spec_from_discovery_pulls_entry_and_metrics():
    spec = edge_spec_from_discovery(_sd(), run_id="r1", timeframe_minutes=5)
    assert spec is not None
    assert spec.structure == "engulfing_bullish"
    assert spec.entry_signal == "EngulfingBullish"
    assert spec.direction == 1            # bullish structure → long only
    assert spec.timeframe_minutes == 5
    assert spec.stop_ticks == 24
    assert spec.target_ticks == 36
    assert spec.observed_pf == 1.62
    assert spec.observed_n == 140
    assert spec.source_run_id == "r1"


def test_direction_for_bearish_and_both():
    spec_short = edge_spec_from_discovery(_sd(structure="pin_bar_bearish"))
    assert spec_short.direction == -1
    spec_both = edge_spec_from_discovery(_sd(structure="large_body"))
    assert spec_both.direction == 0


def test_edge_spec_none_when_no_structure():
    rule = {"rule_str": "adx >= 25", "conditions": [{"column": "adx", "op": "gte", "value": 25}]}
    assert edge_spec_from_rule(rule) is None


def test_edge_spec_skips_rules_without_structure_until_one_matches():
    sd = {
        "signal_entry_discovery": {
            "top_signal_rules": [
                {"rule_str": "adx>=25", "conditions": [{"column": "adx", "value": 25}]},
                {
                    "rule_str": "structure == doji",
                    "conditions": [{"column": "structure", "value": "doji"}],
                    "n_signals": 60,
                },
            ]
        }
    }
    spec = edge_spec_from_discovery(sd)
    assert spec is not None and spec.entry_signal == "Doji"


def test_template_options_round_trip_into_generator():
    spec = edge_spec_from_discovery(_sd(), timeframe_minutes=5)
    opts = spec.template_options()
    assert opts["entry_signal"] == "EngulfingBullish"
    assert opts["timeframe_minutes"] == 5
    assert opts["entry_params"]["timing_mode"] == "next_open"


@pytest.mark.parametrize(
    "nt_pf,nt_n,expected",
    [
        (1.55, 120, "confirmed"),    # within 20% of 1.62
        (1.40, 120, "confirmed"),    # 1.62*0.8 = 1.296; 1.40 >= that
        (1.10, 120, "decayed"),      # edge but well below
        (0.90, 120, "diverged"),     # no edge in NT
        (1.60, 5, "underpowered"),   # too few trades
    ],
)
def test_compare_to_discovery_verdicts(nt_pf, nt_n, expected):
    spec = edge_spec_from_discovery(_sd())
    v = compare_to_discovery(spec, nt_pf=nt_pf, nt_n=nt_n)
    assert v.verdict == expected


def test_compare_confirmed_when_no_observed_pf():
    spec = EdgeSpec(structure="doji", entry_signal="Doji", observed_pf=None)
    v = compare_to_discovery(spec, nt_pf=1.3, nt_n=50)
    assert v.verdict == "confirmed"
    assert "no discovered PF" in v.notes
