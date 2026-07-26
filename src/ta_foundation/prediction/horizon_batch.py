"""
Horizon batch runner — produce many CandleHorizonPredictions in one pass.

Phase 3 of the horizon prediction system. The batch driver:

  1. Resolves an asof timestamp (or index) to a concrete bar position.
  2. Calls one or more agents on a (timeframe, horizon, asof) triple.
  3. Optionally persists results to a HorizonPredictionStore.

It deliberately does not measure outcomes or score — those belong to the
backtest layer (see `backtest_horizon_predictions.py`). This module is also
the integration point for the existing `MarketDataStore`: a thin loader
adapter wraps `MarketDataStore.get_bars(...)` so the same call site works
with synthetic bars in tests and live bars in production.

Walk-forward is enforced by the agent (`asof_idx` is the only access point
to the future), so the batch driver passes the full bar series to keep
loading cheap and lets the agent slice. The runner records
`(spec, prediction, error)` tuples so a single failing asof never aborts
the batch.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

import pandas as pd

from .horizon_agent import HorizonAgent
from .horizon_models import CandleHorizonPrediction
from .horizon_store import HorizonPredictionStore

# (instrument, contract, timeframe) → DataFrame of OHLCV bars
BarLoader = Callable[[str, str, str], Optional[pd.DataFrame]]


# Backwards-compat alias for the runtime-checkable Protocol that lives in
# `horizon_agent.py`. New code should import `HorizonAgent` directly.
HorizonAgentProtocol = HorizonAgent


# ---------------------------------------------------------------------------
# Spec / result
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class HorizonBatchSpec:
    """
    One (instrument, contract, timeframe, horizon, asof) request. `asof` may
    be either a tz-aware Timestamp (resolved to the latest bar at-or-before)
    or an int (used directly as `asof_idx` against the loaded bars).
    """
    instrument: str
    contract: str
    timeframe: str
    horizon: int
    asof: object   # pd.Timestamp | int


@dataclass
class HorizonBatchResult:
    spec: HorizonBatchSpec
    agent_id: str
    prediction: Optional[CandleHorizonPrediction] = None
    error: Optional[str] = None
    asof_idx: Optional[int] = None


# ---------------------------------------------------------------------------
# Asof resolution
# ---------------------------------------------------------------------------

def resolve_asof_idx(bars: pd.DataFrame, asof: object) -> int:
    """
    Map an asof to a row index in `bars`. Accepts:
      - int               → returned verbatim if in range
      - pd.Timestamp      → largest i such that bars["dt"][i] <= asof

    Raises ValueError when no eligible bar exists or the int is out of range.
    """
    if bars is None or len(bars) == 0:
        raise ValueError("resolve_asof_idx: bars is empty")
    if isinstance(asof, (int,)) and not isinstance(asof, bool):
        i = int(asof)
        if i < 0 or i >= len(bars):
            raise ValueError(f"asof_idx {i} out of range for bars length {len(bars)}")
        return i

    ts = pd.Timestamp(asof)
    if ts.tzinfo is None:
        raise ValueError("resolve_asof_idx: asof timestamp must be tz-aware")

    if "dt" not in bars.columns:
        raise ValueError("resolve_asof_idx: bars missing 'dt' column")

    dt = pd.to_datetime(bars["dt"])
    # `dt` should already be tz-aware in this codebase, but be defensive
    if getattr(dt.dt, "tz", None) is None:
        raise ValueError("resolve_asof_idx: bars 'dt' column must be tz-aware")

    mask = dt <= ts
    if not mask.any():
        raise ValueError(f"resolve_asof_idx: no bar at or before {ts}")
    return int(mask[mask].index[-1])


# ---------------------------------------------------------------------------
# Bar loaders
# ---------------------------------------------------------------------------

def make_market_bar_loader(market) -> BarLoader:
    """
    Wrap a `MarketDataStore` in the BarLoader signature used by the runner.
    Avoids a hard import at module level so the prediction package does not
    depend on the market data subsystem when running on synthetic bars.
    """
    def loader(instrument: str, contract: str, timeframe: str) -> Optional[pd.DataFrame]:
        return market.get_bars(instrument, contract, timeframe)
    return loader


def make_static_bar_loader(
    bars_by_key: Dict[Tuple[str, str, str], pd.DataFrame],
) -> BarLoader:
    """
    Test-friendly loader backed by a dict keyed on `(instrument, contract, timeframe)`.
    """
    def loader(instrument: str, contract: str, timeframe: str) -> Optional[pd.DataFrame]:
        return bars_by_key.get((instrument, contract, timeframe))
    return loader


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

class HorizonBatchRunner:
    """
    Drive one or more agents through a sequence of `HorizonBatchSpec`s.

    Bars are loaded once per `(instrument, contract, timeframe)` and reused
    across asofs to keep large schedules cheap. Per-spec failures are
    captured on the result instead of propagating; callers that want to
    fail loudly can inspect `result.error`.
    """

    def __init__(
        self,
        agents: Sequence[HorizonAgentProtocol],
        bar_loader: BarLoader,
        store: Optional[HorizonPredictionStore] = None,
        save_predictions: bool = True,
    ) -> None:
        if not agents:
            raise ValueError("HorizonBatchRunner: at least one agent is required")
        self.agents = list(agents)
        self.bar_loader = bar_loader
        self.store = store
        self.save_predictions = bool(save_predictions)
        self._bars_cache: Dict[Tuple[str, str, str], Optional[pd.DataFrame]] = {}

    # ------------------------------------------------------------------
    def run(self, specs: Iterable[HorizonBatchSpec]) -> List[HorizonBatchResult]:
        results: List[HorizonBatchResult] = []
        for spec in specs:
            results.extend(self._run_one_spec(spec))
        return results

    # ------------------------------------------------------------------
    def _run_one_spec(self, spec: HorizonBatchSpec) -> List[HorizonBatchResult]:
        out: List[HorizonBatchResult] = []
        bars = self._load_bars(spec.instrument, spec.contract, spec.timeframe)
        if bars is None or len(bars) == 0:
            for agent in self.agents:
                out.append(HorizonBatchResult(
                    spec=spec,
                    agent_id=agent.agent_id,
                    error="bar_loader returned empty",
                ))
            return out

        try:
            asof_idx = resolve_asof_idx(bars, spec.asof)
        except ValueError as exc:
            for agent in self.agents:
                out.append(HorizonBatchResult(
                    spec=spec,
                    agent_id=agent.agent_id,
                    error=f"asof_resolution_failed: {exc}",
                ))
            return out

        for agent in self.agents:
            try:
                pred = agent.predict(
                    bars=bars,
                    asof_idx=asof_idx,
                    horizon_candles=spec.horizon,
                    instrument=spec.instrument,
                    contract=spec.contract,
                    timeframe=spec.timeframe,
                )
            except Exception as exc:  # noqa: BLE001 — defensive at boundary
                out.append(HorizonBatchResult(
                    spec=spec,
                    agent_id=agent.agent_id,
                    asof_idx=asof_idx,
                    error=f"agent_error: {type(exc).__name__}: {exc}",
                ))
                continue

            if self.store is not None and self.save_predictions:
                try:
                    self.store.save_prediction(pred)
                except Exception as exc:  # noqa: BLE001
                    out.append(HorizonBatchResult(
                        spec=spec,
                        agent_id=agent.agent_id,
                        asof_idx=asof_idx,
                        prediction=pred,
                        error=f"store_error: {type(exc).__name__}: {exc}",
                    ))
                    continue

            out.append(HorizonBatchResult(
                spec=spec,
                agent_id=agent.agent_id,
                prediction=pred,
                asof_idx=asof_idx,
            ))
        return out

    # ------------------------------------------------------------------
    def _load_bars(
        self,
        instrument: str,
        contract: str,
        timeframe: str,
    ) -> Optional[pd.DataFrame]:
        key = (instrument, contract, timeframe)
        if key in self._bars_cache:
            return self._bars_cache[key]
        bars = self.bar_loader(instrument, contract, timeframe)
        # Defensive: ensure ascending dt ordering when present
        if bars is not None and len(bars) > 0 and "dt" in bars.columns:
            bars = bars.sort_values("dt").reset_index(drop=True)
        self._bars_cache[key] = bars
        return bars

    # ------------------------------------------------------------------
    def clear_cache(self) -> None:
        """Drop loaded bars. Useful in long-running processes."""
        self._bars_cache.clear()


# ---------------------------------------------------------------------------
# Schedule helpers
# ---------------------------------------------------------------------------

def build_schedule(
    instrument: str,
    contract: str,
    timeframes: Sequence[str],
    horizons: Sequence[int],
    asofs: Sequence[object],
) -> List[HorizonBatchSpec]:
    """
    Cartesian product of `(timeframes × horizons × asofs)` for one
    (instrument, contract). Convenience wrapper for callers that want a
    rectangular schedule without writing nested loops.
    """
    specs: List[HorizonBatchSpec] = []
    for tf in timeframes:
        for h in horizons:
            for asof in asofs:
                specs.append(HorizonBatchSpec(
                    instrument=instrument,
                    contract=contract,
                    timeframe=tf,
                    horizon=int(h),
                    asof=asof,
                ))
    return specs


def asofs_from_bars(
    bars: pd.DataFrame,
    *,
    start: Optional[pd.Timestamp] = None,
    end: Optional[pd.Timestamp] = None,
    stride: int = 1,
    warmup: int = 100,
) -> List[pd.Timestamp]:
    """
    Generate a list of asof timestamps from a bar series. Used by the
    backtest layer to iterate every Nth bar inside an [start, end] window.

    `warmup` skips the first N bars so feature warm-up has stabilized.
    `stride` keeps every Nth bar after warmup.
    """
    if bars is None or len(bars) == 0:
        return []
    if "dt" not in bars.columns:
        raise ValueError("asofs_from_bars: bars must include a 'dt' column")
    dt = pd.to_datetime(bars["dt"])

    out: List[pd.Timestamp] = []
    for i in range(int(warmup), len(bars), max(1, int(stride))):
        ts = pd.Timestamp(dt.iloc[i])
        if start is not None and ts < start:
            continue
        if end is not None and ts > end:
            break
        out.append(ts)
    return out
