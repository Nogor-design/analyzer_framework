"""Selector v1 — the transparent composite scorer (NOT an LLM).

The candidate we must prove beats the baselines (``baselines.py``) on the replay
harness (``replay.py``). Per ``docs/designs/daily_lineup_selector.md``: a
robustness gate drops fake-edge-smelling templates, then a documented weighted
sum of normalized features ranks the rest, with the upcoming regime weighted in.

**Leakage discipline (the whole point):** every feature is computed on
``ctx.train_days`` only — the *daily* P&L statistics, never the static
full-window manifest metrics (PF/net/permutation-p), which were computed over a
span that includes the test day and would leak. The static manifest features are
legitimate for the LIVE selector (where "train" == all history before today);
they are deliberately NOT used in the backtest-bootstrap replay.

Diversity is structural: the daily lineup picks one template per slice, so spread
across sessions/tiers is automatic — no extra correlation cap needed here.

Weights/thresholds are config, version-stamped, and tunable (an LLM may *propose*
new weight sets to test through the harness — it never picks live).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from .model import Candidate, SelectionContext, daily_metrics, window_pnls


@dataclass(frozen=True)
class ScoringConfig:
    version: str = "v1.0"
    # feature -> weight in the composite (higher composite => more likely picked).
    # survival is weighted on par with expectancy per the design (prop accounts die
    # on drawdown, not on missed upside).
    weights: dict[str, float] = field(default_factory=lambda: {
        "expectancy": 1.0,   # train mean daily P&L
        "daily_pf": 0.5,     # train daily profit factor
        "green_pct": 0.5,    # consistency: share of active days that are green
        "survival": 1.0,     # train max drawdown (less negative is better)
        "regime_fit": 0.75,  # train mean daily P&L in the upcoming session's regime
        "activity": 0.25,    # active-day count (trade activity ~ robustness; anti-noise)
    })
    min_active_days: int = 3     # robustness gate: too-few-active-day templates are noise
    min_daily_pf: float = 1.0    # robustness gate: drop train-window losers


def _train_features(cand: Candidate, ctx: SelectionContext) -> dict[str, Any]:
    pnls = window_pnls(cand, ctx.train_days)
    m = daily_metrics(pnls)
    active = [p for p in pnls if p != 0.0]
    green_pct = (sum(1 for p in active if p > 0) / len(active)) if active else 0.0
    if ctx.regime_for_test_day is not None and ctx.regime_by_day:
        rdays = [d for d in ctx.train_days if ctx.regime_by_day.get(d) == ctx.regime_for_test_day]
        regime_fit = daily_metrics(window_pnls(cand, rdays))["mean_daily"] if rdays else m["mean_daily"]
    else:
        regime_fit = m["mean_daily"]
    pf = m["daily_pf"]
    pf_val = pf if (pf is not None and pf != float("inf")) else (2.0 if m["net"] > 0 else 0.0)
    return {
        "expectancy": m["mean_daily"],
        "daily_pf": pf_val,
        "green_pct": green_pct,
        "survival": m["max_drawdown"],
        "regime_fit": regime_fit,
        "activity": float(m["n_active_days"]),
        "_n_active": m["n_active_days"],
        "_daily_pf_raw": pf,
    }


def _passes_gate(feats: dict[str, Any], cfg: ScoringConfig) -> bool:
    if feats["_n_active"] < cfg.min_active_days:
        return False
    pf = feats["_daily_pf_raw"]
    if pf is not None and pf != float("inf") and pf < cfg.min_daily_pf:
        return False
    return True


def _normalize(values: Sequence[float]) -> list[float]:
    lo, hi = min(values), max(values)
    if hi - lo < 1e-12:
        return [0.5 for _ in values]   # all equal -> neutral
    return [(v - lo) / (hi - lo) for v in values]


def make_composite_selector(config: Optional[ScoringConfig] = None):
    """Return a replay-compatible selector that picks the top composite-scored
    template per slice. Falls back to the full slice when the robustness gate
    empties it, so a lineup is always fielded."""
    cfg = config or ScoringConfig()
    keys = list(cfg.weights)

    def selector(candidates: Sequence[Candidate], ctx: SelectionContext) -> list[Candidate]:
        if not candidates:
            return []
        feats = {c.template_id: _train_features(c, ctx) for c in candidates}
        gated = [c for c in candidates if _passes_gate(feats[c.template_id], cfg)]
        pool = gated or list(candidates)
        norm: dict[str, dict[str, float]] = {}
        for k in keys:
            col = [feats[c.template_id][k] for c in pool]
            ids = [c.template_id for c in pool]
            norm[k] = dict(zip(ids, _normalize(col)))

        def score(c: Candidate) -> float:
            return sum(cfg.weights[k] * norm[k][c.template_id] for k in keys)

        return [max(pool, key=score)]

    return selector


# Default-config selector for convenience.
composite_selector = make_composite_selector()


def make_topk_composite_selector(k: int, config: Optional[ScoringConfig] = None):
    """Return a selector that fields the top-``k`` composite-scored templates per
    slice (equal-weight), interpolating between the two extremes already measured:
    ``k == 1`` is the pure composite pick; ``k >= slice size`` is ``equal_weight``.

    Why this exists: the Phase-1 replay showed top-1 selection LOSES to full
    diversification on survival (concentration costs drawdown). A basket tests
    whether *some* selection skill survives once diversification is preserved — the
    honest next question, run through the same harness. The robustness gate still
    applies; the gate-survivors are ranked and the top-k taken (or the whole gated
    pool when it has <= k survivors)."""
    cfg = config or ScoringConfig()
    keys = list(cfg.weights)

    def selector(candidates: Sequence[Candidate], ctx: SelectionContext) -> list[Candidate]:
        if not candidates:
            return []
        feats = {c.template_id: _train_features(c, ctx) for c in candidates}
        gated = [c for c in candidates if _passes_gate(feats[c.template_id], cfg)]
        pool = gated or list(candidates)
        norm: dict[str, dict[str, float]] = {}
        for key in keys:
            ids = [c.template_id for c in pool]
            norm[key] = dict(zip(ids, _normalize([feats[c.template_id][key] for c in pool])))

        def score(c: Candidate) -> float:
            return sum(cfg.weights[key] * norm[key][c.template_id] for key in keys)

        return sorted(pool, key=score, reverse=True)[: max(1, k)]

    return selector
