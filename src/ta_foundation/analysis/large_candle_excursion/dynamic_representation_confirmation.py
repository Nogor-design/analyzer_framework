from __future__ import annotations

"""Independent confirmation of the frozen Phase 5 representation block."""

import hashlib
import json
from math import isfinite
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

import numpy as np
import pandas as pd

from ta_foundation.analysis.large_candle_excursion.dynamic_representation_audit import (
    DYNAMIC_REPRESENTATION_SCHEMA_VERSION,
    _canonical_physical_rows,
    _fit_weighted_ridge,
    _json_safe,
)
from ta_foundation.core.manifest import sha256_file


REPRESENTATION_CONFIRMATION_SCHEMA_VERSION = (
    "dynamic_representation_confirmation.v1"
)

DEFAULT_CONFIRMATION_CONFIG: Dict[str, Any] = {
    "development_manifest_sha256": (
        "43ddeb494d1d166925bebfbb4804a73045faa2e9144f90bb5464928b343e97ba"
    ),
    "component_representations": {
        "trend_state": ["trend_state_aligned"],
        "directional_momentum": [
            "return_60m_aligned",
            "vwap_slope_15_aligned",
        ],
        "vwap_location": [
            "close_vs_vwap_aligned",
            "close_vs_vwap_abs",
        ],
    },
    "ridge_penalty": 1.0,
    "training_sequence_weight": "equal_total_weight",
    "component_score_standardization": (
        "full_development_prediction_mean_population_std"
    ),
    "combination": "equal_weight_mean_of_three_components",
    "mode_rule": "combined_score >= 0 continuation else reversion",
    "canonical_lane_rule": [
        "latest_signal_dt",
        "lowest_numeric_timeframe",
        "lexical_lane_id",
    ],
    "maximum_concurrent_per_direction": 3,
}

DEFAULT_CONFIRMATION_GATES: Dict[str, Any] = {
    "minimum_sessions_each_sequence": 20,
    "minimum_opportunities_each_sequence": 500,
    "minimum_rank_ic_each_sequence": 0.0,
    "minimum_equal_sequence_rank_ic": 0.05,
    "minimum_matched_uplift_ticks_each_sequence": 2.5,
    "minimum_equal_mode_uplift_ticks_each_sequence": 2.5,
    "minimum_independent_execution_profit_factor": 1.0,
    "minimum_independent_static_improvement_ticks_per_trade": 2.5,
    "minimum_pooled_mode_selection_rate_pct": 10.0,
    "maximum_gc_ng_positive_improvement_share_pct": 70.0,
}

PHYSICAL_COLUMNS = (
    "schema_version",
    "sequence",
    "instrument",
    "physical_opportunity_id",
    "session_id",
    "signal_dt",
    "entry_dt",
    "signal_side",
    "canonical_lane_id",
    "canonical_lane_event_id",
    "canonical_timeframe",
    "continuation_net_ticks",
    "continuation_exit_known_dt",
    "continuation_trade_direction",
    "reversion_net_ticks",
    "reversion_exit_known_dt",
    "reversion_trade_direction",
    "continuation_baseline_ticks",
    "reversion_baseline_ticks",
    "continuation_residual_ticks",
    "reversion_residual_ticks",
    "paired_advantage_ticks",
    "matched_advantage_ticks",
    "trend_state_aligned",
    "return_60m_aligned",
    "vwap_slope_15_aligned",
    "close_vs_vwap_aligned",
    "close_vs_vwap_abs",
    "max_signal_ratio",
    "signal_lane_fraction",
    "latched_any",
    "zone_break_fraction",
    "clock_fraction",
    "clock_fraction_sq",
)

