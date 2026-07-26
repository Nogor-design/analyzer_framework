from __future__ import annotations

import argparse
import csv
import json
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence

from ta_foundation.optimization.nt_template import (
    StrategyOptimizationTemplate,
    parse_strategy_optimization_template,
)


@dataclass(frozen=True)
class HandoffRunPlanRow:
    template_name: str
    template_path: str
    strategy: str
    mode: str
    pass_id: str
    start_hour: int | None
    duration_hours: int | None
    session_bucket: str
    optimized_parameters: str
    estimated_combinations: int
    optimization_fitness: str
    notes: str


def build_handoff_run_plan(template_paths: Sequence[str | Path]) -> list[HandoffRunPlanRow]:
    rows: list[HandoffRunPlanRow] = []
    for path in sorted((Path(p) for p in template_paths), key=lambda p: p.name.lower()):
        template = parse_strategy_optimization_template(path)
        rows.append(_row_from_template(template))
    return rows


def write_handoff_package(input_dir: str | Path, output_dir: str | Path, recursive: bool = False) -> list[HandoffRunPlanRow]:
    source = Path(input_dir)
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    _clear_handoff_outputs(destination)

    pattern = "**/*.xml" if recursive else "*.xml"
    rows = build_handoff_run_plan(tuple(source.glob(pattern)))

    _write_csv(destination / "run_plan.csv", rows)
    _write_json(destination / "run_plan.json", rows)
    _write_markdown(destination / "run_plan.md", rows)
    _write_team_readme(destination / "README_FOR_TEAM.md", rows)
    _copy_templates(destination / "templates", rows)
    return rows


def _clear_handoff_outputs(destination: Path) -> None:
    for filename in ("run_plan.csv", "run_plan.json", "run_plan.md", "README_FOR_TEAM.md"):
        path = destination / filename
        if path.exists():
            path.unlink()
    templates = destination / "templates"
    if templates.exists():
        shutil.rmtree(templates)


def _row_from_template(template: StrategyOptimizationTemplate) -> HandoffRunPlanRow:
    strategy = template.strategy_type.rsplit(".", 1)[-1]
    optimized = ", ".join(
        f"{p.name}={p.minimum}..{p.maximum} step {p.increment}"
        for p in template.swept_parameters
    )
    return HandoffRunPlanRow(
        template_name=template.path.name,
        template_path=str(template.path),
        strategy=strategy,
        mode=template.mode,
        pass_id=_guess_pass_id(template),
        start_hour=template.start_hour,
        duration_hours=template.duration_hours,
        session_bucket=_session_bucket(template.start_hour),
        optimized_parameters=optimized,
        estimated_combinations=template.estimated_combinations,
        optimization_fitness=template.optimization_fitness.rsplit(".", 1)[-1],
        notes=_notes_for_template(template),
    )


def _guess_pass_id(template: StrategyOptimizationTemplate) -> str:
    names = {parameter.name for parameter in template.swept_parameters}
    if {"averageSlow", "MaxStop", "MaxTPRatio"}.issubset(names) and "DurationTimeH" in names:
        return "pass_1_broad_discovery"
    if {"averageFast", "averageSlow", "MaxStop", "MaxTPRatio"}.issubset(names):
        return "pass_2_candidate_refinement"
    if names and names.issubset({"ProfitStop", "LossStop", "MaxTrades"}):
        return "pass_3_daily_risk_behavior"
    if not names:
        return "fixed_backtest"
    return "custom_or_later_pass"


def _notes_for_template(template: StrategyOptimizationTemplate) -> str:
    notes: list[str] = []
    if template.estimated_combinations > 50000:
        notes.append("large run")
    if template.mode == "regression":
        notes.append("Reverse=true")
    if "averageSlow" in {p.name for p in template.swept_parameters}:
        notes.append("preserve slow-MA diversity when selecting")
    return "; ".join(notes)


