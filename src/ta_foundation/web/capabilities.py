from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from ta_foundation.reports.html.registry import SECTION_REGISTRY


@dataclass(frozen=True)
class Capability:
    """UI-facing description of a runnable ta_foundation workflow."""

    id: str
    name: str
    category: str
    summary: str
    required_inputs: list[str]
    optional_inputs: list[str] = field(default_factory=list)
    report_sections: list[str] = field(default_factory=list)
    config_blocks: dict[str, Any] = field(default_factory=dict)
    estimated_runtime: str = "varies"
    needs_market_data: bool = False
    needs_tick_data: bool = False
    status: str = "planned"
    kind: str = "capability"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReportSectionMetadata:
    """UI-facing metadata derived from the HTML section registry."""

    id: str
    title: str
    category: str
    config_block_keys: list[str] = field(default_factory=list)
    kind: str = "report_section"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CapabilityGroup:
    """Higher-level UI bundle composed from existing YAML blocks and sections."""

    id: str
    name: str
    category: str
    summary: str
    report_sections: list[str]
    config_blocks: dict[str, Any] = field(default_factory=dict)
    required_inputs: list[str] = field(default_factory=list)
    optional_inputs: list[str] = field(default_factory=list)
    needs_market_data: bool = False
    needs_tick_data: bool = False
    estimated_runtime: str = "varies"
    status: str = "ready"
    kind: str = "capability_group"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