DECISION_COLUMNS = (
    "schema_version",
    "sequence",
    "instrument",
    "physical_opportunity_id",
    "session_id",
    "entry_dt",
    "signal_side",
    "trend_state_score",
    "directional_momentum_score",
    "vwap_location_score",
    "combined_score",
    "selected_mode",
    "selected_net_ticks",
    "equal_mode_baseline_ticks",
    "equal_mode_uplift_ticks",
    "selected_matched_residual_ticks",
    "paired_advantage_ticks",
)

EXECUTION_COLUMNS = (
    "schema_version",
    "sequence",
    "policy",
    "physical_opportunity_id",
    "entry_dt",
    "mode",
    "trade_direction",
    "exit_known_dt",
    "executed",
    "capacity_skip",
    "net_ticks",
    "cumulative_net_ticks",
    "drawdown_ticks",
)


class RepresentationConfirmationError(ValueError):
    """Raised when the frozen confirmation contract cannot be honored."""


def run_representation_confirmation(
    development_physical: Sequence[Mapping[str, Any]] | pd.DataFrame,
    confirmation_rows: Mapping[
        str, Sequence[Mapping[str, Any]] | pd.DataFrame
    ],
    *,
    config: Optional[Mapping[str, Any]] = None,
    gates: Optional[Mapping[str, Any]] = None,
    source_manifests: Optional[Mapping[str, Mapping[str, Any]]] = None,
) -> Dict[str, Any]:
    """Fit the frozen block on Phase 5 and score three unused sequences."""
    resolved = _resolve_config(config)
    resolved_gates = _resolve_gates(gates)
    if set(confirmation_rows) != {"GC 04-26", "NG 03-26", "MNQ 03-26"}:
        raise RepresentationConfirmationError(
            "confirmation requires exactly GC 04-26, NG 03-26, and MNQ 03-26"
        )
    development = _canonical_development(development_physical)
    fits = _fit_component_block(development, resolved)

    physical_frames: List[pd.DataFrame] = []
    source_counts: Dict[str, Any] = {}
    for sequence, rows in confirmation_rows.items():
        physical = _canonical_confirmation_rows(rows)
        if physical["sequence"].unique().tolist() != [sequence]:
            raise RepresentationConfirmationError(
                f"confirmation sequence mismatch for {sequence}"
            )
        source_counts[sequence] = {
            "physical_opportunities": len(physical),
            "incomplete_context_exclusions": int(
                physical.attrs.get("excluded_incomplete_context", 0)
            ),
            "multi_signal_opportunities": int(
                physical.attrs.get("multi_signal_opportunities", 0)
            ),
        }
        physical_frames.append(physical)
    physical = pd.concat(physical_frames, ignore_index=True)
    physical = _attach_matched_labels(physical)
    decisions = _score_confirmation(physical, fits, resolved)

    execution_frames: List[pd.DataFrame] = []
    sequence_summaries: List[Dict[str, Any]] = []
    for sequence in sorted(confirmation_rows):
        seq_physical = physical.loc[physical["sequence"] == sequence].copy()
        seq_decisions = decisions.loc[
            decisions["sequence"] == sequence
        ].copy()
        policy_summaries: Dict[str, Dict[str, Any]] = {}
        for policy in ("context_block", "always_continuation", "always_reversion"):
            ledger, policy_summary = _execute_policy(
                seq_physical,
                seq_decisions,
                policy=policy,
                maximum_concurrent=int(
                    resolved["maximum_concurrent_per_direction"]
                ),
            )
            execution_frames.append(ledger)
            policy_summaries[policy] = policy_summary
        sequence_summaries.append(
            _sequence_summary(
                sequence,
                seq_physical,
                seq_decisions,
                policy_summaries,
            )
        )
    executions = pd.concat(execution_frames, ignore_index=True)
    summary = _build_panel_summary(
        sequence_summaries,
        decisions,
        resolved_gates,
    )
    safe_physical = _json_safe(
        physical.loc[:, PHYSICAL_COLUMNS].to_dict("records")
    )
    safe_decisions = _json_safe(
        decisions.loc[:, DECISION_COLUMNS].to_dict("records")
    )
    safe_executions = _json_safe(
        executions.loc[:, EXECUTION_COLUMNS].to_dict("records")
    )
    safe_fits = _json_safe(fits)
    safe_sources = {
        key: {
            "schema_version": value.get("schema_version"),
            "manifest_sha256": value.get("manifest_sha256"),
            "outcome_cube_sha256": value.get("outcome_cube_sha256"),
        }
        for key, value in (source_manifests or {}).items()
    }
    manifest: Dict[str, Any] = {
        "schema_version": REPRESENTATION_CONFIRMATION_SCHEMA_VERSION,
        "research_phase": "dynamic_phase_6",
        "research_classification": "independent_representation_confirmation",
        "development_manifest_sha256": resolved[
            "development_manifest_sha256"
        ],
        "source_outcome_cubes": _json_safe(safe_sources),
        "configuration": {
            "payload": resolved,
            "sha256": _sha256_json(resolved),
        },
        "interpretation_gates": {
            "payload": resolved_gates,
            "sha256": _sha256_json(resolved_gates),
        },
        "contracts": {
            "confirmation_outcomes_used_for_fit": False,
            "equal_component_weight": True,
            "canonical_lane_reward_independent": True,
            "physical_execution_deduplicated": True,
            "capacity_applied_per_policy": True,
            "family_b_authorized": False,
            "forward_paper_authorized": False,
        },
        "source_counts": source_counts,
        "counts": {
            "development_opportunities": len(development),
            "confirmation_opportunities": len(safe_physical),
            "decisions": len(safe_decisions),
            "execution_rows": len(safe_executions),
        },
        "fits_sha256": _sha256_json(safe_fits),
        "physical_ledger_sha256": _sha256_json(safe_physical),
        "decision_ledger_sha256": _sha256_json(safe_decisions),
        "execution_ledger_sha256": _sha256_json(safe_executions),
        "summary_sha256": _sha256_json(summary),
    }
    manifest["manifest_sha256"] = _sha256_json(manifest)
    return {
        "manifest": manifest,
        "summary": summary,
        "fit_rows": safe_fits,
        "physical_rows": safe_physical,
        "decision_rows": safe_decisions,
        "execution_rows": safe_executions,
    }


