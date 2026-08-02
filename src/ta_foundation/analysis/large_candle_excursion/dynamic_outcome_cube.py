from __future__ import annotations

"""Dynamic Phase 1 multi-parameter outcome cube.

The cube is a research ledger, not a selector.  It expands the declared
Family A signal grid, attaches causal context, retains paired paper outcomes,
and gives every physical opportunity and expert a stable identity.
"""

import hashlib
import json
from math import isfinite
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

import numpy as np
import pandas as pd

from ta_foundation.analysis.large_candle_excursion.adaptive_context import (
    attach_context_to_events,
    build_intraday_context,
)
from ta_foundation.analysis.large_candle_excursion.adaptive_window import (
    DEFAULT_ADAPTIVE_WINDOW_CONFIG,
    build_adaptive_event_streams,
)
from ta_foundation.core.manifest import sha256_file


DYNAMIC_OUTCOME_CUBE_SCHEMA_VERSION = "dynamic_outcome_cube.v1"
FAMILY_A_TIMEFRAMES = (1, 2, 3, 5)
FAMILY_A_LOOKBACKS = (5, 10, 20)
FAMILY_A_MULTIPLIERS = (1.25, 1.5, 2.0)
FAMILY_A_SIGNAL_SIDES = ("bull", "bear")
FAMILY_A_MODES = ("continuation", "reversion")


def _deep_merge(
    base: Mapping[str, Any],
    override: Mapping[str, Any],
) -> Dict[str, Any]:
    out = dict(base)
    for key, value in override.items():
        if isinstance(out.get(key), Mapping) and isinstance(value, Mapping):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


DEFAULT_DYNAMIC_FAMILY_A_CONFIG: Dict[str, Any] = _deep_merge(
    DEFAULT_ADAPTIVE_WINDOW_CONFIG,
    {
        "timeframes": list(FAMILY_A_TIMEFRAMES),
        "lookbacks": list(FAMILY_A_LOOKBACKS),
        "bases": ["range"],
        "multipliers": list(FAMILY_A_MULTIPLIERS),
        "signal_direction": "both",
        "outcome": {
            "target_ticks": 75.0,
            "stop_ticks": 150.0,
        },
        "context": {
            "timezone": "America/Denver",
            "session_anchor": "07:30",
            "time_bucket_minutes": 30,
            "vwap_price": "typical",
            "vwap_slope_bars": 15,
            "return_lookback_minutes": 60,
        },
    },
)


OUTCOME_CUBE_COLUMNS = (
    "schema_version",
    "sequence",
    "physical_opportunity_id",
    "lane_event_id",
    "expert_id",
    "outcome_id",
    "signal_family",
    "lane_id",
    "expert_lane_id",
    "signal_side",
    "timeframe",
    "lookback",
    "basis",
    "multiplier",
    "average_mode",
    "mode",
    "context_cell",
    "session_id",
    "time_bucket",
    "trend_state",
    "context_dt",
    "context_history_complete",
    "session_vwap",
    "close_vs_vwap",
    "vwap_slope_15",
    "return_60m",
    "trend_votes",
    "signal_dt",
    "entry_dt",
    "trigger_source_dt",
    "trigger_type",
    "latched_outside_window",
    "source_bar_idx",
    "rolling_avg_ticks",
    "signal_size_ticks",
    "signal_ratio",
    "signal_direction",
    "fresh_trigger",
    "zone_break_trigger",
    "zones_broken",
    "open",
    "high",
    "low",
    "close",
    "target_ticks",
    "stop_ticks",
    "round_trip_cost_ticks",
    "max_hold_minutes",
    "max_concurrent_per_direction",
    "same_bar_policy",
    "trade_direction",
    "outcome_entry_dt",
    "exit_dt",
    "exit_known_dt",
    "entry_price",
    "exit_price",
    "gross_pnl_ticks",
    "net_pnl_ticks",
    "mfe_ticks",
    "mae_ticks",
    "exit_reason",
    "ambiguous_same_bar",
    "bars_held_1m",
    "capacity_eligible",
)


