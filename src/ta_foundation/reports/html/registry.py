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
from ta_foundation.reports.html.sections.strategy_discovery_nt_template import (
    render_strategy_discovery_nt_template,
)
from ta_foundation.reports.html.sections.strategy_discovery_signal_entries import (
    render_strategy_discovery_signal_entries,
)
from ta_foundation.reports.html.sections.strategy_discovery_signal_validation import (
    render_strategy_discovery_signal_validation,
)
from ta_foundation.reports.html.sections.strategy_discovery_signal_exit_sweep import (
    render_strategy_discovery_signal_exit_sweep,
)
from ta_foundation.reports.html.sections.strategy_discovery_signal_simulation import (
    render_strategy_discovery_signal_simulation,
)
from ta_foundation.reports.html.sections.strategy_discovery_anchor_confluence import (
    render_strategy_discovery_anchor_confluence,
)
from ta_foundation.reports.html.sections.regime_parameter_recommendation import render_regime_parameter_recommendation
from ta_foundation.reports.html.sections.strategy_discovery_decision_ledger import (
    render_strategy_discovery_decision_ledger,
)
from ta_foundation.reports.html.sections.strategy_discovery_combo_basket import (
    render_strategy_discovery_combo_basket,
)
from ta_foundation.reports.html.sections.candle_discovery_overview import (
    render_candle_discovery_overview,
)
from ta_foundation.reports.html.sections.candle_discovery_ranking import (
    render_candle_discovery_ranking,
)
from ta_foundation.reports.html.sections.ma_discovery_overview import (
    render_ma_discovery_overview,
)
from ta_foundation.reports.html.sections.orb_discovery_overview import (
    render_orb_discovery_overview,
)
from ta_foundation.reports.html.sections.premarket_discovery_overview import (
    render_premarket_discovery_overview,
)
from ta_foundation.reports.html.sections.bb_discovery_overview import (
    render_bb_discovery_overview,
)
from ta_foundation.reports.html.sections.strategy_discovery_unified import (
    render_strategy_discovery_unified,
)
from ta_foundation.reports.html.sections.lcr_discovery_overview import (
    render_lcr_discovery_overview,
)
from ta_foundation.reports.html.sections.optimization_overview import (
    render_optimization_overview,
)
from ta_foundation.reports.html.sections.large_candle_excursion_summary import (
    render_large_candle_excursion_summary,
)
from ta_foundation.reports.html.sections.large_candle_excursion_distributions import (
    render_large_candle_excursion_distributions,
)
from ta_foundation.reports.html.sections.large_candle_excursion_threshold_hits import (
    render_large_candle_excursion_threshold_hits,
)
from ta_foundation.reports.html.sections.large_candle_excursion_event_table import (
    render_large_candle_excursion_event_table,
)
from ta_foundation.reports.html.sections.large_candle_excursion_methodology import (
    render_large_candle_excursion_methodology,
)
from ta_foundation.reports.html.sections.large_candle_excursion_trade_summary import (
    render_large_candle_excursion_trade_summary,
)
from ta_foundation.reports.html.sections.large_candle_excursion_trade_comparison import (
    render_large_candle_excursion_trade_comparison,
)
from ta_foundation.reports.html.sections.large_candle_excursion_trade_event_table import (
    render_large_candle_excursion_trade_event_table,
)
from ta_foundation.reports.html.sections.large_candle_excursion_volume_context import (
    render_large_candle_excursion_volume_context,
)
from ta_foundation.reports.html.sections.large_candle_excursion_structure_context import (
    render_large_candle_excursion_structure_context,
)
from ta_foundation.reports.html.sections.large_candle_excursion_volatility_context import (
    render_large_candle_excursion_volatility_context,
)
from ta_foundation.reports.html.sections.large_candle_excursion_interactions import (
    render_large_candle_excursion_interactions,
)
from ta_foundation.reports.html.sections.large_candle_excursion_findings_executive_summary import (
    render_large_candle_excursion_findings_executive_summary,
)
from ta_foundation.reports.html.sections.large_candle_excursion_findings_top_discoveries import (
    render_large_candle_excursion_findings_top_discoveries,
)
from ta_foundation.reports.html.sections.large_candle_excursion_findings_interactions import (
    render_large_candle_excursion_findings_interactions,
)
from ta_foundation.reports.html.sections.large_candle_excursion_findings_fragility import (
    render_large_candle_excursion_findings_fragility,
)
from ta_foundation.reports.html.sections.large_candle_excursion_findings_next_tests import (
    render_large_candle_excursion_findings_next_tests,
)
from ta_foundation.reports.html.sections.large_candle_excursion_findings_methodology import (
    render_large_candle_excursion_findings_methodology,
)
from ta_foundation.reports.html.sections.large_candle_excursion_findings_interpretation import (
    render_large_candle_excursion_findings_interpretation,
)
from ta_foundation.reports.html.sections.large_candle_excursion_findings_families import (
    render_large_candle_excursion_findings_families,
)
from ta_foundation.reports.html.sections.large_candle_excursion_findings_decision_engine import (
    render_large_candle_excursion_findings_decision_engine,
)
from ta_foundation.reports.html.sections.large_candle_excursion_elite_reversal_setup_extractor import (
    render_large_candle_excursion_elite_reversal_setup_extractor,
)
from ta_foundation.reports.html.sections.large_candle_excursion_recursive_edge_search import (
    render_large_candle_excursion_recursive_edge_search,
)
from ta_foundation.reports.html.sections.large_candle_excursion_edge_validation_engine import (
    render_large_candle_excursion_edge_validation_engine,
)
from ta_foundation.reports.html.sections.large_candle_excursion_strategy_construction_engine import (
    render_large_candle_excursion_strategy_construction_engine,
)
from ta_foundation.reports.html.sections.large_candle_excursion_signal_context import (
    render_large_candle_excursion_signal_context,
)
from ta_foundation.reports.html.sections.large_candle_excursion_context_diagnostics import (
    render_large_candle_excursion_context_diagnostics,
)
from ta_foundation.reports.html.sections.large_candle_excursion_context_families import (
    render_large_candle_excursion_context_families,
)
from ta_foundation.reports.html.sections.large_candle_excursion_reversal_leaders import (
    render_large_candle_excursion_reversal_leaders,
)
from ta_foundation.reports.html.sections.large_candle_excursion_session_large_moves import (
    render_large_candle_excursion_session_large_moves,
)
from ta_foundation.reports.html.sections.large_candle_excursion_target_curves import (
    render_large_candle_excursion_target_curves,
)
from ta_foundation.reports.html.sections.large_candle_excursion_session_context import (
    render_large_candle_excursion_session_context,
)
from ta_foundation.reports.html.sections.large_candle_excursion_strategy_cards import (
    render_large_candle_excursion_strategy_cards,
)
from ta_foundation.reports.html.sections.large_candle_excursion_discovery_summary import (
    render_large_candle_excursion_discovery_summary,
)
from ta_foundation.reports.html.sections.large_candle_excursion_discovery_broad_scan import (
    render_large_candle_excursion_discovery_broad_scan,
)
from ta_foundation.reports.html.sections.large_candle_excursion_discovery_refinement import (
    render_large_candle_excursion_discovery_refinement,
)
from ta_foundation.reports.html.sections.large_candle_excursion_discovery_chains import (
    render_large_candle_excursion_discovery_chains,
)
from ta_foundation.reports.html.sections.large_candle_excursion_discovery_robustness import (
    render_large_candle_excursion_discovery_robustness,
)
from ta_foundation.reports.html.sections.large_candle_excursion_discovery_diagnostics import (
    render_large_candle_excursion_discovery_diagnostics,
)
from ta_foundation.reports.html.sections.large_candle_excursion_discovery_next_steps import (
    render_large_candle_excursion_discovery_next_steps,
)
from ta_foundation.reports.html.sections.large_candle_excursion_discovery_methodology import (
    render_large_candle_excursion_discovery_methodology,
)

