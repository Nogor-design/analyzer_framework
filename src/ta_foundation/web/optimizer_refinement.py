from __future__ import annotations

"""Risk-parameter refinement run launched from a finished session.

The weekly run finds the structural edge (MA / stop / target / direction /
time). This module lets the operator hand-pick winners from that finished run
and sweep only the *session risk* knobs on them — ProfitStop, LossStop and
MaxTrades — pinning everything structural to each picked candidate's values.

It reuses the recipe engine's child-stage machinery:

- Each picked final template (``F_NNN``) becomes a parent "selected row" whose
  structural params come from the final-backtest manifest's ``strategy_values``
  (the params the recipe varied; the seed pins the rest).
- A new optimizer stage pins those structural params and ``add_optimize``-sweeps
  the three risk knobs.
- A new fixed-backtest stage validates the refined winners.

The originals from the first run are left untouched; the Category Bundle is the
combiner that merges originals + refined for the final prune.
"""

import json
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ta_foundation.web.optimizer_recipe import load_recipe, save_recipe
from ta_foundation.web.optimizer_session import OptimizerSession


# The operator's standard refinement ranges, as NT optimization sweeps
# (min / max / step). ProfitStop & LossStop 1→1001 by 500 = {1,501,1001};
# MaxTrades 1→11 by 2 = {1,3,5,7,9,11}.
DEFAULT_PROFIT_STOP = {"min": 1, "max": 1001, "step": 500}
DEFAULT_LOSS_STOP = {"min": 1, "max": 1001, "step": 500}
DEFAULT_MAX_TRADES = {"min": 1, "max": 11, "step": 2}

FINAL_MANIFEST_REL = Path("generated_templates") / "final_backtest" / "recipe_template_manifest.json"
PARSED_RESULTS_DIRNAME = "parsed_results"


class RefinementError(Exception):
    pass


@dataclass(frozen=True)
class RefinementRanges:
    profit_stop: dict[str, float]
    loss_stop: dict[str, float]
    max_trades: dict[str, float]

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None = None) -> "RefinementRanges":
        payload = payload or {}
        return cls(
            profit_stop=_range_spec(payload.get("profit_stop"), DEFAULT_PROFIT_STOP),
            loss_stop=_range_spec(payload.get("loss_stop"), DEFAULT_LOSS_STOP),
            max_trades=_range_spec(payload.get("max_trades"), DEFAULT_MAX_TRADES),
        )

    def combos_per_candidate(self) -> int:
        return _step_count(self.profit_stop) * _step_count(self.loss_stop) * _step_count(self.max_trades)


@dataclass(frozen=True)
class RefinementPrepResult:
    session_id: str
    refine_stage_id: str
    final_stage_id: str
    source_stage_id: str
    candidate_count: int
    combos_per_candidate: int
    total_combinations: int
    pinned_params: list[str]


def list_refinable_candidates(session: OptimizerSession) -> list[dict[str, Any]]:
    """Return finished final templates the operator can pick to refine, enriched
    with net/PF/trades from the weekly selection CSVs when available."""
    manifest = _load_final_manifest(session)
    metrics = _candidate_metrics(session)
    out: list[dict[str, Any]] = []
    for template_id, record in manifest.items():
        run_id = _run_id_from_template_id(template_id)
        sv = record.get("strategy_values") if isinstance(record, dict) else None
        if not run_id or not isinstance(sv, dict):
            continue
        m = metrics.get(run_id, {})
        out.append({
            "run_id": run_id,
            "strategy_values": sv,
            "parent_candidate_id": record.get("parent_candidate_id"),
            "bucket_id": record.get("bucket_id"),
            "net_profit": m.get("net_profit"),
            "profit_factor": m.get("profit_factor"),
            "trades": m.get("trades"),
            "direction_shape": m.get("direction_shape"),
        })
    out.sort(key=lambda r: (-(r.get("net_profit") or -1e12), _run_sort_key(r["run_id"])))
    return out


def _candidate_metrics(session: OptimizerSession) -> dict[str, dict[str, Any]]:
    import csv
    data_dir = session.directory / "deployment_package" / "weekly_coverage_package" / "data"
    out: dict[str, dict[str, Any]] = {}
    for name in ("operationally_diverse_validated_selection.csv", "best_effort_fallback_selection.csv"):
        path = data_dir / name
        if not path.exists():
            continue
        with path.open(newline="", encoding="utf-8-sig") as handle:
            for row in csv.DictReader(handle):
                rid = str(row.get("run_id") or "")
                if not rid:
                    continue
                out.setdefault(rid, {
                    "net_profit": _num(row.get("net_profit")),
                    "profit_factor": _num(row.get("profit_factor")),
                    "trades": _num(row.get("trades")),
                    "direction_shape": row.get("direction_shape") or "",
                })
    return out


