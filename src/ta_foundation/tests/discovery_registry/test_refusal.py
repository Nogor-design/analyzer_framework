"""Tests for the CLI-side refusal helpers (YAML resolver + penalty injection)."""

from __future__ import annotations

from pathlib import Path

import yaml

from ta_foundation.discovery_registry import (
    ProbeRecord,
    ProbeRegistry,
    compute_probe_identity,
)
from ta_foundation.discovery_registry.refusal import (
    DEFAULT_CUMULATIVE_DECAY_FACTOR,
    build_yaml_resolver,
    compute_effective_n_hypotheses,
    inject_effective_penalty,
)


_BASE_YAML = """
discovery:
  stage: "06_validate"
  instrument: "NQ"
level_discovery:
  enabled: true
  signals:
    large_candle_origin_retest:
      enabled: true
      avg_lookback: [40]
      large_body_mult: [2.5]
      direction: [0]
  outcome:
    ticks:
      enabled: true
      take_profit: [40]
      stop: [16]
  hardening:
    enabled: true
    n_hypotheses_tested: 100
"""


def _seed_probe_registry(output_dir: Path, family: str, totals: list[int]) -> None:
    reg = ProbeRegistry(output_dir)
    for i, n in enumerate(totals):
        reg.append(ProbeRecord(
            hash=f"h{i}", yaml_path=f"x{i}.yaml", sidecar_path=None,
            run_date="2026-05-13T00:00:00Z", instrument="NQ", stage=None,
            families=[family], n_combinations_run=n,
        ))


def test_compute_effective_uses_family_filter(tmp_path: Path):
    family = "level_discovery::large_candle_origin_retest"
    _seed_probe_registry(tmp_path, family, [8640, 1280])
    # Unrelated family added — must NOT contribute to cumulative_family.
    reg = ProbeRegistry(tmp_path)
    reg.append(ProbeRecord(
        hash="other", yaml_path="z.yaml", sidecar_path=None,
        run_date="2026-05-13T01:00:00Z", instrument="NQ", stage=None,
        families=["orb_discovery::breakout"], n_combinations_run=99999,
    ))

    raw = yaml.safe_load(_BASE_YAML)
    ident = compute_probe_identity(raw)
    info = compute_effective_n_hypotheses(tmp_path, ident)
    assert info["cumulative_family"] == 8640 + 1280
    assert info["cumulative_global"] == 8640 + 1280 + 99999
    assert info["decay_factor"] == DEFAULT_CUMULATIVE_DECAY_FACTOR


def test_inject_penalty_promotes_yaml_when_floor_higher(tmp_path: Path):
    family = "level_discovery::large_candle_origin_retest"
    _seed_probe_registry(tmp_path, family, [8640, 1280])  # cumulative=9920, floor=992

    raw = yaml.safe_load(_BASE_YAML)
    ident = compute_probe_identity(raw)
    info = compute_effective_n_hypotheses(tmp_path, ident)
    mutations = inject_effective_penalty(raw, info)

    # YAML had 100, floor is 992, so promote.
    assert len(mutations) == 1
    m = mutations[0]
    assert m["block"] == "level_discovery"
    assert m["yaml_n"] == 100
    assert m["effective_n"] == 992
    # The mutation lands in the cfg dict.
    hardening = raw["level_discovery"]["hardening"]
    assert hardening["n_hypotheses_tested"] == 992
    assert hardening["yaml_n_hypotheses_tested"] == 100


def test_inject_penalty_keeps_yaml_when_already_higher(tmp_path: Path):
    family = "level_discovery::large_candle_origin_retest"
    _seed_probe_registry(tmp_path, family, [1000])  # floor=100

    raw = yaml.safe_load(_BASE_YAML)  # YAML has 100
    ident = compute_probe_identity(raw)
    info = compute_effective_n_hypotheses(tmp_path, ident)
    mutations = inject_effective_penalty(raw, info)
    # Equal — no mutation needed.
    assert mutations == []
    # YAML untouched.
    assert raw["level_discovery"]["hardening"]["n_hypotheses_tested"] == 100
    assert "yaml_n_hypotheses_tested" not in raw["level_discovery"]["hardening"]


def test_inject_penalty_skips_disabled_hardening(tmp_path: Path):
    family = "level_discovery::large_candle_origin_retest"
    _seed_probe_registry(tmp_path, family, [99999])

    raw = yaml.safe_load(_BASE_YAML)
    raw["level_discovery"]["hardening"]["enabled"] = False
    ident = compute_probe_identity(raw)
    info = compute_effective_n_hypotheses(tmp_path, ident)
    mutations = inject_effective_penalty(raw, info)
    assert mutations == []


def test_yaml_resolver_caches_and_handles_missing(tmp_path: Path):
    yaml_path = tmp_path / "probe.yaml"
    yaml_path.write_text(_BASE_YAML, encoding="utf-8")

    resolver = build_yaml_resolver()
    a = resolver(str(yaml_path))
    b = resolver(str(yaml_path))
    assert a is b  # cached

    missing = resolver(str(tmp_path / "does_not_exist.yaml"))
    assert missing is None
