from __future__ import annotations

"""Low-dimensional, leave-one-sequence-out signal representation audit."""

from collections import defaultdict
import hashlib
import json
from math import ceil, isfinite
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from ta_foundation.core.manifest import sha256_file


DYNAMIC_REPRESENTATION_SCHEMA_VERSION = "dynamic_representation_audit.v1"

DEFAULT_REPRESENTATION_CONFIG: Dict[str, Any] = {
    "observational_unit": "physical_opportunity_id",
    "outcome_modes": ["continuation", "reversion"],
    "matched_baseline_fields": ["sequence", "session_id", "signal_side"],
    "cross_fit": "leave_one_sequence_out",
    "training_sequence_weight": "equal_total_weight",
    "standardization": "training_only_population_mean_std",
    "model": "linear_ridge",
    "ridge_penalty": 1.0,
    "ridge_intercept_penalized": False,
    "score_mode_rule": "score >= 0 continuation else reversion",
    "tercile_fraction": 1.0 / 3.0,
    "signal_lane_denominator": 36.0,
    "representations": {
        "trend_state": ["trend_state_aligned"],
        "directional_momentum": [
            "return_60m_aligned",
            "vwap_slope_15_aligned",
        ],
        "vwap_location": [
            "close_vs_vwap_aligned",
            "close_vs_vwap_abs",
        ],
        "signal_extremity": [
            "max_signal_ratio",
            "signal_lane_fraction",
        ],
        "trigger_structure": [
            "latched_any",
            "zone_break_fraction",
        ],
        "session_clock": [
            "clock_fraction",
            "clock_fraction_sq",
        ],
    },
}

