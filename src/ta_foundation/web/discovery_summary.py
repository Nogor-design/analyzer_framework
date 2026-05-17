from __future__ import annotations

"""
Sidecar JSON for Discovery stage runs.

Every stage run writes a `discovery_summary.json` next to its HTML report.
The web UI reads it to:
- render the Setup Cards on each Results pane
- compute pre-selected promotions for the next stage
- power the cross-stage validation aggregation in Stage 6

This module owns the schema, the tier classifier, and the package extractor.
The actual CLI hook that writes the file lives in a follow-up step
(`cli/main.py` — step 5b).

Schema is versioned via `SCHEMA_VERSION`. Older schema versions can still be
deserialized if needed (the loader checks the version). Bump only on
breaking changes.
"""

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from ta_foundation.web.discovery_instruments import Instrument, get_instrument
from ta_foundation.web.discovery_stages import (
    StageDefinition,
    get_family,
    get_stage_definition,
)


SCHEMA_VERSION = 1


# ---------------------------------------------------------------------------
# Tier classifier
# ---------------------------------------------------------------------------

TIER_MOST_ROBUST = "most_robust"
TIER_HIGH_QUALITY = "high_quality"
TIER_SOLID = "solid"
TIER_MARGINAL = "marginal"
TIER_REJECTED = "rejected"

_TIER_LABELS: dict[str, dict[str, str]] = {
    TIER_MOST_ROBUST:  {"label": "Most Robust",  "verdict": "Research candidate; harden before forward use."},
    TIER_HIGH_QUALITY: {"label": "High Quality", "verdict": "Research candidate; needs hardening."},
    TIER_SOLID:        {"label": "Solid",        "verdict": "Needs more data and validation before trading."},
    TIER_MARGINAL:     {"label": "Marginal",     "verdict": "Likely noise. Skip unless you have a reason."},
    TIER_REJECTED:     {"label": "Rejected",     "verdict": "Below profit-factor 1.0; do not trade."},
}


@dataclass(frozen=True)
class TierAssessment:
    id: str
    label: str
    verdict: str
    criteria_met: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "verdict": self.verdict,
            "criteria_met": list(self.criteria_met),
        }


def classify_tier(
    profit_factor: float,
    trade_count: int,
    is_oos_degradation: float | None,
    hardening_passed: bool | None = None,
) -> TierAssessment:
    """Classify a combo into one of five tiers.

    Mirrors the table in discovery/README.md:
      Most Robust  : PF >= 1.5 and n >= 30 and IS/OOS deg <= 0.10
      High Quality : PF >= 1.3 and n >= 20
      Solid        : PF >= 1.1 and n >= 15
      Marginal     : PF >= 1.0
      Rejected     : PF < 1.0
    """
    pf = float(profit_factor or 0.0)
    n = int(trade_count or 0)
    deg = float(is_oos_degradation) if is_oos_degradation is not None else None

    if hardening_passed is False and pf >= 1.0:
        return _make_tier(
            TIER_MARGINAL,
            [f"hardening gates failed", f"profit_factor = {pf:.2f}", f"trade_count = {n}"],
        )

    if pf < 1.0:
        return _make_tier(TIER_REJECTED, [f"profit_factor < 1.0 ({pf:.2f})"])

    if pf >= 1.5 and n >= 30 and deg is not None and deg <= 0.10:
        return _make_tier(
            TIER_MOST_ROBUST,
            [
                f"profit_factor >= 1.5 ({pf:.2f})",
                f"trade_count >= 30 ({n})",
                f"is_oos_degradation <= 0.10 ({deg:.2f})",
            ],
        )

    if pf >= 1.3 and n >= 20:
        criteria = [f"profit_factor >= 1.3 ({pf:.2f})", f"trade_count >= 20 ({n})"]
        if deg is not None:
            criteria.append(f"is_oos_degradation = {deg:.2f}")
        return _make_tier(TIER_HIGH_QUALITY, criteria)

    if pf >= 1.1 and n >= 15:
        return _make_tier(
            TIER_SOLID,
            [f"profit_factor >= 1.1 ({pf:.2f})", f"trade_count >= 15 ({n})"],
        )

    return _make_tier(TIER_MARGINAL, [f"profit_factor >= 1.0 ({pf:.2f})"])


def _make_tier(tier_id: str, criteria_met: list[str]) -> TierAssessment:
    meta = _TIER_LABELS[tier_id]
    return TierAssessment(
        id=tier_id,
        label=meta["label"],
        verdict=meta["verdict"],
        criteria_met=tuple(criteria_met),
    )


# ---------------------------------------------------------------------------
# Schema dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class StageInfo:
    id: str
    label: str
    ordinal: int
    kind: str       # "funnel" | "event_study"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class InstrumentInfo:
    symbol: str
    name: str
    tick_size: float
    tick_value: float
    point_value: float
    contract: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class InputSummary:
    bar_count: int | None = None
    date_range_local: tuple[str, str] | None = None
    session_filter: dict[str, int] | None = None
    session_label: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "bar_count": self.bar_count,
            "date_range_local": list(self.date_range_local) if self.date_range_local else None,
            "session_filter": dict(self.session_filter) if self.session_filter else None,
            "session_label": self.session_label,
        }


@dataclass(frozen=True)
class Diagnostics:
    total_combos_tested: int = 0
    combos_passing_min_trades: int = 0
    runtime_seconds: int | None = None
    warnings: tuple[str, ...] = ()
    # Per-family count of combos that produced metrics (pre-truncation, so the
    # UI can see "MA tested but produced 0 results" even when that family is
    # missing from the top-N rankings list).
    families_with_results: dict[str, int] = field(default_factory=dict)
    # Per-tier count over the rankings actually returned. Lets the UI tell at
    # a glance whether everything was rejected.
    tier_breakdown: dict[str, int] = field(default_factory=dict)
    # Plain-language explanation used by the empty-state banner. None when
    # there's at least one non-rejected ranking.
    empty_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_combos_tested": self.total_combos_tested,
            "combos_passing_min_trades": self.combos_passing_min_trades,
            "runtime_seconds": self.runtime_seconds,
            "warnings": list(self.warnings),
            "families_with_results": dict(self.families_with_results),
            "tier_breakdown": dict(self.tier_breakdown),
            "empty_reason": self.empty_reason,
        }


