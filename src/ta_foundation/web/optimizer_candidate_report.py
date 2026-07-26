from __future__ import annotations

"""Auto-generate a per-candidate HTML report for an optimizer session.

Each ``F_xxx`` folder under
``deployment_package/final_backtest_handoff/nt8_backtest_results/`` is
structurally a single-run NinjaTrader export — exactly what the
existing CLI report pipeline consumes via ``--input``. This module
reuses that pipeline programmatically with a curated finalist-default
section list and writes one HTML per candidate to
``deployment_package/per_candidate_reports/<run_id>.html``.

Operator workflow is zero clicks: ingest finishes → rebuild kicks
this off → links appear on the decision dashboard.

Section selection
-----------------

The base auto set is intentionally narrow and bar-data-free so the
report works without market data staged:

- ``exec_card_god_banner``       — header banner (decoded name + portrait)
- ``run_kpi_cards``              — headline KPIs
- ``analysis_chart_replica``     — equity curve + per-day P/L (from Analysis.csv)
- ``run_metadata_cards``         — backtest period / instrument / etc.
- ``daily_scoreboard``           — daily P/L matrix
- ``daily_winner_spotlight``     — best/worst days
- ``daily_leaderboard_cards``    — daily leader cards
- ``run_settings_table``         — parameter dump

Caller can override the section list (used by the on-demand picker).
"""

import shutil
from dataclasses import asdict, dataclass, field
import math
from pathlib import Path
import re
from typing import Any

from ta_foundation.core.pipeline import IngestResult, ingest_folder
from ta_foundation.core.registry import ParserRegistry
from ta_foundation.parsers.ninjatrader.analysis_by_day_csv import NinjaTraderDailyAnalysisCsvParser
from ta_foundation.parsers.ninjatrader.optimization_csv import NinjaTraderOptimizationCsvParser
from ta_foundation.parsers.ninjatrader.settings_csv import NinjaTraderSettingsCsvParser
from ta_foundation.parsers.ninjatrader.summary_csv import NinjaTraderSummaryCsvParser
from ta_foundation.parsers.ninjatrader.trades_csv import NinjaTraderTradesCsvParser
from ta_foundation.reports.html.builder import HtmlReportBuilder, HtmlSection
from ta_foundation.reports.html.embed import file_to_data_uri
from ta_foundation.reports.html.export_cards import export_exec_cards_to_png
from ta_foundation.reports.html.registry import SECTION_REGISTRY
from ta_foundation.web.report_assets import finalize_report_html, normalize_report_asset_mode
from ta_foundation.web.optimizer_session import OptimizerSession
from ta_foundation.web.optimizer_template_naming_fallback import analyze_template_dict


PER_CANDIDATE_REPORTS_DIRNAME = "per_candidate_reports"
SESSION_CANDIDATE_REPORT_FILENAME = "session_candidate_report.html"
SELECTED_SESSION_CANDIDATE_REPORT_FILENAME = "selected_candidate_report.html"
DEFAULT_PORTRAIT_DIRS: tuple[Path, ...] = (
    Path.home() / "Pictures" / "NewGodImages",
    Path.home() / "Pictures" / "God images",
)

DEFAULT_FINALIST_SECTIONS: list[str] = [
    "run_executive_profile_cards",
    "run_snapshot_clipboard",
    "run_kpi_cards",
    "daily_scoreboard",
    "daily_winner_spotlight",
    "run_settings_table",
]

DEFAULT_SESSION_CANDIDATE_SECTIONS: list[str] = [
    "comparison_overview",
    "final_template_bundle_basket",
    "equity_curve_comparison",
    "run_kpi_cards",
    "run_metadata_cards",
    "daily_scoreboard",
    "daily_winner_spotlight",
    "daily_leaderboard_cards",
    "run_settings_table",
]

EXEC_PROFILE_SECTION: dict[str, Any] = {
    "id": "run_executive_profile_cards",
    "options": {
        "show_hint": True,
        "show_run_image": True,
        "background_style": "image-dark-overlay",
        "card_width_px": 1040,
        "card_padding_px": 24,
        "image_width_px": 340,
        "wlr_days_back": 30,
        "wlr_gap_px": 2,
        "show_detail_charts": True,
        "detail_chart_layout": "stack",
        "detail_chart_width_px": 900,
        "timeline_render_bin_minutes": 15,
        "timeline_cell_h_px": 10,
        "timeline_show_hours": True,
        "timeline_show_summary": True,
    },
}

DAILY_WINNER_SECTION: dict[str, Any] = {
    "id": "daily_winner_spotlight",
    "title": "Daily Winner Insight",
    "options": {
        "top_n": 10,
        "strip_days": 6,
    },
}