CAPABILITIES: tuple[Capability, ...] = (
    Capability(
        id="core_comparison_report",
        name="Core Strategy Comparison",
        category="Reports",
        summary="Ingest NinjaTrader exports and render the standard multi-run comparison report.",
        required_inputs=["input_folder", "output_folder"],
        optional_inputs=["report_title", "run_id_regex", "include_run_images"],
        report_sections=[
            "comparison_overview",
            "run_kpi_cards",
            "daily_scoreboard",
            "equity_curve_comparison",
        ],
        config_blocks={
            "report": {
                "title": "Strategy Comparison",
                "output_filename": "comparison_report.html",
                "timezone": "America/Denver",
            },
            "sections": [
                {"id": "comparison_overview"},
                {"id": "run_kpi_cards"},
                {"id": "daily_scoreboard"},
            ],
        },
        estimated_runtime="fast",
        status="ready",
    ),
    Capability(
        id="optimization_overview",
        name="Optimization Overview",
        category="Reports",
        summary="Summarize NinjaTrader optimization sweeps and rank parameter combinations.",
        required_inputs=["input_folder", "output_folder"],
        optional_inputs=["min_trades", "top_n"],
        report_sections=["optimization_overview"],
        config_blocks={
            "sections": [
                {"id": "optimization_overview", "options": {"top_n": 10, "min_trades": 1}}
            ]
        },
        estimated_runtime="fast",
        status="ready",
    ),
    Capability(
        id="anchor_interaction",
        name="MA Anchor Interaction",
        category="Analysis",
        summary="Score moving-average anchor interactions and TP/SL recommendation candidates.",
        required_inputs=["input_folder", "output_folder", "market_data_folder"],
        optional_inputs=["strategy_family", "anchors"],
        report_sections=[
            "anchor_interaction_overview",
            "anchor_interaction_anchor_matrix",
            "anchor_tp_sl_recommendations",
        ],
        config_blocks={
            "anchor_interaction": {
                "enabled": True,
                "strategy_family": "SMA",
                "anchors": [{"family": "SMA", "length": 20, "source": "close"}],
            }
        },
        estimated_runtime="medium",
        needs_market_data=True,
        status="ready",
    ),
    Capability(
        id="pattern_engine",
        name="Pattern Engine",
        category="Discovery",
        summary="Run parameterized market-pattern sweeps, diagnostics, clustering, and robustness checks.",
        required_inputs=["input_folder", "output_folder", "market_data_folder"],
        optional_inputs=["template_families", "artifact_dir"],
        report_sections=[
            "pattern_engine_overview",
            "pattern_engine_diagnostics",
            "pattern_market_discovery",
        ],
        config_blocks={"pattern_engine": {"enabled": True}},
        estimated_runtime="slow",
        needs_market_data=True,
        needs_tick_data=True,
        status="ready",
    ),
    Capability(
        id="strategy_discovery",
        name="Strategy Discovery",
        category="Discovery",
        summary="Rank candidate entry/exit/filter rules and produce strategy-discovery report sections.",
        required_inputs=["input_folder", "output_folder", "market_data_folder"],
        optional_inputs=["instrument", "contract", "timeframe"],
        report_sections=[
            "strategy_discovery_overview",
            "strategy_discovery_ranked_table",
            "strategy_discovery_validation",
        ],
        config_blocks={
            "strategy_discovery": {
                "enabled": True,
                "instrument": "NQ",
                "contract": "H25",
                "timeframe": "5m",
            }
        },
        estimated_runtime="slow",
        needs_market_data=True,
        status="ready",
    ),
    Capability(
        id="regime_recommender",
        name="Regime Recommender",
        category="Analysis",
        summary="Classify market regimes and render parameter recommendations by regime context.",
        required_inputs=["input_folder", "output_folder", "market_data_folder"],
        optional_inputs=["instrument", "contract"],
        report_sections=["regime_parameter_recommendation"],
        config_blocks={"regime_recommender": {"enabled": True}},
        estimated_runtime="medium",
        needs_market_data=True,
        status="ready",
    ),
    Capability(
        id="daily_prediction",
        name="Daily Prediction",
        category="Prediction",
        summary="Run the existing daily next-session prediction workflow from market bars.",
        required_inputs=["market_data_folder", "prediction_config"],
        optional_inputs=["instrument", "contract", "asof", "agent"],
        report_sections=[],
        config_blocks={
            "prediction": {
                "mode": "daily",
                "instrument": "NQ",
                "contract": "H25",
                "output_dir": ".ta_artifacts/prediction",
            }
        },
        estimated_runtime="medium",
        needs_market_data=True,
        status="ready",
    ),
    Capability(
        id="horizon_prediction",
        name="Horizon Prediction",
        category="Prediction",
        summary="Run calibrated multi-agent candle-horizon predictions and expose horizon report output.",
        required_inputs=["market_data_folder", "prediction_config"],
        optional_inputs=["instrument", "contract", "horizon_timeframe", "horizon_candles", "asof"],
        report_sections=["horizon_overview"],
        config_blocks={
            "sections": [
                {
                    "id": "horizon_overview",
                    "options": {
                        "store_dir": ".ta_artifacts/horizon",
                        "instrument": "NQ",
                        "contract": "H25",
                    },
                }
            ],
            "horizon": {
                "cost_model": {
                    "fixed_points_per_side": 0.50,
                    "slippage_atr_per_side": 0.05,
                    "spread_points": 0.0,
                },
                "abstention": {
                    "min_sample_size": 20,
                    "min_effective_sample_size": 8.0,
                    "min_confidence": 0.40,
                    "honor_agent_abstain": True,
                },
                "tradable_zone": {
                    "min_confidence": 0.55,
                    "min_expected_edge_atr": 0.05,
                    "min_effective_sample_size": 8.0,
                    "kelly_cap": 0.25,
                },
            },
        },
        estimated_runtime="medium",
        needs_market_data=True,
        status="ready",
    ),
    Capability(
        id="market_data_scan",
        name="Market Data Scan",
        category="Data Quality",
        summary="Scan market-data folders for stale or missing minute/tick files before analysis.",
        required_inputs=["market_data_folder"],
        optional_inputs=["stale_days"],
        report_sections=[],
        config_blocks={},
        estimated_runtime="fast",
        needs_market_data=True,
        status="available_in_separate_dashboard",
    ),
    Capability(
        id="strategy_composer",
        name="Strategy Composer",
        category="Web Tools",
        summary="Use the existing LLM-assisted strategy template editor and local backtest workflow.",
        required_inputs=["market_data_folder"],
        optional_inputs=["db_path", "ollama_model"],
        report_sections=[],
        config_blocks={},
        estimated_runtime="medium",
        needs_market_data=True,
        status="ready",
    ),
)


