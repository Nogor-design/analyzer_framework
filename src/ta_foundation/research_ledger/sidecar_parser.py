"""Parse discovery `*_summary.json` sidecars into candidate dicts.

The existing `ta_foundation` discovery pipeline writes one summary sidecar per
report run, with a uniform shape (`schema_version: 1`):

    {
        "schema_version": 1,
        "stage": {"id": "...", "label": "...", "ordinal": int, "kind": "..."},
        "instrument": {"symbol": "NQ", "tick_size": ...},
        "rankings": [
            {
                "rank": int, "family": "<discovery family>",
                "signal": "...", "direction": "long|short|both",
                "timeframe": "1m|5m|...", "session_filter": {...},
                "params": {...}, "metrics": {...},
                "hardening": {"enabled": bool, "passed": bool,
                              "validation": {...},
                              "evaluation_oos": {...}, "slippage_stress": {...}},
                "tier": {"id": "...", "label": "...", "verdict": "..."},
                ...
            }
        ]
    }

This module:

    parse_summary_sidecar(path) -> ParsedSidecar

returns a typed snapshot containing the run-level metadata and a list of
candidate dicts ready for `record_candidates_for_run` (Phase A.2b).

The parser is deliberately lenient: missing optional fields produce None
values; unknown extras are preserved in `notes` so triage agents can read
them. Strict validation belongs upstream (the discovery pipeline that
writes the sidecar) and downstream (the triage agent's interpretation).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


@dataclass(frozen=True)
class ParsedSidecar:
    schema_version: int
    stage_id: Optional[str]
    instrument_symbol: Optional[str]
    timeframe: Optional[str]
    candidates: list[dict] = field(default_factory=list)
    n_rankings: int = 0
    raw_path: str = ""


def parse_summary_sidecar(path: Path | str) -> ParsedSidecar:
    """Read and parse a discovery summary sidecar."""
    p = Path(path)
    body = json.loads(p.read_text(encoding="utf-8"))
    return _parse_body(body, raw_path=str(p))


def parse_summary_body(body: dict, *, raw_path: str = "") -> ParsedSidecar:
    """Parse an already-loaded body. Test-friendly; production callers should
    use `parse_summary_sidecar`."""
    return _parse_body(body, raw_path=raw_path)


def candidate_id_for(run_id: str, rank_in_run: int) -> str:
    """Deterministic candidate id (`c_<run_short>_<rank:03d>`) - the convention
    the backfill importer established."""
    return f"c_{run_id.split('_')[-1]}_{rank_in_run:03d}"


def candidate_dicts_for_run(sidecar_path: Path | str, run_id: str) -> list[dict]:
    """Parse a sidecar and return candidate dicts ready for the
    `record_candidates_for_run` write tool - i.e. each carrying a minted
    `candidate_id`. The bare parser omits `candidate_id` because it has no
    run_id; this is the run-aware adapter the Sweep Operator needs."""
    parsed = parse_summary_sidecar(sidecar_path)
    return [
        {**c, "candidate_id": candidate_id_for(run_id, c["rank_in_run"])}
        for c in parsed.candidates
    ]


# ---- Internals -------------------------------------------------------------


def _parse_body(body: dict, *, raw_path: str) -> ParsedSidecar:
    schema_version = int(body.get("schema_version") or 0)
    stage = body.get("stage") or {}
    instrument = body.get("instrument") or {}
    rankings = body.get("rankings") or []

    timeframe: Optional[str] = None
    if isinstance(rankings, list):
        for r in rankings:
            if isinstance(r, dict) and r.get("timeframe"):
                timeframe = r["timeframe"]
                break

    candidates = [
        _ranking_to_candidate_dict(r, instrument)
        for r in (rankings if isinstance(rankings, list) else [])
        if isinstance(r, dict)
    ]

    return ParsedSidecar(
        schema_version=schema_version,
        stage_id=(stage.get("id") if isinstance(stage, dict) else None),
        instrument_symbol=(instrument.get("symbol") if isinstance(instrument, dict) else None),
        timeframe=timeframe,
        candidates=candidates,
        n_rankings=len(candidates),
        raw_path=raw_path,
    )


def _ranking_to_candidate_dict(r: dict, instrument: dict) -> dict:
    metrics = r.get("metrics") or {}
    hardening = r.get("hardening") or {}
    validation = hardening.get("validation") or {}
    evaluation_oos = hardening.get("evaluation_oos") or {}
    evaluation_holdout = hardening.get("evaluation_holdout") or {}
    slippage_stress = hardening.get("slippage_stress") or {}
    tier = r.get("tier") or {}
    params = r.get("params") or {}

    gate_verdict = _derive_gate_verdict(hardening, tier)
    gate_reasons = _derive_gate_reasons(hardening, validation)

    pf_dev = _coerce_float(metrics.get("profit_factor"))
    n_trades_dev = _coerce_int(metrics.get("trade_count"))
    expectancy_dev = _coerce_float(metrics.get("expectancy_ticks"))

    pf_oos = _coerce_float(evaluation_oos.get("profit_factor")) if evaluation_oos else None
    n_trades_oos = _coerce_int(evaluation_oos.get("trade_count") or evaluation_oos.get("n_trades")) if evaluation_oos else None
    expectancy_oos = _coerce_float(evaluation_oos.get("expectancy_ticks") or evaluation_oos.get("expectancy")) if evaluation_oos else None

    # evaluation_holdout uses the simulator's metric keys (n_trades, expectancy)
    # rather than the discovery-pipeline keys (trade_count, expectancy_ticks).
    pf_holdout = _coerce_float(evaluation_holdout.get("profit_factor")) if evaluation_holdout else None
    n_trades_holdout = _coerce_int(evaluation_holdout.get("n_trades") or evaluation_holdout.get("trade_count")) if evaluation_holdout else None
    expectancy_holdout = _coerce_float(evaluation_holdout.get("expectancy") or evaluation_holdout.get("expectancy_ticks")) if evaluation_holdout else None

    slippage_pass = _derive_slippage_pass(slippage_stress)

    notes = {
        "discovery_family": r.get("family"),
        "signal": r.get("signal"),
        "direction": r.get("direction"),
        "timeframe": r.get("timeframe"),
        "session_filter": r.get("session_filter"),
        "outcome": r.get("outcome"),
        "entry_timing": r.get("entry_timing"),
        "tier_id": tier.get("id"),
        "tier_label": tier.get("label"),
        "tier_verdict": tier.get("verdict"),
        "tier_criteria_met": tier.get("criteria_met"),
        "hardening_enabled": hardening.get("enabled"),
        "hardening_issues": hardening.get("issues"),
        "instrument_symbol": instrument.get("symbol") if isinstance(instrument, dict) else None,
        "regime_breakdown": r.get("regime_breakdown"),
        "session_breakdown": r.get("session_breakdown"),
        "metrics_extras": {
            k: v for k, v in metrics.items()
            if k not in {"profit_factor", "trade_count", "expectancy_ticks"}
        },
    }

    return {
        "rank_in_run": _coerce_int(r.get("rank"), default=0) or 0,
        "params": params,
        "gate_verdict": gate_verdict,
        "gate_reasons": gate_reasons,
        "n_trades_dev": n_trades_dev,
        "pf_dev": pf_dev,
        "expectancy_dev": expectancy_dev,
        "n_trades_oos": n_trades_oos,
        "pf_oos": pf_oos,
        "expectancy_oos": expectancy_oos,
        "n_trades_holdout": n_trades_holdout,
        "pf_holdout": pf_holdout,
        "expectancy_holdout": expectancy_holdout,
        "slippage_stress_pass": slippage_pass,
        "notes": _strip_none(notes),
    }


def _derive_gate_verdict(hardening: dict, tier: dict) -> str:
    """Return 'survivor' | 'rejected' | 'pending'.

    When hardening ran, the pass/fail flag is authoritative. Otherwise fall
    back to the discovery tier. The tier ids are the five emitted by
    ``ta_foundation.web.discovery_summary.classify_tier`` -
    most_robust > high_quality > solid > marginal > rejected. A fast probe's
    job is to surface candidates worth hardening, so the two research-grade
    tiers map to ``survivor`` (= promotable); the noise tiers map to
    ``rejected``; ``solid`` is left ``pending`` for triage to judge.
    (Legacy ids qualified/strong/reject/noise are kept for old sidecars.)
    """
    enabled = bool(hardening.get("enabled"))
    if enabled:
        return "survivor" if hardening.get("passed") is True else "rejected"
    tier_id = (tier.get("id") or "").lower()
    if tier_id in {"most_robust", "high_quality", "qualified", "strong"}:
        return "survivor"
    if tier_id in {"rejected", "reject", "marginal", "noise"}:
        return "rejected"
    return "pending"


def _derive_gate_reasons(hardening: dict, validation: dict) -> Optional[list[dict]]:
    gates = validation.get("gates") if isinstance(validation, dict) else None
    if not isinstance(gates, list):
        return None
    out: list[dict] = []
    for g in gates:
        if not isinstance(g, dict):
            continue
        out.append({
            "gate": g.get("name"),
            "passed": bool(g.get("passed")),
            "value": g.get("value"),
            "threshold": g.get("threshold"),
        })
    return out or None


def _derive_slippage_pass(slippage_stress: dict) -> Optional[bool]:
    if not slippage_stress:
        return None
    if "passed" in slippage_stress:
        return bool(slippage_stress["passed"])
    # Fall back to expectancy_loss_pct if present.
    expectancy_loss_pct = slippage_stress.get("expectancy_loss_pct")
    threshold = slippage_stress.get("max_expectancy_loss_pct")
    if isinstance(expectancy_loss_pct, (int, float)) and isinstance(threshold, (int, float)):
        return expectancy_loss_pct <= threshold
    return None


def _coerce_int(v: Any, *, default: Optional[int] = None) -> Optional[int]:
    if isinstance(v, bool):
        return default
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        if v != v:  # NaN
            return default
        return int(v)
    return default


def _coerce_float(v: Any) -> Optional[float]:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        f = float(v)
        if f != f:  # NaN
            return None
        return f
    return None


def _strip_none(d: dict) -> dict:
    return {k: v for k, v in d.items() if v is not None}


# ---- Family inference -----------------------------------------------------


# Heuristic mapping from (yaml filename, sidecar signal name) to one of the
# 13 starter agentic families. Anything that doesn't match falls back to
# 'legacy_imported' so the FK constraint is satisfied during backfill.
_FAMILY_RULES: list[tuple[tuple[str, ...], str]] = [
    (("vwap_reject_fade", "vwap_reclaim_reject", "vwap_distance_fade"), "vwap_reject_fade"),
    (("vwap_reclaim_continuation", "vwap_continuation"), "vwap_reclaim_continuation"),
    (("orb_failure_reclaim", "failure_reclaim"), "orb_failure_reclaim"),
    (("ny_open_orb", "orb_5m", "orb_breakout", "orb_fast", "orb_large_move"), "orb_breakout"),
    (("liquidity_sweep", "sweep_reclaim", "reference_sweep"), "overnight_high_low_sweep_reclaim"),
    (("prior_overnight", "prior_high_low", "failed_reference_breakout"), "prior_high_low_failed_breakout"),
    (("large_candle",), "large_candle_origin_retest"),
    (("compression",), "compression_then_expansion"),
    (("trend_pullback",), "trend_pullback_continuation"),
    (("ib_extension", "initial_balance_extension"), "initial_balance_extension"),
    (("ib_reversal", "initial_balance_reversal"), "initial_balance_reversal"),
    (("settlement", "prior_close"), "prior_close_settlement_reaction"),
    (("exhaustion",), "exhaustion_into_reference"),
]


def infer_family(yaml_filename: str, signal_name: Optional[str] = None) -> str:
    name = yaml_filename.lower()
    sig = (signal_name or "").lower()
    for needles, family_id in _FAMILY_RULES:
        for needle in needles:
            if needle in name or needle in sig:
                return family_id
    return "legacy_imported"
