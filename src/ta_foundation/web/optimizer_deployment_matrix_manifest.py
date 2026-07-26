from __future__ import annotations

import json
import csv
from pathlib import Path
from typing import Any, Iterable

from ta_foundation.web.optimizer_deployment_matrix import (
    enumerate_cells,
    classify_session,
    classify_tier,
    classify_single_multi,
    build_name,
)


def row_start_minute(row: dict[str, Any]) -> int:
    """Returns start_minute if present else StartTimeH*60 + StartTimeM."""
    if "start_minute" in row:
        return int(row["start_minute"])
    if "StartTimeH" in row and "StartTimeM" in row:
        return int(row["StartTimeH"]) * 60 + int(row["StartTimeM"])
    raise KeyError(f"Row missing 'start_minute' or 'StartTimeH'/'StartTimeM': {row}")


def assign_cell(row: dict[str, Any], rules: dict[str, Any]) -> dict[str, Any]:
    """Returns {session, single_multi, tier_index, side} where side = monster if reverse else god."""
    start_minute = row_start_minute(row)
    session = classify_session(start_minute, rules)

    avg_fast = row.get("average_fast", row.get("averageFast"))
    avg_slow = row.get("average_slow", row.get("averageSlow"))
    if avg_fast is None or avg_slow is None:
        raise KeyError(f"Row missing average_fast/averageFast or average_slow/averageSlow: {row}")

    tier_info = classify_tier(float(avg_fast), float(avg_slow), rules)

    max_trades = row.get("max_trades", row.get("MaxTrades", 1))
    profit_stop = row.get("profit_stop", row.get("ProfitStop", 1))
    loss_stop = row.get("loss_stop", row.get("LossStop", 1))
    max_stop = row.get("max_stop", row.get("MaxStop"))
    max_tp_ratio = row.get("max_tp_ratio", row.get("MaxTPRatio"))
    single_multi = classify_single_multi(
        int(max_trades),
        float(profit_stop),
        float(loss_stop),
        max_stop=None if max_stop is None else float(max_stop),
        max_tp_ratio=None if max_tp_ratio is None else float(max_tp_ratio),
        tick_value=float(rules.get("tick_value", 5.0)),
    )

    reverse = row.get("reverse", row.get("Reverse", False))
    side = "monster" if reverse else "god"

    return {
        "session": session,
        "single_multi": single_multi,
        "tier_index": tier_info["tier_index"],
        "side": side,
    }


def cell_name(row: dict[str, Any], rules: dict[str, Any]) -> str:
    """Best-effort computed name from a row's fields.

    NOT authoritative: the real template names come from the canonical
    ``template_naming`` package (which the optimizer pipeline runs to rename the
    .xml files), and a covered manifest cell uses that pipeline name. This helper
    only serves rows that have no ``template_name`` yet (previews/synthetic). It
    does not model the guardrail-adjusted single/multi or true-max-loss descriptor
    that the canonical namer applies, so its output can differ from the real file.
    """
    start_minute = row_start_minute(row)
    avg_fast = row.get("average_fast", row.get("averageFast", 5))
    avg_slow = row.get("average_slow", row.get("averageSlow", 200))
    reverse = row.get("reverse", row.get("Reverse", False))
    max_trades = row.get("max_trades", row.get("MaxTrades", 1))
    profit_stop = row.get("profit_stop", row.get("ProfitStop", 1))
    loss_stop = row.get("loss_stop", row.get("LossStop", 1))

    max_stop = row.get("max_stop", row.get("MaxStop", 200))
    max_tp_ratio = row.get("max_tp_ratio", row.get("MaxTPRatio", 2.0))

    long_enabled = row.get("long_enabled", row.get("Long", True))
    short_enabled = row.get("short_enabled", row.get("Short", True))

    return build_name(
        start_minute=start_minute,
        average_fast=float(avg_fast),
        average_slow=float(avg_slow),
        reverse=bool(reverse),
        max_trades=int(max_trades),
        profit_stop=float(profit_stop),
        loss_stop=float(loss_stop),
        max_loss=float(max_stop),
        rr=float(max_tp_ratio),
        long_enabled=bool(long_enabled),
        short_enabled=bool(short_enabled),
        rules=rules,
    )