HIGH_LEVEL_CAPABILITY_GROUPS: tuple[CapabilityGroup, ...] = (
    CapabilityGroup(
        id="deployment_boards",
        name="Deployment Boards",
        category="Operations",
        summary="Operator-facing deployment, daily, weekly, and momentum boards.",
        required_inputs=["input_folder", "output_folder"],
        report_sections=[
            "deployment_board_insight",
            "deployment_board_gods",
            "deployment_board_poster",
            "daily_leaderboard_cards",
            "weekly_leaderboard_cards",
            "strategy_momentum_board",
            "strategy_lifecycle_board",
            "strategy_session_momentum_board",
        ],
        estimated_runtime="fast",
    ),
    CapabilityGroup(
        id="trade_diagnostics",
        name="Trade Diagnostics",
        category="Diagnostics",
        summary="Trade, execution, settings, candle overlay, and pipeline diagnostic sections.",
        required_inputs=["input_folder", "output_folder"],
        optional_inputs=["market_data_folder", "include_run_images"],
        report_sections=[
            "run_kpi_cards",
            "run_settings_table",
            "run_metadata_cards",
            "run_snapshot_clipboard",
            "trades_intraday_pnl_by_day",
            "trade_candle_overlay",
            "tick_data_diagnostics",
        ],
        needs_market_data=True,
        estimated_runtime="medium",
    ),
    CapabilityGroup(
        id="leaderboards",
        name="Leaderboards",
        category="Reports",
        summary="Daily, weekly, and winner-board report sections for run comparison.",
        required_inputs=["input_folder", "output_folder"],
        report_sections=[
            "daily_scoreboard",
            "daily_leaderboard_cards",
            "daily_winner_spotlight",
            "weekly_leaderboard_cards",
        ],
        estimated_runtime="fast",
    ),
    CapabilityGroup(
        id="large_candle_excursion",
        name="Large Candle Excursion",
        category="Discovery",
        summary="Large-candle excursion research, findings, validation, and strategy blueprint reports.",
        required_inputs=["input_folder", "output_folder", "market_data_folder"],
        report_sections=[
            "large_candle_excursion_summary",
            "large_candle_excursion_distributions",
            "large_candle_excursion_threshold_hits",
            "large_candle_excursion_event_table",
            "large_candle_excursion_trade_summary",
            "large_candle_excursion_findings_executive_summary",
            "large_candle_excursion_findings_top_discoveries",
            "large_candle_excursion_edge_validation_engine",
            "large_candle_excursion_strategy_blueprints",
            "large_candle_excursion_discovery_summary",
        ],
        needs_market_data=True,
        estimated_runtime="slow",
    ),
    CapabilityGroup(
        id="anchor_interaction_full",
        name="Anchor Interaction Report Pack",
        category="Analysis",
        summary="All registered anchor interaction report sections plus the enabling YAML block.",
        required_inputs=["input_folder", "output_folder", "market_data_folder"],
        report_sections=[
            "anchor_interaction_config",
            "anchor_interaction_overview",
            "anchor_interaction_anchor_matrix",
            "anchor_interaction_tp_sl_spec",
            "anchor_interaction_diagnostics",
            "anchor_interaction_hourly_profile",
            "anchor_tp_sl_recommendations",
        ],
        config_blocks={
            "anchor_interaction": {
                "enabled": True,
                "strategy_family": "SMA",
                "anchors": [{"family": "SMA", "length": 20, "source": "close"}],
            }
        },
        needs_market_data=True,
        estimated_runtime="medium",
    ),
    CapabilityGroup(
        id="pattern_engine_full",
        name="Pattern Engine Report Pack",
        category="Discovery",
        summary="Pattern engine overview, diagnostics, market discovery, clustering, audit, and regime reports.",
        required_inputs=["input_folder", "output_folder", "market_data_folder"],
        report_sections=[
            "pattern_engine_overview",
            "pattern_engine_diagnostics",
            "pattern_market_discovery",
            "pattern_engine_mc_regime",
            "pattern_cluster_drilldown",
            "trade_pattern_audit",
            "market_regime_discovery",
        ],
        config_blocks={"pattern_engine": {"enabled": True}},
        needs_market_data=True,
        needs_tick_data=True,
        estimated_runtime="slow",
    ),
    CapabilityGroup(
        id="strategy_discovery_full",
        name="Strategy Discovery Report Pack",
        category="Discovery",
        summary="Strategy discovery overview, ranking, validation, risk, regime, signal, and decision sections.",
        required_inputs=["input_folder", "output_folder", "market_data_folder"],
        report_sections=[
            "strategy_discovery_overview",
            "strategy_discovery_ranked_table",
            "strategy_discovery_entry_rules",
            "strategy_discovery_filter_rules",
            "strategy_discovery_exit_policies",
            "strategy_discovery_validation",
            "strategy_discovery_mae_mfe",
            "strategy_discovery_risk_metrics",
            "strategy_discovery_decision_ledger",
            "strategy_discovery_combo_basket",
        ],
        config_blocks={
            "strategy_discovery": {
                "enabled": True,
                "instrument": "NQ",
                "contract": "H25",
                "timeframe": "5m",
            }
        },
        needs_market_data=True,
        estimated_runtime="slow",
    ),
    CapabilityGroup(
        id="regime_recommender_pack",
        name="Regime Recommender",
        category="Analysis",
        summary="Regime recommendation capability and its registered report section.",
        required_inputs=["input_folder", "output_folder", "market_data_folder"],
        report_sections=["regime_parameter_recommendation"],
        config_blocks={"regime_recommender": {"enabled": True}},
        needs_market_data=True,
        estimated_runtime="medium",
    ),
    CapabilityGroup(
        id="horizon_prediction_overview",
        name="Horizon Overview",
        category="Prediction",
        summary="Registered horizon prediction overview section for existing horizon stores.",
        required_inputs=["output_folder"],
        optional_inputs=["horizon_store_dir", "instrument", "contract"],
        report_sections=["horizon_overview"],
        config_blocks={
            "sections": [
                {
                    "id": "horizon_overview",
                    "options": {
                        "store_dir": ".ta_artifacts/horizon",
                        "instrument": "NQ",
                        "contract": "H25",
                    },
                }
            ]
        },
        estimated_runtime="fast",
    ),
)


