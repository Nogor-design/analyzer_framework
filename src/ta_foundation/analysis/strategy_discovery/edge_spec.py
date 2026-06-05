from __future__ import annotations

"""
EdgeSpec — the confirmable description of a discovered edge.
============================================================
Bridges discovery results and the NinjaTrader recipe optimizer. An EdgeSpec
captures everything needed to (a) reproduce a discovered entry in
StrategyDiscoveryFilter and (b) judge whether an NT recipe run confirmed the
edge or it decayed / diverged.

This module is pure (no web/optimizer imports) so it stays analysis-layer. The
recipe document is built in web/optimizer_recipe_from_edge.py from an EdgeSpec.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ta_foundation.analysis.strategy_discovery.nt_template_generator import (
    _STRUCTURE_TO_ENTRY_SIGNAL,
    _TIMING_TO_ENUM,
)


# Structures whose direction is inherent (others trade both sides).
_STRUCTURE_DIRECTION = {
    "pin_bar_bullish": 1,
    "engulfing_bullish": 1,
    "pin_bar_bearish": -1,
    "engulfing_bearish": -1,
}


def _safe_float(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    import math
    return None if math.isnan(f) or math.isinf(f) else f


def _safe_int(v: Any) -> Optional[int]:
    f = _safe_float(v)
    return None if f is None else int(round(f))


@dataclass
class EdgeSpec:
    """A discovered edge, ready to confirm in NinjaTrader."""

    structure: str
    entry_signal: str                       # C# SdfEntrySignal enum name
    family: str = "candle"
    timeframe_minutes: int = 1
    timing_mode: str = "next_open"          # discovery-style name
    direction: int = 0                      # 1 long / -1 short / 0 both
    stop_ticks: Optional[int] = None
    target_ticks: Optional[int] = None
    entry_params: Dict[str, Any] = field(default_factory=dict)
    regime_mode: Optional[str] = None       # C# SdfMarketRegimeMode name, if pinned

    # Observed metrics — the bar the NT run must clear.
    observed_pf: Optional[float] = None
    observed_win_rate: Optional[float] = None
    observed_n: Optional[int] = None

    rule_str: str = ""
    source_run_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "structure": self.structure,
            "entry_signal": self.entry_signal,
            "family": self.family,
            "timeframe_minutes": self.timeframe_minutes,
            "timing_mode": self.timing_mode,
            "direction": self.direction,
            "stop_ticks": self.stop_ticks,
            "target_ticks": self.target_ticks,
            "entry_params": dict(self.entry_params),
            "regime_mode": self.regime_mode,
            "observed_pf": self.observed_pf,
            "observed_win_rate": self.observed_win_rate,
            "observed_n": self.observed_n,
            "rule_str": self.rule_str,
            "source_run_id": self.source_run_id,
        }

    def timing_enum(self) -> str:
        return _TIMING_TO_ENUM.get(str(self.timing_mode).lower(), "NextOpen")

    def template_options(self) -> Dict[str, Any]:
        """Options dict for nt_template_generator.generate_nt_template, so the
        seed baseline carries this exact entry."""
        ep = dict(self.entry_params)
        ep.setdefault("timing_mode", self.timing_mode)
        return {
            "entry_signal": self.entry_signal,
            "entry_params": ep,
            "timeframe_minutes": self.timeframe_minutes,
        }


def _structure_from_conditions(conditions: List[Dict[str, Any]]) -> Optional[str]:
    for cond in conditions or []:
        if str(cond.get("column")) == "structure":
            return str(cond.get("value"))
    return None


def _regime_from_conditions(conditions: List[Dict[str, Any]]) -> Optional[str]:
    for cond in conditions or []:
        if str(cond.get("column")) == "regime":
            return str(cond.get("value"))
    return None


def _entry_signal_for(structure: Optional[str]) -> Optional[str]:
    if not structure:
        return None
    return _STRUCTURE_TO_ENTRY_SIGNAL.get(structure)


def edge_spec_from_rule(
    rule: Dict[str, Any],
    *,
    timeframe_minutes: int = 1,
    timing_mode: str = "next_open",
    entry_params: Optional[Dict[str, Any]] = None,
    stop_ticks: Optional[int] = None,
    target_ticks: Optional[int] = None,
    observed_pf: Optional[float] = None,
    source_run_id: str = "",
) -> Optional[EdgeSpec]:
    """
    Build an EdgeSpec from a single signal_entry_discovery rule dict. Returns
    None if the rule carries no recognizable entry structure.
    """
    conditions = rule.get("conditions") or []
    structure = _structure_from_conditions(conditions)
    entry_signal = _entry_signal_for(structure)
    if not entry_signal:
        return None

    direction = _STRUCTURE_DIRECTION.get(structure, 0)
    return EdgeSpec(
        structure=structure,
        entry_signal=entry_signal,
        family="candle",
        timeframe_minutes=int(timeframe_minutes or 1),
        timing_mode=timing_mode,
        direction=direction,
        stop_ticks=_safe_int(stop_ticks),
        target_ticks=_safe_int(target_ticks),
        entry_params=dict(entry_params or {}),
        regime_mode=None,  # regime mapping is left to the generator's heuristics
        observed_pf=_safe_float(observed_pf if observed_pf is not None else rule.get("profit_factor")),
        observed_win_rate=_safe_float(rule.get("win_rate")),
        observed_n=_safe_int(rule.get("n_signals")),
        rule_str=str(rule.get("rule_str") or ""),
        source_run_id=source_run_id,
    )


def edge_spec_from_discovery(
    sd: Dict[str, Any],
    *,
    run_id: str = "",
    timeframe_minutes: int = 1,
    timing_mode: str = "next_open",
    options: Optional[Dict[str, Any]] = None,
) -> Optional[EdgeSpec]:
    """
    Build the top EdgeSpec from a strategy_discovery dict, preferring the best
    signal rule and pulling stop/target + observed PF from the exit sweep.
    """
    options = options or {}
    sed = sd.get("signal_entry_discovery") or {}
    rules = sed.get("top_signal_rules") or []
    if not rules:
        return None

    ses = sd.get("signal_exit_sweep") or {}
    overall = ses.get("overall_best") or {}
    stop_ticks = overall.get("stop")
    target_ticks = overall.get("target")
    observed_pf = overall.get("avg_profit_factor")

    # Walk rules until one yields a recognizable structure.
    for rule in rules:
        spec = edge_spec_from_rule(
            rule,
            timeframe_minutes=options.get("timeframe_minutes", timeframe_minutes),
            timing_mode=options.get("timing_mode", timing_mode),
            entry_params=options.get("entry_params"),
            stop_ticks=stop_ticks,
            target_ticks=target_ticks,
            observed_pf=observed_pf,
            source_run_id=run_id,
        )
        if spec is not None:
            return spec
    return None


# ---------------------------------------------------------------------------
# Confirmation verdict — does the NT result match the discovery?
# ---------------------------------------------------------------------------

@dataclass
class ConfirmationVerdict:
    verdict: str               # confirmed | decayed | diverged | underpowered
    nt_pf: Optional[float]
    observed_pf: Optional[float]
    pf_ratio: Optional[float]
    nt_n: Optional[int]
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "verdict": self.verdict,
            "nt_pf": self.nt_pf,
            "observed_pf": self.observed_pf,
            "pf_ratio": self.pf_ratio,
            "nt_n": self.nt_n,
            "notes": self.notes,
        }


def compare_to_discovery(
    edge: EdgeSpec,
    nt_pf: Optional[float],
    nt_n: Optional[int],
    *,
    pf_tolerance: float = 0.20,
    min_trades: int = 20,
) -> ConfirmationVerdict:
    """
    Judge an NT recipe result against the discovered edge.

    verdicts:
      underpowered — too few NT trades to judge (nt_n < min_trades)
      diverged     — NT PF <= 1.0 (no edge in NT). Likely a parity bug OR the
                     discovered edge was an artifact. Investigate parity FIRST.
      decayed      — NT has an edge (PF > 1) but materially below discovery
                     (more than pf_tolerance below observed).
      confirmed    — NT PF within tolerance of (or above) the discovered PF.
    """
    obs = _safe_float(edge.observed_pf)
    pf = _safe_float(nt_pf)
    ratio = (pf / obs) if (pf is not None and obs not in (None, 0)) else None

    if nt_n is not None and nt_n < min_trades:
        return ConfirmationVerdict(
            "underpowered", pf, obs, ratio, nt_n,
            f"Only {nt_n} NT trades (< {min_trades}); result not yet judgeable.",
        )

    if pf is None:
        return ConfirmationVerdict("underpowered", pf, obs, ratio, nt_n, "No NT profit factor available.")

    if pf <= 1.0:
        return ConfirmationVerdict(
            "diverged", pf, obs, ratio, nt_n,
            "NT shows no edge (PF <= 1.0). Check C#/Python parity before trusting either side.",
        )

    if obs is None:
        return ConfirmationVerdict(
            "confirmed", pf, obs, ratio, nt_n,
            "NT shows an edge (PF > 1.0); no discovered PF on record to compare against.",
        )

    if pf >= obs * (1.0 - pf_tolerance):
        return ConfirmationVerdict(
            "confirmed", pf, obs, ratio, nt_n,
            f"NT PF {pf:.2f} within {pf_tolerance:.0%} of discovered {obs:.2f}.",
        )

    return ConfirmationVerdict(
        "decayed", pf, obs, ratio, nt_n,
        f"NT PF {pf:.2f} is more than {pf_tolerance:.0%} below discovered {obs:.2f}.",
    )
