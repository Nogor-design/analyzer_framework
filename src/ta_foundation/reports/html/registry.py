# src/ta_foundation/reports/html/registry.py

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
from ta_foundation.reports.html.sections.tick_data_diagnostics import render_tick_data_diagnostics
from ta_foundation.reports.html.sections.filter_discovery import render_filter_discovery
from ta_foundation.reports.html.sections.exit_policy_simulation import render_exit_policy_simulation
from ta_foundation.reports.html.sections.exit_policy_simulation2 import render_exit_policy_simulation2
from ta_foundation.reports.html.sections.exit_policy_trade_debug import render_exit_policy_trade_debug
from ta_foundation.reports.html.sections.pattern_engine_diagnostics import render_pattern_engine_diagnostics
from ta_foundation.reports.html.sections.pattern_market_discovery import render_pattern_market_discovery
from ta_foundation.reports.html.sections.pattern_engine_mc_regime  import render_pattern_engine_mc_regime
from ta_foundation.reports.html.sections.market_regime_discovery import render_market_regime_discovery
from ta_foundation.reports.html.sections.anchor_interaction_overview import (
    render_anchor_interaction_overview,
)
from ta_foundation.reports.html.sections.anchor_tp_sl_recommendations import render_anchor_tp_sl_recommendations
#render_anchor_tp_sl_recommendations
from ta_foundation.reports.html.sections.anchor_interaction_config import (
    render_anchor_interaction_config,
)

from ta_foundation.reports.html.sections.anchor_interaction_overview import (
    render_anchor_interaction_overview,
)
from ta_foundation.reports.html.sections.anchor_interaction_anchor_matrix import (
    render_anchor_interaction_anchor_matrix,
)
from ta_foundation.reports.html.sections.anchor_interaction_tp_sl_spec import (
    render_anchor_interaction_tp_sl_spec,
)
from ta_foundation.reports.html.sections.anchor_interaction_diagnostics import (
    render_anchor_interaction_diagnostics,
)
from ta_foundation.reports.html.sections.anchor_tp_sl_recommendations import (
    render_anchor_tp_sl_recommendations,
)
from ta_foundation.reports.html.sections.anchor_interaction_hourly_profile import (
    render_anchor_interaction_hourly_profile,
)
# render_pattern_engine_diagnostics
# render_pattern_market_discovery
# NEW
from ta_foundation.reports.html.sections.pattern_engine_overview import (
    render_pattern_engine_overview,
)
from ta_foundation.reports.html.sections.pattern_cluster_drilldown import (
    render_pattern_cluster_drilldown,
)
from ta_foundation.reports.html.sections.trade_pattern_audit import (
    render_trade_pattern_audit,
)
from ta_foundation.reports.html.sections.strategy_discovery_overview import (
    render_strategy_discovery_overview,
)
from ta_foundation.reports.html.sections.strategy_discovery_ranked_table import (
    render_strategy_discovery_ranked_table,
)
from ta_foundation.reports.html.sections.strategy_discovery_entry_rules import (
    render_strategy_discovery_entry_rules,
)
from ta_foundation.reports.html.sections.strategy_discovery_comparison import (
    render_strategy_discovery_comparison,
)
from ta_foundation.reports.html.sections.strategy_discovery_filter_rules import (
    render_strategy_discovery_filter_rules,
)
from ta_foundation.reports.html.sections.strategy_discovery_exit_policies import (
    render_strategy_discovery_exit_policies,
)
from ta_foundation.reports.html.sections.strategy_discovery_feature_importance import (
    render_strategy_discovery_feature_importance,
)
from ta_foundation.reports.html.sections.strategy_discovery_validation import (
    render_strategy_discovery_validation,
)
from ta_foundation.reports.html.sections.strategy_discovery_mae_mfe import (
    render_strategy_discovery_mae_mfe,
)
from ta_foundation.reports.html.sections.strategy_discovery_position_sizing import (
    render_strategy_discovery_position_sizing,
)
from ta_foundation.reports.html.sections.strategy_discovery_risk_metrics import (
    render_strategy_discovery_risk_metrics,
)
from ta_foundation.reports.html.sections.strategy_discovery_drawdown import (
    render_strategy_discovery_drawdown,
)
from ta_foundation.reports.html.sections.strategy_discovery_cohort import (
    render_strategy_discovery_cohort,
)
from ta_foundation.reports.html.sections.strategy_discovery_classification import (
    render_strategy_discovery_classification,
)
from ta_foundation.reports.html.sections.strategy_discovery_evaluation import (
    render_strategy_discovery_evaluation,
)
from ta_foundation.reports.html.sections.strategy_discovery_regime import (
    render_strategy_discovery_regime,
)
from ta_foundation.reports.html.sections.strategy_discovery_parameter_sensitivity import (
    render_strategy_discovery_parameter_sensitivity,
)

