"""Unit tests for ProbeRegistry, GraveyardRegistry, and check_graveyard."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from ta_foundation.discovery_registry import (
    GraveyardRecord,
    GraveyardRegistry,
    ProbeRecord,
    ProbeRegistry,
    check_graveyard,
    compute_probe_identity,
    probe_hash,
)


WIDE_STOP_YAML = """
discovery:
  stage: "03_levels_regions"
  instrument: "NQ"
level_discovery:
  enabled: true
  timeframes: [1, 5]
  signals:
    large_candle_origin_retest:
      enabled: true
      avg_lookback: [20, 40]
      large_body_mult: [2.0, 2.5]
      min_body_ticks: [8.0, 12.0]
      max_retest_bars: [6, 12]
      touch_ticks: [5.0, 8.0]
      min_close_ticks: [2.0, 3.0]
      direction: [0]
  entry_timing:
    next_open: {enabled: true}
  outcome:
    ticks:
      enabled: true
      take_profit: [40, 60, 80, 100, 150]
      stop: [16, 20]
"""

WIDE_STOP_NEAR_VARIANT = """
discovery:
  stage: "03_levels_regions"
  instrument: "NQ"
level_discovery:
  enabled: true
  timeframes: [1, 5]
  signals:
    large_candle_origin_retest:
      enabled: true
      # 7/8 param ranges shared with WIDE_STOP_YAML; min_close_ticks tweaked.
      avg_lookback: [20, 40]
      large_body_mult: [2.0, 2.5]
      min_body_ticks: [8.0, 12.0]
      max_retest_bars: [6, 12]
      touch_ticks: [5.0, 8.0]
      min_close_ticks: [2.0, 3.0, 4.0]   # slight superset
      direction: [0]
  entry_timing:
    next_open: {enabled: true}
  outcome:
    ticks:
      enabled: true
      take_profit: [40, 60, 80, 100, 150]
      stop: [16, 20]
"""

UNRELATED_FAMILY_YAML = """
discovery:
  stage: "04_ny_open"
  instrument: "NQ"
orb_discovery:
  enabled: true
  timeframes: [5]
  signals:
    orb_breakout:
      enabled: true
      lookback_minutes: [15, 30]
  entry_timing:
    break_extreme: {enabled: true}
  outcome:
    ticks:
      enabled: true
      take_profit: [40, 60]
      stop: [8, 12]
