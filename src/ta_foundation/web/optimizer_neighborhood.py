from __future__ import annotations

"""Parameter-neighborhood validation: same windows, perturbed parameters.

Concept
-------

Pick one or more final candidates and re-run each with small ±pct
offsets around its numeric parameters. If the strategy stays profitable
one increment away, it's a robust peak; if performance collapses, it
was a needle peak in the optimizer's surface.

This is the cheaper one-at-a-time variant (sweep each parameter while
holding the rest at the candidate's value). A ``full_cube`` mode is
available but cell count grows multiplicatively. Numeric params only —
bools / enums / strings are skipped.

Output
------

    <session>/deployment_package/neighborhood/
        cells.json                    # planned cells per candidate
        templates/<run>__C<i>.xml     # one template per (candidate, cell)
        nt_output/<run>__C<i>/        # NT exports per cell
        nt8_run_batch_command.json    # IPC payload for the AddOn
        NEIGHBORHOOD_README.md
        stability.json                # machine-readable per-cell stats
        stability.md                  # operator-facing markdown report
"""

import json
import math
import re
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ta_foundation.optimization.neighborhood import (
    NeighborhoodCell,
    NeighborhoodError,
    ParameterSweepSpec,
    plan_neighborhood_cells,
)
from ta_foundation.web.optimizer_session import OptimizerSession


NB_DIRNAME = "neighborhood"
NB_TEMPLATES_DIRNAME = "templates"
NB_OUTPUT_DIRNAME = "nt_output"
NB_COMMAND_FILENAME = "nt8_run_batch_command.json"
DEFAULT_PCT = 0.10
DEFAULT_STEPS = 4
DEFAULT_MODE = "one_at_a_time"


class OptimizerNeighborhoodError(Exception):
    pass


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CellTemplate:
    candidate_run_id: str
    cell_index: int
    label: str
    overrides: dict[str, Any]
    source_template: str
    output_template: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CellResult:
    candidate_run_id: str
    cell_index: int
    label: str
    overrides: dict[str, Any]
    trades: int
    net_profit: float | None
    profit_factor: float | None
    max_drawdown: float | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CandidateNeighborhoodStability:
    candidate_run_id: str
    center_trades: int
    center_net_profit: float | None
    center_profit_factor: float | None
    cells_run: int
    cells_with_trades: int
    cells_with_pf_above_1: int
    pf_min: float | None
    pf_median: float | None
    pf_max: float | None
    net_min: float | None
    net_median: float | None
    net_max: float | None
    coefficient_of_variation_net: float | None
    stability_flags: list[str] = field(default_factory=list)
    per_param_summaries: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class NeighborhoodGeneration:
    session_id: str
    output_dir: str
    mode: str
    pct: float
    steps: int
    templates: list[CellTemplate]
    notes: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "output_dir": self.output_dir,
            "mode": self.mode,
            "pct": self.pct,
            "steps": self.steps,
            "templates": [t.to_dict() for t in self.templates],
            "notes": self.notes,
        }


@dataclass(frozen=True)
class NeighborhoodStatus:
    session_id: str
    output_dir: str
    template_count: int
    nt_output_present: bool
    per_cell_results: list[CellResult]
    candidate_stability: list[CandidateNeighborhoodStability]
    notes: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "output_dir": self.output_dir,
            "template_count": self.template_count,
            "nt_output_present": self.nt_output_present,
            "per_cell_results": [r.to_dict() for r in self.per_cell_results],
            "candidate_stability": [s.to_dict() for s in self.candidate_stability],
            "notes": self.notes,
        }


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