def build_deployment_manifest(
    rows: Iterable[dict[str, Any]],
    rules: dict[str, Any],
    *,
    fitness: tuple[str, ...] = ("profit_factor", "total_net_profit"),
) -> dict[str, Any]:
    """
    Assign each row to its cell; keep the BEST row per cell (sort by fitness keys descending).
    Returns manifest dict with all 252 cells.
    """
    best_rows: dict[tuple[Any, ...], dict[str, Any]] = {}

    for row in rows:
        coords = assign_cell(row, rules)
        key = (coords["session"], coords["single_multi"], coords["tier_index"], coords["side"])

        if key not in best_rows:
            best_rows[key] = row
        else:
            current_best = best_rows[key]
            for metric in fitness:
                val_new = row.get(metric)
                val_old = current_best.get(metric)

                # Handle missing as -inf
                v_new = float(val_new) if val_new is not None else float("-inf")
                v_old = float(val_old) if val_old is not None else float("-inf")

                if v_new > v_old:
                    best_rows[key] = row
                    break
                elif v_new < v_old:
                    break

    all_cells = enumerate_cells(rules)
    manifest_cells = []
    covered_count = 0

    for cell in all_cells:
        key = (cell["session"], cell["single_multi"], cell["tier_index"], cell["side"])
        entry = {
            "session": cell["session"],
            "single_multi": cell["single_multi"],
            "tier_index": cell["tier_index"],
            "side": cell["side"],
        }

        if key in best_rows:
            row = best_rows[key]
            # The authoritative name is the one the canonical template_naming
            # pipeline already stamped on the real .xml (carried as
            # ``template_name``). Only fall back to a computed name when a row has
            # no pipeline name (e.g. synthetic/preview rows) -- never override a
            # real file's name, since the predictor must be able to find it.
            entry.update(
                {
                    "name": row.get("template_name") or cell_name(row, rules),
                    "run_id": row.get("run_id"),
                    "template_name": row.get("template_name"),
                    "template_path": row.get("template_path"),
                    "profit_factor": row.get("profit_factor"),
                    "total_net_profit": row.get("total_net_profit"),
                    "trades": row.get("trades"),
                    "status": "covered",
                }
            )
            covered_count += 1
        else:
            entry.update(
                {
                    "name": None,
                    "run_id": None,
                    "template_name": None,
                    "template_path": None,
                    "profit_factor": None,
                    "total_net_profit": None,
                    "trades": None,
                    "status": "missing",
                }
            )
        manifest_cells.append(entry)

    return {
        "cells": manifest_cells,
        "total": len(all_cells),
        "covered": covered_count,
        "missing": len(all_cells) - covered_count,
    }


def apply_best_effort_fallback(
    manifest: dict[str, Any],
    rules: dict[str, Any],
    *,
    fitness: tuple[str, ...] = ("profit_factor", "total_net_profit"),
) -> dict[str, Any]:
    """Fill ``missing`` cells from the best available **same-side** donor cell so
    the predictor always sees a full 252-cell grid.

    God (reverse=False) and Monster (reverse=True) templates behave oppositely, so
    a donor is NEVER borrowed across ``side``. Among covered cells of the same side,
    the donor is the one minimizing distance ``(|Δtier|, single_multi mismatch,
    |Δsession|)``, with higher fitness breaking ties.

    Returns a NEW manifest dict. Filled cells get ``status == "fallback"`` plus a
    ``fallback_source`` with the donor's coords; truly unfillable cells (no covered
    cell shares their side) stay ``"missing"``. Adds a ``"fallback"`` count.
    """
    session_order = {w["session"]: i for i, w in enumerate(rules["session_windows"])}

    covered = [c for c in manifest["cells"] if c.get("status") == "covered"]
    covered_by_side: dict[str, list[dict[str, Any]]] = {}
    for c in covered:
        covered_by_side.setdefault(c["side"], []).append(c)

    def fitness_key(cell: dict[str, Any]) -> tuple[float, ...]:
        out: list[float] = []
        for metric in fitness:
            val = cell.get(metric)
            out.append(float(val) if val is not None else float("-inf"))
        return tuple(out)

    new_cells: list[dict[str, Any]] = []
    fallback_count = 0
    for cell in manifest["cells"]:
        if cell.get("status") != "missing":
            new_cells.append(dict(cell))
            continue

        donors = covered_by_side.get(cell["side"], [])
        if not donors:
            new_cells.append(dict(cell))  # unfillable: no same-side coverage anywhere
            continue

        def donor_rank(d: dict[str, Any]) -> tuple[Any, ...]:
            tier_dist = abs(int(d["tier_index"]) - int(cell["tier_index"]))
            sm_mismatch = 0 if d["single_multi"] == cell["single_multi"] else 1
            sess_dist = abs(
                session_order.get(d["session"], 0) - session_order.get(cell["session"], 0)
            )
            # Proximity first; better fitness (negated → ascending sort) breaks ties.
            return (tier_dist, sm_mismatch, sess_dist, tuple(-v for v in fitness_key(d)))

        donor = min(donors, key=donor_rank)
        entry = dict(cell)
        entry.update(
            {
                "name": donor.get("name"),
                "run_id": donor.get("run_id"),
                "template_name": donor.get("template_name"),
                "template_path": donor.get("template_path"),
                "profit_factor": donor.get("profit_factor"),
                "total_net_profit": donor.get("total_net_profit"),
                "trades": donor.get("trades"),
                "status": "fallback",
                "fallback_source": {
                    "session": donor["session"],
                    "single_multi": donor["single_multi"],
                    "tier_index": donor["tier_index"],
                    "side": donor["side"],
                },
            }
        )
        new_cells.append(entry)
        fallback_count += 1

    covered_count = len(covered)
    missing_count = sum(1 for c in new_cells if c.get("status") == "missing")
    return {
        "cells": new_cells,
        "total": len(new_cells),
        "covered": covered_count,
        "fallback": fallback_count,
        "missing": missing_count,
    }


