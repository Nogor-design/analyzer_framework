from __future__ import annotations

"""Factorial threshold/lookback effects and lag-one market-shift audit."""

import hashlib
import json
from math import isfinite
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

import numpy as np
import pandas as pd

from ta_foundation.core.manifest import sha256_file


SCHEMA_VERSION = "dynamic_factorial_shift_audit.v1"
DISCOVERY_SEQUENCES = [
    "NQ 03-26", "NQ 06-26", "NQ 09-26", "ES 03-26",
    "RTY 03-26", "YM 03-26", "YM 06-26",
]
CONFIRMATION_SEQUENCES = ["GC 04-26", "NG 03-26"]
SENSITIVITY_SEQUENCES = ["MNQ 03-26"]

DEFAULT_CONFIG: Dict[str, Any] = {
    "timeframes": [1, 2, 3, 5],
    "lookbacks": [5, 10, 20],
    "multipliers": [1.5, 1.6, 1.7],
    "modes": ["continuation", "reversion"],
    "block_sessions": 5,
    "capacity_per_direction": 3,
    "factor_material_ticks": 2.5,
    "factor_same_sign_sequences": 7,
}

DEFAULT_STATIC_GATES: Dict[str, Any] = {
    "minimum_outcomes_each_discovery_sequence": 50,
    "minimum_sessions_each_discovery_sequence": 20,
    "minimum_positive_discovery_sequences": 5,
    "minimum_discovery_mean_ticks": 2.5,
    "minimum_positive_block_rate_pct": 55.0,
    "minimum_positive_capacity_discovery_sequences": 5,
    "maximum_positive_capacity_sequence_share_pct": 50.0,
}

DEFAULT_SHIFT_GATES: Dict[str, Any] = {
    "minimum_transitions_each_sequence": 5,
    "minimum_positive_ic_sequences": 7,
    "minimum_transition_weighted_ic": 0.10,
    "minimum_uplift_sequences": 7,
    "minimum_sequence_uplift_ticks": 2.5,
    "minimum_cross_sequence_uplift_ticks": 2.5,
    "maximum_positive_uplift_sequence_share_pct": 50.0,
}


class FactorialShiftAuditError(ValueError):
    pass


