from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List

import pytest

from ta_foundation.strategies.LargeCandleReversal.generate_nt8_template import (
    HOLD_RULE_MAP,
    IMPLEMENTED_ONSETS,
    OPTIMIZER_PARAMETER_NAMES,
    SESSION_LABELS_CANONICAL,
    blueprint_to_fields,
    canonical_session_label,
    parse_candle_bucket,
    patch_seed_text,
    write_templates_from_file,
)


# ---------------------------------------------------------------------------
# Seed XML fixture — a minimal but faithful stand-in for a user-saved NT8
# template.  Every <Parameter> in the real template has:
#   Name, Min, Max, ValueSerializable, Increment,
#   ParameterTypeSerializable, EnumValuesSerializable (empty or with <string>)
# The Strategy/<LargeCandleReversal> section carries both NT framework fields
# and every custom property.
# ---------------------------------------------------------------------------

_ASSEMBLY_HASH = "82fd54e78c594c619c42fb2234eb15e4"  # install-specific in real files


def _param_xml(name: str, value: str, type_serialisable: str,
               xsd_type: str, enum_values: List[str] | None = None) -> str:
    if enum_values:
        enum_xml = "<EnumValuesSerializable>" + "".join(f"<string>{v}</string>" for v in enum_values) + "</EnumValuesSerializable>"
    else:
        enum_xml = "<EnumValuesSerializable />"
    return (
        "<Parameter>"
        f"{enum_xml}"
        "<Increment>1</Increment>"
        f'<Max xsi:type="xsd:{xsd_type}">{value}</Max>'
        f'<Min xsi:type="xsd:{xsd_type}">{value}</Min>'
        f"<Name>{name}</Name>"
        f"<ParameterTypeSerializable>{type_serialisable}</ParameterTypeSerializable>"
        f"<ValueSerializable>{value}</ValueSerializable>"
        "</Parameter>"
    )


def _enum_param(name: str, value: str, enum_type: str) -> str:
    type_ser = f"NinjaTrader.NinjaScript.Strategies.{enum_type}, {_ASSEMBLY_HASH}, Version=8.1.7.0, Culture=neutral, PublicKeyToken=null"
    return (
        "<Parameter>"
        f"<EnumValuesSerializable><string>{value}</string></EnumValuesSerializable>"
        "<Increment>1</Increment>"
        '<Max xsi:type="xsd:int">0</Max>'
        '<Min xsi:type="xsd:int">0</Min>'
        f"<Name>{name}</Name>"
        f"<ParameterTypeSerializable>{type_ser}</ParameterTypeSerializable>"
        f"<ValueSerializable>{value}</ValueSerializable>"
        "</Parameter>"
    )


def _int_param(name: str, value: int) -> str:
    type_ser = "System.Int32, mscorlib, Version=4.0.0.0, Culture=neutral, PublicKeyToken=b77a5c561934e089"
    return _param_xml(name, str(value), type_ser, "int")


def _double_param(name: str, value: float) -> str:
    type_ser = "System.Double, mscorlib, Version=4.0.0.0, Culture=neutral, PublicKeyToken=b77a5c561934e089"
    return _param_xml(name, str(value), type_ser, "double")


def _bool_param(name: str, value: bool) -> str:
    type_ser = "System.Boolean, mscorlib, Version=4.0.0.0, Culture=neutral, PublicKeyToken=b77a5c561934e089"
    pascal = "True" if value else "False"
    lower = "true" if value else "false"
    return (
        "<Parameter>"
        "<EnumValuesSerializable />"
        "<Increment>1</Increment>"
        f'<Max xsi:type="xsd:boolean">{lower}</Max>'
        f'<Min xsi:type="xsd:boolean">{lower}</Min>'
        f"<Name>{name}</Name>"
        f"<ParameterTypeSerializable>{type_ser}</ParameterTypeSerializable>"
        f"<ValueSerializable>{pascal}</ValueSerializable>"
        "</Parameter>"
    )


