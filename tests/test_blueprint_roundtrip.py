"""
Round-trip integration test: blueprint JSON → NT8 XML template → structural
assertions.

Catches silent drift between research output and the C# strategy contract.
Every assertion here corresponds to a way a template could load into
NinjaTrader and take zero trades: a misspelled session label, an
unimplemented onset, a missing hold-rule threshold, a stale
`<AllowedSessionsCsv>` element.

The fixture blueprints cover the combinations the exporter actually emits in
production: the three accepted hold rules × the failed-continuation onset,
plus a deliberate "bad blueprint" for the skip-and-record path.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List

import pytest

from ta_foundation.strategies.LargeCandleReversal.generate_nt8_template import (
    HOLD_RULE_MAP,
    IMPLEMENTED_ONSETS,
    SESSION_LABELS_CANONICAL,
    _RESEARCH_SESSION_ALIASES,
    canonical_session_label,
    parse_candle_bucket,
    write_templates_from_file,
)

from ta_foundation.tests.strategies.test_generate_nt8_template import (
    _build_seed_xml,
    _blueprint,
)


# ---------------------------------------------------------------------------
# Representative blueprint set — stands in for the real exporter output.
# ---------------------------------------------------------------------------

def _make_blueprint(
    *,
    blueprint_id: str,
    hold_rule: str,
    candle_bucket: str = "50-75",
    basis: str = "range",
    allowed_sessions: List[str],
    onset: str = "first_large_after_failed_continuation",
    direction_policy: str = "counter_to_failed_continuation",
) -> Dict[str, Any]:
    bp = _blueprint(
        blueprint_id=blueprint_id,
        direction_policy=direction_policy,
    )
    bp["provenance"]["onset_condition"] = onset
    bp["onset_detection"]["candle_size"]["basis"] = basis
    bp["onset_detection"]["candle_size"]["candle_bucket"] = candle_bucket
    bp["post_entry_management"]["primary_hold_rule"] = {"name": hold_rule}
    bp["session_filter"] = {
        "mode": "allowlist",
        "allowed_sessions": list(allowed_sessions),
    }
    return bp


@pytest.fixture
def representative_blueprints() -> List[Dict[str, Any]]:
    """Every tradeable hold rule crossed with the only implemented onset —
    the shape the exporter actually produces when the research run has no
    issues.  Plus one bad blueprint we expect to be skipped."""
    return [
        _make_blueprint(
            blueprint_id="first_large_after_failed_continuation_x_midpoint_reclaim_yes_1m",
            hold_rule="midpoint_reclaim_yes",
            allowed_sessions=["power_hour"],
        ),
        _make_blueprint(
            blueprint_id="first_large_after_failed_continuation_x_rebreak_no_1m",
            hold_rule="rebreak_no",
            candle_bucket="75+",
            allowed_sessions=["ny_open", "power_hour"],
        ),
        _make_blueprint(
            blueprint_id="first_large_after_failed_continuation_x_explosive_start_1m",
            hold_rule="explosive_start",
            allowed_sessions=["asia"],
        ),
        _make_blueprint(
            blueprint_id="first_large_after_failed_continuation_x_orderly_start_1m",
            hold_rule="orderly_start",
            allowed_sessions=["london_ny_overlap"],
        ),
        _make_blueprint(
            blueprint_id="first_large_after_failed_continuation_x_fav2bar_ge_35pct_1m",
            hold_rule="fav2bar_ge_35pct",
            candle_bucket="50-75",
            basis="body",
            allowed_sessions=["ny_pre_open"],
        ),
        _make_blueprint(
            blueprint_id="first_large_after_failed_continuation_x_adv2bar_lt_20pct_1m",
            hold_rule="adv2bar_lt_20pct",
            allowed_sessions=["mid_day"],
        ),
        # Legacy session label — exporter should canonicalise it to ny_pre_open.
        _make_blueprint(
            blueprint_id="first_large_after_failed_continuation_x_midpoint_reclaim_yes_legacy_session_1m",
            hold_rule="midpoint_reclaim_yes",
            allowed_sessions=["ny_pre"],
        ),
        # Bad blueprint — unimplemented onset.  Should be skipped.
        _make_blueprint(
            blueprint_id="first_large_after_compression_x_midpoint_reclaim_yes_1m",
            hold_rule="midpoint_reclaim_yes",
            onset="first_large_after_compression",
            allowed_sessions=["power_hour"],
        ),
    ]


@pytest.fixture
def seed_path(tmp_path: Path) -> Path:
    p = tmp_path / "Test.xml"
    p.write_text(_build_seed_xml(), encoding="utf-8")
    return p


@pytest.fixture
def generated_templates(
    tmp_path: Path,
    representative_blueprints: List[Dict[str, Any]],
    seed_path: Path,
) -> Dict[str, Path]:
    """Write the full blueprint set through the exporter and return a map
    `blueprint_id → xml path`.  The bad blueprint is not in the result map."""
    input_path = tmp_path / "blueprints.json"
    input_path.write_text(
        json.dumps({"blueprints": representative_blueprints}),
        encoding="utf-8",
    )
    out_dir = tmp_path / "templates"
    written = write_templates_from_file(input_path, out_dir, seed_path)
    return {p.stem: p for p in written}


# ---------------------------------------------------------------------------
# Helpers for asserting on NT's prefixed-attribute XML.
# ---------------------------------------------------------------------------

def _parse(path: Path) -> ET.Element:
    raw = path.read_text(encoding="utf-8-sig")
    stripped = re.sub(r'\sxmlns(:\w+)?="[^"]+"', "", raw)
    stripped = re.sub(r'\s\w+:type="[^"]+"', "", stripped)
    return ET.fromstring(stripped)


def _strategy_body(root: ET.Element) -> ET.Element:
    body = root.find("./Strategy/LargeCandleReversal")
    assert body is not None
    return body


def _optimizer_param(root: ET.Element, name: str) -> ET.Element:
    p = root.find(f".//Parameter[Name='{name}']")
    assert p is not None, f"missing <Parameter> block for {name}"
    return p


# ---------------------------------------------------------------------------
# Contract assertions — every generated template must pass all of these.
# ---------------------------------------------------------------------------

def test_unimplemented_onset_is_skipped(generated_templates: Dict[str, Path]) -> None:
    for stem in generated_templates:
        assert "after_compression" not in stem, (
            f"template {stem!r} was emitted for an unimplemented onset — "
            "the C# strategy will take zero trades"
        )


def test_skipped_report_records_the_bad_onset(tmp_path: Path, generated_templates: Dict[str, Path]) -> None:
    # generated_templates uses tmp_path/templates; locate the CSV alongside it.
    template_dir = next(iter(generated_templates.values())).parent
    csv_path = template_dir / "skipped_blueprints.csv"
    assert csv_path.is_file(), "exporter must emit skipped_blueprints.csv when it skips anything"
    text = csv_path.read_text(encoding="utf-8")
    assert "first_large_after_compression" in text


def test_onset_condition_is_in_implemented_set(generated_templates: Dict[str, Path]) -> None:
    for stem, path in generated_templates.items():
        body = _strategy_body(_parse(path))
        onset = body.find("OnsetCondition").text
        # Reverse-lookup: the enum string must correspond to an implemented
        # research label.  Only FirstLargeAfterFailedContinuation qualifies.
        assert onset == "FirstLargeAfterFailedContinuation", (
            f"{stem}: OnsetCondition={onset} is not implemented by LargeCandleReversal.cs"
        )
        # Sanity — the research label must also be in IMPLEMENTED_ONSETS.
        assert "first_large_after_failed_continuation" in IMPLEMENTED_ONSETS


def test_primary_hold_rule_enum_and_thresholds(generated_templates: Dict[str, Path]) -> None:
    allowed_enum_values = {spec["enum"] for spec in HOLD_RULE_MAP.values()}
    for stem, path in generated_templates.items():
        body = _strategy_body(_parse(path))
        rule = body.find("PrimaryHoldRule").text
        assert rule in allowed_enum_values, (
            f"{stem}: PrimaryHoldRule={rule} is not in HOLD_RULE_MAP's enum values"
        )
        # Threshold-input sanity checks — a template emitting OrderlyStart
        # with OrderlyFav2BarPctMin=0 would silently never satisfy the rule.
        if rule == "OrderlyStart":
            assert float(body.find("OrderlyFav2BarPctMin").text) > 0
            assert float(body.find("OrderlyAdv2BarPctMax").text) > 0
        if rule == "ExplosiveStart":
            assert float(body.find("ExplosiveFav2BarPctMin").text) > 0
            assert float(body.find("ExplosiveAdv2BarPctMax").text) > 0
        if rule == "Fav2BarOnly":
            assert float(body.find("Fav2BarOnlyPctMin").text) > 0
        if rule == "Adv2BarOnly":
            assert float(body.find("Adv2BarOnlyPctMax").text) > 0


def test_exactly_ten_allow_session_elements_per_template(generated_templates: Dict[str, Path]) -> None:
    for stem, path in generated_templates.items():
        body = _strategy_body(_parse(path))
        properties = set(SESSION_LABELS_CANONICAL.values())
        found = {el.tag for el in body if el.tag in properties}
        assert found == properties, (
            f"{stem}: expected {len(properties)} AllowSession* elements, got {len(found)}. "
            f"Missing: {properties - found}"
        )
        # Each must be an explicit true/false string (NinjaTrader doesn't
        # tolerate omitted booleans in a saved template).
        for prop in properties:
            v = body.find(prop).text
            assert v in ("true", "false"), f"{stem}.{prop}={v!r} is not boolean"


def test_allowlist_mode_has_at_least_one_session_true(
    representative_blueprints: List[Dict[str, Any]],
    generated_templates: Dict[str, Path],
) -> None:
    for bp in representative_blueprints:
        stem = bp["blueprint_id"]
        if stem not in generated_templates:
            continue  # bad blueprint, skipped
        body = _strategy_body(_parse(generated_templates[stem]))
        if body.find("SessionMode").text != "Allowlist":
            continue
        true_props = [
            prop for prop in SESSION_LABELS_CANONICAL.values()
            if body.find(prop).text == "true"
        ]
        assert true_props, (
            f"{stem}: SessionMode=Allowlist but no AllowSession* is true — "
            "template would take zero trades"
        )


def test_allowlist_sessions_match_research_labels(
    representative_blueprints: List[Dict[str, Any]],
    generated_templates: Dict[str, Path],
) -> None:
    for bp in representative_blueprints:
        stem = bp["blueprint_id"]
        if stem not in generated_templates:
            continue
        body = _strategy_body(_parse(generated_templates[stem]))
        if body.find("SessionMode").text != "Allowlist":
            continue
        expected_props = {
            SESSION_LABELS_CANONICAL[canonical_session_label(s)]
            for s in bp["session_filter"]["allowed_sessions"]
        }
        actual_true = {
            prop for prop in SESSION_LABELS_CANONICAL.values()
            if body.find(prop).text == "true"
        }
        assert actual_true == expected_props, (
            f"{stem}: AllowSession* true-set {sorted(actual_true)} "
            f"does not match canonicalised research labels {sorted(expected_props)}"
        )


def test_no_allowed_sessions_csv_anywhere(generated_templates: Dict[str, Path]) -> None:
    for stem, path in generated_templates.items():
        raw = path.read_text(encoding="utf-8-sig")
        assert "AllowedSessionsCsv" not in raw, (
            f"{stem}: stale <AllowedSessionsCsv> element present; NT will error on load"
        )


def test_candle_bucket_is_reflected_in_min_max_ticks(
    representative_blueprints: List[Dict[str, Any]],
    generated_templates: Dict[str, Path],
) -> None:
    for bp in representative_blueprints:
        stem = bp["blueprint_id"]
        if stem not in generated_templates:
            continue
        bucket = bp["onset_detection"]["candle_size"].get("candle_bucket")
        if not bucket:
            continue
        lo, hi = parse_candle_bucket(bucket)
        basis = bp["onset_detection"]["candle_size"]["basis"]
        body = _strategy_body(_parse(generated_templates[stem]))
        if basis == "body":
            assert int(body.find("MinBodyTicks").text) == lo
            expected_max = hi if hi is not None else 0
            assert int(body.find("MaxBodyTicks").text) == expected_max
            # Range fields untouched (0 = disabled for Max).
            assert int(body.find("MaxRangeTicks").text) == 0
        else:
            assert int(body.find("MinRangeTicks").text) == lo
            expected_max = hi if hi is not None else 0
            assert int(body.find("MaxRangeTicks").text) == expected_max
            assert int(body.find("MaxBodyTicks").text) == 0


def test_failed_continuation_lookback_is_at_least_one(generated_templates: Dict[str, Path]) -> None:
    for stem, path in generated_templates.items():
        body = _strategy_body(_parse(path))
        v = int(body.find("FailedContinuationLookbackSignals").text)
        assert v >= 1, f"{stem}: FailedContinuationLookbackSignals={v} would never satisfy HasFailedContinuation"


def test_direction_policy_matches_exporter_mapping(
    representative_blueprints: List[Dict[str, Any]],
    generated_templates: Dict[str, Path],
) -> None:
    """Task 5 is still pending human review — until then, the exporter must
    emit exactly the policy the blueprint requested.  This test will be
    tightened to reference the design-note decision once it is signed off."""
    for bp in representative_blueprints:
        stem = bp["blueprint_id"]
        if stem not in generated_templates:
            continue
        body = _strategy_body(_parse(generated_templates[stem]))
        emitted = body.find("DirectionPolicy").text
        expected_policy = bp["direction_policy"]
        from ta_foundation.strategies.LargeCandleReversal.generate_nt8_template import (
            DIRECTION_POLICY_ENUM,
        )
        assert emitted == DIRECTION_POLICY_ENUM[expected_policy], (
            f"{stem}: DirectionPolicy {emitted!r} does not match "
            f"research request {expected_policy!r}"
        )


def test_session_aliases_cover_session_classifier_labels() -> None:
    # Any label the Python session_classifier can emit must resolve.
    session_classifier_labels = {
        "asia", "london", "london_ny_overlap", "ny_pre",
        "ny_open", "mid_ny", "power_hour", "after_hours",
    }
    for label in session_classifier_labels:
        canonical_session_label(label)  # raises on any unknown


def test_no_template_would_silently_take_zero_trades(generated_templates: Dict[str, Path]) -> None:
    """Consolidated health check — each emitted template must pass a minimum
    viability bar."""
    for stem, path in generated_templates.items():
        body = _strategy_body(_parse(path))
        # Onset implemented.
        assert body.find("OnsetCondition").text == "FirstLargeAfterFailedContinuation"
        # Hold rule valid.
        assert body.find("PrimaryHoldRule").text in {s["enum"] for s in HOLD_RULE_MAP.values()}
        # If Allowlist, at least one box ticked.
        if body.find("SessionMode").text == "Allowlist":
            any_true = any(
                body.find(p).text == "true"
                for p in SESSION_LABELS_CANONICAL.values()
            )
            assert any_true, f"{stem}: Allowlist with nothing allowed"
        # Failed-continuation lookback >= 1.
        assert int(body.find("FailedContinuationLookbackSignals").text) >= 1