def run_factorial_shift_audit(
    sequence_rows: Mapping[str, Sequence[Mapping[str, Any]] | pd.DataFrame],
    *,
    source_manifests: Optional[Mapping[str, Mapping[str, Any]]] = None,
) -> Dict[str, Any]:
    expected_sequences = set(
        DISCOVERY_SEQUENCES + CONFIRMATION_SEQUENCES + SENSITIVITY_SEQUENCES
    )
    if set(sequence_rows) != expected_sequences:
        raise FactorialShiftAuditError("factorial audit requires the frozen ten sequences")
    frames = []
    for sequence, rows in sequence_rows.items():
        frame = _canonical_rows(rows)
        if frame["sequence"].unique().tolist() != [sequence]:
            raise FactorialShiftAuditError(f"sequence mismatch for {sequence}")
        frames.append(frame)
    frame = pd.concat(frames, ignore_index=True)

    exact_rows, block_rows = _exact_and_block_summaries(frame)
    exact = pd.DataFrame(exact_rows)
    blocks = pd.DataFrame(block_rows)
    capacity_rows, capacity_summary_rows = _capacity_audit(frame)
    capacity_summary = pd.DataFrame(capacity_summary_rows)
    exact = exact.merge(
        capacity_summary,
        on=["sequence", "cell_id", "timeframe", "lookback", "multiplier", "mode"],
        validate="one_to_one",
    )
    marginal_rows, contrast_rows, contrast_panel = _factor_effects(exact)
    transition_rows, shift_sequence_rows, shift_summary = _shift_persistence(blocks)
    candidate_rows = _static_candidates(exact, blocks)
    passing_candidates = [row for row in candidate_rows if row["passed"]]
    edge_present = bool(passing_candidates) or bool(shift_summary["gates"]["passed"])
    summary = _json_safe({
        "schema_version": SCHEMA_VERSION,
        "research_phase": "dynamic_phase_7",
        "panel": {
            "sequences": 10,
            "outcome_rows": len(frame),
            "exact_cell_sequence_rows": len(exact_rows),
            "five_session_block_rows": len(block_rows),
            "capacity_rows": len(capacity_rows),
            "transition_rows": len(transition_rows),
            "material_factor_contrasts": sum(row["material_effect"] for row in contrast_panel),
            "passing_static_cells": len(passing_candidates),
        },
        "contrast_panel": contrast_panel,
        "static_candidates": candidate_rows,
        "shift_persistence": shift_summary,
        "result_label": (
            "ROBUST_PARAMETER_EDGE_PRESENT" if edge_present
            else "NO_ROBUST_PARAMETER_EDGE"
        ),
        "static_confirmation_authorized": bool(passing_candidates),
        "adaptive_replay_authorized": bool(shift_summary["gates"]["passed"]),
        "family_b_authorized": False,
        "forward_paper_authorized": False,
    })
    safe = {
        "exact_rows": _json_safe(exact.to_dict("records")),
        "marginal_rows": _json_safe(marginal_rows),
        "contrast_rows": _json_safe(contrast_rows),
        "block_rows": _json_safe(block_rows),
        "transition_rows": _json_safe(transition_rows),
        "shift_sequence_rows": _json_safe(shift_sequence_rows),
        "capacity_rows": _json_safe(capacity_rows),
        "candidate_rows": _json_safe(candidate_rows),
    }
    sources = {
        sequence: {
            "manifest_sha256": manifest.get("manifest_sha256"),
            "outcome_cube_sha256": manifest.get("outcome_cube_sha256"),
        }
        for sequence, manifest in (source_manifests or {}).items()
    }
    manifest: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "research_phase": "dynamic_phase_7",
        "source_outcome_cubes": sources,
        "configuration": {"payload": DEFAULT_CONFIG, "sha256": _sha256_json(DEFAULT_CONFIG)},
        "static_gates": {"payload": DEFAULT_STATIC_GATES, "sha256": _sha256_json(DEFAULT_STATIC_GATES)},
        "shift_gates": {"payload": DEFAULT_SHIFT_GATES, "sha256": _sha256_json(DEFAULT_SHIFT_GATES)},
        "contracts": {
            "all_cells_reported": True,
            "lag_one_blocks": True,
            "sequence_state_reset": True,
            "capacity_per_cell": True,
            "family_b_authorized": False,
            "forward_paper_authorized": False,
        },
        "counts": {key: len(value) for key, value in safe.items()},
        "summary_sha256": _sha256_json(summary),
        **{f"{key}_sha256": _sha256_json(value) for key, value in safe.items()},
    }
    manifest["manifest_sha256"] = _sha256_json(manifest)
    return {"manifest": manifest, "summary": summary, **safe}