def _build_seed_xml() -> str:
    """Minimal seed XML that contains every field the generator should patch.
    Note: this seed mirrors what NT would emit after the .cs patch — the ten
    AllowSession* Parameter blocks are NOT included, so we exercise the
    generator's auto-insert path."""
    params = "".join([
        _enum_param("OnsetCondition",  "FirstLargeAfterFailedContinuation", "LcrOnsetCondition"),
        _enum_param("DirectionPolicy", "CounterToFailedContinuation",       "LcrDirectionPolicy"),
        _int_param("CandleLookbackBars", 20),
        _enum_param("CandleBasis", "Range", "LcrCandleBasis"),
        _double_param("ThresholdMultiplier", 1.5),
        _int_param("MinBodyTicks", 2),
        _int_param("MinRangeTicks", 4),
        _int_param("MaxBodyTicks", 0),
        _int_param("MaxRangeTicks", 0),
        _int_param("FailedContinuationLookbackSignals", 1),
        _int_param("FailedSignalRebreakWindowBars", 10),
        _int_param("AtrPeriod", 14),
        _double_param("VwapStretchAtrMin", 0.75),
        _double_param("VolumeMultipleMin", 2.5),
        _int_param("DirectionStreakMin", 3),
        _double_param("RangeExpansionMultipleMin", 1.5),
        _bool_param("RequireContextGates", False),
        _int_param("QuietLookbackMinutes", 60),
        _int_param("QuietMaxPriorSignals", 1),
        _enum_param("SessionMode", "AllSession", "LcrSessionMode"),
        _enum_param("TriggerEvent", "OnBarClose", "LcrTriggerEvent"),
        _enum_param("FillType", "MarketOnNextOpen", "LcrFillType"),
        _int_param("MaxEntriesPerSession", 1),
        _int_param("Contracts", 1),
        _enum_param("StopStyle", "SignalCandleExtremeWithCap", "LcrStopStyle"),
        _int_param("StopBaseOffsetTicks", 4),
        _int_param("MaxStopTicks", 100),
        _double_param("AtrFallbackMultiple", 1.2),
        _int_param("RecommendedStopTicks", 40),
        _int_param("EvaluationBar", 2),
        _enum_param("HoldRuleMode", "AnyOf", "LcrHoldRuleMode"),
        _enum_param("PrimaryHoldRule", "MidpointReclaimYes", "LcrPrimaryHoldRule"),
        _int_param("MidpointReclaimBars", 2),
        _int_param("RebreakCheckBars", 2),
        _double_param("ExplosiveFav2BarPctMin", 45),
        _double_param("ExplosiveAdv2BarPctMax", 20),
        _double_param("OrderlyFav2BarPctMin", 25),
        _double_param("OrderlyAdv2BarPctMax", 35),
        _double_param("Fav2BarOnlyPctMin", 35),
        _double_param("Adv2BarOnlyPctMax", 20),
        _enum_param("OnFailAction", "ExitAtMarket", "LcrOnFailAction"),
        _enum_param("OnPassAction", "TrailForRunner", "LcrOnPassAction"),
        _int_param("ScalpTargetPct", 30),
        _int_param("ExpansionTargetPct", 62),
        _int_param("RunnerTargetPct", 125),
        _int_param("TimeStopMinutes", 30),
        _enum_param("TrailStyle", "Atr", "LcrTrailStyle"),
        _double_param("AtrTrailMultiple", 1.5),
        _int_param("BaseUnit", 1),
        _int_param("MaxAddOns", 1),
        _int_param("MaxTotalUnits", 2),
        _bool_param("EnableDebugPrint", False),
        _bool_param("DrawMarkers", True),
    ])
    strategy_body_fields = [
        "<BlueprintId>lcr_default</BlueprintId>",
        "<OnsetCondition>FirstLargeAfterFailedContinuation</OnsetCondition>",
        "<DirectionPolicy>CounterToFailedContinuation</DirectionPolicy>",
        "<CandleLookbackBars>20</CandleLookbackBars>",
        "<CandleBasis>Range</CandleBasis>",
        "<ThresholdMultiplier>1.5</ThresholdMultiplier>",
        "<MinBodyTicks>2</MinBodyTicks>",
        "<MinRangeTicks>4</MinRangeTicks>",
        "<MaxBodyTicks>0</MaxBodyTicks>",
        "<MaxRangeTicks>0</MaxRangeTicks>",
        "<FailedContinuationLookbackSignals>1</FailedContinuationLookbackSignals>",
        "<FailedSignalRebreakWindowBars>10</FailedSignalRebreakWindowBars>",
        "<AtrPeriod>14</AtrPeriod>",
        "<VwapStretchAtrMin>0.75</VwapStretchAtrMin>",
        "<VolumeMultipleMin>2.5</VolumeMultipleMin>",
        "<DirectionStreakMin>3</DirectionStreakMin>",
        "<RangeExpansionMultipleMin>1.5</RangeExpansionMultipleMin>",
        "<RequireContextGates>false</RequireContextGates>",
        "<QuietLookbackMinutes>60</QuietLookbackMinutes>",
        "<QuietMaxPriorSignals>1</QuietMaxPriorSignals>",
        "<SessionMode>AllSession</SessionMode>",
        # legacy seed still carries AllowedSessionsCsv — generator strips it.
        "<AllowedSessionsCsv />",
        "<TriggerEvent>OnBarClose</TriggerEvent>",
        "<FillType>MarketOnNextOpen</FillType>",
        "<MaxEntriesPerSession>1</MaxEntriesPerSession>",
        "<Contracts>1</Contracts>",
        "<StopStyle>SignalCandleExtremeWithCap</StopStyle>",
        "<StopBaseOffsetTicks>4</StopBaseOffsetTicks>",
        "<MaxStopTicks>100</MaxStopTicks>",
        "<AtrFallbackMultiple>1.2</AtrFallbackMultiple>",
        "<RecommendedStopTicks>40</RecommendedStopTicks>",
        "<EvaluationBar>2</EvaluationBar>",
        "<HoldRuleMode>AnyOf</HoldRuleMode>",
        "<PrimaryHoldRule>MidpointReclaimYes</PrimaryHoldRule>",
        "<MidpointReclaimBars>2</MidpointReclaimBars>",
        "<RebreakCheckBars>2</RebreakCheckBars>",
        "<ExplosiveFav2BarPctMin>45</ExplosiveFav2BarPctMin>",
        "<ExplosiveAdv2BarPctMax>20</ExplosiveAdv2BarPctMax>",
        "<OrderlyFav2BarPctMin>25</OrderlyFav2BarPctMin>",
        "<OrderlyAdv2BarPctMax>35</OrderlyAdv2BarPctMax>",
        "<Fav2BarOnlyPctMin>35</Fav2BarOnlyPctMin>",
        "<Adv2BarOnlyPctMax>20</Adv2BarOnlyPctMax>",
        "<OnFailAction>ExitAtMarket</OnFailAction>",
        "<OnPassAction>TrailForRunner</OnPassAction>",
        "<ScalpTargetPct>30</ScalpTargetPct>",
        "<ExpansionTargetPct>62</ExpansionTargetPct>",
        "<RunnerTargetPct>125</RunnerTargetPct>",
        "<TimeStopMinutes>30</TimeStopMinutes>",
        "<TrailStyle>Atr</TrailStyle>",
        "<AtrTrailMultiple>1.5</AtrTrailMultiple>",
        "<BaseUnit>1</BaseUnit>",
        "<MaxAddOns>1</MaxAddOns>",
        "<MaxTotalUnits>2</MaxTotalUnits>",
        "<EnableDebugPrint>false</EnableDebugPrint>",
        "<DrawMarkers>true</DrawMarkers>",
    ]
    framework = (
        "<InstrumentOrInstrumentList>NQ 06-26</InstrumentOrInstrumentList>"
        "<From>2026-04-18T00:00:00</From>"
        "<To>2026-04-23T00:00:00</To>"
        "<BarsRequiredToTrade>60</BarsRequiredToTrade>"
    )
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<StrategyTemplate>'
        '<StrategyType>NinjaTrader.NinjaScript.Strategies.LargeCandleReversal</StrategyType>'
        '<OptimizationParameters>'
        '<ArrayOfParameter xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        f'{params}'
        '</ArrayOfParameter>'
        '</OptimizationParameters>'
        '<Strategy>'
        '<LargeCandleReversal xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        f'{framework}'
        f'{"".join(strategy_body_fields)}'
        '</LargeCandleReversal>'
        '</Strategy>'
        '</StrategyTemplate>'
    )


