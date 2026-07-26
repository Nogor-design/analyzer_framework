from .models import AgentStats, DailyPrediction, LevelOutcome, PredictionOutcome, SimilarContext
from .store import PredictionStore, DuplicateOutcomeError
from .outcome_measurer import measure_outcome
from .scorer import score_prediction
from .calibrator import extract_feature_vector, find_similar_contexts, compute_agent_stats
from .context_builder import build_prediction_context
from .orchestrator import (
    predict_next_day,
    measure_and_learn,
    statistical_stub_agent,
    PredictionValidationError,
    InsufficientBarsError,
)
from .claude_agent import ClaudeMarketAgent
from .ollama_agent import OllamaMarketAgent

# ---- Horizon prediction system (Phase 1) ----------------------------------
from .horizon_models import (
    CandleHorizonOutcome,
    CandleHorizonPrediction,
    HORIZON_SCHEMA_VERSION,
    VALID_DIRECTIONS,
    VALID_SESSIONS,
    VALID_THRESHOLD_ORDER,
    VALID_TIMEFRAMES,
)
from .session_classifier import (
    SessionConfig,
    label_session,
    label_sessions_for_index,
)
from .horizon_outcome_measurer import measure_horizon_outcome
from .horizon_scorer import HorizonCompositeWeights, score_horizon_prediction
from .statistical_probability_agent import (
    StatisticalProbabilityAgent,
    StatisticalProbabilityAgentConfig,
)

# ---- Horizon prediction system (Phase 2) ----------------------------------
from .horizon_store import (
    DuplicateHorizonOutcomeError,
    HorizonPredictionStore,
)
from .analogue_probability_agent import (
    AnalogueProbabilityAgent,
    AnalogueProbabilityAgentConfig,
)
from .horizon_calibrator import (
    HorizonBucketKey,
    HorizonBucketStats,
    compute_all_bucket_stats,
    compute_horizon_bucket_stats,
    compute_per_bucket_ece,
    group_by_bucket,
    lookup_calibration_error,
)

# ---- Horizon prediction system (Phase 3) ----------------------------------
from .horizon_agent import (
    AgentFactory,
    AgentRegistry,
    DEFAULT_REGISTRY,
    HorizonAgent,
    register_default_agents,
)
from .horizon_batch import (
    BarLoader,
    HorizonAgentProtocol,
    HorizonBatchResult,
    HorizonBatchRunner,
    HorizonBatchSpec,
    asofs_from_bars,
    build_schedule,
    make_market_bar_loader,
    make_static_bar_loader,
    resolve_asof_idx,
)
from .backtest_horizon_predictions import (
    HorizonBacktestConfig,
    HorizonBacktestSummary,
    run_horizon_backtest,
    run_walk_forward_replay,
)
from .horizon_reports import (
    AgentLeaderboardRow,
    BestEdgeCell,
    CalibrationReportEntry,
    DriftReportRow,
    HorizonReportBundle,
    SessionMatrixCell,
    TimeframeHorizonCell,
    build_agent_leaderboard,
    build_best_edge_cells,
    build_calibration_report,
    build_drift_report,
    build_full_report,
    build_session_matrix,
    build_timeframe_horizon_matrix,
    format_agent_leaderboard,
    format_best_edge_cells,
    format_calibration_report,
    format_drift_report,
    format_full_report,
    format_session_matrix,
    format_timeframe_horizon_matrix,
)

# ---- Horizon prediction system (Phase 4) ----------------------------------
from .horizon_specialists import (
    REGIME_SPECIALIST_AGENT_ID,
    SESSION_SPECIALIST_AGENT_ID,
    make_regime_specialist_agent,
    make_session_specialist_agent,
)
from .horizon_ensemble import (
    EnsembleHorizonAgent,
    StackingKey,
    StackingWeightTable,
    compute_stacking_weights,
)

