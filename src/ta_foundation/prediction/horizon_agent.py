"""
Formal `HorizonAgent` protocol + agent-type registry.

Phase 3 used a duck-typed `HorizonAgentProtocol` defined inside
`horizon_batch.py`. As more agent types arrived (statistical, analogue,
regime / session specialists, ensemble) the duck-type became the load-
bearing contract for the whole prediction stack. This module promotes
it to a first-class, runtime-checkable Protocol so:

  - new agents have a single import to subclass against,
  - configuration-driven instantiation (`AgentRegistry.create("analogue")`)
    becomes possible without per-call-site if/elif chains,
  - `isinstance(obj, HorizonAgent)` actually works at runtime when
    callers want to validate plugins or YAML-loaded specs.

Backward compatibility: `horizon_batch.HorizonAgentProtocol` is preserved
as an alias so existing imports keep working.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Protocol, runtime_checkable

import pandas as pd

from .horizon_models import CandleHorizonPrediction


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class HorizonAgent(Protocol):
    """
    Minimum contract a horizon-prediction agent must satisfy.

    Concrete agents:
      - StatisticalProbabilityAgent
      - AnalogueProbabilityAgent
      - regime/session specialists (built via factories)
      - EnsembleHorizonAgent

    Each emits a `CandleHorizonPrediction` from a tz-aware OHLCV bar
    series. Walk-forward leakage is the agent's responsibility — it must
    never use bars at indices > asof_idx for prediction-side computation
    (history bucketing, feature extraction). Outcome measurement is a
    separate step handled by `horizon_outcome_measurer`.
    """
    agent_id: str

    def predict(
        self,
        bars: pd.DataFrame,
        asof_idx: int,
        horizon_candles: int,
        instrument: str,
        contract: str,
        timeframe: str,
    ) -> CandleHorizonPrediction: ...


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

AgentFactory = Callable[..., HorizonAgent]


class AgentRegistry:
    """
    Keyed lookup for agent constructors. Plugins / config layers ask the
    registry for an agent by name; the registry returns a fresh instance
    constructed with whatever kwargs the caller supplies.

    Registration is idempotent for the same `(name, factory)` pair so
    importing the registration module twice (e.g. once from `__init__`
    and once from a CLI) does not raise.
    """

    def __init__(self) -> None:
        self._factories: Dict[str, AgentFactory] = {}

    # ------------------------------------------------------------------
    def register(
        self,
        name: str,
        factory: AgentFactory,
        *,
        replace: bool = False,
    ) -> None:
        if not name:
            raise ValueError("AgentRegistry.register: name must be non-empty")
        existing = self._factories.get(name)
        if existing is factory:
            return
        if existing is not None and not replace:
            raise ValueError(
                f"Agent type {name!r} already registered. "
                f"Pass replace=True to overwrite."
            )
        self._factories[name] = factory

    def unregister(self, name: str) -> None:
        self._factories.pop(name, None)

    # ------------------------------------------------------------------
    def create(self, name: str, /, **kwargs: Any) -> HorizonAgent:
        factory = self._factories.get(name)
        if factory is None:
            raise KeyError(
                f"Unknown agent type: {name!r}. "
                f"Known types: {sorted(self._factories.keys())}"
            )
        return factory(**kwargs)

    def list_types(self) -> List[str]:
        return sorted(self._factories.keys())

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self._factories


# ---------------------------------------------------------------------------
# Default registry
# ---------------------------------------------------------------------------

DEFAULT_REGISTRY: AgentRegistry = AgentRegistry()


def register_default_agents(registry: AgentRegistry = DEFAULT_REGISTRY) -> None:
    """
    Register the built-in agent types. Imports are deferred to keep the
    `prediction` package import graph simple — `horizon_agent.py` is
    a lightweight dependency for subclasses, and we don't want pulling
    it in to drag every concrete agent + numpy + scipy with it.
    """
    from .analogue_probability_agent import AnalogueProbabilityAgent
    from .horizon_specialists import (
        make_regime_specialist_agent,
        make_session_specialist_agent,
    )
    from .statistical_probability_agent import StatisticalProbabilityAgent

    registry.register("statistical", StatisticalProbabilityAgent, replace=True)
    registry.register("analogue", AnalogueProbabilityAgent, replace=True)
    registry.register("regime_specialist", make_regime_specialist_agent, replace=True)
    registry.register("session_specialist", make_session_specialist_agent, replace=True)


# Auto-register on import so callers get a populated registry by default.
# Plugins can layer on top via `register_default_agents(my_registry)` or
# direct `DEFAULT_REGISTRY.register(...)`.
register_default_agents()