# ---------------------------------------------------------------------------
# Blueprint fixtures
# ---------------------------------------------------------------------------

def _blueprint(**overrides: Any) -> Dict[str, Any]:
    bp: Dict[str, Any] = {
        "blueprint_id": "flc_x_midpoint_reclaim_yes_1m",
        "provenance": {
            "onset_condition": "first_large_after_failed_continuation",
            "early_path_condition": "midpoint_reclaim_yes",
            "n": 1432,
            "win_rate_pct": 92.6,
        },
        "timeframe_minutes": 1,
        "direction_policy": "counter_to_failed_continuation",
        "onset_detection": {
            "candle_size": {
                "lookback_bars": 20, "basis": "range", "threshold_mode": "multiplier",
                "threshold_value": 1.5, "min_body_ticks": 2, "min_range_ticks": 4,
                "sweep_dominant": {"lookback_bars": 20, "basis": "range", "threshold_value": 1.5, "n_events_matched": 512},
            },
            "failed_continuation": {"lookback_signals": 1, "require_prior_rebreak": True, "rebreak_window_bars": 10},
            "context_gates": {
                "vwap_stretch_atr_min": 0.75, "volume_multiple_min": 2.5,
                "direction_streak_min": 3, "range_expansion_multiple_min": 1.5,
            },
            "quiet_filter": {"lookback_minutes": 60, "max_prior_signals": 1},
            "atr_period": 14,
        },
        "entry_rule": {"trigger_event": "on_bar_close", "fill_type": "market_on_next_open", "max_entries_per_session": 1},
        "stop_rule": {"style": "signal_candle_extreme_with_cap", "base_offset_ticks": 4, "max_stop_ticks": 100,
                      "atr_fallback_multiple": 1.2, "recommended_stop_ticks": 31},
        "post_entry_management": {
            "evaluation_bar": 2,
            "primary_hold_rule": {"name": "midpoint_reclaim_yes", "requires_midpoint_reclaim_within_bars": 2},
            "union_hold_rules": {
                "type": "any_of",
                "rules": [
                    {"name": "midpoint_reclaim_yes", "requires_midpoint_reclaim_within_bars": 2},
                    {"name": "rebreak_no", "max_rebreak_within_bars": 0, "check_bars": 2},
                    {"name": "explosive_start", "fav_2bar_pct_min": 45.0, "adv_2bar_pct_max": 20.0},
                ],
            },
            "on_fail_action": "exit_at_market", "on_pass_action": "trail_for_runner",
        },
        "exit_rule": {
            "scalp_target_pct_of_signal_candle": 30, "expansion_target_pct_of_signal_candle": 62,
            "runner_target_pct_of_signal_candle": 125, "time_stop_minutes": 30,
            "trail_style": "atr", "atr_trail_multiple": 1.5,
        },
        "session_filter": {"mode": "allowlist", "allowed_sessions": ["london_ny_overlap"]},
        "risk_and_friction": {"base_unit": 1, "max_add_ons": 1, "max_total_units": 2,
                              "commission_per_trade_usd": 4.18, "slippage_ticks_per_side": 1},
    }
    bp.update(overrides)
    return bp


