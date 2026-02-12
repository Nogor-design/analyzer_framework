from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ta_foundation.reports.html.sections.daily_scoreboard import render_daily_scoreboard
from ta_foundation.reports.html.sections.comparison_overview import render_comparison_overview
from ta_foundation.reports.html.sections.equity_curve import render_equity_curve_all_runs
from ta_foundation.reports.html.sections.run_kpis import render_run_kpis
from ta_foundation.reports.html.sections.run_snapshot_clipboard import render_run_snapshot_clipboard
from ta_foundation.reports.html.sections.run_settings_table import render_run_settings_table
from ta_foundation.reports.html.sections.run_metadata import render_run_metadata_cards
from ta_foundation.reports.html.sections.run_executive_profile_cards import (
    render_run_executive_profile_cards,
)
from ta_foundation.reports.html.sections.run_card_catalog import render_run_card_catalog
from ta_foundation.reports.html.sections.daily_leaderboard_cards import render_daily_leaderboard_cards
from ta_foundation.reports.html.sections.weekly_leaderboard_cards import render_weekly_leaderboard_cards
from ta_foundation.reports.html.sections.trades_intraday_pnl_by_day import (
    render_trades_intraday_pnl_by_day,
)
from ta_foundation.reports.html.sections.trade_candle_overlay import render_trade_candle_overlay
from ta_foundation.reports.html.sections.apex_drawdown_survival_profile import render_apex_drawdown_survival_profile


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
    "daily_scoreboard": SectionDef(
        id="daily_scoreboard",
        default_title="Daily Scoreboard",
        render_fn=render_daily_scoreboard,
    ),
    "run_card_catalog": SectionDef(
        id="run_card_catalog",
        default_title="Run Card Catalog",
        render_fn=render_run_card_catalog,
    ),
    "daily_leaderboard_cards": SectionDef(
        id="daily_leaderboard_cards",
        default_title="Daily Leaders (Session Winners)",
        render_fn=render_daily_leaderboard_cards,
    ),
    "weekly_leaderboard_cards": SectionDef(
        id="weekly_leaderboard_cards",
        default_title="Weekly Leaders",
        render_fn=render_weekly_leaderboard_cards,
    ),
    "trades_intraday_pnl_by_day": SectionDef(
        id="trades_intraday_pnl_by_day",
        default_title="Intraday Trade PnL by Day (MFE Overlay)",
        render_fn=render_trades_intraday_pnl_by_day,
    ),
    "trade_candle_overlay": SectionDef(
        id="trade_candle_overlay",
        default_title="Trades on Candles",
        render_fn=render_trade_candle_overlay,
    ),
    "apex_drawdown_survival_profile": SectionDef(
        id="apex_drawdown_survival_profile",
        default_title="apex drawdown",
        render_fn=render_apex_drawdown_survival_profile,
    ),

}