def write_factorial_shift_audit(output_dir: Path, result: Mapping[str, Any]) -> Dict[str, str]:
    root = Path(output_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    definitions = {
        "summary": ("factorial_shift_summary.json", "json", "summary"),
        "exact_cells": ("exact_cell_summary.csv", "csv", "exact_rows"),
        "marginals": ("lookback_multiplier_marginals.csv", "csv", "marginal_rows"),
        "contrasts": ("adjacent_factor_contrasts.csv", "csv", "contrast_rows"),
        "blocks": ("five_session_blocks.csv", "csv", "block_rows"),
        "transitions": ("lag_one_transitions.csv", "csv", "transition_rows"),
        "shift_sequences": ("shift_sequence_summary.csv", "csv", "shift_sequence_rows"),
        "capacity": ("capacity_ledger.csv", "csv", "capacity_rows"),
        "candidates": ("static_edge_candidates.csv", "csv", "candidate_rows"),
        "manifest": ("factorial_shift_manifest.json", "json", "manifest"),
    }
    paths = {name: root / spec[0] for name, spec in definitions.items()}
    if any(path.exists() for path in paths.values()):
        raise FileExistsError(f"refusing to overwrite factorial artifacts: {root}")
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


def _canonical_rows(rows: Sequence[Mapping[str, Any]] | pd.DataFrame) -> pd.DataFrame:
    frame = rows.copy() if isinstance(rows, pd.DataFrame) else pd.DataFrame(rows)
    required = {
        "sequence", "lane_event_id", "physical_opportunity_id", "session_id",
        "timeframe", "lookback", "multiplier", "mode", "signal_dt", "entry_dt",
        "exit_known_dt", "trade_direction", "net_pnl_ticks",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise FactorialShiftAuditError("factorial cube missing: " + ", ".join(missing))
    out = frame.loc[:, sorted(required)].copy()
    for column in ("signal_dt", "entry_dt", "exit_known_dt"):
        out[column] = pd.to_datetime(out[column], errors="raise", utc=True).dt.tz_convert("America/Denver")
    for column in ("timeframe", "lookback", "trade_direction"):
        out[column] = pd.to_numeric(out[column], errors="raise").astype(int)
    for column in ("multiplier", "net_pnl_ticks"):
        out[column] = pd.to_numeric(out[column], errors="raise").astype(float)
    if not np.isfinite(out[["multiplier", "net_pnl_ticks"]].to_numpy()).all():
        raise FactorialShiftAuditError("factorial numeric inputs must be finite")
    if out.duplicated(["sequence", "lane_event_id", "mode"]).any():
        raise FactorialShiftAuditError("duplicate lane-event mode outcome")
    expected = set(DEFAULT_CONFIG["multipliers"])
    if set(out["multiplier"].unique()) != expected:
        raise FactorialShiftAuditError("factorial multiplier catalog changed")
    out["cell_id"] = out.apply(
        lambda row: (
            f"tf{int(row['timeframe'])}|lb{int(row['lookback'])}|"
            f"x{float(row['multiplier']):.1f}|{row['mode']}"
        ),
        axis=1,
    )
    first = out.groupby(["sequence", "session_id"])["signal_dt"].min().reset_index()
    first = first.sort_values(["sequence", "signal_dt"])
    first["session_index"] = first.groupby("sequence").cumcount()
    first["block_index"] = first["session_index"] // int(DEFAULT_CONFIG["block_sessions"])
    counts = first.groupby(["sequence", "block_index"])["session_id"].transform("nunique")
    first["complete_block"] = counts == int(DEFAULT_CONFIG["block_sessions"])
    out = out.merge(first[["sequence", "session_id", "session_index", "block_index", "complete_block"]], on=["sequence", "session_id"], validate="many_to_one")
    return out.sort_values(["sequence", "signal_dt", "cell_id", "lane_event_id"]).reset_index(drop=True)


def _metrics(group: pd.DataFrame) -> Dict[str, Any]:
    pnl = group["net_pnl_ticks"].astype(float)
    gains = float(pnl[pnl > 0].sum())
    losses = abs(float(pnl[pnl < 0].sum()))
    equity = pnl.cumsum()
    drawdown = equity.cummax().clip(lower=0) - equity
    return {
        "outcomes": len(group),
        "sessions": int(group["session_id"].nunique()),
        "mean_net_ticks": float(pnl.mean()),
        "total_net_ticks": float(pnl.sum()),
        "positive_rate_pct": 100.0 * float((pnl > 0).mean()),
        "profit_factor": gains / losses if losses else None,
        "max_drawdown_ticks": float(drawdown.max()) if len(drawdown) else 0.0,
    }


def _exact_and_block_summaries(frame: pd.DataFrame) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    exact_rows: List[Dict[str, Any]] = []
    block_rows: List[Dict[str, Any]] = []
    keys = ["sequence", "cell_id", "timeframe", "lookback", "multiplier", "mode"]
    for key, group in frame.groupby(keys, sort=True):
        sequence, cell_id, timeframe, lookback, multiplier, mode = key
        midpoint = (int(group["session_index"].max()) + 1) / 2.0
        first = group.loc[group["session_index"] < midpoint]
        second = group.loc[group["session_index"] >= midpoint]
        complete_blocks = group.loc[group["complete_block"]]
        block_means = complete_blocks.groupby("block_index")["net_pnl_ticks"].mean()
        exact_rows.append({
            "schema_version": SCHEMA_VERSION, "sequence": sequence, "cell_id": cell_id,
            "timeframe": int(timeframe), "lookback": int(lookback), "multiplier": float(multiplier), "mode": mode,
            **_metrics(group),
            "first_half_mean_net_ticks": float(first["net_pnl_ticks"].mean()) if len(first) else None,
            "second_half_mean_net_ticks": float(second["net_pnl_ticks"].mean()) if len(second) else None,
            "complete_blocks": len(block_means),
            "positive_block_rate_pct": 100.0 * float((block_means > 0).mean()) if len(block_means) else None,
            "block_sign_changes": int((np.sign(block_means).diff().fillna(0) != 0).sum()) if len(block_means) else 0,
        })
        for block_index, block in complete_blocks.groupby("block_index", sort=True):
            block_rows.append({
                "schema_version": SCHEMA_VERSION, "sequence": sequence, "block_index": int(block_index),
                "cell_id": cell_id, "timeframe": int(timeframe), "lookback": int(lookback),
                "multiplier": float(multiplier), "mode": mode, **_metrics(block),
            })
    return exact_rows, block_rows


def _capacity_audit(frame: pd.DataFrame) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    ledger: List[Dict[str, Any]] = []
    summaries: List[Dict[str, Any]] = []
    keys = ["sequence", "cell_id", "timeframe", "lookback", "multiplier", "mode"]
    for key, group in frame.groupby(keys, sort=True):
        if group["physical_opportunity_id"].duplicated().any():
            raise FactorialShiftAuditError(f"duplicate physical opportunity in cell {key}")
        active = {1: [], -1: []}
        rewards: List[float] = []
        cumulative = peak = maxdd = 0.0
        skips = 0
        for row in group.sort_values(["entry_dt", "physical_opportunity_id"]).to_dict("records"):
            direction = int(row["trade_direction"])
            entry = pd.Timestamp(row["entry_dt"])
            active[direction] = [value for value in active[direction] if value > entry]
            executed = len(active[direction]) < int(DEFAULT_CONFIG["capacity_per_direction"])
            reward = float(row["net_pnl_ticks"]) if executed else 0.0
            if executed:
                active[direction].append(pd.Timestamp(row["exit_known_dt"]))
                rewards.append(reward)
                cumulative += reward
                peak = max(peak, cumulative)
                maxdd = max(maxdd, peak - cumulative)
            else:
                skips += 1
            ledger.append({
                "schema_version": SCHEMA_VERSION, "sequence": key[0], "cell_id": key[1],
                "physical_opportunity_id": row["physical_opportunity_id"], "entry_dt": entry,
                "trade_direction": direction, "executed": executed, "capacity_skip": not executed,
                "net_ticks": reward, "cumulative_net_ticks": cumulative,
            })
        rewards_array = np.asarray(rewards, dtype=float)
        gains = float(rewards_array[rewards_array > 0].sum()) if len(rewards_array) else 0.0
        losses = abs(float(rewards_array[rewards_array < 0].sum())) if len(rewards_array) else 0.0
        summaries.append({
            "sequence": key[0], "cell_id": key[1], "timeframe": int(key[2]), "lookback": int(key[3]),
            "multiplier": float(key[4]), "mode": key[5], "capacity_trades": len(rewards),
            "capacity_skips": skips, "capacity_net_ticks": float(rewards_array.sum()) if len(rewards_array) else 0.0,
            "capacity_mean_net_ticks": float(rewards_array.mean()) if len(rewards_array) else None,
            "capacity_profit_factor": gains / losses if losses else None, "capacity_max_drawdown_ticks": maxdd,
        })
    return ledger, summaries


def _factor_effects(exact: pd.DataFrame) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    marginal: List[Dict[str, Any]] = []
    for (sequence, lookback, multiplier), group in exact.groupby(["sequence", "lookback", "multiplier"], sort=True):
        marginal.append({
            "schema_version": SCHEMA_VERSION, "sequence": sequence, "lookback": int(lookback),
            "multiplier": float(multiplier), "exact_cells": len(group),
            "equal_cell_mean_net_ticks": float(group["mean_net_ticks"].mean()),
            "total_outcomes": int(group["outcomes"].sum()),
            "equal_cell_capacity_mean_ticks": float(group["capacity_mean_net_ticks"].mean()),
        })
    marginal_frame = pd.DataFrame(marginal)
    contrasts: List[Dict[str, Any]] = []
    specs = [
        ("multiplier", 1.5, 1.6, "1.60-1.50"), ("multiplier", 1.6, 1.7, "1.70-1.60"),
        ("lookback", 5, 10, "10-5"), ("lookback", 10, 20, "20-10"),
    ]
    for factor, low, high, label in specs:
        other = "lookback" if factor == "multiplier" else "multiplier"
        for (sequence, other_value), group in marginal_frame.groupby(["sequence", other], sort=True):
            low_rows = group.loc[group[factor] == low]
            high_rows = group.loc[group[factor] == high]
            if len(low_rows) != 1 or len(high_rows) != 1:
                continue
            contrasts.append({
                "schema_version": SCHEMA_VERSION, "sequence": sequence, "factor": factor,
                "contrast": label, other: other_value,
                "delta_mean_net_ticks": float(high_rows.iloc[0]["equal_cell_mean_net_ticks"] - low_rows.iloc[0]["equal_cell_mean_net_ticks"]),
                "delta_outcomes": int(high_rows.iloc[0]["total_outcomes"] - low_rows.iloc[0]["total_outcomes"]),
                "delta_capacity_mean_ticks": float(high_rows.iloc[0]["equal_cell_capacity_mean_ticks"] - low_rows.iloc[0]["equal_cell_capacity_mean_ticks"]),
            })
    contrast_frame = pd.DataFrame(contrasts)
    panel: List[Dict[str, Any]] = []
    group_fields = ["factor", "contrast", "lookback"] if "lookback" in contrast_frame.columns else []
    # Normalize the sparse other-factor columns into one label for stable grouping.
    contrast_frame["held_factor"] = np.where(
        contrast_frame["factor"] == "multiplier",
        "lb=" + contrast_frame["lookback"].fillna(0).astype(float).astype(int).astype(str),
        "x=" + contrast_frame["multiplier"].fillna(0).map(lambda value: f"{value:.1f}"),
    )
    for key, group in contrast_frame.groupby(["factor", "contrast", "held_factor"], sort=True):
        values = group["delta_mean_net_ticks"].astype(float)
        same_sign = max(int((values > 0).sum()), int((values < 0).sum()))
        mean_delta = float(values.mean())
        panel.append({
            "schema_version": SCHEMA_VERSION, "factor": key[0], "contrast": key[1], "held_factor": key[2],
            "sequences": len(group), "sequence_equal_delta_ticks": mean_delta,
            "positive_sequences": int((values > 0).sum()), "negative_sequences": int((values < 0).sum()),
            "same_sign_sequences": same_sign,
            "material_effect": abs(mean_delta) >= float(DEFAULT_CONFIG["factor_material_ticks"]) and same_sign >= int(DEFAULT_CONFIG["factor_same_sign_sequences"]),
        })
    return marginal, _json_safe(contrast_frame.to_dict("records")), panel


def _shift_persistence(blocks: pd.DataFrame) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    transitions: List[Dict[str, Any]] = []
    for (sequence, timeframe, mode), group in blocks.groupby(["sequence", "timeframe", "mode"], sort=True):
        piv = group.pivot(index="block_index", columns=["lookback", "multiplier"], values="mean_net_ticks").sort_index()
        complete = piv.dropna()
        for current_index in complete.index:
            previous_index = current_index - 1
            if previous_index not in complete.index:
                continue
            previous = complete.loc[previous_index]
            current = complete.loc[current_index]
            ic = previous.corr(current, method="spearman")
            selected = previous.sort_values(ascending=False).index[0]
            current_best = current.sort_values(ascending=False).index[0]
            transitions.append({
                "schema_version": SCHEMA_VERSION, "sequence": sequence, "timeframe": int(timeframe), "mode": mode,
                "prior_block_index": int(previous_index), "current_block_index": int(current_index),
                "rank_ic": float(ic), "prior_top_lookback": int(selected[0]), "prior_top_multiplier": float(selected[1]),
                "prior_top_current_reward_ticks": float(current.loc[selected]), "current_all_mean_ticks": float(current.mean()),
                "top_cell_uplift_ticks": float(current.loc[selected] - current.mean()),
                "current_best_lookback": int(current_best[0]), "current_best_multiplier": float(current_best[1]),
                "top_identity_changed": selected != current_best,
                "hindsight_best_reward_ticks": float(current.max()),
            })
    frame = pd.DataFrame(transitions)
    sequence_rows: List[Dict[str, Any]] = []
    for sequence, group in frame.groupby("sequence", sort=True):
        sequence_rows.append({
            "schema_version": SCHEMA_VERSION, "sequence": sequence, "transitions": len(group),
            "mean_rank_ic": float(group["rank_ic"].mean()), "mean_top_cell_uplift_ticks": float(group["top_cell_uplift_ticks"].mean()),
            "positive_ic_pct": 100.0 * float((group["rank_ic"] > 0).mean()),
            "positive_uplift_pct": 100.0 * float((group["top_cell_uplift_ticks"] > 0).mean()),
            "top_identity_change_pct": 100.0 * float(group["top_identity_changed"].mean()),
            "hindsight_best_uplift_over_all_ticks": float((group["hindsight_best_reward_ticks"] - group["current_all_mean_ticks"]).mean()),
        })
    positive_contrib = [max(0.0, row["mean_top_cell_uplift_ticks"] * row["transitions"]) for row in sequence_rows]
    total_contrib = sum(positive_contrib)
    largest_share = 100.0 * max(positive_contrib) / total_contrib if total_contrib > 0 else None
    total_transitions = sum(row["transitions"] for row in sequence_rows)
    weighted_ic = sum(row["mean_rank_ic"] * row["transitions"] for row in sequence_rows) / total_transitions
    cross_uplift = sum(row["mean_top_cell_uplift_ticks"] * row["transitions"] for row in sequence_rows) / total_transitions
    gates = {
        "minimum_transitions": all(row["transitions"] >= DEFAULT_SHIFT_GATES["minimum_transitions_each_sequence"] for row in sequence_rows),
        "positive_ic_sequences": sum(row["mean_rank_ic"] > 0 for row in sequence_rows) >= DEFAULT_SHIFT_GATES["minimum_positive_ic_sequences"],
        "weighted_rank_ic": weighted_ic >= DEFAULT_SHIFT_GATES["minimum_transition_weighted_ic"],
        "uplift_sequences": sum(row["mean_top_cell_uplift_ticks"] >= DEFAULT_SHIFT_GATES["minimum_sequence_uplift_ticks"] for row in sequence_rows) >= DEFAULT_SHIFT_GATES["minimum_uplift_sequences"],
        "cross_sequence_uplift": cross_uplift >= DEFAULT_SHIFT_GATES["minimum_cross_sequence_uplift_ticks"],
        "uplift_not_concentrated": largest_share is not None and largest_share < DEFAULT_SHIFT_GATES["maximum_positive_uplift_sequence_share_pct"],
    }
    return transitions, sequence_rows, _json_safe({
        "sequence_summaries": sequence_rows,
        "panel": {"transitions": total_transitions, "transition_weighted_rank_ic": weighted_ic, "transition_weighted_uplift_ticks": cross_uplift, "largest_positive_uplift_sequence_share_pct": largest_share},
        "gates": {"thresholds": DEFAULT_SHIFT_GATES, "criteria": gates, "passed": all(gates.values())},
        "result_label": "CAUSAL_SHIFT_SIGNAL_PRESENT" if all(gates.values()) else "SHIFT_VISIBLE_ONLY_IN_HINDSIGHT_OR_UNSTABLE",
    })


def _static_candidates(exact: pd.DataFrame, blocks: pd.DataFrame) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    cell_fields = ["cell_id", "timeframe", "lookback", "multiplier", "mode"]
    for key, group in exact.groupby(cell_fields, sort=True):
        by_sequence = {row["sequence"]: row for row in group.to_dict("records")}
        if not set(DISCOVERY_SEQUENCES + CONFIRMATION_SEQUENCES).issubset(by_sequence):
            continue
        discovery = [by_sequence[name] for name in DISCOVERY_SEQUENCES]
        confirmation = [by_sequence[name] for name in CONFIRMATION_SEQUENCES]
        positive_capacity = [max(0.0, float(row["capacity_net_ticks"])) for row in discovery]
        positive_total = sum(positive_capacity)
        concentration = 100.0 * max(positive_capacity) / positive_total if positive_total > 0 else None
        cell_blocks = blocks.loc[(blocks["cell_id"] == key[0]) & blocks["sequence"].isin(DISCOVERY_SEQUENCES)]
        criteria = {
            "coverage": all(row["outcomes"] >= DEFAULT_STATIC_GATES["minimum_outcomes_each_discovery_sequence"] and row["sessions"] >= DEFAULT_STATIC_GATES["minimum_sessions_each_discovery_sequence"] for row in discovery),
            "positive_discovery_sequences": sum(row["mean_net_ticks"] > 0 for row in discovery) >= DEFAULT_STATIC_GATES["minimum_positive_discovery_sequences"],
            "discovery_mean": float(np.mean([row["mean_net_ticks"] for row in discovery])) >= DEFAULT_STATIC_GATES["minimum_discovery_mean_ticks"],
            "both_halves_positive": float(np.mean([row["first_half_mean_net_ticks"] for row in discovery])) > 0 and float(np.mean([row["second_half_mean_net_ticks"] for row in discovery])) > 0,
            "positive_blocks": 100.0 * float((cell_blocks["mean_net_ticks"] > 0).mean()) >= DEFAULT_STATIC_GATES["minimum_positive_block_rate_pct"],
            "capacity_discovery": sum(row["capacity_net_ticks"] > 0 and row["capacity_profit_factor"] is not None and row["capacity_profit_factor"] > 1 for row in discovery) >= DEFAULT_STATIC_GATES["minimum_positive_capacity_discovery_sequences"],
            "gc_ng_confirmation": all(row["outcomes"] >= 50 and row["mean_net_ticks"] > 0 and row["capacity_net_ticks"] > 0 and row["capacity_profit_factor"] is not None and row["capacity_profit_factor"] > 1 for row in confirmation),
            "capacity_not_concentrated": concentration is not None and concentration < DEFAULT_STATIC_GATES["maximum_positive_capacity_sequence_share_pct"],
        }
        candidates.append({
            "schema_version": SCHEMA_VERSION, "cell_id": key[0], "timeframe": int(key[1]), "lookback": int(key[2]), "multiplier": float(key[3]), "mode": key[4],
            "discovery_sequence_equal_mean_ticks": float(np.mean([row["mean_net_ticks"] for row in discovery])),
            "positive_discovery_sequences": sum(row["mean_net_ticks"] > 0 for row in discovery),
            "positive_capacity_discovery_sequences": sum(row["capacity_net_ticks"] > 0 for row in discovery),
            "discovery_positive_block_rate_pct": 100.0 * float((cell_blocks["mean_net_ticks"] > 0).mean()),
            "gc_mean_ticks": float(by_sequence["GC 04-26"]["mean_net_ticks"]), "ng_mean_ticks": float(by_sequence["NG 03-26"]["mean_net_ticks"]),
            "gc_capacity_net_ticks": float(by_sequence["GC 04-26"]["capacity_net_ticks"]), "ng_capacity_net_ticks": float(by_sequence["NG 03-26"]["capacity_net_ticks"]),
            "mnq_mean_ticks": float(by_sequence["MNQ 03-26"]["mean_net_ticks"]) if "MNQ 03-26" in by_sequence else None,
            "largest_positive_capacity_sequence_share_pct": concentration,
            "criteria": criteria, "passed": all(criteria.values()),
        })
    return _json_safe(candidates)


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(_json_safe(payload), indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(json.dumps(_json_safe(value), sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


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
    if value is pd.NA:
        return None
    return value