@pytest.fixture
def seed_path(tmp_path: Path) -> Path:
    p = tmp_path / "Test.xml"
    p.write_text(_build_seed_xml(), encoding="utf-8")
    return p


@pytest.fixture
def seed_text(seed_path: Path) -> str:
    return seed_path.read_text(encoding="utf-8-sig")


# ---------------------------------------------------------------------------
# blueprint_to_fields
# ---------------------------------------------------------------------------

def test_blueprint_to_fields_maps_all_required_inputs() -> None:
    fields = blueprint_to_fields(_blueprint())
    expected = {
        "BlueprintId",
        "OnsetCondition", "DirectionPolicy",
        "CandleLookbackBars", "CandleBasis", "ThresholdMultiplier",
        "MinBodyTicks", "MinRangeTicks", "MaxBodyTicks", "MaxRangeTicks",
        "FailedContinuationLookbackSignals", "FailedSignalRebreakWindowBars",
        "AtrPeriod",
        "VwapStretchAtrMin", "VolumeMultipleMin", "DirectionStreakMin",
        "RangeExpansionMultipleMin", "RequireContextGates",
        "QuietLookbackMinutes", "QuietMaxPriorSignals",
        "SessionMode", "TriggerEvent", "FillType", "MaxEntriesPerSession", "Contracts",
        "StopStyle", "StopBaseOffsetTicks", "MaxStopTicks", "AtrFallbackMultiple", "RecommendedStopTicks",
        "EvaluationBar", "HoldRuleMode", "PrimaryHoldRule",
        "MidpointReclaimBars", "RebreakCheckBars",
        "ExplosiveFav2BarPctMin", "ExplosiveAdv2BarPctMax",
        "OrderlyFav2BarPctMin", "OrderlyAdv2BarPctMax",
        "Fav2BarOnlyPctMin", "Adv2BarOnlyPctMax",
        "OnFailAction", "OnPassAction",
        "ScalpTargetPct", "ExpansionTargetPct", "RunnerTargetPct", "TimeStopMinutes",
        "TrailStyle", "AtrTrailMultiple",
        "BaseUnit", "MaxAddOns", "MaxTotalUnits",
        "EnableDebugPrint", "DrawMarkers",
    }
    assert expected.issubset(set(fields.keys()))
    # Ten AllowSession* booleans — the C# contract is all-present or load fails.
    assert set(SESSION_LABELS_CANONICAL.values()).issubset(set(fields.keys()))