DEFAULT_REPRESENTATION_GATES: Dict[str, Any] = {
    "minimum_opportunities_each_sequence": 250,
    "minimum_sessions_each_sequence": 20,
    "minimum_positive_rank_sequences": 5,
    "minimum_sequence_equal_rank_ic": 0.03,
    "minimum_material_uplift_sequences": 5,
    "minimum_sequence_uplift_ticks": 2.5,
    "minimum_sequence_equal_uplift_ticks": 2.5,
    "minimum_material_contrast_sequences": 5,
    "minimum_sequence_contrast_ticks": 5.0,
    "minimum_sequence_equal_contrast_ticks": 5.0,
    "minimum_positive_instruments": 3,
    "minimum_mode_selection_rate_pct": 10.0,
    "maximum_positive_uplift_sequence_share_pct": 50.0,
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
    "continuation_net_ticks",
    "reversion_net_ticks",
    "continuation_baseline_ticks",
    "reversion_baseline_ticks",
    "continuation_residual_ticks",
    "reversion_residual_ticks",
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

SCORE_COLUMNS = (
    "schema_version",
    "representation_id",
    "sequence",
    "instrument",
    "physical_opportunity_id",
    "session_id",
    "signal_side",
    "score",
    "selected_mode",
    "selected_residual_ticks",
    "matched_advantage_ticks",
    "score_rank",
    "score_tercile",
)


class RepresentationAuditError(ValueError):
    """Raised when the frozen Phase 5 contract cannot be honored."""


def run_representation_audit(
    sequence_rows: Mapping[
        str, Sequence[Mapping[str, Any]] | pd.DataFrame
    ],
    *,
    config: Optional[Mapping[str, Any]] = None,
    gates: Optional[Mapping[str, Any]] = None,
    source_manifests: Optional[Mapping[str, Mapping[str, Any]]] = None,
) -> Dict[str, Any]:
    """Run the complete seven-sequence cross-fitted audit."""
    resolved = _resolve_config(config)
    resolved_gates = _resolve_gates(gates)
    if len(sequence_rows) != 7:
        raise RepresentationAuditError(
            "representation audit requires exactly seven sequences"
        )

    physical_frames: List[pd.DataFrame] = []
    incomplete_context_exclusions: Dict[str, int] = {}
    multi_signal_opportunities: Dict[str, int] = {}
    multi_outcome_opportunities: Dict[str, int] = {}
    for declared_sequence, rows in sequence_rows.items():
        frame = _canonical_physical_rows(rows)
        actual = frame["sequence"].unique().tolist()
        if actual != [str(declared_sequence)]:
            raise RepresentationAuditError(
                f"sequence key {declared_sequence!r} does not match rows {actual}"
            )
        physical_frames.append(frame)
        incomplete_context_exclusions[str(declared_sequence)] = int(
            frame.attrs.get("excluded_incomplete_context", 0)
        )
        multi_signal_opportunities[str(declared_sequence)] = int(
            frame.attrs.get("multi_signal_opportunities", 0)
        )
        multi_outcome_opportunities[str(declared_sequence)] = int(
            frame.attrs.get("multi_outcome_opportunities", 0)
        )
    physical = pd.concat(physical_frames, ignore_index=True)
    physical = _attach_matched_baselines(physical)

    fit_rows: List[Dict[str, Any]] = []
    score_frames: List[pd.DataFrame] = []
    sequence_summaries: List[Dict[str, Any]] = []
    representation_summaries: List[Dict[str, Any]] = []
    instrument_summaries: List[Dict[str, Any]] = []
    sequences = sorted(physical["sequence"].unique())
    representations = resolved["representations"]

    for representation_id, feature_names in representations.items():
        representation_scores: List[pd.DataFrame] = []
        for held_out in sequences:
            training = physical.loc[physical["sequence"] != held_out].copy()
            scoring = physical.loc[physical["sequence"] == held_out].copy()
            fit = _fit_weighted_ridge(
                training,
                feature_names=feature_names,
                ridge_penalty=float(resolved["ridge_penalty"]),
            )
            scored = _score_holdout(
                scoring,
                representation_id=representation_id,
                feature_names=feature_names,
                fit=fit,
                tercile_fraction=float(resolved["tercile_fraction"]),
            )
            fit_rows.append(
                {
                    "schema_version": DYNAMIC_REPRESENTATION_SCHEMA_VERSION,
                    "representation_id": representation_id,
                    "held_out_sequence": held_out,
                    "training_sequences": sorted(
                        training["sequence"].unique().tolist()
                    ),
                    "feature_names": list(feature_names),
                    **fit,
                }
            )
            score_frames.append(scored)
            representation_scores.append(scored)
            sequence_summaries.append(
                _summarize_score_group(
                    scored,
                    representation_id=representation_id,
                    group_type="sequence",
                    group_id=held_out,
                )
            )
        combined = pd.concat(representation_scores, ignore_index=True)
        for instrument, group in combined.groupby("instrument", sort=True):
            instrument_summaries.append(
                _summarize_score_group(
                    group,
                    representation_id=representation_id,
                    group_type="instrument",
                    group_id=str(instrument),
                )
            )
        representation_summaries.append(
            _summarize_representation(
                representation_id,
                combined,
                [
                    row
                    for row in sequence_summaries
                    if row["representation_id"] == representation_id
                ],
                [
                    row
                    for row in instrument_summaries
                    if row["representation_id"] == representation_id
                ],
                resolved_gates,
            )
        )

    scores = pd.concat(score_frames, ignore_index=True)
    key_columns = ["representation_id", "physical_opportunity_id"]
    if scores.duplicated(key_columns).any():
        raise RepresentationAuditError("duplicate cross-fit scores detected")
    panel_passed = any(
        bool(row["gates"]["passed"]) for row in representation_summaries
    )
    summary = _json_safe(
        {
            "schema_version": DYNAMIC_REPRESENTATION_SCHEMA_VERSION,
            "research_phase": "dynamic_phase_5",
            "research_classification": "cross_fitted_representation_diagnostic",
            "representation_summaries": representation_summaries,
            "sequence_summaries": sequence_summaries,
            "instrument_summaries": instrument_summaries,
            "panel": {
                "sequences": len(sequences),
                "representations": len(representations),
                "physical_opportunities": len(physical),
                "incomplete_context_exclusions": (
                    incomplete_context_exclusions
                ),
                "multi_signal_opportunities": multi_signal_opportunities,
                "multi_outcome_opportunities": (
                    multi_outcome_opportunities
                ),
                "cross_fitted_scores": len(scores),
                "passing_representations": sum(
                    bool(row["gates"]["passed"])
                    for row in representation_summaries
                ),
            },
            "result_label": (
                "REPRESENTATION_EVIDENCE_PRESENT"
                if panel_passed
                else "NO_STABLE_LOW_DIMENSIONAL_REPRESENTATION"
            ),
            "representation_confirmation_authorized": panel_passed,
            "selector_rebuild_authorized": False,
            "family_b_authorized": False,
            "forward_paper_authorized": False,
        }
    )
    safe_physical = _json_safe(
        physical.loc[:, PHYSICAL_COLUMNS].to_dict("records")
    )
    safe_fits = _json_safe(fit_rows)
    safe_scores = _json_safe(
        scores.loc[:, SCORE_COLUMNS].to_dict("records")
    )
    safe_sources = {
        key: {
            "schema_version": value.get("schema_version"),
            "manifest_sha256": value.get("manifest_sha256"),
            "outcome_cube_sha256": value.get("outcome_cube_sha256"),
        }
        for key, value in (source_manifests or {}).items()
    }
    manifest: Dict[str, Any] = {
        "schema_version": DYNAMIC_REPRESENTATION_SCHEMA_VERSION,
        "research_phase": "dynamic_phase_5",
        "research_classification": "cross_fitted_representation_diagnostic",
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
            "physical_opportunity_deduplicated": True,
            "matched_session_signal_side_baseline": True,
            "leave_one_sequence_out": True,
            "held_out_outcomes_used_for_fit": False,
            "hindsight_oracle_used": False,
            "capacity_projection_used": False,
            "selector_rebuild_authorized": False,
            "family_b_authorized": False,
            "forward_paper_authorized": False,
        },
        "counts": {
            "sequences": len(sequences),
            "representations": len(representations),
            "physical_opportunities": len(safe_physical),
            "incomplete_context_exclusions": (
                incomplete_context_exclusions
            ),
            "multi_signal_opportunities": multi_signal_opportunities,
            "multi_outcome_opportunities": multi_outcome_opportunities,
            "fit_records": len(safe_fits),
            "cross_fitted_scores": len(safe_scores),
        },
        "physical_ledger_sha256": _sha256_json(safe_physical),
        "fit_ledger_sha256": _sha256_json(safe_fits),
        "score_ledger_sha256": _sha256_json(safe_scores),
        "summary_sha256": _sha256_json(summary),
    }
    manifest["manifest_sha256"] = _sha256_json(manifest)
    return {
        "manifest": manifest,
        "summary": summary,
        "physical_rows": safe_physical,
        "fit_rows": safe_fits,
        "score_rows": safe_scores,
    }


