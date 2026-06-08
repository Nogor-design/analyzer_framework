"""Daily-lineup selection (Phase 1).

Replaces the unaccountable hand-ChatGPT picker with a measured selector: a
transparent scorer that must beat dumb baselines out-of-sample on a walk-forward
replay before it is trusted. See ``docs/designs/daily_lineup_selector.md``.
"""
from .model import Candidate, SelectionContext, daily_metrics, window_pnls
from .baselines import DEFAULT_BASELINES
from .replay import compare_selectors, replay_selector

__all__ = [
    "Candidate",
    "SelectionContext",
    "daily_metrics",
    "window_pnls",
    "DEFAULT_BASELINES",
    "replay_selector",
    "compare_selectors",
]
