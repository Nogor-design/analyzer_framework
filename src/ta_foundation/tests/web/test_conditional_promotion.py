from __future__ import annotations

import yaml

from ta_foundation.web.conditional_promotion import (
    promote_rule_to_config,
    write_conditional_probe_yamls,
)


def _parent_config() -> dict:
    return {
        "report": {"title": "Parent", "output": "parent.html"},
        "discovery": {"stage": "03_levels_regions", "instrument": "NQ"},
        "level_discovery": {
            "enabled": True,
            "min_trades": 20,
            "timeframes": [1],
            "signals": {
                "swing_level_test": {"enabled": True},
                "vwap_reclaim_reject": {
                    "enabled": True,
                    "min_dist_ticks": [6, 8],
                    "max_dist_ticks": [20, 24],
                    "min_pierce_ticks": [1, 2],
                    "direction": [0],
                    "invert_direction": [True],
                },
                "liquidity_sweep_failure": {"enabled": True},
            },
            "entry_timing": {
                "next_open": {"enabled": True},
                "break_extreme": {"enabled": True},
                "body_midpoint": {"enabled": True},
            },
            "outcome": {
                "atr": {"enabled": True, "target_mult": 1.5, "stop_mult": 1.0},
                "ticks": {"enabled": True, "take_profit": [16, 20], "stop": [10, 12]},
            },
        },
    }


def _sidecar() -> dict:
    return {
        "report_html": "D:/runs/parent.html",
        "stage": {"id": "03_levels_regions"},
        "rankings": [
            {
                "rank": 2,
                "family": "level",
                "signal": "vwap_reclaim_reject",
                "params": {"min_pierce_ticks": 2, "invert_direction": True},
                "outcome": {"mode": "ticks", "tp_ticks": 20, "sl_ticks": 10},
            }
        ],
    }


def test_promote_rule_converts_london_vwap_reject_distance_timing_and_outcome():
    rule = {
        "rank": 1,
        "rule_str": (
            "session_label == London AND level_type == vwap_reject AND "
            "level_dist_ticks >= 7.2 AND timing_mode == next_open AND "
            "outcome_mode == ticks_20_10"
        ),
        "conditions": [
            {"column": "session_label", "op": "eq", "value": "London"},
            {"column": "level_type", "op": "eq", "value": "vwap_reject"},
            {"column": "level_dist_ticks", "op": "gte", "value": 7.2},
            {"column": "timing_mode", "op": "eq", "value": "next_open"},
            {"column": "outcome_mode", "op": "eq", "value": "ticks_20_10"},
        ],
        "n_trades": 21,
        "profit_factor": 2.8,
    }

    cfg = promote_rule_to_config(
        _parent_config(),
        sidecar=_sidecar(),
        ranking=_sidecar()["rankings"][0],
        rule=rule,
        parent_config_path="discovery/03_parent.yaml",
        sidecar_path="out/03_parent_summary.json",
    )

    assert cfg is not None
    level = cfg["level_discovery"]
    assert level["session_filter"] == {
        "hour_from": 0,
        "minute_from": 0,
        "hour_to": 6,
        "minute_to": 0,
        "timezone": "America/Denver",
    }
    assert level["signals"]["swing_level_test"]["enabled"] is False
    assert level["signals"]["liquidity_sweep_failure"]["enabled"] is False
    vwap = level["signals"]["vwap_reclaim_reject"]
    assert vwap["enabled"] is True
    assert vwap["direction"] == [-1]
    assert vwap["invert_direction"] == [True]
    assert vwap["min_dist_ticks"] == [8]
    assert level["entry_timing"]["next_open"]["enabled"] is True
    assert level["entry_timing"]["break_extreme"]["enabled"] is False
    assert level["entry_timing"]["body_midpoint"]["enabled"] is False
    assert level["outcome"]["atr"]["enabled"] is False
    assert level["outcome"]["ticks"]["take_profit"] == [20]
    assert level["outcome"]["ticks"]["stop"] == [10]
    assert cfg["conditional_promotion"]["parent_rank"] == 2
    assert cfg["conditional_promotion"]["rule_string"] == rule["rule_str"]
    assert cfg["conditional_promotion"]["parent_params"] == {"min_pierce_ticks": 2, "invert_direction": True}
    assert cfg["conditional_promotion"]["status"] == "research_candidate"


def test_promote_rule_converts_vwap_reclaim_and_max_distance():
    rule = {
        "rank": 3,
        "rule_str": "level_type == vwap_reclaim AND level_dist_ticks <= 12.1",
        "conditions": [
            {"column": "level_type", "op": "eq", "value": "vwap_reclaim"},
            {"column": "level_dist_ticks", "op": "lte", "value": 12.1},
        ],
    }

    cfg = promote_rule_to_config(
        _parent_config(),
        sidecar=_sidecar(),
        ranking=_sidecar()["rankings"][0],
        rule=rule,
    )

    assert cfg is not None
    vwap = cfg["level_discovery"]["signals"]["vwap_reclaim_reject"]
    assert vwap["direction"] == [1]
    assert vwap["max_dist_ticks"] == [13]


def test_promote_market_pos_long_respects_inverted_direction():
    rule = {
        "rank": 2,
        "rule_str": "market_pos == Long",
        "conditions": [{"column": "market_pos", "op": "eq", "value": "Long"}],
    }

    cfg = promote_rule_to_config(
        _parent_config(),
        sidecar=_sidecar(),
        ranking=_sidecar()["rankings"][0],
        rule=rule,
    )

    assert cfg is not None
    vwap = cfg["level_discovery"]["signals"]["vwap_reclaim_reject"]
    assert vwap["invert_direction"] == [True]
    assert vwap["direction"] == [-1]


