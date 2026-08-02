from __future__ import annotations

"""Dynamic Phase 4C causal learnability audit.

The audit asks whether a coarse Family A parameter-family rank, computed only
from strictly known prior outcomes, predicts next-session paper reward. It is
an identifiability diagnostic and never projects executable capacity.
"""

from collections import defaultdict
import hashlib
import json
from math import ceil, isfinite
from pathlib import Path
from statistics import median
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from ta_foundation.core.manifest import sha256_file


DYNAMIC_CAUSAL_LEARNABILITY_SCHEMA_VERSION = (
    "dynamic_causal_learnability.v1"
)

DEFAULT_CAUSAL_LEARNABILITY_CONFIG: Dict[str, Any] = {
    "family_fields": [
        "timeframe",
        "lookback",
        "multiplier",
        "mode",
    ],
    "pooled_fields": [
        "signal_side",
        "time_bucket",
        "trend_state",
    ],
    "lookback_sessions": 5,
    "minimum_prior_family_sessions": 3,
    "shrinkage_prior_sessions": 3.0,
    "top_fraction": 0.20,
    "minimum_ranked_families_per_session": 20,
    "strict_evidence_rule": "exit_known_dt < decision_asof",
    "paper_outcome_filter": "capacity_eligible",
}

DEFAULT_CAUSAL_LEARNABILITY_GATES: Dict[str, Any] = {
    "minimum_evaluated_sessions_each_sequence": 20,
    "minimum_positive_rank_ic_sequences": 5,
    "minimum_pair_weighted_rank_ic": 0.05,
    "minimum_uplift_sequences": 5,
    "minimum_sequence_mean_uplift_ticks": 2.5,
    "minimum_cross_sequence_mean_uplift_ticks": 2.5,
    "minimum_cross_sequence_positive_rate_uplift_pp": 5.0,
    "maximum_positive_uplift_sequence_share_pct": 50.0,
}

FAMILY_SESSION_COLUMNS = (
    "schema_version",
    "sequence",
    "session_index",
    "session_id",
    "family_id",
    "timeframe",
    "lookback",
    "multiplier",
    "mode",
    "outcomes",
    "mean_net_ticks",
    "total_net_ticks",
    "positive_outcomes",
    "positive_rate_pct",
    "evidence_through",
)

RANK_COLUMNS = (
    "schema_version",
    "sequence",
    "session_index",
    "session_id",
    "decision_asof",
    "family_id",
    "timeframe",
    "lookback",
    "multiplier",
    "mode",
    "prior_window_start_session",
    "prior_window_end_session",
    "prior_family_sessions",
    "prior_family_mean_ticks",
    "prior_grand_mean_ticks",
    "shrinkage_score_ticks",
    "rank",
    "ranked_families",
    "top_quintile",
    "evidence_through",
    "realized_outcomes",
    "realized_mean_net_ticks",
    "realized_total_net_ticks",
    "realized_positive_outcomes",
)

SESSION_COLUMNS = (
    "schema_version",
    "sequence",
    "session_index",
    "session_id",
    "decision_asof",
    "ranked_families",
    "top_families",
    "rank_ic",
    "top_mean_net_ticks",
    "all_mean_net_ticks",
    "top_quintile_uplift_ticks",
    "top_positive_rate_pct",
    "all_positive_rate_pct",
    "positive_rate_uplift_pp",
    "top_paper_outcomes",
    "top_paper_total_net_ticks",
)


class CausalLearnabilityError(ValueError):
    """Raised when the frozen learnability contract cannot be honored."""