@dataclass(frozen=True)
class ComboMetrics:
    trade_count: int
    profit_factor: float
    win_rate: float
    expectancy_ticks: float | None = None
    avg_win_ticks: float | None = None
    avg_loss_ticks: float | None = None
    max_drawdown_ticks: float | None = None
    sharpe: float | None = None
    is_oos_degradation: float | None = None
    in_sample_pf: float | None = None
    out_of_sample_pf: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExplainStrings:
    what_it_trades: str
    when_it_works: str
    risks: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PromotePayload:
    next_stages: tuple[str, ...]
    yaml_overrides: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "next_stages": list(self.next_stages),
            "yaml_overrides": dict(self.yaml_overrides),
        }


@dataclass(frozen=True)
class RankingEntry:
    rank: int
    family: str
    signal: str
    timeframe: str
    direction: str
    entry_timing: str
    params: dict[str, Any]
    outcome: dict[str, Any]
    session_filter: dict[str, Any]
    metrics: ComboMetrics
    tier: TierAssessment
    explain: ExplainStrings
    promote_payload: PromotePayload
    hardening: dict[str, Any] = field(default_factory=dict)
    conditional_rules: tuple[dict[str, Any], ...] = ()
    # Sibling combos whose metrics were indistinguishable from this row's
    # (same family/signal/tf/direction/timing AND PF/trade_count/expectancy
    # match after rounding). Each entry surfaces the param keys that differ
    # so the user knows which knobs didn't move the needle on this dataset.
    variants: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "rank": self.rank,
            "family": self.family,
            "signal": self.signal,
            "timeframe": self.timeframe,
            "direction": self.direction,
            "entry_timing": self.entry_timing,
            "params": dict(self.params),
            "outcome": dict(self.outcome),
            "session_filter": dict(self.session_filter),
            "metrics": self.metrics.to_dict(),
            "tier": self.tier.to_dict(),
            "explain": self.explain.to_dict(),
            "promote_payload": self.promote_payload.to_dict(),
            "hardening": dict(self.hardening),
            "conditional_rules": [dict(r) for r in self.conditional_rules],
            "variants": [dict(v) for v in self.variants],
        }


@dataclass(frozen=True)
class NextStageRecommendation:
    stage_id: str
    reason: str
    preselect_ranks: tuple[int, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage_id": self.stage_id,
            "reason": self.reason,
            "preselect_ranks": list(self.preselect_ranks),
        }


@dataclass(frozen=True)
class DiscoverySummary:
    stage: StageInfo
    instrument: InstrumentInfo
    input_summary: InputSummary
    diagnostics: Diagnostics
    rankings: tuple[RankingEntry, ...]
    next_stage_recommendations: tuple[NextStageRecommendation, ...]
    report_html: str = ""
    generated_at: str = ""
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "stage": self.stage.to_dict(),
            "generated_at": self.generated_at,
            "report_html": self.report_html,
            "instrument": self.instrument.to_dict(),
            "input_summary": self.input_summary.to_dict(),
            "diagnostics": self.diagnostics.to_dict(),
            "rankings": [r.to_dict() for r in self.rankings],
            "next_stage_recommendations": [n.to_dict() for n in self.next_stage_recommendations],
        }


# ---------------------------------------------------------------------------
# Family-specific normalization
# ---------------------------------------------------------------------------

def _signal_label(family_id: str, signal_id: str) -> str:
    family = get_family(family_id)
    if family is None:
        return signal_id
    for sub in family.sub_signals:
        if sub["id"] == signal_id:
            return sub["label"]
    return signal_id


def _normalize_combo(family_id: str, raw: dict[str, Any]) -> dict[str, Any] | None:
    """Pull the fields we care about out of one sweep result row.

    Sweep rows from different families share most field names but vary in a few
    places (`pattern_id` vs `signal_id`, etc.). This function tolerates both.
    Returns None for rows that lack the metrics needed to score them.
    """
    metrics = raw.get("metrics") or {}
    pf = metrics.get("profit_factor")
    n = metrics.get("n_trades", raw.get("n_trades"))
    if pf is None or n is None:
        return None

    signal = (
        raw.get("signal_id")
        or raw.get("pattern_id")
        or raw.get("signal")
        or raw.get("pattern")
        or "unknown"
    )

    hardening = raw.get("hardening") if isinstance(raw.get("hardening"), dict) else {}
    hardening_degradation = _hardening_degradation(hardening)
    hardening_passed = raw.get("hardening_passed")
    if hardening_passed is None and hardening:
        hardening_passed = hardening.get("passed")

    return {
        "family": family_id,
        "signal": str(signal),
        "timeframe": _format_timeframe(raw.get("tf") or raw.get("timeframe")),
        "direction": str(raw.get("direction_mode") or raw.get("direction") or "both"),
        "entry_timing": str(raw.get("entry_timing") or raw.get("entry") or "next_open"),
        "params": dict(raw.get("params") or {}),
        "outcome": _extract_outcome(raw),
        "session_filter": dict(raw.get("session_filter") or {}),
        "profit_factor": float(pf),
        "trade_count": int(n),
        "win_rate": _opt_float(metrics.get("win_rate")),
        "expectancy_ticks": _opt_float(metrics.get("avg_trade")),
        "avg_win_ticks": _opt_float(metrics.get("avg_winner")),
        "avg_loss_ticks": _opt_float(metrics.get("avg_loser")),
        "max_drawdown_ticks": _opt_float(metrics.get("max_drawdown")),
        "sharpe": _opt_float(metrics.get("sharpe_ratio")),
        "is_oos_degradation": _opt_float(
            hardening_degradation
            if hardening_degradation is not None
            else raw.get("is_oos_degradation")
        ),
        "in_sample_pf": _opt_float(metrics.get("in_sample_pf")),
        "out_of_sample_pf": _opt_float(metrics.get("out_of_sample_pf")),
        "hardening": hardening,
        "hardening_passed": hardening_passed if isinstance(hardening_passed, bool) else None,
        "conditional_rules": _extract_conditional_rules(raw),
    }


