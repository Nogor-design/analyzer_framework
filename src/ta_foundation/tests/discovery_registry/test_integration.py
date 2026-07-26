"""End-to-end integration test for the graveyard refusal + cumulative penalty.

These tests use real probe YAMLs from `discovery/` and a backfilled registry
from real sidecars in `output/`. They are skipped when the repo's
`output/` doesn't contain the expected sidecars (e.g. fresh clones), so
they don't break clean-room CI runs.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from ta_foundation.discovery_registry import (
    GraveyardRegistry,
    check_graveyard,
    compute_probe_identity,
)
from ta_foundation.discovery_registry.backfill import backfill
from ta_foundation.discovery_registry.refusal import (
    build_yaml_resolver,
    compute_effective_n_hypotheses,
    inject_effective_penalty,
)


REPO_ROOT = Path(__file__).resolve().parents[4]
OUTPUT_DIR = REPO_ROOT / "output"
DISCOVERY_DIR = REPO_ROOT / "discovery"

# YAMLs the integration test targets. Skip the file if missing.
WIDE_STOP_PROBE = DISCOVERY_DIR / "03_nq_large_candle_origin_retest_wide_stop_probe.yaml"
WIDE_STOP_HARDENING = DISCOVERY_DIR / "03_nq_large_candle_origin_retest_wide_stop_high_atr_locked_hardening.yaml"
BODY_MIDPOINT_HARDENING = DISCOVERY_DIR / "04_nq_ny_open_orb_failure_reclaim_body_midpoint_locked_hardening.yaml"


def _seed_isolated_output(tmp_path: Path) -> Path:
    """Copy real sidecars to an isolated tmp output_dir + run backfill."""
    if not OUTPUT_DIR.is_dir():
        pytest.skip("repo output/ dir not present")
    out = tmp_path / "output"
    out.mkdir()
    for s in OUTPUT_DIR.glob("*_summary.json"):
        if s.name.startswith("_"):
            continue
        shutil.copy(s, out / s.name)
    summary = backfill(out, discovery_dirs=[DISCOVERY_DIR, DISCOVERY_DIR / "generated"], verbose=False)
    assert summary.sidecars_scanned > 0
    assert summary.probe_records_added > 0
    return out


def _load_yaml(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


@pytest.mark.skipif(not WIDE_STOP_PROBE.exists(), reason="wide-stop probe YAML missing")
def test_wide_stop_probe_refused_by_graveyard(tmp_path: Path):
    out = _seed_isolated_output(tmp_path)
    raw = _load_yaml(WIDE_STOP_PROBE)
    ident = compute_probe_identity(raw)

    graveyard = GraveyardRegistry(out)
    resolver = build_yaml_resolver()

    hit = check_graveyard(ident, graveyard, identity_resolver=resolver)
    assert hit is not None, (
        "Wide-stop probe YAML must refuse after backfill — it was rejected "
        "on broad PF<1.0 in the actual run that produced the sidecar."
    )
    # Exact hash match is expected since the YAML is unchanged.
    assert hit.match_kind == "exact_hash"
    assert "rejected" in hit.matched_record.reason.lower() or \
           "pf" in hit.matched_record.reason.lower()


@pytest.mark.skipif(not WIDE_STOP_HARDENING.exists(), reason="wide-stop hardening YAML missing")
def test_wide_stop_hardening_refused_by_graveyard(tmp_path: Path):
    out = _seed_isolated_output(tmp_path)
    raw = _load_yaml(WIDE_STOP_HARDENING)
    ident = compute_probe_identity(raw)

    graveyard = GraveyardRegistry(out)
    resolver = build_yaml_resolver()
    hit = check_graveyard(ident, graveyard, identity_resolver=resolver)
    assert hit is not None
    assert hit.match_kind == "exact_hash"
    assert "hardening failed" in hit.matched_record.reason.lower()


@pytest.mark.skipif(not BODY_MIDPOINT_HARDENING.exists(), reason="body-midpoint YAML missing")
def test_body_midpoint_NOT_refused(tmp_path: Path):
    """The currently-shadow-enrolled body-midpoint candidate must NOT refuse —
    it's the actively monitored live candidate and its prior hardening passed."""
    out = _seed_isolated_output(tmp_path)
    raw = _load_yaml(BODY_MIDPOINT_HARDENING)
    ident = compute_probe_identity(raw)
    graveyard = GraveyardRegistry(out)
    resolver = build_yaml_resolver()
    hit = check_graveyard(ident, graveyard, identity_resolver=resolver)
    assert hit is None, (
        "Body-midpoint hardening should not be in the graveyard — its prior "
        "hardening passed and it is currently in shadow."
    )


@pytest.mark.skipif(not WIDE_STOP_HARDENING.exists(), reason="wide-stop hardening YAML missing")
def test_cumulative_penalty_against_large_candle_family(tmp_path: Path):
    """The large-candle family has tested ~10K hypotheses in the real backfill.
    Verify the family-filtered cumulative reflects that order of magnitude."""
    out = _seed_isolated_output(tmp_path)
    raw = _load_yaml(WIDE_STOP_HARDENING)
    ident = compute_probe_identity(raw)
    info = compute_effective_n_hypotheses(out, ident)
    # As of backfill, large_candle family cumulative is ~9924. Allow slack.
    assert info["cumulative_family"] >= 5000, (
        f"Expected significant cumulative count for large_candle family; "
        f"got {info['cumulative_family']}. Did the backfill change shape?"
    )
    # Inject penalty mutates only if floor > yaml_n. Wide-stop hardening
    # YAML has n_hypotheses_tested=1280; floor=cumulative/10≈992 → no mutation.
    # We're not asserting on mutation count here, only that the calc is sane.
    assert info["decay_factor"] == 10