def _session_bucket(start_hour: int | None) -> str:
    if start_hour is None:
        return "Unclassified"
    if 0 <= start_hour < 4:
        return "London Early"
    if 4 <= start_hour < 7:
        return "London Late"
    if 7 <= start_hour < 9:
        return "Pre-Market"
    if 9 <= start_hour < 11:
        return "NY Open"
    if 11 <= start_hour < 14:
        return "Midday"
    if 14 <= start_hour < 16:
        return "Power Hour"
    if 16 <= start_hour < 20:
        return "Overlap"
    return "Asia"


def _write_csv(path: Path, rows: Sequence[HandoffRunPlanRow]) -> None:
    fieldnames = list(HandoffRunPlanRow.__dataclass_fields__.keys())
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))


def _write_json(path: Path, rows: Sequence[HandoffRunPlanRow]) -> None:
    payload = {
        "schema_version": 1,
        "template_count": len(rows),
        "rows": [asdict(row) for row in rows],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _write_markdown(path: Path, rows: Sequence[HandoffRunPlanRow]) -> None:
    lines = [
        "# Optimizer Run Plan",
        "",
        f"Templates: {len(rows)}",
        "",
        "| Template | Mode | Pass | Session | Start | Duration | Combos | Optimized Parameters |",
        "|---|---|---|---|---:|---:|---:|---|",
    ]
    for row in rows:
        lines.append(
            "| {template} | {mode} | {pass_id} | {session} | {start} | {duration} | {combos} | {params} |".format(
                template=row.template_name,
                mode=row.mode,
                pass_id=row.pass_id,
                session=row.session_bucket,
                start="" if row.start_hour is None else row.start_hour,
                duration="" if row.duration_hours is None else row.duration_hours,
                combos=row.estimated_combinations,
                params=row.optimized_parameters.replace("|", "\\|"),
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_team_readme(path: Path, rows: Sequence[HandoffRunPlanRow]) -> None:
    modes = sorted({row.mode for row in rows})
    lines = [
        "# Team Optimization Handoff",
        "",
        "Run each XML template in NinjaTrader Strategy Analyzer and return the exported result folder.",
        "",
        "Expected exports per completed template:",
        "",
        "- Settings.csv",
        "- Summary.csv",
        "- Analysis.csv",
        "- Trades.csv",
        "",
        f"Modes included: {', '.join(modes) if modes else 'none'}",
        f"Templates included: {len(rows)}",
        "",
        "Suggested review order:",
        "",
        "1. Run broad discovery templates first.",
        "2. Return results before running refinement templates unless a refinement package was explicitly requested.",
        "3. Keep output folder names aligned with the template names.",
        "4. Do not rename exported CSV files inside each result folder.",
        "",
        "Selection notes:",
        "",
        "- Review profit factor, total net profit, max drawdown dollars, and trade count together.",
        "- Watch for candidates where wins are fading near the end of the test period.",
        "- Keep slow-MA diversity instead of selecting only near-duplicate winners.",
        "- Keep Breakout and Regression results separated until final portfolio selection.",
        "",
        "See run_plan.csv or run_plan.md for the exact template list.",
        "",
        "Template folders:",
        "",
        "- templates/breakout/",
        "- templates/regression/",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _copy_templates(destination: Path, rows: Sequence[HandoffRunPlanRow]) -> None:
    for row in rows:
        source = Path(row.template_path)
        if not source.exists():
            continue
        pass_folder = _safe_folder_name(row.pass_id)
        target_dir = destination / row.mode / pass_folder
        target_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target_dir / source.name)


def _safe_folder_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in value).strip("_")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a team handoff run plan from NinjaTrader optimization XML templates.")
    parser.add_argument("--input-dir", required=True, help="Folder containing NinjaTrader Strategy Analyzer XML templates.")
    parser.add_argument("--output-dir", required=True, help="Folder where run_plan artifacts should be written.")
    parser.add_argument("--recursive", action="store_true", help="Search for XML templates recursively.")
    args = parser.parse_args(argv)

    rows = write_handoff_package(args.input_dir, args.output_dir, recursive=args.recursive)
    print(f"Wrote handoff package with {len(rows)} templates to {Path(args.output_dir)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