def _extract_conditional_rules(raw: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    ed = raw.get("entry_discovery")
    if not isinstance(ed, dict):
        return ()
    rules = ed.get("top_rules") or ed.get("rules") or []
    if not isinstance(rules, list):
        return ()
    out: list[dict[str, Any]] = []
    for rule in rules[:5]:
        if isinstance(rule, dict):
            out.append(dict(rule))
    return tuple(out)


def _hardening_degradation(hardening: dict[str, Any]) -> float | None:
    validation = hardening.get("validation") or {}
    wf = validation.get("wf_results") or {}
    return _opt_float(wf.get("oos_degradation"))


def _format_timeframe(tf: Any) -> str:
    if tf is None:
        return ""
    if isinstance(tf, (int, float)) and not isinstance(tf, bool):
        return f"{int(tf)}m"
    return str(tf)


def _extract_outcome(raw: dict[str, Any]) -> dict[str, Any]:
    outcome_mode = raw.get("outcome_mode") or "ticks"
    out: dict[str, Any] = {"mode": str(outcome_mode)}
    params = raw.get("params") or {}
    for key in ("tp_ticks", "sl_ticks", "max_bars_timeout"):
        if key in params:
            out[key] = params[key]
        elif key in raw:
            out[key] = raw[key]
    return out


def _opt_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# How tightly two combos must match before we treat them as indistinguishable.
# Trade counts are integers so exact equality is the right test. Floats get
# rounded; 4 decimal places is well below the noise floor of any single-trade
# difference and well above floating-point jitter from PF arithmetic.
_DEDUPE_PF_DECIMALS = 4
_DEDUPE_EXPECTANCY_DECIMALS = 4


def _dedupe_signature(combo: dict[str, Any]) -> tuple:
    """Build a hashable key that captures everything we'd need to consider
    two combos indistinguishable. If two normalized combos produce the same
    key, their differing params didn't move the metrics on this dataset.
    """
    pf = combo.get("profit_factor")
    pf_rounded = round(float(pf), _DEDUPE_PF_DECIMALS) if pf is not None else None
    expectancy = combo.get("expectancy_ticks")
    if expectancy is None:
        expectancy_rounded = None
    else:
        try:
            expectancy_rounded = round(float(expectancy), _DEDUPE_EXPECTANCY_DECIMALS)
        except (TypeError, ValueError):
            expectancy_rounded = None
    return (
        combo.get("family"),
        combo.get("signal"),
        combo.get("timeframe"),
        combo.get("direction"),
        combo.get("entry_timing"),
        pf_rounded,
        combo.get("trade_count"),
        expectancy_rounded,
    )


def _diff_params(kept: dict[str, Any], sibling: dict[str, Any]) -> dict[str, Any]:
    """Return the keys whose values differ between two param dicts.

    The returned dict carries ONLY the differing keys with the sibling's
    values, so the UI can show "min_range_ticks: 8" without re-listing
    every shared parameter.
    """
    out: dict[str, Any] = {}
    for k, v in (sibling or {}).items():
        if (kept or {}).get(k) != v:
            out[k] = v
    return out


def _dedupe_near_identical(combos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse runs of indistinguishable combos into a single entry.

    Combos must already be sorted by PF desc - the first hit on any
    signature wins (which preserves the user-visible ordering). Each kept
    combo gains a `_variants` list containing the dropped siblings'
    differing params/outcome keys. The leading underscore keeps the field
    out of `RankingEntry.params`; we copy it onto `RankingEntry.variants`
    when building the entries.
    """
    seen: dict[tuple, dict[str, Any]] = {}
    out: list[dict[str, Any]] = []
    for combo in combos:
        key = _dedupe_signature(combo)
        existing = seen.get(key)
        if existing is None:
            seen[key] = combo
            combo["_variants"] = []
            out.append(combo)
            continue
        # Fold this combo into the previously kept one. Capture only the
        # params (and outcome knobs) that actually differ.
        diff = _diff_params(existing.get("params") or {}, combo.get("params") or {})
        # Outcome knobs that differ are interesting too - e.g. two ORB rows
        # with different TP/SL ratios that produce the same metrics.
        outcome_diff = _diff_params(existing.get("outcome") or {}, combo.get("outcome") or {})
        if outcome_diff:
            # Tag outcome-only diffs so the UI can render them distinctly.
            for k, v in outcome_diff.items():
                diff.setdefault(f"outcome.{k}", v)
        existing["_variants"].append({"params": diff})
    return out


# ---------------------------------------------------------------------------
# Explain strings
# ---------------------------------------------------------------------------


def _explain_detail_parts(family: str, combo: dict[str, Any]) -> list[str]:
    """Format family-relevant disambiguating params into short tag-style
    fragments. Returns a list of strings ready for ", ".join().

    The selection is intentionally narrow - only the knobs that actually
    differentiate one combo from another. Anything universal (tick_size,
    atr_period) is omitted. Outcome TP/SL are appended last.
    """
    params = combo.get("params") or {}
    outcome = combo.get("outcome") or {}
    parts: list[str] = []

    if family == "orb":
        if params.get("orb_minutes") is not None:
            parts.append(f"{params['orb_minutes']}m opening range")
        if params.get("min_range_ticks") is not None:
            parts.append(f">={params['min_range_ticks']}t min range")
        if params.get("require_close_beyond"):
            parts.append("close-beyond required")
    elif family == "candle":
        if params.get("body_multiplier") is not None:
            parts.append(f"{params['body_multiplier']}x body multiplier")
        if params.get("body_to_range_max") is not None:
            parts.append(f"body/range <= {params['body_to_range_max']}")
        if params.get("wick_to_body_min") is not None:
            parts.append(f"wick/body >= {params['wick_to_body_min']}")
    elif family == "ma":
        fast = params.get("fast_period") or params.get("fast")
        slow = params.get("slow_period") or params.get("slow")
        if fast is not None and slow is not None:
            parts.append(f"MA {fast}/{slow}")
    elif family == "bb":
        period = params.get("period") or params.get("bb_period")
        std = params.get("std_dev") or params.get("std")
        if period is not None and std is not None:
            parts.append(f"BB {period}/{std}")
    elif family == "lcr":
        size_mult = params.get("size_multiplier") or params.get("multiplier")
        if size_mult is not None:
            parts.append(f"{size_mult}x size mult")
        if params.get("lookback") is not None:
            parts.append(f"{params['lookback']}-bar lookback")
        if params.get("zone_type"):
            parts.append(f"{params['zone_type']} zone")
    elif family == "breakout":
        n_bars = params.get("n_bars") or params.get("lookback")
        if n_bars is not None:
            parts.append(f"{n_bars}-bar breakout")
    elif family == "level":
        if params.get("touch_count") is not None:
            parts.append(f"{params['touch_count']} touches")
        if params.get("level_kind"):
            parts.append(f"{params['level_kind']} level")
    elif family == "pullback":
        if params.get("trend_period") is not None:
            parts.append(f"{params['trend_period']}-period trend")
        if params.get("retracement_pct") is not None:
            parts.append(f"{params['retracement_pct']}% retrace")

    # TP/SL applies to every family. The outcome dict has these reliably;
    # the params dict sometimes does too. Outcome wins.
    tp = outcome.get("tp_ticks", params.get("tp_ticks"))
    sl = outcome.get("sl_ticks", params.get("sl_ticks"))
    if tp is not None and sl is not None:
        parts.append(f"TP {tp}t / SL {sl}t")
    elif tp is not None:
        parts.append(f"TP {tp}t")
    elif sl is not None:
        parts.append(f"SL {sl}t")

    sf = combo.get("session_filter") or {}
    if isinstance(sf, dict) and sf.get("hour_from") is not None and sf.get("hour_to") is not None:
        hf = sf["hour_from"]
        mf = sf.get("minute_from") or 0
        ht = sf["hour_to"]
        parts.append(f"session {hf:02d}:{mf:02d}-{ht:02d}:00")

    return parts


def _build_explain(combo: dict[str, Any]) -> ExplainStrings:
    family = combo["family"]
    signal_label = _signal_label(family, combo["signal"])
    tf = combo["timeframe"] or "minute"
    direction = combo["direction"]
    entry = combo["entry_timing"].replace("_", " ")
    n = combo["trade_count"]
    deg = combo.get("is_oos_degradation")

    direction_phrase = {
        "long": "long-only",
        "short": "short-only",
        "both": "long and short",
    }.get(direction, direction)

    # Append the disambiguating params so two cards that differ only in a
    # single knob (e.g. orb_minutes 5 vs 15, or body_multiplier 1.5 vs 2.5)
    # can be told apart at a glance instead of forcing the user to expand
    # both and diff the params dict.
    detail_parts = _explain_detail_parts(family, combo)
    detail_str = (" " + ", ".join(detail_parts) + ".") if detail_parts else ""

    what = (
        f'"{signal_label}" on {tf} bars, {direction_phrase}, '
        f"entered on the {entry}.{detail_str}"
    )

    if combo.get("max_drawdown_ticks") is not None and combo.get("max_drawdown_ticks") < -100:
        when = "Best in trending periods; this combo's drawdown has been deep historically."
    elif (combo.get("win_rate") or 0) >= 0.55:
        when = "High win-rate setup; works best when the win pattern persists."
    else:
        when = "Profit factor edge comes from larger winners than losers."

    risks: list[str] = []
    if n < 30:
        risks.append(f"Sample size is small ({n} trades) - wider confidence interval.")
    if deg is None:
        risks.append("In-sample / out-of-sample degradation not yet measured.")
    elif deg > 0.30:
        risks.append(f"IS/OOS degradation is high ({deg:.0%}); edge may not survive live.")
    if not risks:
        risks.append("Standard backtest caveats - past performance doesn't guarantee future results.")

    return ExplainStrings(
        what_it_trades=what,
        when_it_works=when,
        risks=" ".join(risks),
    )


# ---------------------------------------------------------------------------
# Promote payload — yaml_overrides for the next stage
# ---------------------------------------------------------------------------

def _build_promote_payload(combo: dict[str, Any], stage: StageDefinition) -> PromotePayload:
    family = combo["family"]
    next_stages = tuple(stage.next_stage_recommendations)

    builders = {
        "candle":   _promote_candle,
        "ma":       _promote_ma,
        "orb":      _promote_orb,
        "bb":       _promote_bb,
        "lcr":      _promote_lcr,
        "breakout": _promote_breakout,
        "pullback": _promote_pullback,
        "level":    _promote_level,
        "large_candle_excursion": _promote_lce,
    }
    builder = builders.get(family)
    overrides = builder(combo) if builder else {}
    return PromotePayload(next_stages=next_stages, yaml_overrides=overrides)


def _outcome_overrides(combo: dict[str, Any]) -> dict[str, Any]:
    """Build the {outcome: {ticks: {...}}} override block from a combo's TP/SL."""
    out = combo.get("outcome") or {}
    tp = out.get("tp_ticks")
    sl = out.get("sl_ticks")
    block: dict[str, Any] = {}
    if tp is not None or sl is not None:
        ticks: dict[str, Any] = {"enabled": True}
        if tp is not None:
            ticks["take_profit"] = [tp]
        if sl is not None:
            ticks["stop"] = [sl]
        block["outcome"] = {"ticks": ticks}
    return block


def _entry_timing_override(combo: dict[str, Any]) -> dict[str, Any]:
    et = combo.get("entry_timing") or "next_open"
    block = {
        "next_open":     {"enabled": et == "next_open"},
        "break_extreme": {"enabled": et == "break_extreme"},
        "body_midpoint": {"enabled": et == "body_midpoint"},
    }
    return {"entry_timing": block}


def _timeframe_override(combo: dict[str, Any]) -> dict[str, Any]:
    tf = combo.get("timeframe") or ""
    if tf.endswith("m"):
        try:
            return {"timeframes": [int(tf[:-1])]}
        except ValueError:
            return {}
    return {}


def _promote_candle(combo: dict[str, Any]) -> dict[str, Any]:
    pattern_id = combo["signal"]
    params = combo.get("params") or {}
    pattern_block: dict[str, Any] = {"enabled": True}
    for key, value in params.items():
        # Wrap scalars as single-value lists since candle pattern params are sweep lists.
        if isinstance(value, list):
            pattern_block[key] = value
        else:
            pattern_block[key] = [value]

    candle_block: dict[str, Any] = {
        "enabled": True,
        "patterns": {pattern_id: pattern_block},
    }
    candle_block.update(_timeframe_override(combo))
    candle_block.update(_outcome_overrides(combo))
    candle_block.update(_entry_timing_override(combo))
    if combo.get("session_filter"):
        candle_block["session_filter"] = combo["session_filter"]
    if combo.get("direction"):
        candle_block["direction"] = combo["direction"]
    return {"candle_discovery": candle_block}


def _promote_ma(combo: dict[str, Any]) -> dict[str, Any]:
    signal_id = combo["signal"]
    params = combo.get("params") or {}
    signal_block: dict[str, Any] = {"enabled": True}
    for key, value in params.items():
        signal_block[key] = value if isinstance(value, list) else [value]
    block: dict[str, Any] = {
        "enabled": True,
        "signals": {signal_id: signal_block},
    }
    block.update(_timeframe_override(combo))
    block.update(_outcome_overrides(combo))
    block.update(_entry_timing_override(combo))
    return {"ma_discovery": block}


def _promote_orb(combo: dict[str, Any]) -> dict[str, Any]:
    params = combo.get("params") or {}
    block: dict[str, Any] = {"enabled": True}
    if "orb_minutes" in params:
        block["orb"] = {"orb_minutes": [params["orb_minutes"]]}
    elif "orb_window_min" in params:
        block["orb"] = {"orb_minutes": [params["orb_window_min"]]}
    block.update(_outcome_overrides(combo))
    block.update(_entry_timing_override(combo))
    return {"orb_discovery": block}


def _promote_bb(combo: dict[str, Any]) -> dict[str, Any]:
    signal_id = combo["signal"]
    params = combo.get("params") or {}
    signal_block: dict[str, Any] = {"enabled": True}
    for key, value in params.items():
        signal_block[key] = value if isinstance(value, list) else [value]
    block: dict[str, Any] = {
        "enabled": True,
        "signals": {signal_id: signal_block},
    }
    block.update(_timeframe_override(combo))
    block.update(_outcome_overrides(combo))
    block.update(_entry_timing_override(combo))
    return {"bb_discovery": block}


def _promote_lcr(combo: dict[str, Any]) -> dict[str, Any]:
    signal_type = combo["signal"]
    params = combo.get("params") or {}
    block: dict[str, Any] = {
        "enabled": True,
        "signal_types": [signal_type],
    }
    if "size_multiplier" in params:
        block["size_multipliers"] = [params["size_multiplier"]]
    if "lookback" in params:
        block["lookbacks"] = [params["lookback"]]
    if "zone_type" in params:
        block["zone_types"] = [params["zone_type"]]
    out = combo.get("outcome") or {}
    if out.get("tp_ticks") is not None:
        block["tp_ticks"] = [out["tp_ticks"]]
    if out.get("sl_ticks") is not None:
        block["sl_ticks"] = [out["sl_ticks"]]
    return {"lcr_discovery": block}


def _promote_breakout(combo: dict[str, Any]) -> dict[str, Any]:
    return _promote_simple_signal_family(combo, yaml_block="breakout_discovery")


def _promote_pullback(combo: dict[str, Any]) -> dict[str, Any]:
    return _promote_simple_signal_family(combo, yaml_block="pullback_discovery")


def _promote_level(combo: dict[str, Any]) -> dict[str, Any]:
    return _promote_simple_signal_family(combo, yaml_block="level_discovery")


def _promote_simple_signal_family(combo: dict[str, Any], *, yaml_block: str) -> dict[str, Any]:
    signal_id = combo["signal"]
    params = combo.get("params") or {}
    signal_block: dict[str, Any] = {"enabled": True}
    for key, value in params.items():
        signal_block[key] = value if isinstance(value, list) else [value]
    block: dict[str, Any] = {
        "enabled": True,
        "signals": {signal_id: signal_block},
    }
    if "lookback_bars" in params:
        block["lookback_bars"] = [params["lookback_bars"]]
    block.update(_timeframe_override(combo))
    block.update(_outcome_overrides(combo))
    return {yaml_block: block}


def _promote_lce(combo: dict[str, Any]) -> dict[str, Any]:
    return {"large_candle_excursion": {"enabled": True}}


# ---------------------------------------------------------------------------
# Builder — assemble a DiscoverySummary from extracted combo rows
# ---------------------------------------------------------------------------

def build_summary(
    *,
    stage_id: str,
    instrument_symbol: str,
    raw_results: dict[str, list[dict[str, Any]]],
    contract: str = "",
    input_summary: InputSummary | None = None,
    runtime_seconds: int | None = None,
    warnings: Iterable[str] = (),
    report_html_path: str | Path = "",
    top_n: int = 25,
    generated_at: str | None = None,
) -> DiscoverySummary:
    """Assemble a DiscoverySummary from sweep results.

    Args:
        stage_id: which stage emitted these results (e.g. "01_quick_scan")
        instrument_symbol: which instrument was tested
        raw_results: mapping of family_id -> list of raw sweep result dicts
                     (the same shape stored under pkg.metadata['derived'][<key>])
        contract: optional contract code (e.g. "H25")
        input_summary: bar count, date range, session window
        runtime_seconds: how long the run took, if measured
        warnings: any warnings to surface in the diagnostics block
        report_html_path: path to the HTML report this sidecar accompanies
        top_n: how many top-ranked combos to retain (after sorting by PF desc)
        generated_at: ISO timestamp; defaults to now in UTC
    """
    stage = get_stage_definition(stage_id)
    if stage is None:
        raise ValueError(f"Unknown stage id: {stage_id}")

    instrument = get_instrument(instrument_symbol)
    if instrument is None:
        raise ValueError(f"Unknown instrument: {instrument_symbol}")

    # Normalize and rank. We track two parallel counts: total rows seen across
    # all families, and per-family "passing" counts so the UI can report which
    # families produced anything at all (vs. which were tested but emitted no
    # combos).
    combos: list[dict[str, Any]] = []
    total = 0
    passing = 0
    families_with_results: dict[str, int] = {}
    for family_id in stage.enabled_families:
        rows = raw_results.get(family_id) or []
        family_count = 0
        for raw in rows:
            total += 1
            normalized = _normalize_combo(family_id, raw)
            if normalized is None:
                continue
            passing += 1
            family_count += 1
            combos.append(normalized)
        # Always record an entry per enabled family - 0 is informative.
        families_with_results[family_id] = family_count

    combos.sort(key=lambda c: c["profit_factor"], reverse=True)
    # Dedupe near-identical rankings BEFORE truncation. Two combos that share
    # family/signal/tf/direction/timing AND produce indistinguishable metrics
    # (PF and expectancy match after rounding, trade_count is exactly equal)
    # are folded into one row - the user shouldn't have to scroll past four
    # cards that score identically because one knob didn't filter anything.
    combos = _dedupe_near_identical(combos)
    combos = combos[:top_n]

    # Build ranking entries
    rankings: list[RankingEntry] = []
    for idx, combo in enumerate(combos, start=1):
        tier = classify_tier(
            combo["profit_factor"],
            combo["trade_count"],
            combo.get("is_oos_degradation"),
            combo.get("hardening_passed"),
        )
        explain = _build_explain(combo)
        promote = _build_promote_payload(combo, stage)
        metrics = ComboMetrics(
            trade_count=combo["trade_count"],
            profit_factor=combo["profit_factor"],
            win_rate=combo.get("win_rate") or 0.0,
            expectancy_ticks=combo.get("expectancy_ticks"),
            avg_win_ticks=combo.get("avg_win_ticks"),
            avg_loss_ticks=combo.get("avg_loss_ticks"),
            max_drawdown_ticks=combo.get("max_drawdown_ticks"),
            sharpe=combo.get("sharpe"),
            is_oos_degradation=combo.get("is_oos_degradation"),
            in_sample_pf=combo.get("in_sample_pf"),
            out_of_sample_pf=combo.get("out_of_sample_pf"),
        )
        rankings.append(
            RankingEntry(
                rank=idx,
                family=combo["family"],
                signal=combo["signal"],
                timeframe=combo["timeframe"],
                direction=combo["direction"],
                entry_timing=combo["entry_timing"],
                params=combo.get("params") or {},
                outcome=combo.get("outcome") or {},
                session_filter=combo.get("session_filter") or {},
                metrics=metrics,
                tier=tier,
                explain=explain,
                promote_payload=promote,
                hardening=combo.get("hardening") or {},
                conditional_rules=tuple(combo.get("conditional_rules") or ()),
                variants=tuple(combo.get("_variants") or ()),
            )
        )

    # Tier breakdown over the rankings we actually return. Used by the UI's
    # empty-state banner to detect "everything rejected" without re-scanning.
    tier_breakdown: dict[str, int] = {}
    for entry in rankings:
        tid = entry.tier.id
        tier_breakdown[tid] = tier_breakdown.get(tid, 0) + 1

    bar_count = input_summary.bar_count if input_summary else None
    empty_reason = _empty_reason(
        total=total,
        passing=passing,
        rankings=rankings,
        tier_breakdown=tier_breakdown,
        bar_count=bar_count,
    )

    # The recommendations builder needs the tier breakdown so it can pivot
    # to "longer history / different instrument / try LCE" instead of
    # silently returning [] when there's no edge.
    next_recs = _build_next_stage_recommendations(
        stage,
        rankings,
        tier_breakdown=tier_breakdown,
        bar_count=bar_count,
    )

    return DiscoverySummary(
        stage=StageInfo(
            id=stage.id,
            label=stage.label,
            ordinal=stage.ordinal,
            kind=stage.kind,
        ),
        instrument=_make_instrument_info(instrument, contract=contract),
        input_summary=input_summary or InputSummary(),
        diagnostics=Diagnostics(
            total_combos_tested=total,
            combos_passing_min_trades=passing,
            runtime_seconds=runtime_seconds,
            warnings=tuple(warnings),
            families_with_results=families_with_results,
            tier_breakdown=tier_breakdown,
            empty_reason=empty_reason,
        ),
        rankings=tuple(rankings),
        next_stage_recommendations=tuple(next_recs),
        report_html=str(report_html_path),
        generated_at=generated_at or _now_iso(),
        schema_version=SCHEMA_VERSION,
    )


def _empty_reason(
    *,
    total: int,
    passing: int,
    rankings: list[RankingEntry],
    tier_breakdown: dict[str, int],
    bar_count: int | None,
) -> str | None:
    """Return a plain-language reason when the user sees nothing useful.

    The categories we want to distinguish for the UI banner:
    - no rows arrived at all (sweep didn't produce anything testable)
    - rows arrived but none had enough trades to score
    - rows scored but every one of them is in the rejected tier

    Returns None when at least one non-rejected ranking exists.
    """
    if not rankings:
        if total == 0:
            return (
                "The sweep produced no results. The selected families may not have "
                "fired any signals on this market data, or the market data "
                "folder didn't supply usable bars."
            )
        if passing == 0:
            return (
                "The sweep produced rows, but none had enough trades to score. "
                "Lower min_trades, widen the date range, or pick a different stage."
            )
        return "No combos produced metrics suitable for ranking."
    rejected = tier_breakdown.get(TIER_REJECTED, 0)
    if rejected == len(rankings):
        msg = (
            "Every ranked setup failed the quality gates (profit factor below 1.0). "
            "This is a valid 'no edge' result for the dataset you tested."
        )
        if bar_count is not None and bar_count < 50_000:
            msg += (
                " Your sample is small (~"
                f"{bar_count:,} bars) - try a longer history before drawing conclusions."
            )
        return msg
    return None


def _make_instrument_info(inst: Instrument, *, contract: str) -> InstrumentInfo:
    return InstrumentInfo(
        symbol=inst.symbol,
        name=inst.name,
        tick_size=inst.tick_size,
        tick_value=inst.tick_value,
        point_value=inst.point_value,
        contract=contract,
    )


def _build_next_stage_recommendations(
    stage: StageDefinition,
    rankings: list[RankingEntry],
    *,
    tier_breakdown: dict[str, int] | None = None,
    bar_count: int | None = None,
) -> list[NextStageRecommendation]:
    """Decide which next stages (or other actions) the user should consider.

    Three branches:
      - At least one ranking with PF >= 1.2 in a non-rejected tier:
        recommend the next funnel stage that deep-dives that family.
      - Empty or all-rejected: surface "longer history" / "different
        instrument" / "LCE" as actionable structured entries instead of
        leaving the list empty. The UI renders these the same way.
      - This stage's `next_stage_recommendations` registry is empty AND
        we have winners: nothing to suggest (stage 6 etc.).
    """
    rejected_count = (tier_breakdown or {}).get(TIER_REJECTED, 0)
    is_no_edge_outcome = (
        not rankings
        or (rejected_count > 0 and rejected_count == len(rankings))
    )

    if is_no_edge_outcome:
        return _empty_case_recommendations(stage, bar_count=bar_count)

    if not stage.next_stage_recommendations:
        return []

    # Group rankings by family and find the families with at least one combo at PF >= 1.2
    families_with_edge: dict[str, list[int]] = {}
    for entry in rankings:
        if entry.metrics.profit_factor >= 1.2 and entry.tier.id != TIER_REJECTED:
            families_with_edge.setdefault(entry.family, []).append(entry.rank)

    if not families_with_edge:
        return []

    recommendations: list[NextStageRecommendation] = []
    family_to_stage = {
        "candle":   "02_candle_patterns",
        "lcr":      "03_levels_regions",
        "breakout": "03_levels_regions",
        "level":    "03_levels_regions",
        "orb":      "05_orb_momentum",
        "bb":       "05_orb_momentum",
        "ma":       "05_orb_momentum",
        "pullback": "05_orb_momentum",
    }
    seen_stages: set[str] = set()
    for family_id, ranks in families_with_edge.items():
        suggested = family_to_stage.get(family_id)
        if not suggested or suggested in seen_stages:
            continue
        if suggested not in stage.next_stage_recommendations:
            continue
        seen_stages.add(suggested)
        recommendations.append(
            NextStageRecommendation(
                stage_id=suggested,
                reason=(
                    f"{family_id} family had {len(ranks)} setup(s) above PF 1.2 - "
                    f"worth deep-diving."
                ),
                preselect_ranks=tuple(sorted(ranks)[:5]),
            )
        )

    return recommendations


# Sentinel "stage" ids used by the empty-case recommendations. They aren't
# real funnel stages - the UI renders them as informational links / hints
# rather than as a dispatch target.
NEXT_STAGE_LONGER_HISTORY = "_action.longer_history"
NEXT_STAGE_DIFFERENT_INSTRUMENT = "_action.different_instrument"
NEXT_STAGE_TRY_LCE = "large_candle_excursion"


def _empty_case_recommendations(
    stage: StageDefinition,
    *,
    bar_count: int | None,
) -> list[NextStageRecommendation]:
    """Recommendations to surface when the user got no edge or no rankings.

    Order matters - the UI renders them top-down. We lead with the most
    actionable suggestion for the typical scenario (small dataset).
    """
    recs: list[NextStageRecommendation] = []

    if bar_count is not None and bar_count < 200_000:
        # Roughly six months of 1m RTH bars. Below that the dataset is too
        # short to trust either a "no-edge" or an "edge" verdict.
        recs.append(NextStageRecommendation(
            stage_id=NEXT_STAGE_LONGER_HISTORY,
            reason=(
                f"Only ~{bar_count:,} bars were tested - extend the date range "
                f"to at least 6-12 months before treating this as a verdict."
            ),
        ))

    recs.append(NextStageRecommendation(
        stage_id=NEXT_STAGE_DIFFERENT_INSTRUMENT,
        reason=(
            "Some markets simply don't have edge for these signal families. "
            "Switch the Instrument picker (top-right) and re-run this stage."
        ),
    ))

    # Skip the LCE suggestion when the user is already on the LCE page.
    if stage.id != "large_candle_excursion":
        recs.append(NextStageRecommendation(
            stage_id=NEXT_STAGE_TRY_LCE,
            reason=(
                "Switch to the Large Candle Excursion event-study page (LCE link "
                "in the header) to learn how this market behaves after impulse "
                "moves before sweeping again."
            ),
        ))

    return recs


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

def write_summary(summary: DiscoverySummary, path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(summary.to_dict(), indent=2, ensure_ascii=False)
    target.write_text(text, encoding="utf-8")
    return target


# ---------------------------------------------------------------------------
# CLI integration helper
# ---------------------------------------------------------------------------

# Sweep modules use either `sweep_results` (most families) or `results` (LCR).
_RAW_ROW_KEYS = ("sweep_results", "results")


def write_sidecar_for_run(
    *,
    packages: dict[str, Any],
    discovery_block: dict[str, Any],
    report_html_path: str | Path,
    runtime_seconds: int | None = None,
    warnings: Iterable[str] = (),
    bars_1m: Any = None,
    family_configs: dict[str, dict[str, Any]] | None = None,
) -> Path | None:
    """Write `discovery_summary.json` next to a report when a `discovery:` block is present.

    The CLI calls this once per rendered report. `discovery_block` is the
    `discovery:` top-level dict in the report YAML, e.g.

        discovery:
          stage: "01_quick_scan"
          instrument: "NQ"
          contract: "H25"

    Returns the sidecar path on success, or None if the block is unusable
    (missing/unknown stage or instrument). Errors during the build itself
    propagate so callers can log them — the report HTML still gets written.
    """
    if not isinstance(discovery_block, dict):
        return None
    stage_id = str(discovery_block.get("stage") or "").strip()
    instrument_symbol = str(discovery_block.get("instrument") or "").strip()
    if not stage_id or not instrument_symbol:
        return None

    stage = get_stage_definition(stage_id)
    if stage is None:
        return None

    raw_results = _collect_raw_results(packages, stage)
    family_configs = family_configs or {}

    # Inject session_filter from each family's YAML config into every row
    # that doesn't already carry one. The sweep orchestrators apply the
    # session filter upstream (by slicing bars_1m) but never echo it back
    # onto each result row, so without this the sidecar reports
    # session_filter: {} on every ranking even when one was applied.
    stage_session_filter: dict[str, Any] | None = None
    for family_id, rows in raw_results.items():
        cfg_block = family_configs.get(family_id) or {}
        cfg_filter = cfg_block.get("session_filter") if isinstance(cfg_block, dict) else None
        if not isinstance(cfg_filter, dict) or not cfg_filter:
            continue
        if stage_session_filter is None:
            stage_session_filter = dict(cfg_filter)
        for row in rows:
            if not isinstance(row, dict):
                continue
            existing = row.get("session_filter")
            if not isinstance(existing, dict) or not existing:
                row["session_filter"] = dict(cfg_filter)

    input_summary = _build_input_summary_from_bars(
        bars_1m,
        discovery_block.get("session_label"),
        session_filter=stage_session_filter,
    )

    summary = build_summary(
        stage_id=stage_id,
        instrument_symbol=instrument_symbol,
        raw_results=raw_results,
        contract=str(discovery_block.get("contract") or ""),
        input_summary=input_summary,
        runtime_seconds=runtime_seconds,
        warnings=warnings,
        report_html_path=report_html_path,
    )

    target = sidecar_path_for_report(report_html_path)
    return write_summary(summary, target)


def _collect_raw_results(
    packages: dict[str, Any],
    stage: StageDefinition,
) -> dict[str, list[dict[str, Any]]]:
    """Pull sweep result rows from any package — they all carry the same dict."""
    out: dict[str, list[dict[str, Any]]] = {}
    if not packages:
        return out

    family_to_yaml = {fam.id: fam.yaml_block for fam in (get_family(fid) for fid in stage.enabled_families) if fam}
    for family_id, yaml_block in family_to_yaml.items():
        rows = _first_nonempty_rows(packages, yaml_block)
        if rows:
            out[family_id] = rows
    return out


def _first_nonempty_rows(packages: dict[str, Any], derived_key: str) -> list[dict[str, Any]]:
    for pkg in packages.values():
        derived = (getattr(pkg, "metadata", {}) or {}).get("derived") or {}
        block = derived.get(derived_key)
        if not isinstance(block, dict):
            continue
        for key in _RAW_ROW_KEYS:
            rows = block.get(key)
            if isinstance(rows, list) and rows:
                return rows
    return []


def _build_input_summary_from_bars(
    bars: Any,
    session_label: Any,
    *,
    session_filter: dict[str, Any] | None = None,
) -> InputSummary:
    # InputSummary stores session_filter as Optional[dict[str, int]]; coerce
    # YAML values defensively so non-int defaults don't blow up the dataclass.
    sf: dict[str, int] | None = None
    if isinstance(session_filter, dict) and session_filter:
        sf = {}
        for k, v in session_filter.items():
            try:
                sf[str(k)] = int(v)
            except (TypeError, ValueError):
                continue
        if not sf:
            sf = None

    if bars is None:
        return InputSummary(session_label=str(session_label or ""), session_filter=sf)
    try:
        n = int(len(bars))
    except TypeError:
        return InputSummary(session_label=str(session_label or ""), session_filter=sf)
    date_range: tuple[str, str] | None = None
    try:
        if "dt" in getattr(bars, "columns", []):
            dt_series = bars["dt"]
        else:
            dt_series = getattr(bars, "index", None)
        if dt_series is not None and n:
            start = str(dt_series.iloc[0] if hasattr(dt_series, "iloc") else dt_series[0])
            end = str(dt_series.iloc[-1] if hasattr(dt_series, "iloc") else dt_series[-1])
            date_range = (start, end)
    except Exception:
        date_range = None
    return InputSummary(
        bar_count=n,
        date_range_local=date_range,
        session_label=str(session_label or ""),
        session_filter=sf,
    )


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


# Pre-2026-05 builds wrote one shared file per output folder, which silently
# overwrote earlier stages' sidecars. We now derive the sidecar name from the
# report HTML's stem so each stage has its own JSON. The old name is kept as
# a read-time fallback so reports rendered before the change still surface.
_LEGACY_SIDECAR_FILENAME = "discovery_summary.json"


def sidecar_path_for_report(report_html_path: str | Path) -> Path:
    """Return the canonical sidecar path for a given report HTML path.

    `01_quick_scan.html` -> `01_quick_scan_summary.json`
    """
    p = Path(report_html_path)
    return p.with_name(f"{p.stem}_summary.json")


def resolve_sidecar_path(report_html_path: str | Path) -> Path | None:
    """Find an existing sidecar for a report, preferring the per-stage name
    and falling back to the legacy shared filename. Returns None if neither
    exists.
    """
    canonical = sidecar_path_for_report(report_html_path)
    if canonical.exists():
        return canonical
    legacy = Path(report_html_path).with_name(_LEGACY_SIDECAR_FILENAME)
    if legacy.exists():
        return legacy
    return None


def read_summary(path: str | Path) -> dict[str, Any]:
    """Read a discovery_summary.json. Returns the raw dict — the UI can
    render directly from it without re-hydrating dataclasses.
    """
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"discovery_summary at {path} is not a JSON object")
    version = data.get("schema_version")
    if version != SCHEMA_VERSION:
        # For now we only read the current version. When SCHEMA_VERSION bumps,
        # add migration here rather than rejecting older files outright.
        raise ValueError(
            f"discovery_summary at {path} has schema_version {version}, "
            f"expected {SCHEMA_VERSION}"
        )
    return data


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
