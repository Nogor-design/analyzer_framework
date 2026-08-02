from __future__ import annotations

"""Dynamic Phase 4 cross-sequence evaluation helpers.

These helpers summarize independently reset Phase 3 selector runs against
their hash-bound Phase 2 labels. Oracle rows are evaluation-only.
"""

from ast import literal_eval
from collections import defaultdict
import hashlib
import json
from math import isfinite
from pathlib import Path
from statistics import median
from typing import Any, Dict, List, Mapping, Sequence, Tuple

import pandas as pd

from ta_foundation.core.manifest import sha256_file


DYNAMIC_MULTISEQUENCE_REPLAY_SCHEMA_VERSION = "dynamic_multisequence_replay.v1"
REQUIRED_PROFILES = ("Fast", "Balanced", "Slow")
REQUIRED_PROTOCOLS = ("daily_frozen", "event_updated")
PRIMARY_PROFILE = "Balanced"
PRIMARY_PROTOCOL = "daily_frozen"

SUMMARY_COLUMNS = (
    "schema_version",
    "sequence",
    "profile",
    "protocol",
    "primary_row",
    "sessions",
    "session_start",
    "session_end",
    "trades",
    "net_ticks",
    "profit_factor",
    "maximum_drawdown_ticks",
    "activation_days",
    "profitable_activation_days",
    "activation_day_precision_pct",
    "largest_profitable_session_share_pct",
    "policy_switches",
    "parameter_switches",
    "median_policy_dwell_boundaries",
    "window_decision_boundaries",
    "window_signature_switches",
    "distinct_selected_experts",
    "oracle_active_regimes",
    "detected_oracle_active_regimes",
    "active_regime_detection_rate_pct",
    "median_activation_delay_sessions",
    "exact_expert_tracked_regimes",
    "exact_expert_tracking_rate_pct",
    "median_exact_expert_delay_sessions",
    "oracle_off_regimes",
    "deactivated_oracle_off_regimes",
    "oracle_off_deactivation_rate_pct",
    "median_deactivation_delay_sessions",
    "oracle_active_cell_trades",
    "oracle_active_cell_net_ticks",
    "oracle_exact_expert_trades",
    "oracle_exact_expert_net_ticks",
    "opportunity_missed_while_inactive_ticks",
    "selector_penalized_objective_ticks",
    "best_static_per_cell_raw_net_ticks",
    "bounded_oracle_penalized_objective_ticks",
    "static_selector_regret_ticks",
    "dynamic_regret_ticks",
    "dynamic_regret_improvement_vs_static_ticks",
    "dynamic_regret_below_static",
    "forward_authorized",
)

TIMELINE_COLUMNS = (
    "schema_version",
    "sequence",
    "profile",
    "protocol",
    "decision_index",
    "decision_asof",
    "session_id",
    "window_id",
    "signal_side",
    "trend_state",
    "start_time",
    "end_time",
    "time_buckets",
    "expert_ids",
    "expert_lane_id",
    "timeframe",
    "lookback",
    "basis",
    "multiplier",
    "mode",
    "trade_direction",
)


class DynamicMultisequenceError(ValueError):
    """Raised when Phase 4 evaluation contracts are not satisfied."""