SectionRenderer = Callable[[dict], str]


@dataclass(frozen=True)
class SectionDef:
    id: str
    default_title: str
    render_fn: SectionRenderer


SECTION_REGISTRY: dict[str, SectionDef] = {
    # --- existing sections ---
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
        default_title="Apex Drawdown Survival",
        render_fn=render_apex_drawdown_survival_profile,
    ),
    "tick_data_diagnostics": SectionDef(
        id="tick_data_diagnostics",
        default_title="Tick Data Diagnostics",
        render_fn=render_tick_data_diagnostics,
    ),
    "filter_discovery": SectionDef(
        id="filter_discovery",
        default_title="Filter Discovery",
        render_fn=render_filter_discovery,
    ),
    "exit_policy_simulation": SectionDef(
        id="exit_policy_simulation",
        default_title="Exit Policy Simulation",
        render_fn=render_exit_policy_simulation,
    ),
    "exit_policy_trade_debug": SectionDef(
        id="exit_policy_trade_debug",
        default_title="Exit Policy Debug",
        render_fn=render_exit_policy_trade_debug,
    ),
    "exit_policy_simulation2": SectionDef(
        id="exit_policy_simulation2",
        default_title="Exit Policy Simulation 2",
        render_fn=render_exit_policy_simulation2,
    ),

    # --- NEW: Pattern Engine ---
    "pattern_engine_overview": SectionDef(
        id="pattern_engine_overview",
        default_title="Pattern Engine Overview",
        render_fn=render_pattern_engine_overview,
    ),
    "pattern_cluster_drilldown": SectionDef(
        id="pattern_cluster_drilldown",
        default_title="Pattern Cluster Drilldown",
        render_fn=render_pattern_cluster_drilldown,
    ),
    "pattern_engine_diagnostics": SectionDef(
        id="pattern_engine_diagnostics",
        default_title="Pattern Diagnostic",
        render_fn=render_pattern_engine_diagnostics,
    ),
    "pattern_market_discovery": SectionDef(
        id="pattern_market_discovery",
        default_title="Pattern Discovery",
        render_fn=render_pattern_market_discovery,
    ),
    "pattern_engine_mc_regime": SectionDef(
        id="pattern_engine_mc_regime",
        default_title="Pattern Engine MC",
        render_fn=render_pattern_engine_mc_regime,
    ),
    "market_regime_discovery": SectionDef(
        id="market_regime_discovery",
        default_title="Market Regime Discovery",
        render_fn=render_market_regime_discovery,
    ),
    "anchor_interaction_overview": SectionDef(
        id="anchor_interaction_overview",
        default_title="Anchor Interaction Overview",
        render_fn=render_anchor_interaction_overview,
    ),
    "anchor_interaction_config": SectionDef(
        id="anchor_interaction_config",
        default_title="MA Anchor Configuration",
        render_fn=render_anchor_interaction_config,
    ),
    "anchor_interaction_overview": SectionDef(
        id="anchor_interaction_overview",
        default_title="MA Anchor Overview",
        render_fn=render_anchor_interaction_overview,
    ),
    "anchor_interaction_anchor_matrix": SectionDef(
        id="anchor_interaction_anchor_matrix",
        default_title="MA Anchor Matrix",
        render_fn=render_anchor_interaction_anchor_matrix,
    ),
    "anchor_interaction_tp_sl_spec": SectionDef(
        id="anchor_interaction_tp_sl_spec",
        default_title="MA Anchor TP/SL Specification",
        render_fn=render_anchor_interaction_tp_sl_spec,
    ),
    "anchor_interaction_diagnostics": SectionDef(
        id="anchor_interaction_diagnostics",
        default_title="MA Anchor Diagnostics",
        render_fn=render_anchor_interaction_diagnostics,
    ),
    "anchor_tp_sl_recommendations": SectionDef(
        id="anchor_tp_sl_recommendations",
        default_title="MA Anchor TP/SL Recommendations",
        render_fn=render_anchor_tp_sl_recommendations,
    ),
    "anchor_interaction_hourly_profile": SectionDef(
        id="anchor_interaction_hourly_profile",
        default_title="TP/SL by Hour of Day",
        render_fn=render_anchor_interaction_hourly_profile,
    ),

    # --- Trade Pattern Audit ---
    "trade_pattern_audit": SectionDef(
        id="trade_pattern_audit",
        default_title="Trade Pattern Audit",
        render_fn=render_trade_pattern_audit,
    ),

    # --- Strategy Discovery ---
    "strategy_discovery_overview": SectionDef(
        id="strategy_discovery_overview",
        default_title="Strategy Discovery Overview",
        render_fn=render_strategy_discovery_overview,
    ),
    "strategy_discovery_ranked_table": SectionDef(
        id="strategy_discovery_ranked_table",
        default_title="Strategy Discovery — Ranked Table",
        render_fn=render_strategy_discovery_ranked_table,
    ),
    "strategy_discovery_entry_rules": SectionDef(
        id="strategy_discovery_entry_rules",
        default_title="Strategy Discovery — Entry Rules",
        render_fn=render_strategy_discovery_entry_rules,
    ),
    "strategy_discovery_comparison": SectionDef(
        id="strategy_discovery_comparison",
        default_title="Strategy Discovery — Cross-Run Comparison",
        render_fn=render_strategy_discovery_comparison,
    ),
    "strategy_discovery_filter_rules": SectionDef(
        id="strategy_discovery_filter_rules",
        default_title="Strategy Discovery — Filter Rules",
        render_fn=render_strategy_discovery_filter_rules,
    ),
    "strategy_discovery_exit_policies": SectionDef(
        id="strategy_discovery_exit_policies",
        default_title="Strategy Discovery — Exit Policies",
        render_fn=render_strategy_discovery_exit_policies,
    ),
    "strategy_discovery_feature_importance": SectionDef(
        id="strategy_discovery_feature_importance",
        default_title="Strategy Discovery — Feature Importance",
        render_fn=render_strategy_discovery_feature_importance,
    ),
    "strategy_discovery_validation": SectionDef(
        id="strategy_discovery_validation",
        default_title="Strategy Discovery — Walk-Forward Validation",
        render_fn=render_strategy_discovery_validation,
    ),
    "strategy_discovery_mae_mfe": SectionDef(
        id="strategy_discovery_mae_mfe",
        default_title="Strategy Discovery — MAE/MFE Profile",
        render_fn=render_strategy_discovery_mae_mfe,
    ),
    "strategy_discovery_position_sizing": SectionDef(
        id="strategy_discovery_position_sizing",
        default_title="Strategy Discovery — Position Sizing",
        render_fn=render_strategy_discovery_position_sizing,
    ),
    "strategy_discovery_risk_metrics": SectionDef(
        id="strategy_discovery_risk_metrics",
        default_title="Strategy Discovery — Risk-Adjusted Metrics",
        render_fn=render_strategy_discovery_risk_metrics,
    ),
    "strategy_discovery_drawdown": SectionDef(
        id="strategy_discovery_drawdown",
        default_title="Strategy Discovery — Drawdown Analysis",
        render_fn=render_strategy_discovery_drawdown,
    ),
    "strategy_discovery_cohort": SectionDef(
        id="strategy_discovery_cohort",
        default_title="Strategy Discovery — Cohort Analysis (Drift / Decay)",
        render_fn=render_strategy_discovery_cohort,
    ),
    "strategy_discovery_classification": SectionDef(
        id="strategy_discovery_classification",
        default_title="Strategy Discovery — Automation Classification",
        render_fn=render_strategy_discovery_classification,
    ),
    "strategy_discovery_evaluation": SectionDef(
        id="strategy_discovery_evaluation",
        default_title="Strategy Discovery — Trade Evaluation",
        render_fn=render_strategy_discovery_evaluation,
    ),
    "strategy_discovery_regime": SectionDef(
        id="strategy_discovery_regime",
        default_title="Strategy Discovery — Market Regime",
        render_fn=render_strategy_discovery_regime,
    ),
    "strategy_discovery_parameter_sensitivity": SectionDef(
        id="strategy_discovery_parameter_sensitivity",
        default_title="Strategy Discovery — Parameter Sensitivity",
        render_fn=render_strategy_discovery_parameter_sensitivity,
    ),
# render_pattern_engine_diagnostics
}