# Shared overview renderer reused for breakout, pullback, level
def _make_generic_overview(discovery_key: str, title_label: str, accent: str):
    """Factory: returns a render function for a simple discovery overview."""
    def _render(ctx: dict) -> str:
        data = None
        if discovery_key in ctx:
            data = ctx[discovery_key]
        elif ctx.get("all_options", {}).get(discovery_key):
            data = ctx["all_options"][discovery_key]
        else:
            for pkg in (ctx.get("packages") or {}).values():
                derived = getattr(pkg, "metadata", {}).get("derived", {})
                if discovery_key in derived:
                    data = derived[discovery_key]
                    break

        if not data:
            return (f'<div style="padding:20px;background:#fff3cd;border:1px solid #ffc107;'
                    f'border-radius:4px"><strong>{title_label}:</strong> No results found. '
                    f'Enable <code>{discovery_key}.enabled: true</code> in YAML.</div>')

        results = data.get("sweep_results", [])
        n_run   = data.get("n_combinations_run", 0)
        n_res   = data.get("n_results", len(results))
        pfs     = [float(r["metrics"]["profit_factor"]) for r in results
                   if r.get("metrics", {}).get("profit_factor") is not None]
        top_pf  = round(max(pfs), 2) if pfs else 0.0
        avg_pf  = round(sum(pfs) / len(pfs), 2) if pfs else 0.0

        def _pf_color(pf):
            if pf is None: return "#f5f5f5"
            if pf >= 1.5:  return "#c6efce"
            if pf >= 1.2:  return "#e2efda"
            if pf >= 1.0:  return "#ffeb9c"
            return "#ffc7ce"

        def _stat(label, value):
            return (f'<div style="background:#fff;border-left:4px solid {accent};'
                    f'padding:10px 16px;border-radius:4px;min-width:100px">'
                    f'<div style="font-size:11px;color:#888">{label}</div>'
                    f'<div style="font-size:20px;font-weight:700;color:{accent}">{value}</div></div>')

        def _cell(v, bg):
            return (f'<td style="background:{bg};text-align:center;padding:6px 10px;'
                    f'border:1px solid #ddd">{v}</td>')

        def _hdr(t):
            return (f'<th style="background:#2c3e50;color:#fff;padding:8px 12px;'
                    f'text-align:center;border:1px solid #ddd">{t}</th>')

        # Signal × TF heatmap
        pivot: dict = {}
        for r in results:
            sid = str(r.get("signal_id", "?"))
            tf  = int(r.get("tf", 0))
            pf  = r.get("metrics", {}).get("profit_factor")
            if pf is None: continue
            pivot.setdefault(sid, {}).setdefault(tf, []).append(float(pf))

        html  = f'<div style="font-family:Arial,sans-serif">'
        html += f'<p style="color:#666;font-size:13px">{n_run:,} combos — {n_res:,} results</p>'
        html += '<div style="display:flex;gap:14px;flex-wrap:wrap;margin-bottom:18px">'
        html += _stat("Results",  str(n_res))
        html += _stat("Best PF",  str(top_pf))
        html += _stat("Avg PF",   str(avg_pf))
        html += _stat("PF ≥ 1.0", str(sum(1 for p in pfs if p >= 1.0)))
        html += _stat("PF ≥ 1.3", str(sum(1 for p in pfs if p >= 1.3)))
        html += '</div>'

        if pivot:
            all_tfs = sorted({tf for vals in pivot.values() for tf in vals})
            hdr_row = _hdr("Signal") + "".join(_hdr(f"{tf}m") for tf in all_tfs)
            body    = []
            for sid in sorted(pivot):
                row = f'<td style="font-weight:600;padding:6px 10px;border:1px solid #ddd">{sid}</td>'
                for tf in all_tfs:
                    vs = pivot[sid].get(tf)
                    if vs:
                        avg = round(sum(vs) / len(vs), 2)
                        row += _cell(str(avg), _pf_color(avg))
                    else:
                        row += _cell("—", "#f5f5f5")
                body.append(f"<tr>{row}</tr>")

            html += (f'<h3 style="color:#2c3e50;border-bottom:2px solid {accent};padding-bottom:5px">'
                     f'Signal × Timeframe — Average PF</h3>')
            html += ('<div style="overflow-x:auto"><table style="border-collapse:collapse;width:100%;font-size:13px">'
                     f'<thead><tr>{hdr_row}</tr></thead>'
                     f'<tbody>{"".join(body)}</tbody></table></div>')

        html += '</div>'
        return html

    _render.__name__ = f"render_{discovery_key}_overview"
    return _render


