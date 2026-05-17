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
from pathlib import Path
from typing import Any

from ta_foundation.core.pipeline import ingest_folder
from ta_foundation.core.registry import ParserRegistry
from ta_foundation.parsers.ninjatrader.analysis_by_day_csv import NinjaTraderDailyAnalysisCsvParser
from ta_foundation.parsers.ninjatrader.optimization_csv import NinjaTraderOptimizationCsvParser
from ta_foundation.parsers.ninjatrader.settings_csv import NinjaTraderSettingsCsvParser
from ta_foundation.parsers.ninjatrader.summary_csv import NinjaTraderSummaryCsvParser
from ta_foundation.parsers.ninjatrader.trades_csv import NinjaTraderTradesCsvParser
from ta_foundation.reports.html.builder import HtmlReportBuilder, HtmlSection
from ta_foundation.reports.html.registry import SECTION_REGISTRY
from ta_foundation.web.optimizer_session import OptimizerSession


PER_CANDIDATE_REPORTS_DIRNAME = "per_candidate_reports"

DEFAULT_FINALIST_SECTIONS: list[str] = [
    "exec_card_god_banner",
    "run_kpi_cards",
    "analysis_chart_replica",
    "run_metadata_cards",
    "daily_scoreboard",
    "daily_winner_spotlight",
    "daily_leaderboard_cards",
    "run_settings_table",
]


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


def group_sections_by_bucket() -> list[dict[str, Any]]:
    """Return report sections grouped for the on-demand picker page."""
    buckets: list[dict[str, Any]] = [
        {"id": bucket_id, "title": title, "description": description, "sections": []}
        for bucket_id, title, description, _patterns in SECTION_BUCKET_RULES
    ]
    default_selected = set(DEFAULT_FINALIST_SECTIONS)

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
    sections: list[str] | None = None,
    images_dir: Path | str | None = None,
    output_dir: Path | None = None,
) -> CandidateReportResult:
    """Build a single per-candidate HTML report.

    Parameters
    ----------
    session : OptimizerSession
        The optimizer session whose final-Backtest results live on disk.
    run_id : str
        Candidate id (e.g. ``F_001``).
    sections : list[str] | None
        Section ids in render order. ``None`` uses
        :data:`DEFAULT_FINALIST_SECTIONS`. Unknown section ids are
        silently skipped with a note.
    images_dir : Path | str | None
        Portrait images directory for the god/monster banner. ``None``
        means the banner renders the decoded name only, no portrait.
    output_dir : Path | None
        Where to write the HTML. Defaults to
        ``<session>/deployment_package/per_candidate_reports/``.
    """
    pkg_dir = session.directory / "deployment_package"
    candidate_dir = pkg_dir / "final_backtest_handoff" / "nt8_backtest_results" / run_id
    if not candidate_dir.exists():
        raise CandidateReportError(
            f"No backtest results for {run_id} at {candidate_dir}"
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

    base_options: dict[str, Any] = {
        "run_id": run_id,
        "label": pkg.summary.kpis_all.get("strategy") if pkg.summary else run_id,
        "analysis_csv_path": str(analysis_csv) if analysis_csv.exists() else None,
        "template_path": str(template_path) if template_path else None,
        "images_dir": str(images_dir) if images_dir else None,
        "market_suffix": market_root or None,
    }
    if not analysis_csv.exists():
        notes.append(f"Analysis.csv not present for {run_id}; chart-replica section will degrade.")
    if template_path is None:
        notes.append(f"No matching template for {run_id}; banner will fall back to plain text.")

    html_sections: list[HtmlSection] = []
    rendered: list[str] = []
    for sec_id in requested:
        section_def = SECTION_REGISTRY.get(sec_id)
        if section_def is None:
            notes.append(f"Unknown section id: {sec_id}")
            continue
        html_sections.append(HtmlSection(
            id=section_def.id,
            title=section_def.default_title,
            render_fn=section_def.render_fn,
            options=dict(base_options),
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
    out_path.write_text(html, encoding="utf-8")

    return CandidateReportResult(
        run_id=run_id,
        html_path=str(out_path),
        sections_rendered=rendered,
        notes=notes,
    )


def build_all_candidate_reports(
    session: OptimizerSession,
    *,
    sections: list[str] | None = None,
    images_dir: Path | str | None = None,
    purge_existing: bool = True,
) -> CandidateReportBatchResult:
    """Build a report for every candidate in the final-Backtest results dir.

    ``purge_existing=True`` (default) removes any previous per-candidate
    HTML so stale reports don't survive a rebuild. The output directory
    itself is preserved.
    """
    pkg_dir = session.directory / "deployment_package"
    results_dir = pkg_dir / "final_backtest_handoff" / "nt8_backtest_results"
    out_dir = pkg_dir / PER_CANDIDATE_REPORTS_DIRNAME

    notes: list[str] = []
    if not results_dir.exists():
        return CandidateReportBatchResult(
            session_id=session.id,
            output_dir=str(out_dir),
            per_candidate=[],
            notes=[f"No final-Backtest results at {results_dir}; nothing to render."],
        )

    if purge_existing and out_dir.exists():
        for child in out_dir.glob("*.html"):
            try:
                child.unlink()
            except OSError:
                pass

    candidate_dirs = sorted(p for p in results_dir.iterdir() if p.is_dir())
    per: list[CandidateReportResult] = []
    for cand in candidate_dirs:
        run_id = cand.name
        try:
            result = build_candidate_report(
                session, run_id, sections=sections, images_dir=images_dir,
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


def _find_template_path_for_run_id(
    session: OptimizerSession, run_id: str,
) -> Path | None:
    """Find the named_backtest_template XML that produced this run.

    Templates are named like ``01_Breakout_PantheonMasterBotV01TesterV2.xml``
    where the leading number maps to ``F_<num:03d>``. The first match
    by leading-number is returned.
    """
    named_dir = (session.directory / "deployment_package" / "final_backtest_handoff"
                 / "named_backtest_templates")
    if not named_dir.exists():
        return None
    suffix_num = _run_id_to_number(run_id)
    if suffix_num is None:
        return None
    for xml in sorted(named_dir.rglob("*.xml")):
        stem = xml.stem
        if not stem:
            continue
        head = stem.split("_", 1)[0]
        try:
            n = int(head)
        except ValueError:
            continue
        if n == suffix_num:
            return xml
    return None


def _run_id_to_number(run_id: str) -> int | None:
    if not run_id.startswith("F_"):
        return None
    try:
        return int(run_id[2:])
    except ValueError:
        return None


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