def test_promote_direction_short_respects_inverted_direction():
    rule = {
        "rank": 2,
        "rule_str": "direction == -1.0",
        "conditions": [{"column": "direction", "op": "eq", "value": "-1.0"}],
    }

    cfg = promote_rule_to_config(
        _parent_config(),
        sidecar=_sidecar(),
        ranking=_sidecar()["rankings"][0],
        rule=rule,
    )

    assert cfg is not None
    assert cfg["level_discovery"]["signals"]["vwap_reclaim_reject"]["direction"] == [1]


def test_timing_rule_that_is_already_fixed_is_not_promoted():
    rule = {
        "rank": 1,
        "rule_str": "timing_mode == next_open",
        "conditions": [{"column": "timing_mode", "op": "eq", "value": "next_open"}],
    }
    parent = _parent_config()
    parent["level_discovery"]["entry_timing"]["break_extreme"]["enabled"] = False
    parent["level_discovery"]["entry_timing"]["body_midpoint"]["enabled"] = False

    assert promote_rule_to_config(
        parent,
        sidecar=_sidecar(),
        ranking=_sidecar()["rankings"][0],
        rule=rule,
    ) is None


def test_unconvertible_rule_returns_none():
    rule = {
        "rank": 1,
        "rule_str": "regime == trend",
        "conditions": [{"column": "regime", "op": "eq", "value": "trend"}],
    }

    assert promote_rule_to_config(
        _parent_config(),
        sidecar=_sidecar(),
        ranking=_sidecar()["rankings"][0],
        rule=rule,
    ) is None


def test_write_conditional_probe_yamls_writes_convertible_nested_rules(tmp_path):
    sidecar = _sidecar()
    sidecar["rankings"][0]["conditional_rules"] = [
        {
            "rank": 1,
            "rule_str": "session_label == London",
            "conditions": [{"column": "session_label", "op": "eq", "value": "London"}],
            "n_trades": 25,
            "profit_factor": 1.2,
        },
        {
            "rank": 2,
            "rule_str": "regime == trend",
            "conditions": [{"column": "regime", "op": "eq", "value": "trend"}],
            "n_trades": 25,
            "profit_factor": 1.2,
        },
    ]

    written = write_conditional_probe_yamls(
        parent_config=_parent_config(),
        sidecar=sidecar,
        generated_dir=tmp_path,
        parent_config_path="discovery/03_parent.yaml",
        sidecar_path="outputs/03_parent_summary.json",
    )

    assert len(written) == 1
    payload = yaml.safe_load(written[0].read_text(encoding="utf-8"))
    assert payload["level_discovery"]["session_filter"]["hour_from"] == 0
    assert payload["conditional_promotion"]["parent_sidecar"] == "outputs/03_parent_summary.json"


def test_write_conditional_probe_yamls_skips_already_promoted_parent(tmp_path):
    parent = _parent_config()
    parent["conditional_promotion"] = {"parent_rank": 1}
    sidecar = _sidecar()
    sidecar["rankings"][0]["conditional_rules"] = [
        {
            "rank": 1,
            "rule_str": "session_label == London",
            "conditions": [{"column": "session_label", "op": "eq", "value": "London"}],
            "n_trades": 25,
            "profit_factor": 1.2,
        },
    ]

    written = write_conditional_probe_yamls(
        parent_config=parent,
        sidecar=sidecar,
        generated_dir=tmp_path,
    )

    assert written == []


def test_write_conditional_probe_yamls_dedupes_identical_effective_configs(tmp_path):
    sidecar = _sidecar()
    sidecar["rankings"][0]["conditional_rules"] = [
        {
            "rank": 1,
            "rule_str": "level_type == vwap_reject",
            "conditions": [{"column": "level_type", "op": "eq", "value": "vwap_reject"}],
            "n_trades": 25,
            "profit_factor": 1.2,
        },
        {
            "rank": 2,
            "rule_str": "level_type == vwap_reject AND timing_mode == next_open",
            "conditions": [
                {"column": "level_type", "op": "eq", "value": "vwap_reject"},
                {"column": "timing_mode", "op": "eq", "value": "next_open"},
            ],
            "n_trades": 25,
            "profit_factor": 1.2,
        },
    ]
    parent = _parent_config()
    parent["level_discovery"]["entry_timing"]["break_extreme"]["enabled"] = False
    parent["level_discovery"]["entry_timing"]["body_midpoint"]["enabled"] = False

    written = write_conditional_probe_yamls(
        parent_config=parent,
        sidecar=sidecar,
        generated_dir=tmp_path,
    )

    assert len(written) == 1


def test_write_conditional_probe_yamls_skips_losing_rules_by_default(tmp_path):
    sidecar = _sidecar()
    sidecar["rankings"][0]["conditional_rules"] = [
        {
            "rank": 1,
            "rule_str": "level_dist_ticks <= 16.395",
            "conditions": [{"column": "level_dist_ticks", "op": "lte", "value": 16.395}],
            "n_trades": 605,
            "profit_factor": 0.8021,
        },
    ]

    written = write_conditional_probe_yamls(
        parent_config=_parent_config(),
        sidecar=sidecar,
        generated_dir=tmp_path,
    )

    assert written == []
