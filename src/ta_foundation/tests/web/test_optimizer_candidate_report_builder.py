from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from ta_foundation.web import app as web_app
from ta_foundation.web import optimizer_session as opt_session
from ta_foundation.web.optimizer_candidate_report import (
    DEFAULT_FINALIST_SECTIONS,
    DEFAULT_SESSION_CANDIDATE_SECTIONS,
    group_sections_by_bucket,
)
from ta_foundation.web.optimizer_report_config import (
    final_report_config_path,
    save_final_report_config,
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


def test_section_buckets_can_use_session_report_defaults():
    buckets = group_sections_by_bucket(default_sections=DEFAULT_SESSION_CANDIDATE_SECTIONS)
    checked = {
        section["id"]
        for bucket in buckets
        for section in bucket["sections"]
        if section["checked"]
    }

    assert checked == set(DEFAULT_SESSION_CANDIDATE_SECTIONS)


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


@needs_fixture
def test_candidate_report_route_serves_generated_html(client):
    test_client, _session_dir = client
    build = test_client.post(
        f"/api/optimizer/sessions/{FIXTURE_SESSION}/candidates/F_008/report-builder",
        json={"sections": ["run_kpi_cards"]},
    )
    assert build.status_code == 200

    res = test_client.get(
        f"/optimizer/sessions/{FIXTURE_SESSION}/candidates/F_008/report"
    )

    assert res.status_code == 200
    assert res.mimetype == "text/html"
    assert "run_kpi_cards" in res.get_data(as_text=True)


@needs_fixture
def test_session_candidate_report_route_serves_generated_html(client):
    test_client, _session_dir = client
    build = test_client.post(
        f"/api/optimizer/sessions/{FIXTURE_SESSION}/candidate-session-report",
        json={},
    )
    payload = build.get_json()
    assert build.status_code == 200
    assert payload["result"]["package_count"] == 8

    res = test_client.get(f"/optimizer/sessions/{FIXTURE_SESSION}/candidate-report")

    assert res.status_code == 200
    assert res.mimetype == "text/html"
    body = res.get_data(as_text=True)
    assert "comparison_overview" in body
    assert "F_001" in body


@needs_fixture
def test_final_report_builder_page_lists_saved_sections(client):
    test_client, session_dir = client
    session = opt_session.get_session(FIXTURE_SESSION)
    assert session is not None
    save_final_report_config(session, {"sections": ["run_kpi_cards"]})

    res = test_client.get(f"/optimizer/sessions/{FIXTURE_SESSION}/final-report-builder")
    body = res.get_data(as_text=True)

    assert res.status_code == 200
    assert "Final all-template report sections" in body
    assert "run_kpi_cards" in body
    assert "Save and rebuild final report" in body
    assert (session_dir / "deployment_package" / "report_configs" / "final_report_config.json").exists()


@needs_fixture
def test_final_report_builder_post_saves_and_uses_selected_sections(client):
    test_client, session_dir = client
    res = test_client.post(
        f"/api/optimizer/sessions/{FIXTURE_SESSION}/candidate-session-report",
        json={"sections": ["run_kpi_cards"], "save": True},
    )
    payload = res.get_json()

    assert res.status_code == 200
    assert payload["result"]["sections_rendered"] == ["run_kpi_cards"]
    assert payload["config"]["sections"] == [{"id": "run_kpi_cards"}]
    assert final_report_config_path(opt_session.get_session(FIXTURE_SESSION)).exists()

    html = (
        session_dir
        / "deployment_package"
        / "session_candidate_report.html"
    ).read_text(encoding="utf-8")
    assert "run_kpi_cards" in html
    assert "comparison_overview" not in html


@needs_fixture
def test_selected_final_report_uses_only_requested_run_ids(client):
    test_client, session_dir = client
    res = test_client.post(
        f"/api/optimizer/sessions/{FIXTURE_SESSION}/candidate-session-report",
        json={"sections": ["run_kpi_cards"], "save": False, "run_ids": ["F_001", "F_002"]},
    )
    payload = res.get_json()

    assert res.status_code == 200
    assert payload["report_url"] == f"/optimizer/sessions/{FIXTURE_SESSION}/candidate-report-selected"
    assert payload["result"]["package_count"] == 2
    assert payload["result"]["run_ids"] == ["F_001", "F_002"]

    html_path = session_dir / "deployment_package" / "selected_candidate_report.html"
    html = html_path.read_text(encoding="utf-8")
    assert "F_001" in html
    assert "F_002" in html
    assert "F_008" not in html

    served = test_client.get(f"/optimizer/sessions/{FIXTURE_SESSION}/candidate-report-selected")
    assert served.status_code == 200
    assert served.mimetype == "text/html"
