from __future__ import annotations

"""Build an ExampleWeeklyReport-style folder for optimizer final templates.

This deliberately does not rebuild the weekly coverage package. It takes the
templates already generated for a finished optimizer session, resolves their
matching final-backtest run ids, and writes separate report files plus a
templates folder in one handoff directory.
"""

import csv
import json
import shutil
import zipfile
from dataclasses import asdict, dataclass, field
from html import escape
from pathlib import Path
from typing import Any

from ta_foundation.web.optimizer_candidate_report import (
    DAILY_WINNER_SECTION,
    EXEC_PROFILE_SECTION,
    WEEKLY_PROP_SECTION,
    build_session_candidate_report,
    load_session_candidate_ingest,
    _section_with_overrides,
)
from ta_foundation.web.optimizer_final_templates import (
    FinalTemplateError,
    final_renamed_index_path,
    final_named_templates_dir,
    final_template_export_name_for_session,
    list_active_final_templates,
    list_active_final_templates_filtered,
    rename_final_templates,
)
from ta_foundation.web.optimizer_session import OptimizerSession


REPORT_PACK_DIRNAME = "ExampleWeeklyReport"
TEMPLATES_DIRNAME = "templates"
ZIP_FILENAME = "ExampleWeeklyReport.zip"


WEEKLY_LEADERBOARD_CARDS_SECTION: dict[str, Any] = {
    "id": "weekly_leaderboard_cards",
    "title": "Weekly Prop Dashboard",
    "options": {
        "top_n": 299,
        "starting_balance": 50000,
        "trailing_dd": 2500,
        "baseline_mode": "fresh_week",
        "show_card_image": True,
        "show_chart": False,
        "show_debug_table": False,
        "warn_buffer": 500,
        "compact_noimg": True,
        "bot_columns": 1,
    },
}

FAST_EXEC_PROFILE_SECTION: dict[str, Any] = _section_with_overrides(
    EXEC_PROFILE_SECTION,
    {
        # Weekly published packs favor browser-friendly speed and weight over
        # regenerating 98 per-run detail chart images on every build.
        "show_detail_charts": False,
    },
)

WEEKLY_SCOREBOARD_BUNDLES_SECTION: dict[str, Any] = {
    "id": "daily_scoreboard",
    "title": "Daily Scoreboard + Best Daily Pairs",
    "options": {
        "show_individual_equity": True,
        "include_summary_table": True,
        "include_all_bot_charts": False,
        "combo_sets": [
            {
                "name": "Best Daily Pairs",
                "mode": "top",
                "k": 2,
                "top_n": 6,
                "max_render": 1,
                "beam_width": 120,
            },
        ],
    },
}


class WeeklyReportPackError(Exception):
    pass


@dataclass(frozen=True)
class WeeklyReportArtifact:
    name: str
    path: str
    url: str
    status: str = "built"
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class WeeklyReportPackResult:
    session_id: str
    report_dir: str
    report_url: str
    zip_path: str
    zip_url: str
    template_count: int
    run_ids: list[str]
    reports: list[WeeklyReportArtifact]
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "report_dir": self.report_dir,
            "report_url": self.report_url,
            "zip_path": self.zip_path,
            "zip_url": self.zip_url,
            "template_count": self.template_count,
            "run_ids": list(self.run_ids),
            "reports": [report.to_dict() for report in self.reports],
            "notes": list(self.notes),
        }


def weekly_report_pack_dir(session: OptimizerSession) -> Path:
    return session.directory / "deployment_package" / REPORT_PACK_DIRNAME


def weekly_report_pack_zip_path(session: OptimizerSession) -> Path:
    return weekly_report_pack_dir(session).parent / ZIP_FILENAME


def weekly_report_pack_index_path(session: OptimizerSession) -> Path:
    return weekly_report_pack_dir(session) / "index.html"


