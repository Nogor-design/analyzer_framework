from __future__ import annotations

import base64
import json
from pathlib import Path

from ta_foundation.web import optimizer_session
from ta_foundation.web.report_assets import finalize_report_html, report_asset_dir
from ta_foundation.web.weekly_coverage_app import create_app


_ONE_PIXEL_PNG = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8"
    "/w8AAgMBgJ2mWQAAAABJRU5ErkJggg=="
)


def test_finalize_report_html_externalizes_embedded_images(tmp_path: Path):
    html_path = tmp_path / "report.html"
    html = (
        f'<html><body><img src="data:image/png;base64,{_ONE_PIXEL_PNG}">'
        f'<img src="data:image/png;base64,{_ONE_PIXEL_PNG}"></body></html>'
    )

    updated, notes = finalize_report_html(
        html,
        html_path,
        asset_mode="external",
    )

    asset_dir = report_asset_dir(html_path)
    files = list(asset_dir.glob("*"))
    assert len(files) == 1
    assert files[0].read_bytes() == base64.b64decode(_ONE_PIXEL_PNG)
    assert f'{asset_dir.name}/' in updated
    assert "data:image/png;base64" not in updated
    assert notes


def test_finalize_report_html_embedded_clears_stale_asset_dir(tmp_path: Path):
    html_path = tmp_path / "report.html"
    asset_dir = report_asset_dir(html_path)
    asset_dir.mkdir()
    (asset_dir / "old.png").write_bytes(b"old")

    updated, notes = finalize_report_html(
        "<html><body>ok</body></html>",
        html_path,
        asset_mode="embedded",
    )

    assert updated == "<html><body>ok</body></html>"
    assert notes == []
    assert not asset_dir.exists()


def test_weekly_public_reports_page_and_asset_route(tmp_path: Path):
    optimizer_session.set_storage_root(tmp_path / "sessions")
    try:
        session = optimizer_session.create_session(
            label="Weekly Publish",
            strategy_id="PantheonMasterBotV01TesterV2",
            seed_template_path="weekly.xml",
            instrument="NQ 06-26",
            market_suffix="NQ",
        )
        (session.directory / "recipe.json").write_text(
            json.dumps(
                {
                    "recipe_id": "rec_weekly_publish",
                    "stages": [{"selection": {"mode": "coverage_matrix_sequence"}}],
                }
            ),
            encoding="utf-8",
        )
        report_path = session.directory / "deployment_package" / "session_candidate_report.html"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            '<html><body><img src="session_candidate_report_assets/img-0001-test.png"></body></html>',
            encoding="utf-8",
        )
        asset_dir = report_path.parent / "session_candidate_report_assets"
        asset_dir.mkdir(parents=True, exist_ok=True)
        (asset_dir / "img-0001-test.png").write_bytes(base64.b64decode(_ONE_PIXEL_PNG))

        pack_root = session.directory / "deployment_package" / "ExampleWeeklyReport"
        pack_root.mkdir(parents=True, exist_ok=True)
        (pack_root / "index.html").write_text("<html>pack</html>", encoding="utf-8")
        (pack_root / "Weekly-Prop-Dashboard.html").write_text("<html>weekly pack report</html>", encoding="utf-8")
        pack_asset_dir = pack_root / "Weekly-Prop-Dashboard_assets"
        pack_asset_dir.mkdir()
        (pack_asset_dir / "img.png").write_bytes(base64.b64decode(_ONE_PIXEL_PNG))

        app = create_app()
        client = app.test_client()

        reports_res = client.get("/reports")
        assert reports_res.status_code == 200
        page = reports_res.get_data(as_text=True)
        assert "Published Weekly Reports" in page
        assert "Weekly Publish" in page
        assert f"/optimizer/sessions/{session.id}/candidate-report" in page

        asset_res = client.get(
            f"/optimizer/sessions/{session.id}/candidate-report_assets/img-0001-test.png"
        )
        assert asset_res.status_code == 200
        assert asset_res.data == base64.b64decode(_ONE_PIXEL_PNG)

        pack_asset_res = client.get(
            f"/optimizer/sessions/{session.id}/weekly-reports/files/Weekly-Prop-Dashboard_assets/img.png"
        )
        assert pack_asset_res.status_code == 200
        assert pack_asset_res.data == base64.b64decode(_ONE_PIXEL_PNG)
    finally:
        optimizer_session.set_storage_root(None)
