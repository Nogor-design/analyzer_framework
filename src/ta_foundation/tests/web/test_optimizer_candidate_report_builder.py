from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from ta_foundation.web import app as web_app
from ta_foundation.web import optimizer_session as opt_session
from ta_foundation.web.optimizer_candidate_report import (
    DEFAULT_FINALIST_SECTIONS,
    group_sections_by_bucket,
)
from ta_foundation.reports.html.registry import SECTION_REGISTRY


FIXTURE_SESSION = "opt_5bab6a5ee1ea"
FIXTURE_ROOT = Path(".ta_artifacts/web_optimizer/sessions") / FIXTURE_SESSION


def _have_fixture() -> bool:
    return (FIXTURE_ROOT / "session.json").exists()


needs_fixture = pytest.mark.skipif(
    not _have_fixture(),
    reason=f"requires real session fixture at {FIXTURE_ROOT}",
)


@pytest.fixture
def client(tmp_path: Path):
    if not _have_fixture():
        pytest.skip(f"no fixture at {FIXTURE_ROOT}")
    sessions_root = tmp_path / "sessions"
    sessions_root.mkdir()
    shutil.copytree(FIXTURE_ROOT, sessions_root / FIXTURE_SESSION)
    opt_session.set_storage_root(sessions_root)
    app = web_app.create_app()
    try:
        with app.test_client() as test_client:
            yield test_client, sessions_root / FIXTURE_SESSION
    finally:
        opt_session.set_storage_root(None)


def test_section_buckets_include_every_registered_section_once():
    buckets = group_sections_by_bucket()
    section_ids = [
        section["id"]
        for bucket in buckets
        for section in bucket["sections"]
    ]
    assert len(buckets) == 3
    assert sorted(section_ids) == sorted(SECTION_REGISTRY.keys())
    assert len(section_ids) == len(set(section_ids))
    for section_id in DEFAULT_FINALIST_SECTIONS:
        assert section_id in section_ids


@needs_fixture
def test_report_builder_page_lists_sections(client):
    test_client, _session_dir = client
    res = test_client.get(
        f"/optimizer/sessions/{FIXTURE_SESSION}/candidates/F_001/report-builder"
    )
    body = res.get_data(as_text=True)

    assert res.status_code == 200
    assert "Rebuild this candidate's report" in body
    assert "run_kpi_cards" in body
    assert "pattern_engine_" in body


@needs_fixture
def test_report_builder_post_renders_only_selected_sections(client):
    test_client, session_dir = client
    res = test_client.post(
        f"/api/optimizer/sessions/{FIXTURE_SESSION}/candidates/F_001/report-builder",
        json={"sections": ["run_kpi_cards"]},
    )
    payload = res.get_json()

    assert res.status_code == 200
    assert payload["result"]["sections_rendered"] == ["run_kpi_cards"]

    html_path = (
        session_dir
        / "deployment_package"
        / "per_candidate_reports"
        / "F_001.html"
    )
    html = html_path.read_text(encoding="utf-8")
    assert "run_kpi_cards" in html
    assert "run_settings_table" not in html
    assert "analysis_chart_replica" not in html
