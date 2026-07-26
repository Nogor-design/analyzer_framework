"""
Rule-based abstention policy for horizon predictions.

A prediction may already declare itself abstaining (insufficient samples
inside the agent), but we also want a *deployment-time* policy that can
veto otherwise-emitted predictions on operator-defined criteria — e.g.,
"never trust a prediction in the `range` regime" or "require fallback
level ≤ 1".

The policy is a flat dataclass of named thresholds, not an arbitrary
expression DSL. That keeps the YAML schema readable, the evaluation
order well-defined, and avoids `eval`-flavored security footguns.

Evaluation never mutates the input prediction. `apply()` returns a new
`CandleHorizonPrediction` instance with `abstain=True` and a populated
`abstain_reason` when any rule fires.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Optional, Tuple

from .horizon_models import VALID_ABSTAIN_REASONS, CandleHorizonPrediction


@dataclass
class AbstentionPolicy:
    """
    Operator-defined thresholds that override an emitted prediction to
    abstain. Each field has a permissive default so an unset policy
    leaves predictions untouched.
    """
    min_sample_size: int = 0
    min_effective_sample_size: float = 0.0
    max_fallback_level: int = 99
    min_confidence: float = 0.0
    regime_blacklist: List[str] = field(default_factory=list)
    session_blacklist: List[str] = field(default_factory=list)

    # If the agent declared abstain itself, do we honor that as the final
    # state (True) or recheck against the policy thresholds (False)?
    # Default: honor — the agent always wins on its own abstention.
    honor_agent_abstain: bool = True

    # ------------------------------------------------------------------
    def evaluate(
        self,
        pred: CandleHorizonPrediction,
    ) -> Tuple[bool, Optional[str]]:
        """
        Return `(should_abstain, reason)`. Reason is one of
        `VALID_ABSTAIN_REASONS` so it round-trips through the existing
        prediction schema cleanly.
        """
        if pred.abstain and self.honor_agent_abstain:
            return True, pred.abstain_reason or "insufficient_samples"

        if pred.sample_size < int(self.min_sample_size):
            return True, "insufficient_samples"
        if pred.effective_sample_size < float(self.min_effective_sample_size):
            return True, "insufficient_samples"
        if pred.fallback_level > int(self.max_fallback_level):
            return True, "uncalibrated"

        confidence = max(
            pred.bullish_probability,
            pred.bearish_probability,
            pred.neutral_probability,
        )
        if confidence < float(self.min_confidence):
            return True, "low_confidence"

        regime = str((pred.feature_snapshot or {}).get("regime") or "")
        if regime and regime in self.regime_blacklist:
            return True, "regime_drift"

        if pred.session_label and pred.session_label in self.session_blacklist:
            return True, "regime_drift"

        return False, None

    # ------------------------------------------------------------------
    def apply(self, pred: CandleHorizonPrediction) -> CandleHorizonPrediction:
        """
        Return `pred` unchanged, or a copy forced to abstain when any
        rule fires. The copy zeros direction/threshold probabilities so
        downstream consumers cannot accidentally trade a vetoed forecast.
        """
        should, reason = self.evaluate(pred)
        if not should:
            return pred

        # Normalize the reason to one of the schema's allowed values.
        if reason not in VALID_ABSTAIN_REASONS:
            reason = "unknown"

        return replace(
            pred,
            bullish_probability=0.0,
            bearish_probability=0.0,
            neutral_probability=0.0,
            confidence=0.0,
            upside_threshold_probability=0.0,
            downside_threshold_probability=0.0,
            neither_threshold_probability=1.0,
            abstain=True,
            abstain_reason=reason,
            reasoning_summary=(
                pred.reasoning_summary
                + f" | abstention_policy: {reason}"
            ).strip(" |"),
        )

    # ------------------------------------------------------------------
    def as_dict(self) -> Dict[str, Any]:
        return {
            "min_sample_size": self.min_sample_size,
            "min_effective_sample_size": self.min_effective_sample_size,
            "max_fallback_level": self.max_fallback_level,
            "min_confidence": self.min_confidence,
            "regime_blacklist": list(self.regime_blacklist),
            "session_blacklist": list(self.session_blacklist),
            "honor_agent_abstain": self.honor_agent_abstain,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any] | None) -> "AbstentionPolicy":
        d = dict(d or {})
        return cls(
            min_sample_size=int(d.get("min_sample_size", 0) or 0),
            min_effective_sample_size=float(d.get("min_effective_sample_size", 0.0) or 0.0),
            max_fallback_level=int(d.get("max_fallback_level", 99) or 99),
            min_confidence=float(d.get("min_confidence", 0.0) or 0.0),
            regime_blacklist=list(d.get("regime_blacklist") or []),
            session_blacklist=list(d.get("session_blacklist") or []),
            honor_agent_abstain=bool(d.get("honor_agent_abstain", True)),
        )