def generate_neighborhood_templates(
    session: OptimizerSession,
    *,
    pct: float = DEFAULT_PCT,
    steps: int = DEFAULT_STEPS,
    mode: str = DEFAULT_MODE,
    candidate_run_ids: list[str] | None = None,
) -> NeighborhoodGeneration:
    pkg_dir = session.directory / "deployment_package"
    named_dir = pkg_dir / "final_backtest_handoff" / "named_backtest_templates"
    if not named_dir.exists():
        raise OptimizerNeighborhoodError(
            f"No named backtest templates at {named_dir}. Run the final fixed Backtest phase first."
        )

    doc = session.load_document()
    optimized_specs = _build_specs_from_session(doc.parameters, pct=pct, steps=steps)
    if not optimized_specs:
        raise OptimizerNeighborhoodError(
            "Session has no numeric optimized parameters. Nothing to sweep."
        )

    out_dir = pkg_dir / NB_DIRNAME
    templates_dir = out_dir / NB_TEMPLATES_DIRNAME
    if templates_dir.exists():
        shutil.rmtree(templates_dir)
    templates_dir.mkdir(parents=True, exist_ok=True)

    selection: set[str] | None = None
    if candidate_run_ids is not None:
        selection = {str(s).strip() for s in candidate_run_ids if str(s).strip()}
        if not selection:
            raise OptimizerNeighborhoodError("candidate_run_ids was empty")

    templates: list[CellTemplate] = []
    notes: list[str] = []
    plan_per_candidate: dict[str, list[NeighborhoodCell]] = {}

    for xml_path in sorted(named_dir.rglob("*.xml")):
        run_id = _candidate_run_id_for_template(xml_path)
        if selection is not None and run_id not in selection:
            continue
        source_text = xml_path.read_text(encoding="utf-8")
        candidate_params = _read_candidate_params(source_text, [s.name for s in optimized_specs])
        try:
            cells = plan_neighborhood_cells(
                candidate_params=candidate_params,
                sweep_specs=optimized_specs,
                mode=mode,
            )
        except NeighborhoodError as exc:
            notes.append(f"{run_id}: {exc}")
            continue
        if not cells:
            notes.append(f"{run_id}: no neighborhood cells generated (check pct/steps).")
            continue
        plan_per_candidate[run_id] = cells

        for cell in cells:
            patched = source_text
            for name, value in cell.overrides.items():
                patched = _patch_tag_text(patched, name, _format_xml_value(value))
            stem = f"{run_id}__C{cell.index:02d}"
            target = templates_dir / f"{stem}.xml"
            target.write_text(patched, encoding="utf-8")
            templates.append(CellTemplate(
                candidate_run_id=run_id,
                cell_index=cell.index,
                label=cell.label,
                overrides=dict(cell.overrides),
                source_template=str(xml_path),
                output_template=str(target),
            ))

    if selection is not None:
        produced = {t.candidate_run_id for t in templates}
        missing = sorted(selection - produced)
        if missing:
            notes.append(f"Requested candidates not found in named_backtest_templates: {missing}")

    nt_output_dir = out_dir / NB_OUTPUT_DIRNAME
    command = {
        "action": "RunBatch",
        "sourceFolder": str(templates_dir.resolve()),
        "destFolder": str(nt_output_dir.resolve()),
        "instrument": doc.instrument,
    }
    (out_dir / NB_COMMAND_FILENAME).write_text(json.dumps(command, indent=2), encoding="utf-8")

    (out_dir / "cells.json").write_text(
        json.dumps({
            "schema_version": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "mode": mode,
            "pct": pct,
            "steps": steps,
            "candidate_run_ids": sorted(selection) if selection is not None else None,
            "per_candidate_cells": {
                run_id: [c.to_dict() for c in cells]
                for run_id, cells in plan_per_candidate.items()
            },
            "sweep_specs": [s.to_dict() for s in optimized_specs],
        }, indent=2),
        encoding="utf-8",
    )

    _write_readme(
        out_dir / "NEIGHBORHOOD_README.md",
        templates=templates,
        plan_per_candidate=plan_per_candidate,
        command_path=str(out_dir / NB_COMMAND_FILENAME),
        nt_output_dir=str(nt_output_dir),
        mode=mode, pct=pct, steps=steps,
    )

    return NeighborhoodGeneration(
        session_id=session.id,
        output_dir=str(out_dir.resolve()),
        mode=mode, pct=pct, steps=steps,
        templates=templates,
        notes=notes,
    )


def trigger_neighborhood_run(
    session: OptimizerSession,
    *,
    command_file: Path | None = None,
) -> dict[str, Any]:
    from ta_foundation.web.optimizer_runner import DEFAULT_COMMAND_FILE

    pkg_dir = session.directory / "deployment_package"
    out_dir = pkg_dir / NB_DIRNAME
    templates_dir = out_dir / NB_TEMPLATES_DIRNAME
    if not templates_dir.exists() or not any(templates_dir.glob("*.xml")):
        raise OptimizerNeighborhoodError(
            f"No neighborhood templates to dispatch at {templates_dir}. "
            "Call generate_neighborhood_templates first."
        )
    doc = session.load_document()
    nt_output_dir = out_dir / NB_OUTPUT_DIRNAME
    nt_output_dir.mkdir(parents=True, exist_ok=True)

    target = Path(command_file) if command_file is not None else DEFAULT_COMMAND_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "action": "RunBatch",
        "runId": "nb_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S"),
        "sourceFolder": str(templates_dir.resolve()),
        "destFolder": str(nt_output_dir.resolve()),
        "instrument": doc.instrument,
    }
    target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return {"command_file": str(target), "payload": payload}