def test_blueprint_to_fields_does_not_emit_allowed_sessions_csv() -> None:
    fields = blueprint_to_fields(_blueprint())
    assert "AllowedSessionsCsv" not in fields


def test_enums_and_booleans_formatted_for_nt() -> None:
    fields = blueprint_to_fields(_blueprint())
    assert fields["OnsetCondition"] == "FirstLargeAfterFailedContinuation"
    assert fields["DirectionPolicy"] == "CounterToFailedContinuation"
    assert fields["CandleBasis"] == "Range"
    assert fields["HoldRuleMode"] == "AnyOf"
    assert fields["PrimaryHoldRule"] == "MidpointReclaimYes"
    assert fields["RequireContextGates"] == "false"
    assert fields["DrawMarkers"] == "true"


def test_session_filter_toggles_only_listed_sessions() -> None:
    bp = _blueprint()
    bp["session_filter"]["allowed_sessions"] = ["power_hour", "asia"]
    fields = blueprint_to_fields(bp)
    assert fields["AllowSessionPowerHour"] == "true"
    assert fields["AllowSessionAsia"] == "true"
    assert fields["AllowSessionNyOpen"] == "false"
    assert fields["AllowSessionLondonNyOverlap"] == "false"


def test_legacy_session_label_is_translated() -> None:
    # session_classifier.py emits "ny_pre"; C# expects "ny_pre_open".
    assert canonical_session_label("ny_pre") == "ny_pre_open"
    assert canonical_session_label("mid_ny") == "mid_day"
    assert canonical_session_label("london") == "asia"


def test_unknown_session_label_raises() -> None:
    with pytest.raises(ValueError, match=r"unknown session label"):
        canonical_session_label("premarket")


def test_failed_continuation_lookback_defaults_to_one() -> None:
    bp = _blueprint()
    bp["onset_detection"]["failed_continuation"] = {}  # no lookback_signals
    fields = blueprint_to_fields(bp)
    assert fields["FailedContinuationLookbackSignals"] == "1"


def test_candle_bucket_populates_range_ticks_for_range_basis() -> None:
    bp = _blueprint()
    bp["onset_detection"]["candle_size"]["candle_bucket"] = "50-75"
    bp["onset_detection"]["candle_size"]["basis"] = "range"
    fields = blueprint_to_fields(bp)
    assert fields["MinRangeTicks"] == "50"
    assert fields["MaxRangeTicks"] == "75"
    assert fields["MaxBodyTicks"] == "0"  # body is not pinned in range-basis


def test_candle_bucket_populates_body_ticks_for_body_basis() -> None:
    bp = _blueprint()
    bp["onset_detection"]["candle_size"]["candle_bucket"] = "50-75"
    bp["onset_detection"]["candle_size"]["basis"] = "body"
    fields = blueprint_to_fields(bp)
    assert fields["MinBodyTicks"] == "50"
    assert fields["MaxBodyTicks"] == "75"
    assert fields["MaxRangeTicks"] == "0"


