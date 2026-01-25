from __future__ import annotations

from ta_foundation.reports.html.builder import HtmlReportBuilder, HtmlSection
from ta_foundation.reports.html.sections.comparison_overview import render_comparison_overview
from ta_foundation.reports.html.sections.equity_curve import render_equity_curve_all_runs
from ta_foundation.reports.html.sections.run_kpis import render_run_kpis
from ta_foundation.reports.html.sections.run_metadata import render_run_metadata_cards
from ta_foundation.reports.html.sections.drawdown_curve import render_drawdown_curve


def build_comparison_report(packages: dict, report_title: str = "Strategy Comparison Report") -> str:
    builder = HtmlReportBuilder(
        report_title=report_title,
        sections=[
            HtmlSection("Comparison Overview", render_comparison_overview),
            HtmlSection("Equity Curve Comparison", render_equity_curve_all_runs),
            HtmlSection("Run Metadata Cards", render_run_metadata_cards),
            HtmlSection("Run KPI Cards", render_run_kpis),
            HtmlSection("Drawdown Curve", render_drawdown_curve),
        ],
    )
    ctx = {"packages": packages}
    return builder.build(ctx)
