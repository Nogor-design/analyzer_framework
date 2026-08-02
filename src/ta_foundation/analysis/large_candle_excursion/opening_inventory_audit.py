from __future__ import annotations

"""Causal opening-inventory acceptance audit for fresh large candles."""

import hashlib
import json
from math import isfinite
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

import numpy as np
import pandas as pd

from ta_foundation.core.manifest import sha256_file


SCHEMA_VERSION = "opening_inventory_audit.v1"
DEVELOPMENT_SEQUENCES = [
    "NQ 03-26", "NQ 06-26", "NQ 09-26", "ES 03-26",
    "RTY 03-26", "YM 03-26", "YM 06-26",
]
TRANSFER_SEQUENCES = ["GC 04-26", "NG 03-26"]
SENSITIVITY_SEQUENCES = ["MNQ 03-26"]
POLICIES = [
    "acceptance", "always_continuation", "always_reversion", "opposite_mapping"
]
CONFIG: Dict[str, Any] = {
    "timeframe": 1,
    "lookback": 5,
    "multiplier": 1.5,
    "modes": ["continuation", "reversion"],
    "trigger_type": "fresh",
    "window_start": "07:30",
    "window_end": "09:29",
    "prior_cash_close": "13:59",
    "preopen_close": "07:29",
    "maximum_prior_close_age_days": 4,
    "block_sessions": 5,
    "capacity_per_direction": 3,
}
GATES: Dict[str, Any] = {
    "minimum_opportunities_each_development_sequence": 50,
    "minimum_sessions_each_development_sequence": 20,
    "minimum_positive_paired_sequences": 5,
    "minimum_sequence_equal_paired_uplift_ticks": 2.5,
    "minimum_positive_capacity_sequences": 5,
    "minimum_positive_non_nq_instruments": 2,
    "minimum_sequence_equal_capacity_mean_ticks": 2.5,
    "minimum_positive_block_rate_pct": 55.0,
    "minimum_static_beats": 5,
    "maximum_positive_capacity_sequence_share_pct": 50.0,
}


class OpeningInventoryAuditError(ValueError):
    pass