def test_candle_bucket_open_ended() -> None:
    assert parse_candle_bucket("75+") == (75, None)
    assert parse_candle_bucket("50-75") == (50, 75)
    assert parse_candle_bucket(None) == (0, None)


def test_hold_rule_orderly_start_wires_thresholds() -> None:
    bp = _blueprint()
    bp["post_entry_management"]["primary_hold_rule"] = {"name": "orderly_start"}
    fields = blueprint_to_fields(bp)
    assert fields["PrimaryHoldRule"] == "OrderlyStart"
    assert float(fields["OrderlyFav2BarPctMin"]) == 25.0
    assert float(fields["OrderlyAdv2BarPctMax"]) == 35.0


def test_hold_rule_fav2bar_wires_threshold() -> None:
    bp = _blueprint()
    bp["post_entry_management"]["primary_hold_rule"] = {"name": "fav2bar_ge_35pct"}
    fields = blueprint_to_fields(bp)
    assert fields["PrimaryHoldRule"] == "Fav2BarOnly"
    assert float(fields["Fav2BarOnlyPctMin"]) == 35.0


def test_hold_rule_adv2bar_wires_threshold() -> None:
    bp = _blueprint()
    bp["post_entry_management"]["primary_hold_rule"] = {"name": "adv2bar_lt_20pct"}
    fields = blueprint_to_fields(bp)
    assert fields["PrimaryHoldRule"] == "Adv2BarOnly"
    assert float(fields["Adv2BarOnlyPctMax"]) == 20.0


# ---------------------------------------------------------------------------
# Seed-based patching
# ---------------------------------------------------------------------------

def _parse_for_assertions(text: str) -> ET.Element:
    import re as _re
    stripped = _re.sub(r'\sxmlns(:\w+)?="[^"]+"', "", text)
    stripped = _re.sub(r'\s\w+:type="[^"]+"', "", stripped)
    return ET.fromstring(stripped)


def test_patch_seed_preserves_assembly_hash_and_metadata(seed_text: str) -> None:
    bp = _blueprint()
    bp["onset_detection"]["candle_size"]["lookback_bars"] = 40
    fields = blueprint_to_fields(bp)
    new_text = patch_seed_text(seed_text, fields)

    assert f"LcrOnsetCondition, {_ASSEMBLY_HASH}" in new_text
    assert "<InstrumentOrInstrumentList>NQ 06-26</InstrumentOrInstrumentList>" in new_text
    assert "<From>2026-04-18T00:00:00</From>" in new_text
    assert 'xmlns:xsd="http://www.w3.org/2001/XMLSchema"' in new_text


def test_patch_updates_optimizer_parameters_max_min_value(seed_text: str) -> None:
    bp = _blueprint()
    bp["onset_detection"]["candle_size"]["lookback_bars"] = 40
    bp["exit_rule"]["runner_target_pct_of_signal_candle"] = 150
    fields = blueprint_to_fields(bp)
    new_text = patch_seed_text(seed_text, fields)
    root = _parse_for_assertions(new_text)

    lookback = root.find(".//Parameter[Name='CandleLookbackBars']")
    assert lookback is not None
    assert lookback.find("Max").text == "40"
    assert lookback.find("Min").text == "40"
    assert lookback.find("ValueSerializable").text == "40"

    runner = root.find(".//Parameter[Name='RunnerTargetPct']")
    assert runner is not None
    assert runner.find("ValueSerializable").text == "150"


def test_patch_writes_session_boolean_elements(seed_text: str) -> None:
    bp = _blueprint()
    bp["session_filter"]["allowed_sessions"] = ["asia", "power_hour"]
    fields = blueprint_to_fields(bp)
    new_text = patch_seed_text(seed_text, fields)

    # No stale AllowedSessionsCsv.
    assert "<AllowedSessionsCsv" not in new_text
    # Strategy body carries all ten.
    body = _parse_for_assertions(new_text).find("./Strategy/LargeCandleReversal")
    for prop in SESSION_LABELS_CANONICAL.values():
        el = body.find(prop)
        assert el is not None, f"missing Strategy-section element {prop}"
    assert body.find("AllowSessionAsia").text == "true"
    assert body.find("AllowSessionPowerHour").text == "true"
    assert body.find("AllowSessionNyOpen").text == "false"


