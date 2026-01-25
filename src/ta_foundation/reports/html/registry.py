from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ta_foundation.reports.html.sections.comparison_overview import render_comparison_overview
from ta_foundation.reports.html.sections.equity_curve import render_equity_curve_all_runs
from ta_foundation.reports.html.sections.run_kpis import render_run_kpis
from ta_foundation.reports.html.sections.run_metadata import render_run_metadata_cards
from ta_foundation.reports.html.sections.drawdown_curve import render_drawdown_curve

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
    "run_metadata_cards": SectionDef(
        id="run_metadata_cards",
        default_title="Run Metadata Cards",
        render_fn=render_run_metadata_cards,
    ),
    "run_kpi_cards": SectionDef(
        id="run_kpi_cards",
        default_title="Run KPI Cards",
        render_fn=render_run_kpis,
    ),
    "drawdown_curve": SectionDef(
        id="drawdown_curve",
        default_title="Drawdown Curve",
        render_fn=render_drawdown_curve,
    ),
}