def neighborhood_status(session: OptimizerSession) -> NeighborhoodStatus:
    pkg_dir = session.directory / "deployment_package"
    out_dir = pkg_dir / NB_DIRNAME
    templates_dir = out_dir / NB_TEMPLATES_DIRNAME
    nt_output_dir = out_dir / NB_OUTPUT_DIRNAME

    template_count = len(list(templates_dir.glob("*.xml"))) if templates_dir.exists() else 0
    nt_output_present = (
        nt_output_dir.exists() and any(p.is_dir() for p in nt_output_dir.iterdir())
    ) if nt_output_dir.exists() else False

    notes: list[str] = []
    if not templates_dir.exists():
        notes.append("Neighborhood templates have not been generated yet.")
        return NeighborhoodStatus(
            session_id=session.id,
            output_dir=str(out_dir.resolve()) if out_dir.exists() else str(out_dir),
            template_count=0, nt_output_present=False,
            per_cell_results=[], candidate_stability=[], notes=notes,
        )

    # Load the cells plan so we can map cell_index -> overrides/label.
    cells_plan: dict[str, dict[int, dict[str, Any]]] = {}
    cells_json = out_dir / "cells.json"
    if cells_json.exists():
        try:
            payload = json.loads(cells_json.read_text(encoding="utf-8"))
            for run_id, cells in (payload.get("per_candidate_cells") or {}).items():
                cells_plan[run_id] = {int(c["index"]): c for c in cells}
        except (OSError, json.JSONDecodeError):
            notes.append("cells.json is unreadable.")

    per_cell: list[CellResult] = []
    by_candidate: dict[str, list[CellResult]] = {}
    if nt_output_present:
        for cell_dir in sorted(p for p in nt_output_dir.iterdir() if p.is_dir()):
            run_id, cell_index = _parse_cell_dir(cell_dir.name)
            if run_id is None:
                notes.append(f"Unexpected output folder name: {cell_dir.name}")
                continue
            trades_path = cell_dir / "Trades.csv"
            stats = _summarize_trades(trades_path) if trades_path.exists() else None
            if stats is None:
                notes.append(f"{cell_dir.name}: Trades.csv missing or unparseable")
                continue
            plan_entry = cells_plan.get(run_id, {}).get(cell_index, {})
            label = str(plan_entry.get("label") or "")
            overrides = plan_entry.get("overrides") or {}
            result = CellResult(
                candidate_run_id=run_id,
                cell_index=cell_index,
                label=label,
                overrides=dict(overrides),
                trades=int(stats["trade_count"]),
                net_profit=_safe_float(stats["net_profit"]),
                profit_factor=_safe_float(stats["profit_factor"]),
                max_drawdown=_safe_float(stats["max_drawdown"]),
            )
            per_cell.append(result)
            by_candidate.setdefault(run_id, []).append(result)

    final_results = pkg_dir / "final_backtest_handoff" / "nt8_backtest_results"
    candidate_stability: list[CandidateNeighborhoodStability] = []
    for run_id, results in sorted(by_candidate.items()):
        results.sort(key=lambda r: r.cell_index)
        center = _summarize_trades(final_results / run_id / "Trades.csv") \
            if (final_results / run_id / "Trades.csv").exists() else None
        candidate_stability.append(_build_stability(run_id, center, results))

    return NeighborhoodStatus(
        session_id=session.id,
        output_dir=str(out_dir.resolve()),
        template_count=template_count,
        nt_output_present=nt_output_present,
        per_cell_results=per_cell,
        candidate_stability=candidate_stability,
        notes=notes,
    )


