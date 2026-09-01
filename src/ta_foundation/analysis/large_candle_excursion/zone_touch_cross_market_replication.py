from __future__ import annotations

"""Frozen cross-market replication audit for the bearish zone-touch policy."""

import hashlib
import json
from math import isfinite
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence

import numpy as np
import pandas as pd

from ta_foundation.core.manifest import sha256_file


SCHEMA_VERSION = "zone_touch_cross_market_replication.v1"
EXPECTED_SEQUENCES = ("ES 09-26", "RTY 09-26", "YM 09-26")
FROZEN_POLICY: Dict[str, Any] = {
    "timeframe": 1,
    "lookback": 5,
    "basis": "range",
    "multiplier": 1.5,
    "average_mode": "include_current",
    "signal_side": "bear",
    "trigger_type": "zone_touch",
    "mode": "continuation",
    "target_ticks": 75.0,
    "stop_ticks": 150.0,
    "round_trip_cost_ticks": 4.796,
    "max_concurrent_per_direction": 3,
    "block_sessions": 5,
    "bootstrap_draws": 10_000,
    "bootstrap_seed": 20260803,
}
FROZEN_GATES: Dict[str, Any] = {
    "minimum_executed_each_sequence": 60,
    "minimum_executed_pooled": 180,
    "minimum_positive_sequences": 2,
    "minimum_pooled_profit_factor": 1.10,
    "minimum_positive_block_rate_pct": 55.0,
    "maximum_positive_sequence_share_pct": 60.0,
    "minimum_stressed_profit_factor": 1.0,
    "bootstrap_confidence": 0.95,
}


class ZoneTouchReplicationError(ValueError):
    pass


