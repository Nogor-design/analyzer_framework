from __future__ import annotations

"""Causal context scoring and activation for adaptive large-candle events.

The gate consumes the complete paired event streams from ``adaptive_window``
and the decision-time features from ``adaptive_context``.  It intentionally
keeps lane and signal-side histories separate, and every historical outcome
must be known strictly before the score timestamp.
"""

from collections import Counter, defaultdict
from datetime import date, time
from math import exp, isfinite, log, sqrt
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from ta_foundation.analysis.large_candle_excursion.adaptive_context import (
    attach_context_to_events,
    build_intraday_context,
    structurally_aligned_mode,
)
from ta_foundation.analysis.large_candle_excursion.adaptive_window import (
    DEFAULT_ADAPTIVE_WINDOW_CONFIG,
    build_adaptive_event_streams,
)


MODES = ("continuation", "reversion")
TREND_STATES = ("up", "down", "mixed")
GATE_STATES = ("OFF", "WATCH", "ON", "DECAYING")

CONTEXT_GATE_PROFILES: Dict[str, Dict[str, Any]] = {
    "fast": {
        "training_days": 10,
        "half_life_days": 3.0,
        "min_local_signals": 6,
    },
    "balanced": {
        "training_days": 15,
        "half_life_days": 5.0,
        "min_local_signals": 8,
    },
    "slow": {
        "training_days": 20,
        "half_life_days": 8.0,
        "min_local_signals": 12,
    },
}

DEFAULT_CONTEXT_GATE_CONFIG: Dict[str, Any] = {
    "enabled": True,
    "profile": "balanced",
    "replay_protocol": "daily_frozen",
    "policy": "aligned_only",
    "min_unique_sessions": 3,
    "prior_strength": 5.0,
    "confidence_z": 0.5,
    "min_expected_net_ticks": 0.0,
    "min_lower_bound_ticks": 0.0,
    "mode_margin_ticks": 5.0,
    "max_stale_days": None,
    "fast_half_life_days": 3.0,
    "slow_half_life_days": 8.0,
    "fast_decay_ratio": 0.5,
}