def summarize_dynamic_sequence(
    *,
    selector_summaries: Sequence[Mapping[str, Any]],
    selector_state_rows: Sequence[Mapping[str, Any]],
    selector_execution_rows: Sequence[Mapping[str, Any]],
    selector_window_rows: Sequence[Mapping[str, Any]],
    oracle_summary: Mapping[str, Any],
    oracle_session_rows: Sequence[Mapping[str, Any]],
    oracle_regime_rows: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Build per-profile/protocol Phase 4 metrics for exactly one sequence."""
    summaries = [dict(row) for row in selector_summaries]
    if not summaries:
        raise DynamicMultisequenceError("selector summaries are required")
    sequences = {str(row.get("sequence") or "") for row in summaries}
    if len(sequences) != 1 or "" in sequences:
        raise DynamicMultisequenceError(
            "Phase 4 sequence evaluation requires exactly one sequence"
        )
    sequence = next(iter(sequences))
    _validate_matrix(summaries)

    state_rows = _records(selector_state_rows)
    execution_rows = _records(selector_execution_rows)
    window_rows = _records(selector_window_rows)
    oracle_sessions = _records(oracle_session_rows)
    oracle_regimes = _records(oracle_regime_rows)
    for name, rows in (
        ("selector state", state_rows),
        ("selector execution", execution_rows),
        ("selector window", window_rows),
        ("oracle session", oracle_sessions),
        ("oracle regime", oracle_regimes),
    ):
        _require_sequence(rows, sequence=sequence, label=name)
    _validate_independent_reset(state_rows)

    session_indexes = {
        str(row["session_id"]): int(row["session_index"])
        for row in oracle_sessions
    }
    oracle_by_session_cell = {
        (str(row["session_id"]), str(row["context_cell"])): row
        for row in oracle_sessions
    }
    output: List[Dict[str, Any]] = []
    for summary in sorted(
        summaries,
        key=lambda row: (
            REQUIRED_PROFILES.index(str(row["profile"])),
            REQUIRED_PROTOCOLS.index(str(row["protocol"])),
        ),
    ):
        profile = str(summary["profile"])
        protocol = str(summary["protocol"])
        states = [
            row
            for row in state_rows
            if str(row["profile"]) == profile
            and str(row["protocol"]) == protocol
        ]
        executions = [
            row
            for row in execution_rows
            if str(row["profile"]) == profile
            and str(row["protocol"]) == protocol
        ]
        windows = [
            row
            for row in window_rows
            if str(row["profile"]) == profile
            and str(row["protocol"]) == protocol
        ]
        output.append(
            _summarize_matrix_row(
                summary,
                states=states,
                executions=executions,
                windows=windows,
                oracle_regimes=oracle_regimes,
                oracle_summary=oracle_summary,
                oracle_by_session_cell=oracle_by_session_cell,
                session_indexes=session_indexes,
            )
        )

    timeline = [
        {column: row.get(column) for column in TIMELINE_COLUMNS}
        for row in window_rows
        if str(row["profile"]) == PRIMARY_PROFILE
        and str(row["protocol"]) == PRIMARY_PROTOCOL
    ]
    timeline.sort(
        key=lambda row: (
            int(row["decision_index"]),
            str(row["start_time"]),
            str(row["window_id"]),
        )
    )
    return {
        "schema_version": DYNAMIC_MULTISEQUENCE_REPLAY_SCHEMA_VERSION,
        "research_phase": "dynamic_phase_4",
        "sequence": sequence,
        "summary_rows": _json_safe(output),
        "timeline_rows": _json_safe(timeline),
        "evaluation_contract": evaluation_contract(),
    }


def build_multisequence_summary(
    sequence_results: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Combine independent summaries without pooling selector state."""
    if not sequence_results:
        raise DynamicMultisequenceError("sequence results are required")
    sequences = [str(result.get("sequence") or "") for result in sequence_results]
    if any(not value for value in sequences) or len(set(sequences)) != len(sequences):
        raise DynamicMultisequenceError(
            "sequence results must have unique non-empty sequence names"
        )
    rows = [
        dict(row)
        for result in sequence_results
        for row in result.get("summary_rows", [])
    ]
    expected = len(sequences) * len(REQUIRED_PROFILES) * len(REQUIRED_PROTOCOLS)
    if len(rows) != expected:
        raise DynamicMultisequenceError(
            "cross-sequence summary does not contain the full selector matrix"
        )
    primary = [row for row in rows if bool(row["primary_row"])]
    positive = [row for row in primary if float(row["net_ticks"]) > 0.0]
    concentration = [
        float(row["largest_profitable_session_share_pct"])
        for row in primary
        if row["largest_profitable_session_share_pct"] is not None
    ]
    aggregate = {
        "sequences": len(sequences),
        "matrix_rows": len(rows),
        "primary_rows": len(primary),
        "positive_primary_sequences": len(positive),
        "positive_primary_sequence_pct": (
            100.0 * len(positive) / len(primary) if primary else None
        ),
        "primary_net_ticks_sum_diagnostic_only": sum(
            float(row["net_ticks"]) for row in primary
        ),
        "primary_trades_sum_diagnostic_only": sum(
            int(row["trades"]) for row in primary
        ),
        "worst_primary_largest_profitable_session_share_pct": (
            max(concentration) if concentration else None
        ),
        "state_pooled_across_sequences": False,
        "pnl_sum_is_not_a_shared_selector_backtest": True,
        "primary_dynamic_regret_below_static_sequences": sum(
            bool(row["dynamic_regret_below_static"]) for row in primary
        ),
    }
    profile_protocol = []
    for protocol in REQUIRED_PROTOCOLS:
        for profile in REQUIRED_PROFILES:
            selected = [
                row
                for row in rows
                if row["protocol"] == protocol and row["profile"] == profile
            ]
            gross_profit = gross_loss = 0.0
            for row in selected:
                net = float(row["net_ticks"])
                profit_factor = row["profit_factor"]
                if profit_factor in (None, "Infinity"):
                    if net > 0.0:
                        gross_profit += net
                    continue
                ratio = float(profit_factor)
                if abs(ratio - 1.0) <= 1e-12:
                    continue
                loss = net / (ratio - 1.0)
                gross_loss += loss
                gross_profit += ratio * loss
            profile_protocol.append(
                {
                    "profile": profile,
                    "protocol": protocol,
                    "positive_sequences": sum(
                        float(row["net_ticks"]) > 0.0 for row in selected
                    ),
                    "sequences": len(selected),
                    "trades": sum(int(row["trades"]) for row in selected),
                    "net_ticks_sum_diagnostic_only": sum(
                        float(row["net_ticks"]) for row in selected
                    ),
                    "pooled_profit_factor_diagnostic_only": (
                        gross_profit / gross_loss if gross_loss > 0.0 else None
                    ),
                }
            )
    return _json_safe(
        {
            "schema_version": DYNAMIC_MULTISEQUENCE_REPLAY_SCHEMA_VERSION,
            "research_phase": "dynamic_phase_4",
            "research_classification": "causal_cross_sequence_development_replay",
            "sequences": sequences,
            "evaluation_contract": evaluation_contract(),
            "aggregate_diagnostics": aggregate,
            "profile_protocol_diagnostics": profile_protocol,
            "forward_authorized": False,
        }
    )


def write_dynamic_multisequence_replay(
    output_dir: Path,
    *,
    summary_rows: Sequence[Mapping[str, Any]],
    timeline_rows: Sequence[Mapping[str, Any]],
    cross_sequence_summary: Mapping[str, Any],
    manifest: Mapping[str, Any],
    overwrite: bool = False,
) -> Dict[str, Path]:
    """Write the compact, hash-bound Phase 4 evaluation bundle."""
    root = Path(output_dir).resolve()
    paths = {
        "summary": root / "phase4_summary.csv",
        "timeline": root / "phase4_primary_window_timeline.csv",
        "cross_sequence_summary": root / "phase4_cross_sequence_summary.json",
        "manifest": root / "phase4_manifest.json",
    }
    existing = [path for path in paths.values() if path.exists()]
    if existing and not overwrite:
        raise FileExistsError(
            "refusing to overwrite Dynamic Phase 4 artifacts: "
            + ", ".join(str(path) for path in existing)
        )
    root.mkdir(parents=True, exist_ok=True)
    _write_csv(paths["summary"], summary_rows, SUMMARY_COLUMNS)
    _write_csv(paths["timeline"], timeline_rows, TIMELINE_COLUMNS)
    _write_json(paths["cross_sequence_summary"], cross_sequence_summary)

    payload = dict(manifest)
    research_phase = str(payload.get("research_phase") or "dynamic_phase_4")
    research_classification = str(
        payload.get("research_classification")
        or "causal_cross_sequence_development_replay"
    )
    payload.update(
        {
            "schema_version": DYNAMIC_MULTISEQUENCE_REPLAY_SCHEMA_VERSION,
            "research_phase": research_phase,
            "research_classification": research_classification,
            "evaluation_contract": evaluation_contract(),
            "forward_authorized": False,
            "artifacts": {
                name: {
                    "filename": path.name,
                    "bytes": path.stat().st_size,
                    "sha256": sha256_file(path),
                }
                for name, path in paths.items()
                if name != "manifest"
            },
        }
    )
    payload["manifest_sha256"] = _sha256_json(payload)
    _write_json(paths["manifest"], payload)
    return paths


def evaluation_contract() -> Dict[str, Any]:
    return {
        "required_profiles": list(REQUIRED_PROFILES),
        "required_protocols": list(REQUIRED_PROTOCOLS),
        "primary_profile": PRIMARY_PROFILE,
        "primary_protocol": PRIMARY_PROTOCOL,
        "selector_state_reset_per_sequence": True,
        "oracle_use": "post_replay_evaluation_only",
        "activation_delay": (
            "observed sessions from oracle EXPERT-regime start to first "
            "selector ON boundary in the same context cell"
        ),
        "exact_expert_delay": (
            "observed sessions from oracle EXPERT-regime start to first "
            "selector ON boundary selecting the oracle expert"
        ),
        "deactivation_delay": (
            "observed sessions from oracle OFF-regime start to first boundary "
            "without selector ON in the same context cell"
        ),
        "event_updated_session_collapse": (
            "a cell is active if any intraday boundary is ON"
        ),
        "decaying_is_executable": False,
        "cross_sequence_state_pooling": False,
    }


def _summarize_matrix_row(
    summary: Mapping[str, Any],
    *,
    states: Sequence[Mapping[str, Any]],
    executions: Sequence[Mapping[str, Any]],
    windows: Sequence[Mapping[str, Any]],
    oracle_regimes: Sequence[Mapping[str, Any]],
    oracle_summary: Mapping[str, Any],
    oracle_by_session_cell: Mapping[Tuple[str, str], Mapping[str, Any]],
    session_indexes: Mapping[str, int],
) -> Dict[str, Any]:
    activity: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for row in states:
        key = (str(row["session_id"]), str(row["context_cell"]))
        record = activity.setdefault(key, {"active": False, "experts": set()})
        if str(row["state"]) == "ON":
            record["active"] = True
            if row.get("selected_expert_id"):
                record["experts"].add(str(row["selected_expert_id"]))

    active_regimes = [
        row for row in oracle_regimes if str(row["oracle_state"]) == "EXPERT"
    ]
    off_regimes = [
        row for row in oracle_regimes if str(row["oracle_state"]) == "OFF"
    ]
    activation_delays: List[int] = []
    expert_delays: List[int] = []
    deactivation_delays: List[int] = []
    for regime in active_regimes:
        cell = str(regime["context_cell"])
        start = int(regime["start_session_index"])
        end = int(regime["end_session_index"])
        active_hits: List[int] = []
        exact_hits: List[int] = []
        oracle_expert = str(regime["selected_expert_id"])
        for (session, candidate_cell), record in activity.items():
            index = session_indexes.get(session)
            if candidate_cell != cell or index is None or not start <= index <= end:
                continue
            if bool(record["active"]):
                active_hits.append(index)
            if oracle_expert in record["experts"]:
                exact_hits.append(index)
        if active_hits:
            activation_delays.append(min(active_hits) - start)
        if exact_hits:
            expert_delays.append(min(exact_hits) - start)
    for regime in off_regimes:
        cell = str(regime["context_cell"])
        start = int(regime["start_session_index"])
        end = int(regime["end_session_index"])
        inactive_hits: List[int] = []
        for session, index in session_indexes.items():
            if start <= index <= end:
                record = activity.get((session, cell))
                if record is None or not bool(record["active"]):
                    inactive_hits.append(index)
        if inactive_hits:
            deactivation_delays.append(min(inactive_hits) - start)

    executed = [row for row in executions if _truth_value(row.get("executed"))]
    by_session: Dict[str, float] = defaultdict(float)
    oracle_active_cell_pnls: List[float] = []
    oracle_exact_expert_pnls: List[float] = []
    for row in executed:
        session = str(row["session_id"])
        pnl = float(row["net_pnl_ticks"])
        by_session[session] += pnl
        cells = _list_value(row.get("candidate_context_cells"))
        labels = [
            oracle_by_session_cell.get((session, str(cell))) for cell in cells
        ]
        active_labels = [
            label
            for label in labels
            if label is not None and str(label["oracle_state"]) == "EXPERT"
        ]
        if active_labels:
            oracle_active_cell_pnls.append(pnl)
        selected = str(row.get("selected_expert_id") or "")
        if selected and any(
            str(label.get("selected_expert_id") or "") == selected
            for label in active_labels
        ):
            oracle_exact_expert_pnls.append(pnl)
    profitable_sessions = [
        value for value in by_session.values() if value > 0.0
    ]
    total_profitable = sum(profitable_sessions)
    largest_share = (
        100.0 * max(profitable_sessions) / total_profitable
        if total_profitable > 0.0
        else None
    )

    policy_switches, parameter_switches, dwell = _policy_turnover(states)
    window_boundaries, window_switches = _window_turnover(
        windows,
        decision_indexes={int(row["decision_index"]) for row in states},
    )
    execution = dict(summary["execution"])
    regret = dict(summary["dynamic_regret"])
    state_summary = dict(summary["states"])
    ceiling = dict(oracle_summary["detectability_ceiling"])
    static_objective = float(ceiling["best_static_per_cell_raw_net_ticks"])
    oracle_objective = float(
        ceiling["bounded_oracle_penalized_objective_ticks"]
    )
    selector_objective = regret["selector_penalized_objective_ticks"]
    static_selector_regret = (
        static_objective - float(selector_objective)
        if selector_objective is not None
        else None
    )
    static_benchmark_dynamic_regret = oracle_objective - static_objective
    dynamic_regret = regret["dynamic_regret_ticks"]
    regret_improvement = (
        static_benchmark_dynamic_regret - float(dynamic_regret)
        if dynamic_regret is not None
        else None
    )
    return _json_safe(
        {
            "schema_version": DYNAMIC_MULTISEQUENCE_REPLAY_SCHEMA_VERSION,
            "sequence": str(summary["sequence"]),
            "profile": str(summary["profile"]),
            "protocol": str(summary["protocol"]),
            "primary_row": bool(summary["primary_row"]),
            "sessions": int(summary["sessions"]),
            "session_start": str(summary["session_start"]),
            "session_end": str(summary["session_end"]),
            "trades": int(execution["trades"]),
            "net_ticks": float(execution["net_ticks"]),
            "profit_factor": execution["profit_factor"],
            "maximum_drawdown_ticks": float(
                execution["maximum_drawdown_ticks"]
            ),
            "activation_days": int(execution["activation_days"]),
            "profitable_activation_days": int(
                execution["profitable_activation_days"]
            ),
            "activation_day_precision_pct": execution[
                "activation_day_precision_pct"
            ],
            "largest_profitable_session_share_pct": largest_share,
            "policy_switches": policy_switches,
            "parameter_switches": parameter_switches,
            "median_policy_dwell_boundaries": (
                median(dwell) if dwell else None
            ),
            "window_decision_boundaries": window_boundaries,
            "window_signature_switches": window_switches,
            "distinct_selected_experts": int(
                state_summary["distinct_selected_experts"]
            ),
            "oracle_active_regimes": len(active_regimes),
            "detected_oracle_active_regimes": len(activation_delays),
            "active_regime_detection_rate_pct": _rate(
                len(activation_delays), len(active_regimes)
            ),
            "median_activation_delay_sessions": (
                median(activation_delays) if activation_delays else None
            ),
            "exact_expert_tracked_regimes": len(expert_delays),
            "exact_expert_tracking_rate_pct": _rate(
                len(expert_delays), len(active_regimes)
            ),
            "median_exact_expert_delay_sessions": (
                median(expert_delays) if expert_delays else None
            ),
            "oracle_off_regimes": len(off_regimes),
            "deactivated_oracle_off_regimes": len(deactivation_delays),
            "oracle_off_deactivation_rate_pct": _rate(
                len(deactivation_delays), len(off_regimes)
            ),
            "median_deactivation_delay_sessions": (
                median(deactivation_delays) if deactivation_delays else None
            ),
            "oracle_active_cell_trades": len(oracle_active_cell_pnls),
            "oracle_active_cell_net_ticks": sum(oracle_active_cell_pnls),
            "oracle_exact_expert_trades": len(oracle_exact_expert_pnls),
            "oracle_exact_expert_net_ticks": sum(oracle_exact_expert_pnls),
            "opportunity_missed_while_inactive_ticks": float(
                execution[
                    "positive_paper_opportunity_missed_while_inactive_ticks"
                ]
            ),
            "selector_penalized_objective_ticks": regret[
                "selector_penalized_objective_ticks"
            ],
            "best_static_per_cell_raw_net_ticks": static_objective,
            "bounded_oracle_penalized_objective_ticks": regret[
                "bounded_oracle_penalized_objective_ticks"
            ],
            "static_selector_regret_ticks": static_selector_regret,
            "dynamic_regret_ticks": regret["dynamic_regret_ticks"],
            "dynamic_regret_improvement_vs_static_ticks": regret_improvement,
            "dynamic_regret_below_static": (
                regret_improvement > 0.0
                if regret_improvement is not None
                else False
            ),
            "forward_authorized": False,
        }
    )


def _validate_matrix(summaries: Sequence[Mapping[str, Any]]) -> None:
    actual = {
        (str(row.get("profile")), str(row.get("protocol"))) for row in summaries
    }
    expected = {
        (profile, protocol)
        for profile in REQUIRED_PROFILES
        for protocol in REQUIRED_PROTOCOLS
    }
    if actual != expected or len(summaries) != len(expected):
        raise DynamicMultisequenceError(
            "Phase 4 requires the complete frozen Fast/Balanced/Slow by "
            "daily-frozen/event-updated matrix"
        )


def _validate_independent_reset(
    state_rows: Sequence[Mapping[str, Any]],
) -> None:
    grouped: Dict[Tuple[str, str], List[Mapping[str, Any]]] = defaultdict(list)
    for row in state_rows:
        grouped[(str(row["profile"]), str(row["protocol"]))].append(row)
    expected = {
        (profile, protocol)
        for profile in REQUIRED_PROFILES
        for protocol in REQUIRED_PROTOCOLS
    }
    if set(grouped) != expected:
        raise DynamicMultisequenceError(
            "selector state ledger does not contain the complete matrix"
        )
    for key, rows in grouped.items():
        first_index = min(int(row["decision_index"]) for row in rows)
        if first_index != 0:
            raise DynamicMultisequenceError(
                f"selector state did not reset at decision zero for {key}"
            )
        first = [row for row in rows if int(row["decision_index"]) == 0]
        if any(str(row.get("previous_state") or "") != "OFF" for row in first):
            raise DynamicMultisequenceError(
                f"selector state carryover detected for {key}"
            )


def _policy_turnover(
    states: Sequence[Mapping[str, Any]],
) -> Tuple[int, int, List[int]]:
    grouped: Dict[str, List[Mapping[str, Any]]] = defaultdict(list)
    for row in states:
        grouped[str(row["context_cell"])].append(row)
    switches = parameter_switches = 0
    dwell_lengths: List[int] = []
    for rows in grouped.values():
        by_boundary: Dict[int, List[Mapping[str, Any]]] = defaultdict(list)
        for row in rows:
            by_boundary[int(row["decision_index"])].append(row)
        policies: List[str] = []
        for index in sorted(by_boundary):
            on = [
                row
                for row in by_boundary[index]
                if str(row["state"]) == "ON" and row.get("selected_expert_id")
            ]
            policies.append(
                str(on[-1]["selected_expert_id"]) if on else "OFF"
            )
        if not policies:
            continue
        run = 1
        last_active: str | None = None
        for prior, current in zip(policies, policies[1:]):
            if prior != "OFF" and last_active is None:
                last_active = prior
            if current == prior:
                run += 1
                continue
            switches += 1
            if current != "OFF" and last_active not in (None, current):
                parameter_switches += 1
            if current != "OFF":
                last_active = current
            dwell_lengths.append(run)
            run = 1
        dwell_lengths.append(run)
    return switches, parameter_switches, dwell_lengths


def _window_turnover(
    windows: Sequence[Mapping[str, Any]],
    *,
    decision_indexes: Sequence[int],
) -> Tuple[int, int]:
    signatures: Dict[int, List[Tuple[Any, ...]]] = defaultdict(list)
    for row in windows:
        signatures[int(row["decision_index"])].append(
            (
                str(row["signal_side"]),
                str(row["trend_state"]),
                str(row["start_time"]),
                str(row["end_time"]),
                str(row["expert_lane_id"]),
                str(row["mode"]),
                str(row["trade_direction"]),
            )
        )
    indexes = sorted(set(decision_indexes))
    if not indexes:
        return 0, 0
    ordered = [tuple(sorted(signatures[index])) for index in indexes]
    return len(ordered), sum(
        current != prior for prior, current in zip(ordered, ordered[1:])
    )


def _records(value: Any) -> List[Dict[str, Any]]:
    if isinstance(value, pd.DataFrame):
        return value.to_dict("records")
    return [dict(row) for row in value]


def _require_sequence(
    rows: Sequence[Mapping[str, Any]],
    *,
    sequence: str,
    label: str,
) -> None:
    if not rows:
        raise DynamicMultisequenceError(f"{label} rows are required")
    actual = {str(row.get("sequence") or "") for row in rows}
    if actual != {sequence}:
        raise DynamicMultisequenceError(
            f"{label} rows are not isolated to sequence {sequence}"
        )


def _list_value(value: Any) -> List[Any]:
    if isinstance(value, (list, tuple, set)):
        return list(value)
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return []
    text = str(value).strip()
    if not text:
        return []
    for parser in (json.loads, literal_eval):
        try:
            parsed = parser(text)
        except (ValueError, SyntaxError, json.JSONDecodeError):
            continue
        if isinstance(parsed, (list, tuple, set)):
            return list(parsed)
    return [text]


def _truth_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "t", "yes", "y", "1"}


def _rate(numerator: int, denominator: int) -> float | None:
    return 100.0 * numerator / denominator if denominator else None


def _write_csv(
    path: Path,
    rows: Sequence[Mapping[str, Any]],
    columns: Sequence[str],
) -> None:
    pd.DataFrame(list(rows), columns=list(columns)).to_csv(
        path, index=False, lineterminator="\n"
    )


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(
            _json_safe(payload),
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )


def _sha256_json(value: Any) -> str:
    encoded = json.dumps(
        _json_safe(value),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if hasattr(value, "item"):
        return _json_safe(value.item())
    if isinstance(value, float) and not isfinite(value):
        if value > 0:
            return "Infinity"
        if value < 0:
            return "-Infinity"
        return None
    return value