def run_opening_inventory_audit(
    sequence_rows: Mapping[str, Sequence[Mapping[str, Any]] | pd.DataFrame],
    sequence_bars: Mapping[str, pd.DataFrame],
    *,
    source_manifests: Optional[Mapping[str, Mapping[str, Any]]] = None,
    raw_source_registry: Optional[Mapping[str, Mapping[str, Any]]] = None,
) -> Dict[str, Any]:
    expected = set(DEVELOPMENT_SEQUENCES + TRANSFER_SEQUENCES + SENSITIVITY_SEQUENCES)
    if set(sequence_rows) != expected or set(sequence_bars) != expected:
        raise OpeningInventoryAuditError("opening audit requires the frozen ten sequences")
    registry = dict(raw_source_registry or {})
    if set(registry) != expected:
        raise OpeningInventoryAuditError("complete raw source registry is required")

    opportunity_frames: List[pd.DataFrame] = []
    state_rows: List[Dict[str, Any]] = []
    for sequence in DEVELOPMENT_SEQUENCES + TRANSFER_SEQUENCES + SENSITIVITY_SEQUENCES:
        rows = _canonical_rows(sequence_rows[sequence], sequence)
        paired = _pair_modes(rows)
        states = _overnight_states(
            sequence_bars[sequence],
            sequence,
            paired["session_id"].unique().tolist(),
            float(registry[sequence]["tick_size"]),
        )
        state_rows.extend(states)
        state = pd.DataFrame(states)
        paired = paired.merge(state, on=["sequence", "session_id"], validate="many_to_one")
        paired = paired.loc[paired["state_eligible"]].copy()
        if paired.empty:
            raise OpeningInventoryAuditError(f"no eligible overnight states for {sequence}")
        if (paired["overnight_evidence_dt"] >= paired["signal_dt"]).any():
            raise OpeningInventoryAuditError("overnight evidence is not strictly pre-signal")
        paired["aligned"] = paired["signal_direction"] == paired["overnight_direction"]
        paired["primary_mode"] = np.where(paired["aligned"], "continuation", "reversion")
        paired["primary_reward_ticks"] = np.where(
            paired["aligned"], paired["continuation_reward_ticks"], paired["reversion_reward_ticks"]
        )
        paired["counterfactual_reward_ticks"] = np.where(
            paired["aligned"], paired["reversion_reward_ticks"], paired["continuation_reward_ticks"]
        )
        paired["paired_uplift_ticks"] = (
            paired["primary_reward_ticks"] - paired["counterfactual_reward_ticks"]
        )
        opportunity_frames.append(paired)

    opportunities = pd.concat(opportunity_frames, ignore_index=True)
    opportunities = _attach_session_indices(opportunities)
    policy_rows = _expand_policies(opportunities)
    capacity_rows = _apply_capacity(policy_rows)
    sequence_policy_rows, block_rows = _policy_summaries(capacity_rows)
    mechanism_rows = _mechanism_summaries(opportunities)
    decision = _evaluate_gates(sequence_policy_rows, mechanism_rows, block_rows)

    safe = {
        "opportunity_rows": _json_safe(opportunities.to_dict("records")),
        "session_state_rows": _json_safe(state_rows),
        "capacity_rows": _json_safe(capacity_rows.to_dict("records")),
        "sequence_policy_rows": _json_safe(sequence_policy_rows),
        "mechanism_rows": _json_safe(mechanism_rows),
        "block_rows": _json_safe(block_rows),
    }
    summary = _json_safe({
        "schema_version": SCHEMA_VERSION,
        "research_phase": "opening_inventory_acceptance",
        "panel": {
            "sequences": 10,
            "eligible_opportunities": len(opportunities),
            "policy_capacity_rows": len(capacity_rows),
            "complete_policy_blocks": len(block_rows),
        },
        "development": decision["development"],
        "transfer": decision["transfer"],
        "mechanism_sequence_summaries": mechanism_rows,
        "result_label": decision["development"]["result_label"],
        "transfer_label": decision["transfer"]["result_label"],
        "later_confirmation_authorized": decision["development"]["gates"]["passed"],
        "forward_paper_authorized": False,
        "family_b_authorized": False,
    })
    sources = {
        sequence: {
            "manifest_sha256": manifest.get("manifest_sha256"),
            "outcome_cube_sha256": manifest.get("outcome_cube_sha256"),
        }
        for sequence, manifest in (source_manifests or {}).items()
    }
    manifest: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "research_phase": "opening_inventory_acceptance",
        "source_outcome_cubes": sources,
        "raw_source_registry": _json_safe(registry),
        "configuration": {"payload": CONFIG, "sha256": _sha256_json(CONFIG)},
        "gates": {"payload": GATES, "sha256": _sha256_json(GATES)},
        "contracts": {
            "paired_modes": True,
            "strictly_prior_overnight_evidence": True,
            "one_policy_entry_per_physical_opportunity": True,
            "capacity_per_policy": True,
            "controls_cannot_replace_primary": True,
            "forward_paper_authorized": False,
        },
        "counts": {key: len(value) for key, value in safe.items()},
        "summary_sha256": _sha256_json(summary),
        **{f"{key}_sha256": _sha256_json(value) for key, value in safe.items()},
    }
    manifest["manifest_sha256"] = _sha256_json(manifest)
    return {"manifest": manifest, "summary": summary, **safe}