WEEKLY_PROP_SECTION: dict[str, Any] = {
    "id": "weekly_leaderboard_cards",
    "title": "Weekly Prop Dashboard",
    "options": {
        "top_n": 200,
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

DEFAULT_FINALIST_SECTION_OPTIONS: dict[str, dict[str, Any]] = {
    "run_executive_profile_cards": dict(EXEC_PROFILE_SECTION["options"]),
    "run_snapshot_clipboard": {
        "style": "minimal",
        "density": "compact",
        "layout": "stack",
        "columns": 1,
        "show_hint": False,
    },
    "daily_winner_spotlight": dict(DAILY_WINNER_SECTION["options"]),
}


SECTION_BUCKET_RULES: list[tuple[str, str, str, tuple[str, ...]]] = [
    (
        "run_scoped",
        "Run-scoped (safe - always works)",
        "Uses run export data only, so these sections are expected to render for every candidate.",
        (
            "run_*",
            "daily_*",
            "trade_*",
            "equity_curve_*",
            "exit_policy_*",
            "apex_*",
            "exec_card_*",
            "analysis_chart_replica",
            "optimization_overview",
            "strategy_parameter_matrix",
        ),
    ),
    (
        "bar_market_data",
        "Bar/market-data dependent",
        "These sections may render empty placeholders unless market bars or ticks were staged for the session.",
        (
            "pattern_engine_*",
            "anchor_interaction_*",
            "anchor_tp_sl_*",
            "large_candle_*",
            "tick_*",
            "filter_*",
            "*_discovery_*",
            "regime_*",
            "market_regime_*",
            "horizon_*",
            "trade_candle_overlay",
        ),
    ),
    (
        "multi_candidate",
        "Multi-candidate",
        "These sections are comparison-oriented, so a single-candidate report will usually render a thinner view.",
        (
            "comparison_*",
            "weekly_*",
            "strategy_lifecycle_*",
            "strategy_momentum_*",
            "strategy_session_momentum_*",
            "deployment_board_*",
        ),
    ),
]


class CandidateReportError(Exception):
    pass


@dataclass(frozen=True)
class CandidateReportResult:
    run_id: str
    html_path: str | None
    sections_rendered: list[str]
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CandidateReportBatchResult:
    session_id: str
    output_dir: str
    per_candidate: list[CandidateReportResult]
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "output_dir": self.output_dir,
            "per_candidate": [c.to_dict() for c in self.per_candidate],
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class SessionCandidateReportResult:
    session_id: str
    html_path: str | None
    sections_rendered: list[str]
    package_count: int
    cards_dir: str | None = None
    cards_exported: int = 0
    run_ids: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def group_sections_by_bucket(
    default_sections: list[str] | tuple[str, ...] | set[str] | None = None,
) -> list[dict[str, Any]]:
    """Return report sections grouped for the on-demand picker page."""
    buckets: list[dict[str, Any]] = [
        {"id": bucket_id, "title": title, "description": description, "sections": []}
        for bucket_id, title, description, _patterns in SECTION_BUCKET_RULES
    ]
    default_selected = set(default_sections or DEFAULT_FINALIST_SECTIONS)

    for section_id, section_def in SECTION_REGISTRY.items():
        bucket_index = _bucket_index_for_section(section_id)
        title = section_def.default_title or section_id
        buckets[bucket_index]["sections"].append({
            "id": section_id,
            "title": title,
            "checked": section_id in default_selected,
        })
    return buckets


def build_candidate_report(
    session: OptimizerSession,
    run_id: str,
    *,
    sections: list[str | dict[str, Any]] | None = None,
    images_dir: Path | str | None = None,
    output_dir: Path | None = None,
    report_asset_mode: str = "embedded",
) -> CandidateReportResult:
    """Build a single per-candidate HTML report.

    Parameters
    ----------
    session : OptimizerSession
        The optimizer session whose final-Backtest results live on disk.
    run_id : str
        Candidate id (e.g. ``F_001``).
    sections : list[str | dict[str, Any]] | None
        Section ids or section config dicts in render order. ``None`` uses
        :data:`DEFAULT_FINALIST_SECTIONS`. Unknown section ids are
        silently skipped with a note.
    images_dir : Path | str | None
        Portrait images directory for the god/monster banner. ``None``
        means the banner renders the decoded name only, no portrait.
    output_dir : Path | None
        Where to write the HTML. Defaults to
        ``<session>/deployment_package/per_candidate_reports/``.
    report_asset_mode : str
        ``embedded`` keeps self-contained HTML; ``external`` writes sibling
        image files for lighter online viewing.
    """
    report_asset_mode = normalize_report_asset_mode(report_asset_mode)
    pkg_dir = session.directory / "deployment_package"
    candidate_dir = _resolve_candidate_results_dir(pkg_dir, run_id)
    if candidate_dir is None:
        raise CandidateReportError(
            f"No backtest results for {run_id} under {pkg_dir}"
        )

    out_dir = output_dir or (pkg_dir / PER_CANDIDATE_REPORTS_DIRNAME)
    out_dir.mkdir(parents=True, exist_ok=True)

    requested = sections or DEFAULT_FINALIST_SECTIONS
    notes: list[str] = []

    registry = _build_registry()
    ingest = ingest_folder(
        candidate_dir,
        registry=registry,
        recursive=False,
        include_run_images=False,
        load_tick_data=False,
    )
    if not ingest.packages:
        raise CandidateReportError(
            f"Ingest produced no packages for {candidate_dir}"
        )
    # The ingest derives run_id from the CSV stems (e.g. F_001_Trades.csv).
    pkg = next(iter(ingest.packages.values()))

    analysis_csv = candidate_dir / "Analysis.csv"
    template_path = _find_template_path_for_run_id(session, run_id)

    # Derive market root from the session contract: "NQ 06-26" -> "NQ".
    # Falls back to market_suffix on the document, then empty.
    doc = session.load_document()
    instrument = (doc.instrument or "").strip()
    market_root = instrument.split()[0] if instrument else (doc.market_suffix or "").strip()

    resolved_images_dir = _resolve_images_dir(images_dir)

    base_options: dict[str, Any] = {
        "run_id": run_id,
        "label": pkg.summary.kpis_all.get("strategy") if pkg.summary else run_id,
        "analysis_csv_path": str(analysis_csv) if analysis_csv.exists() else None,
        "template_path": str(template_path) if template_path else None,
        "images_dir": resolved_images_dir,
        "market_suffix": market_root or None,
    }
    if not images_dir and resolved_images_dir:
        notes.append(f"Using default portrait images dir(s): {resolved_images_dir}")
    if not analysis_csv.exists():
        notes.append(f"Analysis.csv not present for {run_id}; chart-replica section will degrade.")
    if template_path is None:
        notes.append(f"No matching template for {run_id}; banner will fall back to plain text.")

    notes.extend(
        _enrich_final_template_report_packages(
            session,
            {run_id: pkg},
            images_dir=images_dir,
        )
    )

    html_sections: list[HtmlSection] = []
    rendered: list[str] = []
    for entry in requested:
        sec_id, title_override, section_options = _normalize_section_entry(entry)
        section_def = SECTION_REGISTRY.get(sec_id)
        if section_def is None:
            notes.append(f"Unknown section id: {sec_id}")
            continue
        options = dict(base_options)
        options.update(DEFAULT_FINALIST_SECTION_OPTIONS.get(sec_id, {}))
        options.update(section_options)
        html_sections.append(HtmlSection(
            id=section_def.id,
            title=title_override or section_def.default_title,
            render_fn=section_def.render_fn,
            options=options,
        ))
        rendered.append(sec_id)

    if not html_sections:
        raise CandidateReportError(
            "No valid sections in the requested list; nothing to render."
        )

    builder = HtmlReportBuilder(
        report_title=f"{run_id} — {session.id} (finalist report)",
        sections=html_sections,
    )
    context = {
        "packages": ingest.packages,
        "market": ingest.market,
        "options": {},  # per-section options live on each HtmlSection
        "all_options": {},
    }
    html = builder.build(context)

    out_path = out_dir / f"{run_id}.html"
    html, asset_notes = finalize_report_html(
        html,
        out_path,
        asset_mode=report_asset_mode,
    )
    out_path.write_text(html, encoding="utf-8")
    notes.extend(asset_notes)

    return CandidateReportResult(
        run_id=run_id,
        html_path=str(out_path),
        sections_rendered=rendered,
        notes=notes,
    )


def build_all_candidate_reports(
    session: OptimizerSession,
    *,
    sections: list[str | dict[str, Any]] | None = None,
    images_dir: Path | str | None = None,
    purge_existing: bool = True,
    report_asset_mode: str = "embedded",
) -> CandidateReportBatchResult:
    """Build a report for every candidate in the final + promoted result dirs.

    Walks both ``final_backtest_handoff/nt8_backtest_results/`` (F_NNN
    finalists) and ``promoted_handoff/nt8_backtest_results/`` (P_NNN
    rows from the shortlist-promotion path). Reports for both kinds land
    in the same ``per_candidate_reports/`` folder keyed by ``run_id``.

    ``purge_existing=True`` (default) removes any previous per-candidate
    HTML so stale reports don't survive a rebuild. The output directory
    itself is preserved.
    """
    report_asset_mode = normalize_report_asset_mode(report_asset_mode)
    pkg_dir = session.directory / "deployment_package"
    final_results_dir = pkg_dir / "final_backtest_handoff" / "nt8_backtest_results"
    promoted_results_dir = pkg_dir / "promoted_handoff" / "nt8_backtest_results"
    out_dir = pkg_dir / PER_CANDIDATE_REPORTS_DIRNAME

    notes: list[str] = []
    candidate_dirs: list[Path] = []
    if final_results_dir.exists():
        candidate_dirs.extend(sorted(p for p in final_results_dir.iterdir() if p.is_dir()))
    if promoted_results_dir.exists():
        candidate_dirs.extend(sorted(p for p in promoted_results_dir.iterdir() if p.is_dir()))

    if not candidate_dirs:
        return CandidateReportBatchResult(
            session_id=session.id,
            output_dir=str(out_dir),
            per_candidate=[],
            notes=[
                f"No backtest results under {final_results_dir} or "
                f"{promoted_results_dir}; nothing to render."
            ],
        )

    if purge_existing and out_dir.exists():
        for child in out_dir.glob("*.html"):
            try:
                child.unlink()
            except OSError:
                pass

    per: list[CandidateReportResult] = []
    for cand in candidate_dirs:
        run_id = cand.name
        try:
            result = build_candidate_report(
                session,
                run_id,
                sections=sections,
                images_dir=images_dir,
                report_asset_mode=report_asset_mode,
            )
            per.append(result)
        except CandidateReportError as exc:
            notes.append(f"{run_id}: {exc}")
        except Exception as exc:
            notes.append(f"{run_id}: unexpected error: {exc}")

    return CandidateReportBatchResult(
        session_id=session.id,
        output_dir=str(out_dir),
        per_candidate=per,
        notes=notes,
    )


def build_session_candidate_report(
    session: OptimizerSession,
    *,
    sections: list[str | dict[str, Any]] | None = None,
    output_path: Path | None = None,
    images_dir: Path | str | None = None,
    export_exec_cards_png: bool = False,
    exec_cards_dir: Path | None = None,
    dark_shell: bool = False,
    run_ids: list[str] | tuple[str, ...] | set[str] | None = None,
    enrich_packages: bool = True,
    enrich_detail_charts: bool = True,
    report_asset_mode: str = "embedded",
    preloaded_ingest: IngestResult | None = None,
) -> SessionCandidateReportResult:
    """Build one HTML report that ingests every final candidate together.

    This powers comparison-oriented sections that need multiple packages in
    ``ctx["packages"]`` instead of the single-package context used by
    per-candidate reports.
    """
    report_asset_mode = normalize_report_asset_mode(report_asset_mode)
    pkg_dir = session.directory / "deployment_package"
    results_dir = pkg_dir / "final_backtest_handoff" / "nt8_backtest_results"
    out_path = output_path or (pkg_dir / SESSION_CANDIDATE_REPORT_FILENAME)
    notes: list[str] = []
    requested_run_ids = {str(run_id).strip() for run_id in (run_ids or []) if str(run_id).strip()}

    if not results_dir.exists():
        return SessionCandidateReportResult(
            session_id=session.id,
            html_path=None,
            sections_rendered=[],
            package_count=0,
            notes=[f"No final-Backtest results at {results_dir}; nothing to render."],
        )

    requested = sections or DEFAULT_SESSION_CANDIDATE_SECTIONS
    ingest = preloaded_ingest or load_session_candidate_ingest(session)
    if not ingest.packages:
        raise CandidateReportError(
            f"Ingest produced no packages for {results_dir}"
        )
    packages = dict(ingest.packages)
    if requested_run_ids:
        packages = {
            package_id: package
            for package_id, package in packages.items()
            if package_id in requested_run_ids
            or str(getattr(package, "run_id", "") or "") in requested_run_ids
        }
        if not packages:
            raise CandidateReportError(
                "No final candidate packages matched selected run id(s): "
                + ", ".join(sorted(requested_run_ids))
            )
        notes.append(
            "Filtered final report to selected run id(s): "
            + ", ".join(sorted(requested_run_ids))
        )
    if ingest.unparsed_files:
        notes.append(f"{len(ingest.unparsed_files)} non-report file(s) skipped during ingest.")

    if enrich_packages:
        image_notes = _enrich_final_template_report_packages(
            session,
            packages,
            images_dir=images_dir,
            attach_detail_charts=enrich_detail_charts,
        )
        notes.extend(image_notes)
    else:
        notes.extend(_enrich_final_template_report_package_names(session, packages))

    html_sections: list[HtmlSection] = []
    rendered: list[str] = []
    for entry in requested:
        sec_id, title_override, options = _normalize_section_entry(entry)
        section_def = SECTION_REGISTRY.get(sec_id)
        if section_def is None:
            notes.append(f"Unknown section id: {sec_id}")
            continue
        html_sections.append(HtmlSection(
            id=section_def.id,
            title=title_override or section_def.default_title,
            render_fn=section_def.render_fn,
            options=options,
        ))
        rendered.append(sec_id)

    if not html_sections:
        raise CandidateReportError(
            "No valid sections in the requested list; nothing to render."
        )

    builder = HtmlReportBuilder(
        report_title=f"{session.id} — all finalist templates",
        sections=html_sections,
    )
    html = builder.build({
        "packages": packages,
        "market": ingest.market,
        "options": {},
        "all_options": {},
    })
    if dark_shell:
        html = _apply_dark_report_shell(html)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    html, asset_notes = finalize_report_html(
        html,
        out_path,
        asset_mode=report_asset_mode,
    )
    out_path.write_text(html, encoding="utf-8")
    notes.extend(asset_notes)

    cards_dir: Path | None = None
    cards_exported = 0
    if export_exec_cards_png:
        cards_dir = exec_cards_dir or (out_path.parent / "cards")
        try:
            export = export_exec_cards_to_png(out_path, cards_dir)
            cards_exported = len(export.exported)
            for skipped in export.skipped:
                notes.append(f"card export skipped: {skipped}")
        except Exception as exc:
            notes.append(f"card export failed: {exc}")

    return SessionCandidateReportResult(
        session_id=session.id,
        html_path=str(out_path),
        sections_rendered=rendered,
        package_count=len(packages),
        cards_dir=str(cards_dir) if cards_dir else None,
        cards_exported=cards_exported,
        run_ids=sorted(str(run_id) for run_id in packages.keys()),
        notes=notes,
    )


def build_final_template_card_report(
    session: OptimizerSession,
    *,
    output_path: Path | None = None,
    images_dir: Path | str | None = None,
    export_exec_cards_png: bool = True,
    exec_cards_dir: Path | None = None,
    week_ending: str | None = None,
    report_asset_mode: str = "embedded",
) -> SessionCandidateReportResult:
    sections = [
        DAILY_WINNER_SECTION,
        EXEC_PROFILE_SECTION,
        _section_with_overrides(
            WEEKLY_PROP_SECTION,
            {"week_ending": week_ending} if week_ending else {},
        ),
    ]
    pkg_dir = session.directory / "deployment_package"
    out_path = output_path or (pkg_dir / "final_template_cards_report.html")
    return build_session_candidate_report(
        session,
        sections=sections,
        output_path=out_path,
        images_dir=images_dir,
        export_exec_cards_png=export_exec_cards_png,
        exec_cards_dir=exec_cards_dir or (pkg_dir / "cards"),
        dark_shell=True,
        report_asset_mode=report_asset_mode,
    )


def list_existing_candidate_reports(session: OptimizerSession) -> dict[str, str]:
    """Return run_id → HTML path for reports already on disk. Useful
    for the decision dashboard to decide which rows get a link."""
    out_dir = session.directory / "deployment_package" / PER_CANDIDATE_REPORTS_DIRNAME
    if not out_dir.exists():
        return {}
    return {
        p.stem: str(p) for p in sorted(out_dir.glob("*.html"))
        if p.is_file()
    }


def session_candidate_report_path(session: OptimizerSession) -> Path:
    return session.directory / "deployment_package" / SESSION_CANDIDATE_REPORT_FILENAME


def selected_session_candidate_report_path(session: OptimizerSession) -> Path:
    return session.directory / "deployment_package" / SELECTED_SESSION_CANDIDATE_REPORT_FILENAME


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _build_registry() -> ParserRegistry:
    return ParserRegistry(parsers=[
        NinjaTraderTradesCsvParser(),
        NinjaTraderDailyAnalysisCsvParser(),
        NinjaTraderSummaryCsvParser(),
        NinjaTraderSettingsCsvParser(),
        NinjaTraderOptimizationCsvParser(),
    ])


def load_session_candidate_ingest(session: OptimizerSession) -> IngestResult:
    """Load the shared final-backtest ingest used by session-wide reports."""
    results_dir = (
        session.directory
        / "deployment_package"
        / "final_backtest_handoff"
        / "nt8_backtest_results"
    )
    return ingest_folder(
        results_dir,
        registry=_build_registry(),
        recursive=True,
        include_run_images=False,
        load_tick_data=False,
    )


def _normalize_section_entry(entry: str | dict[str, Any]) -> tuple[str, str | None, dict[str, Any]]:
    if isinstance(entry, str):
        return entry, None, {}
    if not isinstance(entry, dict):
        return str(entry), None, {}
    return (
        str(entry.get("id") or ""),
        str(entry.get("title")) if entry.get("title") else None,
        dict(entry.get("options") or {}),
    )


def _section_with_overrides(section: dict[str, Any], options: dict[str, Any]) -> dict[str, Any]:
    out = dict(section)
    merged = dict(section.get("options") or {})
    for key, value in options.items():
        if value is not None:
            merged[key] = value
    out["options"] = merged
    return out


def _apply_dark_report_shell(html: str) -> str:
    css = """
    <style id="ta-final-template-dark-shell">
      :root {
        --bg: #000000;
        --card: #07090d;
        --text: #f8fafc;
        --muted: #94a3b8;
        --border: rgba(148,163,184,0.26);
        --shadow: 0 18px 42px rgba(0,0,0,0.55);
      }
      html, body { background: #000000 !important; color: #f8fafc !important; }
      body { min-height: 100vh; }
      nav {
        background: #02040a !important;
        border-bottom: 1px solid rgba(148,163,184,0.28) !important;
      }
      nav a { color: #cbd5e1 !important; }
      nav a:hover { color: #ffffff !important; }
      .wrap { background: transparent !important; }
      .header { border-bottom-color: rgba(148,163,184,0.22) !important; }
      .title, .card h2, .card h3 { color: #f8fafc !important; }
      .subtitle, .muted, .mono { color: #94a3b8 !important; }
      .card {
        background: #07090d !important;
        border-color: rgba(148,163,184,0.24) !important;
        box-shadow: 0 18px 42px rgba(0,0,0,0.55) !important;
      }
      .card h2 { border-bottom-color: rgba(148,163,184,0.22) !important; }
      .pill {
        background: rgba(37,99,235,0.16) !important;
        border-color: rgba(96,165,250,0.36) !important;
        color: #bfdbfe !important;
      }
      ::-webkit-scrollbar-track { background: #05070c !important; }
      ::-webkit-scrollbar-thumb { background: #334155 !important; }
    </style>
    """
    return html.replace("</head>", f"{css}\n</head>")


def _enrich_final_template_report_packages(
    session: OptimizerSession,
    packages: dict[str, Any],
    *,
    images_dir: Path | str | None,
    attach_detail_charts: bool = True,
) -> list[str]:
    # Keep docs/runbooks/report_image_mapping.md in sync with this mapping
    # chain; weekly reports that show portraits/charts depend on these fields.
    notes: list[str] = []
    resolved_images_dir = _resolve_images_dir(images_dir)
    if not resolved_images_dir:
        notes.append("No portrait images dir configured; executive cards will omit portraits.")

    doc = session.load_document()
    instrument = (doc.instrument or "").strip()
    market_root = instrument.split()[0] if instrument else (doc.market_suffix or "").strip()

    for run_id, pkg in packages.items():
        derived = pkg.metadata.setdefault("derived", {})
        template_path = _find_template_path_for_run_id(session, run_id)
        if template_path is None:
            notes.append(f"{run_id}: no matching renamed/final template XML found for image lookup.")
        else:
            derived["template_path"] = str(template_path)
            _attach_template_display_name(derived, {}, template_path=template_path, market_root=market_root)
            _apply_bot_name_to_settings(pkg, str(derived.get("display_name_spaced") or ""))

        candidate_dir = _resolve_candidate_results_dir(session.directory / "deployment_package", run_id)
        if candidate_dir is not None:
            analysis_csv = candidate_dir / "Analysis.csv"
            if attach_detail_charts and analysis_csv.exists():
                derived["analysis_csv_path"] = str(analysis_csv)
                _attach_analysis_chart_image(pkg, analysis_csv)
                _attach_analysis_derived_metrics(pkg)
            if attach_detail_charts:
                _attach_settings_table_image(pkg)
            _attach_potential_metrics(pkg)

        if template_path is None:
            continue

        if not resolved_images_dir:
            continue

        try:
            from ta_foundation.web.optimizer_image_lookup import lookup_image_for_template
            lookup = lookup_image_for_template(
                template_path,
                images_dir=resolved_images_dir,
                market_suffix=market_root or None,
            )
        except Exception as exc:
            notes.append(f"{run_id}: image lookup failed: {exc}")
            continue

        if lookup.image_path:
            portrait = Path(lookup.image_path)
            uri = file_to_data_uri(portrait)
            if uri:
                derived["run_image_uri"] = uri
                derived["run_image_path"] = str(portrait)
                derived["run_image_source"] = lookup.matched_step or "template_lookup"
                pkg.assets.setdefault("run_image_uri", uri)
                pkg.assets.setdefault("run_image_path", str(portrait))

            background = _matching_background_path(portrait)
            if background is not None:
                bg_uri = file_to_data_uri(background)
                if bg_uri:
                    derived["background_image_uri"] = bg_uri
                    derived["background_image_path"] = str(background)
            elif uri:
                derived.setdefault("background_image_uri", uri)
                derived.setdefault("background_image_path", str(portrait))
        else:
            notes.append(f"{run_id}: no image matched template {template_path.name}.")

        if lookup.decoded:
            derived["template_naming"] = lookup.decoded
            _attach_template_display_name(derived, lookup.decoded, template_path=template_path, market_root=market_root)
        else:
            _attach_template_display_name(derived, {}, template_path=template_path, market_root=market_root)

    return notes


def _enrich_final_template_report_package_names(
    session: OptimizerSession,
    packages: dict[str, Any],
) -> list[str]:
    """Attach semantic template names without generating image/chart assets."""
    notes: list[str] = []
    doc = session.load_document()
    instrument = (doc.instrument or "").strip()
    market_root = instrument.split()[0] if instrument else (doc.market_suffix or "").strip()

    for run_id, pkg in packages.items():
        derived = pkg.metadata.setdefault("derived", {})
        template_path = _find_template_path_for_run_id(session, str(run_id))
        if template_path is None:
            notes.append(f"{run_id}: no matching renamed/final template XML found for display name.")
            continue
        derived["template_path"] = str(template_path)
        _attach_template_display_name(derived, {}, template_path=template_path, market_root=market_root)
        _apply_bot_name_to_settings(pkg, str(derived.get("display_name_spaced") or ""))

    return notes


def _attach_template_display_name(
    derived: dict[str, Any],
    decoded: dict[str, Any],
    *,
    template_path: Path | None = None,
    market_root: str | None = None,
) -> None:
    """Expose the semantic template name to report sections.

    The run folder remains ``F_001`` for data joins and card exports, but the
    visual identity should use the name written by the template-naming pass.
    """
    decoded_map = dict(decoded or {})
    if template_path is not None and not (
        decoded_map.get("compact_name")
        or decoded_map.get("spaced_name")
        or decoded_map.get("output_file_name")
    ):
        try:
            decoded_map = analyze_template_dict(template_path)
        except Exception:
            decoded_map = dict(decoded or {})

    template_display = _stem_without_market(template_path.name if template_path else "", market_root)
    compact = str(decoded_map.get("compact_name") or "").strip()
    spaced = str(decoded_map.get("spaced_name") or "").strip()
    output_name = str(decoded_map.get("output_file_name") or "").strip()

    display = compact or template_display or _stem_without_market(output_name, market_root)
    if display:
        derived["display_name"] = display
    if compact:
        derived["display_name_spaced"] = _space_template_name(compact)
    elif spaced:
        derived["display_name_spaced"] = spaced
    elif template_display:
        derived["display_name_spaced"] = _space_template_name(template_display)
    elif display:
        derived["display_name_spaced"] = _space_template_name(display)


def _stem_without_market(name: str, market_root: str | None) -> str:
    stem = Path(name).stem if name else ""
    market = (market_root or "").strip()
    if market and stem.lower().endswith(f"-{market}".lower()):
        stem = stem[: -(len(market) + 1)]
    return stem


def _space_template_name(name: str) -> str:
    if not name:
        return ""
    spaced = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name)
    spaced = re.sub(r"(?<=[A-Za-z])(?=V\d+$)", " ", spaced)
    return spaced


def _apply_bot_name_to_settings(pkg: Any, bot_name: str) -> None:
    if not bot_name:
        return
    try:
        import pandas as pd
    except Exception:
        return
    df = getattr(pkg, "settings", None)
    if not isinstance(df, pd.DataFrame) or df.empty or "item" not in df.columns:
        return
    mask = df["item"].astype(str).str.strip().str.lower().eq("bot_name")
    if mask.any() and "value" in df.columns:
        df.loc[mask, "value"] = bot_name


def _attach_analysis_derived_metrics(pkg: Any) -> None:
    """Add weighted MAE/MFE/ETD values from the daily Analysis.csv dataframe."""
    try:
        import pandas as pd
    except Exception:
        return

    df = getattr(pkg, "daily", None)
    if not isinstance(df, pd.DataFrame) or df.empty:
        return

    derived = pkg.metadata.setdefault("derived", {})
    for col, target in (
        ("avg_mae", "avg_mae_usd"),
        ("avg_mfe", "avg_mfe_usd"),
        ("avg_etd", "avg_etd_usd"),
    ):
        if col not in df.columns:
            continue
        values = pd.to_numeric(df[col], errors="coerce")
        weights = pd.to_numeric(df.get("trade_count"), errors="coerce") if "trade_count" in df.columns else None
        if weights is not None:
            valid = values.notna() & weights.notna() & (weights > 0)
            if valid.any() and float(weights[valid].sum()) != 0:
                derived[target] = float((values[valid] * weights[valid]).sum() / weights[valid].sum())
                continue
        values = values.dropna()
        if not values.empty:
            derived[target] = float(values.mean())


def _attach_potential_metrics(pkg: Any) -> None:
    """Derive session potential from Pantheon's per-trade bracket and guardrails."""
    derived = pkg.metadata.setdefault("derived", {})
    settings = _settings_map(pkg)
    max_stop = _safe_float(settings.get("maxstop"))
    max_tp_ratio = _safe_float(settings.get("maxtpratio"))
    contracts = _safe_float(settings.get("contracts")) or 1.0
    tick_value = _safe_float(derived.get("tick_value_usd")) or _infer_tick_value(settings)
    profit_stop = _safe_float(settings.get("profitstop"))
    loss_stop = _safe_float(settings.get("lossstop"))
    max_trades = _safe_float(settings.get("maxtrades"))
    use_max_tp = _setting_bool(settings, "use_maxtp", "usemaxtp")

    if max_stop is None or tick_value is None:
        return

    per_trade_loss = max_stop * tick_value * contracts
    raw_max_trades = int(max_trades) if max_trades is not None and max_trades > 0 else None
    target_ticks = math.floor(max_stop * max_tp_ratio) if max_tp_ratio is not None else None
    per_trade_profit = (target_ticks * tick_value * contracts) if target_ticks is not None else None
    if use_max_tp is False:
        per_trade_profit = None

    profit_trades = _effective_trades_for_guard(raw_max_trades, profit_stop, per_trade_profit)
    loss_trades = _effective_trades_for_guard(raw_max_trades, loss_stop, per_trade_loss)

    possible_trade_counts = [value for value in (raw_max_trades, profit_trades, loss_trades) if value is not None]
    if possible_trade_counts:
        derived["effective_max_trades_per_session"] = min(possible_trade_counts)
    if raw_max_trades is not None:
        derived["raw_max_trades_per_session"] = raw_max_trades

    derived["stop_loss_usd_per_trade"] = per_trade_loss
    if target_ticks is not None:
        derived["max_tp_ticks_per_trade"] = target_ticks
    if per_trade_profit is not None:
        derived["max_tp_usd_per_trade"] = per_trade_profit

    if loss_trades is not None:
        derived["max_potential_loss_usd"] = _potential_with_guardrail_overshoot(
            raw_max_trades,
            loss_stop,
            per_trade_loss,
        )
        derived["max_potential_loss_trades"] = loss_trades
    if profit_trades is not None and per_trade_profit is not None:
        derived["max_potential_profit_usd"] = _potential_with_guardrail_overshoot(
            raw_max_trades,
            profit_stop,
            per_trade_profit,
        )
        derived["max_potential_profit_trades"] = profit_trades


def _effective_trades_for_guard(
    raw_max_trades: int | None,
    session_stop: float | None,
    per_trade_amount: float | None,
) -> int | None:
    counts: list[int] = []
    if raw_max_trades is not None:
        counts.append(raw_max_trades)
    if session_stop is not None and session_stop > 0 and per_trade_amount is not None and per_trade_amount > 0:
        counts.append(max(1, int(math.ceil(session_stop / per_trade_amount))))
    return min(counts) if counts else None


def _potential_with_guardrail_overshoot(
    raw_max_trades: int | None,
    session_stop: float | None,
    per_trade_amount: float,
) -> float:
    max_by_trade_count = raw_max_trades * per_trade_amount if raw_max_trades is not None else None
    if session_stop is None or session_stop <= 0:
        return max_by_trade_count if max_by_trade_count is not None else per_trade_amount

    # Pantheon checks the session guard after a trade closes, so the run can
    # be just below the guardrail and then add one more full bracket result.
    max_by_guardrail = max(0.0, session_stop - 1.0) + per_trade_amount
    if max_by_trade_count is None:
        return max_by_guardrail
    return min(max_by_trade_count, max_by_guardrail)


def _setting_bool(settings: dict[str, Any], *keys: str) -> bool | None:
    for key in keys:
        if key not in settings:
            continue
        raw = str(settings.get(key)).strip().lower()
        if raw in {"true", "1", "yes", "y"}:
            return True
        if raw in {"false", "0", "no", "n"}:
            return False
    return None


def _settings_map(pkg: Any) -> dict[str, Any]:
    try:
        import pandas as pd
    except Exception:
        return {}

    df = getattr(pkg, "settings", None)
    if not isinstance(df, pd.DataFrame) or df.empty:
        return {}
    out: dict[str, Any] = {}
    for _, row in df.iterrows():
        key = str(row.get("item", "")).strip().lower()
        if key:
            out[key] = row.get("value", "")
    return out


def _infer_tick_value(settings: dict[str, Any]) -> float | None:
    instrument = str(settings.get("instrument") or "").upper()
    if instrument.startswith("NQ") or instrument.startswith("MNQ"):
        return 5.0 if instrument.startswith("NQ") else 0.5
    if instrument.startswith("ES") or instrument.startswith("MES"):
        return 12.5 if instrument.startswith("ES") else 1.25
    return None


def _safe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        text = str(value).replace("$", "").replace(",", "").strip()
        if text.startswith("(") and text.endswith(")"):
            text = "-" + text[1:-1]
        number = float(text)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    return number


def _matching_background_path(portrait: Path) -> Path | None:
    for ext in (portrait.suffix, ".png", ".jpg", ".jpeg", ".webp", ".gif"):
        candidate = portrait.with_name(f"{portrait.stem}_Background{ext}")
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def _attach_analysis_chart_image(pkg: Any, analysis_csv: Path) -> None:
    fig = None
    try:
        import matplotlib.pyplot as plt
        from ta_foundation.reports.html.sections.analysis_chart_replica import (
            _build_figure,
            _fig_to_data_uri,
            _read_analysis_csv,
        )

        rows = _read_analysis_csv(analysis_csv)
        fig = _build_figure(rows) if rows else None
        if fig is None:
            return
        pkg.metadata.setdefault("derived", {})["analysis_image_uri"] = _fig_to_data_uri(fig)
        pkg.metadata["derived"]["analysis_image_source"] = "generated_from_analysis_csv"
    except Exception as exc:
        pkg.warnings.append({
            "code": "ANALYSIS_CARD_IMAGE_FAILED",
            "message": f"Failed to generate analysis image from {analysis_csv.name}: {exc}",
        })
    finally:
        if fig is not None:
            try:
                plt.close(fig)
            except Exception:
                pass


def _attach_settings_table_image(pkg: Any) -> None:
    fig = None
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import pandas as pd
        from ta_foundation.reports.html.embed import fig_to_base64_png

        settings = getattr(pkg, "settings", None)
        if not isinstance(settings, pd.DataFrame) or settings.empty:
            return
        cols = [c for c in ("section", "item", "value") if c in settings.columns]
        if not cols:
            return
        df = settings.loc[:, cols].head(80).fillna("")
        fig_h = max(4.0, min(22.0, 0.24 * (len(df) + 1)))
        fig, ax = plt.subplots(figsize=(11, fig_h), facecolor="#111827")
        ax.set_facecolor("#111827")
        ax.axis("off")
        table = ax.table(
            cellText=df.astype(str).values,
            colLabels=df.columns,
            loc="center",
            cellLoc="left",
            colLoc="left",
        )
        table.auto_set_font_size(False)
        table.set_fontsize(7.5)
        table.scale(1, 1.25)
        for (row, _col), cell in table.get_celld().items():
            cell.set_edgecolor("#374151")
            cell.set_text_props(color="#f3f4f6")
            cell.set_facecolor("#1f2937" if row == 0 else "#111827")
        pkg.metadata.setdefault("derived", {})["summery_image_uri"] = fig_to_base64_png(fig)
        pkg.metadata["derived"]["summery_image_source"] = "generated_from_settings_csv"
    except Exception as exc:
        pkg.warnings.append({
            "code": "SETTINGS_CARD_IMAGE_FAILED",
            "message": f"Failed to generate settings image: {exc}",
        })
    finally:
        if fig is not None:
            try:
                plt.close(fig)
            except Exception:
                pass


def _resolve_candidate_results_dir(pkg_dir: Path, run_id: str) -> Path | None:
    """Look up ``run_id``'s NT result folder, trying final then promoted.

    Both handoff layouts use ``<root>/nt8_backtest_results/<run_id>/`` so a
    single search across both works for F_NNN finalists and P_NNN promoted
    rows. Returns ``None`` when neither layout has a matching folder.
    """
    candidates = [
        pkg_dir / "final_backtest_handoff" / "nt8_backtest_results" / run_id,
        pkg_dir / "promoted_handoff" / "nt8_backtest_results" / run_id,
    ]
    for path in candidates:
        if path.exists():
            return path
    return None


def _resolve_images_dir(images_dir: Path | str | None) -> str | None:
    if images_dir:
        return str(images_dir)
    defaults = [str(path) for path in DEFAULT_PORTRAIT_DIRS if path.exists() and path.is_dir()]
    return "\n".join(defaults) if defaults else None


def _find_template_path_for_run_id(
    session: OptimizerSession, run_id: str,
) -> Path | None:
    """Find the template XML that produced this run.

    For F_NNN finalists: templates are named like
    ``01_Breakout_PantheonMasterBotV01TesterV2.xml`` where the leading
    number maps to ``F_<num:03d>``. If the operator has run the Decision
    page final-template renamer, prefer its indexed
    ``F_xxx__semantic.xml`` output.

    For P_NNN promoted rows: templates are stamped under
    ``generated_templates/promoted/<P_NNN>.xml`` and mirrored to
    ``deployment_package/promoted_handoff/named_backtest_templates/recipe/<P_NNN>.xml``.
    """
    if run_id.startswith("P_"):
        return _find_promoted_template_path(session, run_id)

    from ta_foundation.web.optimizer_final_templates import find_renamed_template_for_run_id

    renamed = find_renamed_template_for_run_id(session, run_id)
    if renamed is not None:
        return renamed

    named_dir = (session.directory / "deployment_package" / "final_backtest_handoff"
                 / "named_backtest_templates")
    if not named_dir.exists():
        return None
    suffix_num = _run_id_to_number(run_id)
    if suffix_num is None:
        return None
    for xml in sorted(named_dir.rglob("*.xml")):
        stem_run_id = _template_run_id(xml)
        if stem_run_id == run_id:
            return xml
    return None


def _find_promoted_template_path(
    session: OptimizerSession, run_id: str,
) -> Path | None:
    """Return the on-disk XML for a P_NNN promoted run, preferring the
    handoff mirror so the banner shows the same file the operator runs."""
    candidates = [
        session.directory / "deployment_package" / "promoted_handoff"
        / "named_backtest_templates" / "recipe" / f"{run_id}.xml",
        session.directory / "generated_templates" / "promoted" / f"{run_id}.xml",
    ]
    for path in candidates:
        if path.exists():
            return path
    return None


def _run_id_to_number(run_id: str) -> int | None:
    if not run_id.startswith("F_"):
        return None
    try:
        return int(run_id[2:])
    except ValueError:
        return None


def _template_run_id(path: Path) -> str | None:
    stem = path.stem
    if not stem:
        return None
    match = re.match(r"^(?P<prefix>[FP])_(?P<number>\d{3})(?:_|$)", stem, flags=re.IGNORECASE)
    if match:
        return f"{match.group('prefix').upper()}_{int(match.group('number')):03d}"
    head = stem.split("_", 1)[0]
    try:
        number = int(head)
    except ValueError:
        return None
    return f"F_{number:03d}"


def _bucket_index_for_section(section_id: str) -> int:
    for index, (_bucket_id, _title, _description, patterns) in enumerate(SECTION_BUCKET_RULES):
        for pattern in patterns:
            if _section_id_matches_pattern(section_id, pattern):
                return index
    return 0


def _section_id_matches_pattern(section_id: str, pattern: str) -> bool:
    if pattern.startswith("*") and pattern.endswith("*"):
        return pattern.strip("*") in section_id
    if pattern.startswith("*"):
        return section_id.endswith(pattern[1:])
    if pattern.endswith("*"):
        return section_id.startswith(pattern[:-1])
    return section_id == pattern
