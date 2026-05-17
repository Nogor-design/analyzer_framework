"""Tests for the seeded family registry and parameter validation.

Confirms:
- The 13 starter families from migration 0002 are present after init_db.
- Each family has a non-empty mechanism_template and a parsed param spec.
- validate_params catches every violation type listed in family_registry.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ta_foundation.research_ledger import (
    Repository,
    get_family_spec,
    get_repository,
    list_probe_families,
    validate_params,
)


EXPECTED_FAMILIES = {
    "vwap_reject_fade",
    "vwap_reclaim_continuation",
    "prior_high_low_failed_breakout",
    "overnight_high_low_sweep_reclaim",
    "orb_breakout",
    "orb_failure_reclaim",
    "prior_close_settlement_reaction",
    "initial_balance_extension",
    "initial_balance_reversal",
    "large_candle_origin_retest",
    "compression_then_expansion",
    "trend_pullback_continuation",
    "exhaustion_into_reference",
}


@pytest.fixture()
def repo(tmp_path: Path) -> Repository:
    return get_repository(tmp_path / "ledger.db")


def test_all_starter_families_seeded(repo: Repository) -> None:
    seeded = {f.family_id for f in list_probe_families(repo)}
    # All 13 starter families must be present.
    assert EXPECTED_FAMILIES.issubset(seeded)
    # Migration 0003 also seeds 'legacy_imported' for the A.4 backfill catch-all.
    assert "legacy_imported" in seeded


def test_each_starter_family_has_mechanism_template_and_params(repo: Repository) -> None:
    # legacy_imported is deliberately empty — it's the backfill catch-all and
    # should not be proposed by the Hypothesis Author.
    for fam in list_probe_families(repo):
        if fam.family_id == "legacy_imported":
            assert fam.mechanism_template is None
            assert fam.params == ()
            continue
        assert fam.mechanism_template, f"{fam.family_id} missing mechanism_template"
        assert len(fam.mechanism_template) >= 50, (
            f"{fam.family_id} mechanism_template too short for use as a prompt aid"
        )
        assert fam.params, f"{fam.family_id} has no params"


def test_get_family_spec_returns_none_for_missing(repo: Repository) -> None:
    assert get_family_spec(repo, "not_a_family") is None


def test_get_family_spec_round_trip(repo: Repository) -> None:
    spec = get_family_spec(repo, "vwap_reject_fade")
    assert spec is not None
    assert spec.family_id == "vwap_reject_fade"
    by_name = {p.name: p for p in spec.params}
    assert by_name["min_distance_ticks"].type == "int"
    assert by_name["min_distance_ticks"].min == 1
    assert by_name["target_ticks"].max == 800


def test_validate_params_accepts_valid_params(repo: Repository) -> None:
    violations = validate_params(
        repo,
        "vwap_reject_fade",
        {
            "min_distance_ticks": 4,
            "max_distance_ticks": 12,
            "stop_ticks": 8,
            "target_ticks": 24,
        },
    )
    assert violations == []


def test_validate_params_rejects_unknown_param(repo: Repository) -> None:
    violations = validate_params(
        repo,
        "vwap_reject_fade",
        {"stop_ticks": 8, "weird_param": 99},
    )
    codes = {v.code for v in violations}
    assert "unknown_param" in codes
    weird = next(v for v in violations if v.param_name == "weird_param")
    assert weird.code == "unknown_param"
    assert weird.offending_value == 99


def test_validate_params_rejects_below_min(repo: Repository) -> None:
    violations = validate_params(repo, "vwap_reject_fade", {"stop_ticks": 0})
    assert len(violations) == 1
    assert violations[0].code == "below_min"


def test_validate_params_rejects_above_max(repo: Repository) -> None:
    violations = validate_params(repo, "vwap_reject_fade", {"target_ticks": 99999})
    assert len(violations) == 1
    assert violations[0].code == "above_max"


def test_validate_params_rejects_type_mismatch(repo: Repository) -> None:
    violations = validate_params(repo, "vwap_reject_fade", {"stop_ticks": "eight"})
    assert len(violations) == 1
    assert violations[0].code == "type_mismatch"


def test_validate_params_rejects_bool_as_int(repo: Repository) -> None:
    # Common Python footgun: bool is an int subclass. We treat it as a mismatch.
    violations = validate_params(repo, "vwap_reject_fade", {"stop_ticks": True})
    assert violations and violations[0].code == "type_mismatch"


def test_validate_params_rejects_enum_violation(repo: Repository) -> None:
    violations = validate_params(
        repo,
        "orb_breakout",
        {"orb_minutes": 15, "signal_type": "magic", "stop_ticks": 8, "target_ticks": 50},
    )
    assert any(v.code == "not_in_enum" for v in violations)


def test_validate_params_accepts_grid_lists(repo: Repository) -> None:
    violations = validate_params(
        repo,
        "orb_breakout",
        {
            "orb_minutes": [5, 15, 30],
            "signal_type": ["break_close", "break_extreme"],
            "stop_ticks": [8, 12],
            "target_ticks": [50, 100, 150],
        },
    )
    assert violations == []


def test_validate_params_rejects_grid_with_one_bad_element(repo: Repository) -> None:
    violations = validate_params(
        repo,
        "orb_breakout",
        {
            "orb_minutes": [5, 15, 999],  # 999 above max=60
            "signal_type": "break_close",
            "stop_ticks": 8,
            "target_ticks": 50,
        },
    )
    assert any(v.code == "above_max" and v.param_name == "orb_minutes" for v in violations)


def test_validate_params_rejects_empty_grid(repo: Repository) -> None:
    violations = validate_params(repo, "orb_breakout", {"orb_minutes": []})
    assert any(v.code == "empty_grid" for v in violations)


def test_validate_params_unknown_family(repo: Repository) -> None:
    violations = validate_params(repo, "not_a_family", {"x": 1})
    assert len(violations) == 1
    assert violations[0].code == "unknown_family"


def test_validate_float_param_within_range(repo: Repository) -> None:
    violations = validate_params(
        repo,
        "large_candle_origin_retest",
        {"retrace_pct": 0.5, "candle_size_ticks_min": 10, "stop_ticks": 8, "target_ticks": 50},
    )
    assert violations == []


def test_validate_float_param_above_range(repo: Repository) -> None:
    violations = validate_params(
        repo,
        "large_candle_origin_retest",
        {"retrace_pct": 1.5},
    )
    assert any(v.code == "above_max" for v in violations)
