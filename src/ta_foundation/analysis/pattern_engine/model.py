# ta_foundation/analysis/pattern_engine/model.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Literal


DirectionMode = Literal["long", "short", "both"]
EntityType = Literal["pattern", "cluster"]
BlockUnit = Literal["window", "session", "day"]
ClusterMethod = Literal["kmeans", "hier", "hdbscan"]


@dataclass(frozen=True)
class PatternTemplate:
    """
    A pattern family/structure definition plus detection contract.
    The detect_fn must be deterministic and produce vectorized signals.
    """
    family: str
    structure: str
    direction_mode: DirectionMode
    requires_ticks: bool = False

    # Schema / metadata
    param_schema: Dict[str, Any] = field(default_factory=dict)
    feature_keys_emitted: List[str] = field(default_factory=list)

    # Eligibility gating
    valid_regimes: Optional[List[str]] = None

    # Callable hooks (to be assigned by registry)
    # detect_fn signature (recommended):
    #   detect_fn(bars_df, ticks_df|None, params, market_ctx) -> signals_df
    detect_fn: Any = None


@dataclass(frozen=True)
class PatternInstance:
    pattern_id: str
    template_key: str  # e.g. "ORB::orb_break_retest"
    family: str
    structure: str
    direction_mode: DirectionMode
    requires_ticks: bool
    params: Dict[str, Any]
    params_json: str  # canonical stable JSON representation
    version: str      # engine version/hash


@dataclass(frozen=True)
class PropConstraints:
    trailing_drawdown_usd: float
    daily_loss_limit_usd: float
    tick_value_usd: float
    profit_target_usd: Optional[float] = None
    max_contracts: int = 1
    allow_scale: bool = False


@dataclass(frozen=True)
class MonteCarloConfig:
    n_paths: int = 2000
    block_unit: BlockUnit = "day"
    block_window_minutes: int = 60  # used when block_unit == "window"
    regime_stratified: bool = True
    regime_markov: bool = True
    stress_slippage_ticks: Tuple[int, ...] = (0, 1, 2, 4)
    skip_low_liq: bool = False
    seed: int = 7


@dataclass(frozen=True)
class PurgedCVConfig:
    n_folds: int = 5
    embargo_bars: int = 0
    purge_bars: int = 0
    time_col: str = "dt"
    day_col: str = "day_id"
    entity_cols: Tuple[str, ...] = ("entity_type", "entity_id")
    horizon_col: str = "horizon"


@dataclass(frozen=True)
class ClusterConfig:
    method: ClusterMethod = "kmeans"
    k: int = 25
    min_cluster_size: int = 5     # for hdbscan-like methods
    random_state: int = 7
    representative_k: int = 2     # reps per cluster


@dataclass(frozen=True)
class MetaModelConfig:
    enabled: bool = False
    model_type: Literal["ridge", "gbrt"] = "gbrt"
    target_col: str = "prop_survival_score"
    random_state: int = 7