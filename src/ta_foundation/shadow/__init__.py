"""Forward shadow observation runner (Phase D.1).

Builds on the same signal/outcome code path as the discovery simulator and
the locked-holdout simulator. The job is to *observe* enrolled candidates
on incrementally-arriving market data — never to place orders, never to
retune parameters. See `docs/designs/agentic_phase_d_forward_observation.md`.

Public surface:
    - run_shadow_pass(repo, bars_loader, ...) -> ShadowPassReport
    - InMemoryBarsDataSource, FolderBarsDataSource (data_source module)
    - ShadowNotSupported (raised for families that have no shadow adapter yet)
"""

from __future__ import annotations

from ta_foundation.shadow.data_source import (
    BarsDataSource,
    FolderBarsDataSource,
    InMemoryBarsDataSource,
)
from ta_foundation.shadow.health import (
    AnomalyFlag,
    CandidateShadowHealth,
    ShadowHealthReport,
    compute_daily_health,
)
from ta_foundation.shadow.health_report import render_health_markdown
from ta_foundation.shadow.runner import (
    ShadowPassCandidateResult,
    ShadowPassReport,
    run_shadow_pass,
)
from ta_foundation.shadow.simulator import ShadowNotSupported

__all__ = [
    "AnomalyFlag",
    "BarsDataSource",
    "CandidateShadowHealth",
    "FolderBarsDataSource",
    "InMemoryBarsDataSource",
    "ShadowHealthReport",
    "ShadowNotSupported",
    "ShadowPassCandidateResult",
    "ShadowPassReport",
    "compute_daily_health",
    "render_health_markdown",
    "run_shadow_pass",
]