# ---- Horizon prediction system (Phase 5) ----------------------------------
from .horizon_costs import CostModel
from .horizon_tradable_zone import (
    TradableZoneConfig,
    TradableZoneVerdict,
    evaluate_tradable_zone,
)
from .horizon_abstention import AbstentionPolicy
from .horizon_config import (
    HorizonConfig,
    HorizonPipelineResult,
    load_horizon_config,
    load_horizon_config_or_default,
)

__all__ = [
    # Daily system (unchanged)
    "AgentStats",
    "DailyPrediction",
    "LevelOutcome",
    "PredictionOutcome",
    "SimilarContext",
    "PredictionStore",
    "DuplicateOutcomeError",
    "measure_outcome",
    "score_prediction",
    "extract_feature_vector",
    "find_similar_contexts",
    "compute_agent_stats",
    "build_prediction_context",
    "predict_next_day",
    "measure_and_learn",
    "statistical_stub_agent",
    "PredictionValidationError",
    "InsufficientBarsError",
    "ClaudeMarketAgent",
    "OllamaMarketAgent",
    # Horizon system (Phase 1)
    "CandleHorizonOutcome",
    "CandleHorizonPrediction",
    "HORIZON_SCHEMA_VERSION",
    "VALID_DIRECTIONS",
    "VALID_SESSIONS",
    "VALID_THRESHOLD_ORDER",
    "VALID_TIMEFRAMES",
    "SessionConfig",
    "label_session",
    "label_sessions_for_index",
    "measure_horizon_outcome",
    "HorizonCompositeWeights",
    "score_horizon_prediction",
    "StatisticalProbabilityAgent",
    "StatisticalProbabilityAgentConfig",
    # Horizon system (Phase 2)
    "DuplicateHorizonOutcomeError",
    "HorizonPredictionStore",
    "AnalogueProbabilityAgent",
    "AnalogueProbabilityAgentConfig",
    "HorizonBucketKey",
    "HorizonBucketStats",
    "compute_all_bucket_stats",
    "compute_horizon_bucket_stats",
    "compute_per_bucket_ece",
    "group_by_bucket",
    "lookup_calibration_error",
    # Horizon system (Phase 3)
    "AgentFactory",
    "AgentRegistry",
    "DEFAULT_REGISTRY",
    "HorizonAgent",
    "register_default_agents",
    "BarLoader",
    "HorizonAgentProtocol",
    "HorizonBatchResult",
    "HorizonBatchRunner",
    "HorizonBatchSpec",
    "asofs_from_bars",
    "build_schedule",
    "make_market_bar_loader",
    "make_static_bar_loader",
    "resolve_asof_idx",
    "HorizonBacktestConfig",
    "HorizonBacktestSummary",
    "run_horizon_backtest",
    "run_walk_forward_replay",
    "AgentLeaderboardRow",
    "BestEdgeCell",
    "CalibrationReportEntry",
    "DriftReportRow",
    "HorizonReportBundle",
    "SessionMatrixCell",
    "TimeframeHorizonCell",
    "build_agent_leaderboard",
    "build_best_edge_cells",
    "build_calibration_report",
    "build_drift_report",
    "build_full_report",
    "build_session_matrix",
    "build_timeframe_horizon_matrix",
    "format_agent_leaderboard",
    "format_best_edge_cells",
    "format_calibration_report",
    "format_drift_report",
    "format_full_report",
    "format_session_matrix",
    "format_timeframe_horizon_matrix",
    # Horizon system (Phase 4)
    "REGIME_SPECIALIST_AGENT_ID",
    "SESSION_SPECIALIST_AGENT_ID",
    "make_regime_specialist_agent",
    "make_session_specialist_agent",
    "EnsembleHorizonAgent",
    "StackingKey",
    "StackingWeightTable",
    "compute_stacking_weights",
    # Horizon system (Phase 5)
    "CostModel",
    "TradableZoneConfig",
    "TradableZoneVerdict",
    "evaluate_tradable_zone",
    "AbstentionPolicy",
    "HorizonConfig",
    "HorizonPipelineResult",
    "load_horizon_config",
    "load_horizon_config_or_default",
]
