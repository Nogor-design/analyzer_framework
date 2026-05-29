from __future__ import annotations

import json
import zipfile
from io import BytesIO
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from ta_foundation.web import app as web_app
from ta_foundation.web import optimizer_session as opt_session
from ta_foundation.web.optimizer_candidate_report import _find_template_path_for_run_id
from ta_foundation.web.optimizer_decision_dashboard import build_decision_dashboard
from ta_foundation.web.optimizer_final_templates import (
    final_template_export_name,
    final_renamed_index_path,
    list_active_final_templates,
    rename_final_templates,
)


@pytest.fixture(autouse=True)
def isolate_storage(tmp_path: Path):
    opt_session.set_storage_root(tmp_path / "sessions")
    yield
    opt_session.set_storage_root(None)


def _install_fake_template_naming(monkeypatch: pytest.MonkeyPatch, name: str = "CoilApolloInfernoL"):
    fake = SimpleNamespace(
        analyze_template=lambda _path: SimpleNamespace(
            compact_name=name,
            output_file_name=f"{name}.xml",
            phase="Coil",
            ma_name="Apollo",
            descriptor="Inferno",
            direction="L",
        )
    )
    monkeypatch.setitem(sys.modules, "template_naming", fake)


def _make_session(tmp_path: Path):
    session = opt_session.create_session(
        label="final templates",
        strategy_id="FakeStrategy",
        seed_template_path=str(tmp_path / "seed.xml"),
        instrument="NQ 06-26",
    )
    template_dir = (
        session.directory
        / "deployment_package"
        / "final_backtest_handoff"
        / "named_backtest_templates"
        / "breakout"
    )
    template_dir.mkdir(parents=True)
    (template_dir / "01_Breakout_PantheonMasterBotV01TesterV2.xml").write_text(
        "<Strategy><Name>one</Name></Strategy>",
        encoding="utf-8",
    )
    return session


def _write_review(session):
    pkg = session.directory / "deployment_package"
    review_dir = pkg / "final_backtest_handoff" / "final_backtest_review"
    review_dir.mkdir(parents=True, exist_ok=True)
    (pkg / "manifest.json").write_text(
        json.dumps({"decision_state": "candidate_ready_for_operator_review"}),
        encoding="utf-8",
    )
    (review_dir / "evaluated_candidates.json").write_text(
        json.dumps({"rows": [{"run_id": "F_001", "score": 10.0, "trades": 12}]}),
        encoding="utf-8",
    )
    (review_dir / "recommendations.json").write_text(
        json.dumps({"recommendations": [], "rejected": []}),
        encoding="utf-8",
    )


def test_rename_final_templates_keeps_run_id_and_index(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    _install_fake_template_naming(monkeypatch)
    session = _make_session(tmp_path)

    result = rename_final_templates(session)

    assert result.template_count == 1
    renamed = Path(result.templates[0].renamed_path)
    assert renamed.name == "F_001__CoilApolloInfernoL-NQ.xml"
    assert renamed.read_text(encoding="utf-8").startswith("<Strategy>")
    assert final_renamed_index_path(session).exists()
    assert list_active_final_templates(session) == [renamed]
    assert _find_template_path_for_run_id(session, "F_001") == renamed


def test_dashboard_exposes_final_template_links(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    _install_fake_template_naming(monkeypatch)
    session = _make_session(tmp_path)
    _write_review(session)
    rename_final_templates(session)

    dash = build_decision_dashboard(session)

    assert dash.template_links["count"] == 1
    assert dash.template_links["renamed"] is True
    assert dash.template_links["list_url"].endswith("/templates/final")
    assert dash.template_links["download_url"].endswith("/templates/final.zip")


def test_final_template_routes_rename_list_and_zip(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    _install_fake_template_naming(monkeypatch)
    session = _make_session(tmp_path)
    app = web_app.create_app()

    with app.test_client() as client:
        rename_res = client.post(
            f"/api/optimizer/sessions/{session.id}/final-templates/rename",
            json={},
        )
        assert rename_res.status_code == 200
        assert rename_res.get_json()["result"]["template_count"] == 1

        list_res = client.get(f"/optimizer/sessions/{session.id}/templates/final")
        assert list_res.status_code == 200
        assert "F_001__CoilApolloInfernoL-NQ.xml" in list_res.get_data(as_text=True)

        zip_res = client.get(f"/optimizer/sessions/{session.id}/templates/final.zip")
        assert zip_res.status_code == 200
        assert zip_res.mimetype == "application/zip"
        with zipfile.ZipFile(BytesIO(zip_res.data)) as zf:
            assert zf.namelist() == ["F_001.xml"]


def test_final_template_list_route_reports_empty_folder(tmp_path: Path):
    session = opt_session.create_session(
        label="empty final templates",
        strategy_id="FakeStrategy",
        seed_template_path=str(tmp_path / "seed.xml"),
        instrument="NQ 06-26",
    )
    app = web_app.create_app()

    with app.test_client() as client:
        res = client.get(f"/optimizer/sessions/{session.id}/templates/final")

    assert res.status_code == 200
    body = res.get_data(as_text=True)
    assert "Active template folder:" in body
    assert "Template count: 0" in body
    assert "No final template XML files found yet" in body


def test_final_template_export_name_is_short_and_keeps_time_bucket(tmp_path: Path):
    path = tmp_path / (
        "F_001__01_Recipe_stage_2_stage_2_parent_stage_1_"
        "stage_1_starttimeh_00_opt_maxprofitfactor_row00002-NQ.xml"
    )
    path.write_text(
        "<StrategyTemplate><Strategy><FakeStrategy><StartTimeH>16</StartTimeH>"
        "</FakeStrategy></Strategy></StrategyTemplate>",
        encoding="utf-8",
    )

    assert final_template_export_name(path) == "F_001_StartTimeH_16.xml"
