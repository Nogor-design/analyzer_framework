"""Tests for the A.4 backfill: synthesizes a fake outputs/ tree and walks it.

Each test asserts a single invariant: structure of ledger after import,
idempotency, partial-failure handling, etc. No live `outputs/` files are
touched.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ta_foundation.research_ledger import Repository, get_repository
from ta_foundation.research_ledger.backfill import (
    BackfillReport,
    backfill_from_outputs,
    discover_sidecars,
)


@pytest.fixture()
def repo(tmp_path: Path) -> Repository:
    return get_repository(tmp_path / "ledger.db")


def _write_sidecar(
    root: Path,
    dir_name: str,
    *,
    file_stem: str = "comparison_report_summary",
    signal: str = "vwap_reclaim_reject",
    rank: int = 1,
    hardening_passed: bool = True,
    metrics_pf: float = 2.1,
    metrics_trades: int = 120,
    tier_id: str = "qualified",
) -> Path:
    body = {
        "schema_version": 1,
        "stage": {"id": "03_levels_regions", "label": "Levels", "ordinal": 3,
                   "kind": "funnel"},
        "instrument": {"symbol": "NQ", "tick_size": 0.25},
        "rankings": [
            {
                "rank": rank,
                "family": "level",
                "signal": signal,
                "direction": "long",
                "timeframe": "5m",
                "params": {"min_dist_ticks": 8, "stop_ticks": 8, "target_ticks": 24},
                "metrics": {"trade_count": metrics_trades, "profit_factor": metrics_pf,
                             "win_rate": 0.5, "expectancy_ticks": 4.0},
                "hardening": {
                    "enabled": True,
                    "passed": hardening_passed,
                    "validation": {"passed": hardening_passed, "gates": []},
                    "evaluation_oos": {"trade_count": 40, "profit_factor": metrics_pf * 0.9,
                                        "expectancy_ticks": 3.4},
                    "slippage_stress": {"passed": hardening_passed,
                                         "expectancy_loss_pct": 20,
                                         "max_expectancy_loss_pct": 40},
                },
                "tier": {"id": tier_id, "label": tier_id.title(), "verdict": "x",
                         "criteria_met": []},
            }
        ],
    }
    d = root / dir_name
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{file_stem}.json"
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


# ---------- discover_sidecars ----------------------------------------------


def test_discover_sidecars_finds_summary_files(tmp_path: Path) -> None:
    root = tmp_path / "outputs"
    _write_sidecar(root, "run_a")
    _write_sidecar(root, "run_b", file_stem="04_ny_open_summary")
    # Decoy file that should not be picked up.
    (root / "run_a" / "manifest.json").write_text("{}", encoding="utf-8")
    found = discover_sidecars([root])
    assert len(found) == 2


def test_discover_sidecars_handles_missing_root(tmp_path: Path) -> None:
    found = discover_sidecars([tmp_path / "no_such_root"])
    assert found == []


def test_discover_sidecars_deduplicates_across_roots(tmp_path: Path) -> None:
    root = tmp_path / "outputs"
    _write_sidecar(root, "run_a")
    # Same root referenced twice should not yield duplicates.
    found = discover_sidecars([root, root])
    assert len(found) == 1


# ---------- backfill_from_outputs happy path -------------------------------


def test_backfill_imports_one_sidecar(repo: Repository, tmp_path: Path) -> None:
    root = tmp_path / "outputs"
    _write_sidecar(root, "codex_vwap_run_a")
    report = backfill_from_outputs(repo, [root])
    assert report.sidecars_scanned == 1
    assert report.hypotheses_registered == 1
    assert report.runs_inserted == 1
    assert report.candidates_inserted == 1
    assert report.errors == []

    survivors = repo.list_candidates(gate_verdict="survivor")
    assert len(survivors) == 1
    cand = survivors[0]
    # Family inference: 'vwap_reclaim_reject' signal → 'vwap_reject_fade' family.
    h = repo.get_hypothesis(cand.hypothesis_id)
    assert h is not None and h.family == "vwap_reject_fade"
    assert h.instrument == "NQ"
    assert h.registered_by == "backfill"


def test_backfill_imports_multiple_sidecars(repo: Repository, tmp_path: Path) -> None:
    root = tmp_path / "outputs"
    _write_sidecar(root, "run_a", signal="vwap_reclaim_reject")
    _write_sidecar(root, "run_b", signal="orb_break_close",
                   file_stem="04_ny_open_orb_summary", tier_id="reject",
                   hardening_passed=False)
    _write_sidecar(root, "run_c", signal="unknown_signal",
                   tier_id="reject", hardening_passed=False)
    report = backfill_from_outputs(repo, [root])
    assert report.sidecars_parsed == 3
    assert report.hypotheses_registered == 3
    assert report.candidates_inserted == 3

    families = {repo.get_hypothesis(c.hypothesis_id).family
                for c in repo.list_candidates(limit=50)}
    assert "vwap_reject_fade" in families
    assert "legacy_imported" in families


def test_backfill_is_idempotent(repo: Repository, tmp_path: Path) -> None:
    root = tmp_path / "outputs"
    _write_sidecar(root, "run_a")
    first = backfill_from_outputs(repo, [root])
    second = backfill_from_outputs(repo, [root])
    assert first.runs_inserted == 1
    assert second.runs_inserted == 0
    assert second.runs_skipped == 1
    assert second.hypotheses_reused == 1
    # And the ledger still has exactly one candidate.
    assert len(repo.list_candidates()) == 1


# ---------- mode inference --------------------------------------------------


def test_backfill_infers_hardened_mode(repo: Repository, tmp_path: Path) -> None:
    root = tmp_path / "outputs"
    _write_sidecar(root, "codex_vwap_london_reject_fade_hardened_20260508")
    backfill_from_outputs(repo, [root])
    runs = [repo.get_run(r) for r in
            [r.run_id for c in repo.list_candidates() for r in [repo.get_run(c.run_id)] if r]]
    assert any(r and r.mode == "hardened" for r in runs)


def test_backfill_infers_locked_holdout_mode(repo: Repository, tmp_path: Path) -> None:
    root = tmp_path / "outputs"
    _write_sidecar(root, "codex_locked_holdout_run")
    backfill_from_outputs(repo, [root])
    cand = repo.list_candidates()[0]
    run = repo.get_run(cand.run_id)
    assert run is not None and run.mode == "locked_holdout"


def test_backfill_defaults_to_fast_probe_mode(repo: Repository, tmp_path: Path) -> None:
    root = tmp_path / "outputs"
    _write_sidecar(root, "some_run_with_no_mode_keyword")
    backfill_from_outputs(repo, [root])
    cand = repo.list_candidates()[0]
    run = repo.get_run(cand.run_id)
    assert run is not None and run.mode == "fast_probe"


# ---------- error handling -------------------------------------------------


def test_backfill_handles_unsupported_schema_version(
    repo: Repository, tmp_path: Path
) -> None:
    root = tmp_path / "outputs"
    d = root / "weird_run"
    d.mkdir(parents=True)
    (d / "weird_summary.json").write_text(json.dumps({"schema_version": 99}),
                                           encoding="utf-8")
    report = backfill_from_outputs(repo, [root])
    assert report.sidecars_skipped == 1
    assert report.hypotheses_registered == 0
    assert len(report.errors) == 1


def test_backfill_continues_on_malformed_sidecar(
    repo: Repository, tmp_path: Path
) -> None:
    root = tmp_path / "outputs"
    _write_sidecar(root, "good_run")
    bad = root / "bad_run"
    bad.mkdir()
    (bad / "bad_summary.json").write_text("{not json", encoding="utf-8")
    report = backfill_from_outputs(repo, [root])
    assert report.candidates_inserted >= 1
    assert len(report.errors) >= 1


def test_backfill_preserves_notes_on_candidate(
    repo: Repository, tmp_path: Path
) -> None:
    root = tmp_path / "outputs"
    _write_sidecar(root, "codex_run_with_notes", signal="vwap_reclaim_reject",
                   tier_id="qualified")
    backfill_from_outputs(repo, [root])
    cand = repo.list_candidates()[0]
    assert cand.notes_json is not None
    notes = json.loads(cand.notes_json)
    assert notes["signal"] == "vwap_reclaim_reject"
    assert notes["tier_id"] == "qualified"


def test_backfill_count_hypotheses_tested_reflects_imports(
    repo: Repository, tmp_path: Path
) -> None:
    assert repo.count_hypotheses_tested() == 0
    root = tmp_path / "outputs"
    _write_sidecar(root, "run_a", signal="vwap_reclaim_reject")
    _write_sidecar(root, "run_b", signal="orb_break_close")
    backfill_from_outputs(repo, [root])
    assert repo.count_hypotheses_tested() == 2