def ingest_neighborhood_results(session: OptimizerSession) -> NeighborhoodStatus:
    status = neighborhood_status(session)
    if not status.nt_output_present:
        return status
    pkg_dir = session.directory / "deployment_package"
    out_dir = pkg_dir / NB_DIRNAME
    (out_dir / "stability.json").write_text(
        json.dumps({
            "schema_version": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "session_id": session.id,
            **status.to_dict(),
        }, indent=2),
        encoding="utf-8",
    )
    _write_stability_md(out_dir / "stability.md", session, status)
    return status


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _build_specs_from_session(parameters: list, *, pct: float, steps: int) -> list[ParameterSweepSpec]:
    """Translate the session's optimized parameters into sweep specs.

    Only numeric (int/float) optimize-mode parameters are returned. Bool
    and other types are skipped — they have no meaningful neighborhood.
    """
    specs: list[ParameterSweepSpec] = []
    for p in parameters:
        if getattr(p, "mode", "") != "optimize":
            continue
        type_name = _normalize_type_name(getattr(p, "type_name", ""))
        if type_name not in {"int", "float"}:
            continue
        specs.append(ParameterSweepSpec(
            name=p.name,
            type_name=type_name,
            pct=pct,
            steps=steps,
            increment=_to_number(getattr(p, "increment", None)),
            minimum=_to_number(getattr(p, "minimum", None)),
            maximum=_to_number(getattr(p, "maximum", None)),
        ))
    return specs


def _normalize_type_name(raw: str) -> str:
    s = (raw or "").strip().lower()
    if s in {"int", "int32", "int64", "integer", "system.int32", "system.int64"}:
        return "int"
    if s in {"float", "double", "decimal", "single", "system.double", "system.single", "system.decimal"}:
        return "float"
    return s


def _to_number(value: Any) -> float | int | None:
    if value is None or value == "":
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f == int(f):
        return int(f)
    return f


def _read_candidate_params(xml_text: str, names: list[str]) -> dict[str, Any]:
    """Pull current values for each requested parameter from the XML."""
    out: dict[str, Any] = {}
    for name in names:
        pattern = re.compile(
            r"<" + re.escape(name) + r"(?:\s+[^>]*)?>(.*?)</" + re.escape(name) + r">",
            re.DOTALL,
        )
        m = pattern.search(xml_text)
        if m:
            out[name] = m.group(1).strip()
    return out


def _candidate_run_id_for_template(path: Path) -> str:
    stem = path.stem
    m = re.match(r"^(\d+)_", stem)
    if m:
        return f"F_{int(m.group(1)):03d}"
    return stem


def _parse_cell_dir(name: str) -> tuple[str | None, int]:
    m = re.match(r"^(F_\d+)__C(\d+)$", name)
    if not m:
        return None, -1
    return m.group(1), int(m.group(2))


def _patch_tag_text(text: str, tag: str, value: str) -> str:
    pattern = re.compile(
        r"(<" + re.escape(tag) + r"(?:\s+[^>]*)?>)(.*?)(</" + re.escape(tag) + r">)",
        re.DOTALL,
    )
    return pattern.sub(lambda m: m.group(1) + value + m.group(3), text, count=1)


def _format_xml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value == int(value):
            return str(int(value))
        return repr(value)
    return str(value)


def _summarize_trades(trades_path: Path) -> dict[str, Any] | None:
    if not trades_path.exists():
        return None
    try:
        import csv as _csv
        rows: list[dict[str, str]] = []
        with trades_path.open(encoding="utf-8-sig", newline="") as h:
            reader = _csv.DictReader(h)
            for row in reader:
                rows.append(row)
    except Exception:
        return None
    profits = [_money_to_float(r.get("Profit")) for r in rows]
    profits = [p for p in profits if p is not None]
    if not profits:
        return {"trade_count": 0, "net_profit": 0.0, "profit_factor": None, "max_drawdown": 0.0}
    gross_p = sum(p for p in profits if p > 0)
    gross_l = sum(p for p in profits if p < 0)
    pf: float | None
    if gross_l < 0:
        pf = gross_p / abs(gross_l)
    elif gross_p > 0:
        pf = float("inf")
    else:
        pf = None
    peak = 0.0
    cum = 0.0
    max_dd = 0.0
    for p in profits:
        cum += p
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > max_dd:
            max_dd = dd
    return {
        "trade_count": len(profits),
        "net_profit": sum(profits),
        "profit_factor": pf,
        "max_drawdown": max_dd,
    }