def run_causal_learnability_audit(
    rows: Sequence[Mapping[str, Any]] | pd.DataFrame,
    *,
    config: Optional[Mapping[str, Any]] = None,
    source_manifest: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Run one independently reset sequence audit."""
    resolved = _resolve_config(config)
    frame = _canonical_rows(rows)
    sequences = sorted(frame["sequence"].unique())
    if len(sequences) != 1:
        raise CausalLearnabilityError(
            "learnability audit requires exactly one sequence"
        )
    sequence = str(sequences[0])
    family_parts = _family_index(frame)
    sessions, boundaries = _session_clock(frame)
    family_session_rows = _build_family_session_rows(
        frame,
        sequence=sequence,
        sessions=sessions,
        family_parts=family_parts,
    )
    family_session = pd.DataFrame(family_session_rows)

    lookback_sessions = int(resolved["lookback_sessions"])
    minimum_prior = int(resolved["minimum_prior_family_sessions"])
    prior_strength = float(resolved["shrinkage_prior_sessions"])
    minimum_ranked = int(
        resolved["minimum_ranked_families_per_session"]
    )
    top_fraction = float(resolved["top_fraction"])

    rank_rows: List[Dict[str, Any]] = []
    session_rows: List[Dict[str, Any]] = []
    skipped_insufficient_ranked = 0
    skipped_constant_rank_input = 0
    for session_index, session_id in enumerate(sessions):
        if session_index < minimum_prior:
            continue
        decision_asof = boundaries[session_id]
        prior_session_ids = sessions[
            max(0, session_index - lookback_sessions):session_index
        ]
        evidence = frame.loc[
            frame["session_id"].isin(prior_session_ids)
            & (frame["exit_known_dt"] < decision_asof)
        ]
        if evidence.empty:
            continue
        prior_aggregates = (
            evidence.groupby(["family_id", "session_id"], as_index=False)
            .agg(
                session_mean_ticks=("net_pnl_ticks", "mean"),
                evidence_through=("exit_known_dt", "max"),
            )
        )
        grand_mean = float(prior_aggregates["session_mean_ticks"].mean())
        prior_family = (
            prior_aggregates.groupby("family_id", as_index=False)
            .agg(
                prior_family_sessions=("session_id", "nunique"),
                prior_family_mean_ticks=("session_mean_ticks", "mean"),
                evidence_through=("evidence_through", "max"),
            )
        )
        prior_family = prior_family.loc[
            prior_family["prior_family_sessions"] >= minimum_prior
        ].copy()
        if prior_family.empty:
            continue
        prior_family["shrinkage_score_ticks"] = (
            prior_family["prior_family_sessions"]
            * prior_family["prior_family_mean_ticks"]
            + prior_strength * grand_mean
        ) / (
            prior_family["prior_family_sessions"] + prior_strength
        )
        realized = family_session.loc[
            family_session["session_id"] == session_id,
            [
                "family_id",
                "outcomes",
                "mean_net_ticks",
                "total_net_ticks",
                "positive_outcomes",
            ],
        ]
        ranked = prior_family.merge(realized, on="family_id", how="inner")
        if len(ranked) < minimum_ranked:
            skipped_insufficient_ranked += 1
            continue
        ranked = ranked.sort_values(
            ["shrinkage_score_ticks", "family_id"],
            ascending=[False, True],
        ).reset_index(drop=True)
        ranked["rank"] = np.arange(1, len(ranked) + 1)
        top_count = max(1, ceil(top_fraction * len(ranked)))
        ranked["top_quintile"] = ranked["rank"] <= top_count
        if (
            ranked["shrinkage_score_ticks"].nunique() < 2
            or ranked["mean_net_ticks"].nunique() < 2
        ):
            skipped_constant_rank_input += 1
            continue
        rank_ic = ranked["shrinkage_score_ticks"].corr(
            ranked["mean_net_ticks"], method="spearman"
        )
        top = ranked.loc[ranked["top_quintile"]]
        top_mean = float(top["mean_net_ticks"].mean())
        all_mean = float(ranked["mean_net_ticks"].mean())
        top_positive_rate = 100.0 * float(
            (top["mean_net_ticks"] > 0.0).mean()
        )
        all_positive_rate = 100.0 * float(
            (ranked["mean_net_ticks"] > 0.0).mean()
        )
        window_start = prior_session_ids[0]
        window_end = prior_session_ids[-1]
        for record in ranked.to_dict("records"):
            family_id = str(record["family_id"])
            rank_rows.append(
                {
                    "schema_version": (
                        DYNAMIC_CAUSAL_LEARNABILITY_SCHEMA_VERSION
                    ),
                    "sequence": sequence,
                    "session_index": session_index,
                    "session_id": session_id,
                    "decision_asof": decision_asof,
                    "family_id": family_id,
                    **family_parts[family_id],
                    "prior_window_start_session": window_start,
                    "prior_window_end_session": window_end,
                    "prior_family_sessions": int(
                        record["prior_family_sessions"]
                    ),
                    "prior_family_mean_ticks": float(
                        record["prior_family_mean_ticks"]
                    ),
                    "prior_grand_mean_ticks": grand_mean,
                    "shrinkage_score_ticks": float(
                        record["shrinkage_score_ticks"]
                    ),
                    "rank": int(record["rank"]),
                    "ranked_families": len(ranked),
                    "top_quintile": bool(record["top_quintile"]),
                    "evidence_through": record["evidence_through"],
                    "realized_outcomes": int(record["outcomes"]),
                    "realized_mean_net_ticks": float(
                        record["mean_net_ticks"]
                    ),
                    "realized_total_net_ticks": float(
                        record["total_net_ticks"]
                    ),
                    "realized_positive_outcomes": int(
                        record["positive_outcomes"]
                    ),
                }
            )
        session_rows.append(
            {
                "schema_version": DYNAMIC_CAUSAL_LEARNABILITY_SCHEMA_VERSION,
                "sequence": sequence,
                "session_index": session_index,
                "session_id": session_id,
                "decision_asof": decision_asof,
                "ranked_families": len(ranked),
                "top_families": len(top),
                "rank_ic": float(rank_ic),
                "top_mean_net_ticks": top_mean,
                "all_mean_net_ticks": all_mean,
                "top_quintile_uplift_ticks": top_mean - all_mean,
                "top_positive_rate_pct": top_positive_rate,
                "all_positive_rate_pct": all_positive_rate,
                "positive_rate_uplift_pp": (
                    top_positive_rate - all_positive_rate
                ),
                "top_paper_outcomes": int(top["outcomes"].sum()),
                "top_paper_total_net_ticks": float(
                    top["total_net_ticks"].sum()
                ),
            }
        )

    safe_family_sessions = _json_safe(family_session_rows)
    safe_ranks = _json_safe(rank_rows)
    safe_sessions = _json_safe(session_rows)
    summary = _build_sequence_summary(
        sequence,
        sessions,
        safe_ranks,
        safe_sessions,
        skipped_insufficient_ranked=skipped_insufficient_ranked,
        skipped_constant_rank_input=skipped_constant_rank_input,
    )
    safe_source = _json_safe(dict(source_manifest or {}))
    manifest: Dict[str, Any] = {
        "schema_version": DYNAMIC_CAUSAL_LEARNABILITY_SCHEMA_VERSION,
        "research_phase": "dynamic_phase_4c",
        "research_classification": "causal_learnability_diagnostic",
        "sequence": sequence,
        "source_outcome_cube": {
            "schema_version": safe_source.get("schema_version"),
            "manifest_sha256": safe_source.get("manifest_sha256"),
            "outcome_cube_sha256": safe_source.get("outcome_cube_sha256"),
        },
        "configuration": {
            "payload": resolved,
            "sha256": _sha256_json(resolved),
        },
        "contracts": {
            "causal": True,
            "strict_evidence_rule": resolved["strict_evidence_rule"],
            "hindsight_oracle_used": False,
            "capacity_projection_used": False,
            "state_reset_per_sequence": True,
        },
        "counts": {
            "sessions": len(sessions),
            "families": len(family_parts),
            "family_session_rows": len(safe_family_sessions),
            "rank_rows": len(safe_ranks),
            "evaluated_sessions": len(safe_sessions),
            "skipped_insufficient_ranked_sessions": (
                skipped_insufficient_ranked
            ),
            "skipped_constant_rank_input_sessions": (
                skipped_constant_rank_input
            ),
        },
        "summary_sha256": _sha256_json(summary),
        "family_session_ledger_sha256": _sha256_json(
            safe_family_sessions
        ),
        "rank_ledger_sha256": _sha256_json(safe_ranks),
        "session_ledger_sha256": _sha256_json(safe_sessions),
    }
    manifest["manifest_sha256"] = _sha256_json(manifest)
    return {
        "manifest": manifest,
        "summary": summary,
        "family_session_rows": safe_family_sessions,
        "rank_rows": safe_ranks,
        "session_rows": safe_sessions,
    }


def build_causal_learnability_panel(
    sequence_results: Sequence[Mapping[str, Any]],
    *,
    gates: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Combine independently reset sequence audits and apply frozen gates."""
    resolved_gates = _resolve_gates(gates)
    summaries = [dict(result["summary"]) for result in sequence_results]
    sequences = [str(row["sequence"]) for row in summaries]
    if len(summaries) != 7 or len(set(sequences)) != 7:
        raise CausalLearnabilityError(
            "Phase 4C requires seven unique sequence results"
        )
    total_pairs = sum(int(row["family_session_pairs"]) for row in summaries)
    weighted_rank_ic = (
        sum(
            float(row["mean_rank_ic"])
            * int(row["family_session_pairs"])
            for row in summaries
        )
        / total_pairs
        if total_pairs
        else None
    )
    total_sessions = sum(int(row["evaluated_sessions"]) for row in summaries)
    mean_uplift = (
        sum(
            float(row["mean_top_quintile_uplift_ticks"])
            * int(row["evaluated_sessions"])
            for row in summaries
        )
        / total_sessions
        if total_sessions
        else None
    )
    top_positive = sum(int(row["top_positive_family_sessions"]) for row in summaries)
    top_count = sum(int(row["top_family_sessions"]) for row in summaries)
    all_positive = sum(int(row["all_positive_family_sessions"]) for row in summaries)
    all_count = sum(int(row["family_session_pairs"]) for row in summaries)
    positive_rate_uplift = (
        100.0 * top_positive / top_count
        - 100.0 * all_positive / all_count
        if top_count and all_count
        else None
    )
    contributions = [
        max(
            0.0,
            float(row["mean_top_quintile_uplift_ticks"])
            * int(row["evaluated_sessions"]),
        )
        for row in summaries
    ]
    positive_contribution = sum(contributions)
    largest_share = (
        100.0 * max(contributions) / positive_contribution
        if positive_contribution > 0.0
        else None
    )
    positive_rank_sequences = sum(
        float(row["mean_rank_ic"]) > 0.0 for row in summaries
    )
    uplift_sequences = sum(
        float(row["mean_top_quintile_uplift_ticks"])
        >= float(resolved_gates["minimum_sequence_mean_uplift_ticks"])
        for row in summaries
    )
    criteria = {
        "minimum_sessions_each_sequence": all(
            int(row["evaluated_sessions"])
            >= int(
                resolved_gates[
                    "minimum_evaluated_sessions_each_sequence"
                ]
            )
            for row in summaries
        ),
        "positive_rank_ic_sequences": (
            positive_rank_sequences
            >= int(resolved_gates["minimum_positive_rank_ic_sequences"])
        ),
        "pair_weighted_rank_ic": (
            weighted_rank_ic is not None
            and weighted_rank_ic
            >= float(resolved_gates["minimum_pair_weighted_rank_ic"])
        ),
        "uplift_sequences": (
            uplift_sequences
            >= int(resolved_gates["minimum_uplift_sequences"])
        ),
        "cross_sequence_mean_uplift": (
            mean_uplift is not None
            and mean_uplift
            >= float(
                resolved_gates[
                    "minimum_cross_sequence_mean_uplift_ticks"
                ]
            )
        ),
        "cross_sequence_positive_rate_uplift": (
            positive_rate_uplift is not None
            and positive_rate_uplift
            >= float(
                resolved_gates[
                    "minimum_cross_sequence_positive_rate_uplift_pp"
                ]
            )
        ),
        "positive_uplift_not_concentrated": (
            largest_share is not None
            and largest_share
            < float(
                resolved_gates[
                    "maximum_positive_uplift_sequence_share_pct"
                ]
            )
        ),
    }
    passed = all(criteria.values())
    return _json_safe(
        {
            "schema_version": DYNAMIC_CAUSAL_LEARNABILITY_SCHEMA_VERSION,
            "research_phase": "dynamic_phase_4c",
            "research_classification": "causal_learnability_diagnostic",
            "sequence_summaries": summaries,
            "panel": {
                "sequences": len(summaries),
                "evaluated_sessions": total_sessions,
                "family_session_pairs": total_pairs,
                "positive_rank_ic_sequences": positive_rank_sequences,
                "pair_weighted_mean_rank_ic": weighted_rank_ic,
                "uplift_sequences": uplift_sequences,
                "cross_sequence_mean_top_quintile_uplift_ticks": mean_uplift,
                "cross_sequence_top_positive_rate_pct": (
                    100.0 * top_positive / top_count if top_count else None
                ),
                "cross_sequence_all_positive_rate_pct": (
                    100.0 * all_positive / all_count if all_count else None
                ),
                "cross_sequence_positive_rate_uplift_pp": (
                    positive_rate_uplift
                ),
                "largest_positive_uplift_sequence_share_pct": largest_share,
            },
            "gates": {
                "thresholds": resolved_gates,
                "criteria": criteria,
                "passed": passed,
            },
            "result_label": (
                "CAUSALLY_LEARNABLE"
                if passed
                else "WEAK_OR_INCONSISTENT_CAUSAL_LEARNABILITY"
            ),
            "selector_authorized": passed,
            "family_b_authorized": False,
            "forward_paper_authorized": False,
        }
    )


def write_causal_learnability_audit(
    output_dir: Path,
    result: Mapping[str, Any],
    *,
    overwrite: bool = False,
) -> Dict[str, str]:
    """Write one sequence or panel audit bundle."""
    root = Path(output_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    is_panel = "sequence_results" in result
    if is_panel:
        paths = {
            "summary": root / "learnability_panel_summary.json",
            "family_session_ledger": root / "family_session_ledger.csv",
            "rank_ledger": root / "causal_rank_ledger.csv",
            "session_ledger": root / "session_metrics.csv",
            "manifest": root / "learnability_manifest.json",
        }
        summary = result["summary"]
        family_rows = [
            row
            for child in result["sequence_results"]
            for row in child["family_session_rows"]
        ]
        rank_rows = [
            row
            for child in result["sequence_results"]
            for row in child["rank_rows"]
        ]
        session_rows = [
            row
            for child in result["sequence_results"]
            for row in child["session_rows"]
        ]
    else:
        paths = {
            "summary": root / "learnability_sequence_summary.json",
            "family_session_ledger": root / "family_session_ledger.csv",
            "rank_ledger": root / "causal_rank_ledger.csv",
            "session_ledger": root / "session_metrics.csv",
            "manifest": root / "learnability_manifest.json",
        }
        summary = result["summary"]
        family_rows = result["family_session_rows"]
        rank_rows = result["rank_rows"]
        session_rows = result["session_rows"]
    existing = [path for path in paths.values() if path.exists()]
    if existing and not overwrite:
        raise FileExistsError(
            "refusing to overwrite causal learnability artifacts: "
            + ", ".join(str(path) for path in existing)
        )
    _write_json(paths["summary"], summary)
    _write_csv(paths["family_session_ledger"], family_rows, FAMILY_SESSION_COLUMNS)
    _write_csv(paths["rank_ledger"], rank_rows, RANK_COLUMNS)
    _write_csv(paths["session_ledger"], session_rows, SESSION_COLUMNS)
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
    return {name: str(path) for name, path in paths.items()}


def _build_family_session_rows(
    frame: pd.DataFrame,
    *,
    sequence: str,
    sessions: Sequence[str],
    family_parts: Mapping[str, Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    session_index = {session: index for index, session in enumerate(sessions)}
    rows: List[Dict[str, Any]] = []
    grouped = frame.groupby(["session_id", "family_id"], sort=True)
    for (session_id, family_id), group in grouped:
        pnls = group["net_pnl_ticks"].astype(float)
        rows.append(
            {
                "schema_version": DYNAMIC_CAUSAL_LEARNABILITY_SCHEMA_VERSION,
                "sequence": sequence,
                "session_index": session_index[str(session_id)],
                "session_id": str(session_id),
                "family_id": str(family_id),
                **family_parts[str(family_id)],
                "outcomes": len(group),
                "mean_net_ticks": float(pnls.mean()),
                "total_net_ticks": float(pnls.sum()),
                "positive_outcomes": int((pnls > 0.0).sum()),
                "positive_rate_pct": 100.0 * float((pnls > 0.0).mean()),
                "evidence_through": group["exit_known_dt"].max(),
            }
        )
    return rows


def _build_sequence_summary(
    sequence: str,
    sessions: Sequence[str],
    rank_rows: Sequence[Mapping[str, Any]],
    session_rows: Sequence[Mapping[str, Any]],
    *,
    skipped_insufficient_ranked: int,
    skipped_constant_rank_input: int,
) -> Dict[str, Any]:
    rank_ics = [float(row["rank_ic"]) for row in session_rows]
    uplifts = [
        float(row["top_quintile_uplift_ticks"]) for row in session_rows
    ]
    top_rows = [row for row in rank_rows if bool(row["top_quintile"])]
    return _json_safe(
        {
            "schema_version": DYNAMIC_CAUSAL_LEARNABILITY_SCHEMA_VERSION,
            "sequence": sequence,
            "sessions": len(sessions),
            "session_start": sessions[0],
            "session_end": sessions[-1],
            "evaluated_sessions": len(session_rows),
            "skipped_insufficient_ranked_sessions": (
                skipped_insufficient_ranked
            ),
            "skipped_constant_rank_input_sessions": (
                skipped_constant_rank_input
            ),
            "family_session_pairs": len(rank_rows),
            "top_family_sessions": len(top_rows),
            "mean_rank_ic": float(np.mean(rank_ics)) if rank_ics else None,
            "median_rank_ic": median(rank_ics) if rank_ics else None,
            "positive_rank_ic_sessions": sum(value > 0.0 for value in rank_ics),
            "positive_rank_ic_session_pct": (
                100.0 * sum(value > 0.0 for value in rank_ics) / len(rank_ics)
                if rank_ics
                else None
            ),
            "mean_top_quintile_uplift_ticks": (
                float(np.mean(uplifts)) if uplifts else None
            ),
            "median_top_quintile_uplift_ticks": (
                median(uplifts) if uplifts else None
            ),
            "positive_uplift_sessions": sum(value > 0.0 for value in uplifts),
            "positive_uplift_session_pct": (
                100.0 * sum(value > 0.0 for value in uplifts) / len(uplifts)
                if uplifts
                else None
            ),
            "top_positive_family_sessions": sum(
                float(row["realized_mean_net_ticks"]) > 0.0 for row in top_rows
            ),
            "all_positive_family_sessions": sum(
                float(row["realized_mean_net_ticks"]) > 0.0 for row in rank_rows
            ),
            "top_paper_outcomes": sum(
                int(row["top_paper_outcomes"]) for row in session_rows
            ),
            "top_paper_total_net_ticks": sum(
                float(row["top_paper_total_net_ticks"])
                for row in session_rows
            ),
        }
    )


def _canonical_rows(
    rows: Sequence[Mapping[str, Any]] | pd.DataFrame,
) -> pd.DataFrame:
    frame = rows.copy() if isinstance(rows, pd.DataFrame) else pd.DataFrame(rows)
    required = {
        "sequence",
        "session_id",
        "timeframe",
        "lookback",
        "multiplier",
        "mode",
        "signal_dt",
        "exit_known_dt",
        "net_pnl_ticks",
        "capacity_eligible",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise CausalLearnabilityError(
            "outcome cube is missing learnability columns: "
            + ", ".join(missing)
        )
    if frame.empty:
        raise CausalLearnabilityError(
            "learnability audit requires outcome rows"
        )
    out = frame.loc[:, sorted(required)].copy()
    out["sequence"] = out["sequence"].astype(str)
    out["session_id"] = out["session_id"].astype(str)
    out["mode"] = out["mode"].astype(str)
    for column in ("signal_dt", "exit_known_dt"):
        out[column] = pd.to_datetime(out[column], errors="raise", utc=True)
        out[column] = out[column].dt.tz_convert("America/Denver")
    for column in ("timeframe", "lookback"):
        out[column] = pd.to_numeric(out[column], errors="raise").astype(int)
    out["multiplier"] = pd.to_numeric(
        out["multiplier"], errors="raise"
    ).astype(float)
    out["net_pnl_ticks"] = pd.to_numeric(
        out["net_pnl_ticks"], errors="raise"
    ).astype(float)
    if not np.isfinite(
        out[["multiplier", "net_pnl_ticks"]].to_numpy()
    ).all():
        raise CausalLearnabilityError(
            "learnability numeric inputs must be finite"
        )
    out["capacity_eligible"] = out["capacity_eligible"].map(_truth_value)
    out = out.loc[out["capacity_eligible"]].copy()
    if out.empty:
        raise CausalLearnabilityError(
            "learnability audit requires capacity-eligible paper outcomes"
        )
    out["family_id"] = out.apply(
        lambda row: _family_id(
            int(row["timeframe"]),
            int(row["lookback"]),
            float(row["multiplier"]),
            str(row["mode"]),
        ),
        axis=1,
    )
    return out.sort_values(
        ["signal_dt", "session_id", "family_id", "exit_known_dt"]
    ).reset_index(drop=True)


def _family_index(frame: pd.DataFrame) -> Dict[str, Dict[str, Any]]:
    output: Dict[str, Dict[str, Any]] = {}
    fields = ("timeframe", "lookback", "multiplier", "mode")
    for family_id, group in frame.groupby("family_id", sort=True):
        payload: Dict[str, Any] = {}
        for field in fields:
            values = group[field].unique().tolist()
            if len(values) != 1:
                raise CausalLearnabilityError(
                    f"family {family_id} has inconsistent {field}"
                )
            payload[field] = values[0]
        output[str(family_id)] = _json_safe(payload)
    return output


def _session_clock(
    frame: pd.DataFrame,
) -> Tuple[List[str], Dict[str, pd.Timestamp]]:
    first = (
        frame.groupby("session_id", sort=False)["signal_dt"].min().sort_values()
    )
    sessions = [str(value) for value in first.index]
    boundaries = {
        str(session_id): pd.Timestamp(decision_asof)
        for session_id, decision_asof in first.items()
    }
    return sessions, boundaries


def _family_id(
    timeframe: int,
    lookback: int,
    multiplier: float,
    mode: str,
) -> str:
    multiplier_text = f"{multiplier:g}"
    return (
        f"family:v1:tf{timeframe}m|lb{lookback}|"
        f"x{multiplier_text}|{mode}"
    )


def _resolve_config(config: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    resolved = json.loads(json.dumps(DEFAULT_CAUSAL_LEARNABILITY_CONFIG))
    resolved.update(dict(config or {}))
    if resolved != DEFAULT_CAUSAL_LEARNABILITY_CONFIG:
        raise CausalLearnabilityError(
            "Dynamic Phase 4C freezes the causal learnability configuration"
        )
    return resolved


def _resolve_gates(gates: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    resolved = json.loads(json.dumps(DEFAULT_CAUSAL_LEARNABILITY_GATES))
    resolved.update(dict(gates or {}))
    if resolved != DEFAULT_CAUSAL_LEARNABILITY_GATES:
        raise CausalLearnabilityError(
            "Dynamic Phase 4C freezes the interpretation gates"
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
    raise CausalLearnabilityError(
        f"invalid capacity_eligible value: {value!r}"
    )


def _write_csv(
    path: Path,
    rows: Sequence[Mapping[str, Any]],
    columns: Sequence[str],
) -> None:
    expected = set(columns)
    for index, row in enumerate(rows):
        if set(row) != expected:
            raise CausalLearnabilityError(
                f"learnability row {index} violates stable schema; "
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
    if isinstance(value, float):
        if not isfinite(value):
            return None
        return value
    if value is pd.NA:
        return None
    return value