def write_representation_audit(
    output_dir: Path,
    result: Mapping[str, Any],
) -> Dict[str, str]:
    """Write an immutable hash-bound Phase 5 bundle."""
    root = Path(output_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    paths = {
        "summary": root / "representation_audit_summary.json",
        "physical_ledger": root / "physical_opportunity_ledger.csv",
        "fit_ledger": root / "cross_fit_models.json",
        "score_ledger": root / "cross_fitted_scores.csv",
        "manifest": root / "representation_audit_manifest.json",
    }
    existing = [path for path in paths.values() if path.exists()]
    if existing:
        raise FileExistsError(
            "refusing to overwrite representation audit artifacts: "
            + ", ".join(str(path) for path in existing)
        )
    _write_json(paths["summary"], result["summary"])
    _write_csv(paths["physical_ledger"], result["physical_rows"], PHYSICAL_COLUMNS)
    _write_json(paths["fit_ledger"], result["fit_rows"])
    _write_csv(paths["score_ledger"], result["score_rows"], SCORE_COLUMNS)
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


def _canonical_physical_rows(
    rows: Sequence[Mapping[str, Any]] | pd.DataFrame,
) -> pd.DataFrame:
    frame = rows.copy() if isinstance(rows, pd.DataFrame) else pd.DataFrame(rows)
    required = {
        "sequence",
        "physical_opportunity_id",
        "lane_event_id",
        "lane_id",
        "session_id",
        "signal_side",
        "signal_dt",
        "entry_dt",
        "context_dt",
        "mode",
        "net_pnl_ticks",
        "trend_state",
        "return_60m",
        "vwap_slope_15",
        "close_vs_vwap",
        "signal_ratio",
        "latched_outside_window",
        "zone_break_trigger",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise RepresentationAuditError(
            "outcome cube is missing representation columns: "
            + ", ".join(missing)
        )
    if frame.empty:
        raise RepresentationAuditError("representation audit requires rows")
    out = frame.loc[:, sorted(required)].copy()
    for column in ("signal_dt", "entry_dt", "context_dt"):
        out[column] = pd.to_datetime(out[column], errors="raise", utc=True)
        out[column] = out[column].dt.tz_convert("America/Denver")
    if (out["context_dt"] > out["signal_dt"]).any():
        raise RepresentationAuditError(
            "representation input contains post-signal context"
        )
    for column in (
        "net_pnl_ticks",
        "return_60m",
        "vwap_slope_15",
        "close_vs_vwap",
        "signal_ratio",
    ):
        out[column] = pd.to_numeric(out[column], errors="raise").astype(float)
    if not np.isfinite(out["net_pnl_ticks"].to_numpy()).all():
        raise RepresentationAuditError(
            "representation rewards must be finite"
        )
    feature_values = out[
        [
            "return_60m",
            "vwap_slope_15",
            "close_vs_vwap",
            "signal_ratio",
        ]
    ].to_numpy()
    if np.isinf(feature_values).any():
        raise RepresentationAuditError(
            "representation features cannot be infinite"
        )
    out["latched_outside_window"] = out["latched_outside_window"].map(
        _truth_value
    )
    out["zone_break_trigger"] = out["zone_break_trigger"].map(_truth_value)

    records: List[Dict[str, Any]] = []
    excluded_incomplete_context = 0
    multi_signal_opportunities = 0
    multi_outcome_opportunities = 0
    for physical_id, group in out.groupby(
        "physical_opportunity_id", sort=True
    ):
        identity: Dict[str, Any] = {}
        for column in (
            "sequence",
            "session_id",
            "signal_side",
            "entry_dt",
        ):
            values = group[column].drop_duplicates().tolist()
            if len(values) != 1:
                raise RepresentationAuditError(
                    f"physical opportunity {physical_id} has inconsistent {column}"
                )
            identity[column] = values[0]
        side = str(identity["signal_side"])
        if side not in {"bull", "bear"}:
            raise RepresentationAuditError(f"invalid signal side: {side}")
        signal_values = group["signal_dt"].drop_duplicates().tolist()
        if len(signal_values) > 1:
            multi_signal_opportunities += 1
        signal_dt = pd.Timestamp(max(signal_values))
        entry_dt = pd.Timestamp(identity["entry_dt"])
        if not (group["signal_dt"] < entry_dt).all():
            raise RepresentationAuditError(
                f"physical opportunity {physical_id} has a signal at or "
                "after entry"
            )
        mode_values: Dict[str, float] = {}
        physical_has_multiple_outcomes = False
        for mode in ("continuation", "reversion"):
            mode_rows = group.loc[
                group["mode"].astype(str) == mode,
                ["lane_event_id", "net_pnl_ticks"],
            ]
            if mode_rows.empty:
                raise RepresentationAuditError(
                    f"physical opportunity {physical_id} lacks {mode} outcomes"
                )
            lane_reward_counts = mode_rows.groupby(
                "lane_event_id"
            )["net_pnl_ticks"].nunique()
            if lane_reward_counts.gt(1).any():
                raise RepresentationAuditError(
                    f"physical opportunity {physical_id} has a non-invariant "
                    f"lane outcome for {mode}"
                )
            lane_rewards = mode_rows.drop_duplicates(
                "lane_event_id"
            )["net_pnl_ticks"]
            if lane_rewards.nunique() > 1:
                physical_has_multiple_outcomes = True
            mode_values[mode] = float(lane_rewards.median())
        if physical_has_multiple_outcomes:
            multi_outcome_opportunities += 1
        lane_rows = group.sort_values("lane_event_id").drop_duplicates(
            "lane_event_id"
        )
        side_sign = 1.0 if side == "bull" else -1.0
        trend_values = lane_rows["trend_state"].astype(str).map(
            {"up": 1.0, "mixed": 0.0, "down": -1.0}
        )
        if trend_values.isna().any():
            bad = sorted(
                set(lane_rows.loc[trend_values.isna(), "trend_state"].astype(str))
            )
            raise RepresentationAuditError(
                f"invalid trend states: {bad}"
            )
        clock_minutes = (
            signal_dt.hour * 60 + signal_dt.minute - (10 * 60)
        )
        clock_fraction = float(clock_minutes / 200.0)
        sequence = str(identity["sequence"])
        record = {
                "schema_version": DYNAMIC_REPRESENTATION_SCHEMA_VERSION,
                "sequence": sequence,
                "instrument": sequence.split()[0],
                "physical_opportunity_id": str(physical_id),
                "session_id": str(identity["session_id"]),
                "signal_dt": signal_dt,
                "entry_dt": entry_dt,
                "signal_side": side,
                "continuation_net_ticks": mode_values["continuation"],
                "reversion_net_ticks": mode_values["reversion"],
                "trend_state_aligned": side_sign
                * float(trend_values.median()),
                "return_60m_aligned": side_sign
                * float(lane_rows["return_60m"].median()),
                "vwap_slope_15_aligned": side_sign
                * float(lane_rows["vwap_slope_15"].median()),
                "close_vs_vwap_aligned": side_sign
                * float(lane_rows["close_vs_vwap"].median()),
                "close_vs_vwap_abs": float(
                    lane_rows["close_vs_vwap"].abs().median()
                ),
                "max_signal_ratio": float(lane_rows["signal_ratio"].max()),
                "signal_lane_fraction": float(
                    lane_rows["lane_id"].nunique() / 36.0
                ),
                "latched_any": float(
                    lane_rows["latched_outside_window"].any()
                ),
                "zone_break_fraction": float(
                    lane_rows["zone_break_trigger"].mean()
                ),
                "clock_fraction": clock_fraction,
                "clock_fraction_sq": clock_fraction**2,
            }
        feature_fields = [
            field
            for names in DEFAULT_REPRESENTATION_CONFIG[
                "representations"
            ].values()
            for field in names
        ]
        if not all(isfinite(float(record[field])) for field in feature_fields):
            excluded_incomplete_context += 1
            continue
        records.append(record)
    result = pd.DataFrame(records).sort_values(
        ["sequence", "signal_dt", "physical_opportunity_id"]
    ).reset_index(drop=True)
    result.attrs["excluded_incomplete_context"] = (
        excluded_incomplete_context
    )
    result.attrs["multi_signal_opportunities"] = multi_signal_opportunities
    result.attrs["multi_outcome_opportunities"] = (
        multi_outcome_opportunities
    )
    return result


def _attach_matched_baselines(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    group_fields = ["sequence", "session_id", "signal_side"]
    out["continuation_baseline_ticks"] = out.groupby(
        group_fields
    )["continuation_net_ticks"].transform("mean")
    out["reversion_baseline_ticks"] = out.groupby(
        group_fields
    )["reversion_net_ticks"].transform("mean")
    out["continuation_residual_ticks"] = (
        out["continuation_net_ticks"] - out["continuation_baseline_ticks"]
    )
    out["reversion_residual_ticks"] = (
        out["reversion_net_ticks"] - out["reversion_baseline_ticks"]
    )
    out["matched_advantage_ticks"] = (
        out["continuation_residual_ticks"]
        - out["reversion_residual_ticks"]
    )
    return out


def _fit_weighted_ridge(
    training: pd.DataFrame,
    *,
    feature_names: Sequence[str],
    ridge_penalty: float,
) -> Dict[str, Any]:
    features = training.loc[:, feature_names].to_numpy(dtype=float)
    target = training["matched_advantage_ticks"].to_numpy(dtype=float)
    means = features.mean(axis=0)
    scales = features.std(axis=0, ddof=0)
    scales = np.where(scales > 1e-12, scales, 1.0)
    standardized = (features - means) / scales
    design = np.column_stack([np.ones(len(training)), standardized])
    sequence_counts = training["sequence"].value_counts().to_dict()
    weights = training["sequence"].map(
        lambda value: 1.0 / sequence_counts[value]
    ).to_numpy(dtype=float, copy=True)
    weights *= len(training) / weights.sum()
    penalty = np.eye(design.shape[1], dtype=float) * ridge_penalty
    penalty[0, 0] = 0.0
    lhs = design.T @ (weights[:, None] * design) + penalty
    rhs = design.T @ (weights * target)
    coefficients = np.linalg.pinv(lhs) @ rhs
    return {
        "training_rows": len(training),
        "training_sequence_rows": {
            str(key): int(value)
            for key, value in sorted(sequence_counts.items())
        },
        "feature_means": means.tolist(),
        "feature_scales": scales.tolist(),
        "intercept": float(coefficients[0]),
        "coefficients": coefficients[1:].tolist(),
    }


def _score_holdout(
    scoring: pd.DataFrame,
    *,
    representation_id: str,
    feature_names: Sequence[str],
    fit: Mapping[str, Any],
    tercile_fraction: float,
) -> pd.DataFrame:
    values = scoring.loc[:, feature_names].to_numpy(dtype=float)
    means = np.asarray(fit["feature_means"], dtype=float)
    scales = np.asarray(fit["feature_scales"], dtype=float)
    coefficients = np.asarray(fit["coefficients"], dtype=float)
    score = float(fit["intercept"]) + ((values - means) / scales) @ coefficients
    out = scoring[
        [
            "sequence",
            "instrument",
            "physical_opportunity_id",
            "session_id",
            "signal_side",
            "continuation_residual_ticks",
            "reversion_residual_ticks",
            "matched_advantage_ticks",
        ]
    ].copy()
    out["schema_version"] = DYNAMIC_REPRESENTATION_SCHEMA_VERSION
    out["representation_id"] = representation_id
    out["score"] = score
    out["selected_mode"] = np.where(
        out["score"] >= 0.0, "continuation", "reversion"
    )
    out["selected_residual_ticks"] = np.where(
        out["score"] >= 0.0,
        out["continuation_residual_ticks"],
        out["reversion_residual_ticks"],
    )
    ordered = out.sort_values(
        ["score", "physical_opportunity_id"],
        ascending=[False, True],
    ).index
    ranks = pd.Series(np.arange(1, len(out) + 1), index=ordered)
    out["score_rank"] = ranks.loc[out.index].astype(int)
    tercile_count = max(1, ceil(tercile_fraction * len(out)))
    out["score_tercile"] = "middle"
    out.loc[out["score_rank"] <= tercile_count, "score_tercile"] = "top"
    out.loc[
        out["score_rank"] > len(out) - tercile_count, "score_tercile"
    ] = "bottom"
    return out.loc[:, SCORE_COLUMNS]


def _summarize_score_group(
    scored: pd.DataFrame,
    *,
    representation_id: str,
    group_type: str,
    group_id: str,
) -> Dict[str, Any]:
    rank_ic = None
    if (
        scored["score"].nunique() >= 2
        and scored["matched_advantage_ticks"].nunique() >= 2
    ):
        value = scored["score"].corr(
            scored["matched_advantage_ticks"], method="spearman"
        )
        rank_ic = float(value) if pd.notna(value) else None
    top = scored.loc[scored["score_tercile"] == "top"]
    bottom = scored.loc[scored["score_tercile"] == "bottom"]
    contrast = (
        float(top["matched_advantage_ticks"].mean())
        - float(bottom["matched_advantage_ticks"].mean())
    )
    continuation_rate = 100.0 * float(
        (scored["selected_mode"] == "continuation").mean()
    )
    return _json_safe(
        {
            "schema_version": DYNAMIC_REPRESENTATION_SCHEMA_VERSION,
            "representation_id": representation_id,
            "group_type": group_type,
            "group_id": group_id,
            "opportunities": len(scored),
            "sessions": int(scored["session_id"].nunique()),
            "rank_ic": rank_ic,
            "selected_mode_mean_uplift_ticks": float(
                scored["selected_residual_ticks"].mean()
            ),
            "selected_mode_total_uplift_ticks": float(
                scored["selected_residual_ticks"].sum()
            ),
            "selected_mode_positive_residual_rate_pct": 100.0
            * float((scored["selected_residual_ticks"] > 0.0).mean()),
            "continuation_selection_rate_pct": continuation_rate,
            "reversion_selection_rate_pct": 100.0 - continuation_rate,
            "top_bottom_matched_advantage_contrast_ticks": contrast,
            "continuation_mean_residual_ticks": float(
                scored.loc[
                    scored["selected_mode"] == "continuation",
                    "selected_residual_ticks",
                ].mean()
            )
            if (scored["selected_mode"] == "continuation").any()
            else None,
            "reversion_mean_residual_ticks": float(
                scored.loc[
                    scored["selected_mode"] == "reversion",
                    "selected_residual_ticks",
                ].mean()
            )
            if (scored["selected_mode"] == "reversion").any()
            else None,
        }
    )


def _summarize_representation(
    representation_id: str,
    scores: pd.DataFrame,
    sequence_rows: Sequence[Mapping[str, Any]],
    instrument_rows: Sequence[Mapping[str, Any]],
    gates: Mapping[str, Any],
) -> Dict[str, Any]:
    rank_values = [
        float(row["rank_ic"])
        for row in sequence_rows
        if row["rank_ic"] is not None
    ]
    uplift_values = [
        float(row["selected_mode_mean_uplift_ticks"])
        for row in sequence_rows
    ]
    contrast_values = [
        float(row["top_bottom_matched_advantage_contrast_ticks"])
        for row in sequence_rows
    ]
    positive_contributions = [
        max(0.0, float(row["selected_mode_total_uplift_ticks"]))
        for row in sequence_rows
    ]
    total_positive = sum(positive_contributions)
    largest_share = (
        100.0 * max(positive_contributions) / total_positive
        if total_positive > 0.0
        else None
    )
    continuation_rate = 100.0 * float(
        (scores["selected_mode"] == "continuation").mean()
    )
    positive_rank_sequences = sum(value > 0.0 for value in rank_values)
    material_uplift_sequences = sum(
        value >= float(gates["minimum_sequence_uplift_ticks"])
        for value in uplift_values
    )
    material_contrast_sequences = sum(
        value >= float(gates["minimum_sequence_contrast_ticks"])
        for value in contrast_values
    )
    positive_instruments = sum(
        float(row["selected_mode_mean_uplift_ticks"]) > 0.0
        for row in instrument_rows
    )
    criteria = {
        "minimum_coverage": all(
            int(row["opportunities"])
            >= int(gates["minimum_opportunities_each_sequence"])
            and int(row["sessions"])
            >= int(gates["minimum_sessions_each_sequence"])
            for row in sequence_rows
        ),
        "positive_rank_sequences": positive_rank_sequences
        >= int(gates["minimum_positive_rank_sequences"]),
        "sequence_equal_rank_ic": len(rank_values) == len(sequence_rows)
        and float(np.mean(rank_values))
        >= float(gates["minimum_sequence_equal_rank_ic"]),
        "material_uplift_sequences": material_uplift_sequences
        >= int(gates["minimum_material_uplift_sequences"]),
        "sequence_equal_uplift": float(np.mean(uplift_values))
        >= float(gates["minimum_sequence_equal_uplift_ticks"]),
        "material_contrast_sequences": material_contrast_sequences
        >= int(gates["minimum_material_contrast_sequences"]),
        "sequence_equal_contrast": float(np.mean(contrast_values))
        >= float(gates["minimum_sequence_equal_contrast_ticks"]),
        "positive_instruments": positive_instruments
        >= int(gates["minimum_positive_instruments"]),
        "mode_selection_balance": min(
            continuation_rate, 100.0 - continuation_rate
        )
        >= float(gates["minimum_mode_selection_rate_pct"]),
        "positive_uplift_not_concentrated": largest_share is not None
        and largest_share
        < float(gates["maximum_positive_uplift_sequence_share_pct"]),
    }
    return _json_safe(
        {
            "schema_version": DYNAMIC_REPRESENTATION_SCHEMA_VERSION,
            "representation_id": representation_id,
            "opportunities": len(scores),
            "sequences": len(sequence_rows),
            "instruments": len(instrument_rows),
            "positive_rank_sequences": positive_rank_sequences,
            "sequence_equal_mean_rank_ic": (
                float(np.mean(rank_values)) if rank_values else None
            ),
            "opportunity_weighted_rank_ic": (
                float(
                    sum(
                        float(row["rank_ic"]) * int(row["opportunities"])
                        for row in sequence_rows
                        if row["rank_ic"] is not None
                    )
                    / sum(
                        int(row["opportunities"])
                        for row in sequence_rows
                        if row["rank_ic"] is not None
                    )
                )
                if rank_values
                else None
            ),
            "material_uplift_sequences": material_uplift_sequences,
            "sequence_equal_mean_selected_uplift_ticks": float(
                np.mean(uplift_values)
            ),
            "opportunity_weighted_mean_selected_uplift_ticks": float(
                scores["selected_residual_ticks"].mean()
            ),
            "material_contrast_sequences": material_contrast_sequences,
            "sequence_equal_mean_top_bottom_contrast_ticks": float(
                np.mean(contrast_values)
            ),
            "positive_instruments": positive_instruments,
            "continuation_selection_rate_pct": continuation_rate,
            "reversion_selection_rate_pct": 100.0 - continuation_rate,
            "largest_positive_uplift_sequence_share_pct": largest_share,
            "gates": {
                "thresholds": dict(gates),
                "criteria": criteria,
                "passed": all(criteria.values()),
            },
            "result_label": (
                "CROSS_FITTED_REPRESENTATION_SIGNAL"
                if all(criteria.values())
                else "NO_STABLE_REPRESENTATION_SIGNAL"
            ),
        }
    )


def _resolve_config(config: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    resolved = json.loads(json.dumps(DEFAULT_REPRESENTATION_CONFIG))
    resolved.update(dict(config or {}))
    if resolved != DEFAULT_REPRESENTATION_CONFIG:
        raise RepresentationAuditError(
            "Dynamic Phase 5 freezes the representation configuration"
        )
    return resolved


def _resolve_gates(gates: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    resolved = json.loads(json.dumps(DEFAULT_REPRESENTATION_GATES))
    resolved.update(dict(gates or {}))
    if resolved != DEFAULT_REPRESENTATION_GATES:
        raise RepresentationAuditError(
            "Dynamic Phase 5 freezes the interpretation gates"
        )
    return resolved


def _truth_value(value: Any) -> bool:
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    normalized = str(value).strip().lower()
    if normalized in {"true", "t", "yes", "y", "1", "1.0"}:
        return True
    if normalized in {"false", "f", "no", "n", "0", "0.0"}:
        return False
    raise RepresentationAuditError(f"invalid boolean value: {value!r}")


def _write_csv(
    path: Path,
    rows: Sequence[Mapping[str, Any]],
    columns: Sequence[str],
) -> None:
    expected = set(columns)
    for index, row in enumerate(rows):
        if set(row) != expected:
            raise RepresentationAuditError(
                f"representation row {index} violates stable schema; "
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