def run_adaptive_context_gate(
    bars_1m: pd.DataFrame,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Run one causal context-gate profile, policy, and replay protocol.

    Phase 4's research runner can call this API for each named profile and
    protocol.  This function returns complete JSON-safe decision and state
    ledgers rather than a UI sample.
    """
    cfg = _deep_merge(DEFAULT_ADAPTIVE_WINDOW_CONFIG, config or {})
    gate_cfg = _resolve_gate_config(cfg)
    if not gate_cfg["enabled"]:
        return {"enabled": False}
    if bars_1m is None or bars_1m.empty:
        return {
            "enabled": True,
            "message": "no minute bars",
            "streams": [],
        }

    context_frame = build_intraday_context(bars_1m, cfg)
    raw_streams = build_adaptive_event_streams(bars_1m, cfg)
    replay_sessions = (
        sorted(
            context_frame["session_id"].dropna().astype(str).unique().tolist()
        )
        if "session_id" in context_frame.columns
        else None
    )
    stream_results: List[Dict[str, Any]] = []
    for stream in raw_streams:
        events = attach_context_to_events(stream["events"], context_frame, cfg)
        stream_results.append(
            _run_stream_replay(
                events,
                stream,
                cfg,
                gate_cfg,
                replay_sessions=replay_sessions,
            )
        )

    result = {
        "enabled": True,
        "n_bars": int(len(context_frame)),
        "start": _iso(context_frame["dt"].min()),
        "end": _iso(context_frame["dt"].max()),
        "n_events": int(sum(item["n_events"] for item in stream_results)),
        "n_streams": int(len(stream_results)),
        "profile": gate_cfg["profile"],
        "policy": gate_cfg["policy"],
        "replay_protocol": gate_cfg["replay_protocol"],
        "streams": stream_results,
        "config": {
            "context": _context_config_payload(cfg),
            "context_gate": gate_cfg,
            "signal_and_outcome": cfg,
        },
        "methodology": {
            "outcome_eligibility": (
                "Both paired outcomes must have exit_known_dt strictly before "
                "the score-map timestamp."
            ),
            "hierarchical_backoff": (
                "Local time_bucket x trend_state estimates shrink to the "
                "time_bucket parent, which shrinks to lane x signal-side history."
            ),
            "execution": (
                "Only ON decisions consume per-direction capacity; WATCH and "
                "DECAYING remain paper signals."
            ),
        },
    }
    return _json_safe(result)


def eligible_history(
    events: Sequence[Mapping[str, Any]],
    decision_asof: Any,
    config: Mapping[str, Any],
) -> List[Mapping[str, Any]]:
    """Return paired outcomes causally eligible at ``decision_asof``.

    Equality is deliberately excluded.  Training recency is measured from the
    signal timestamp, while weighting below is measured from when each outcome
    became known.
    """
    asof = _aware_timestamp(decision_asof, label="decision_asof")
    training_days = max(1, int(config.get("training_days", 15)))
    earliest = asof - pd.Timedelta(days=training_days)
    eligible: List[Mapping[str, Any]] = []
    for event in events:
        signal_dt = _event_timestamp(event)
        if not (earliest <= signal_dt < asof):
            continue
        paired = True
        for mode in MODES:
            outcome = event.get(mode) or {}
            if not outcome.get("available"):
                paired = False
                break
            exit_known = _aware_timestamp(
                outcome.get("exit_known_dt"),
                label=f"{mode}.exit_known_dt",
            )
            if exit_known >= asof:
                paired = False
                break
        if paired:
            eligible.append(event)
    return eligible


def score_context_cell(
    events: Sequence[Mapping[str, Any]],
    decision_asof: Any,
    time_bucket: str,
    trend_state: str,
    config: Mapping[str, Any],
) -> Dict[str, Any]:
    """Score both modes through local, parent, and stream evidence levels."""
    asof = _aware_timestamp(decision_asof, label="decision_asof")
    completed = eligible_history(events, asof, config)
    parent_rows = [
        row for row in completed
        if str(row.get("time_bucket")) == str(time_bucket)
    ]
    local_rows = [
        row for row in parent_rows
        if str(row.get("trend_state")) == str(trend_state)
    ]

    modes: Dict[str, Dict[str, Any]] = {}
    for mode in MODES:
        hierarchy = _score_mode_hierarchy(
            local_rows,
            parent_rows,
            completed,
            mode,
            asof,
            config,
            half_life_days=float(config["half_life_days"]),
        )
        fast = _score_mode_hierarchy(
            local_rows,
            parent_rows,
            completed,
            mode,
            asof,
            config,
            half_life_days=float(config["fast_half_life_days"]),
        )
        slow = _score_mode_hierarchy(
            local_rows,
            parent_rows,
            completed,
            mode,
            asof,
            config,
            half_life_days=float(config["slow_half_life_days"]),
        )
        hierarchy["local"]["fast_posterior_mean_ticks"] = fast["local"][
            "posterior_mean_ticks"
        ]
        hierarchy["local"]["slow_posterior_mean_ticks"] = slow["local"][
            "posterior_mean_ticks"
        ]
        modes[mode] = hierarchy

    local_n = min(modes[mode]["local"]["n"] for mode in MODES)
    unique_sessions = min(
        modes[mode]["local"]["unique_sessions"] for mode in MODES
    )
    newest = max(
        (
            modes[mode]["local"]["newest_eligible_outcome_dt"]
            for mode in MODES
            if modes[mode]["local"]["newest_eligible_outcome_dt"] is not None
        ),
        default=None,
    )
    return {
        "decision_asof": asof,
        "time_bucket": str(time_bucket),
        "trend_state": str(trend_state),
        "paired_history_n": int(len(completed)),
        "local_n": int(local_n),
        "local_unique_sessions": int(unique_sessions),
        "newest_local_outcome_dt": newest,
        "modes": modes,
    }


def select_context_policy(
    score: Mapping[str, Any],
    signal_side: str,
    config: Mapping[str, Any],
) -> Dict[str, Any]:
    """Convert a paired cell score into an auditable target state."""
    trend_state = str(score.get("trend_state") or "mixed").lower()
    policy = str(config.get("policy", "aligned_only")).lower()
    if policy not in {"aligned_only", "evidence_only"}:
        raise ValueError("policy must be 'aligned_only' or 'evidence_only'")

    if trend_state == "mixed":
        return _assessment("OFF", "MIXED_TREND")

    local = {
        mode: score["modes"][mode]["local"]
        for mode in MODES
    }
    evidence_winner = max(
        MODES,
        key=lambda mode: float(local[mode]["posterior_mean_ticks"]),
    )
    aligned_mode = structurally_aligned_mode(trend_state, signal_side)

    newest = score.get("newest_local_outcome_dt")
    max_stale_days = float(config["max_stale_days"])
    if newest is not None:
        asof = _aware_timestamp(score["decision_asof"], label="decision_asof")
        age_days = (
            asof - _aware_timestamp(newest, label="newest_local_outcome_dt")
        ).total_seconds() / 86400.0
        if age_days > max_stale_days:
            return _assessment(
                "OFF",
                "STALE_MAP",
                evidence_winner=evidence_winner,
                aligned_mode=aligned_mode,
            )

    if int(score.get("local_n") or 0) < int(config["min_local_signals"]):
        return _assessment(
            "OFF",
            "INSUFFICIENT_LOCAL_HISTORY",
            evidence_winner=evidence_winner,
            aligned_mode=aligned_mode,
        )
    if int(score.get("local_unique_sessions") or 0) < int(
        config["min_unique_sessions"]
    ):
        return _assessment(
            "OFF",
            "INSUFFICIENT_UNIQUE_DAYS",
            evidence_winner=evidence_winner,
            aligned_mode=aligned_mode,
        )

    if policy == "aligned_only":
        if evidence_winner != aligned_mode:
            return _assessment(
                "OFF",
                "STRUCTURAL_MODE_MISMATCH",
                evidence_winner=evidence_winner,
                aligned_mode=aligned_mode,
            )
        selected_mode = aligned_mode
    else:
        selected_mode = evidence_winner

    if selected_mode is None:
        return _assessment("OFF", "MIXED_TREND")
    other_mode = (
        "reversion" if selected_mode == "continuation" else "continuation"
    )
    selected = local[selected_mode]
    posterior = float(selected["posterior_mean_ticks"])
    advantage = posterior - float(local[other_mode]["posterior_mean_ticks"])
    common = {
        "selected_mode": selected_mode,
        "evidence_winner": evidence_winner,
        "aligned_mode": aligned_mode,
        "mode_advantage_ticks": round(advantage, 3),
    }

    if posterior <= float(config["min_expected_net_ticks"]):
        return _assessment("OFF", "POSTERIOR_NOT_POSITIVE", **common)
    if advantage < float(config["mode_margin_ticks"]):
        return _assessment("OFF", "MODE_ADVANTAGE_TOO_SMALL", **common)
    if float(selected["lower_bound_ticks"]) < float(
        config["min_lower_bound_ticks"]
    ):
        return _assessment("WATCH", "LOWER_BOUND_NOT_POSITIVE", **common)

    fast = float(selected["fast_posterior_mean_ticks"])
    slow = float(selected["slow_posterior_mean_ticks"])
    decay_ratio = max(0.0, float(config.get("fast_decay_ratio", 0.5)))
    fast_decay = slow > 0.0 and fast < slow * decay_ratio
    return _assessment(
        "ON",
        "QUALIFIED",
        fast_score_decay=fast_decay,
        **common,
    )


def transition_gate_state(
    previous_state: str,
    assessment: Mapping[str, Any],
) -> Dict[str, Any]:
    """Apply the OFF/WATCH/ON/DECAYING state machine."""
    previous = str(previous_state or "OFF").upper()
    target = str(assessment.get("target_state") or "OFF").upper()
    if previous not in GATE_STATES:
        raise ValueError(f"invalid previous state: {previous_state!r}")
    if target not in {"OFF", "WATCH", "ON"}:
        raise ValueError(f"invalid target state: {target!r}")

    reason = str(assessment.get("reason_code") or "POSTERIOR_NOT_POSITIVE")
    forced_off = reason in {"MIXED_TREND", "STALE_MAP"}
    if forced_off:
        state = "OFF"
    elif target == "OFF":
        state = "DECAYING" if previous == "ON" else "OFF"
    elif target == "WATCH":
        state = "DECAYING" if previous in {"ON", "DECAYING"} else "WATCH"
    elif bool(assessment.get("fast_score_decay")) and previous in {
        "ON",
        "DECAYING",
    }:
        state = "DECAYING"
        reason = "FAST_SCORE_DECAY"
    elif previous == "OFF":
        state = "WATCH"
        reason = "WATCH_CONFIRMATION_REQUIRED"
    else:
        state = "ON"

    return {
        "previous_state": previous,
        "state": state,
        "reason_code": reason,
        "transition": f"{previous}->{state}",
    }


def _run_stream_replay(
    events: Sequence[Mapping[str, Any]],
    stream: Mapping[str, Any],
    config: Mapping[str, Any],
    gate_config: Mapping[str, Any],
    *,
    replay_sessions: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    ordered = sorted(events, key=_event_timestamp)
    protocol = str(gate_config["replay_protocol"])
    if protocol == "event_updated":
        decisions, state_ledger = _event_updated_replay(
            ordered,
            stream,
            config,
            gate_config,
        )
    else:
        decisions, state_ledger = _frozen_replay(
            ordered,
            stream,
            config,
            gate_config,
            protocol,
            replay_sessions=replay_sessions,
        )

    _apply_execution_capacity(decisions, config)
    tick_value = float(config.get("tick_value", 5.0))
    return {
        "lane_id": stream["lane_id"],
        "signal_side": stream["signal_side"],
        "timeframe": int(stream["timeframe"]),
        "lookback": int(stream["lookback"]),
        "basis": stream["basis"],
        "multiplier": float(stream["multiplier"]),
        "n_events": int(len(ordered)),
        "summary": _performance_summary(decisions, tick_value),
        "baselines": {
            mode: _baseline_summary(ordered, mode, tick_value)
            for mode in MODES
        },
        "state_ledger": state_ledger,
        "decisions": decisions,
    }


def _frozen_replay(
    events: Sequence[Mapping[str, Any]],
    stream: Mapping[str, Any],
    config: Mapping[str, Any],
    gate_config: Mapping[str, Any],
    protocol: str,
    *,
    replay_sessions: Optional[Sequence[str]] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    if protocol not in {"daily_frozen", "weekly_frozen"}:
        raise ValueError(
            "replay_protocol must be daily_frozen, weekly_frozen, "
            "or event_updated"
        )

    grouped: Dict[str, List[Mapping[str, Any]]] = defaultdict(list)
    for event in events:
        grouped[_replay_period(event, protocol)].append(event)
    periods = set(grouped)
    periods.update(
        _period_from_session(session_id, protocol)
        for session_id in (replay_sessions or ())
    )

    previous_states: Dict[Tuple[str, str], str] = {}
    decisions: List[Dict[str, Any]] = []
    ledger: List[Dict[str, Any]] = []
    cells = _configured_cells(config)
    for period in sorted(periods):
        period_events = sorted(grouped[period], key=_event_timestamp)
        asof = _period_asof(period, protocol, config)
        frozen_map: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for time_bucket, trend_state in cells:
            key = (time_bucket, trend_state)
            record = _build_state_record(
                events,
                stream,
                asof,
                time_bucket,
                trend_state,
                previous_states.get(key, "OFF"),
                gate_config,
                period,
            )
            previous_states[key] = record["state"]
            frozen_map[key] = record
            ledger.append(record)

        for event in period_events:
            key = (str(event.get("time_bucket")), str(event.get("trend_state")))
            record = frozen_map.get(key)
            if record is None:
                record = _build_state_record(
                    events,
                    stream,
                    asof,
                    key[0],
                    key[1],
                    previous_states.get(key, "OFF"),
                    gate_config,
                    period,
                )
                previous_states[key] = record["state"]
                frozen_map[key] = record
                ledger.append(record)
            decisions.append(_decision_from_state(event, stream, record))
    return decisions, ledger


def _event_updated_replay(
    events: Sequence[Mapping[str, Any]],
    stream: Mapping[str, Any],
    config: Mapping[str, Any],
    gate_config: Mapping[str, Any],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    previous_states: Dict[Tuple[str, str], str] = {}
    decisions: List[Dict[str, Any]] = []
    ledger: List[Dict[str, Any]] = []
    for index, event in enumerate(events):
        time_bucket = str(event.get("time_bucket"))
        trend_state = str(event.get("trend_state"))
        key = (time_bucket, trend_state)
        asof = _event_timestamp(event)
        record = _build_state_record(
            events,
            stream,
            asof,
            time_bucket,
            trend_state,
            previous_states.get(key, "OFF"),
            gate_config,
            f"event:{index}",
        )
        previous_states[key] = record["state"]
        ledger.append(record)
        decisions.append(_decision_from_state(event, stream, record))
    return decisions, ledger


def _build_state_record(
    events: Sequence[Mapping[str, Any]],
    stream: Mapping[str, Any],
    asof: pd.Timestamp,
    time_bucket: str,
    trend_state: str,
    previous_state: str,
    gate_config: Mapping[str, Any],
    period: str,
) -> Dict[str, Any]:
    score = score_context_cell(
        events,
        asof,
        time_bucket,
        trend_state,
        gate_config,
    )
    assessment = select_context_policy(
        score,
        str(stream["signal_side"]),
        gate_config,
    )
    transition = transition_gate_state(previous_state, assessment)
    return {
        "lane_id": stream["lane_id"],
        "signal_side": stream["signal_side"],
        "period": period,
        "score_map_asof": asof,
        "time_bucket": time_bucket,
        "trend_state": trend_state,
        **transition,
        "target_state": assessment["target_state"],
        "selected_mode": assessment.get("selected_mode"),
        "evidence_winner": assessment.get("evidence_winner"),
        "aligned_mode": assessment.get("aligned_mode"),
        "mode_advantage_ticks": assessment.get("mode_advantage_ticks"),
        "score": score,
    }


def _decision_from_state(
    event: Mapping[str, Any],
    stream: Mapping[str, Any],
    state_record: Mapping[str, Any],
) -> Dict[str, Any]:
    return {
        "lane_id": stream["lane_id"],
        "signal_side": stream["signal_side"],
        "timeframe": int(stream["timeframe"]),
        "lookback": int(stream["lookback"]),
        "basis": stream["basis"],
        "multiplier": float(stream["multiplier"]),
        "signal_dt": event.get("signal_dt"),
        "entry_dt": event.get("entry_dt"),
        "trigger_source_dt": event.get("trigger_source_dt"),
        "latched_outside_window": event.get("latched_outside_window"),
        "source_bar_idx": event.get("source_bar_idx"),
        "rolling_avg_ticks": event.get("rolling_avg_ticks"),
        "signal_size_ticks": event.get("signal_size_ticks"),
        "signal_ratio": event.get("signal_ratio"),
        "signal_direction": event.get("signal_direction"),
        "session_id": event.get("session_id"),
        "time_bucket": event.get("time_bucket"),
        "trend_state": event.get("trend_state"),
        "context_dt": event.get("context_dt"),
        "session_vwap": event.get("session_vwap"),
        "close_vs_vwap": event.get("close_vs_vwap"),
        "vwap_slope_15": event.get("vwap_slope_15"),
        "return_60m": event.get("return_60m"),
        "close_vs_vwap_vote": event.get("close_vs_vwap_vote"),
        "vwap_slope_15_vote": event.get("vwap_slope_15_vote"),
        "return_60m_vote": event.get("return_60m_vote"),
        "trend_votes": event.get("trend_votes"),
        "context_history_complete": event.get("context_history_complete"),
        "trigger_type": event.get("trigger_type"),
        "fresh_trigger": event.get("fresh_trigger"),
        "zone_break_trigger": event.get("zone_break_trigger"),
        "zones_broken": event.get("zones_broken"),
        "zone_id": event.get("zone_id"),
        "zone_touch_count": event.get("zone_touch_count"),
        "zone_age_bars": event.get("zone_age_bars"),
        "next_zone_dist_ticks": event.get("next_zone_dist_ticks"),
        "score_map_asof": state_record["score_map_asof"],
        "state": state_record["state"],
        "state_reason_code": state_record["reason_code"],
        "transition": state_record["transition"],
        "selected_mode": state_record.get("selected_mode"),
        "evidence_winner": state_record.get("evidence_winner"),
        "aligned_mode": state_record.get("aligned_mode"),
        "mode_advantage_ticks": state_record.get("mode_advantage_ticks"),
        "capacity_eligible": None,
        "capacity_skipped": False,
        "outcome_available": False,
        "actual_trade_direction": None,
        "actual_entry_price": None,
        "actual_exit_dt": None,
        "actual_exit_known_dt": None,
        "actual_exit_price": None,
        "actual_exit_reason": None,
        "actual_gross_ticks": None,
        "actual_net_ticks": None,
        "continuation": event.get("continuation"),
        "reversion": event.get("reversion"),
        "_event": event,
    }


def _apply_execution_capacity(
    decisions: Sequence[Dict[str, Any]],
    config: Mapping[str, Any],
) -> None:
    maximum = max(
        1,
        int(
            (config.get("outcome") or {}).get(
                "max_concurrent_per_direction",
                3,
            )
        ),
    )
    active: Dict[int, List[pd.Timestamp]] = {1: [], -1: []}
    for decision in sorted(decisions, key=lambda row: _aware_timestamp(
        row["entry_dt"], label="entry_dt"
    )):
        event = decision.pop("_event")
        if decision["state"] != "ON" or not decision.get("selected_mode"):
            continue
        outcome = event.get(decision["selected_mode"]) or {}
        if not outcome.get("available"):
            continue
        direction = int(outcome["trade_direction"])
        entry_dt = _aware_timestamp(outcome["entry_dt"], label="entry_dt")
        active[direction] = [
            exit_dt for exit_dt in active[direction]
            if exit_dt > entry_dt
        ]
        decision["outcome_available"] = True
        decision["actual_trade_direction"] = direction
        decision["actual_entry_price"] = outcome.get("entry_price")
        decision["actual_exit_dt"] = outcome.get("exit_dt")
        decision["actual_exit_known_dt"] = outcome["exit_known_dt"]
        decision["actual_exit_price"] = outcome.get("exit_price")
        decision["actual_exit_reason"] = outcome.get("exit_reason")
        decision["actual_gross_ticks"] = outcome.get("gross_pnl_ticks")
        if len(active[direction]) >= maximum:
            decision["capacity_eligible"] = False
            decision["capacity_skipped"] = True
            continue
        decision["capacity_eligible"] = True
        decision["actual_net_ticks"] = outcome.get("net_pnl_ticks")
        active[direction].append(
            _aware_timestamp(
                outcome["exit_known_dt"],
                label="exit_known_dt",
            )
        )


def _score_mode_hierarchy(
    local_rows: Sequence[Mapping[str, Any]],
    parent_rows: Sequence[Mapping[str, Any]],
    stream_rows: Sequence[Mapping[str, Any]],
    mode: str,
    asof: pd.Timestamp,
    config: Mapping[str, Any],
    *,
    half_life_days: float,
) -> Dict[str, Dict[str, Any]]:
    stream = _raw_level_stats(
        stream_rows,
        mode,
        asof,
        half_life_days,
    )
    stream["posterior_mean_ticks"] = stream["weighted_mean_ticks"]
    stream["lower_bound_ticks"] = _lower_bound(
        stream,
        stream["posterior_mean_ticks"],
        config,
    )

    parent = _raw_level_stats(
        parent_rows,
        mode,
        asof,
        half_life_days,
    )
    parent["posterior_mean_ticks"] = _shrink(
        parent,
        stream["posterior_mean_ticks"],
        config,
    )
    parent["lower_bound_ticks"] = _lower_bound(
        parent,
        parent["posterior_mean_ticks"],
        config,
    )

    local = _raw_level_stats(
        local_rows,
        mode,
        asof,
        half_life_days,
    )
    local["posterior_mean_ticks"] = _shrink(
        local,
        parent["posterior_mean_ticks"],
        config,
    )
    local["lower_bound_ticks"] = _lower_bound(
        local,
        local["posterior_mean_ticks"],
        config,
    )
    return {
        "local": _round_stats(local),
        "parent": _round_stats(parent),
        "stream": _round_stats(stream),
    }


def _raw_level_stats(
    rows: Sequence[Mapping[str, Any]],
    mode: str,
    asof: pd.Timestamp,
    half_life_days: float,
) -> Dict[str, Any]:
    values: List[float] = []
    weights: List[float] = []
    sessions = set()
    known_times: List[pd.Timestamp] = []
    half_life = max(0.1, float(half_life_days))
    for row in rows:
        outcome = row.get(mode) or {}
        value = outcome.get("net_pnl_ticks")
        if value is None or not isfinite(float(value)):
            continue
        known = _aware_timestamp(
            outcome["exit_known_dt"],
            label=f"{mode}.exit_known_dt",
        )
        age_days = max(
            0.0,
            (asof - known).total_seconds() / 86400.0,
        )
        values.append(float(value))
        weights.append(exp(-log(2.0) * age_days / half_life))
        sessions.add(_event_session_id(row))
        known_times.append(known)

    vals = np.asarray(values, dtype=float)
    wts = np.asarray(weights, dtype=float)
    weight_sum = float(wts.sum()) if len(wts) else 0.0
    weighted_mean = (
        float(np.average(vals, weights=wts))
        if len(vals) and weight_sum > 0.0
        else 0.0
    )
    effective_n = (
        float(weight_sum ** 2 / np.square(wts).sum())
        if len(wts) and float(np.square(wts).sum()) > 0.0
        else 0.0
    )
    variance = (
        float(np.average(np.square(vals - weighted_mean), weights=wts))
        if len(vals) >= 2 and weight_sum > 0.0
        else 0.0
    )
    win_rate = (
        100.0 * float(wts[vals > 0.0].sum()) / weight_sum
        if len(vals) and weight_sum > 0.0
        else 0.0
    )
    return {
        "n": int(len(vals)),
        "effective_n": effective_n,
        "unique_sessions": int(len(sessions)),
        "weight_sum": weight_sum,
        "weighted_mean_ticks": weighted_mean,
        "weighted_variance": variance,
        "weighted_win_rate_pct": win_rate,
        "oldest_eligible_outcome_dt": min(known_times) if known_times else None,
        "newest_eligible_outcome_dt": max(known_times) if known_times else None,
    }


def _shrink(
    stats: Mapping[str, Any],
    prior_mean: float,
    config: Mapping[str, Any],
) -> float:
    weight = float(stats["weight_sum"])
    strength = max(0.0, float(config.get("prior_strength", 5.0)))
    if weight + strength <= 0.0:
        return float(prior_mean)
    return (
        weight * float(stats["weighted_mean_ticks"])
        + strength * float(prior_mean)
    ) / (weight + strength)


def _lower_bound(
    stats: Mapping[str, Any],
    posterior: float,
    config: Mapping[str, Any],
) -> float:
    effective_n = max(1.0, float(stats["effective_n"]))
    standard_error = sqrt(
        max(0.0, float(stats["weighted_variance"])) / effective_n
    )
    return float(posterior) - max(
        0.0,
        float(config.get("confidence_z", 0.5)),
    ) * standard_error


def _round_stats(stats: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "n": int(stats["n"]),
        "effective_n": round(float(stats["effective_n"]), 3),
        "unique_sessions": int(stats["unique_sessions"]),
        "weighted_mean_ticks": round(
            float(stats["weighted_mean_ticks"]),
            3,
        ),
        "posterior_mean_ticks": round(
            float(stats["posterior_mean_ticks"]),
            3,
        ),
        "lower_bound_ticks": round(
            float(stats["lower_bound_ticks"]),
            3,
        ),
        "weighted_win_rate_pct": round(
            float(stats["weighted_win_rate_pct"]),
            1,
        ),
        "oldest_eligible_outcome_dt": stats[
            "oldest_eligible_outcome_dt"
        ],
        "newest_eligible_outcome_dt": stats[
            "newest_eligible_outcome_dt"
        ],
    }


def _performance_summary(
    decisions: Sequence[Mapping[str, Any]],
    tick_value: float,
) -> Dict[str, Any]:
    traded = [
        row for row in decisions
        if row.get("actual_net_ticks") is not None
    ]
    pnls = [float(row["actual_net_ticks"]) for row in traded]
    metrics = _pnl_metrics(pnls, tick_value)
    unique_days = len(
        {str(row.get("session_id")) for row in traded}
    )
    by_mode = _attribution(traded, "selected_mode", tick_value)
    by_direction = _attribution(
        traded,
        "actual_trade_direction",
        tick_value,
    )
    by_bucket = _attribution(traded, "time_bucket", tick_value)
    by_trend = _attribution(traded, "trend_state", tick_value)
    by_trigger = _attribution(traded, "trigger_type", tick_value)
    day_pnl: Dict[str, float] = defaultdict(float)
    for row in traded:
        day_pnl[str(row.get("session_id"))] += float(row["actual_net_ticks"])
    return {
        **metrics,
        "signals_seen": int(len(decisions)),
        "coverage_pct": round(
            100.0 * len(traded) / len(decisions),
            1,
        ) if decisions else 0.0,
        "unique_days": int(unique_days),
        "trades_per_day": round(len(traded) / unique_days, 3)
        if unique_days else 0.0,
        "capacity_skips": int(
            sum(bool(row.get("capacity_skipped")) for row in decisions)
        ),
        "false_activation_days": int(
            sum(value < 0.0 for value in day_pnl.values())
        ),
        "state_counts": dict(Counter(
            str(row.get("state")) for row in decisions
        )),
        "reason_counts": dict(Counter(
            str(row.get("state_reason_code")) for row in decisions
        )),
        "transition_counts": dict(Counter(
            str(row.get("transition")) for row in decisions
        )),
        "by_mode": by_mode,
        "by_direction": by_direction,
        "by_time_bucket": by_bucket,
        "by_trend_state": by_trend,
        "by_trigger_type": by_trigger,
    }


def _baseline_summary(
    events: Sequence[Mapping[str, Any]],
    mode: str,
    tick_value: float,
) -> Dict[str, Any]:
    pnls = [
        float(event[mode]["net_pnl_ticks"])
        for event in events
        if (event.get(mode) or {}).get("available")
        and (event.get(mode) or {}).get("capacity_eligible", True)
        and (event.get(mode) or {}).get("net_pnl_ticks") is not None
    ]
    return _pnl_metrics(pnls, tick_value)


def _pnl_metrics(pnls: Sequence[float], tick_value: float) -> Dict[str, Any]:
    if not pnls:
        return {
            "n_trades": 0,
            "win_rate_pct": None,
            "profit_factor": None,
            "avg_trade_ticks": None,
            "total_net_ticks": 0.0,
            "total_net_dollars": 0.0,
            "max_drawdown_ticks": 0.0,
            "longest_losing_streak": 0,
        }
    wins = [value for value in pnls if value > 0.0]
    losses = [value for value in pnls if value < 0.0]
    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0
    losing_streak = 0
    longest_losing_streak = 0
    for value in pnls:
        equity += value
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
        losing_streak = losing_streak + 1 if value < 0.0 else 0
        longest_losing_streak = max(
            longest_losing_streak,
            losing_streak,
        )
    gross_loss = abs(sum(losses))
    return {
        "n_trades": int(len(pnls)),
        "win_rate_pct": round(100.0 * len(wins) / len(pnls), 1),
        "profit_factor": round(sum(wins) / gross_loss, 3)
        if gross_loss > 0.0 else None,
        "avg_trade_ticks": round(sum(pnls) / len(pnls), 3),
        "total_net_ticks": round(sum(pnls), 3),
        "total_net_dollars": round(sum(pnls) * tick_value, 2),
        "max_drawdown_ticks": round(max_drawdown, 3),
        "longest_losing_streak": int(longest_losing_streak),
    }


def _attribution(
    rows: Sequence[Mapping[str, Any]],
    field: str,
    tick_value: float,
) -> Dict[str, Dict[str, Any]]:
    grouped: Dict[str, List[float]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get(field))].append(float(row["actual_net_ticks"]))
    return {
        key: _pnl_metrics(values, tick_value)
        for key, values in sorted(grouped.items())
    }


def _assessment(
    target_state: str,
    reason_code: str,
    **values: Any,
) -> Dict[str, Any]:
    return {
        "target_state": target_state,
        "reason_code": reason_code,
        "selected_mode": values.pop("selected_mode", None),
        "evidence_winner": values.pop("evidence_winner", None),
        "aligned_mode": values.pop("aligned_mode", None),
        "mode_advantage_ticks": values.pop("mode_advantage_ticks", None),
        "fast_score_decay": values.pop("fast_score_decay", False),
        **values,
    }


def _resolve_gate_config(config: Mapping[str, Any]) -> Dict[str, Any]:
    supplied = config.get("context_gate") or {}
    profile = str(supplied.get("profile", "balanced")).strip().lower()
    if profile not in CONTEXT_GATE_PROFILES:
        raise ValueError(
            f"unknown context-gate profile {profile!r}; expected "
            f"{sorted(CONTEXT_GATE_PROFILES)}"
        )
    resolved = {
        **DEFAULT_CONTEXT_GATE_CONFIG,
        **CONTEXT_GATE_PROFILES[profile],
        **supplied,
        "profile": profile,
    }
    protocol = str(resolved["replay_protocol"]).strip().lower()
    if protocol not in {
        "daily_frozen",
        "weekly_frozen",
        "event_updated",
    }:
        raise ValueError(
            "replay_protocol must be daily_frozen, weekly_frozen, "
            "or event_updated"
        )
    policy = str(resolved["policy"]).strip().lower()
    if policy not in {"aligned_only", "evidence_only"}:
        raise ValueError("policy must be 'aligned_only' or 'evidence_only'")
    resolved["replay_protocol"] = protocol
    resolved["policy"] = policy
    resolved["training_days"] = max(1, int(resolved["training_days"]))
    resolved["min_local_signals"] = max(
        1,
        int(resolved["min_local_signals"]),
    )
    resolved["min_unique_sessions"] = max(
        1,
        int(resolved["min_unique_sessions"]),
    )
    resolved["half_life_days"] = max(
        0.1,
        float(resolved["half_life_days"]),
    )
    if resolved.get("max_stale_days") is None:
        resolved["max_stale_days"] = resolved["half_life_days"]
    resolved["max_stale_days"] = max(
        0.0,
        float(resolved["max_stale_days"]),
    )
    return resolved


def _configured_cells(
    config: Mapping[str, Any],
) -> List[Tuple[str, str]]:
    width = _bucket_width(config)
    time_filter = config.get("time_filter") or {}
    if time_filter.get("enabled", True):
        start = _clock_minutes(time_filter.get("start", "00:00"))
        end = _clock_minutes(time_filter.get("end", "23:59"))
        if end < start:
            minutes = list(range((start // width) * width, 24 * 60, width))
            minutes += list(range(0, (end // width) * width + 1, width))
        else:
            minutes = list(
                range(
                    (start // width) * width,
                    (end // width) * width + 1,
                    width,
                )
            )
    else:
        minutes = list(range(0, 24 * 60, width))
    return [
        (_bucket_label(minute, width), trend)
        for minute in minutes
        for trend in TREND_STATES
    ]


def _replay_period(event: Mapping[str, Any], protocol: str) -> str:
    return _period_from_session(_event_session_id(event), protocol)


def _period_from_session(session_id: str, protocol: str) -> str:
    session = date.fromisoformat(str(session_id))
    if protocol == "daily_frozen":
        return session.isoformat()
    monday = session - pd.Timedelta(days=session.weekday())
    return pd.Timestamp(monday).date().isoformat()


def _period_asof(
    period: str,
    protocol: str,
    config: Mapping[str, Any],
) -> pd.Timestamp:
    del protocol
    context = config.get("context") or {}
    anchor = str(context.get("session_anchor", "07:30"))
    timezone = str(
        context.get(
            "timezone",
            (config.get("time_filter") or {}).get(
                "timezone",
                "America/Denver",
            ),
        )
    )
    return pd.Timestamp(f"{period} {anchor}", tz=timezone)


def _event_timestamp(event: Mapping[str, Any]) -> pd.Timestamp:
    value = event.get("signal_dt", event.get("entry_dt"))
    return _aware_timestamp(value, label="event timestamp")


def _event_session_id(event: Mapping[str, Any]) -> str:
    value = event.get("session_id")
    if value:
        return str(value)
    return _event_timestamp(event).date().isoformat()


def _aware_timestamp(value: Any, *, label: str) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        raise ValueError(f"{label} must be timezone-aware")
    return timestamp


def _bucket_width(config: Mapping[str, Any]) -> int:
    context = config.get("context") or {}
    adaptive = config.get("adaptive") or {}
    value = context.get(
        "time_bucket_minutes",
        adaptive.get("time_bin_minutes", 30),
    )
    return max(1, int(value))


def _clock_minutes(value: Any) -> int:
    parsed = time.fromisoformat(str(value))
    return parsed.hour * 60 + parsed.minute


def _bucket_label(start_minute: int, width: int) -> str:
    return (
        f"{_minute_label(start_minute)}-"
        f"{_minute_label(start_minute + width)}"
    )


def _minute_label(minute: int) -> str:
    minute %= 24 * 60
    return f"{minute // 60:02d}:{minute % 60:02d}"


def _context_config_payload(config: Mapping[str, Any]) -> Dict[str, Any]:
    context = config.get("context") or {}
    return {
        "timezone": context.get(
            "timezone",
            (config.get("time_filter") or {}).get(
                "timezone",
                "America/Denver",
            ),
        ),
        "session_anchor": context.get("session_anchor", "07:30"),
        "time_bucket_minutes": _bucket_width(config),
        "vwap_price": context.get("vwap_price", "typical"),
        "vwap_slope_bars": context.get("vwap_slope_bars", 15),
        "return_lookback_minutes": context.get(
            "return_lookback_minutes",
            60,
        ),
    }


def _deep_merge(
    base: Mapping[str, Any],
    override: Mapping[str, Any],
) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, value in base.items():
        out[key] = _deep_merge(value, {}) if isinstance(value, dict) else value
    for key, value in override.items():
        if (
            key in out
            and isinstance(out[key], dict)
            and isinstance(value, Mapping)
        ):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _iso(value: Any) -> Optional[str]:
    if value is None or pd.isna(value):
        return None
    return pd.Timestamp(value).isoformat()


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _json_safe(item)
            for key, item in value.items()
            if str(key) != "_event"
        }
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (pd.Timestamp, np.datetime64)):
        return _iso(value)
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, float) and not isfinite(value):
        return None
    return value
