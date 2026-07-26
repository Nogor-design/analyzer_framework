"""
Trading-cost model for the horizon prediction system.

Phase 5 introduces a cost model so the tradable-zone filter can decide
which predictions actually carry positive *net* expected edge after the
trader's all-in friction (commission, spread, slippage).

Costs are expressed in two units that compose:

  - `fixed_points_per_side`  → constant per fill (commission + spread mid-cross,
                               in instrument points).
  - `slippage_atr_per_side`  → variable per fill, scaled by the asof bar's
                               prior_atr so volatile regimes pay more.

Round-trip cost (entry + exit) = 2 × (fixed_points_per_side
                                       + slippage_atr_per_side × prior_atr).

`spread_points` is included in `fixed_points_per_side` for most users; it
exists as a separate field for instruments where spread is volatility-
independent and you prefer to track it separately.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict


@dataclass
class CostModel:
    """All-in trading cost in instrument points."""
    fixed_points_per_side: float = 0.0
    slippage_atr_per_side: float = 0.0
    spread_points: float = 0.0

    # ------------------------------------------------------------------
    def round_trip_cost_points(self, prior_atr: float) -> float:
        """
        Total round-trip cost in points. `prior_atr` should be the asof
        bar's ATR (the same value the agent used to set thresholds).
        Negative ATR (e.g., the measurer's `-1.0` sentinel for fallback)
        is treated as 0 so we never produce a negative cost.
        """
        atr = max(0.0, float(prior_atr))
        per_side = (
            float(self.fixed_points_per_side)
            + float(self.spread_points)
            + float(self.slippage_atr_per_side) * atr
        )
        return 2.0 * per_side

    def round_trip_cost_atr(self, prior_atr: float) -> float:
        """Cost expressed in ATR units (handy for cross-instrument comparison)."""
        atr = max(1e-9, float(prior_atr))
        return self.round_trip_cost_points(atr) / atr

    def as_dict(self) -> Dict[str, Any]:
        return {
            "fixed_points_per_side": self.fixed_points_per_side,
            "slippage_atr_per_side": self.slippage_atr_per_side,
            "spread_points": self.spread_points,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any] | None) -> "CostModel":
        d = dict(d or {})
        return cls(
            fixed_points_per_side=float(d.get("fixed_points_per_side") or 0.0),
            slippage_atr_per_side=float(d.get("slippage_atr_per_side") or 0.0),
            spread_points=float(d.get("spread_points") or 0.0),
        )