def _num(value: Any) -> Any:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def prepare_refinement(
    session: OptimizerSession,
    candidate_run_ids: list[str],
    *,
    ranges: RefinementRanges | None = None,
    keep_per_candidate: int = 2,
) -> RefinementPrepResult:
    """Write the picked candidates as a parent selection and append a refine +
    final-backtest stage to the recipe. Does NOT submit to NinjaTrader — the
    caller rearms the orchestrator and lets it advance."""
    ranges = ranges or RefinementRanges.from_payload(None)
    picked = {str(r).strip() for r in candidate_run_ids if str(r).strip()}
    if not picked:
        raise RefinementError("No candidates selected to refine.")

    by_run = {c["run_id"]: c for c in list_refinable_candidates(session)}
    rows: list[dict[str, Any]] = []
    pinned: set[str] = set()
    for run_id in sorted(picked, key=_run_sort_key):
        cand = by_run.get(run_id)
        if cand is None:
            continue
        sv = cand["strategy_values"]
        row: dict[str, Any] = {"candidate_id": run_id, "parent_candidate_id": run_id}
        for name, value in sv.items():
            row[f"param_{name}"] = value
            pinned.add(name)
        rows.append(row)
    if not rows:
        raise RefinementError("None of the selected run ids were found in the final manifest.")

    token = secrets.token_hex(3)
    source_stage_id = f"refine_src_{token}"
    refine_stage_id = f"refine_risk_{token}"
    final_stage_id = f"refine_final_{token}"

    # Parent selection the child stage reads.
    src_dir = session.directory / PARSED_RESULTS_DIRNAME / source_stage_id
    src_dir.mkdir(parents=True, exist_ok=True)
    (src_dir / "selected.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")

    pinned_sorted = sorted(pinned)
    refine_stage = {
        "stage_id": refine_stage_id,
        "stage_type": "optimizer",
        "description": "Refine session risk knobs (ProfitStop / LossStop / MaxTrades) on picked winners.",
        "from": f"{source_stage_id}.selected_rows",
        "pin": pinned_sorted,
        "optimize_inside_template": {
            "ProfitStop": dict(ranges.profit_stop),
            "LossStop": dict(ranges.loss_stop),
            "MaxTrades": dict(ranges.max_trades),
        },
        "selection": {
            "group_by": ["parent_candidate_id"],
            "keep_per_group": max(1, int(keep_per_candidate)),
            "fitness_metrics": ["profit_factor", "total_net_profit"],
        },
    }
    final_stage = {
        "stage_id": final_stage_id,
        "stage_type": "fixed_backtest",
        "from": f"{refine_stage_id}.selected_rows",
        "finalists_per_bucket": max(1, int(keep_per_candidate)),
        "description": "Validate refined risk-knob winners with fixed backtests.",
    }

    recipe = load_recipe(session).to_dict()
    recipe.setdefault("stages", [])
    recipe["stages"].append(refine_stage)
    recipe["stages"].append(final_stage)
    save_recipe(session, recipe)

    # Rebuild the plan so the new stages are known. Child/final stages are
    # deferred until their parent selections exist — which we just wrote.
    from ta_foundation.web.optimizer_recipe_plan import build_and_save_recipe_plan
    build_and_save_recipe_plan(session)

    return RefinementPrepResult(
        session_id=session.id,
        refine_stage_id=refine_stage_id,
        final_stage_id=final_stage_id,
        source_stage_id=source_stage_id,
        candidate_count=len(rows),
        combos_per_candidate=ranges.combos_per_candidate(),
        total_combinations=len(rows) * ranges.combos_per_candidate(),
        pinned_params=pinned_sorted,
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _load_final_manifest(session: OptimizerSession) -> dict[str, Any]:
    path = session.directory / FINAL_MANIFEST_REL
    if not path.exists():
        raise RefinementError(
            "This session has no final-backtest manifest yet — finish the first "
            "run (Build package) before refining."
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RefinementError(f"Could not read final manifest: {exc}") from exc
    templates = data.get("templates") if isinstance(data, dict) else None
    if isinstance(templates, dict):
        return templates
    if isinstance(templates, list):
        out: dict[str, Any] = {}
        for record in templates:
            if isinstance(record, dict):
                out[str(record.get("template_id") or len(out))] = record
        return out
    raise RefinementError("Final manifest has no templates block.")


def _run_id_from_template_id(template_id: Any) -> str:
    text = str(template_id or "")
    marker = "final_backtest__"
    return text[len(marker):] if text.startswith(marker) else ""


def _run_sort_key(run_id: str) -> tuple[int, str]:
    digits = "".join(ch for ch in str(run_id) if ch.isdigit())
    return (int(digits) if digits else 10**9, str(run_id))


def _range_spec(value: Any, default: dict[str, float]) -> dict[str, float]:
    if not isinstance(value, dict):
        return dict(default)
    out: dict[str, float] = {}
    for key in ("min", "max", "step"):
        try:
            out[key] = float(value[key]) if key in value else float(default[key])
        except (TypeError, ValueError):
            out[key] = float(default[key])
    if out["step"] <= 0:
        out["step"] = float(default["step"])
    if out["max"] < out["min"]:
        out["max"] = out["min"]
    return out


def _step_count(spec: dict[str, float]) -> int:
    lo, hi, step = spec.get("min", 0), spec.get("max", 0), spec.get("step", 1)
    if step <= 0 or hi < lo:
        return 1
    return int((hi - lo) // step) + 1
