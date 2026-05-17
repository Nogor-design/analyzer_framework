from __future__ import annotations

"""Tests for the per-candidate report builder.

Uses the real optimizer fixture session ``opt_5bab6a5ee1ea`` to keep
ingest realistic (the parsers expect actual NinjaTrader CSV shapes
that are tedious to fabricate). Each test that needs the fixture is
skipped automatically if the session isn't present.
"""

import shutil
from pathlib import Path

import pytest

from ta_foundation.web import optimizer_session as opt_session
from ta_foundation.web.optimizer_candidate_report import (
    DEFAULT_FINALIST_SECTIONS,
    PER_CANDIDATE_REPORTS_DIRNAME,
    CandidateReportError,
    build_all_candidate_reports,
    build_candidate_report,
    list_existing_candidate_reports,
)


FIXTURE_SESSION = "opt_5bab6a5ee1ea"
FIXTURE_ROOT = Path(".ta_artifacts/web_optimizer/sessions") / FIXTURE_SESSION


def _have_fixture() -> bool:
    return (FIXTURE_ROOT / "session.json").exists()


needs_fixture = pytest.mark.skipif(
    not _have_fixture(),
    reason=f"requires real session fixture at {FIXTURE_ROOT}",
)


@pytest.fixture
def fixture_session(tmp_path: Path):
    """Copy the real session into a temp dir so we don't mutate the
    on-disk fixture between tests."""
    if not _have_fixture():
        pytest.skip(f"no fixture at {FIXTURE_ROOT}")
    sessions_root = tmp_path / "sessions"
    sessions_root.mkdir()
    shutil.copytree(FIXTURE_ROOT, sessions_root / FIXTURE_SESSION)
    opt_session.set_storage_root(sessions_root)
    yield opt_session.get_session(FIXTURE_SESSION)
    opt_session.set_storage_root(None)


@needs_fixture
def test_single_candidate_renders_default_sections(fixture_session):
    result = build_candidate_report(fixture_session, "F_001")
    assert result.html_path is not None
    assert Path(result.html_path).exists()
    assert Path(result.html_path).stat().st_size > 50_000  # non-trivial HTML
    assert result.sections_rendered == DEFAULT_FINALIST_SECTIONS
    assert all(not n.startswith("Unknown section id") for n in result.notes)


@needs_fixture
def test_custom_section_subset(fixture_session):
    result = build_candidate_report(
        fixture_session, "F_001",
        sections=["run_kpi_cards", "run_settings_table"],
    )
    assert result.sections_rendered == ["run_kpi_cards", "run_settings_table"]
    html = Path(result.html_path).read_text(encoding="utf-8")
    # Banner + analysis chart sections should not appear.
    assert "exec_card_god_banner" not in html
    assert "analysis_chart_replica" not in html
    # Requested sections do appear.
    assert "run_kpi_cards" in html


@needs_fixture
def test_unknown_sections_recorded_as_notes(fixture_session):
    result = build_candidate_report(
        fixture_session, "F_001",
        sections=["run_kpi_cards", "made_up_section"],
    )
    assert result.sections_rendered == ["run_kpi_cards"]
    assert any("Unknown section" in n for n in result.notes)


@needs_fixture
def test_missing_candidate_raises(fixture_session):
    with pytest.raises(CandidateReportError):
        build_candidate_report(fixture_session, "F_999")


@needs_fixture
def test_batch_builds_all_finalists(fixture_session):
    result = build_all_candidate_reports(fixture_session)
    assert result.session_id == FIXTURE_SESSION
    # The real fixture has 8 finalists.
    assert len(result.per_candidate) == 8
    for r in result.per_candidate:
        assert Path(r.html_path).exists()


@needs_fixture
def test_list_existing_after_batch(fixture_session):
    build_all_candidate_reports(fixture_session)
    existing = list_existing_candidate_reports(fixture_session)
    assert "F_001" in existing
    assert Path(existing["F_001"]).suffix == ".html"


@needs_fixture
def test_purge_existing_removes_orphaned_html(fixture_session):
    out_dir = fixture_session.directory / "deployment_package" / PER_CANDIDATE_REPORTS_DIRNAME
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "OLD_orphan.html").write_text("legacy", encoding="utf-8")
    build_all_candidate_reports(fixture_session, purge_existing=True)
    # Orphan should be gone; F_001 should be present.
    assert not (out_dir / "OLD_orphan.html").exists()
    assert (out_dir / "F_001.html").exists()


def test_batch_with_no_results_returns_empty(tmp_path: Path):
    opt_session.set_storage_root(tmp_path / "sessions")
    try:
        session = opt_session.create_session(label="empty", strategy_id="X",
                                             seed_template_path="", instrument="NQ 06-26")
        result = build_all_candidate_reports(session)
        assert result.per_candidate == []
        assert any("nothing to render" in n.lower() for n in result.notes)
    finally:
        opt_session.set_storage_root(None)