def write_manifest(manifest: dict[str, Any], out_dir: str | Path) -> dict[str, Path]:
    """Writes JSON and CSV manifest files to out_dir."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    json_path = out_dir / "deployment_matrix_manifest.json"
    with json_path.open("w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    csv_path = out_dir / "deployment_matrix_manifest.csv"
    if manifest["cells"]:
        # Cells are heterogeneous: fallback cells carry ``fallback_source`` and
        # covered cells may carry nested predictor ``features``. Flatten nested
        # data to scalar columns and take the union of keys so DictWriter never
        # trips on a missing/extra field.
        def _flatten(prefix: str, value: Any, row: dict[str, Any]) -> None:
            if isinstance(value, dict):
                for child_key, child_value in value.items():
                    _flatten(f"{prefix}.{child_key}", child_value, row)
            else:
                row[prefix] = value

        flat_rows: list[dict[str, Any]] = []
        fieldnames: list[str] = []
        for cell in manifest["cells"]:
            row: dict[str, Any] = {}
            for key, value in cell.items():
                if key in {"fallback_source", "features"}:
                    continue
                row[key] = value
            src = cell.get("fallback_source")
            if isinstance(src, dict):
                row["fallback_source"] = (
                    f"{src.get('session')}|{src.get('single_multi')}|"
                    f"{src.get('tier_index')}|{src.get('side')}"
                )
            features = cell.get("features")
            if isinstance(features, dict):
                _flatten("features", features, row)
            for key in row:
                if key not in fieldnames:
                    fieldnames.append(key)
            flat_rows.append(row)
        with csv_path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, restval="")
            writer.writeheader()
            writer.writerows(flat_rows)

    return {"json": json_path, "csv": csv_path}


def render_coverage_grid_html(manifest: dict[str, Any], rules: dict[str, Any]) -> str:
    """Renders a compact HTML coverage grid."""
    sides = ["god", "monster"]
    tiers = [i for i in range(1, len(rules["ma_tiers"]) + 1)]
    sessions = [s["session"] for s in rules["session_windows"]]

    html = ["<div class='deployment-coverage'>"]
    html.append("<style>")
    html.append("  .deployment-coverage table { border-collapse: collapse; text-align: center; font-family: sans-serif; font-size: 0.8em; }")
    html.append("  .deployment-coverage th, .deployment-coverage td { border: 1px solid #ccc; padding: 4px; }")
    html.append("  .deployment-coverage .covered { background-color: #28a745; color: white; }")
    html.append("  .deployment-coverage .fallback { background-color: #f0ad4e; color: white; }")
    html.append("  .deployment-coverage .missing { background-color: #dc3545; color: white; }")
    html.append("</style>")

    for side in sides:
        html.append(f"<h3>{side.capitalize()} Coverage</h3>")
        html.append("<table>")

        # Header: Tiers
        html.append("<tr><th>Session / Type</th>")
        for t in tiers:
            html.append(f"<th>T{t}</th>")
        html.append("</tr>")

        for session in sessions:
            for sm in ["single", "multi"]:
                html.append(f"<tr><td style='text-align: left;'>{session} ({sm})</td>")
                for t in tiers:
                    # Find cell in manifest
                    cell = next(
                        (
                            c
                            for c in manifest["cells"]
                            if c["session"] == session
                            and c["single_multi"] == sm
                            and c["tier_index"] == t
                            and c["side"] == side
                        ),
                        None,
                    )

                    status = cell["status"] if cell else "missing"
                    cls = status if status in ("covered", "fallback") else "missing"
                    char = {"covered": "C", "fallback": "F"}.get(status, "M")
                    html.append(f"<td class='{cls}'>{char}</td>")
                html.append("</tr>")

        html.append("</table><br/>")

    html.append("</div>")
    return "\n".join(html)