def build_dynamic_family_a_outcome_cube(
    bars_1m: pd.DataFrame,
    config: Optional[Mapping[str, Any]] = None,
    *,
    sequence: str,
    source_metadata: Optional[Mapping[str, Any]] = None,
    strict_catalog: bool = True,
) -> Dict[str, Any]:
    """Build the complete Dynamic Phase 1 ledger for one chronology.

    ``strict_catalog=True`` freezes the declared 36 Family A signal lanes,
    both signal sides, and the reconciled 75/150-tick bracket.  Tests and
    development fixtures may opt out only to exercise the same mechanics on a
    smaller grid.

    Events whose higher-timeframe source bar is not complete and outcome pairs
    censored by the right edge are intentionally withheld.  They may appear
    after more bars arrive, but no already-emitted row can be revised merely
    because future bars were appended.
    """
    sequence_id = str(sequence).strip()
    if not sequence_id:
        raise ValueError("sequence must be a non-empty stable identifier")

    resolved = _deep_merge(DEFAULT_DYNAMIC_FAMILY_A_CONFIG, config or {})
    if strict_catalog:
        _validate_family_a_catalog(resolved)

    bars = _prepare_source_bars(bars_1m)
    observation_asof = pd.Timestamp(bars["dt"].max()) + pd.Timedelta(minutes=1)
    context_frame = build_intraday_context(bars, resolved)
    streams = build_adaptive_event_streams(bars, resolved)

    attachable: List[Dict[str, Any]] = []
    incomplete_source_events = 0
    incomplete_outcome_pairs = 0
    for stream in streams:
        for raw_event in stream["events"]:
            event = dict(raw_event)
            if not _source_bar_complete(event, observation_asof):
                incomplete_source_events += 1
                continue
            if not all(
                _outcome_is_final(
                    event.get(mode) or {},
                    event,
                    resolved,
                    observation_asof,
                )
                for mode in FAMILY_A_MODES
            ):
                incomplete_outcome_pairs += 1
                continue
            event["_lane_id"] = str(stream["lane_id"])
            event["_timeframe"] = int(stream["timeframe"])
            event["_lookback"] = int(stream["lookback"])
            event["_basis"] = str(stream["basis"])
            event["_multiplier"] = float(stream["multiplier"])
            attachable.append(event)

    contextual_events = attach_context_to_events(
        attachable,
        context_frame,
        resolved,
    )
    rows: List[Dict[str, Any]] = []
    experts: Dict[str, Dict[str, Any]] = {}
    for event in contextual_events:
        physical_id = _physical_opportunity_id(sequence_id, event)
        lane_event_id = _lane_event_id(sequence_id, event)
        for mode in FAMILY_A_MODES:
            expert_parameters = _expert_parameters(event, mode, resolved)
            expert_id = _stable_id("expert", expert_parameters)
            experts.setdefault(
                expert_id,
                {
                    "expert_id": expert_id,
                    "parameters": _json_safe(expert_parameters),
                    "parameters_sha256": _sha256_json(expert_parameters),
                },
            )
            rows.append(
                _outcome_row(
                    sequence_id,
                    physical_id,
                    lane_event_id,
                    expert_id,
                    event,
                    mode,
                    resolved,
                )
            )

    rows.sort(
        key=lambda row: (
            str(row["entry_dt"]),
            str(row["physical_opportunity_id"]),
            str(row["expert_id"]),
            str(row["mode"]),
        )
    )
    expert_catalog = sorted(experts.values(), key=lambda row: row["expert_id"])
    lane_catalog = _build_lane_catalog(resolved)
    opportunities = deduplicate_execution_candidates(rows)

    source = {
        "bar_content_sha256": _hash_source_bars(bars),
        "rows": int(len(bars)),
        "start": pd.Timestamp(bars["dt"].min()).isoformat(),
        "end": pd.Timestamp(bars["dt"].max()).isoformat(),
        "observation_asof": observation_asof.isoformat(),
        "provenance": _json_safe(dict(source_metadata or {})),
    }
    safe_config = _json_safe(resolved)
    manifest: Dict[str, Any] = {
        "schema_version": DYNAMIC_OUTCOME_CUBE_SCHEMA_VERSION,
        "research_phase": "dynamic_phase_1",
        "sequence": sequence_id,
        "source_data": source,
        "configuration": {
            "sha256": _sha256_json(safe_config),
            "payload": safe_config,
        },
        "catalog_contract": {
            "strict_family_a": bool(strict_catalog),
            "signal_lane_count": int(len(lane_catalog) // 2),
            "signal_side_lane_count": int(len(lane_catalog)),
            "timeframes": sorted(
                {int(row["timeframe"]) for row in lane_catalog}
            ),
            "lookbacks": sorted(
                {int(row["lookback"]) for row in lane_catalog}
            ),
            "multipliers": sorted(
                {float(row["multiplier"]) for row in lane_catalog}
            ),
            "signal_sides": list(FAMILY_A_SIGNAL_SIDES),
            "modes": list(FAMILY_A_MODES),
        },
        "causality_contract": {
            "context_boundary": "context_dt <= signal_dt",
            "outcome_boundary": "eligible only when exit_known_dt < decision_asof",
            "right_edge_policy": (
                "withhold incomplete source bars and paired outcomes until final"
            ),
            "execution_deduplication_key": "physical_opportunity_id",
            "maximum_executable_experts_per_physical_opportunity": 1,
        },
        "counts": {
            "source_streams_with_events": int(len(streams)),
            "complete_lane_events": int(len(contextual_events)),
            "outcome_rows": int(len(rows)),
            "observed_experts": int(len(expert_catalog)),
            "physical_opportunities": int(len(opportunities)),
            "duplicate_lane_events": int(
                max(0, len(contextual_events) - len(opportunities))
            ),
            "withheld_incomplete_source_events": int(incomplete_source_events),
            "withheld_incomplete_outcome_pairs": int(incomplete_outcome_pairs),
        },
        "row_columns": list(OUTCOME_CUBE_COLUMNS),
        "lane_catalog_sha256": _sha256_json(lane_catalog),
        "expert_catalog_sha256": _sha256_json(expert_catalog),
        "physical_opportunity_index_sha256": _sha256_json(opportunities),
        "outcome_cube_sha256": _sha256_json(rows),
    }
    manifest["manifest_sha256"] = _sha256_json(manifest)
    return {
        "manifest": manifest,
        "lane_catalog": lane_catalog,
        "expert_catalog": expert_catalog,
        "physical_opportunities": opportunities,
        "rows": rows,
    }


def deduplicate_execution_candidates(
    rows: Sequence[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    """Collapse expert rows to one execution key per physical opportunity.

    No paper outcome is discarded from the cube.  The returned index is the
    explicit boundary a later selector must use to enforce at most one live
    execution among all lane/mode experts representing the same opportunity.
    """
    grouped: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        physical_id = str(row.get("physical_opportunity_id") or "")
        if not physical_id:
            raise ValueError("outcome row missing physical_opportunity_id")
        record = grouped.setdefault(
            physical_id,
            {
                "physical_opportunity_id": physical_id,
                "sequence": row.get("sequence"),
                "entry_dt": row.get("entry_dt"),
                "signal_side": row.get("signal_side"),
                "session_id": row.get("session_id"),
                "lane_event_ids": set(),
                "expert_ids": set(),
                "outcome_ids": set(),
            },
        )
        for field in ("sequence", "entry_dt", "signal_side", "session_id"):
            if record[field] != row.get(field):
                raise ValueError(
                    f"physical opportunity collision on {field}: {physical_id}"
                )
        record["lane_event_ids"].add(str(row.get("lane_event_id")))
        record["expert_ids"].add(str(row.get("expert_id")))
        record["outcome_ids"].add(str(row.get("outcome_id")))

    out: List[Dict[str, Any]] = []
    for record in grouped.values():
        lane_event_ids = sorted(record.pop("lane_event_ids"))
        expert_ids = sorted(record.pop("expert_ids"))
        outcome_ids = sorted(record.pop("outcome_ids"))
        out.append(
            {
                **record,
                "candidate_lane_event_count": len(lane_event_ids),
                "candidate_expert_count": len(expert_ids),
                "paper_outcome_count": len(outcome_ids),
                "candidate_lane_event_ids": lane_event_ids,
                "candidate_expert_ids": expert_ids,
                "paper_outcome_ids": outcome_ids,
            }
        )
    return sorted(
        out,
        key=lambda row: (
            str(row["entry_dt"]),
            str(row["physical_opportunity_id"]),
        ),
    )


def write_dynamic_outcome_cube(
    output_dir: Path,
    cube: Mapping[str, Any],
    *,
    overwrite: bool = False,
) -> Dict[str, str]:
    """Write a stable Phase 1 artifact bundle and hash-bound manifest."""
    root = Path(output_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    paths = {
        "outcome_cube": root / "outcome_cube.csv",
        "lane_catalog": root / "lane_catalog.json",
        "expert_catalog": root / "expert_catalog.json",
        "physical_opportunities": root / "physical_opportunities.json",
        "manifest": root / "outcome_cube_manifest.json",
    }
    existing = [path for path in paths.values() if path.exists()]
    if existing and not overwrite:
        raise FileExistsError(
            "refusing to overwrite outcome-cube artifacts: "
            + ", ".join(str(path) for path in existing)
        )

    rows = list(cube.get("rows") or [])
    expected_columns = set(OUTCOME_CUBE_COLUMNS)
    for index, row in enumerate(rows):
        if set(row) != expected_columns:
            missing = sorted(expected_columns - set(row))
            extra = sorted(set(row) - expected_columns)
            raise ValueError(
                f"outcome row {index} violates the stable schema; "
                f"missing={missing}, extra={extra}"
            )
    frame = pd.DataFrame(rows, columns=OUTCOME_CUBE_COLUMNS)
    for column in frame.columns:
        frame[column] = frame[column].map(_csv_scalar)
    frame.to_csv(
        paths["outcome_cube"],
        index=False,
        encoding="utf-8",
        lineterminator="\n",
    )
    _write_json(paths["lane_catalog"], list(cube.get("lane_catalog") or []))
    _write_json(paths["expert_catalog"], list(cube.get("expert_catalog") or []))
    _write_json(
        paths["physical_opportunities"],
        list(cube.get("physical_opportunities") or []),
    )

    manifest = dict(cube.get("manifest") or {})
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


def _validate_family_a_catalog(config: Mapping[str, Any]) -> None:
    checks = (
        ("timeframes", _positive_ints(config.get("timeframes") or []), list(FAMILY_A_TIMEFRAMES)),
        ("lookbacks", _positive_ints(config.get("lookbacks") or []), list(FAMILY_A_LOOKBACKS)),
        (
            "multipliers",
            _positive_floats(config.get("multipliers") or []),
            list(FAMILY_A_MULTIPLIERS),
        ),
        ("bases", sorted({str(v).lower() for v in config.get("bases") or []}), ["range"]),
    )
    for label, actual, expected in checks:
        if actual != expected:
            raise ValueError(
                f"strict Family A {label} must be {expected}; got {actual}"
            )
    if str(config.get("signal_direction")).lower() != "both":
        raise ValueError("strict Family A requires signal_direction='both'")
    outcome = config.get("outcome") or {}
    if float(outcome.get("target_ticks", 0.0)) != 75.0:
        raise ValueError("strict Family A requires target_ticks=75")
    if float(outcome.get("stop_ticks", 0.0)) != 150.0:
        raise ValueError("strict Family A requires stop_ticks=150")


def _prepare_source_bars(bars: pd.DataFrame) -> pd.DataFrame:
    required = ("dt", "open", "high", "low", "close", "volume")
    if bars is None or bars.empty:
        raise ValueError("Dynamic Phase 1 requires non-empty one-minute bars")
    missing = [column for column in required if column not in bars.columns]
    if missing:
        raise ValueError(f"dynamic outcome cube missing bar columns: {missing}")
    out = bars.loc[:, required].copy()
    out["dt"] = pd.to_datetime(out["dt"])
    if out["dt"].dt.tz is None:
        raise ValueError("dynamic outcome cube requires timezone-aware bars")
    for column in required[1:]:
        out[column] = pd.to_numeric(out[column], errors="raise").astype(float)
    if not np.isfinite(out.loc[:, required[1:]].to_numpy()).all():
        raise ValueError("dynamic outcome cube bar values must be finite")
    if (out["volume"] < 0).any():
        raise ValueError("dynamic outcome cube volume must be non-negative")
    return (
        out.sort_values("dt")
        .drop_duplicates("dt", keep="last")
        .reset_index(drop=True)
    )


def _source_bar_complete(
    event: Mapping[str, Any],
    observation_asof: pd.Timestamp,
) -> bool:
    return (
        _aware_timestamp(event.get("entry_dt"), label="event.entry_dt")
        <= observation_asof
    )


def _outcome_is_final(
    outcome: Mapping[str, Any],
    event: Mapping[str, Any],
    config: Mapping[str, Any],
    observation_asof: pd.Timestamp,
) -> bool:
    if not outcome.get("available"):
        return False
    exit_dt = _aware_timestamp(outcome.get("exit_dt"), label="outcome.exit_dt")
    exit_known = _aware_timestamp(
        outcome.get("exit_known_dt"),
        label="outcome.exit_known_dt",
    )
    if exit_known <= exit_dt or exit_known > observation_asof:
        return False
    if str(outcome.get("exit_reason")) == "timeout":
        max_hold = max(
            1,
            int((config.get("outcome") or {}).get("max_hold_minutes", 120)),
        )
        hold_end = _aware_timestamp(
            event.get("entry_dt"),
            label="event.entry_dt",
        ) + pd.Timedelta(minutes=max_hold)
        if exit_dt < hold_end:
            return False
    return True


def _build_lane_catalog(config: Mapping[str, Any]) -> List[Dict[str, Any]]:
    catalog: List[Dict[str, Any]] = []
    for timeframe in _positive_ints(config.get("timeframes") or []):
        for lookback in _positive_ints(config.get("lookbacks") or []):
            for basis in sorted(
                {str(value).lower() for value in config.get("bases") or []}
            ):
                for multiplier in _positive_floats(
                    config.get("multipliers") or []
                ):
                    lane_id = _lane_id(timeframe, lookback, basis, multiplier)
                    for signal_side in FAMILY_A_SIGNAL_SIDES:
                        parameters = _lane_parameters(
                            timeframe,
                            lookback,
                            basis,
                            multiplier,
                            signal_side,
                            config,
                        )
                        catalog.append(
                            {
                                "lane_id": lane_id,
                                "expert_lane_id": f"{lane_id}|{signal_side}",
                                "signal_side": signal_side,
                                "timeframe": timeframe,
                                "lookback": lookback,
                                "basis": basis,
                                "multiplier": multiplier,
                                "parameters": _json_safe(parameters),
                                "parameters_sha256": _sha256_json(parameters),
                            }
                        )
    return catalog


def _lane_parameters(
    timeframe: int,
    lookback: int,
    basis: str,
    multiplier: float,
    signal_side: str,
    config: Mapping[str, Any],
) -> Dict[str, Any]:
    return {
        "signal_family": "candle_center_bot_v2",
        "timeframe_minutes": int(timeframe),
        "range_lookback": int(lookback),
        "size_basis": str(basis),
        "large_candle_multiplier": float(multiplier),
        "signal_side": str(signal_side),
        "average_mode": config.get("average_mode"),
        "bars_required": config.get("bars_required"),
        "tick_size": config.get("tick_size"),
        "tick_value": config.get("tick_value"),
        "signals": config.get("signals") or {},
        "time_filter": config.get("time_filter") or {},
    }


def _expert_parameters(
    event: Mapping[str, Any],
    mode: str,
    config: Mapping[str, Any],
) -> Dict[str, Any]:
    outcome = config.get("outcome") or {}
    context = config.get("context") or {}
    return {
        **_lane_parameters(
            int(event["_timeframe"]),
            int(event["_lookback"]),
            str(event["_basis"]),
            float(event["_multiplier"]),
            str(event["signal_side"]),
            config,
        ),
        "context": {
            "definition": context,
            "cell": {
                "time_bucket": event.get("time_bucket"),
                "trend_state": event.get("trend_state"),
            },
        },
        "mode": mode,
        "exit_profile": outcome,
    }


def _outcome_row(
    sequence: str,
    physical_id: str,
    lane_event_id: str,
    expert_id: str,
    event: Mapping[str, Any],
    mode: str,
    config: Mapping[str, Any],
) -> Dict[str, Any]:
    outcome_cfg = config.get("outcome") or {}
    outcome = event[mode]
    context_cell = "|".join(
        (
            str(event.get("signal_side")),
            str(event.get("time_bucket")),
            str(event.get("trend_state")),
        )
    )
    row = {column: None for column in OUTCOME_CUBE_COLUMNS}
    row.update(
        {
            "schema_version": DYNAMIC_OUTCOME_CUBE_SCHEMA_VERSION,
            "sequence": sequence,
            "physical_opportunity_id": physical_id,
            "lane_event_id": lane_event_id,
            "expert_id": expert_id,
            "outcome_id": _stable_id(
                "outcome",
                {"lane_event_id": lane_event_id, "expert_id": expert_id},
            ),
            "signal_family": "candle_center_bot_v2",
            "lane_id": event["_lane_id"],
            "expert_lane_id": f"{event['_lane_id']}|{event['signal_side']}",
            "signal_side": event.get("signal_side"),
            "timeframe": event["_timeframe"],
            "lookback": event["_lookback"],
            "basis": event["_basis"],
            "multiplier": event["_multiplier"],
            "average_mode": event.get("average_mode"),
            "mode": mode,
            "context_cell": context_cell,
            "target_ticks": outcome_cfg.get("target_ticks"),
            "stop_ticks": outcome_cfg.get("stop_ticks"),
            "round_trip_cost_ticks": outcome_cfg.get("round_trip_cost_ticks"),
            "max_hold_minutes": outcome_cfg.get("max_hold_minutes"),
            "max_concurrent_per_direction": outcome_cfg.get(
                "max_concurrent_per_direction"
            ),
            "same_bar_policy": outcome_cfg.get("same_bar_policy"),
            "outcome_entry_dt": outcome.get("entry_dt"),
        }
    )
    for field in (
        "session_id",
        "time_bucket",
        "trend_state",
        "context_dt",
        "context_history_complete",
        "session_vwap",
        "close_vs_vwap",
        "vwap_slope_15",
        "return_60m",
        "trend_votes",
        "signal_dt",
        "entry_dt",
        "trigger_source_dt",
        "trigger_type",
        "latched_outside_window",
        "source_bar_idx",
        "rolling_avg_ticks",
        "signal_size_ticks",
        "signal_ratio",
        "signal_direction",
        "fresh_trigger",
        "zone_break_trigger",
        "zones_broken",
        "open",
        "high",
        "low",
        "close",
    ):
        row[field] = event.get(field)
    row["session_id"] = str(
        _aware_timestamp(event.get("entry_dt"), label="event.entry_dt").date()
    )
    for field in (
        "trade_direction",
        "exit_dt",
        "exit_known_dt",
        "entry_price",
        "exit_price",
        "gross_pnl_ticks",
        "net_pnl_ticks",
        "mfe_ticks",
        "mae_ticks",
        "exit_reason",
        "ambiguous_same_bar",
        "bars_held_1m",
        "capacity_eligible",
    ):
        row[field] = outcome.get(field)
    return _json_safe(row)


def _physical_opportunity_id(
    sequence: str,
    event: Mapping[str, Any],
) -> str:
    return _stable_id(
        "physical",
        {
            "sequence": sequence,
            "signal_family": "candle_center_bot_v2",
            "entry_dt_utc": _utc_iso(event.get("entry_dt")),
            "signal_side": event.get("signal_side"),
        },
    )


def _lane_event_id(sequence: str, event: Mapping[str, Any]) -> str:
    return _stable_id(
        "lane_event",
        {
            "sequence": sequence,
            "lane_id": event["_lane_id"],
            "signal_side": event.get("signal_side"),
            "signal_dt_utc": _utc_iso(event.get("signal_dt")),
            "entry_dt_utc": _utc_iso(event.get("entry_dt")),
            "trigger_source_dt_utc": _utc_iso(event.get("trigger_source_dt")),
            "trigger_type": event.get("trigger_type"),
        },
    )


def _hash_source_bars(bars: pd.DataFrame) -> str:
    digest = hashlib.sha256()
    digest.update(b"dynamic_outcome_cube_source_bars.v1\n")
    for row in bars.itertuples(index=False):
        values = [
            _utc_iso(row.dt),
            *[
                float(value).hex()
                for value in (
                    row.open,
                    row.high,
                    row.low,
                    row.close,
                    row.volume,
                )
            ],
        ]
        digest.update(("|".join(values) + "\n").encode("utf-8"))
    return digest.hexdigest()


def _stable_id(prefix: str, payload: Mapping[str, Any]) -> str:
    return f"{prefix}:v1:{_sha256_json(payload)[:24]}"


def _utc_iso(value: Any) -> str:
    timestamp = _aware_timestamp(value, label="timestamp")
    return timestamp.tz_convert("UTC").isoformat()


def _aware_timestamp(value: Any, *, label: str) -> pd.Timestamp:
    if value is None:
        raise ValueError(f"{label} is missing")
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        raise ValueError(f"{label} must be timezone-aware")
    return timestamp


def _lane_id(
    timeframe: int,
    lookback: int,
    basis: str,
    multiplier: float,
) -> str:
    return (
        f"tf{timeframe}m|lb{lookback}|{basis}|x{float(multiplier):g}"
    )


def _positive_ints(values: Iterable[Any]) -> List[int]:
    return sorted({int(value) for value in values if int(value) > 0})


def _positive_floats(values: Iterable[Any]) -> List[float]:
    return sorted({float(value) for value in values if float(value) > 0})


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


def _csv_scalar(value: Any) -> Any:
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(
            _json_safe(value),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    return value


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
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (pd.Timestamp, np.datetime64)):
        return pd.Timestamp(value).isoformat()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, float):
        return value if isfinite(value) else None
    if isinstance(value, Path):
        return str(value)
    return value
