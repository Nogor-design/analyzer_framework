from __future__ import annotations

import json
from pathlib import Path
import zipfile

from ta_foundation.web import optimizer_session
from ta_foundation.web.weekly_report_publish import (
    load_published_site_manifest,
    publish_weekly_session,
    set_published_site_root,
)


def _write_weekly_recipe(session_dir: Path) -> None:
    (session_dir / "recipe.json").write_text(
        json.dumps(
            {
                "recipe_id": "rec_weekly_coverage",
                "stages": [{"selection": {"mode": "coverage_matrix_sequence"}}],
            }
        ),
        encoding="utf-8",
    )


def test_publish_weekly_session_builds_static_archive(tmp_path: Path):
    optimizer_session.set_storage_root(tmp_path / "sessions")
    set_published_site_root(tmp_path / "published-site")
    session = optimizer_session.create_session(
        label="Week 27",
        strategy_id="PantheonMasterBotV01TesterV2",
        seed_template_path="seed.xml",
        instrument="NQ 06-26",
        market_suffix="NQ",
    )
    session.update(oos_from_date="2026-06-24", oos_to_date="2026-07-01")
    _write_weekly_recipe(session.directory)

    pkg = session.directory / "deployment_package"
    weekly_pack = pkg / "ExampleWeeklyReport"
    weekly_pack.mkdir(parents=True)
    (weekly_pack / "index.html").write_text(
        (
            '<html><body>'
            f'<a href="/optimizer/sessions/{session.id}/weekly-reports.zip">zip</a>'
            f'<a href="/optimizer/sessions/{session.id}/weekly-reports/files/Weekly-Prop-Dashboard.html">report</a>'
            '</body></html>'
        ),
        encoding="utf-8",
    )
    (pkg / "ExampleWeeklyReport.zip").write_text("zip", encoding="utf-8")
    (pkg / "session_candidate_report.html").write_text("<html>standard</html>", encoding="utf-8")
    (pkg / "session_candidate_report_assets").mkdir()
    (pkg / "session_candidate_report_assets" / "img.png").write_text("asset", encoding="utf-8")
    coverage_dir = pkg / "weekly_coverage_package" / "reports"
    coverage_dir.mkdir(parents=True)
    (coverage_dir / "operationally_diverse_weekly_coverage_package_report.html").write_text(
        "<html>coverage</html>",
        encoding="utf-8",
    )
    (coverage_dir / "weekly_strategy_daily_update_report.html").write_text(
        "<html>daily</html>",
        encoding="utf-8",
    )
    (pkg / "weekly_coverage_package.zip").write_text("zip2", encoding="utf-8")
    review_dir = pkg / "final_backtest_handoff" / "final_backtest_review"
    review_dir.mkdir(parents=True)
    (review_dir / "review_summary.json").write_text(
        json.dumps({"counts": {"candidates": 98, "recommendations": 8}}),
        encoding="utf-8",
    )

    result = publish_weekly_session(session)

    manifest = load_published_site_manifest()
    root_html = Path(result.site_root, "index.html").read_text(encoding="utf-8")
    assert result.report_count >= 4
    assert Path(result.site_root, "index.html").exists()
    assert Path(result.week_dir, "index.html").exists()
    assert Path(result.week_dir, "weekly-report-pack", "index.html").exists()
    assert Path(result.week_dir, "standard-report.html").exists()
    assert Path(result.week_dir, "standard-report_assets", "img.png").exists()
    pack_html = Path(result.week_dir, "weekly-report-pack", "index.html").read_text(encoding="utf-8")
    assert f'/weeks/{result.slug}/downloads/ExampleWeeklyReport.zip' in pack_html
    assert f'href="/weeks/{result.slug}/weekly-report-pack/Weekly-Prop-Dashboard.html"' in pack_html
    assert f"/optimizer/sessions/{session.id}/weekly-reports/files/" not in pack_html
    expected_week_prefix = f"weeks/{result.slug}"
    assert "weeks/" in root_html
    assert expected_week_prefix in root_html
    assert f"{expected_week_prefix}/weekly-report-pack/index.html" in root_html
    assert manifest["entries"]
    assert manifest["entries"][0]["session_id"] == session.id
    assert manifest["entries"][0]["final_template_count"] == 98
    assert manifest["entries"][0]["final_recommendation_count"] == 8

    optimizer_session.set_storage_root(None)
    set_published_site_root(None)


def test_publish_weekly_session_uses_zip_when_pack_folder_is_partial(tmp_path: Path):
    optimizer_session.set_storage_root(tmp_path / "sessions")
    set_published_site_root(tmp_path / "published-site")
    session = optimizer_session.create_session(
        label="Week 27",
        strategy_id="PantheonMasterBotV01TesterV2",
        seed_template_path="seed.xml",
        instrument="NQ 06-26",
        market_suffix="NQ",
    )
    session.update(oos_from_date="2026-06-24", oos_to_date="2026-07-01")
    _write_weekly_recipe(session.directory)

    pkg = session.directory / "deployment_package"
    weekly_pack = pkg / "ExampleWeeklyReport"
    weekly_pack.mkdir(parents=True)
    (weekly_pack / "Daily-Winner-Insight.html").write_text("<html>partial folder</html>", encoding="utf-8")
    with zipfile.ZipFile(pkg / "ExampleWeeklyReport.zip", "w") as zf:
        zf.writestr("ExampleWeeklyReport/index.html", "<html>zip weekly pack</html>")
        zf.writestr("ExampleWeeklyReport/Weekly-Prop-Dashboard.html", "<html>weekly prop</html>")
    (pkg / "session_candidate_report.html").write_text("<html>standard</html>", encoding="utf-8")
    coverage_dir = pkg / "weekly_coverage_package" / "reports"
    coverage_dir.mkdir(parents=True)
    (coverage_dir / "operationally_diverse_weekly_coverage_package_report.html").write_text(
        "<html>coverage</html>",
        encoding="utf-8",
    )

    result = publish_weekly_session(session)

    assert Path(result.week_dir, "weekly-report-pack", "index.html").exists()
    assert "restored from ExampleWeeklyReport.zip" in " ".join(result.notes)

    optimizer_session.set_storage_root(None)
    set_published_site_root(None)