def test_patch_inserts_session_boolean_parameters_when_missing(seed_text: str) -> None:
    bp = _blueprint()
    bp["session_filter"]["allowed_sessions"] = ["ny_open"]
    fields = blueprint_to_fields(bp)
    new_text = patch_seed_text(seed_text, fields)
    root = _parse_for_assertions(new_text)
    for prop in SESSION_LABELS_CANONICAL.values():
        p = root.find(f".//Parameter[Name='{prop}']")
        assert p is not None, f"missing <Parameter> block for {prop}"
    # ny_open is true; power_hour is false.
    p_ny = root.find(".//Parameter[Name='AllowSessionNyOpen']/ValueSerializable")
    assert p_ny.text == "True"
    p_ph = root.find(".//Parameter[Name='AllowSessionPowerHour']/ValueSerializable")
    assert p_ph.text == "False"


def test_enum_params_keep_min_max_as_seed_integer_indices(seed_text: str) -> None:
    bp = _blueprint()
    bp["direction_policy"] = "counter_to_directional_run"
    fields = blueprint_to_fields(bp)
    new_text = patch_seed_text(seed_text, fields)
    root = _parse_for_assertions(new_text)

    dp = root.find(".//Parameter[Name='DirectionPolicy']")
    assert dp.find("Max").text == "0"
    assert dp.find("Min").text == "0"
    assert dp.find("ValueSerializable").text == "CounterToDirectionalRun"

    lookback = root.find(".//Parameter[Name='CandleLookbackBars']")
    assert lookback.find("Max").text == lookback.find("ValueSerializable").text


def test_patch_boolean_serialisable_is_pascal_case(seed_text: str) -> None:
    fields = blueprint_to_fields(_blueprint())
    new_text = patch_seed_text(seed_text, fields)
    root = _parse_for_assertions(new_text)

    dm_opt = root.find(".//Parameter[Name='DrawMarkers']/ValueSerializable")
    assert dm_opt is not None and dm_opt.text == "True"
    req_opt = root.find(".//Parameter[Name='RequireContextGates']/ValueSerializable")
    assert req_opt is not None and req_opt.text == "False"

    body = root.find("./Strategy/LargeCandleReversal")
    assert body.find("DrawMarkers").text == "true"
    assert body.find("RequireContextGates").text == "false"


def test_optimizer_skips_string_fields(seed_text: str) -> None:
    assert "BlueprintId" not in OPTIMIZER_PARAMETER_NAMES
    assert "AllowedSessionsCsv" not in OPTIMIZER_PARAMETER_NAMES
    fields = blueprint_to_fields(_blueprint())
    new_text = patch_seed_text(seed_text, fields)
    body = _parse_for_assertions(new_text).find("./Strategy/LargeCandleReversal")
    assert body.find("BlueprintId").text


# ---------------------------------------------------------------------------
# End-to-end: write_templates_from_file
# ---------------------------------------------------------------------------

def test_write_templates_from_file_emits_one_per_blueprint(tmp_path: Path) -> None:
    seed_path = tmp_path / "Test.xml"
    seed_path.write_text(_build_seed_xml(), encoding="utf-8")

    bp1 = _blueprint()
    bp2 = _blueprint()
    bp2["blueprint_id"] = "flc_x_rebreak_no_1m"
    bp2["post_entry_management"]["primary_hold_rule"] = {
        "name": "rebreak_no", "max_rebreak_within_bars": 0, "check_bars": 2,
    }

    input_path = tmp_path / "blueprints.json"
    input_path.write_text(json.dumps({"blueprints": [bp1, bp2]}), encoding="utf-8")

    out_dir = tmp_path / "out"
    written = write_templates_from_file(input_path, out_dir, seed_path)

    assert len(written) == 2
    names = {p.name for p in written}
    assert "flc_x_midpoint_reclaim_yes_1m.xml" in names
    assert "flc_x_rebreak_no_1m.xml" in names
    for p in written:
        ET.parse(p)


