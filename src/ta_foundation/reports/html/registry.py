from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ta_foundation.reports.html.sections.comparison_overview import render_comparison_overview
from ta_foundation.reports.html.sections.equity_curve import render_equity_curve_all_runs
from ta_foundation.reports.html.sections.run_kpis import render_run_kpis
from ta_foundation.reports.html.sections.run_snapshot_clipboard import render_run_snapshot_clipboard
from ta_foundation.reports.html.sections.run_settings_table import render_run_settings_table
from ta_foundation.reports.html.sections.run_metadata import render_run_metadata_cards
from ta_foundation.reports.html.sections.run_executive_profile_cards import (
    render_run_executive_profile_cards,
)

SectionRenderer = Callable[[dict], str]


@dataclass(frozen=True)
class SectionDef:
    id: str
    default_title: str
    render_fn: SectionRenderer


SECTION_REGISTRY: dict[str, SectionDef] = {
    "comparison_overview": SectionDef(
        id="comparison_overview",
        default_title="Comparison Overview",
        render_fn=render_comparison_overview,
    ),
    "equity_curve_comparison": SectionDef(
        id="equity_curve_comparison",
        default_title="Equity Curve Comparison",
        render_fn=render_equity_curve_all_runs,
    ),
    "run_kpi_cards": SectionDef(
        id="run_kpi_cards",
        default_title="Run KPI Cards",
        render_fn=render_run_kpis,
    ),
    "run_snapshot_clipboard": SectionDef(
        id="run_snapshot_clipboard",
        default_title="snapshot",
        render_fn=render_run_snapshot_clipboard,
    ),
    "run_settings_table": SectionDef(
        id="run_settings_table",
        default_title="Run Settings",
        render_fn=render_run_settings_table,
    ),

    "run_metadata_cards": SectionDef(
        id="run_metadata_cards",
        default_title="Run Metadata Cards",
        render_fn=render_run_metadata_cards,
    ),
    "run_executive_profile_cards": SectionDef(
        id="run_executive_profile_cards",
        default_title="Executive Strategy Profiles",
        render_fn=render_run_executive_profile_cards,
    ),

}

# from __future__ import annotations
#
# from typing import Any, Callable
#
# # NOTE:
# # This registry is the single source of truth for allowed section IDs in report.yaml.
# # build_report_from_config() raises KeyError if a YAML section id is not present here.
#
# RenderFn = Callable[[dict[str, Any]], str]
#
# # --- Core sections (existing) ---
# from ta_foundation.reports.html.sections.comparison_overview import render_comparison_overview
# from ta_foundation.reports.html.sections.equity_curve import render_equity_curve_all_runs
# from ta_foundation.reports.html.sections.run_metadata import render_run_metadata_cards
# from ta_foundation.reports.html.sections.run_kpis import render_run_kpis
#
# # --- New section (this chat) ---
# # If you have not yet added this file, either add it first or comment out the import temporarily.
# from ta_foundation.reports.html.sections.run_snapshot_clipboard import render_run_snapshot_clipboard
#
#
# SECTION_REGISTRY: dict[str, RenderFn] = {
#     # Comparison / summary
#     "comparison_overview": render_comparison_overview,
#
#     # Charts
#     "equity_curve_comparison": render_equity_curve_all_runs,
#
#     # Per-run cards
#     "run_metadata_cards": render_run_metadata_cards,
#     "run_kpi_cards": render_run_kpis,
#
#     # Copy/paste friendly per-run snapshot table (Google Slides friendly)
#     "run_snapshot_clipboard": render_run_snapshot_clipboard,
# }

