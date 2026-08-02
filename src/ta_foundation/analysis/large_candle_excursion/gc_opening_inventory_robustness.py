from __future__ import annotations

"""Same-contract tick and statistical robustness audit for GC inventory policy."""

import hashlib
import json
from math import isfinite
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence

import numpy as np
import pandas as pd

from ta_foundation.core.manifest import sha256_file


SCHEMA_VERSION = "gc_opening_inventory_robustness.v1"
SEQUENCE = "GC 04-26"
CONFIG: Dict[str, Any] = {
    "tick_size": 0.1,
    "target_ticks": 75.0,
    "stop_ticks": 150.0,
    "round_trip_cost_ticks": 4.398,
    "capacity_per_direction": 3,
    "minimum_tick_coverage_pct": 99.0,
    "minimum_positive_block_rate_pct": 60.0,
    "maximum_profitable_day_share_pct": 25.0,
    "bootstrap_draws": 10000,
    "permutation_draws": 10000,
    "random_seed": 20260731,
    "cost_multipliers": [1.0, 1.5, 2.0],
}


class GCRobustnessAuditError(ValueError):
    pass


def run_gc_opening_inventory_robustness(
    parent_opportunities: Sequence[Mapping[str, Any]] | pd.DataFrame,
    source_cube: Sequence[Mapping[str, Any]] | pd.DataFrame,
    tick_path: Path,
    *,
    source_bindings: Mapping[str, Any],
) -> Dict[str, Any]:
    opportunities = _canonical_opportunities(parent_opportunities)
    selected = _select_source_outcomes(opportunities, source_cube)
    tick_ns, tick_prices, tick_meta = _load_relevant_ticks(Path(tick_path), selected)
    tick_rows = _resolve_tick_outcomes(selected, tick_ns, tick_prices)
    tick_frame = pd.DataFrame(tick_rows)
    capacity = _apply_capacity(tick_frame.loc[tick_frame["tick_path_complete"]].copy())
    capacity_rows = capacity.to_dict("records")
    base = _capacity_metrics(capacity, float(CONFIG["round_trip_cost_ticks"]))
    blocks = _block_summaries(capacity)
    deletions = _block_deletions(capacity, blocks)
    costs = _cost_stress(capacity)
    bootstrap_rows, bootstrap_summary = _session_bootstrap(capacity)
    permutation_rows, permutation_summary = _session_permutation(opportunities)
    without_ambiguous = _apply_capacity(
        tick_frame.loc[tick_frame["tick_path_complete"] & ~tick_frame["original_ambiguous_same_bar"]].copy()
    )
    without_ambiguous_metrics = _capacity_metrics(
        without_ambiguous, float(CONFIG["round_trip_cost_ticks"])
    )

    coverage_pct = 100.0 * float(tick_frame["tick_path_complete"].mean())
    complete_blocks = [row for row in blocks if row["complete_block"]]
    positive_block_pct = (
        100.0 * sum(row["capacity_net_ticks"] > 0 for row in complete_blocks) / len(complete_blocks)
        if complete_blocks else 0.0
    )
    cost_2x = next(row for row in costs if row["cost_multiplier"] == 2.0)
    positive_days = capacity.groupby("session_id")["capacity_net_ticks"].sum().clip(lower=0)
    concentration = (
        100.0 * float(positive_days.max()) / float(positive_days.sum())
        if float(positive_days.sum()) > 0 else None
    )
    gates = {
        "tick_coverage": coverage_pct >= CONFIG["minimum_tick_coverage_pct"],
        "positive_execution": base["total_net_ticks"] > 0 and _pf_above_one(base["profit_factor"]),
        "both_halves": base["first_half_net_ticks"] > 0 and base["second_half_net_ticks"] > 0,
        "positive_blocks": positive_block_pct >= CONFIG["minimum_positive_block_rate_pct"],
        "block_deletion": bool(deletions) and all(row["remaining_net_ticks"] > 0 for row in deletions),
        "bootstrap_lower_bound": bootstrap_summary["mean_ticks_95pct_lower"] > 0,
        "permutation": permutation_summary["one_sided_p_value"] <= 0.05,
        "double_cost": cost_2x["total_net_ticks"] > 0 and _pf_above_one(cost_2x["profit_factor"]),
        "day_concentration": concentration is not None and concentration < CONFIG["maximum_profitable_day_share_pct"],
        "ambiguous_exclusion": (
            without_ambiguous_metrics["total_net_ticks"] > 0
            and _pf_above_one(without_ambiguous_metrics["profit_factor"])
        ),
    }
    passed = all(gates.values())
    comparison = {
        "opportunities": len(tick_frame),
        "covered_opportunities": int(tick_frame["tick_path_complete"].sum()),
        "coverage_pct": coverage_pct,
        "exit_reason_changes": int(tick_frame["exit_reason_changed"].sum()),
        "reward_changes": int((tick_frame["tick_net_ticks"] - tick_frame["original_net_ticks"]).abs().gt(1e-9).sum()),
        "original_ambiguous_same_bar": int(tick_frame["original_ambiguous_same_bar"].sum()),
        "ambiguous_resolved_target": int(((tick_frame["original_ambiguous_same_bar"]) & (tick_frame["tick_exit_reason"] == "target")).sum()),
        "ambiguous_resolved_stop": int(((tick_frame["original_ambiguous_same_bar"]) & (tick_frame["tick_exit_reason"] == "stop")).sum()),
        "tick_minus_original_paper_ticks": float((tick_frame["tick_net_ticks"] - tick_frame["original_net_ticks"]).sum()),
    }
    summary = _json_safe({
        "schema_version": SCHEMA_VERSION,
        "research_phase": "gc_opening_inventory_same_contract_robustness",
        "sequence": SEQUENCE,
        "tick_source": tick_meta,
        "comparison": comparison,
        "tick_capacity": base,
        "positive_complete_block_rate_pct": positive_block_pct,
        "largest_profitable_day_share_pct": concentration,
        "bootstrap": bootstrap_summary,
        "permutation": permutation_summary,
        "without_original_ambiguous": without_ambiguous_metrics,
        "cost_stress": costs,
        "gates": {"thresholds": CONFIG, "criteria": gates, "passed": passed},
        "result_label": (
            "GC_OBSERVATION_SURVIVES_SAME_CONTRACT_ROBUSTNESS"
            if passed else "GC_OBSERVATION_FRAGILE_OR_UNCONFIRMED"
        ),
        "independent_confirmation": False,
        "forward_paper_authorized": False,
    })
    safe = {
        "tick_rows": _json_safe(tick_rows),
        "capacity_rows": _json_safe(capacity_rows),
        "block_rows": _json_safe(blocks),
        "deletion_rows": _json_safe(deletions),
        "cost_rows": _json_safe(costs),
        "bootstrap_rows": _json_safe(bootstrap_rows),
        "permutation_rows": _json_safe(permutation_rows),
    }
    manifest: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "research_phase": "gc_opening_inventory_same_contract_robustness",
        "source_bindings": _json_safe(source_bindings),
        "configuration": {"payload": CONFIG, "sha256": _sha256_json(CONFIG)},
        "contracts": {
            "frozen_parent_policy": True,
            "tick_order_preserved": True,
            "same_contract_only": True,
            "independent_confirmation": False,
            "forward_paper_authorized": False,
        },
        "counts": {key: len(value) for key, value in safe.items()},
        "summary_sha256": _sha256_json(summary),
        **{f"{key}_sha256": _sha256_json(value) for key, value in safe.items()},
    }
    manifest["manifest_sha256"] = _sha256_json(manifest)
    return {"manifest": manifest, "summary": summary, **safe}


