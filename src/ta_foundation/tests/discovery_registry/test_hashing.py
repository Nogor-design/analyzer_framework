"""Unit tests for probe-identity hashing."""

from __future__ import annotations

import yaml

from ta_foundation.discovery_registry.hashing import (
    KNOWN_DISCOVERY_BLOCKS,
    compute_probe_identity,
    jaccard_overlap,
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
    break_extreme: {enabled: false}
  outcome:
    ticks:
      enabled: true
      take_profit: [40, 60, 80, 100, 150]
      stop: [16, 20]
"""

# Same probe identity as WIDE_STOP_YAML but with reordered keys and lists,
# different title, and a research note. Hash MUST match.
WIDE_STOP_YAML_REORDERED = """
report:
  title: "Different title shouldn't matter"
research_note:
  status: "test"
discovery:
  instrument: "NQ"
  stage: "03_levels_regions"
level_discovery:
  timeframes: [5, 1]
  enabled: true
  signals:
    large_candle_origin_retest:
      direction: [0]
      enabled: true
      large_body_mult: [2.5, 2.0]
      max_retest_bars: [12, 6]
      min_body_ticks: [12.0, 8.0]
      min_close_ticks: [3.0, 2.0]
      avg_lookback: [40, 20]
      touch_ticks: [8.0, 5.0]
  outcome:
    ticks:
      take_profit: [100, 40, 150, 60, 80]
      enabled: true
      stop: [20, 16]
  entry_timing:
    break_extreme: {enabled: false}
    next_open: {enabled: true}
"""

# Different outcome geometry: 8t stop instead of 16/20. Hash MUST differ.
NARROW_STOP_YAML = """
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
      take_profit: [16, 24, 40, 60, 100]
      stop: [8, 12, 16, 20]
"""


def _parse(yaml_text: str) -> dict:
    return yaml.safe_load(yaml_text)


def test_compute_identity_basic_shape():
    raw = _parse(WIDE_STOP_YAML)
    ident = compute_probe_identity(raw)
    assert ident.instrument == "NQ"
    assert ident.stage == "03_levels_regions"
    assert len(ident.signals) == 1
    sig = ident.signals[0]
    assert sig.block == "level_discovery"
    assert sig.signal == "large_candle_origin_retest"
    assert "avg_lookback" in sig.param_ranges
    # avg_lookback should be sorted
    assert sig.param_ranges["avg_lookback"] == (20, 40)
    assert ident.outcome_mode == "ticks"
    assert ident.outcome_take_profit == (40, 60, 80, 100, 150)
    assert ident.outcome_stop == (16, 20)
    assert ident.entry_timing == ("next_open",)
    assert ident.timeframes == (1, 5)


def test_hash_stable_across_formatting():
    a = compute_probe_identity(_parse(WIDE_STOP_YAML))
    b = compute_probe_identity(_parse(WIDE_STOP_YAML_REORDERED))
    assert probe_hash(a) == probe_hash(b), (
        "Probe hash must be invariant to list ordering, key ordering, "
        "extra metadata, and YAML whitespace."
    )


def test_hash_differs_when_outcome_geometry_changes():
    a = compute_probe_identity(_parse(WIDE_STOP_YAML))
    b = compute_probe_identity(_parse(NARROW_STOP_YAML))
    assert probe_hash(a) != probe_hash(b)


def test_disabled_signal_excluded():
    raw = _parse(WIDE_STOP_YAML)
    # Disable the signal in a copy
    raw["level_discovery"]["signals"]["large_candle_origin_retest"]["enabled"] = False
    ident = compute_probe_identity(raw)
    assert ident.signals == ()


def test_non_grid_keys_ignored():
    """Detector-tuning keys (tick_size, atr_period, min_atr_ticks) should not
    appear in param_ranges — they are not part of the search grid."""
    raw = _parse(WIDE_STOP_YAML)
    raw["level_discovery"]["signals"]["large_candle_origin_retest"]["tick_size"] = 0.25
    raw["level_discovery"]["signals"]["large_candle_origin_retest"]["atr_period"] = 14
    ident = compute_probe_identity(raw)
    sig = ident.signals[0]
    assert "tick_size" not in sig.param_ranges
    assert "atr_period" not in sig.param_ranges


def test_jaccard_overlap():
    assert jaccard_overlap((1, 2, 3), (1, 2, 3)) == 1.0
    assert jaccard_overlap((), ()) == 1.0
    assert jaccard_overlap((1, 2), (3, 4)) == 0.0
    assert jaccard_overlap((1, 2, 3), (2, 3, 4)) == 0.5  # 2 shared / 4 union
    # Approximate equality
    val = jaccard_overlap((1, 2, 3, 4), (3, 4, 5))
    assert abs(val - 2 / 5) < 1e-9


def test_all_known_blocks_recognised():
    """Every entry in KNOWN_DISCOVERY_BLOCKS must be reachable by the walker."""
    for block in KNOWN_DISCOVERY_BLOCKS:
        raw = {
            "discovery": {"instrument": "NQ"},
            block: {
                "enabled": True,
                "signals": {
                    "test_signal": {"enabled": True, "p": [1, 2, 3]},
                },
            },
        }
        ident = compute_probe_identity(raw)
        assert len(ident.signals) == 1
        assert ident.signals[0].block == block


def test_empty_yaml_yields_empty_identity():
    ident = compute_probe_identity({})
    assert ident.signals == ()
    assert ident.instrument == "UNKNOWN"


# --- ORB-style nested block (orb_discovery.orb.{signal_type: [...]}) ---

ORB_BODY_MIDPOINT_YAML = """
discovery:
  stage: "06_validate"
  instrument: "NQ"
orb_discovery:
  enabled: true
  orb:
    signal_type: ["failure_reclaim"]
    orb_minutes: [5]
    session_open_hour: 7
    session_open_minute: 30
    direction: [0]
    min_range_ticks: [8]
    require_close_beyond: [true]
    min_sweep_ticks: [4.0]
    close_back_ticks: [0.0]
    max_reclaim_bars: [1]
  entry_timing:
    body_midpoint: {enabled: true, fill_timeout_bars: 5}
    next_open: {enabled: false}
  outcome:
    ticks:
      enabled: true
      take_profit: [150]
      stop: [20]
"""


def test_orb_nested_signal_type_extracted():
    raw = _parse(ORB_BODY_MIDPOINT_YAML)
    ident = compute_probe_identity(raw)
    assert len(ident.signals) == 1
    sig = ident.signals[0]
    assert sig.block == "orb_discovery"
    assert sig.signal == "failure_reclaim"
    # session_open_hour / session_open_minute are detector-config, not grid.
    assert "session_open_hour" not in sig.param_ranges
    # signal_type IS the identity, not a grid param.
    assert "signal_type" in sig.param_ranges  # tracked but won't drive overlap differently
    assert ident.outcome_mode == "ticks"
    assert ident.outcome_take_profit == (150,)
    assert ident.outcome_stop == (20,)
    assert ident.entry_timing == ("body_midpoint",)


def test_orb_signal_type_list_yields_multiple_signals():
    """If signal_type contains multiple values, each becomes its own SignalSpec."""
    raw = _parse(ORB_BODY_MIDPOINT_YAML)
    raw["orb_discovery"]["orb"]["signal_type"] = ["breakout", "failure_reclaim"]
    ident = compute_probe_identity(raw)
    names = sorted([s.signal for s in ident.signals])
    assert names == ["breakout", "failure_reclaim"]
