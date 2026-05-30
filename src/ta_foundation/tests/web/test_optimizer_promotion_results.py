"""Tests for the promoted NT-output mirror + per-candidate-report wiring."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from ta_foundation.web import optimizer_session as opt_session
from ta_foundation.web import optimizer_candidate_report as candidate_report
from ta_foundation.web.optimizer_candidate_report import (
    _find_promoted_template_path,
    _resolve_candidate_results_dir,
    build_all_candidate_reports,
)
from ta_foundation.web.optimizer_promotion import (
    PROMOTED_DIRNAME,
    PROMOTED_HANDOFF_DIRNAME,
    PROMOTED_MANIFEST_FILENAME,
)
from ta_foundation.web.optimizer_promotion_results import (
    PROMOTED_RESULTS_DIRNAME,
    _mirror_promoted_results,
)


@pytest.fixture(autouse=True)
def isolate_storage(tmp_path: Path):
    opt_session.set_storage_root(tmp_path / "sessions")
    yield
    opt_session.set_storage_root(None)


def _make_session() -> opt_session.OptimizerSession:
    return opt_session.create_session(
        strategy_id="FakeStrategy",
        seed_template_path="C:/fake/seed.xml",
        instrument="NQ 06-26",
    )


def _write_manifest(session, template_ids: list[str]) -> Path:
    manifest_path = (
        session.directory / "generated_templates" / PROMOTED_DIRNAME
        / PROMOTED_MANIFEST_FILENAME
    )
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps({
        "schema_version": 1,
        "stage_id": PROMOTED_DIRNAME,
        "stage_type": "promoted_backtest",
        "template_count": len(template_ids),
        "templates": [
            {"template_id": tid, "source_stage_id": "stage_1",
             "source_candidate_id": f"stage_1__T__{tid}"}
            for tid in template_ids
        ],
    }), encoding="utf-8")
    return manifest_path


def _write_nt_output(session, template_id: str, files: dict[str, str]) -> Path:
    out = session.directory / "nt_output" / PROMOTED_DIRNAME / template_id
    out.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (out / name).write_text(content, encoding="utf-8")
    return out


# ---------------------------------------------------------------------------
# _mirror_promoted_results
# ---------------------------------------------------------------------------

def test_mirror_copies_each_template_folder():
    session = _make_session()
    manifest_path = _write_manifest(session, ["P_001", "P_002"])
    _write_nt_output(session, "P_001", {"Summary.csv": "a", "Trades.csv": "b"})
    _write_nt_output(session, "P_002", {"Summary.csv": "c"})

    target = (
        session.directory / "deployment_package" / PROMOTED_HANDOFF_DIRNAME
        / PROMOTED_RESULTS_DIRNAME
    )
    mirrored = _mirror_promoted_results(
        nt_output_dir=session.directory / "nt_output" / PROMOTED_DIRNAME,
        manifest_path=manifest_path,
        target_root=target,
    )
    assert sorted(mirrored) == ["P_001", "P_002"]
    assert (target / "P_001" / "Summary.csv").read_text() == "a"
    assert (target / "P_001" / "Trades.csv").read_text() == "b"
    assert (target / "P_002" / "Summary.csv").read_text() == "c"


def test_mirror_is_noop_when_manifest_missing():
    session = _make_session()
    mirrored = _mirror_promoted_results(
        nt_output_dir=session.directory / "nt_output" / PROMOTED_DIRNAME,
        manifest_path=session.directory / "nope.json",
        target_root=session.directory / "out",
    )
    assert mirrored == []


def test_mirror_skips_templates_without_nt_output():
    session = _make_session()
    manifest_path = _write_manifest(session, ["P_001", "P_002"])
    _write_nt_output(session, "P_001", {"Summary.csv": "a"})
    # No nt_output for P_002

    mirrored = _mirror_promoted_results(
        nt_output_dir=session.directory / "nt_output" / PROMOTED_DIRNAME,
        manifest_path=manifest_path,
        target_root=session.directory / "out",
    )
    assert mirrored == ["P_001"]
    assert not (session.directory / "out" / "P_002").exists()


def test_mirror_overwrites_existing_target_folder():
    session = _make_session()
    manifest_path = _write_manifest(session, ["P_001"])
    _write_nt_output(session, "P_001", {"Summary.csv": "fresh"})
    target = session.directory / "out"
    stale = target / "P_001" / "stale.csv"
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_text("stale", encoding="utf-8")

    _mirror_promoted_results(
        nt_output_dir=session.directory / "nt_output" / PROMOTED_DIRNAME,
        manifest_path=manifest_path,
        target_root=target,
    )
    assert (target / "P_001" / "Summary.csv").read_text() == "fresh"
    assert not (target / "P_001" / "stale.csv").exists()


# ---------------------------------------------------------------------------
# _resolve_candidate_results_dir
# ---------------------------------------------------------------------------

def test_resolve_candidate_results_dir_prefers_final(tmp_path: Path):
    pkg = tmp_path / "deployment_package"
    final = pkg / "final_backtest_handoff" / "nt8_backtest_results" / "F_001"
    promoted = pkg / "promoted_handoff" / "nt8_backtest_results" / "F_001"
    final.mkdir(parents=True)
    promoted.mkdir(parents=True)
    assert _resolve_candidate_results_dir(pkg, "F_001") == final


def test_resolve_candidate_results_dir_falls_back_to_promoted(tmp_path: Path):
    pkg = tmp_path / "deployment_package"
    promoted = pkg / "promoted_handoff" / "nt8_backtest_results" / "P_001"
    promoted.mkdir(parents=True)
    assert _resolve_candidate_results_dir(pkg, "P_001") == promoted


def test_resolve_candidate_results_dir_returns_none_when_missing(tmp_path: Path):
    pkg = tmp_path / "deployment_package"
    assert _resolve_candidate_results_dir(pkg, "P_999") is None


# ---------------------------------------------------------------------------
# _find_promoted_template_path
# ---------------------------------------------------------------------------

def test_find_promoted_template_prefers_handoff_mirror():
    session = _make_session()
    mirror = (
        session.directory / "deployment_package" / PROMOTED_HANDOFF_DIRNAME
        / "named_backtest_templates" / "recipe" / "P_001.xml"
    )
    mirror.parent.mkdir(parents=True, exist_ok=True)
    mirror.write_text("<m/>", encoding="utf-8")
    stamped = session.directory / "generated_templates" / PROMOTED_DIRNAME / "P_001.xml"
    stamped.parent.mkdir(parents=True, exist_ok=True)
    stamped.write_text("<s/>", encoding="utf-8")
    assert _find_promoted_template_path(session, "P_001") == mirror


def test_find_promoted_template_falls_back_to_generated():
    session = _make_session()
    stamped = session.directory / "generated_templates" / PROMOTED_DIRNAME / "P_001.xml"
    stamped.parent.mkdir(parents=True, exist_ok=True)
    stamped.write_text("<s/>", encoding="utf-8")
    assert _find_promoted_template_path(session, "P_001") == stamped


def test_find_promoted_template_returns_none_when_missing():
    session = _make_session()
    assert _find_promoted_template_path(session, "P_001") is None


# ---------------------------------------------------------------------------
# build_all_candidate_reports walks both result trees
# ---------------------------------------------------------------------------

def test_build_all_candidate_reports_walks_final_and_promoted_dirs(monkeypatch):
    session = _make_session()
    pkg = session.directory / "deployment_package"
    (pkg / "final_backtest_handoff" / "nt8_backtest_results" / "F_001").mkdir(parents=True)
    (pkg / "final_backtest_handoff" / "nt8_backtest_results" / "F_002").mkdir(parents=True)
    (pkg / "promoted_handoff" / "nt8_backtest_results" / "P_001").mkdir(parents=True)

    calls: list[str] = []

    def fake_build_candidate_report(session, run_id, **kwargs):
        calls.append(run_id)
        return SimpleNamespace(
            run_id=run_id,
            html_path=str(pkg / candidate_report.PER_CANDIDATE_REPORTS_DIRNAME / f"{run_id}.html"),
            sections_rendered=[],
            notes=[],
            to_dict=lambda: {"run_id": run_id},
        )

    monkeypatch.setattr(candidate_report, "build_candidate_report", fake_build_candidate_report)
    result = build_all_candidate_reports(session, purge_existing=False)

    assert sorted(calls) == ["F_001", "F_002", "P_001"]
    assert len(result.per_candidate) == 3


def test_build_all_candidate_reports_returns_empty_when_no_results():
    session = _make_session()
    result = build_all_candidate_reports(session)
    assert result.per_candidate == []
    assert any("nothing to render" in n for n in result.notes)
