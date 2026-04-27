"""
YAML-driven configuration for the horizon prediction system.

Phase 5 surfaces three operator-tunable layers as a single config file:

    horizon:
      cost_model:
        fixed_points_per_side: 0.50
        slippage_atr_per_side: 0.05
        spread_points: 0.0
      abstention:
        min_sample_size: 20
        max_fallback_level: 1
        min_confidence: 0.40
        min_effective_sample_size: 8.0
        regime_blacklist: []
        session_blacklist: []
        honor_agent_abstain: true
      tradable_zone:
        min_confidence: 0.55
        min_expected_edge_atr: 0.05
        min_effective_sample_size: 8.0
        allow_neutral_argmax: false
        kelly_cap: 0.25
        min_size_fraction: 0.0

The loader is permissive: missing top-level sections fall through to
dataclass defaults, so callers can ship a minimal file. Unknown keys are
ignored (not errored) so adding fields to the schema does not break
older configs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from .horizon_abstention import AbstentionPolicy
from .horizon_costs import CostModel
from .horizon_models import CandleHorizonPrediction
from .horizon_tradable_zone import (
    TradableZoneConfig,
    TradableZoneVerdict,
    evaluate_tradable_zone,
)


@dataclass
class HorizonConfig:
    cost_model: CostModel = field(default_factory=CostModel)
    abstention: AbstentionPolicy = field(default_factory=AbstentionPolicy)
    tradable_zone: TradableZoneConfig = field(default_factory=TradableZoneConfig)

    # ------------------------------------------------------------------
    def apply(
        self,
        pred: CandleHorizonPrediction,
    ) -> "HorizonPipelineResult":
        """
        Run the full deployment pipeline against a single prediction:

            1. Apply abstention policy → maybe-vetoed prediction.
            2. Evaluate tradable-zone with the cost model on the
               (post-policy) prediction.

        Returns a `HorizonPipelineResult` carrying both the (possibly
        modified) prediction and the verdict.
        """
        post_policy = self.abstention.apply(pred)
        verdict = evaluate_tradable_zone(
            prediction=post_policy,
            cost_model=self.cost_model,
            config=self.tradable_zone,
        )
        return HorizonPipelineResult(
            prediction=post_policy,
            verdict=verdict,
            policy_changed=post_policy is not pred,
        )

    # ------------------------------------------------------------------
    def as_dict(self) -> Dict[str, Any]:
        return {
            "cost_model": self.cost_model.as_dict(),
            "abstention": self.abstention.as_dict(),
            "tradable_zone": self.tradable_zone.as_dict(),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any] | None) -> "HorizonConfig":
        d = dict(d or {})
        return cls(
            cost_model=CostModel.from_dict(d.get("cost_model")),
            abstention=AbstentionPolicy.from_dict(d.get("abstention")),
            tradable_zone=TradableZoneConfig.from_dict(d.get("tradable_zone")),
        )


@dataclass
class HorizonPipelineResult:
    prediction: CandleHorizonPrediction
    verdict: TradableZoneVerdict
    policy_changed: bool = False


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def load_horizon_config(path: str | Path) -> HorizonConfig:
    """
    Load a `HorizonConfig` from a YAML file.

    Supports both `horizon: { … }` (preferred) and a top-level mapping
    with the section keys directly. Missing sections fall back to
    dataclass defaults; unknown keys are ignored.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"horizon config not found: {p}")
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"horizon config root must be a mapping, got {type(raw).__name__}")

    section = raw.get("horizon") if isinstance(raw.get("horizon"), dict) else raw
    return HorizonConfig.from_dict(section)


def load_horizon_config_or_default(path: Optional[str | Path]) -> HorizonConfig:
    """
    Convenience wrapper for callers that may or may not have a config
    on disk. Returns dataclass defaults when `path` is None or missing.
    """
    if path is None:
        return HorizonConfig()
    p = Path(path)
    if not p.exists():
        return HorizonConfig()
    return load_horizon_config(p)
