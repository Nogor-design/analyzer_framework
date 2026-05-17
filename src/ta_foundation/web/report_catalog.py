from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
from typing import Any

import yaml


@dataclass(frozen=True)
class ReportParameter:
    path: str
    label: str
    description: str
    default: Any
    kind: str = "text"
    group: str = "General"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReportTemplate:
    id: str
    name: str
    category: str
    description: str
    requires_backtest_data: bool
    needs_market_data: bool
    default_config: dict[str, Any]
    parameters: list[ReportParameter] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["default_yaml"] = dump_template_yaml(self.default_config)
        return payload


CLI_PARAMETERS: tuple[ReportParameter, ...] = (
    ReportParameter(
        path="input_folder",
        label="Input folder",
        description="Folder containing NinjaTrader backtest exports such as trades, summary, settings, daily analysis, and optimization CSV files.",
        default="C:/Users/Owner/Downloads",
        group="CLI input/output",
    ),
    ReportParameter(
        path="output_folder",
        label="Output folder",
        description="Folder where HTML reports, summaries, manifest.json, exported cards, and related artifacts are written.",
        default="./outputs/web_reports",
        group="CLI input/output",
    ),
    ReportParameter(
        path="market_data_folder",
        label="Market data folder",
        description="Folder containing NinjaTrader minute/tick market data files. Required for market-aware analysis, prediction, pattern discovery, and strategy discovery.",
        default="D:/MarketData",
        group="CLI input/output",
    ),
    ReportParameter(
        path="report_config_path",
        label="Report YAML path",
        description="Where the generated YAML config should be saved before the CLI report job runs.",
        default="./outputs/web_reports/generated_report.yaml",
        group="CLI input/output",
    ),
    ReportParameter(
        path="recursive",
        label="Recursive import",
        description="Search nested folders under the input folder for NinjaTrader exports.",
        default=True,
        kind="boolean",
        group="CLI ingest options",
    ),
    ReportParameter(
        path="include_run_images",
        label="Include run images",
        description="Discover and embed per-run images/cards when matching image files are available.",
        default=True,
        kind="boolean",
        group="CLI artifact options",
    ),
    ReportParameter(
        path="export_exec_cards_png",
        label="Export execution cards",
        description="After writing the HTML report, export execution profile cards as PNG files.",
        default=False,
        kind="boolean",
        group="CLI artifact options",
    ),
    ReportParameter(
        path="exec_cards_dir",
        label="Execution cards folder",
        description="Optional destination for PNG execution cards when export execution cards is enabled.",
        default="",
        group="CLI artifact options",
    ),
    ReportParameter(
        path="no_tick_data",
        label="Skip tick data",
        description="Avoid loading tick files when the selected report only needs minute bars. Useful for faster discovery runs.",
        default=False,
        kind="boolean",
        group="CLI ingest options",
    ),
    ReportParameter(
        path="run_id_regex",
        label="Run ID regex",
        description="Optional regex used by ingest to derive or filter run identifiers from export filenames.",
        default="",
        group="CLI ingest options",
    ),
    ReportParameter(
        path="db_path",
        label="DuckDB path",
        description="Optional experiment registry database path passed through to the CLI when reports need registry context.",
        default="",
        group="CLI input/output",
    ),
)


