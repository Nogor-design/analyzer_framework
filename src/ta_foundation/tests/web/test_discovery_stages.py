from __future__ import annotations

import json

import pytest

from ta_foundation.web.discovery_glossary import reload_glossary
from ta_foundation.web.discovery_stages import (
    StageDefinition,
    get_family,
    get_stage,
    get_stage_definition,
    list_event_studies,
    list_families,
    list_funnel_stages,
    list_stages,
    reload_stages,
)


@pytest.fixture(autouse=True)
def _ensure_real_stages_loaded():
    reload_stages()


# ---------------------------------------------------------------------------
# Funnel structure
# ---------------------------------------------------------------------------

def test_funnel_stages_are_six_ordinal_one_through_six():
    stages = list_funnel_stages()
    assert len(stages) == 6
    ordinals = [s["ordinal"] for s in stages]
    assert ordinals == [1, 2, 3, 4, 5, 6]


def test_event_studies_include_large_candle_excursion():
    events = list_event_studies()
    assert any(s["id"] == "large_candle_excursion" for s in events)
    for s in events:
        assert s["kind"] == "event_study"
        assert s["ordinal"] == 0


def test_list_stages_returns_funnel_first_then_events():
    stages = list_stages()
    funnel_count = sum(1 for s in stages if s["kind"] == "funnel")
    event_count = sum(1 for s in stages if s["kind"] == "event_study")
    assert funnel_count == 6
    assert event_count >= 1
    # Funnel stages all come before event studies in this list
    seen_event = False
    for s in stages:
        if s["kind"] == "event_study":
            seen_event = True
        elif seen_event:
            pytest.fail("Funnel stage appeared after an event study")


def test_each_stage_loads_default_yaml_from_disk():
    for stage_id in (
        "01_quick_scan", "02_candle_patterns", "03_levels_regions",
        "04_ny_open", "05_orb_momentum", "06_validate",
        "large_candle_excursion",
    ):
        sdef = get_stage_definition(stage_id)
        assert sdef is not None, stage_id
        assert isinstance(sdef.default_yaml, dict)
        assert sdef.default_yaml, f"{stage_id} loaded an empty YAML"
        assert "report" in sdef.default_yaml
        assert "sections" in sdef.default_yaml


def test_each_enabled_family_has_yaml_block_in_default_yaml():
    for stage in list_stages():
        sdef = get_stage_definition(stage["id"])
        for family_id in sdef.enabled_families:
            family = get_family(family_id)
            assert family is not None, f"{stage['id']} references unknown family {family_id}"
            assert family.yaml_block in sdef.default_yaml, (
                f"{stage['id']} enables family '{family_id}' but its yaml_block "
                f"'{family.yaml_block}' is missing from {sdef.source_yaml_filename}"
            )


def test_only_stage_six_depends_on_promotions():
    for stage in list_stages():
        if stage["id"] == "06_validate":
            assert stage["depends_on_promotions"] is True
        else:
            assert stage["depends_on_promotions"] is False


def test_stage_six_accepts_every_promotable_family():
    stage = get_stage_definition("06_validate")
    assert stage is not None
    assert set(stage.enabled_families) == {
        "candle", "ma", "orb", "bb", "lcr", "breakout", "pullback", "level",
    }


def test_bb_stage_signals_match_engine_registry():
    from ta_foundation.analysis.entry_strategies.bb.signals import BB_SIGNAL_REGISTRY

    family = get_family("bb")
    assert family is not None
    assert {sig["id"] for sig in family.sub_signals} == set(BB_SIGNAL_REGISTRY)


# ---------------------------------------------------------------------------
# Stage metadata quality (beginner-facing copy)
# ---------------------------------------------------------------------------

def test_every_stage_has_required_metadata():
    for stage in list_stages():
        for field in (
            "id", "label", "short_label", "one_liner",
            "long_blurb", "what_to_look_at", "runtime_estimate",
        ):
            assert isinstance(stage[field], str) and stage[field].strip(), (
                f"{stage['id']} missing or empty {field}"
            )
        assert stage["runtime_seconds_estimate"] > 0
        assert isinstance(stage["sticky_help"], list) and stage["sticky_help"]


def test_stage_sections_are_extracted_for_ui():
    """Each funnel stage's default_yaml has sections; the UI summary reflects them."""
    for stage in list_funnel_stages():
        assert isinstance(stage["sections"], list)
        assert stage["sections"], f"{stage['id']} has no sections"
        for section in stage["sections"]:
            assert isinstance(section, dict)
            assert "id" in section


def test_next_stage_recommendations_resolve():
    known_ids = {s["id"] for s in list_stages()}
    for stage in list_stages():
        for ref in stage["next_stage_recommendations"]:
            assert ref in known_ids, f"{stage['id']} -> unknown next stage {ref}"


# ---------------------------------------------------------------------------
# Family registry
# ---------------------------------------------------------------------------

def test_families_include_all_eight_signal_families_plus_lce():
    ids = {f["id"] for f in list_families()}
    expected = {
        "candle", "ma", "orb", "bb", "lcr",
        "breakout", "pullback", "level",
        "large_candle_excursion",
    }
    assert expected.issubset(ids)


def test_each_family_has_yaml_block_and_glossary_term():
    glossary = reload_glossary().terms
    for family in list_families():
        assert family["yaml_block"]
        assert family["glossary_term"] in glossary, (
            f"family {family['id']} references unknown glossary term {family['glossary_term']}"
        )


def test_candle_family_lists_all_nine_patterns():
    candle = get_family("candle")
    assert candle is not None
    pattern_ids = {p["id"] for p in candle.sub_signals}
    expected = {
        "large_body", "clean_breakout_bar",
        "pin_bar_bullish", "pin_bar_bearish",
        "engulfing_bullish", "engulfing_bearish",
        "inside_bar", "outside_bar", "doji",
    }
    assert expected.issubset(pattern_ids)


def test_lcr_family_lists_four_signal_types():
    lcr = get_family("lcr")
    assert lcr is not None
    types = {p["id"] for p in lcr.sub_signals}
    assert types == {"fresh", "touch", "break", "retrace"}


def test_each_sub_signal_glossary_term_resolves():
    glossary = reload_glossary().terms
    for family in list_families():
        for sub in family["sub_signals"]:
            assert sub["glossary_term"] in glossary, (
                f"family {family['id']}/{sub['id']} references unknown glossary term {sub['glossary_term']}"
            )


def test_get_stage_with_include_default_yaml():
    out = get_stage("01_quick_scan", include_default_yaml=True)
    assert out is not None
    assert "default_yaml" in out
    assert "candle_discovery" in out["default_yaml"]


def test_get_stage_without_default_yaml_omits_it():
    out = get_stage("01_quick_scan", include_default_yaml=False)
    assert out is not None
    assert "default_yaml" not in out


def test_get_stage_unknown_returns_none():
    assert get_stage("does_not_exist") is None
    assert get_stage_definition("does_not_exist") is None


# ---------------------------------------------------------------------------
# JSON serialization
# ---------------------------------------------------------------------------

def test_list_stages_payload_is_json_safe():
    json.dumps({"funnel": list_funnel_stages(), "events": list_event_studies(), "families": list_families()})


def test_get_stage_with_default_yaml_is_json_safe():
    out = get_stage("02_candle_patterns", include_default_yaml=True)
    assert out is not None
    json.dumps(out)