render_breakout_discovery_overview = _make_generic_overview(
    "breakout_discovery", "Breakout Discovery", "#e74c3c")
render_pullback_discovery_overview = _make_generic_overview(
    "pullback_discovery", "Pullback Discovery", "#1abc9c")
render_level_discovery_overview = _make_generic_overview(
    "level_discovery", "Level Discovery", "#f39c12")

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
    "regime_parameter_recommendation": SectionDef(
        id="regime_parameter_recommendation",
        default_title="Regime Parameter Recommendation",
        render_fn=render_regime_parameter_recommendation,
    ),
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
    "strategy_discovery_nt_template": SectionDef(
        id="strategy_discovery_nt_template",
        default_title="Strategy Discovery — NinjaTrader Template",
        render_fn=render_strategy_discovery_nt_template,
    ),
    "strategy_discovery_signal_entries": SectionDef(
        id="strategy_discovery_signal_entries",
        default_title="Strategy Discovery — Signal Entry Discovery",
        render_fn=render_strategy_discovery_signal_entries,
    ),
    "strategy_discovery_signal_validation": SectionDef(
        id="strategy_discovery_signal_validation",
        default_title="Strategy Discovery — Signal Rule Walk-Forward Validation",
        render_fn=render_strategy_discovery_signal_validation,
    ),
    "strategy_discovery_signal_exit_sweep": SectionDef(
        id="strategy_discovery_signal_exit_sweep",
        default_title="Strategy Discovery — Signal Corpus Exit Parameter Sweep",
        render_fn=render_strategy_discovery_signal_exit_sweep,
    ),
    "strategy_discovery_signal_simulation": SectionDef(
        id="strategy_discovery_signal_simulation",
        default_title="Strategy Discovery — Signal Corpus P&L Simulation",
        render_fn=render_strategy_discovery_signal_simulation,
    ),
    "strategy_discovery_anchor_confluence": SectionDef(
        id="strategy_discovery_anchor_confluence",
        default_title="Strategy Discovery × Anchor Confluence",
        render_fn=render_strategy_discovery_anchor_confluence,
    ),
    "strategy_discovery_decision_ledger": SectionDef(
        id="strategy_discovery_decision_ledger",
        default_title="Strategy Discovery — Decision Ledger",
        render_fn=render_strategy_discovery_decision_ledger,
    ),
    "strategy_discovery_combo_basket": SectionDef(
        id="strategy_discovery_combo_basket",
        default_title="Strategy Discovery — Combo Basket",
        render_fn=render_strategy_discovery_combo_basket,
    ),
    "candle_discovery_overview": SectionDef(
        id="candle_discovery_overview",
        default_title="Candle Discovery — Pattern × TF × Regime Overview",
        render_fn=render_candle_discovery_overview,
    ),
    "candle_discovery_ranking": SectionDef(
        id="candle_discovery_ranking",
        default_title="Candle Discovery — 5-Tier Ranking",
        render_fn=render_candle_discovery_ranking,
    ),
    "ma_discovery_overview": SectionDef(
        id="ma_discovery_overview",
        default_title="MA Discovery — Cross & Pullback Overview",
        render_fn=render_ma_discovery_overview,
    ),
    "orb_discovery_overview": SectionDef(
        id="orb_discovery_overview",
        default_title="ORB Discovery — Opening Range Breakout Overview",
        render_fn=render_orb_discovery_overview,
    ),
    "premarket_discovery_overview": SectionDef(
        id="premarket_discovery_overview",
        default_title="Pre-Market Predictor — Does the Premarket Signal the Open Direction?",
        render_fn=render_premarket_discovery_overview,
    ),
    "bb_discovery_overview": SectionDef(
        id="bb_discovery_overview",
        default_title="BB Discovery — Bollinger Band Strategies Overview",
        render_fn=render_bb_discovery_overview,
    ),
    "strategy_discovery_unified": SectionDef(
        id="strategy_discovery_unified",
        default_title="Unified Strategy Discovery — Cross-Strategy Ranking",
        render_fn=render_strategy_discovery_unified,
    ),
    "breakout_discovery_overview": SectionDef(
        id="breakout_discovery_overview",
        default_title="Breakout Discovery — N-Bar & Volatility Breakouts",
        render_fn=render_breakout_discovery_overview,
    ),
    "pullback_discovery_overview": SectionDef(
        id="pullback_discovery_overview",
        default_title="Pullback Discovery — Trend Pullback & Continuation",
        render_fn=render_pullback_discovery_overview,
    ),
    "level_discovery_overview": SectionDef(
        id="level_discovery_overview",
        default_title="Level Discovery — Swing Levels, Consolidation & Round Numbers",
        render_fn=render_level_discovery_overview,
    ),
    "lcr_discovery_overview": SectionDef(
        id="lcr_discovery_overview",
        default_title="LCR Discovery — Large Candle Region Analysis",
        render_fn=render_lcr_discovery_overview,
    ),

    # --- Optimization ---
    "optimization_overview": SectionDef(
        id="optimization_overview",
        default_title="Optimization Results Overview",
        render_fn=render_optimization_overview,
    ),

    # --- Large Candle Forward Excursion ---
    "large_candle_excursion_summary": SectionDef(
        id="large_candle_excursion_summary",
        default_title="Large Candle Excursion — Summary",
        render_fn=render_large_candle_excursion_summary,
    ),
    "large_candle_excursion_distributions": SectionDef(
        id="large_candle_excursion_distributions",
        default_title="Large Candle Excursion — Distributions & Breakdowns",
        render_fn=render_large_candle_excursion_distributions,
    ),
    "large_candle_excursion_threshold_hits": SectionDef(
        id="large_candle_excursion_threshold_hits",
        default_title="Large Candle Excursion — Threshold Hit Rates",
        render_fn=render_large_candle_excursion_threshold_hits,
    ),
    "large_candle_excursion_event_table": SectionDef(
        id="large_candle_excursion_event_table",
        default_title="Large Candle Excursion — Event Detail Table",
        render_fn=render_large_candle_excursion_event_table,
    ),
    "large_candle_excursion_methodology": SectionDef(
        id="large_candle_excursion_methodology",
        default_title="Large Candle Excursion — Methodology",
        render_fn=render_large_candle_excursion_methodology,
    ),
    "large_candle_excursion_trade_summary": SectionDef(
        id="large_candle_excursion_trade_summary",
        default_title="Large Candle Excursion — Trade Analysis Summary",
        render_fn=render_large_candle_excursion_trade_summary,
    ),
    "large_candle_excursion_trade_comparison": SectionDef(
        id="large_candle_excursion_trade_comparison",
        default_title="Large Candle Excursion — Continuation vs Reverse Comparison",
        render_fn=render_large_candle_excursion_trade_comparison,
    ),
    "large_candle_excursion_trade_event_table": SectionDef(
        id="large_candle_excursion_trade_event_table",
        default_title="Large Candle Excursion — Trade Event Detail Table",
        render_fn=render_large_candle_excursion_trade_event_table,
    ),
    "large_candle_excursion_volume_context": SectionDef(
        id="large_candle_excursion_volume_context",
        default_title="Large Candle Excursion — Volume Context",
        render_fn=render_large_candle_excursion_volume_context,
    ),
    "large_candle_excursion_structure_context": SectionDef(
        id="large_candle_excursion_structure_context",
        default_title="Large Candle Excursion — Candle Structure Context",
        render_fn=render_large_candle_excursion_structure_context,
    ),
    "large_candle_excursion_volatility_context": SectionDef(
        id="large_candle_excursion_volatility_context",
        default_title="Large Candle Excursion — Volatility Context",
        render_fn=render_large_candle_excursion_volatility_context,
    ),
    "large_candle_excursion_interactions": SectionDef(
        id="large_candle_excursion_interactions",
        default_title="Large Candle Excursion — Context Interaction Tables",
        render_fn=render_large_candle_excursion_interactions,
    ),
    "large_candle_excursion_findings_executive_summary": SectionDef(
        id="large_candle_excursion_findings_executive_summary",
        default_title="Large Candle Excursion Findings — Executive Summary",
        render_fn=render_large_candle_excursion_findings_executive_summary,
    ),
    "large_candle_excursion_findings_top_discoveries": SectionDef(
        id="large_candle_excursion_findings_top_discoveries",
        default_title="Large Candle Excursion Findings — Top Discoveries",
        render_fn=render_large_candle_excursion_findings_top_discoveries,
    ),
    "large_candle_excursion_findings_interactions": SectionDef(
        id="large_candle_excursion_findings_interactions",
        default_title="Large Candle Excursion Findings — Strongest Interaction Effects",
        render_fn=render_large_candle_excursion_findings_interactions,
    ),
    "large_candle_excursion_findings_fragility": SectionDef(
        id="large_candle_excursion_findings_fragility",
        default_title="Large Candle Excursion Findings — Fragility Warnings",
        render_fn=render_large_candle_excursion_findings_fragility,
    ),
    "large_candle_excursion_findings_next_tests": SectionDef(
        id="large_candle_excursion_findings_next_tests",
        default_title="Large Candle Excursion Findings — Suggested Next Tests",
        render_fn=render_large_candle_excursion_findings_next_tests,
    ),
    "large_candle_excursion_findings_methodology": SectionDef(
        id="large_candle_excursion_findings_methodology",
        default_title="Large Candle Excursion Findings — Methodology",
        render_fn=render_large_candle_excursion_findings_methodology,
    ),
    "large_candle_excursion_findings_interpretation": SectionDef(
        id="large_candle_excursion_findings_interpretation",
        default_title="Large Candle Excursion Findings — Research Interpretation Guide",
        render_fn=render_large_candle_excursion_findings_interpretation,
    ),
    "large_candle_excursion_findings_families": SectionDef(
        id="large_candle_excursion_findings_families",
        default_title="Large Candle Excursion — Top Setup Families",
        render_fn=render_large_candle_excursion_findings_families,
    ),
    "large_candle_excursion_findings_decision_engine": SectionDef(
        id="large_candle_excursion_findings_decision_engine",
        default_title="Large Candle Excursion Findings — Reversal Decision Engine",
        render_fn=render_large_candle_excursion_findings_decision_engine,
    ),
    "large_candle_excursion_elite_reversal_setup_extractor": SectionDef(
        id="large_candle_excursion_elite_reversal_setup_extractor",
        default_title="Large Candle Excursion Findings — Elite Reversal Setup Extractor",
        render_fn=render_large_candle_excursion_elite_reversal_setup_extractor,
    ),
    "large_candle_excursion_recursive_edge_search": SectionDef(
        id="large_candle_excursion_recursive_edge_search",
        default_title="Large Candle Excursion Findings — Recursive Edge Search",
        render_fn=render_large_candle_excursion_recursive_edge_search,
    ),
    "large_candle_excursion_edge_validation_engine": SectionDef(
        id="large_candle_excursion_edge_validation_engine",
        default_title="Large Candle Excursion Findings — Edge Validation Engine",
        render_fn=render_large_candle_excursion_edge_validation_engine,
    ),
    "large_candle_excursion_strategy_construction_engine": SectionDef(
        id="large_candle_excursion_strategy_construction_engine",
        default_title="Large Candle Excursion Findings — Strategy Construction Engine",
        render_fn=render_large_candle_excursion_strategy_construction_engine,
    ),
    "large_candle_excursion_target_curves": SectionDef(
        id="large_candle_excursion_target_curves",
        default_title="Large Candle Excursion — Target Curve Analysis",
        render_fn=render_large_candle_excursion_target_curves,
    ),
    "large_candle_excursion_session_context": SectionDef(
        id="large_candle_excursion_session_context",
        default_title="Large Candle Excursion — Session Context",
        render_fn=render_large_candle_excursion_session_context,
    ),
    "large_candle_excursion_signal_context": SectionDef(
        id="large_candle_excursion_signal_context",
        default_title="Large Candle Excursion — Signal Candle Context Intelligence",
        render_fn=render_large_candle_excursion_signal_context,
    ),
    "large_candle_excursion_context_diagnostics": SectionDef(
        id="large_candle_excursion_context_diagnostics",
        default_title="Large Candle Excursion — Signal Context Coverage Diagnostics",
        render_fn=render_large_candle_excursion_context_diagnostics,
    ),
    "large_candle_excursion_context_families": SectionDef(
        id="large_candle_excursion_context_families",
        default_title="Large Candle Excursion — Top Context-Conditioned Setup Families",
        render_fn=render_large_candle_excursion_context_families,
    ),
    "large_candle_excursion_reversal_leaders": SectionDef(
        id="large_candle_excursion_reversal_leaders",
        default_title="Large Candle Excursion — Large Reversal Move Leaders",
        render_fn=render_large_candle_excursion_reversal_leaders,
    ),
    "large_candle_excursion_session_large_moves": SectionDef(
        id="large_candle_excursion_session_large_moves",
        default_title="Large Candle Excursion — Large Move Probability by Session",
        render_fn=render_large_candle_excursion_session_large_moves,
    ),
    "large_candle_excursion_strategy_cards": SectionDef(
        id="large_candle_excursion_strategy_cards",
        default_title="Large Candle Excursion — Strategy Cards",
        render_fn=render_large_candle_excursion_strategy_cards,
    ),
    "large_candle_excursion_discovery_summary": SectionDef(
        id="large_candle_excursion_discovery_summary",
        default_title="Large Candle Excursion Discovery — Executive Summary",
        render_fn=render_large_candle_excursion_discovery_summary,
    ),
    "large_candle_excursion_discovery_broad_scan": SectionDef(
        id="large_candle_excursion_discovery_broad_scan",
        default_title="Large Candle Excursion Discovery — Broad Scan Results",
        render_fn=render_large_candle_excursion_discovery_broad_scan,
    ),
    "large_candle_excursion_discovery_refinement": SectionDef(
        id="large_candle_excursion_discovery_refinement",
        default_title="Large Candle Excursion Discovery — Refinement Results",
        render_fn=render_large_candle_excursion_discovery_refinement,
    ),
    "large_candle_excursion_discovery_chains": SectionDef(
        id="large_candle_excursion_discovery_chains",
        default_title="Large Candle Excursion Discovery — Chained Discoveries",
        render_fn=render_large_candle_excursion_discovery_chains,
    ),
    "large_candle_excursion_discovery_robustness": SectionDef(
        id="large_candle_excursion_discovery_robustness",
        default_title="Large Candle Excursion Discovery — Robustness Validation",
        render_fn=render_large_candle_excursion_discovery_robustness,
    ),
    "large_candle_excursion_discovery_diagnostics": SectionDef(
        id="large_candle_excursion_discovery_diagnostics",
        default_title="Large Candle Excursion Discovery — Diagnostics",
        render_fn=render_large_candle_excursion_discovery_diagnostics,
    ),
    "large_candle_excursion_discovery_next_steps": SectionDef(
        id="large_candle_excursion_discovery_next_steps",
        default_title="Large Candle Excursion Discovery — Suggested Next Steps",
        render_fn=render_large_candle_excursion_discovery_next_steps,
    ),
    "large_candle_excursion_discovery_methodology": SectionDef(
        id="large_candle_excursion_discovery_methodology",
        default_title="Large Candle Excursion Discovery — Methodology",
        render_fn=render_large_candle_excursion_discovery_methodology,
    ),
# render_pattern_engine_diagnostics
}
