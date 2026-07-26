"""
Tests for the PantheonBotV2 parameter registry.

Coverage:
  - Internal consistency (no dup properties, declared roles valid).
  - Enum round-trip (NT → analysis → NT is identity, both directions).
  - Coverage check: every `[NinjaScriptProperty]` declared in the C# file
    has a registry entry, and the registry doesn't reference phantom
    properties.
  - Settings-CSV round-trip: every key in the captured fixture settings
    CSV is either in the registry or explicitly allow-listed.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from ta_foundation.analysis.strategies.pantheon_bot_v2.param_map import (
    PANTHEON_BOT_V2_PARAMS,
    ROLES,
    analysis_value_to_nt_enum,
    get_param,
    nt_enum_to_analysis_value,
    params_by_role,
    registered_properties,
)
from ta_foundation.analysis.strategies.pantheon_bot_v2.settings_loader import (
    load_settings,
)


CS_FILE = (
    Path(__file__).resolve().parents[4]
    / "strategies"
    / "PantheonBotV2"
    / "PantheonBotV2.cs"
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Internal consistency
# ---------------------------------------------------------------------------

def test_no_duplicate_property_names():
    names = [p.nt_property for p in PANTHEON_BOT_V2_PARAMS]
    dups = {n for n in names if names.count(n) > 1}
    assert not dups, f"duplicate property entries: {sorted(dups)}"


def test_every_param_has_known_role():
    for p in PANTHEON_BOT_V2_PARAMS:
        assert p.role in ROLES, f"{p.nt_property} has unknown role {p.role}"


def test_enum_params_have_unique_nt_values():
    for p in PANTHEON_BOT_V2_PARAMS:
        if p.nt_type != "enum":
            continue
        nt_vals = [em.nt_enum_value for em in p.enum_values]
        dups = {v for v in nt_vals if nt_vals.count(v) > 1}
        assert not dups, f"{p.nt_property} has duplicate NT enum values: {dups}"


def test_enum_params_have_unique_analysis_values():
    for p in PANTHEON_BOT_V2_PARAMS:
        if p.nt_type != "enum":
            continue
        an_vals = [em.analysis_value for em in p.enum_values]
        dups = {v for v in an_vals if an_vals.count(v) > 1}
        assert not dups, f"{p.nt_property} has duplicate analysis values: {dups}"


# ---------------------------------------------------------------------------
# Enum round-trip
# ---------------------------------------------------------------------------

def test_enum_round_trip_nt_to_analysis_to_nt():
    for p in PANTHEON_BOT_V2_PARAMS:
        if p.nt_type != "enum":
            continue
        for em in p.enum_values:
            assert (
                analysis_value_to_nt_enum(p.nt_property, em.analysis_value)
                == em.nt_enum_value
            ), f"round-trip broken on {p.nt_property} {em}"


def test_enum_round_trip_analysis_to_nt_to_analysis():
    for p in PANTHEON_BOT_V2_PARAMS:
        if p.nt_type != "enum":
            continue
        for em in p.enum_values:
            assert (
                nt_enum_to_analysis_value(p.nt_property, em.nt_enum_value)
                == em.analysis_value
            )


def test_unknown_enum_raises():
    with pytest.raises(KeyError):
        analysis_value_to_nt_enum("RequiredTrendRegimeFilter", "sideways")
    with pytest.raises(KeyError):
        nt_enum_to_analysis_value("RequiredTrendRegimeFilter", "Diagonal")


def test_non_enum_property_rejects_enum_lookup():
    with pytest.raises(ValueError):
        nt_enum_to_analysis_value("MaxStop", "200")


# ---------------------------------------------------------------------------
# Coverage check against the .cs file
# ---------------------------------------------------------------------------

_NS_PROP_RE = re.compile(
    r"\[NinjaScriptProperty[^\]]*\][\s\S]{0,400}?"
    r"public\s+[\w\.<>\[\]]+\s+(?P<name>[A-Za-z_][\w]*)\s*\{",
)


def _ninjascript_properties_in_cs() -> set[str]:
    text = CS_FILE.read_text(encoding="utf-8-sig", errors="replace")
    return {m.group("name") for m in _NS_PROP_RE.finditer(text)}


@pytest.mark.skipif(not CS_FILE.exists(), reason="PantheonBotV2.cs not in repo")
def test_every_cs_ninjascript_property_is_registered():
    declared = _ninjascript_properties_in_cs()
    registered = set(registered_properties())
    missing = declared - registered
    assert not missing, (
        f"PantheonBotV2.cs declares NinjaScriptProperties that are not in "
        f"the param_map registry: {sorted(missing)}. Add them so analysis "
        f"insights translate cleanly to strategy templates."
    )


@pytest.mark.skipif(not CS_FILE.exists(), reason="PantheonBotV2.cs not in repo")
def test_no_phantom_properties_in_registry():
    declared = _ninjascript_properties_in_cs()
    registered = set(registered_properties())
    phantom = registered - declared
    assert not phantom, (
        f"param_map registers properties not declared in PantheonBotV2.cs: "
        f"{sorted(phantom)}. Either remove them or the .cs needs them added."
    )


# ---------------------------------------------------------------------------
# Settings CSV round-trip
# ---------------------------------------------------------------------------

def _settings_csv_paths() -> list[Path]:
    return sorted(FIXTURES_DIR.glob("debug_*.csv")) if FIXTURES_DIR.exists() else []


@pytest.mark.skipif(not _settings_csv_paths(), reason="No fixture settings CSVs")
@pytest.mark.parametrize("settings_path", _settings_csv_paths(),
                         ids=lambda p: p.name)
def test_settings_csv_keys_are_all_registered(settings_path: Path):
    settings = load_settings(settings_path)
    registered = set(registered_properties())
    unknown = set(settings.keys()) - registered
    assert not unknown, (
        f"{settings_path.name} contains property keys not in the registry: "
        f"{sorted(unknown)}. Add them to param_map or fix the settings_loader "
        f"display→property mapping."
    )


# ---------------------------------------------------------------------------
# Role helpers
# ---------------------------------------------------------------------------

def test_params_by_role_returns_only_that_role():
    for role in ROLES:
        for p in params_by_role(role):
            assert p.role == role


def test_filter_role_has_the_four_regime_filters():
    filter_props = {p.nt_property for p in params_by_role("filter")}
    expected = {
        "RequiredTrendRegimeFilter",
        "RequiredVwapRegimeFilter",
        "RequiredVolatilityRegimeFilter",
        "BlockedVolatilityRegimeFilter",
    }
    assert expected <= filter_props, (
        f"filter role missing: {expected - filter_props}"
    )


def test_get_param_unknown_raises():
    with pytest.raises(KeyError):
        get_param("NotAProperty")
