"""
Specialist horizon agents.

Specialists are thin wrappers over `AnalogueProbabilityAgent` configured to
emphasize one conditioning dimension (regime or session) over the other.
They share the same prediction shape as the base agent so the ensemble can
combine them without special cases.

  - **Regime specialist** filters analogues to bars whose regime matches the
    asof bar (`trend_up` / `trend_down` / `range`). Falls back to unfiltered
    only when fewer than `min_k_local` analogues match the regime.
  - **Session specialist** filters analogues to bars whose session label
    matches the asof bar (`asia` / `london` / `ny_open` / …). Falls back
    to unfiltered when fewer than `min_k_local` analogues match.

Both specialists use the same `_filter_with_fallback` from the base agent —
the regime-only branch was added in Phase 4 so a regime specialist actually
gets a regime-matched neighbor pool.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Optional

from .analogue_probability_agent import (
    AnalogueProbabilityAgent,
    AnalogueProbabilityAgentConfig,
)

REGIME_SPECIALIST_AGENT_ID = "regime_specialist_v1"
SESSION_SPECIALIST_AGENT_ID = "session_specialist_v1"


def make_regime_specialist_agent(
    config: Optional[AnalogueProbabilityAgentConfig] = None,
    agent_id: str = REGIME_SPECIALIST_AGENT_ID,
) -> AnalogueProbabilityAgent:
    """
    Build an AnalogueProbabilityAgent that always requires regime match
    and never requires session match. Other parameters fall through to
    the supplied config (or to AnalogueProbabilityAgentConfig defaults).
    """
    cfg = config or AnalogueProbabilityAgentConfig()
    cfg = replace(cfg, require_session_match=False, require_regime_match=True)
    return AnalogueProbabilityAgent(config=cfg, agent_id=agent_id)


def make_session_specialist_agent(
    config: Optional[AnalogueProbabilityAgentConfig] = None,
    agent_id: str = SESSION_SPECIALIST_AGENT_ID,
) -> AnalogueProbabilityAgent:
    """
    Build an AnalogueProbabilityAgent that always requires session match
    and never requires regime match. Other parameters fall through.
    """
    cfg = config or AnalogueProbabilityAgentConfig()
    cfg = replace(cfg, require_session_match=True, require_regime_match=False)
    return AnalogueProbabilityAgent(config=cfg, agent_id=agent_id)
