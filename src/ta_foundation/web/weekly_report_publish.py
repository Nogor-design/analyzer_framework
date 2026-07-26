from __future__ import annotations

"""Publish weekly optimizer reports into a static archive site.

The optimizer and NinjaTrader backtests still run locally. This module turns
the generated HTML artifacts into a static, week-by-week archive folder that
can be previewed through the standalone app or deployed to simple static
hosting.
"""

import json
import shutil
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Any


_PUBLISHED_SITE_ROOT: Path | None = None


class PublishedReportError(Exception):
    pass


@dataclass(frozen=True)
class PublishedReportLink:
    label: str
    href: str
    kind: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PublishedWeekEntry:
    slug: str
    session_id: str
    title: str
    week_end: str
    instrument: str
    strategy_id: str
    session_label: str
    updated_at: str
    published_at: str
    final_template_count: int
    final_recommendation_count: int
    week_page_href: str
    report_links: list[PublishedReportLink] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "report_links": [link.to_dict() for link in self.report_links],
        }


@dataclass(frozen=True)
class PublishWeeklySiteResult:
    session_id: str
    slug: str
    site_root: str
    week_dir: str
    week_page_href: str
    report_count: int
    report_links: list[PublishedReportLink] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "slug": self.slug,
            "site_root": self.site_root,
            "week_dir": self.week_dir,
            "week_page_href": self.week_page_href,
            "report_count": self.report_count,
            "report_links": [link.to_dict() for link in self.report_links],
            "notes": list(self.notes),
        }


def set_published_site_root(path: Path | str | None) -> None:
    global _PUBLISHED_SITE_ROOT
    _PUBLISHED_SITE_ROOT = Path(path).resolve() if path else None


def published_site_root() -> Path:
    if _PUBLISHED_SITE_ROOT is not None:
        return _PUBLISHED_SITE_ROOT
    repo_root = Path(__file__).resolve().parents[3]
    return repo_root / ".ta_artifacts" / "web_optimizer" / "published-site"


def published_site_index_path() -> Path:
    return published_site_root() / "index.html"


def published_site_manifest_path() -> Path:
    return published_site_root() / "site_manifest.json"


def load_published_site_manifest() -> dict[str, Any]:
    path = published_site_manifest_path()
    if not path.exists():
        return {"schema_version": 1, "entries": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"schema_version": 1, "entries": []}
    if not isinstance(payload, dict):
        return {"schema_version": 1, "entries": []}
    entries = payload.get("entries")
    if not isinstance(entries, list):
        payload["entries"] = []
    return payload


def published_entry_for_session(session_id: str) -> dict[str, Any] | None:
    manifest = load_published_site_manifest()
    for entry in manifest.get("entries", []):
        if isinstance(entry, dict) and str(entry.get("session_id") or "") == session_id:
            return entry
    return None