def _money_to_float(value: Any) -> float | None:
    if value is None:
        return None
    s = str(value).strip()
    if not s or s.lower() in {"nan", "none"}:
        return None
    negative = s.startswith("(") and s.endswith(")")
    s = s.strip("()$ ").replace(",", "")
    if not s:
        return None
    try:
        out = float(s)
    except ValueError:
        return None
    return -out if negative else out


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def _build_stability(
    run_id: str,
    center: dict[str, Any] | None,
    results: list[CellResult],
) -> CandidateNeighborhoodStability:
    nets = [r.net_profit for r in results if r.net_profit is not None]
    pfs = [r.profit_factor for r in results if r.profit_factor is not None and math.isfinite(r.profit_factor)]
    n_cells = len(results)
    n_with_trades = sum(1 for r in results if r.trades > 0)
    n_pf_above_1 = sum(1 for r in results if r.profit_factor is not None and r.profit_factor > 1.0)

    mean_net: float | None = sum(nets) / len(nets) if nets else None
    median_net = _median(nets) if nets else None
    pf_min = min(pfs) if pfs else None
    pf_med = _median(pfs) if pfs else None
    pf_max = max(pfs) if pfs else None
    net_min = min(nets) if nets else None
    net_max = max(nets) if nets else None

    coef_of_var: float | None = None
    if nets and mean_net is not None and abs(mean_net) > 1e-9 and len(nets) > 1:
        variance = sum((n - mean_net) ** 2 for n in nets) / len(nets)
        coef_of_var = (variance ** 0.5) / abs(mean_net)

    flags: list[str] = []
    if n_cells and n_with_trades == 0:
        flags.append("no neighborhood cells produced trades")
    elif n_with_trades <= n_cells / 2:
        flags.append(f"only {n_with_trades}/{n_cells} cells produced trades")
    if n_cells and n_pf_above_1 <= n_cells / 2:
        flags.append(f"PF stayed > 1.0 in only {n_pf_above_1}/{n_cells} cells")
    if coef_of_var is not None and coef_of_var > 1.5:
        flags.append(f"net profit highly variable across cells (CoV {coef_of_var:.2f})")
    if center is not None and center.get("profit_factor") is not None:
        center_pf = center["profit_factor"]
        if pf_med is not None and math.isfinite(center_pf) and center_pf > 1.5 and pf_med < 1.0:
            flags.append(f"needle peak: center PF {center_pf:.2f} → neighborhood median {pf_med:.2f}")

    # Per-parameter summaries (one_at_a_time only carries one override per cell).
    per_param: dict[str, list[CellResult]] = {}
    for r in results:
        if len(r.overrides) == 1:
            (name,) = r.overrides.keys()
            per_param.setdefault(name, []).append(r)
    per_param_summaries: list[dict[str, Any]] = []
    for name, group in sorted(per_param.items()):
        group_pfs = [g.profit_factor for g in group if g.profit_factor is not None and math.isfinite(g.profit_factor)]
        group_nets = [g.net_profit for g in group if g.net_profit is not None]
        per_param_summaries.append({
            "parameter": name,
            "cells": len(group),
            "cells_with_pf_above_1": sum(1 for pf in group_pfs if pf > 1.0),
            "pf_min": min(group_pfs) if group_pfs else None,
            "pf_median": _median(group_pfs) if group_pfs else None,
            "pf_max": max(group_pfs) if group_pfs else None,
            "net_median": _median(group_nets) if group_nets else None,
        })

    return CandidateNeighborhoodStability(
        candidate_run_id=run_id,
        center_trades=int((center or {}).get("trade_count") or 0),
        center_net_profit=_safe_float((center or {}).get("net_profit")),
        center_profit_factor=_safe_float((center or {}).get("profit_factor")),
        cells_run=n_cells,
        cells_with_trades=n_with_trades,
        cells_with_pf_above_1=n_pf_above_1,
        pf_min=pf_min,
        pf_median=pf_med,
        pf_max=pf_max,
        net_min=net_min,
        net_median=median_net,
        net_max=net_max,
        coefficient_of_variation_net=coef_of_var,
        stability_flags=flags,
        per_param_summaries=per_param_summaries,
    )


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    s = sorted(values)
    n = len(s)
    if n % 2 == 1:
        return s[n // 2]
    return (s[n // 2 - 1] + s[n // 2]) / 2


def _write_readme(path: Path, *, templates, plan_per_candidate, command_path, nt_output_dir, mode, pct, steps) -> None:
    lines = [
        "# Parameter Neighborhood Validation",
        "",
        f"Mode: {mode}",
        f"Per-param offset: ±{pct * 100:.0f}%  ({steps} off-center samples per parameter)",
        f"Candidates: {len(plan_per_candidate)}",
        f"Total cells: {len(templates)}",
        "",
        "Each cell re-runs a candidate's fixed-Backtest template with a small",
        "offset on one (or a combination of) numeric parameter(s). Compare",
        "per-cell results against the original center result to detect needle",
        "peaks vs. robust plateaus.",
        "",
        "## How to run",
        "",
        f"1. Drop `{command_path}` into the AddOn's watched location, or POST",
        "   `/api/optimizer/sessions/<id>/neighborhood/run` to dispatch.",
        f"2. NT writes per-cell exports under `{nt_output_dir}/<run>__C<i>/`.",
        "3. POST `/api/optimizer/sessions/<id>/neighborhood/ingest` to compute",
        "   the stability report.",
        "",
        "## Planned cells",
        "",
    ]
    for run_id, cells in sorted(plan_per_candidate.items()):
        lines.append(f"### {run_id}")
        lines.append("")
        for c in cells:
            lines.append(f"- C{c.index:02d}: {c.label}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_stability_md(path: Path, session: OptimizerSession, status: NeighborhoodStatus) -> None:
    lines = [
        "# Parameter Neighborhood Stability Report",
        "",
        f"Session: `{session.id}`",
        f"Templates: {status.template_count}",
        f"Candidates with results: {len(status.candidate_stability)}",
        "",
    ]
    if status.candidate_stability:
        lines.extend([
            "## Per-candidate stability",
            "",
            "| Run | Center PF | Center net | Cells | With trades | PF>1 | PF min | PF med | PF max | Net med | CoV(net) | Flags |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
        ])
        for s in status.candidate_stability:
            flags = "; ".join(s.stability_flags) or "ok"
            lines.append(
                f"| {s.candidate_run_id} | {_fmt(s.center_profit_factor, 2)} | {_fmt(s.center_net_profit, 0)} | "
                f"{s.cells_run} | {s.cells_with_trades} | {s.cells_with_pf_above_1} | "
                f"{_fmt(s.pf_min, 2)} | {_fmt(s.pf_median, 2)} | {_fmt(s.pf_max, 2)} | "
                f"{_fmt(s.net_median, 0)} | {_fmt(s.coefficient_of_variation_net, 2)} | {flags} |"
            )
        # Per-parameter breakdown (one_at_a_time only).
        any_param = any(s.per_param_summaries for s in status.candidate_stability)
        if any_param:
            lines.extend([
                "",
                "## Per-parameter sensitivity (one-at-a-time)",
                "",
                "| Run | Parameter | Cells | PF>1 | PF min | PF med | PF max | Net med |",
                "|---|---|---:|---:|---:|---:|---:|---:|",
            ])
            for s in status.candidate_stability:
                for p in s.per_param_summaries:
                    lines.append(
                        f"| {s.candidate_run_id} | {p['parameter']} | {p['cells']} | "
                        f"{p['cells_with_pf_above_1']} | {_fmt(p.get('pf_min'), 2)} | "
                        f"{_fmt(p.get('pf_median'), 2)} | {_fmt(p.get('pf_max'), 2)} | "
                        f"{_fmt(p.get('net_median'), 0)} |"
                    )
    if status.per_cell_results:
        lines.extend([
            "",
            "## Per-cell results",
            "",
            "| Run | Cell | Label | Trades | Net | PF | DD |",
            "|---|---:|---|---:|---:|---:|---:|",
        ])
        for r in sorted(status.per_cell_results, key=lambda x: (x.candidate_run_id, x.cell_index)):
            lines.append(
                f"| {r.candidate_run_id} | {r.cell_index} | {r.label} | "
                f"{r.trades} | {_fmt(r.net_profit, 0)} | {_fmt(r.profit_factor, 2)} | {_fmt(r.max_drawdown, 0)} |"
            )
    if status.notes:
        lines.extend(["", "## Notes", ""])
        for n in status.notes:
            lines.append(f"- {n}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _fmt(value: Any, digits: int) -> str:
    if value is None:
        return ""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return str(value)
    if not math.isfinite(f):
        return ""
    if abs(f) >= 1000:
        return f"{f:,.0f}"
    return f"{f:.{digits}f}"