def test_write_skips_unimplemented_onset_and_records_csv(tmp_path: Path) -> None:
    seed_path = tmp_path / "Test.xml"
    seed_path.write_text(_build_seed_xml(), encoding="utf-8")

    ok_bp = _blueprint()
    bad_bp = _blueprint()
    bad_bp["blueprint_id"] = "krk_x_midpoint_1m"
    bad_bp["provenance"]["onset_condition"] = "first_large_after_directional_run"

    input_path = tmp_path / "blueprints.json"
    input_path.write_text(json.dumps({"blueprints": [ok_bp, bad_bp]}), encoding="utf-8")

    out_dir = tmp_path / "out"
    written = write_templates_from_file(input_path, out_dir, seed_path)
    assert len(written) == 1
    assert "krk_x_midpoint_1m" not in {p.stem for p in written}

    skipped_csv = out_dir / "skipped_blueprints.csv"
    assert skipped_csv.is_file()
    text = skipped_csv.read_text(encoding="utf-8")
    assert "krk_x_midpoint_1m" in text
    assert "first_large_after_directional_run" in text


def test_write_strict_raises_on_unimplemented_onset(tmp_path: Path) -> None:
    seed_path = tmp_path / "Test.xml"
    seed_path.write_text(_build_seed_xml(), encoding="utf-8")

    bad_bp = _blueprint()
    bad_bp["provenance"]["onset_condition"] = "first_large_after_compression"
    input_path = tmp_path / "blueprints.json"
    input_path.write_text(json.dumps({"blueprints": [bad_bp]}), encoding="utf-8")

    with pytest.raises(ValueError, match=r"not implemented"):
        write_templates_from_file(input_path, tmp_path / "out", seed_path, strict=True)


def test_write_refuses_to_overwrite_seed_file(tmp_path: Path) -> None:
    seed_dir = tmp_path / "out"
    seed_dir.mkdir()
    seed_path = seed_dir / "Test.xml"
    seed_path.write_text(_build_seed_xml(), encoding="utf-8")
    bp = _blueprint()
    bp["blueprint_id"] = "Test"
    input_path = tmp_path / "blueprints.json"
    input_path.write_text(json.dumps({"blueprints": [bp]}), encoding="utf-8")

    seed_bytes_before = seed_path.read_bytes()
    written = write_templates_from_file(input_path, seed_dir, seed_path)

    assert len(written) == 1
    assert written[0].name == "Test_generated.xml"
    assert seed_path.read_bytes() == seed_bytes_before


def test_write_fails_clearly_without_seed(tmp_path: Path) -> None:
    input_path = tmp_path / "blueprints.json"
    input_path.write_text(json.dumps({"blueprints": [_blueprint()]}), encoding="utf-8")
    out_dir = tmp_path / "out"
    missing_seed = tmp_path / "does_not_exist.xml"
    with pytest.raises(FileNotFoundError) as exc:
        write_templates_from_file(input_path, out_dir, missing_seed)
    assert "seed template not found" in str(exc.value)


def test_write_empty_blueprints_returns_empty_list(tmp_path: Path) -> None:
    seed_path = tmp_path / "Test.xml"
    seed_path.write_text(_build_seed_xml(), encoding="utf-8")
    input_path = tmp_path / "blueprints.json"
    input_path.write_text(json.dumps({"blueprints": []}), encoding="utf-8")
    written = write_templates_from_file(input_path, tmp_path / "out", seed_path)
    assert written == []


def test_implemented_onsets_contains_only_the_one_supported() -> None:
    assert IMPLEMENTED_ONSETS == frozenset({"first_large_after_failed_continuation"})


def test_hold_rule_map_covers_all_c_sharp_enum_values() -> None:
    # Every HOLD_RULE_MAP entry must resolve to a real LcrPrimaryHoldRule enum.
    allowed_enum = {
        "MidpointReclaimYes", "RebreakNo", "ExplosiveStart",
        "OrderlyStart", "Fav2BarOnly", "Adv2BarOnly",
    }
    for label, spec in HOLD_RULE_MAP.items():
        assert spec["enum"] in allowed_enum, f"{label} → {spec['enum']} not a valid C# enum value"