def write_opening_inventory_audit(output_dir: Path, result: Mapping[str, Any]) -> Dict[str, str]:
    root = Path(output_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    definitions = {
        "summary": ("opening_inventory_summary.json", "json", "summary"),
        "opportunities": ("paired_opportunity_ledger.csv", "csv", "opportunity_rows"),
        "states": ("overnight_session_states.csv", "csv", "session_state_rows"),
        "capacity": ("policy_capacity_ledger.csv", "csv", "capacity_rows"),
        "policies": ("sequence_policy_summary.csv", "csv", "sequence_policy_rows"),
        "mechanism": ("mechanism_sequence_summary.csv", "csv", "mechanism_rows"),
        "blocks": ("five_session_policy_blocks.csv", "csv", "block_rows"),
        "manifest": ("opening_inventory_manifest.json", "json", "manifest"),
    }
    paths = {name: root / spec[0] for name, spec in definitions.items()}
    if any(path.exists() for path in paths.values()):
        raise FileExistsError(f"refusing to overwrite opening-inventory artifacts: {root}")
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


def _canonical_rows(rows: Sequence[Mapping[str, Any]] | pd.DataFrame, sequence: str) -> pd.DataFrame:
    frame = rows.copy() if isinstance(rows, pd.DataFrame) else pd.DataFrame(rows)
    required = {
        "sequence", "physical_opportunity_id", "lane_event_id", "session_id",
        "signal_dt", "entry_dt", "exit_known_dt", "signal_direction", "mode",
        "trade_direction", "net_pnl_ticks", "timeframe", "lookback", "multiplier",
        "trigger_type",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise OpeningInventoryAuditError("opening cube missing: " + ", ".join(missing))
    out = frame.loc[:, sorted(required)].copy()
    if out["sequence"].astype(str).unique().tolist() != [sequence]:
        raise OpeningInventoryAuditError(f"sequence mismatch for {sequence}")
    for column in ("signal_dt", "entry_dt", "exit_known_dt"):
        out[column] = pd.to_datetime(out[column], errors="raise", utc=True).dt.tz_convert("America/Denver")
    for column in ("timeframe", "lookback", "signal_direction", "trade_direction"):
        out[column] = pd.to_numeric(out[column], errors="raise").astype(int)
    for column in ("multiplier", "net_pnl_ticks"):
        out[column] = pd.to_numeric(out[column], errors="raise").astype(float)
    if not np.isfinite(out[["multiplier", "net_pnl_ticks"]].to_numpy()).all():
        raise OpeningInventoryAuditError("opening numeric inputs must be finite")
    if set(out["timeframe"]) != {CONFIG["timeframe"]} or set(out["lookback"]) != {CONFIG["lookback"]}:
        raise OpeningInventoryAuditError("opening timeframe/lookback catalog changed")
    if set(out["multiplier"]) != {CONFIG["multiplier"]}:
        raise OpeningInventoryAuditError("opening multiplier catalog changed")
    if set(out["mode"].astype(str)) != set(CONFIG["modes"]):
        raise OpeningInventoryAuditError("opening mode catalog changed")
    if set(out["trigger_type"].astype(str)) != {CONFIG["trigger_type"]}:
        raise OpeningInventoryAuditError("opening trigger catalog changed")
    minute = out["signal_dt"].dt.hour * 60 + out["signal_dt"].dt.minute
    if ((minute < 450) | (minute > 569)).any():
        raise OpeningInventoryAuditError("signal outside frozen opening window")
    if (out["entry_dt"] <= out["signal_dt"]).any() or (out["exit_known_dt"] <= out["entry_dt"]).any():
        raise OpeningInventoryAuditError("invalid causal outcome timestamps")
    if out.duplicated(["physical_opportunity_id", "mode"]).any():
        raise OpeningInventoryAuditError("duplicate physical-opportunity mode")
    return out.sort_values(["signal_dt", "physical_opportunity_id", "mode"]).reset_index(drop=True)


def _pair_modes(frame: pd.DataFrame) -> pd.DataFrame:
    records: List[Dict[str, Any]] = []
    for physical_id, group in frame.groupby("physical_opportunity_id", sort=True):
        if len(group) != 2 or set(group["mode"]) != set(CONFIG["modes"]):
            raise OpeningInventoryAuditError(f"missing paired mode for {physical_id}")
        invariant = ["sequence", "lane_event_id", "session_id", "signal_dt", "entry_dt", "signal_direction"]
        if any(group[column].nunique(dropna=False) != 1 for column in invariant):
            raise OpeningInventoryAuditError(f"paired-mode identity mismatch for {physical_id}")
        by_mode = {str(row["mode"]): row for row in group.to_dict("records")}
        cont, rev = by_mode["continuation"], by_mode["reversion"]
        signal_direction = int(cont["signal_direction"])
        if int(cont["trade_direction"]) != signal_direction or int(rev["trade_direction"]) != -signal_direction:
            raise OpeningInventoryAuditError("mode direction semantics changed")
        records.append({
            "schema_version": SCHEMA_VERSION,
            "sequence": cont["sequence"],
            "physical_opportunity_id": physical_id,
            "lane_event_id": cont["lane_event_id"],
            "session_id": str(cont["session_id"]),
            "signal_dt": cont["signal_dt"],
            "entry_dt": cont["entry_dt"],
            "signal_direction": signal_direction,
            "continuation_reward_ticks": float(cont["net_pnl_ticks"]),
            "continuation_direction": int(cont["trade_direction"]),
            "continuation_exit_known_dt": cont["exit_known_dt"],
            "reversion_reward_ticks": float(rev["net_pnl_ticks"]),
            "reversion_direction": int(rev["trade_direction"]),
            "reversion_exit_known_dt": rev["exit_known_dt"],
        })
    return pd.DataFrame(records)


def _overnight_states(
    bars: pd.DataFrame,
    sequence: str,
    sessions: Sequence[str],
    tick_size: float,
) -> List[Dict[str, Any]]:
    if tick_size <= 0:
        raise OpeningInventoryAuditError("tick size must be positive")
    required = {"dt", "close"}
    if not required.issubset(bars.columns):
        raise OpeningInventoryAuditError("bars require dt and close")
    frame = bars.loc[:, ["dt", "close"]].copy()
    frame["dt"] = pd.to_datetime(frame["dt"], errors="raise", utc=True).dt.tz_convert("America/Denver")
    frame["close"] = pd.to_numeric(frame["close"], errors="raise").astype(float)
    frame = frame.sort_values("dt").drop_duplicates("dt", keep="last")
    frame["date"] = frame["dt"].dt.date
    frame["clock"] = frame["dt"].dt.strftime("%H:%M")
    preopen = frame.loc[frame["clock"] == CONFIG["preopen_close"]].set_index("date")
    cash = frame.loc[frame["clock"] == CONFIG["prior_cash_close"]].set_index("date")
    cash_dates = sorted(cash.index.unique())
    records: List[Dict[str, Any]] = []
    for session_value in sorted(set(str(value) for value in sessions)):
        session_date = pd.Timestamp(session_value).date()
        pre = preopen.loc[[session_date]].iloc[-1] if session_date in preopen.index else None
        prior_dates = [value for value in cash_dates if value < session_date]
        prior_date = prior_dates[-1] if prior_dates else None
        age = (session_date - prior_date).days if prior_date is not None else None
        prior = cash.loc[[prior_date]].iloc[-1] if prior_date is not None else None
        eligible = pre is not None and prior is not None and age is not None and age <= int(CONFIG["maximum_prior_close_age_days"])
        overnight_ticks = (
            (float(pre["close"]) - float(prior["close"])) / tick_size if eligible else None
        )
        direction = int(np.sign(overnight_ticks)) if eligible else 0
        records.append({
            "schema_version": SCHEMA_VERSION,
            "sequence": sequence,
            "session_id": session_value,
            "prior_cash_close_dt": prior["dt"] if prior is not None else None,
            "prior_cash_close": float(prior["close"]) if prior is not None else None,
            "preopen_close_dt": pre["dt"] if pre is not None else None,
            "preopen_close": float(pre["close"]) if pre is not None else None,
            "overnight_evidence_dt": pre["dt"] if pre is not None else None,
            "prior_close_age_days": age,
            "overnight_ticks": overnight_ticks,
            "overnight_direction": direction,
            "state_eligible": bool(eligible and direction != 0),
        })
    return records


def _attach_session_indices(frame: pd.DataFrame) -> pd.DataFrame:
    first = frame.groupby(["sequence", "session_id"])["signal_dt"].min().reset_index()
    first = first.sort_values(["sequence", "signal_dt"])
    first["session_index"] = first.groupby("sequence").cumcount()
    first["block_index"] = first["session_index"] // int(CONFIG["block_sessions"])
    counts = first.groupby(["sequence", "block_index"])["session_id"].transform("nunique")
    first["complete_block"] = counts == int(CONFIG["block_sessions"])
    return frame.merge(
        first[["sequence", "session_id", "session_index", "block_index", "complete_block"]],
        on=["sequence", "session_id"],
        validate="many_to_one",
    )


def _expand_policies(opportunities: pd.DataFrame) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    for row in opportunities.to_dict("records"):
        primary = str(row["primary_mode"])
        selections = {
            "acceptance": primary,
            "always_continuation": "continuation",
            "always_reversion": "reversion",
            "opposite_mapping": "reversion" if primary == "continuation" else "continuation",
        }
        for policy, mode in selections.items():
            rows.append({
                "schema_version": SCHEMA_VERSION,
                "sequence": row["sequence"], "policy": policy,
                "physical_opportunity_id": row["physical_opportunity_id"],
                "session_id": row["session_id"], "session_index": int(row["session_index"]),
                "block_index": int(row["block_index"]), "complete_block": bool(row["complete_block"]),
                "signal_dt": row["signal_dt"], "entry_dt": row["entry_dt"],
                "selected_mode": mode,
                "trade_direction": int(row[f"{mode}_direction"]),
                "exit_known_dt": row[f"{mode}_exit_known_dt"],
                "paper_net_ticks": float(row[f"{mode}_reward_ticks"]),
            })
    return pd.DataFrame(rows)


def _apply_capacity(policy_rows: pd.DataFrame) -> pd.DataFrame:
    ledger: List[Dict[str, Any]] = []
    for (sequence, policy), group in policy_rows.groupby(["sequence", "policy"], sort=True):
        if group["physical_opportunity_id"].duplicated().any():
            raise OpeningInventoryAuditError("duplicate physical opportunity within policy")
        active = {1: [], -1: []}
        cumulative = 0.0
        for row in group.sort_values(["entry_dt", "physical_opportunity_id"]).to_dict("records"):
            direction = int(row["trade_direction"])
            entry = pd.Timestamp(row["entry_dt"])
            active[direction] = [value for value in active[direction] if value > entry]
            executed = len(active[direction]) < int(CONFIG["capacity_per_direction"])
            net = float(row["paper_net_ticks"]) if executed else 0.0
            if executed:
                active[direction].append(pd.Timestamp(row["exit_known_dt"]))
                cumulative += net
            ledger.append({**row, "executed": executed, "capacity_skip": not executed,
                           "capacity_net_ticks": net, "cumulative_net_ticks": cumulative})
    return pd.DataFrame(ledger)


def _reward_metrics(values: pd.Series) -> Dict[str, Any]:
    rewards = values.astype(float)
    gains = float(rewards[rewards > 0].sum())
    losses = abs(float(rewards[rewards < 0].sum()))
    equity = rewards.cumsum()
    drawdown = equity.cummax().clip(lower=0) - equity
    return {
        "total_net_ticks": float(rewards.sum()),
        "mean_net_ticks": float(rewards.mean()) if len(rewards) else None,
        "profit_factor": gains / losses if losses else None,
        "positive_rate_pct": 100.0 * float((rewards > 0).mean()) if len(rewards) else None,
        "max_drawdown_ticks": float(drawdown.max()) if len(drawdown) else 0.0,
    }


def _policy_summaries(capacity: pd.DataFrame) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    summaries: List[Dict[str, Any]] = []
    blocks: List[Dict[str, Any]] = []
    for (sequence, policy), group in capacity.groupby(["sequence", "policy"], sort=True):
        group = group.sort_values(["entry_dt", "physical_opportunity_id"])
        executed = group.loc[group["executed"]]
        midpoint = (int(group["session_index"].max()) + 1) / 2.0
        first = group.loc[group["session_index"] < midpoint]
        second = group.loc[group["session_index"] >= midpoint]
        days = group.groupby("session_id")["capacity_net_ticks"].sum()
        positive_days = days.clip(lower=0)
        concentration = (
            100.0 * float(positive_days.max()) / float(positive_days.sum())
            if float(positive_days.sum()) > 0 else None
        )
        metrics = _reward_metrics(executed["capacity_net_ticks"])
        summaries.append({
            "schema_version": SCHEMA_VERSION, "sequence": sequence, "policy": policy,
            "opportunities": len(group), "sessions": int(group["session_id"].nunique()),
            "paper_mean_net_ticks": float(group["paper_net_ticks"].mean()),
            "executed_trades": len(executed), "capacity_skips": int(group["capacity_skip"].sum()),
            **{f"capacity_{key}": value for key, value in metrics.items()},
            "capacity_ticks_per_opportunity": float(group["capacity_net_ticks"].sum()) / len(group),
            "first_half_capacity_net_ticks": float(first["capacity_net_ticks"].sum()),
            "second_half_capacity_net_ticks": float(second["capacity_net_ticks"].sum()),
            "largest_profitable_day_share_pct": concentration,
        })
        complete = group.loc[group["complete_block"]]
        for block_index, block in complete.groupby("block_index", sort=True):
            block_executed = block.loc[block["executed"]]
            blocks.append({
                "schema_version": SCHEMA_VERSION, "sequence": sequence, "policy": policy,
                "block_index": int(block_index), "sessions": int(block["session_id"].nunique()),
                "opportunities": len(block), "executed_trades": len(block_executed),
                "capacity_net_ticks": float(block["capacity_net_ticks"].sum()),
                "capacity_ticks_per_opportunity": float(block["capacity_net_ticks"].sum()) / len(block),
                "positive": float(block["capacity_net_ticks"].sum()) > 0,
            })
    return summaries, blocks


def _mechanism_summaries(opportunities: pd.DataFrame) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for sequence, group in opportunities.groupby("sequence", sort=True):
        group = group.sort_values(["signal_dt", "physical_opportunity_id"])
        midpoint = (int(group["session_index"].max()) + 1) / 2.0
        aligned = group.loc[group["aligned"]]
        opposed = group.loc[~group["aligned"]]
        session_states = group.drop_duplicates("session_id").sort_values("signal_dt")
        rows.append({
            "schema_version": SCHEMA_VERSION, "sequence": sequence,
            "opportunities": len(group), "sessions": int(group["session_id"].nunique()),
            "paired_uplift_ticks": float(group["paired_uplift_ticks"].mean()),
            "positive_pair_rate_pct": 100.0 * float((group["paired_uplift_ticks"] > 0).mean()),
            "aligned_opportunities": len(aligned), "opposed_opportunities": len(opposed),
            "aligned_paired_uplift_ticks": float(aligned["paired_uplift_ticks"].mean()) if len(aligned) else None,
            "opposed_paired_uplift_ticks": float(opposed["paired_uplift_ticks"].mean()) if len(opposed) else None,
            "continuation_share_pct": 100.0 * float((group["primary_mode"] == "continuation").mean()),
            "mode_switches": int((group["primary_mode"] != group["primary_mode"].shift()).iloc[1:].sum()),
            "overnight_state_changes": int((session_states["overnight_direction"] != session_states["overnight_direction"].shift()).iloc[1:].sum()),
            "first_half_paired_uplift_ticks": float(group.loc[group["session_index"] < midpoint, "paired_uplift_ticks"].mean()),
            "second_half_paired_uplift_ticks": float(group.loc[group["session_index"] >= midpoint, "paired_uplift_ticks"].mean()),
        })
    return rows


def _evaluate_gates(
    policy_rows: Sequence[Mapping[str, Any]],
    mechanism_rows: Sequence[Mapping[str, Any]],
    block_rows: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    policies = {(row["sequence"], row["policy"]): row for row in policy_rows}
    mechanisms = {row["sequence"]: row for row in mechanism_rows}
    development_mechanisms = [mechanisms[name] for name in DEVELOPMENT_SEQUENCES]
    primary = [policies[(name, "acceptance")] for name in DEVELOPMENT_SEQUENCES]
    capacity_passes = [
        row for row in primary
        if row["capacity_total_net_ticks"] > 0
        and row["capacity_profit_factor"] is not None
        and row["capacity_profit_factor"] > 1.0
    ]
    non_nq = {str(row["sequence"]).split()[0] for row in capacity_passes} & {"ES", "RTY", "YM"}
    static_uplifts: Dict[str, float] = {}
    for sequence in DEVELOPMENT_SEQUENCES + TRANSFER_SEQUENCES + SENSITIVITY_SEQUENCES:
        current = policies[(sequence, "acceptance")]["capacity_ticks_per_opportunity"]
        best_static = max(
            policies[(sequence, "always_continuation")]["capacity_ticks_per_opportunity"],
            policies[(sequence, "always_reversion")]["capacity_ticks_per_opportunity"],
        )
        static_uplifts[sequence] = float(current - best_static)
    dev_blocks = [
        row for row in block_rows
        if row["sequence"] in DEVELOPMENT_SEQUENCES and row["policy"] == "acceptance"
    ]
    positive_contributions = [max(0.0, float(row["capacity_total_net_ticks"])) for row in primary]
    positive_total = sum(positive_contributions)
    concentration = (
        100.0 * max(positive_contributions) / positive_total if positive_total > 0 else None
    )
    criteria = {
        "coverage": all(
            row["opportunities"] >= GATES["minimum_opportunities_each_development_sequence"]
            and row["sessions"] >= GATES["minimum_sessions_each_development_sequence"]
            for row in development_mechanisms
        ),
        "paired_uplift": (
            sum(row["paired_uplift_ticks"] > 0 for row in development_mechanisms)
            >= GATES["minimum_positive_paired_sequences"]
            and float(np.mean([row["paired_uplift_ticks"] for row in development_mechanisms]))
            >= GATES["minimum_sequence_equal_paired_uplift_ticks"]
        ),
        "both_mechanism_arms": (
            float(np.mean([row["aligned_paired_uplift_ticks"] for row in development_mechanisms])) > 0
            and float(np.mean([row["opposed_paired_uplift_ticks"] for row in development_mechanisms])) > 0
        ),
        "capacity_breadth": (
            len(capacity_passes) >= GATES["minimum_positive_capacity_sequences"]
            and len(non_nq) >= GATES["minimum_positive_non_nq_instruments"]
        ),
        "capacity_mean": (
            float(np.mean([row["capacity_mean_net_ticks"] for row in primary]))
            >= GATES["minimum_sequence_equal_capacity_mean_ticks"]
        ),
        "both_halves": (
            sum(row["first_half_capacity_net_ticks"] for row in primary) > 0
            and sum(row["second_half_capacity_net_ticks"] for row in primary) > 0
        ),
        "positive_blocks": (
            100.0 * sum(bool(row["positive"]) for row in dev_blocks) / len(dev_blocks)
            >= GATES["minimum_positive_block_rate_pct"]
        ) if dev_blocks else False,
        "beats_static": (
            sum(static_uplifts[name] > 0 for name in DEVELOPMENT_SEQUENCES)
            >= GATES["minimum_static_beats"]
            and float(np.mean([static_uplifts[name] for name in DEVELOPMENT_SEQUENCES])) >= 0
        ),
        "not_concentrated": (
            concentration is not None
            and concentration < GATES["maximum_positive_capacity_sequence_share_pct"]
        ),
    }
    development_passed = all(criteria.values())

    transfer_criteria: Dict[str, bool] = {}
    for sequence in TRANSFER_SEQUENCES:
        mechanism = mechanisms[sequence]
        row = policies[(sequence, "acceptance")]
        transfer_criteria[sequence] = bool(
            mechanism["opportunities"] >= 50
            and mechanism["paired_uplift_ticks"] > 0
            and row["capacity_total_net_ticks"] > 0
            and row["capacity_profit_factor"] is not None
            and row["capacity_profit_factor"] > 1.0
            and static_uplifts[sequence] >= 0
        )
    transfer_passed = all(transfer_criteria.values())
    return _json_safe({
        "development": {
            "sequence_equal_paired_uplift_ticks": float(np.mean([row["paired_uplift_ticks"] for row in development_mechanisms])),
            "positive_paired_sequences": sum(row["paired_uplift_ticks"] > 0 for row in development_mechanisms),
            "sequence_equal_capacity_mean_ticks": float(np.mean([row["capacity_mean_net_ticks"] for row in primary])),
            "positive_capacity_sequences": len(capacity_passes),
            "positive_non_nq_instruments": sorted(non_nq),
            "pooled_first_half_capacity_net_ticks": float(sum(row["first_half_capacity_net_ticks"] for row in primary)),
            "pooled_second_half_capacity_net_ticks": float(sum(row["second_half_capacity_net_ticks"] for row in primary)),
            "positive_block_rate_pct": 100.0 * sum(bool(row["positive"]) for row in dev_blocks) / len(dev_blocks) if dev_blocks else None,
            "static_uplift_ticks_per_opportunity": {name: static_uplifts[name] for name in DEVELOPMENT_SEQUENCES},
            "sequence_equal_static_uplift_ticks_per_opportunity": float(np.mean([static_uplifts[name] for name in DEVELOPMENT_SEQUENCES])),
            "static_beats": sum(static_uplifts[name] > 0 for name in DEVELOPMENT_SEQUENCES),
            "largest_positive_capacity_sequence_share_pct": concentration,
            "gates": {"thresholds": GATES, "criteria": criteria, "passed": development_passed},
            "result_label": "OPENING_INVENTORY_DEVELOPMENT_EVIDENCE" if development_passed else "OPENING_INVENTORY_NOT_ESTABLISHED",
        },
        "transfer": {
            "criteria": transfer_criteria,
            "static_uplift_ticks_per_opportunity": {name: static_uplifts[name] for name in TRANSFER_SEQUENCES},
            "gates": {"passed": transfer_passed},
            "result_label": "TRANSFER_PRESENT" if transfer_passed else "TRANSFER_NOT_PRESENT",
        },
    })


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