def publish_weekly_session(
    session,
    *,
    title: str | None = None,
    overwrite: bool = True,
) -> PublishWeeklySiteResult:
    from ta_foundation.web.optimizer_candidate_report import session_candidate_report_path
    from ta_foundation.web.optimizer_weekly_coverage_package import (
        weekly_coverage_report_path,
        weekly_coverage_zip_path,
        weekly_daily_update_report_path,
    )
    from ta_foundation.web.optimizer_weekly_report_pack import (
        weekly_report_pack_dir,
        weekly_report_pack_zip_path,
    )

    doc = session.load_document()
    summary = session.summary()
    pack_dir = weekly_report_pack_dir(session)
    pack_zip = weekly_report_pack_zip_path(session)
    pack_index = pack_dir / "index.html"
    if not pack_index.exists() and not pack_zip.exists():
        raise PublishedReportError(
            "Weekly report pack is missing. Build the weekly report pack first so the published site has a primary report."
        )

    site_root = published_site_root()
    site_root.mkdir(parents=True, exist_ok=True)
    slug = _slug_for_session(session)
    week_dir = site_root / "weeks" / slug
    if week_dir.exists():
        if not overwrite:
            raise PublishedReportError(f"Published week already exists: {slug}")
        shutil.rmtree(week_dir)
    week_dir.mkdir(parents=True, exist_ok=True)

    report_links: list[PublishedReportLink] = []
    notes: list[str] = []

    if pack_index.exists():
        _copy_tree(pack_dir, week_dir / "weekly-report-pack")
    else:
        _extract_weekly_pack_zip(pack_zip, week_dir / "weekly-report-pack")
        notes.append("Published weekly report pack was restored from ExampleWeeklyReport.zip because the unpacked folder was incomplete.")
    _rewrite_weekly_pack_index_for_static_site(
        session.id,
        week_dir / "weekly-report-pack",
        week_dir,
    )
    report_links.append(
        PublishedReportLink(
            label="Weekly report pack",
            href=f"weekly-report-pack/index.html",
            kind="weekly_report_pack",
        )
    )

    if pack_zip.exists():
        downloads_dir = week_dir / "downloads"
        downloads_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(pack_zip, downloads_dir / pack_zip.name)
        report_links.append(
            PublishedReportLink(
                label="Download weekly report ZIP",
                href=f"downloads/{pack_zip.name}",
                kind="weekly_report_zip",
            )
        )

    standard_report = session_candidate_report_path(session)
    if standard_report.exists():
        href = _copy_report_with_assets(standard_report, week_dir, "standard-report.html")
        report_links.append(
            PublishedReportLink(label="Standard session report", href=href, kind="standard_report")
        )
    else:
        notes.append("Standard session report was not present at publish time.")

    coverage_report = weekly_coverage_report_path(session)
    if coverage_report.exists():
        href = _copy_report_with_assets(coverage_report, week_dir, "coverage-package-report.html")
        report_links.append(
            PublishedReportLink(label="Coverage package report", href=href, kind="coverage_package_report")
        )
    else:
        notes.append("Coverage package report was not present at publish time.")

    daily_update = weekly_daily_update_report_path(session)
    if daily_update.exists():
        href = _copy_report_with_assets(daily_update, week_dir, "daily-update-report.html")
        report_links.append(
            PublishedReportLink(label="Daily update report", href=href, kind="daily_update_report")
        )
    else:
        notes.append("Daily update report was not present at publish time.")

    coverage_zip = weekly_coverage_zip_path(session)
    if coverage_zip.exists():
        downloads_dir = week_dir / "downloads"
        downloads_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(coverage_zip, downloads_dir / coverage_zip.name)
        report_links.append(
            PublishedReportLink(
                label="Download coverage package ZIP",
                href=f"downloads/{coverage_zip.name}",
                kind="coverage_package_zip",
            )
        )

    week_end = str(doc.oos_to_date or "").strip() or _published_date()
    final_template_count = _resolve_final_template_count(session, summary, pack_dir, pack_zip)
    final_recommendation_count = _resolve_final_recommendation_count(session, summary)
    entry = PublishedWeekEntry(
        slug=slug,
        session_id=session.id,
        title=title or _default_title(doc, week_end),
        week_end=week_end,
        instrument=str(doc.instrument or ""),
        strategy_id=str(doc.strategy_id or ""),
        session_label=str(doc.label or ""),
        updated_at=str(doc.updated_at or ""),
        published_at=_published_timestamp(),
        final_template_count=final_template_count,
        final_recommendation_count=final_recommendation_count,
        week_page_href=f"weeks/{slug}/index.html",
        report_links=report_links,
    )

    (week_dir / "week_manifest.json").write_text(
        json.dumps(entry.to_dict(), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    _write_week_page(week_dir / "index.html", entry, notes)
    _update_site_manifest(entry)
    _write_site_index(load_published_site_manifest())

    return PublishWeeklySiteResult(
        session_id=session.id,
        slug=slug,
        site_root=str(site_root.resolve()),
        week_dir=str(week_dir.resolve()),
        week_page_href=entry.week_page_href,
        report_count=len(report_links),
        report_links=report_links,
        notes=notes,
    )


def _slug_for_session(session) -> str:
    doc = session.load_document()
    week_end = str(doc.oos_to_date or "").strip() or _published_date()
    instrument = str((doc.instrument or "").split()[0] if doc.instrument else (doc.market_suffix or "")).strip() or "market"
    label = str(doc.label or "weekly").strip() or "weekly"
    return _safe_slug(f"{week_end}-{instrument}-{label}-{session.id}")


def _default_title(doc, week_end: str) -> str:
    instrument = str((doc.instrument or "").split()[0] if doc.instrument else (doc.market_suffix or "")).strip() or "Market"
    label = str(doc.label or "Weekly Coverage").strip() or "Weekly Coverage"
    return f"Week Ending {week_end} - {instrument} - {label}"


def _copy_tree(source: Path, dest: Path) -> None:
    shutil.copytree(source, dest)


def _extract_weekly_pack_zip(source_zip: Path, dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(source_zip) as zf:
        members = [name for name in zf.namelist() if name and not name.endswith("/")]
        top_levels = {Path(name).parts[0] for name in members if Path(name).parts}
        if len(top_levels) == 1:
            top_level = next(iter(top_levels))
            tmp_root = dest.parent / f"{dest.name}__tmp_extract"
            if tmp_root.exists():
                shutil.rmtree(tmp_root)
            tmp_root.mkdir(parents=True, exist_ok=True)
            zf.extractall(tmp_root)
            extracted_root = tmp_root / top_level
            if not extracted_root.exists():
                raise PublishedReportError(
                    f"Weekly report ZIP did not contain expected folder: {top_level}"
                )
            shutil.copytree(extracted_root, dest, dirs_exist_ok=True)
            shutil.rmtree(tmp_root)
            return
        zf.extractall(dest)
    if not (dest / "index.html").exists():
        raise PublishedReportError(
            "Weekly report ZIP did not contain an index.html entry."
        )


def _copy_report_with_assets(source_html: Path, week_dir: Path, dest_name: str) -> str:
    dest_html = week_dir / dest_name
    shutil.copy2(source_html, dest_html)
    source_assets = source_html.parent / f"{source_html.stem}_assets"
    if source_assets.exists() and source_assets.is_dir():
        dest_assets = week_dir / f"{dest_html.stem}_assets"
        if dest_assets.exists():
            shutil.rmtree(dest_assets)
        shutil.copytree(source_assets, dest_assets)
    return dest_html.name


def _rewrite_weekly_pack_index_for_static_site(
    session_id: str,
    pack_dir: Path,
    week_dir: Path,
) -> None:
    index_path = pack_dir / "index.html"
    if not index_path.exists():
        return
    try:
        html = index_path.read_text(encoding="utf-8")
    except OSError:
        return

    original = html
    site_root = published_site_root()
    pack_prefix = "/" + pack_dir.relative_to(site_root).as_posix().strip("/")
    download_prefix = "/" + (week_dir.relative_to(site_root) / "downloads").as_posix().strip("/")
    html = html.replace(
        f'href="/optimizer/sessions/{session_id}/weekly-reports.zip"',
        f'href="{download_prefix}/ExampleWeeklyReport.zip"',
    )
    html = html.replace(
        f'href="/optimizer/sessions/{session_id}/weekly-reports/files/',
        f'href="{pack_prefix}/',
    )

    if html != original:
        index_path.write_text(html, encoding="utf-8")


def _update_site_manifest(entry: PublishedWeekEntry) -> None:
    path = published_site_manifest_path()
    payload = load_published_site_manifest()
    entries = [
        existing
        for existing in payload.get("entries", [])
        if isinstance(existing, dict) and str(existing.get("session_id") or "") != entry.session_id
    ]
    entries.append(entry.to_dict())
    entries.sort(key=lambda item: (str(item.get("week_end") or ""), str(item.get("published_at") or "")), reverse=True)
    payload["schema_version"] = 1
    payload["entries"] = entries
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _write_site_index(payload: dict[str, Any]) -> None:
    root = published_site_root()
    root.mkdir(parents=True, exist_ok=True)
    rows: list[str] = []
    for raw in payload.get("entries", []):
        if not isinstance(raw, dict):
            continue
        links = raw.get("report_links") or []
        week_href = str(raw.get("week_page_href") or "")
        link_html = " ".join(
            f'<a class="btn-primary" href="{escape(_archive_link_href(week_href, str(link.get("href") or "")))}">{escape(str(link.get("label") or ""))}</a>'
            for link in links[:3]
            if isinstance(link, dict) and link.get("href") and link.get("label")
        )
        rows.append(
            "<section class='card'>"
            f"<div class='eyebrow'>Week Ending {escape(str(raw.get('week_end') or ''))}</div>"
            f"<h2>{escape(str(raw.get('title') or 'Weekly Report'))}</h2>"
            "<div class='meta'>"
            f"<span>{escape(str(raw.get('instrument') or '?'))}</span>"
            f"<span>{escape(str(raw.get('strategy_id') or '?'))}</span>"
            f"<span>{escape(str(raw.get('updated_at') or ''))}</span>"
            "</div>"
            "<div class='stats'>"
            f"<span>{int(raw.get('final_template_count') or 0)} final templates</span>"
            f"<span>{int(raw.get('final_recommendation_count') or 0)} final recommendations</span>"
            f"<span>{len(links)} published artifacts</span>"
            "</div>"
            f"<p class='path'>{escape(str(raw.get('session_id') or ''))}</p>"
            "<div class='actions'>"
            f"<a class='btn-ghost' href='{escape(week_href)}'>Open week</a>"
            f"{link_html}"
            "</div>"
            "</section>"
        )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Weekly Optimizer Report Archive</title>
  <style>
    :root {{
      --bg: #0b1220;
      --panel: #111827;
      --panel2: #172033;
      --text: #ecf2ff;
      --muted: #93a4bf;
      --line: #243047;
      --accent: #f59e0b;
      --accentText: #111827;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: radial-gradient(circle at top, #17233a 0%, #0b1220 45%, #07101c 100%); color: var(--text); font-family: Segoe UI, Arial, sans-serif; }}
    .wrap {{ max-width: 1180px; margin: 0 auto; padding: 32px 20px 60px; }}
    .hero {{ display: grid; gap: 14px; padding: 26px; border: 1px solid var(--line); background: linear-gradient(180deg, rgba(255,255,255,0.03), rgba(255,255,255,0.01)); border-radius: 18px; }}
    .hero h1 {{ margin: 0; font-size: 34px; line-height: 1.05; }}
    .hero p {{ margin: 0; color: var(--muted); max-width: 760px; }}
    .grid {{ display: grid; gap: 18px; margin-top: 22px; }}
    .card {{ padding: 20px; border: 1px solid var(--line); background: linear-gradient(180deg, var(--panel2), var(--panel)); border-radius: 16px; }}
    .eyebrow {{ color: #fcd34d; font-size: 12px; letter-spacing: 0.08em; text-transform: uppercase; }}
    h2 {{ margin: 8px 0 10px; font-size: 22px; }}
    .meta, .stats {{ display: flex; flex-wrap: wrap; gap: 8px 16px; color: var(--muted); font-size: 13px; }}
    .stats {{ margin-top: 12px; }}
    .path {{ margin: 12px 0 0; color: #64748b; font-family: Consolas, monospace; font-size: 12px; }}
    .actions {{ display: flex; flex-wrap: wrap; gap: 10px; margin-top: 18px; }}
    .btn-primary, .btn-ghost {{ display: inline-block; padding: 10px 14px; border-radius: 10px; text-decoration: none; font-size: 13px; }}
    .btn-primary {{ background: var(--accent); color: var(--accentText); font-weight: 700; }}
    .btn-ghost {{ border: 1px solid #415272; color: var(--text); }}
    .empty {{ margin-top: 18px; padding: 20px; border-radius: 16px; border: 1px dashed var(--line); color: var(--muted); }}
  </style>
</head>
<body>
  <main class="wrap">
    <section class="hero">
      <div class="eyebrow">Weekly Archive</div>
      <h1>Published Optimizer Reports</h1>
      <p>Each week in this archive links to the full report pack plus the supporting optimizer reports that were available when the week was published. Generate locally, publish once, then push this folder to your public host.</p>
    </section>
    {''.join(rows) if rows else '<section class="empty">No weekly reports have been published yet.</section>'}
  </main>
</body>
</html>
"""
    published_site_index_path().write_text(html, encoding="utf-8")


def _write_week_page(path: Path, entry: PublishedWeekEntry, notes: list[str]) -> None:
    links_html = "".join(
        f"<li><a href=\"{escape(link.href)}\">{escape(link.label)}</a></li>"
        for link in entry.report_links
    )
    notes_html = "".join(f"<li>{escape(note)}</li>" for note in notes if note)
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{escape(entry.title)}</title>
  <style>
    body {{ margin: 0; background: #0b1220; color: #ecf2ff; font-family: Segoe UI, Arial, sans-serif; }}
    main {{ max-width: 980px; margin: 0 auto; padding: 32px 20px 60px; }}
    a {{ color: #fbbf24; }}
    .card {{ border: 1px solid #243047; background: #111827; border-radius: 16px; padding: 22px; margin-bottom: 18px; }}
    .eyebrow {{ color: #fcd34d; font-size: 12px; letter-spacing: 0.08em; text-transform: uppercase; }}
    h1 {{ margin: 8px 0 10px; font-size: 34px; line-height: 1.06; }}
    .meta {{ display: flex; flex-wrap: wrap; gap: 10px 18px; color: #93a4bf; font-size: 13px; }}
    ul {{ margin: 12px 0 0 18px; }}
    li {{ margin: 8px 0; }}
    .back {{ display: inline-block; margin-bottom: 14px; color: #93a4bf; text-decoration: none; }}
  </style>
</head>
<body>
  <main>
    <a class="back" href="../../index.html">&larr; Back to archive</a>
    <section class="card">
      <div class="eyebrow">Week Ending {escape(entry.week_end)}</div>
      <h1>{escape(entry.title)}</h1>
      <div class="meta">
        <span>{escape(entry.instrument)}</span>
        <span>{escape(entry.strategy_id)}</span>
        <span>Published {escape(entry.published_at)}</span>
        <span>Session {escape(entry.session_id)}</span>
      </div>
    </section>
    <section class="card">
      <h2>Reports</h2>
      <ul>{links_html}</ul>
    </section>
    <section class="card">
      <h2>Run Summary</h2>
      <ul>
        <li>{entry.final_template_count} final templates were available when this week was published.</li>
        <li>{entry.final_recommendation_count} final recommendations were available when this week was published.</li>
      </ul>
    </section>
    {f'<section class="card"><h2>Publish Notes</h2><ul>{notes_html}</ul></section>' if notes_html else ''}
  </main>
</body>
</html>
"""
    path.write_text(html, encoding="utf-8")


def _published_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _published_date() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _safe_slug(value: str) -> str:
    cleaned = []
    prev_dash = False
    for ch in value.lower():
        if ch.isalnum():
            cleaned.append(ch)
            prev_dash = False
        else:
            if not prev_dash:
                cleaned.append("-")
                prev_dash = True
    slug = "".join(cleaned).strip("-")
    return slug or "weekly-report"


def _archive_link_href(week_page_href: str, link_href: str) -> str:
    href = str(link_href or "").strip()
    if not href:
        return href
    if href.startswith("/") or "://" in href:
        return href
    week_dir = Path(str(week_page_href or "").strip()).parent.as_posix().strip(".")
    if not week_dir:
        return href
    return f"{week_dir}/{href}"


def _resolve_final_template_count(
    session,
    summary: dict[str, Any],
    pack_dir: Path,
    pack_zip: Path,
) -> int:
    count = int(summary.get("final_template_count") or 0)
    if count > 0:
        return count
    review_counts = _review_summary_counts(session)
    if int(review_counts.get("candidates") or 0) > 0:
        return int(review_counts["candidates"])
    templates_dir = pack_dir / "templates"
    if templates_dir.exists():
        return sum(1 for path in templates_dir.iterdir() if path.is_file() and path.suffix.lower() == ".xml")
    if pack_zip.exists():
        with zipfile.ZipFile(pack_zip) as zf:
            return sum(
                1
                for name in zf.namelist()
                if name.lower().endswith(".xml") and "/templates/" in name.replace("\\", "/")
            )
    return 0


def _resolve_final_recommendation_count(session, summary: dict[str, Any]) -> int:
    count = int(summary.get("final_recommendation_count") or 0)
    if count > 0:
        return count
    review_counts = _review_summary_counts(session)
    return int(review_counts.get("recommendations") or 0)


def _review_summary_counts(session) -> dict[str, Any]:
    review_summary = (
        session.directory
        / "deployment_package"
        / "final_backtest_handoff"
        / "final_backtest_review"
        / "review_summary.json"
    )
    if not review_summary.exists():
        return {}
    try:
        payload = json.loads(review_summary.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    counts = payload.get("counts")
    return counts if isinstance(counts, dict) else {}