def list_capabilities() -> list[dict[str, Any]]:
    return [capability.to_dict() for capability in CAPABILITIES]


def get_capability(capability_id: str) -> dict[str, Any] | None:
    for capability in (*CAPABILITIES, *HIGH_LEVEL_CAPABILITY_GROUPS):
        if capability.id == capability_id:
            return capability.to_dict()
    return None


def list_capability_groups() -> list[dict[str, Any]]:
    return [group.to_dict() for group in HIGH_LEVEL_CAPABILITY_GROUPS]


def list_report_sections() -> list[dict[str, Any]]:
    return [section.to_dict() for section in discover_report_sections()]


def list_report_section_categories() -> list[dict[str, Any]]:
    grouped: dict[str, list[str]] = {}
    for section in discover_report_sections():
        grouped.setdefault(section.category, []).append(section.id)
    return [
        {"category": category, "section_ids": sorted(section_ids)}
        for category, section_ids in sorted(grouped.items())
    ]


def discover_report_sections() -> list[ReportSectionMetadata]:
    sections = [
        ReportSectionMetadata(
            id=section_id,
            title=section_def.default_title,
            category=_category_for_section(section_id),
            config_block_keys=_config_blocks_for_section(section_id),
        )
        for section_id, section_def in SECTION_REGISTRY.items()
    ]
    return sorted(sections, key=lambda s: (s.category, s.title.lower(), s.id))


def _category_for_section(section_id: str) -> str:
    prefix_categories = (
        ("anchor_interaction", "Anchor Interaction"),
        ("anchor_tp_sl", "Anchor Interaction"),
        ("pattern_", "Pattern Engine"),
        ("market_regime", "Pattern Engine"),
        ("strategy_discovery", "Strategy Discovery"),
        ("candle_discovery", "Entry Discovery"),
        ("ma_discovery", "Entry Discovery"),
        ("orb_discovery", "Entry Discovery"),
        ("premarket_discovery", "Entry Discovery"),
        ("bb_discovery", "Entry Discovery"),
        ("breakout_discovery", "Entry Discovery"),
        ("pullback_discovery", "Entry Discovery"),
        ("level_discovery", "Entry Discovery"),
        ("lcr_discovery", "Entry Discovery"),
        ("regime_", "Regime Recommender"),
        ("optimization_", "Optimization"),
        ("large_candle_excursion", "Large Candle Excursion"),
        ("deployment_board", "Deployment Boards"),
        ("daily_", "Leaderboards"),
        ("weekly_", "Leaderboards"),
        ("strategy_momentum", "Leaderboards"),
        ("strategy_lifecycle", "Risk"),
        ("strategy_session_momentum", "Leaderboards"),
        ("trade_", "Trade Diagnostics"),
        ("trades_", "Trade Diagnostics"),
        ("tick_data", "Trade Diagnostics"),
        ("run_", "Run Diagnostics"),
        ("horizon_", "Prediction"),
        ("apex_", "Risk"),
        ("exit_policy", "Exit Policy"),
        ("filter_discovery", "Discovery"),
    )
    for prefix, category in prefix_categories:
        if section_id.startswith(prefix):
            return category
    if section_id in {"comparison_overview", "equity_curve_comparison"}:
        return "Core Reports"
    return "Other Reports"


def _config_blocks_for_section(section_id: str) -> list[str]:
    if section_id.startswith("anchor_"):
        return ["anchor_interaction"]
    if section_id.startswith("pattern_") or section_id == "market_regime_discovery":
        return ["pattern_engine"]
    if section_id.startswith("strategy_discovery"):
        return ["strategy_discovery"]
    if section_id == "regime_parameter_recommendation":
        return ["regime_recommender"]
    if section_id.startswith("horizon_"):
        return ["horizon"]
    return []