"""


def test_probe_registry_round_trip(tmp_path: Path):
    reg = ProbeRegistry(tmp_path)
    record = ProbeRecord(
        hash="abc123",
        yaml_path="discovery/foo.yaml",
        sidecar_path="output/foo_summary.json",
        run_date="2026-05-13T12:00:00Z",
        instrument="NQ",
        stage="03_levels_regions",
        families=["level_discovery::large_candle_origin_retest"],
        n_combinations_run=1280,
    )
    assert reg.append(record) is True
    # Idempotent
    assert reg.append(record) is False
    # Reload
    reg2 = ProbeRegistry(tmp_path)
    records = reg2.records()
    assert len(records) == 1
    assert records[0].hash == "abc123"
    assert records[0].n_combinations_run == 1280


def test_probe_registry_cumulative_count(tmp_path: Path):
    reg = ProbeRegistry(tmp_path)
    reg.append(ProbeRecord(
        hash="h1", yaml_path="a.yaml", sidecar_path=None,
        run_date="2026-05-01T00:00:00Z", instrument="NQ", stage=None,
        families=["level_discovery::sig_a"], n_combinations_run=8640,
    ))
    reg.append(ProbeRecord(
        hash="h2", yaml_path="b.yaml", sidecar_path=None,
        run_date="2026-05-13T00:00:00Z", instrument="NQ", stage=None,
        families=["level_discovery::sig_a"], n_combinations_run=1280,
    ))
    reg.append(ProbeRecord(
        hash="h3", yaml_path="c.yaml", sidecar_path=None,
        run_date="2026-05-13T01:00:00Z", instrument="NQ", stage=None,
        families=["orb_discovery::orb_breakout"], n_combinations_run=500,
    ))
    assert reg.cumulative_hypotheses() == 8640 + 1280 + 500
    # Family filter
    assert reg.cumulative_hypotheses(
        family_filter=["level_discovery::sig_a"]
    ) == 8640 + 1280
    assert reg.cumulative_hypotheses(
        family_filter=["orb_discovery::orb_breakout"]
    ) == 500


def test_graveyard_exact_hash_hit(tmp_path: Path):
    raw = yaml.safe_load(WIDE_STOP_YAML)
    ident = compute_probe_identity(raw)
    h = probe_hash(ident)

    gr = GraveyardRegistry(tmp_path)
    gr.append(GraveyardRecord(
        hash=h,
        yaml_path="discovery/wide_stop.yaml",
        sidecar_path=None,
        verdict_date="2026-05-13T18:00:00Z",
        reason="hardening t-test failed; slippage stress catastrophic",
        families=["level_discovery::large_candle_origin_retest"],
        instrument="NQ",
        stress_failure_cell={"slip_ticks": 2, "delay_bars": 1, "expectancy_loss_pct": 597.0},
    ))

    hit = check_graveyard(ident, gr)
    assert hit is not None
    assert hit.match_kind == "exact_hash"
    assert hit.proposed_hash == h
    assert "slippage stress" in hit.matched_record.reason


def test_graveyard_near_match_with_resolver(tmp_path: Path):
    """Different YAMLs encoding similar enough probes should hit as near-match."""
    raw_old = yaml.safe_load(WIDE_STOP_YAML)
    ident_old = compute_probe_identity(raw_old)
    h_old = probe_hash(ident_old)

    raw_new = yaml.safe_load(WIDE_STOP_NEAR_VARIANT)
    ident_new = compute_probe_identity(raw_new)

    assert probe_hash(ident_old) != probe_hash(ident_new), \
        "Test setup invalid — variants must hash differently for near-match path."

    gr = GraveyardRegistry(tmp_path)
    gr.append(GraveyardRecord(
        hash=h_old,
        yaml_path="discovery/wide_stop.yaml",
        sidecar_path=None,
        verdict_date="2026-05-13T18:00:00Z",
        reason="hardening failed",
        families=["level_discovery::large_candle_origin_retest"],
        instrument="NQ",
    ))

    def resolver(yaml_path: str):
        # In a real run this would re-read the file from disk; tests inject directly.
        return ident_old if yaml_path == "discovery/wide_stop.yaml" else None

    hit = check_graveyard(ident_new, gr, identity_resolver=resolver)
    assert hit is not None
    assert hit.match_kind == "near_match"
    assert hit.param_overlap >= 0.80
    assert hit.outcome_overlap == 1.0  # same TP/SL grid


def test_graveyard_no_match_for_unrelated_family(tmp_path: Path):
    raw_other = yaml.safe_load(WIDE_STOP_YAML)
    ident_other = compute_probe_identity(raw_other)

    raw_orb = yaml.safe_load(UNRELATED_FAMILY_YAML)
    ident_orb = compute_probe_identity(raw_orb)

    gr = GraveyardRegistry(tmp_path)
    gr.append(GraveyardRecord(
        hash=probe_hash(ident_other),
        yaml_path="discovery/other.yaml",
        sidecar_path=None,
        verdict_date="2026-05-13T18:00:00Z",
        reason="some failure",
        families=["level_discovery::large_candle_origin_retest"],
        instrument="NQ",
    ))

    def resolver(yaml_path: str):
        return ident_other if yaml_path == "discovery/other.yaml" else None

    hit = check_graveyard(ident_orb, gr, identity_resolver=resolver)
    assert hit is None  # different family — no match


def test_graveyard_near_match_disabled_without_resolver(tmp_path: Path):
    """Without an identity_resolver, only exact-hash matches are returned."""
    raw_old = yaml.safe_load(WIDE_STOP_YAML)
    ident_old = compute_probe_identity(raw_old)
    raw_new = yaml.safe_load(WIDE_STOP_NEAR_VARIANT)
    ident_new = compute_probe_identity(raw_new)

    gr = GraveyardRegistry(tmp_path)
    gr.append(GraveyardRecord(
        hash=probe_hash(ident_old),
        yaml_path="discovery/wide_stop.yaml",
        sidecar_path=None,
        verdict_date="2026-05-13T18:00:00Z",
        reason="failed",
        families=["level_discovery::large_candle_origin_retest"],
        instrument="NQ",
    ))

    hit = check_graveyard(ident_new, gr, identity_resolver=None)
    assert hit is None


def test_graveyard_override_appended(tmp_path: Path):
    raw = yaml.safe_load(WIDE_STOP_YAML)
    ident = compute_probe_identity(raw)
    h = probe_hash(ident)
    gr = GraveyardRegistry(tmp_path)
    gr.append(GraveyardRecord(
        hash=h, yaml_path="x.yaml", sidecar_path=None,
        verdict_date="2026-05-13T18:00:00Z", reason="failed",
        families=["level_discovery::large_candle_origin_retest"],
        instrument="NQ",
    ))

    gr.record_override(h, "testing-different-stop-budget", "discovery/new_attempt.yaml")
    gr_reload = GraveyardRegistry(tmp_path)
    rec = gr_reload.find_by_hash(h)
    assert rec is not None
    assert len(rec.override_history) == 1
    assert rec.override_history[0]["reason"] == "testing-different-stop-budget"
    assert rec.override_history[0]["yaml_path"] == "discovery/new_attempt.yaml"


def test_registry_file_created_in_output_dir(tmp_path: Path):
    reg = ProbeRegistry(tmp_path)
    reg.append(ProbeRecord(
        hash="h", yaml_path="x.yaml", sidecar_path=None,
        run_date="2026-05-13T00:00:00Z", instrument="NQ", stage=None,
        families=[], n_combinations_run=0,
    ))
    assert (tmp_path / "_probe_registry.json").exists()
    with (tmp_path / "_probe_registry.json").open() as f:
        data = json.load(f)
    assert data["version"] == 1
    assert len(data["records"]) == 1