def build_weekly_report_pack(
    session: OptimizerSession,
    *,
    run_ids: list[str] | None = None,
    include_all_active_templates: bool = False,
    report_asset_mode: str = "embedded",
) -> WeeklyReportPackResult:
    """Build separate weekly reports for the selected generated templates.

    Selection order:
    1. Explicit ``run_ids`` from the caller.
    2. Semantically renamed active generated final templates.
    3. Existing pruned/weekly selections, only as a fallback for older sessions.
    """
    naming_note = _ensure_final_templates_named(session)
    selected_run_ids, source_note = _resolve_report_run_ids(
        session,
        explicit=run_ids,
        include_all_active_templates=include_all_active_templates,
    )
    if not selected_run_ids:
        raise WeeklyReportPackError(
            "No template run ids resolved. Generate final templates first, "
            "or pass explicit run_ids."
        )

    root = weekly_report_pack_dir(session)
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)

    copied_templates = _copy_templates(session, selected_run_ids, root / TEMPLATES_DIRNAME)
    reports: list[WeeklyReportArtifact] = []
    shared_ingest = load_session_candidate_ingest(session)

    specs: list[tuple[str, list[str | dict[str, Any]], bool, bool, bool]] = [
        (
            "Daily-Winner-Insight.html",
            [DAILY_WINNER_SECTION],
            True,
            False,
            False,
        ),
        (
            "Executive-Strategy-Profiles.html",
            [FAST_EXEC_PROFILE_SECTION],
            True,
            True,
            False,
        ),
        (
            "Weekly-Prop-Dashboard.html",
            [WEEKLY_PROP_SECTION],
            True,
            True,
            False,
        ),
        (
            "Weekly-Leaderboard-Cards.html",
            [WEEKLY_LEADERBOARD_CARDS_SECTION],
            True,
            True,
            False,
        ),
        (
            "Strategy-Momentum-Board.html",
            [
                {
                    "id": "strategy_momentum_board",
                    "title": "Strategy Momentum Board",
                    "options": {"top_n": 40, "strip_days": 5},
                }
            ],
            True,
            False,
            False,
        ),
        (
            "Strategy-Session-Momentum-Board.html",
            [
                {
                    "id": "strategy_session_momentum_board",
                    "title": "Strategy Session Momentum Board",
                    "options": {
                        "overall_top_n": 5,
                        "top_n_per_session": 5,
                        "strip_days": 5,
                    },
                }
            ],
            True,
            False,
            False,
        ),
        (
            "Daily-Scoreboard-Bundles.html",
            [WEEKLY_SCOREBOARD_BUNDLES_SECTION],
            False,
            False,
            False,
        ),
        (
            "Runnable-Bundle-Basket.html",
            [
                {
                    "id": "final_template_bundle_basket",
                    "title": "Runnable Bundle Basket",
                    "options": {
                        "bucket_param": "Start_Time_(HH)",
                        "top_n": 12,
                        "max_per_bucket": 4,
                        "show_chart": True,
                    },
                }
            ],
            True,
            False,
            False,
        ),
    ]

    for filename, sections, dark_shell, needs_enrichment, needs_detail_charts in specs:
        out_path = root / filename
        try:
            result = build_session_candidate_report(
                session,
                sections=sections,
                output_path=out_path,
                dark_shell=dark_shell,
                run_ids=selected_run_ids,
                enrich_packages=needs_enrichment,
                enrich_detail_charts=needs_detail_charts,
                report_asset_mode=report_asset_mode,
                preloaded_ingest=shared_ingest,
            )
        except Exception as exc:
            reports.append(
                WeeklyReportArtifact(
                    name=filename,
                    path=str(out_path.resolve()),
                    url=f"/optimizer/sessions/{session.id}/weekly-reports/files/{filename}",
                    status="failed",
                    notes=[str(exc)],
                )
            )
            continue
        reports.append(
            WeeklyReportArtifact(
                name=filename,
                path=str(out_path.resolve()),
                url=f"/optimizer/sessions/{session.id}/weekly-reports/files/{filename}",
                status="built" if result.html_path else "empty",
                notes=list(result.notes),
            )
        )

    _write_manifest(
        root / "manifest.json",
        {
            "schema_version": 1,
            "session_id": session.id,
            "source": source_note,
            "naming": naming_note,
            "template_count": copied_templates,
            "run_ids": selected_run_ids,
            "reports": [report.to_dict() for report in reports],
        },
    )
    _write_index(
        root / "index.html",
        session,
        reports,
        copied_templates,
        selected_run_ids,
        source_note,
        naming_note,
    )
    zip_path = weekly_report_pack_zip_path(session)
    _write_zip(root, zip_path)

    return WeeklyReportPackResult(
        session_id=session.id,
        report_dir=str(root.resolve()),
        report_url=f"/optimizer/sessions/{session.id}/weekly-reports",
        zip_path=str(zip_path.resolve()),
        zip_url=f"/optimizer/sessions/{session.id}/weekly-reports.zip",
        template_count=copied_templates,
        run_ids=selected_run_ids,
        reports=reports,
        notes=[naming_note, source_note],
    )


def _ensure_final_templates_named(session: OptimizerSession) -> str:
    source_ids = _source_final_template_run_ids(session)
    indexed_ids = set(_run_ids_from_renamed_index(session))
    if source_ids and source_ids.issubset(indexed_ids):
        return "Used existing semantic renamed final templates."
    try:
        result = rename_final_templates(session)
    except FinalTemplateError as exc:
        if source_ids:
            return (
                "Template semantic renaming was unavailable; falling back to the existing "
                f"named final templates. Reason: {exc}"
            )
        raise WeeklyReportPackError(f"Final template naming failed: {exc}") from exc
    return f"Renamed {result.template_count} final templates before report build."


def _source_final_template_run_ids(session: OptimizerSession) -> set[str]:
    out: set[str] = set()
    source_dir = final_named_templates_dir(session)
    if not source_dir.exists():
        return out
    for path in source_dir.rglob("*.xml"):
        run_id = _run_id_from_name(path)
        if run_id:
            out.add(run_id)
    return out