REPORT_TEMPLATES: tuple[ReportTemplate, ...] = (
    ReportTemplate(
        id="weekly_prop_dashboard",
        name="Weekly Prop Dashboard",
        category="Backtest Reports",
        description="Loads NinjaTrader backtest exports and renders the weekly leaderboard/dashboard style used in the root report.yaml.",
        requires_backtest_data=True,
        needs_market_data=False,
        default_config={
            "report": {
                "title": "Weekly Prop Dashboard",
                "output_filename": "weekly_prop_dashboard.html",
                "timezone": "America/Denver",
            },
            "sections": [
                {
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
            ],
        },
        parameters=[
            ReportParameter("report.title", "Report title", "Title shown at the top of the generated HTML report.", "Weekly Prop Dashboard"),
            ReportParameter("report.output_filename", "Output filename", "HTML filename written inside the selected output folder.", "weekly_prop_dashboard.html"),
            ReportParameter("sections.0.options.top_n", "Top N", "Maximum number of ranked weekly strategies to show.", 200, "number"),
            ReportParameter("sections.0.options.starting_balance", "Starting balance", "Account starting balance used for prop-style drawdown/buffer calculations.", 50000, "number"),
            ReportParameter("sections.0.options.trailing_dd", "Trailing drawdown", "Trailing drawdown limit used when calculating survival/buffer status.", 2500, "number"),
            ReportParameter("sections.0.options.baseline_mode", "Baseline mode", "Use fresh_week to reset weekly baseline, or continuous to carry equity forward.", "fresh_week"),
            ReportParameter("sections.0.options.show_card_image", "Show card image", "Include embedded run/card images when they are available.", True, "boolean"),
            ReportParameter("sections.0.options.show_chart", "Show chart", "Render the section chart when available.", False, "boolean"),
            ReportParameter("sections.0.options.warn_buffer", "Warning buffer", "Dollar buffer threshold used to flag strategies near drawdown danger.", 500, "number"),
        ],
    ),
    ReportTemplate(
        id="executive_cards_report",
        name="Executive Cards Report",
        category="Backtest Reports",
        description="Mined from report.yaml: loads backtest exports, embeds run images, and renders executive profile cards suitable for HTML review and PNG export.",
        requires_backtest_data=True,
        needs_market_data=False,
        default_config={
            "report": {
                "title": "Executive Strategy Profiles",
                "output_filename": "executive_cards_report.html",
                "embedded_images": True,
                "timezone": "America/Denver",
            },
            "sections": [
                {
                    "id": "run_executive_profile_cards",
                    "title": "Executive Strategy Profiles",
                    "options": {
                        "show_hint": True,
                        "show_run_image": True,
                        "background_style": "image-dark-overlay",
                        "card_width_px": 1180,
                        "card_padding_px": 24,
                        "image_width_px": 420,
                        "wlr_days_back": 30,
                        "wlr_gap_px": 2,
                        "show_detail_charts": True,
                        "detail_chart_layout": "stack",
                        "detail_chart_width_px": 1080,
                        "timeline_render_bin_minutes": 15,
                        "timeline_cell_h_px": 10,
                        "timeline_show_hours": True,
                        "timeline_show_summary": True,
                    },
                },
                {"id": "run_kpi_cards", "title": "Run KPI Cards"},
                {"id": "run_settings_table", "title": "Run Settings", "options": {"max_rows": 500}},
            ],
        },
        parameters=[
            ReportParameter("report.title", "Report title", "Title shown at the top of the generated report.", "Executive Strategy Profiles", group="Report"),
            ReportParameter("report.output_filename", "Output filename", "HTML filename written inside the output folder.", "executive_cards_report.html", group="Report"),
            ReportParameter("sections.0.options.show_run_image", "Show run image", "Embed matched per-run card/image assets into each executive card.", True, "boolean", "Executive cards"),
            ReportParameter("sections.0.options.background_style", "Background style", "Card background style supported by run_executive_profile_cards, such as solid or image-dark-overlay.", "image-dark-overlay", group="Executive cards"),
            ReportParameter("sections.0.options.card_width_px", "Card width", "PNG/HTML card width in pixels.", 1180, "number", "Executive cards"),
            ReportParameter("sections.0.options.image_width_px", "Image width", "Width allocated to the run image in pixels.", 420, "number", "Executive cards"),
            ReportParameter("sections.0.options.wlr_days_back", "W/L days back", "Number of recent trading days shown in the win/loss strip.", 30, "number", "Timeline and detail charts"),
            ReportParameter("sections.0.options.show_detail_charts", "Show detail charts", "Include detail charts beneath the profile summary when available.", True, "boolean", "Timeline and detail charts"),
            ReportParameter("sections.0.options.detail_chart_layout", "Detail chart layout", "Layout for detail charts: stack or two-up.", "stack", group="Timeline and detail charts"),
            ReportParameter("sections.0.options.timeline_render_bin_minutes", "Timeline bin minutes", "Minute bin size used by the intraday activity timeline.", 15, "number", "Timeline and detail charts"),
            ReportParameter("sections.2.options.max_rows", "Settings max rows", "Maximum number of settings rows shown in the run settings table.", 500, "number", "Run settings"),
        ],
    ),
    ReportTemplate(
        id="core_comparison",
        name="Core Strategy Comparison",
        category="Backtest Reports",
        description="General multi-run comparison from NinjaTrader backtest exports: overview, KPIs, daily board, and equity curve.",
        requires_backtest_data=True,
        needs_market_data=False,
        default_config={
            "report": {
                "title": "Strategy Comparison",
                "output_filename": "comparison_report.html",
                "timezone": "America/Denver",
            },
            "sections": [
                {"id": "comparison_overview"},
                {"id": "run_kpi_cards"},
                {"id": "daily_scoreboard", "options": {"show_individual_equity": True, "include_summary_table": True}},
                {"id": "equity_curve_comparison"},
            ],
        },
        parameters=[
            ReportParameter("report.title", "Report title", "Title shown at the top of the generated report.", "Strategy Comparison"),
            ReportParameter("report.output_filename", "Output filename", "HTML filename written inside the output folder.", "comparison_report.html"),
            ReportParameter("sections.2.options.show_individual_equity", "Show individual equity", "Include per-run equity charts in the daily scoreboard.", True, "boolean"),
            ReportParameter("sections.2.options.include_summary_table", "Include summary table", "Include the scoreboard summary table below the chart.", True, "boolean"),
        ],
    ),
    ReportTemplate(
        id="strategy_discovery_full",
        name="Strategy Discovery Full",
        category="Strategy Discovery",
        description="Runs the strategy_discovery analysis block and renders ranking, validation, risk, drawdown, entry/filter/exit discovery, signal, and NT template sections.",
        requires_backtest_data=True,
        needs_market_data=True,
        default_config={
            "report": {
                "title": "Strategy Discovery Report",
                "output_filename": "strategy_discovery_report.html",
                "timezone": "America/Denver",
            },
            "strategy_discovery": {
                "enabled": True,
                "instrument": "NQ",
                "contract": "06-26",
                "timeframe": "5m",
                "tick_size": 0.25,
                "cost_model": {
                    "commission_per_side": 2.09,
                    "slippage_ticks": 1,
                    "tick_value": 5.00,
                },
                "walk_forward": {
                    "wf_type": "rolling",
                    "is_pct": 0.70,
                    "min_is_trades": 50,
                    "min_oos_trades": 20,
                    "n_folds": 5,
                    "degradation_threshold": 0.20,
                },
                "entry_discovery": {"enabled": True, "max_depth": 2, "min_trades": 20, "top_n": 25},
                "filter_discovery": {"enabled": True, "min_trades": 20, "top_n": 20},
                "exit_discovery": {"enabled": True, "max_combos": 80},
                "position_sizing": {"enabled": True, "initial_equity": 10000.0, "n_sim": 500},
                "risk_metrics": {"enabled": True, "initial_equity": 10000.0},
                "drawdown_analysis": {"enabled": True, "initial_equity": 10000.0},
                "cohort_analysis": {"enabled": True, "cohort_by": "count", "cohort_size": 20},
                "parameter_sensitivity": {"enabled": True, "top_n_rules": 5, "n_steps": 4},
                "entry_pattern_bridge": {
                    "enabled": True,
                    "primary_horizon": 20,
                    "match_window_bars": 3,
                    "mode": "hybrid",
                    "hybrid_weights": {
                        "corpus_win_rate": 0.45,
                        "execution_agreement": 0.35,
                        "selectivity": 0.20,
                    },
                    "min_signals": 20,
                },
                "signal_entry_discovery": {
                    "enabled": True,
                    "max_depth": 2,
                    "min_signals": 15,
                    "max_candidates": 300,
                    "top_n": 20,
                    "adx_thresholds": [20, 25, 30],
                    "corpus_win_rate_thresholds": [0.50, 0.55, 0.60, 0.65],
                },
                "signal_exit_sweep": {
                    "enabled": True,
                    "stop_grid": [8, 12, 16, 20, 24, 32, 48, 64],
                    "target_grid": [8, 12, 16, 20, 24, 32, 48, 64, 80, 96],
                    "min_signals": 20,
                    "top_n_per_rule": 5,
                    "min_rr": 1.0,
                    "max_rules": 10,
                },
                "cluster_distance_threshold": 1.5,
            },
            "sections": [
                {"id": "strategy_discovery_comparison", "title": "Cross-Run Comparison"},
                {"id": "strategy_discovery_ranked_table", "title": "Strategy Ranking"},
                {"id": "strategy_discovery_overview", "title": "Strategy Discovery Per-Run Overview", "options": {"max_runs": 10}},
                {"id": "strategy_discovery_entry_rules", "title": "Entry Rule Discovery", "options": {"max_runs": 5}},
                {"id": "strategy_discovery_filter_rules", "title": "Filter Rule Discovery", "options": {"max_runs": 5}},
                {"id": "strategy_discovery_exit_policies", "title": "Exit Policy Sweep", "options": {"top_n_policies": 10}},
                {"id": "strategy_discovery_feature_importance", "title": "Feature Importance", "options": {"top_n_features": 12}},
                {"id": "strategy_discovery_validation", "title": "Walk-Forward Validation"},
                {"id": "strategy_discovery_mae_mfe", "title": "MAE/MFE Profile", "options": {"show_direction": True}},
                {"id": "strategy_discovery_signal_entries", "title": "Signal Corpus Entry Discovery", "options": {"top_n_rules": 20}},
                {"id": "strategy_discovery_signal_exit_sweep", "title": "Signal Corpus Exit Parameter Sweep", "options": {"show_baseline": True}},
                {"id": "strategy_discovery_nt_template", "title": "NinjaTrader Template", "options": {"tick_value": 5.0, "tick_size": 0.25}},
            ],
        },
        parameters=[
            ReportParameter("strategy_discovery.instrument", "Instrument", "Instrument root used to match loaded market data and label strategy output.", "NQ", group="Market data"),
            ReportParameter("strategy_discovery.contract", "Contract", "Market data contract to use, such as 06-26. Use an empty value only where the loader supports auto selection.", "06-26", group="Market data"),
            ReportParameter("strategy_discovery.timeframe", "Timeframe", "Bar timeframe used for regime, ATR, and exit-policy analysis.", "5m", group="Market data"),
            ReportParameter("strategy_discovery.tick_size", "Tick size", "Price increment per tick for the instrument.", 0.25, "number", "Market data"),
            ReportParameter("strategy_discovery.cost_model.commission_per_side", "Commission per side", "Commission in dollars per contract side applied before validation/ranking.", 2.09, "number", "Cost model"),
            ReportParameter("strategy_discovery.cost_model.slippage_ticks", "Slippage ticks", "Slippage ticks per side used in the cost model.", 1, "number", "Cost model"),
            ReportParameter("strategy_discovery.walk_forward.min_is_trades", "Min IS trades", "Minimum in-sample trades required for each walk-forward fold.", 50, "number", "Walk-forward validation"),
            ReportParameter("strategy_discovery.walk_forward.min_oos_trades", "Min OOS trades", "Minimum out-of-sample trades required for each walk-forward fold.", 20, "number", "Walk-forward validation"),
            ReportParameter("strategy_discovery.entry_discovery.top_n", "Entry rules kept", "Number of top discovered entry rules to retain.", 25, "number", "Trade-anchored discovery"),
            ReportParameter("strategy_discovery.filter_discovery.top_n", "Filter rules kept", "Number of exclusion/filter rules to retain.", 20, "number", "Trade-anchored discovery"),
            ReportParameter("strategy_discovery.signal_entry_discovery.enabled", "Signal entry discovery", "Run pure signal-corpus entry discovery from pattern engine artifacts.", True, "boolean", "Signal corpus discovery"),
            ReportParameter("strategy_discovery.signal_entry_discovery.adx_thresholds", "ADX thresholds", "Simple list of ADX thresholds to try during signal entry rule discovery.", [20, 25, 30], "list", "Signal corpus discovery"),
            ReportParameter("strategy_discovery.signal_exit_sweep.stop_grid", "Stop grid", "Simple list of stop values in ticks to sweep against signal corpus rules.", [8, 12, 16, 20, 24, 32, 48, 64], "list", "Signal corpus discovery"),
            ReportParameter("strategy_discovery.signal_exit_sweep.target_grid", "Target grid", "Simple list of target values in ticks to sweep against signal corpus rules.", [8, 12, 16, 20, 24, 32, 48, 64, 80, 96], "list", "Signal corpus discovery"),
            ReportParameter("sections.2.options.max_runs", "Overview max runs", "Number of ranked runs shown in the per-run overview section.", 10, "number", "Rendered sections"),
            ReportParameter("sections.7.options.top_n_features", "Feature rows", "Rows shown in the feature-importance section.", 12, "number", "Rendered sections"),
            ReportParameter("sections.9.options.top_n_rules", "Signal rules shown", "Maximum signal-corpus rules displayed in the report.", 20, "number", "Rendered sections"),
        ],
    ),
    ReportTemplate(
        id="horizon_overview",
        name="Horizon Prediction Overview",
        category="Prediction Reports",
        description="Renders an existing horizon prediction store as an HTML report section. This does not ingest backtest exports.",
        requires_backtest_data=False,
        needs_market_data=False,
        default_config={
            "report": {
                "title": "Horizon Prediction Overview",
                "output_filename": "horizon_overview.html",
                "timezone": "America/Denver",
            },
            "sections": [
                {
                    "id": "horizon_overview",
                    "options": {
                        "store_dir": ".ta_artifacts/horizon",
                        "instrument": "NQ",
                        "contract": "06-26",
                        "min_samples_cell": 5,
                        "min_samples_edge": 20,
                        "min_samples_calibration": 20,
                        "drift_recent_n": 50,
                        "top_n_edge": 20,
                    },
                }
            ],
        },
        parameters=[
            ReportParameter("sections.0.options.store_dir", "Horizon store dir", "Directory containing horizon_predictions.jsonl and horizon_outcomes.jsonl partitions.", ".ta_artifacts/horizon"),
            ReportParameter("sections.0.options.instrument", "Instrument", "Instrument partition to load from the horizon store.", "NQ"),
            ReportParameter("sections.0.options.contract", "Contract", "Contract partition to load from the horizon store.", "06-26"),
            ReportParameter("sections.0.options.min_samples_cell", "Min cell samples", "Minimum samples needed before showing matrix cells as meaningful.", 5, "number"),
            ReportParameter("sections.0.options.top_n_edge", "Top edge rows", "Maximum number of best-edge rows to show.", 20, "number"),
        ],
    ),
)


def list_report_templates() -> list[dict[str, Any]]:
    return [template.to_dict() for template in REPORT_TEMPLATES]


def list_cli_parameters() -> list[dict[str, Any]]:
    return [param.to_dict() for param in CLI_PARAMETERS]


def get_report_template(template_id: str) -> ReportTemplate | None:
    for template in REPORT_TEMPLATES:
        if template.id == template_id:
            return template
    return None


def build_template_config(template_id: str, values: dict[str, Any] | None = None) -> dict[str, Any]:
    template = get_report_template(template_id)
    if template is None:
        raise ValueError(f"Unknown report template: {template_id}")
    config = deepcopy(template.default_config)
    typed_values = values or {}
    params = {param.path: param for param in template.parameters}
    for path, raw_value in typed_values.items():
        param = params.get(path)
        value = _coerce_value(raw_value, param.kind if param else "text")
        _set_dotted_path(config, path, value)
    return config


def build_template_yaml(template_id: str, values: dict[str, Any] | None = None) -> str:
    return dump_template_yaml(build_template_config(template_id, values))


def dump_template_yaml(config: dict[str, Any]) -> str:
    return yaml.safe_dump(config, sort_keys=False, allow_unicode=False)


def _set_dotted_path(target: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    current: Any = target
    for part in parts[:-1]:
        if isinstance(current, list):
            current = current[int(part)]
        else:
            current = current.setdefault(part, {})
    last = parts[-1]
    if isinstance(current, list):
        current[int(last)] = value
    else:
        current[last] = value


def _coerce_value(value: Any, kind: str) -> Any:
    if kind == "boolean":
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on"}
    if kind == "number":
        text = str(value).strip()
        if text == "":
            return None
        try:
            if any(ch in text for ch in (".", "e", "E")):
                return float(text)
            return int(text)
        except ValueError:
            return value
    if kind == "list":
        if isinstance(value, list):
            return value
        text = str(value).strip()
        if text == "":
            return []
        try:
            parsed = yaml.safe_load(text)
        except yaml.YAMLError:
            parsed = None
        if isinstance(parsed, list):
            return parsed
        return [_coerce_list_item(item) for item in text.split(",") if item.strip()]
    return value


def _coerce_list_item(value: str) -> Any:
    text = value.strip()
    try:
        parsed = yaml.safe_load(text)
    except yaml.YAMLError:
        return text
    if isinstance(parsed, (str, int, float, bool)) or parsed is None:
        return parsed
    return text