def write_gc_opening_inventory_robustness(output_dir: Path, result: Mapping[str, Any]) -> Dict[str, str]:
    root = Path(output_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    definitions = {
        "summary": ("gc_robustness_summary.json", "json", "summary"),
        "ticks": ("tick_opportunity_ledger.csv", "csv", "tick_rows"),
        "capacity": ("tick_capacity_ledger.csv", "csv", "capacity_rows"),
        "blocks": ("five_session_blocks.csv", "csv", "block_rows"),
        "deletions": ("leave_one_block_out.csv", "csv", "deletion_rows"),
        "costs": ("cost_stress.csv", "csv", "cost_rows"),
        "bootstrap": ("session_bootstrap.csv", "csv", "bootstrap_rows"),
        "permutation": ("session_permutation.csv", "csv", "permutation_rows"),
        "manifest": ("gc_robustness_manifest.json", "json", "manifest"),
    }
    paths = {name: root / value[0] for name, value in definitions.items()}
    if any(path.exists() for path in paths.values()):
        raise FileExistsError(f"refusing to overwrite GC robustness artifacts: {root}")
    for name, (_, kind, key) in definitions.items():
        if name == "manifest":
            continue
        if kind == "json":
            _write_json(paths[name], result[key])
        else:
            pd.DataFrame(result[key]).to_csv(paths[name], index=False)
    manifest = dict(result["manifest"])
    manifest.pop("manifest_sha256", None)
    manifest["artifacts"] = {
        name: {"filename": path.name, "bytes": path.stat().st_size, "sha256": sha256_file(path)}
        for name, path in paths.items() if name != "manifest"
    }
    manifest["manifest_sha256"] = _sha256_json(manifest)
    _write_json(paths["manifest"], manifest)
    return {name: str(path) for name, path in paths.items()}


def _canonical_opportunities(rows: Sequence[Mapping[str, Any]] | pd.DataFrame) -> pd.DataFrame:
    frame = rows.copy() if isinstance(rows, pd.DataFrame) else pd.DataFrame(rows)
    required = {
        "sequence", "physical_opportunity_id", "session_id", "session_index", "block_index",
        "complete_block", "signal_direction", "overnight_direction", "primary_mode",
        "continuation_reward_ticks", "reversion_reward_ticks", "paired_uplift_ticks",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise GCRobustnessAuditError("parent opportunities missing: " + ", ".join(missing))
    out = frame.loc[frame["sequence"].astype(str) == SEQUENCE].copy()
    if out.empty or out["physical_opportunity_id"].duplicated().any():
        raise GCRobustnessAuditError("GC parent opportunities missing or duplicated")
    for column in ("signal_direction", "overnight_direction", "session_index", "block_index"):
        out[column] = pd.to_numeric(out[column], errors="raise").astype(int)
    for column in ("continuation_reward_ticks", "reversion_reward_ticks", "paired_uplift_ticks"):
        out[column] = pd.to_numeric(out[column], errors="raise").astype(float)
    expected_mode = np.where(
        out["signal_direction"] == out["overnight_direction"], "continuation", "reversion"
    )
    if not np.array_equal(expected_mode, out["primary_mode"].astype(str).to_numpy()):
        raise GCRobustnessAuditError("parent acceptance mapping changed")
    return out.sort_values(["session_index", "physical_opportunity_id"]).reset_index(drop=True)


def _select_source_outcomes(opportunities: pd.DataFrame, rows: Sequence[Mapping[str, Any]] | pd.DataFrame) -> pd.DataFrame:
    cube = rows.copy() if isinstance(rows, pd.DataFrame) else pd.DataFrame(rows)
    required = {
        "sequence", "physical_opportunity_id", "mode", "entry_dt", "exit_dt", "exit_known_dt",
        "entry_price", "exit_price", "gross_pnl_ticks", "net_pnl_ticks", "exit_reason",
        "ambiguous_same_bar", "trade_direction", "round_trip_cost_ticks",
    }
    missing = sorted(required - set(cube.columns))
    if missing:
        raise GCRobustnessAuditError("source cube missing: " + ", ".join(missing))
    cube = cube.loc[cube["sequence"].astype(str) == SEQUENCE].copy()
    selected = opportunities.merge(
        cube,
        left_on=["physical_opportunity_id", "primary_mode"],
        right_on=["physical_opportunity_id", "mode"],
        how="left",
        validate="one_to_one",
        suffixes=("", "_cube"),
    )
    if selected["entry_dt"].isna().any() or len(selected) != len(opportunities):
        raise GCRobustnessAuditError("parent policy opportunities do not match source cube")
    for column in ("entry_dt", "exit_dt", "exit_known_dt"):
        selected[column] = pd.to_datetime(selected[column], errors="raise", utc=True)
    for column in ("entry_price", "exit_price", "gross_pnl_ticks", "net_pnl_ticks", "round_trip_cost_ticks"):
        selected[column] = pd.to_numeric(selected[column], errors="raise").astype(float)
    selected["trade_direction"] = pd.to_numeric(selected["trade_direction"], errors="raise").astype(int)
    selected["original_ambiguous_same_bar"] = selected["ambiguous_same_bar"].map(_as_bool)
    intrabar_exit = selected["exit_reason"].astype(str).isin(
        ["target", "stop", "ambiguous_stop_first"]
    )
    selected["tick_scan_end_dt"] = selected["exit_dt"] + pd.to_timedelta(
        intrabar_exit.astype(int), unit="min"
    )
    if not np.allclose(selected["round_trip_cost_ticks"], CONFIG["round_trip_cost_ticks"]):
        raise GCRobustnessAuditError("GC cost changed")
    return selected


def _load_relevant_ticks(path: Path, outcomes: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, Dict[str, Any]]:
    if not path.is_file():
        raise GCRobustnessAuditError(f"tick file missing: {path}")
    windows: Dict[int, tuple[int, int]] = {}
    for row in outcomes.itertuples():
        start = pd.Timestamp(row.entry_dt).tz_convert("UTC")
        end = pd.Timestamp(row.tick_scan_end_dt).tz_convert("UTC")
        if start.date() != end.date():
            raise GCRobustnessAuditError("GC tick horizon crosses UTC date")
        date_key = int(start.strftime("%Y%m%d"))
        start_second = start.hour * 3600 + start.minute * 60 + start.second
        end_second = end.hour * 3600 + end.minute * 60 + end.second
        current = windows.get(date_key)
        windows[date_key] = (
            min(start_second, current[0]) if current else start_second,
            max(end_second, current[1]) if current else end_second,
        )
    midnight_ns = {
        key: int(pd.Timestamp(str(key), tz="UTC").value) for key in windows
    }
    timestamps: List[int] = []
    prices: List[float] = []
    raw_rows = malformed = 0
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for raw_rows, line in enumerate(handle, start=1):
            try:
                timestamp_raw, last_s, _ = line.rstrip("\n").split(";", 2)
                date_s, time_s, frac_s = timestamp_raw.split()
                date_key = int(date_s)
                window = windows.get(date_key)
                if window is None:
                    continue
                hh = int(time_s[0:2]); mm = int(time_s[2:4]); ss = int(time_s[4:6])
                second = hh * 3600 + mm * 60 + ss
                if second < window[0] - 60 or second > window[1] + 60:
                    continue
                frac = ("".join(char for char in frac_s if char.isdigit()) or "0")[:6].ljust(6, "0")
                timestamps.append(midnight_ns[date_key] + second * 1_000_000_000 + int(frac) * 1000)
                prices.append(float(last_s))
            except Exception:
                malformed += 1
    ts = np.asarray(timestamps, dtype=np.int64)
    px = np.asarray(prices, dtype=float)
    if not len(ts) or malformed:
        raise GCRobustnessAuditError(f"tick parse failed: retained={len(ts)} malformed={malformed}")
    if np.any(ts[1:] < ts[:-1]):
        raise GCRobustnessAuditError("tick timestamps are not monotonic in file order")
    return ts, px, {
        "path": str(path.resolve()), "bytes": path.stat().st_size,
        "sha256": sha256_file(path), "raw_rows": raw_rows,
        "retained_rows": len(ts), "malformed_rows": malformed,
        "start": pd.Timestamp(ts[0], tz="UTC"), "end": pd.Timestamp(ts[-1], tz="UTC"),
    }


def _resolve_tick_outcomes(outcomes: pd.DataFrame, tick_ns: np.ndarray, prices: np.ndarray) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    tick_size = float(CONFIG["tick_size"])
    cost = float(CONFIG["round_trip_cost_ticks"])
    for row in outcomes.to_dict("records"):
        entry_ns = int(pd.Timestamp(row["entry_dt"]).value)
        horizon_ns = int(pd.Timestamp(row["tick_scan_end_dt"]).value)
        lo = int(np.searchsorted(tick_ns, entry_ns, side="left"))
        hi = int(np.searchsorted(tick_ns, horizon_ns, side="right"))
        complete = lo < hi and tick_ns[lo] <= entry_ns + 60_000_000_000
        first_target = first_stop = None
        if complete:
            path = prices[lo:hi]
            direction = int(row["trade_direction"])
            entry_price = float(row["entry_price"])
            target_price = entry_price + direction * float(CONFIG["target_ticks"]) * tick_size
            stop_price = entry_price - direction * float(CONFIG["stop_ticks"]) * tick_size
            target_hits = np.flatnonzero(path >= target_price) if direction == 1 else np.flatnonzero(path <= target_price)
            stop_hits = np.flatnonzero(path <= stop_price) if direction == 1 else np.flatnonzero(path >= stop_price)
            first_target = int(target_hits[0]) if len(target_hits) else None
            first_stop = int(stop_hits[0]) if len(stop_hits) else None
        hit_reason = None
        hit_offset = None
        if first_target is not None and (first_stop is None or first_target < first_stop):
            hit_reason, hit_offset = "target", first_target
        elif first_stop is not None:
            hit_reason, hit_offset = "stop", first_stop
        if hit_reason is None:
            complete = bool(complete and tick_ns[hi - 1] >= horizon_ns - 60_000_000_000)
            tick_reason = str(row["exit_reason"])
            tick_gross = float(row["gross_pnl_ticks"])
            tick_exit_dt = pd.Timestamp(row["exit_dt"])
            tick_exit_known_dt = pd.Timestamp(row["exit_known_dt"])
        else:
            tick_reason = hit_reason
            tick_gross = float(CONFIG["target_ticks"] if hit_reason == "target" else -CONFIG["stop_ticks"])
            tick_exit_dt = pd.Timestamp(tick_ns[lo + int(hit_offset)], tz="UTC")
            minute_ns = 60_000_000_000
            known_ns = ((int(tick_exit_dt.value) + minute_ns - 1) // minute_ns) * minute_ns
            tick_exit_known_dt = pd.Timestamp(known_ns, tz="UTC")
        tick_net = tick_gross - cost
        rows.append({
            "schema_version": SCHEMA_VERSION, "sequence": SEQUENCE,
            "physical_opportunity_id": row["physical_opportunity_id"],
            "session_id": row["session_id"], "session_index": int(row["session_index"]),
            "block_index": int(row["block_index"]), "complete_block": _as_bool(row["complete_block"]),
            "primary_mode": row["primary_mode"], "trade_direction": int(row["trade_direction"]),
            "entry_dt": row["entry_dt"], "entry_price": float(row["entry_price"]),
            "original_exit_dt": row["exit_dt"], "original_exit_reason": row["exit_reason"],
            "original_net_ticks": float(row["net_pnl_ticks"]),
            "original_ambiguous_same_bar": bool(row["original_ambiguous_same_bar"]),
            "tick_path_complete": complete, "tick_exit_dt": tick_exit_dt,
            "tick_exit_known_dt": tick_exit_known_dt, "tick_exit_reason": tick_reason,
            "tick_gross_ticks": tick_gross, "tick_net_ticks": tick_net,
            "exit_reason_changed": tick_reason != str(row["exit_reason"]),
        })
    return rows


def _apply_capacity(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame.assign(executed=False, capacity_skip=False, capacity_net_ticks=0.0)
    if frame["physical_opportunity_id"].duplicated().any():
        raise GCRobustnessAuditError("duplicate tick opportunity")
    active = {1: [], -1: []}
    rows: List[Dict[str, Any]] = []
    for row in frame.sort_values(["entry_dt", "physical_opportunity_id"]).to_dict("records"):
        direction = int(row["trade_direction"])
        entry = pd.Timestamp(row["entry_dt"])
        active[direction] = [value for value in active[direction] if value > entry]
        executed = len(active[direction]) < int(CONFIG["capacity_per_direction"])
        if executed:
            active[direction].append(pd.Timestamp(row["tick_exit_known_dt"]))
        rows.append({**row, "executed": executed, "capacity_skip": not executed,
                     "capacity_net_ticks": float(row["tick_net_ticks"]) if executed else 0.0})
    return pd.DataFrame(rows)


def _capacity_metrics(capacity: pd.DataFrame, cost: float) -> Dict[str, Any]:
    executed = capacity.loc[capacity["executed"]].copy()
    rewards = executed["tick_gross_ticks"].astype(float) - float(cost)
    gains = float(rewards[rewards > 0].sum()); losses = abs(float(rewards[rewards < 0].sum()))
    midpoint = (int(capacity["session_index"].max()) + 1) / 2.0 if len(capacity) else 0
    first = capacity.loc[capacity["session_index"] < midpoint]
    second = capacity.loc[capacity["session_index"] >= midpoint]
    return {
        "opportunities": len(capacity), "executed_trades": len(executed),
        "capacity_skips": int(capacity["capacity_skip"].sum()) if len(capacity) else 0,
        "total_net_ticks": float(rewards.sum()),
        "mean_net_ticks": float(rewards.mean()) if len(rewards) else None,
        "profit_factor": gains / losses if losses else None,
        "positive_rate_pct": 100.0 * float((rewards > 0).mean()) if len(rewards) else None,
        "first_half_net_ticks": float((first.loc[first["executed"], "tick_gross_ticks"] - cost).sum()),
        "second_half_net_ticks": float((second.loc[second["executed"], "tick_gross_ticks"] - cost).sum()),
    }


def _block_summaries(capacity: pd.DataFrame) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for block_index, group in capacity.groupby("block_index", sort=True):
        executed = group.loc[group["executed"]]
        net = float(executed["tick_net_ticks"].sum())
        rows.append({
            "schema_version": SCHEMA_VERSION, "block_index": int(block_index),
            "sessions": int(group["session_id"].nunique()),
            "complete_block": bool(group["complete_block"].all()),
            "opportunities": len(group), "executed_trades": len(executed),
            "capacity_net_ticks": net, "positive": net > 0,
        })
    return rows


def _block_deletions(capacity: pd.DataFrame, blocks: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    rows = []
    for block in blocks:
        if not block["complete_block"]:
            continue
        kept = capacity.loc[capacity["block_index"] != block["block_index"]]
        rows.append({
            "schema_version": SCHEMA_VERSION, "deleted_block_index": int(block["block_index"]),
            "deleted_net_ticks": float(block["capacity_net_ticks"]),
            "remaining_net_ticks": float(kept.loc[kept["executed"], "tick_net_ticks"].sum()),
        })
    return rows


def _cost_stress(capacity: pd.DataFrame) -> List[Dict[str, Any]]:
    rows = []
    for multiplier in CONFIG["cost_multipliers"]:
        cost = float(CONFIG["round_trip_cost_ticks"]) * float(multiplier)
        rows.append({"schema_version": SCHEMA_VERSION, "cost_multiplier": float(multiplier),
                     "round_trip_cost_ticks": cost, **_capacity_metrics(capacity, cost)})
    return rows


def _session_bootstrap(capacity: pd.DataFrame) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    executed = capacity.loc[capacity["executed"]]
    daily = executed.groupby("session_id").agg(gross=("tick_gross_ticks", "sum"), trades=("tick_gross_ticks", "size"))
    rng = np.random.default_rng(int(CONFIG["random_seed"]))
    values: List[float] = []
    for draw in range(int(CONFIG["bootstrap_draws"])):
        indexes = rng.integers(0, len(daily), size=len(daily))
        sample = daily.iloc[indexes]
        mean = float((sample["gross"].sum() - CONFIG["round_trip_cost_ticks"] * sample["trades"].sum()) / sample["trades"].sum())
        values.append(mean)
    array = np.asarray(values)
    rows = [{"schema_version": SCHEMA_VERSION, "draw": index, "mean_net_ticks": value} for index, value in enumerate(values)]
    return rows, {
        "draws": len(values), "sessions": len(daily),
        "mean_ticks_95pct_lower": float(np.quantile(array, 0.025)),
        "mean_ticks_95pct_upper": float(np.quantile(array, 0.975)),
        "probability_positive_pct": 100.0 * float((array > 0).mean()),
    }


def _session_permutation(opportunities: pd.DataFrame) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    sessions = sorted(opportunities["session_id"].astype(str).unique())
    index = {value: position for position, value in enumerate(sessions)}
    session_direction = opportunities.drop_duplicates("session_id").set_index("session_id")["overnight_direction"]
    directions = np.asarray([int(session_direction.loc[value]) for value in sessions])
    codes = opportunities["session_id"].astype(str).map(index).to_numpy()
    signal = opportunities["signal_direction"].to_numpy(dtype=int)
    cont = opportunities["continuation_reward_ticks"].to_numpy(dtype=float)
    rev = opportunities["reversion_reward_ticks"].to_numpy(dtype=float)
    observed = float(np.where(signal == directions[codes], cont - rev, rev - cont).mean())
    rng = np.random.default_rng(int(CONFIG["random_seed"]))
    null: List[float] = []
    for _ in range(int(CONFIG["permutation_draws"])):
        shuffled = rng.permutation(directions)
        null.append(float(np.where(signal == shuffled[codes], cont - rev, rev - cont).mean()))
    array = np.asarray(null)
    p_value = float((1 + np.sum(array >= observed)) / (len(array) + 1))
    rows = [{"schema_version": SCHEMA_VERSION, "draw": index, "paired_uplift_ticks": value} for index, value in enumerate(null)]
    return rows, {
        "draws": len(null), "sessions": len(sessions), "observed_paired_uplift_ticks": observed,
        "null_mean_ticks": float(array.mean()), "null_95pct_upper_ticks": float(np.quantile(array, 0.95)),
        "one_sided_p_value": p_value,
    }


def _pf_above_one(value: Any) -> bool:
    return value is not None and float(value) > 1.0


def _as_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}
    return bool(value)


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(_json_safe(payload), indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def _sha256_json(value: Any) -> str:
    encoded = json.dumps(_json_safe(value), sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, np.generic):
        return _json_safe(value.item())
    if isinstance(value, float) and not isfinite(value):
        return None
    if value is pd.NA or value is pd.NaT:
        return None
    return value
