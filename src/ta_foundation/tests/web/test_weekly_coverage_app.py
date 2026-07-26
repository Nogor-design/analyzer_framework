import json
import zipfile

from ta_foundation.web import optimizer_session
from ta_foundation.web.weekly_coverage_app import create_app
from ta_foundation.web.weekly_report_publish import set_published_site_root


def test_weekly_app_redirects_recipe_to_weekly():
    app = create_app()
    client = app.test_client()

    res = client.get("/optimizer/recipe")

    assert res.status_code == 302
    assert res.headers["Location"].endswith("/optimizer/weekly-coverage")


def test_weekly_recent_filters_non_weekly_sessions(tmp_path):
    optimizer_session.set_storage_root(tmp_path / "sessions")
    weekly = optimizer_session.create_session(
        label="Weekly",
        strategy_id="PantheonMasterBotV01TesterV2",
        seed_template_path="weekly.xml",
        instrument="NQ 06-26",
        market_suffix="NQ",
    )
    non_weekly = optimizer_session.create_session(
        label="Other",
        strategy_id="PantheonMasterBotV01TesterV2",
        seed_template_path="other.xml",
        instrument="NQ 06-26",
        market_suffix="NQ",
    )
    (weekly.directory / "recipe.json").write_text(
        json.dumps(
            {
                "recipe_id": "rec_weekly_coverage",
                "stages": [
                    {"selection": {"mode": "coverage_matrix_sequence"}},
                ],
            }
        ),
        encoding="utf-8",
    )
    (non_weekly.directory / "recipe.json").write_text(
        json.dumps(
            {
                "recipe_id": "rec_other_flow",
                "stages": [
                    {"selection": {"mode": "plain_rank"}},
                ],
            }
        ),
        encoding="utf-8",
    )

    app = create_app()
    client = app.test_client()

    res = client.get("/api/optimizer/weekly-coverage/recent")

    assert res.status_code == 200
    body = res.get_json()
    assert [row["session_id"] for row in body["sessions"]] == [weekly.id]


def test_reports_route_serves_published_index_when_available(tmp_path):
    optimizer_session.set_storage_root(tmp_path / "sessions")
    set_published_site_root(tmp_path / "published-site")
    site_root = tmp_path / "published-site"
    site_root.mkdir(parents=True)
    (site_root / "index.html").write_text("<html><body>published archive</body></html>", encoding="utf-8")

    app = create_app()
    client = app.test_client()

    res = client.get("/reports")
    assert res.status_code == 302
    assert res.headers["Location"].endswith("/reports/index.html")

    html = client.get("/reports/index.html")
    assert html.status_code == 200
    assert "published archive" in html.get_data(as_text=True)

    set_published_site_root(None)


def test_weekly_reports_zip_route_serves_pack_zip(tmp_path):
    optimizer_session.set_storage_root(tmp_path / "sessions")
    session = optimizer_session.create_session(
        label="Weekly",
        strategy_id="PantheonMasterBotV01TesterV2",
        seed_template_path="weekly.xml",
        instrument="NQ 06-26",
        market_suffix="NQ",
    )
    pkg = session.directory / "deployment_package"
    pkg.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(pkg / "ExampleWeeklyReport.zip", "w") as zf:
        zf.writestr("ExampleWeeklyReport/index.html", "<html>ok</html>")

    app = create_app()
    client = app.test_client()

    res = client.get(f"/optimizer/sessions/{session.id}/weekly-reports.zip")

    assert res.status_code == 200
    assert "ExampleWeeklyReport.zip" in res.headers.get("Content-Disposition", "")

    optimizer_session.set_storage_root(None)