def _resolve_report_run_ids(
    session: OptimizerSession,
    *,
    explicit: list[str] | None,
    include_all_active_templates: bool,
) -> tuple[list[str], str]:
    del include_all_active_templates
    if explicit:
        return _clean_run_ids(explicit), "Used explicit selected run ids."

    ids = _run_ids_from_renamed_index(session)
    if not ids:
        ids = []
        for path in list_active_final_templates(session):
            run_id = _run_id_from_name(path)
            if run_id:
                ids.append(run_id)
    if ids:
        return _clean_run_ids(ids), "Used active generated final templates."

    pruned = (
        session.directory
        / "deployment_package"
        / "weekly_coverage_package"
        / "pruned_final_bundle"
        / "pruned_bundle_manifest.csv"
    )
    if pruned.exists():
        rows = _read_csv(pruned)
        ids = _clean_run_ids([str(row.get("run_id") or "") for row in rows])
        if ids:
            return ids, f"Used pruned final bundle manifest: {pruned}"

    weekly_data = (
        session.directory
        / "deployment_package"
        / "weekly_coverage_package"
        / "data"
    )
    for filename in (
        "operationally_diverse_validated_selection.csv",
        "best_effort_fallback_selection.csv",
    ):
        path = weekly_data / filename
        if path.exists():
            rows = _read_csv(path)
            ids = _clean_run_ids([str(row.get("run_id") or "") for row in rows])
            if ids:
                return ids, f"Used fallback weekly report selection: {path}"

    return [], "No active generated final templates or fallback weekly/pruned selection found."


def _copy_templates(session: OptimizerSession, run_ids: list[str], out_dir: Path) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    copied = 0
    for template in list_active_final_templates_filtered(session, set(run_ids)):
        dest_name = final_template_export_name_for_session(session, template)
        shutil.copy2(template, out_dir / dest_name)
        copied += 1
    return copied


def _run_ids_from_renamed_index(session: OptimizerSession) -> list[str]:
    path = final_renamed_index_path(session)
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    templates = payload.get("templates") if isinstance(payload, dict) else None
    if not isinstance(templates, dict):
        return []
    return [str(run_id) for run_id in templates.keys()]


def _write_index(
    path: Path,
    session: OptimizerSession,
    reports: list[WeeklyReportArtifact],
    template_count: int,
    run_ids: list[str],
    source_note: str,
    naming_note: str,
) -> None:
    rows = []
    for report in reports:
        status = report.status
        note = _summarize_report_notes(report.notes, len(run_ids))
        rows.append(
            "<tr>"
            f"<td><a href=\"/optimizer/sessions/{escape(session.id)}/weekly-reports/files/{escape(report.name)}\">{escape(report.name)}</a></td>"
            f"<td>{escape(status)}</td>"
            f"<td>{escape(note)}</td>"
            "</tr>"
        )
    html = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Example Weekly Report - {session.id}</title>
  <style>
    body{{font-family:Segoe UI,Arial,sans-serif;background:#0b1020;color:#e5e7eb;margin:24px}}
    a{{color:#fbbf24}} .muted{{color:#94a3b8}} table{{border-collapse:collapse;width:100%;margin-top:18px}}
    th,td{{border-bottom:1px solid #334155;padding:10px;text-align:left;vertical-align:top}}
    th{{color:#cbd5e1;background:#111827}} code{{color:#fde68a}}
  </style>
</head>
<body>
  <h1>Example Weekly Report</h1>
  <p class="muted">Session <code>{session.id}</code>. {template_count} template XML file(s), {len(run_ids)} report run id(s).</p>
  <p class="muted">{escape(naming_note)}</p>
  <p class="muted">{escape(source_note)}</p>
  <p><a href="/optimizer/sessions/{session.id}/weekly-reports.zip">Download ExampleWeeklyReport.zip</a></p>
  <table>
    <thead><tr><th>Report</th><th>Status</th><th>Notes</th></tr></thead>
    <tbody>{''.join(rows)}</tbody>
  </table>
</body>
</html>
"""
    path.write_text(html, encoding="utf-8")


def _write_manifest(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _summarize_report_notes(notes: list[str], run_count: int) -> str:
    out: list[str] = []
    for note in notes:
        if note.startswith("Filtered final report to selected run id(s):"):
            out.append(f"Filtered final report to {run_count} selected template(s).")
            continue
        out.append(note)
        if len(out) >= 2:
            break
    return "; ".join(out)


def _write_zip(folder: Path, zip_path: Path) -> None:
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(folder.parent))


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _clean_run_ids(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        rid = str(value or "").strip()
        if not rid or rid in seen:
            continue
        seen.add(rid)
        out.append(rid)
    return sorted(out, key=_run_sort_key)


def _run_sort_key(run_id: str) -> tuple[int, str]:
    try:
        return (int(str(run_id).replace("F_", "").replace("P_", "")), str(run_id))
    except ValueError:
        return (10**9, str(run_id))


def _run_id_from_name(path: Path) -> str | None:
    import re

    match = re.search(r"(?:^|__)F_(\d{3})(?:__|_|$)", path.stem)
    if match:
        return f"F_{int(match.group(1)):03d}"
    match = re.match(r"^F_(\d{3})(?:_|$)", path.stem)
    if match:
        return f"F_{int(match.group(1)):03d}"
    head = path.stem.split("_", 1)[0]
    try:
        return f"F_{int(head):03d}"
    except ValueError:
        pass
    return None