def run_zone_touch_cross_market_replication(
    sequence_rows: Mapping[str, Sequence[Mapping[str, Any]] | pd.DataFrame],
    *,
    source_manifests: Optional[Mapping[str, Mapping[str, Any]]] = None,
    source_bindings: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Audit the prospectively frozen policy without selecting any parameters."""
    if set(sequence_rows) != set(EXPECTED_SEQUENCES):
        raise ZoneTouchReplicationError(
            "replication requires exactly ES 09-26, RTY 09-26, and YM 09-26"
        )

    event_frames = []
    for sequence in EXPECTED_SEQUENCES:
        frame = _canonical_policy_rows(sequence_rows[sequence], sequence)
        event_frames.append(frame)
    events = pd.concat(event_frames, ignore_index=True).sort_values(
        ["sequence", "entry_dt", "physical_opportunity_id", "lane_event_id"]
    ).reset_index(drop=True)

    capacity = _apply_capacity(events)
    executed = capacity.loc[capacity["executed"]].copy()
    executed = _attach_session_partitions(executed)
    blocks = _five_session_blocks(executed)

    sequence_summaries = [
        {
            "schema_version": SCHEMA_VERSION,
            "sequence": sequence,
            **_metrics(executed.loc[executed["sequence"] == sequence]),
        }
        for sequence in EXPECTED_SEQUENCES
    ]
    pooled = _metrics(executed)
    first_half = _metrics(executed.loc[executed["chronological_half"] == "first"])
    second_half = _metrics(executed.loc[executed["chronological_half"] == "second"])
    stress = _cost_stress(executed)
    bootstrap = _session_cluster_bootstrap(executed)

    complete_blocks = blocks.loc[blocks["complete_block"]]
    positive_block_rate = (
        100.0 * float((complete_blocks["net_ticks"] > 0).mean())
        if len(complete_blocks)
        else None
    )
    positive_sequence_pnl = [
        max(0.0, float(row["net_ticks"])) for row in sequence_summaries
    ]
    positive_total = sum(positive_sequence_pnl)
    largest_positive_share = (
        100.0 * max(positive_sequence_pnl) / positive_total
        if positive_total > 0
        else None
    )

    coverage_criteria = {
        "minimum_each_sequence": all(
            row["trades"] >= FROZEN_GATES["minimum_executed_each_sequence"]
            for row in sequence_summaries
        ),
        "minimum_pooled": pooled["trades"] >= FROZEN_GATES["minimum_executed_pooled"],
    }
    replication_criteria = {
        "positive_sequences": sum(
            row["net_ticks"] > 0 and _pf_above(row, 1.0)
            for row in sequence_summaries
        ) >= FROZEN_GATES["minimum_positive_sequences"],
        "pooled_edge": pooled["net_ticks"] > 0
        and _pf_at_least(pooled, FROZEN_GATES["minimum_pooled_profit_factor"]),
        "both_halves_positive": first_half["mean_ticks"] > 0
        and second_half["mean_ticks"] > 0,
        "positive_blocks": positive_block_rate is not None
        and positive_block_rate >= FROZEN_GATES["minimum_positive_block_rate_pct"],
        "not_concentrated": largest_positive_share is not None
        and largest_positive_share < FROZEN_GATES["maximum_positive_sequence_share_pct"],
        "double_cost_positive": stress["net_ticks"] > 0
        and _pf_above(stress, FROZEN_GATES["minimum_stressed_profit_factor"]),
        "bootstrap_lower_positive": bootstrap["lower_95_mean_ticks"] > 0,
    }
    coverage_passed = all(coverage_criteria.values())
    replicated = coverage_passed and all(replication_criteria.values())
    if not coverage_passed:
        result_label = "ZONE_TOUCH_CROSS_MARKET_INSUFFICIENT_DATA"
    elif replicated:
        result_label = "ZONE_TOUCH_CROSS_MARKET_REPLICATED"
    else:
        result_label = "ZONE_TOUCH_CROSS_MARKET_NOT_REPLICATED"

    summary = _json_safe({
        "schema_version": SCHEMA_VERSION,
        "research_phase": "zone_touch_cross_market_replication",
        "result_label": result_label,
        "sequence_summaries": sequence_summaries,
        "pooled": pooled,
        "first_half": first_half,
        "second_half": second_half,
        "complete_five_session_blocks": int(len(complete_blocks)),
        "positive_five_session_block_rate_pct": positive_block_rate,
        "largest_positive_sequence_share_pct": largest_positive_share,
        "double_cost": {key: value for key, value in stress.items() if key != "rows"},
        "bootstrap": {key: value for key, value in bootstrap.items() if key != "draw_rows"},
        "coverage": {
            "criteria": coverage_criteria,
            "passed": coverage_passed,
        },
        "replication_gates": {
            "criteria": replication_criteria,
            "passed": replicated,
        },
        "tick_path_robustness_authorized": replicated,
        "simulated_forward_design_authorized": replicated,
        "live_trading_authorized": False,
    })

    safe_rows = {
        "event_rows": _records(events),
        "capacity_rows": _records(capacity),
        "block_rows": _records(blocks),
        "bootstrap_rows": bootstrap["draw_rows"],
        "cost_stress_rows": _records(stress["rows"]),
    }
    sources = {
        sequence: {
            "manifest_sha256": manifest.get("manifest_sha256"),
            "outcome_cube_sha256": manifest.get("outcome_cube_sha256"),
            "source_bar_content_sha256": (manifest.get("source_data") or {}).get(
                "bar_content_sha256"
            ),
        }
        for sequence, manifest in (source_manifests or {}).items()
    }
    manifest: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "research_phase": "zone_touch_cross_market_replication",
        "configuration": {
            "payload": FROZEN_POLICY,
            "sha256": _sha256_json(FROZEN_POLICY),
        },
        "gates": {
            "payload": FROZEN_GATES,
            "sha256": _sha256_json(FROZEN_GATES),
        },
        "source_outcome_cubes": sources,
        "source_bindings": _json_safe(dict(source_bindings or {})),
        "contracts": {
            "one_execution_per_physical_opportunity": True,
            "capacity_recomputed_after_deduplication": True,
            "blocks_formed_within_sequence": True,
            "bootstrap_samples_sessions_within_sequence": True,
            "no_parameter_selection": True,
        },
        "counts": {key: len(value) for key, value in safe_rows.items()},
        "summary_sha256": _sha256_json(summary),
        **{f"{key}_sha256": _sha256_json(value) for key, value in safe_rows.items()},
    }
    manifest["manifest_sha256"] = _sha256_json(manifest)
    return {"summary": summary, "manifest": manifest, **safe_rows}


def write_zone_touch_cross_market_replication(
    output_dir: Path,
    result: Mapping[str, Any],
) -> Dict[str, str]:
    root = Path(output_dir).resolve()
    definitions = {
        "summary": ("zone_touch_replication_summary.json", "json", "summary"),
        "events": ("zone_touch_events.csv", "csv", "event_rows"),
        "capacity": ("capacity_ledger.csv", "csv", "capacity_rows"),
        "blocks": ("five_session_blocks.csv", "csv", "block_rows"),
        "bootstrap": ("session_cluster_bootstrap.csv", "csv", "bootstrap_rows"),
        "cost_stress": ("double_cost_ledger.csv", "csv", "cost_stress_rows"),
        "manifest": ("zone_touch_replication_manifest.json", "json", "manifest"),
    }
    paths = {name: root / spec[0] for name, spec in definitions.items()}
    if root.exists() or any(path.exists() for path in paths.values()):
        raise FileExistsError(f"refusing to overwrite zone-touch artifacts: {root}")
    root.mkdir(parents=True)
    for name, (_, kind, key) in definitions.items():
        if name == "manifest":
            continue
        if kind == "json":
            _write_json(paths[name], result[key])
        else:
            pd.DataFrame(result[key]).to_csv(paths[name], index=False, lineterminator="\n")

    manifest = dict(result["manifest"])
    manifest.pop("manifest_sha256", None)
    manifest["artifacts"] = {
        name: {
            "filename": path.name,
            "bytes": int(path.stat().st_size),
            "sha256": sha256_file(path),
        }
        for name, path in paths.items()
        if name != "manifest"
    }
    manifest["manifest_sha256"] = _sha256_json(manifest)
    _write_json(paths["manifest"], manifest)
    return {name: str(path) for name, path in paths.items()}


def _canonical_policy_rows(
    rows: Sequence[Mapping[str, Any]] | pd.DataFrame,
    sequence: str,
) -> pd.DataFrame:
    frame = rows.copy() if isinstance(rows, pd.DataFrame) else pd.DataFrame(rows)
    required = {
        "sequence", "physical_opportunity_id", "lane_event_id", "outcome_id",
        "entry_dt", "exit_known_dt", "session_id", "trade_direction",
        "net_pnl_ticks", "timeframe", "lookback", "basis", "multiplier",
        "average_mode", "signal_side", "trigger_type", "mode", "target_ticks",
        "stop_ticks", "round_trip_cost_ticks",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ZoneTouchReplicationError(f"{sequence} rows missing columns: {missing}")
    if frame.empty:
        return frame
    if set(frame["sequence"].astype(str)) != {sequence}:
        raise ZoneTouchReplicationError(f"sequence mismatch for {sequence}")

    checks = {
        "timeframe": FROZEN_POLICY["timeframe"],
        "lookback": FROZEN_POLICY["lookback"],
        "basis": FROZEN_POLICY["basis"],
        "multiplier": FROZEN_POLICY["multiplier"],
        "average_mode": FROZEN_POLICY["average_mode"],
        "signal_side": FROZEN_POLICY["signal_side"],
        "trigger_type": FROZEN_POLICY["trigger_type"],
        "mode": FROZEN_POLICY["mode"],
        "target_ticks": FROZEN_POLICY["target_ticks"],
        "stop_ticks": FROZEN_POLICY["stop_ticks"],
        "round_trip_cost_ticks": FROZEN_POLICY["round_trip_cost_ticks"],
    }
    mask = pd.Series(True, index=frame.index)
    for column, expected in checks.items():
        if isinstance(expected, float):
            mask &= np.isclose(pd.to_numeric(frame[column], errors="coerce"), expected)
        else:
            mask &= frame[column].astype(str) == str(expected)
    frame = frame.loc[mask].copy()
    if frame.empty:
        return frame
    frame["entry_dt"] = pd.to_datetime(frame["entry_dt"], errors="raise", utc=True)
    frame["exit_known_dt"] = pd.to_datetime(frame["exit_known_dt"], errors="raise", utc=True)
    frame["net_pnl_ticks"] = pd.to_numeric(frame["net_pnl_ticks"], errors="raise")
    frame["trade_direction"] = pd.to_numeric(frame["trade_direction"], errors="raise").astype(int)
    frame = frame.sort_values(
        ["entry_dt", "physical_opportunity_id", "lane_event_id", "outcome_id"]
    ).drop_duplicates("physical_opportunity_id", keep="first")
    return frame.reset_index(drop=True)


def _apply_capacity(events: pd.DataFrame) -> pd.DataFrame:
    records = []
    for sequence, group in events.groupby("sequence", sort=False):
        active: Dict[int, list[pd.Timestamp]] = {1: [], -1: []}
        for row in group.sort_values(["entry_dt", "physical_opportunity_id"]).to_dict("records"):
            direction = int(row["trade_direction"])
            entry = pd.Timestamp(row["entry_dt"])
            active[direction] = [value for value in active[direction] if value > entry]
            executed = len(active[direction]) < FROZEN_POLICY["max_concurrent_per_direction"]
            if executed:
                active[direction].append(pd.Timestamp(row["exit_known_dt"]))
            records.append({
                **row,
                "schema_version": SCHEMA_VERSION,
                "executed": executed,
                "capacity_skip": not executed,
                "executed_net_ticks": float(row["net_pnl_ticks"]) if executed else 0.0,
            })
    return pd.DataFrame(records)


def _attach_session_partitions(executed: pd.DataFrame) -> pd.DataFrame:
    if executed.empty:
        out = executed.copy()
        out["session_index"] = pd.Series(dtype=int)
        out["block_index"] = pd.Series(dtype=int)
        out["chronological_half"] = pd.Series(dtype=str)
        return out
    out = executed.copy()
    sessions = (
        out.groupby(["sequence", "session_id"], as_index=False)["entry_dt"]
        .min()
        .sort_values(["sequence", "entry_dt"])
    )
    sessions["session_index"] = sessions.groupby("sequence").cumcount()
    session_counts = sessions.groupby("sequence")["session_index"].transform("max") + 1
    sessions["chronological_half"] = np.where(
        sessions["session_index"] < session_counts / 2.0, "first", "second"
    )
    sessions["block_index"] = sessions["session_index"] // FROZEN_POLICY["block_sessions"]
    return out.merge(
        sessions[["sequence", "session_id", "session_index", "block_index", "chronological_half"]],
        on=["sequence", "session_id"],
        validate="many_to_one",
    )


def _five_session_blocks(executed: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "schema_version", "sequence", "block_index", "sessions", "trades",
        "net_ticks", "mean_ticks", "profit_factor", "profit_factor_infinite",
        "complete_block",
    ]
    rows = []
    for (sequence, block_index), group in executed.groupby(
        ["sequence", "block_index"], sort=True
    ):
        metrics = _metrics(group)
        rows.append({
            "schema_version": SCHEMA_VERSION,
            "sequence": sequence,
            "block_index": int(block_index),
            **metrics,
            "complete_block": metrics["sessions"] == FROZEN_POLICY["block_sessions"],
        })
    return pd.DataFrame(rows, columns=columns)


def _cost_stress(executed: pd.DataFrame) -> Dict[str, Any]:
    rows = executed.copy()
    rows["base_net_pnl_ticks"] = rows["net_pnl_ticks"].astype(float)
    rows["net_pnl_ticks"] = (
        rows["base_net_pnl_ticks"] - FROZEN_POLICY["round_trip_cost_ticks"]
    )
    metrics = _metrics(rows)
    return {**metrics, "cost_multiple": 2.0, "rows": rows}


def _session_cluster_bootstrap(executed: pd.DataFrame) -> Dict[str, Any]:
    rng = np.random.default_rng(FROZEN_POLICY["bootstrap_seed"])
    by_sequence: Dict[str, list[np.ndarray]] = {}
    for sequence in EXPECTED_SEQUENCES:
        group = executed.loc[executed["sequence"] == sequence]
        by_sequence[sequence] = [
            session["net_pnl_ticks"].to_numpy(dtype=float)
            for _, session in group.groupby("session_id", sort=True)
        ]
    draw_rows = []
    draw_values = []
    for draw in range(FROZEN_POLICY["bootstrap_draws"]):
        sampled = []
        for sequence in EXPECTED_SEQUENCES:
            clusters = by_sequence[sequence]
            if not clusters:
                continue
            indices = rng.integers(0, len(clusters), size=len(clusters))
            sampled.extend(clusters[int(index)] for index in indices)
        values = np.concatenate(sampled) if sampled else np.asarray([], dtype=float)
        mean_ticks = float(values.mean()) if len(values) else 0.0
        draw_values.append(mean_ticks)
        draw_rows.append({
            "schema_version": SCHEMA_VERSION,
            "draw": draw,
            "mean_ticks": mean_ticks,
            "sampled_trades": int(len(values)),
        })
    alpha = (1.0 - FROZEN_GATES["bootstrap_confidence"]) / 2.0
    return {
        "draws": FROZEN_POLICY["bootstrap_draws"],
        "seed": FROZEN_POLICY["bootstrap_seed"],
        "lower_95_mean_ticks": float(np.quantile(draw_values, alpha)),
        "median_mean_ticks": float(np.quantile(draw_values, 0.5)),
        "upper_95_mean_ticks": float(np.quantile(draw_values, 1.0 - alpha)),
        "draw_rows": draw_rows,
    }


def _metrics(frame: pd.DataFrame) -> Dict[str, Any]:
    pnl = frame["net_pnl_ticks"].astype(float) if len(frame) else pd.Series(dtype=float)
    gains = float(pnl.loc[pnl > 0].sum())
    losses = abs(float(pnl.loc[pnl < 0].sum()))
    return {
        "trades": int(len(frame)),
        "sessions": int(frame["session_id"].nunique()) if "session_id" in frame else 0,
        "net_ticks": float(pnl.sum()) if len(pnl) else 0.0,
        "mean_ticks": float(pnl.mean()) if len(pnl) else 0.0,
        "profit_factor": gains / losses if losses > 0 else None,
        "profit_factor_infinite": bool(gains > 0 and losses == 0),
    }


def _pf_at_least(metrics: Mapping[str, Any], threshold: float) -> bool:
    return bool(metrics.get("profit_factor_infinite")) or (
        metrics.get("profit_factor") is not None
        and float(metrics["profit_factor"]) >= threshold
    )


def _pf_above(metrics: Mapping[str, Any], threshold: float) -> bool:
    return bool(metrics.get("profit_factor_infinite")) or (
        metrics.get("profit_factor") is not None
        and float(metrics["profit_factor"]) > threshold
    )


def _records(frame: pd.DataFrame) -> list[Dict[str, Any]]:
    return _json_safe(frame.to_dict("records"))


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(_json_safe(payload), indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _sha256_json(value: Any) -> str:
    encoded = json.dumps(
        _json_safe(value), sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (pd.Timestamp, np.datetime64)):
        return pd.Timestamp(value).isoformat()
    if isinstance(value, np.generic):
        return _json_safe(value.item())
    if isinstance(value, float) and not isfinite(value):
        return None
    if value is pd.NA:
        return None
    return value