def write_representation_confirmation(
    output_dir: Path,
    result: Mapping[str, Any],
) -> Dict[str, str]:
    root = Path(output_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    paths = {
        "summary": root / "representation_confirmation_summary.json",
        "fits": root / "development_component_fits.json",
        "physical_ledger": root / "confirmation_physical_opportunities.csv",
        "decision_ledger": root / "confirmation_decisions.csv",
        "execution_ledger": root / "confirmation_execution.csv",
        "manifest": root / "representation_confirmation_manifest.json",
    }
    existing = [path for path in paths.values() if path.exists()]
    if existing:
        raise FileExistsError(
            "refusing to overwrite representation confirmation artifacts: "
            + ", ".join(str(path) for path in existing)
        )
    _write_json(paths["summary"], result["summary"])
    _write_json(paths["fits"], result["fit_rows"])
    _write_csv(paths["physical_ledger"], result["physical_rows"], PHYSICAL_COLUMNS)
    _write_csv(paths["decision_ledger"], result["decision_rows"], DECISION_COLUMNS)
    _write_csv(
        paths["execution_ledger"],
        result["execution_rows"],
        EXECUTION_COLUMNS,
    )
    manifest = dict(result["manifest"])
    manifest.pop("manifest_sha256", None)
    manifest["artifacts"] = {
        name: {
            "filename": path.name,
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for name, path in paths.items()
        if name != "manifest"
    }
    manifest["manifest_sha256"] = _sha256_json(manifest)
    _write_json(paths["manifest"], manifest)
    return {key: str(value) for key, value in paths.items()}


def _canonical_development(
    rows: Sequence[Mapping[str, Any]] | pd.DataFrame,
) -> pd.DataFrame:
    frame = rows.copy() if isinstance(rows, pd.DataFrame) else pd.DataFrame(rows)
    feature_names = {
        name
        for names in DEFAULT_CONFIRMATION_CONFIG[
            "component_representations"
        ].values()
        for name in names
    }
    required = {
        "sequence",
        "physical_opportunity_id",
        "matched_advantage_ticks",
        *feature_names,
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise RepresentationConfirmationError(
            "development ledger missing columns: " + ", ".join(missing)
        )
    if len(frame) != 43334:
        raise RepresentationConfirmationError(
            "development ledger must contain the frozen 43,334 opportunities"
        )
    if frame["physical_opportunity_id"].duplicated().any():
        raise RepresentationConfirmationError(
            "development physical opportunities must be unique"
        )
    for column in [*feature_names, "matched_advantage_ticks"]:
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    if not np.isfinite(
        frame[[*feature_names, "matched_advantage_ticks"]].to_numpy()
    ).all():
        raise RepresentationConfirmationError(
            "development fit inputs must be finite"
        )
    return frame.reset_index(drop=True)


def _fit_component_block(
    development: pd.DataFrame,
    config: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    fits: List[Dict[str, Any]] = []
    for representation_id, feature_names in config[
        "component_representations"
    ].items():
        fit = _fit_weighted_ridge(
            development,
            feature_names=feature_names,
            ridge_penalty=float(config["ridge_penalty"]),
        )
        values = development.loc[:, feature_names].to_numpy(dtype=float)
        prediction = float(fit["intercept"]) + (
            (
                values
                - np.asarray(fit["feature_means"], dtype=float)
            )
            / np.asarray(fit["feature_scales"], dtype=float)
        ) @ np.asarray(fit["coefficients"], dtype=float)
        prediction_mean = float(prediction.mean())
        prediction_scale = float(prediction.std(ddof=0))
        if prediction_scale <= 1e-12:
            raise RepresentationConfirmationError(
                f"component {representation_id} has constant development score"
            )
        fits.append(
            {
                "schema_version": REPRESENTATION_CONFIRMATION_SCHEMA_VERSION,
                "representation_id": representation_id,
                "feature_names": list(feature_names),
                **fit,
                "development_prediction_mean": prediction_mean,
                "development_prediction_scale": prediction_scale,
            }
        )
    return fits


def _canonical_confirmation_rows(
    rows: Sequence[Mapping[str, Any]] | pd.DataFrame,
) -> pd.DataFrame:
    frame = rows.copy() if isinstance(rows, pd.DataFrame) else pd.DataFrame(rows)
    required = {
        "physical_opportunity_id",
        "lane_event_id",
        "lane_id",
        "timeframe",
        "mode",
        "exit_known_dt",
        "trade_direction",
        "net_pnl_ticks",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise RepresentationConfirmationError(
            "confirmation cube missing canonical-lane columns: "
            + ", ".join(missing)
        )
    features = _canonical_physical_rows(frame)
    canonical_rows: List[Dict[str, Any]] = []
    for physical_id, group in frame.groupby(
        "physical_opportunity_id", sort=True
    ):
        signal_dt = pd.to_datetime(group["signal_dt"], utc=True)
        latest = signal_dt.max()
        candidate = group.loc[signal_dt == latest].copy()
        candidate["timeframe"] = pd.to_numeric(
            candidate["timeframe"], errors="raise"
        ).astype(int)
        minimum_timeframe = int(candidate["timeframe"].min())
        candidate = candidate.loc[
            candidate["timeframe"] == minimum_timeframe
        ].sort_values(["lane_id", "lane_event_id"])
        lane_id = str(candidate.iloc[0]["lane_id"])
        lane_event_id = str(candidate.iloc[0]["lane_event_id"])
        lane = group.loc[
            group["lane_event_id"].astype(str) == lane_event_id
        ].copy()
        payload: Dict[str, Any] = {
            "physical_opportunity_id": str(physical_id),
            "canonical_lane_id": lane_id,
            "canonical_lane_event_id": lane_event_id,
            "canonical_timeframe": minimum_timeframe,
        }
        for mode in ("continuation", "reversion"):
            mode_rows = lane.loc[lane["mode"].astype(str) == mode]
            if mode_rows.empty:
                raise RepresentationConfirmationError(
                    f"canonical lane {lane_event_id} lacks {mode}"
                )
            for column in (
                "net_pnl_ticks",
                "exit_known_dt",
                "trade_direction",
            ):
                values = mode_rows[column].drop_duplicates().tolist()
                if len(values) != 1:
                    raise RepresentationConfirmationError(
                        f"canonical lane {lane_event_id} has non-invariant "
                        f"{mode} {column}"
                    )
                payload[f"{mode}_{column}"] = values[0]
        canonical_rows.append(payload)
    canonical = pd.DataFrame(canonical_rows)
    out = features.merge(
        canonical,
        on="physical_opportunity_id",
        how="inner",
        validate="one_to_one",
    )
    if len(out) != len(features):
        raise RepresentationConfirmationError(
            "canonical lane merge lost physical opportunities"
        )
    out = out.drop(
        columns=["continuation_net_ticks", "reversion_net_ticks"]
    ).rename(
        columns={
            "continuation_net_pnl_ticks": "continuation_net_ticks",
            "reversion_net_pnl_ticks": "reversion_net_ticks",
        }
    )
    for mode in ("continuation", "reversion"):
        out[f"{mode}_net_ticks"] = pd.to_numeric(
            out[f"{mode}_net_ticks"], errors="raise"
        ).astype(float)
        out[f"{mode}_exit_known_dt"] = pd.to_datetime(
            out[f"{mode}_exit_known_dt"], errors="raise", utc=True
        ).dt.tz_convert("America/Denver")
        out[f"{mode}_trade_direction"] = pd.to_numeric(
            out[f"{mode}_trade_direction"], errors="raise"
        ).astype(int)
    out["schema_version"] = REPRESENTATION_CONFIRMATION_SCHEMA_VERSION
    out.attrs.update(features.attrs)
    return out


def _attach_matched_labels(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    fields = ["sequence", "session_id", "signal_side"]
    for mode in ("continuation", "reversion"):
        out[f"{mode}_baseline_ticks"] = out.groupby(fields)[
            f"{mode}_net_ticks"
        ].transform("mean")
        out[f"{mode}_residual_ticks"] = (
            out[f"{mode}_net_ticks"] - out[f"{mode}_baseline_ticks"]
        )
    out["paired_advantage_ticks"] = (
        out["continuation_net_ticks"] - out["reversion_net_ticks"]
    )
    out["matched_advantage_ticks"] = (
        out["continuation_residual_ticks"]
        - out["reversion_residual_ticks"]
    )
    return out.loc[:, PHYSICAL_COLUMNS]


def _score_confirmation(
    physical: pd.DataFrame,
    fits: Sequence[Mapping[str, Any]],
    config: Mapping[str, Any],
) -> pd.DataFrame:
    out = physical[
        [
            "sequence",
            "instrument",
            "physical_opportunity_id",
            "session_id",
            "entry_dt",
            "signal_side",
            "continuation_net_ticks",
            "reversion_net_ticks",
            "continuation_residual_ticks",
            "reversion_residual_ticks",
            "paired_advantage_ticks",
        ]
    ].copy()
    component_columns: List[str] = []
    for fit in fits:
        representation_id = str(fit["representation_id"])
        feature_names = list(fit["feature_names"])
        values = physical.loc[:, feature_names].to_numpy(dtype=float)
        raw = float(fit["intercept"]) + (
            (
                values
                - np.asarray(fit["feature_means"], dtype=float)
            )
            / np.asarray(fit["feature_scales"], dtype=float)
        ) @ np.asarray(fit["coefficients"], dtype=float)
        standardized = (
            raw - float(fit["development_prediction_mean"])
        ) / float(fit["development_prediction_scale"])
        column = f"{representation_id}_score"
        out[column] = standardized
        component_columns.append(column)
    expected = [
        "trend_state_score",
        "directional_momentum_score",
        "vwap_location_score",
    ]
    if component_columns != expected:
        raise RepresentationConfirmationError(
            "confirmation component order changed"
        )
    out["combined_score"] = out[component_columns].mean(axis=1)
    out["selected_mode"] = np.where(
        out["combined_score"] >= 0.0, "continuation", "reversion"
    )
    out["selected_net_ticks"] = np.where(
        out["selected_mode"] == "continuation",
        out["continuation_net_ticks"],
        out["reversion_net_ticks"],
    )
    out["equal_mode_baseline_ticks"] = 0.5 * (
        out["continuation_net_ticks"] + out["reversion_net_ticks"]
    )
    out["equal_mode_uplift_ticks"] = (
        out["selected_net_ticks"] - out["equal_mode_baseline_ticks"]
    )
    out["selected_matched_residual_ticks"] = np.where(
        out["selected_mode"] == "continuation",
        out["continuation_residual_ticks"],
        out["reversion_residual_ticks"],
    )
    out["schema_version"] = REPRESENTATION_CONFIRMATION_SCHEMA_VERSION
    return out.loc[:, DECISION_COLUMNS]


def _execute_policy(
    physical: pd.DataFrame,
    decisions: pd.DataFrame,
    *,
    policy: str,
    maximum_concurrent: int,
) -> tuple[pd.DataFrame, Dict[str, Any]]:
    merged = physical.merge(
        decisions[
            ["physical_opportunity_id", "selected_mode"]
        ],
        on="physical_opportunity_id",
        validate="one_to_one",
    ).sort_values(["entry_dt", "physical_opportunity_id"])
    active: Dict[int, List[pd.Timestamp]] = {1: [], -1: []}
    records: List[Dict[str, Any]] = []
    cumulative = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for row in merged.to_dict("records"):
        mode = (
            str(row["selected_mode"])
            if policy == "context_block"
            else (
                "continuation"
                if policy == "always_continuation"
                else "reversion"
            )
        )
        direction = int(row[f"{mode}_trade_direction"])
        entry_dt = pd.Timestamp(row["entry_dt"])
        active[direction] = [
            value for value in active[direction] if value > entry_dt
        ]
        capacity_skip = len(active[direction]) >= maximum_concurrent
        executed = not capacity_skip
        exit_known = pd.Timestamp(row[f"{mode}_exit_known_dt"])
        reward = float(row[f"{mode}_net_ticks"]) if executed else 0.0
        if executed:
            active[direction].append(exit_known)
            cumulative += reward
            peak = max(peak, cumulative)
            max_drawdown = max(max_drawdown, peak - cumulative)
        records.append(
            {
                "schema_version": REPRESENTATION_CONFIRMATION_SCHEMA_VERSION,
                "sequence": str(row["sequence"]),
                "policy": policy,
                "physical_opportunity_id": str(
                    row["physical_opportunity_id"]
                ),
                "entry_dt": entry_dt,
                "mode": mode,
                "trade_direction": direction,
                "exit_known_dt": exit_known,
                "executed": executed,
                "capacity_skip": capacity_skip,
                "net_ticks": reward,
                "cumulative_net_ticks": cumulative,
                "drawdown_ticks": peak - cumulative,
            }
        )
    ledger = pd.DataFrame(records, columns=EXECUTION_COLUMNS)
    executed_rewards = ledger.loc[ledger["executed"], "net_ticks"]
    gains = float(executed_rewards.loc[executed_rewards > 0.0].sum())
    losses = abs(float(executed_rewards.loc[executed_rewards < 0.0].sum()))
    profit_factor = gains / losses if losses > 0.0 else None
    return ledger, _json_safe(
        {
            "policy": policy,
            "trades": int(ledger["executed"].sum()),
            "capacity_skips": int(ledger["capacity_skip"].sum()),
            "net_ticks": float(executed_rewards.sum()),
            "mean_net_ticks": float(executed_rewards.mean())
            if len(executed_rewards)
            else None,
            "profit_factor": profit_factor,
            "max_drawdown_ticks": max_drawdown,
        }
    )


def _sequence_summary(
    sequence: str,
    physical: pd.DataFrame,
    decisions: pd.DataFrame,
    policies: Mapping[str, Mapping[str, Any]],
) -> Dict[str, Any]:
    rank_ic = decisions["combined_score"].corr(
        decisions["paired_advantage_ticks"], method="spearman"
    )
    context = policies["context_block"]
    static_cont = policies["always_continuation"]
    static_rev = policies["always_reversion"]
    better_static = max(
        (static_cont, static_rev),
        key=lambda row: float(row["net_ticks"]),
    )
    improvement = float(context["net_ticks"]) - float(
        better_static["net_ticks"]
    )
    trades = int(context["trades"])
    return _json_safe(
        {
            "schema_version": REPRESENTATION_CONFIRMATION_SCHEMA_VERSION,
            "sequence": sequence,
            "instrument": sequence.split()[0],
            "opportunities": len(physical),
            "sessions": int(physical["session_id"].nunique()),
            "rank_ic": float(rank_ic),
            "selected_paper_mean_net_ticks": float(
                decisions["selected_net_ticks"].mean()
            ),
            "equal_mode_paper_mean_net_ticks": float(
                decisions["equal_mode_baseline_ticks"].mean()
            ),
            "equal_mode_uplift_ticks": float(
                decisions["equal_mode_uplift_ticks"].mean()
            ),
            "matched_selected_uplift_ticks": float(
                decisions["selected_matched_residual_ticks"].mean()
            ),
            "continuation_selection_rate_pct": 100.0
            * float((decisions["selected_mode"] == "continuation").mean()),
            "policies": dict(policies),
            "better_static_policy": better_static["policy"],
            "execution_improvement_over_better_static_ticks": improvement,
            "execution_improvement_ticks_per_context_trade": (
                improvement / trades if trades else None
            ),
        }
    )


def _build_panel_summary(
    sequence_rows: Sequence[Mapping[str, Any]],
    decisions: pd.DataFrame,
    gates: Mapping[str, Any],
) -> Dict[str, Any]:
    by_sequence = {str(row["sequence"]): row for row in sequence_rows}
    rank_values = [float(row["rank_ic"]) for row in sequence_rows]
    continuation_rate = 100.0 * float(
        (decisions["selected_mode"] == "continuation").mean()
    )
    independent = [by_sequence["GC 04-26"], by_sequence["NG 03-26"]]
    positive_improvements = [
        max(
            0.0,
            float(row["execution_improvement_over_better_static_ticks"]),
        )
        for row in independent
    ]
    positive_total = sum(positive_improvements)
    largest_share = (
        100.0 * max(positive_improvements) / positive_total
        if positive_total > 0.0
        else None
    )
    criteria = {
        "minimum_coverage": all(
            int(row["sessions"])
            >= int(gates["minimum_sessions_each_sequence"])
            and int(row["opportunities"])
            >= int(gates["minimum_opportunities_each_sequence"])
            for row in sequence_rows
        ),
        "rank_ic_each_sequence": all(
            float(row["rank_ic"])
            > float(gates["minimum_rank_ic_each_sequence"])
            for row in sequence_rows
        ),
        "equal_sequence_rank_ic": float(np.mean(rank_values))
        >= float(gates["minimum_equal_sequence_rank_ic"]),
        "matched_uplift_each_sequence": all(
            float(row["matched_selected_uplift_ticks"])
            >= float(gates["minimum_matched_uplift_ticks_each_sequence"])
            for row in sequence_rows
        ),
        "equal_mode_uplift_each_sequence": all(
            float(row["equal_mode_uplift_ticks"])
            >= float(gates["minimum_equal_mode_uplift_ticks_each_sequence"])
            for row in sequence_rows
        ),
        "independent_execution_profitable": all(
            float(row["policies"]["context_block"]["net_ticks"]) > 0.0
            and row["policies"]["context_block"]["profit_factor"] is not None
            and float(row["policies"]["context_block"]["profit_factor"])
            > float(gates["minimum_independent_execution_profit_factor"])
            for row in independent
        ),
        "independent_beats_better_static": all(
            float(row["execution_improvement_ticks_per_context_trade"])
            >= float(
                gates[
                    "minimum_independent_static_improvement_ticks_per_trade"
                ]
            )
            for row in independent
        ),
        "mode_selection_balance": min(
            continuation_rate, 100.0 - continuation_rate
        )
        >= float(gates["minimum_pooled_mode_selection_rate_pct"]),
        "gc_ng_improvement_not_concentrated": largest_share is not None
        and largest_share
        < float(
            gates["maximum_gc_ng_positive_improvement_share_pct"]
        ),
    }
    passed = all(criteria.values())
    return _json_safe(
        {
            "schema_version": REPRESENTATION_CONFIRMATION_SCHEMA_VERSION,
            "research_phase": "dynamic_phase_6",
            "research_classification": (
                "independent_representation_confirmation"
            ),
            "sequence_summaries": list(sequence_rows),
            "panel": {
                "sequences": len(sequence_rows),
                "opportunities": len(decisions),
                "equal_sequence_mean_rank_ic": float(np.mean(rank_values)),
                "continuation_selection_rate_pct": continuation_rate,
                "reversion_selection_rate_pct": 100.0 - continuation_rate,
                "largest_gc_ng_positive_improvement_share_pct": (
                    largest_share
                ),
            },
            "gates": {
                "thresholds": dict(gates),
                "criteria": criteria,
                "passed": passed,
            },
            "result_label": (
                "INDEPENDENT_REPRESENTATION_CONFIRMED"
                if passed
                else "INDEPENDENT_REPRESENTATION_NOT_CONFIRMED"
            ),
            "context_policy_development_authorized": passed,
            "family_b_authorized": False,
            "forward_paper_authorized": False,
        }
    )


def _resolve_config(config: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    resolved = json.loads(json.dumps(DEFAULT_CONFIRMATION_CONFIG))
    resolved.update(dict(config or {}))
    if resolved != DEFAULT_CONFIRMATION_CONFIG:
        raise RepresentationConfirmationError(
            "Dynamic Phase 6 freezes the confirmation configuration"
        )
    return resolved


def _resolve_gates(gates: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    resolved = json.loads(json.dumps(DEFAULT_CONFIRMATION_GATES))
    resolved.update(dict(gates or {}))
    if resolved != DEFAULT_CONFIRMATION_GATES:
        raise RepresentationConfirmationError(
            "Dynamic Phase 6 freezes the confirmation gates"
        )
    return resolved


def _write_csv(
    path: Path,
    rows: Sequence[Mapping[str, Any]],
    columns: Sequence[str],
) -> None:
    expected = set(columns)
    for index, row in enumerate(rows):
        if set(row) != expected:
            raise RepresentationConfirmationError(
                f"confirmation row {index} violates stable schema; "
                f"missing={sorted(expected - set(row))}, "
                f"extra={sorted(set(row) - expected)}"
            )
    pd.DataFrame(rows, columns=columns).to_csv(path, index=False)


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
