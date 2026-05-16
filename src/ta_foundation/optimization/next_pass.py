from __future__ import annotations

import argparse
import csv
import json
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence

from ta_foundation.optimization.evaluator import EvaluationConfig, evaluate_results
from ta_foundation.optimization.handoff import write_handoff_package
from ta_foundation.optimization.result_intake import IntakeResultRow, ingest_result_folder
from ta_foundation.optimization.template_generator import generate_candidate_refinement_template
from ta_foundation.optimization.template_generator import generate_daily_risk_template


@dataclass(frozen=True)
class NextPassLineageRow:
    source_run_id: str
    generated_template: str
    target_pass: str
    mode: str
    score: float
    total_net_profit: float | None
    profit_factor: float | None
    max_drawdown: float | None
    trades: int | None
    start_hour: int | None
    duration_hours: int | None
    average_fast: int | None
    average_slow: int | None
    max_stop: int | None
    max_tp_ratio: float | None
    long_enabled: str
    short_enabled: str
    reason: str


def create_pass2_refinement_handoff(
    seed_template: str | Path,
    results_dir: str | Path,
    output_dir: str | Path,
    *,
    count: int = 8,
    include_run_ids: Sequence[str] | None = None,
    average_fast_min: int = 2,
    average_fast_max: int = 10,
    config: EvaluationConfig = EvaluationConfig(),
) -> list[Path]:
    rows = ingest_result_folder(results_dir)
    by_run_id = {row.run_id: row for row in rows}
    candidates = _select_candidates(rows, config, include_run_ids, count)

    destination = Path(output_dir)
    generated_dir = destination / "generated_pass2_templates"
    _clear_generated_dir(generated_dir)
    generated: list[Path] = []
    lineage: list[NextPassLineageRow] = []
    for candidate in candidates:
        row = by_run_id[candidate.run_id]
        if not _has_refinement_settings(row):
            continue
        mode = "regression" if str(row.reverse).lower() == "true" else "breakout"
        target = generated_dir / mode / f"Pass2_{mode.capitalize()}_{_safe_name(row.run_id)}.xml"
        generated_path = generate_candidate_refinement_template(
            seed_template,
            target,
            start_hour=row.start_hour or 0,
            duration_hours=row.duration_hours or 2,
            reverse=mode == "regression",
            average_slow=row.average_slow or 200,
            max_stop=row.max_stop or 100,
            max_tp_ratio=row.max_tp_ratio or 1.0,
            average_fast_min=average_fast_min,
            average_fast_max=average_fast_max,
        )
        generated.append(generated_path)
        lineage.append(_lineage_row(row, candidate, generated_path, "pass2", mode))

    handoff_dir = destination / "team_handoff"
    write_handoff_package(generated_dir, handoff_dir, recursive=True)
    _write_lineage(destination, lineage)
    _write_lineage(handoff_dir, lineage)
    return generated


def create_pass3_risk_handoff(
    seed_template: str | Path,
    results_dir: str | Path,
    output_dir: str | Path,
    *,
    count: int = 8,
    include_run_ids: Sequence[str] | None = None,
    config: EvaluationConfig = EvaluationConfig(),
) -> list[Path]:
    rows = ingest_result_folder(results_dir)
    by_run_id = {row.run_id: row for row in rows}
    candidates = _select_candidates(rows, config, include_run_ids, count)

    destination = Path(output_dir)
    generated_dir = destination / "generated_pass3_templates"
    _clear_generated_dir(generated_dir)
    generated: list[Path] = []
    lineage: list[NextPassLineageRow] = []
    for candidate in candidates:
        row = by_run_id[candidate.run_id]
        if not _has_risk_settings(row):
            continue
        mode = "regression" if str(row.reverse).lower() == "true" else "breakout"
        target = generated_dir / mode / f"Pass3_{mode.capitalize()}_{_safe_name(row.run_id)}.xml"
        generated_path = generate_daily_risk_template(
            seed_template,
            target,
            start_hour=row.start_hour or 0,
            duration_hours=row.duration_hours or 2,
            reverse=mode == "regression",
            average_fast=row.average_fast or 5,
            average_slow=row.average_slow or 200,
            max_stop=row.max_stop or 100,
            max_tp_ratio=row.max_tp_ratio or 1.0,
            long_enabled=str(row.long_enabled).lower() == "true",
            short_enabled=str(row.short_enabled).lower() == "true",
        )
        generated.append(generated_path)
        lineage.append(_lineage_row(row, candidate, generated_path, "pass3", mode))

    handoff_dir = destination / "team_handoff"
    write_handoff_package(generated_dir, handoff_dir, recursive=True)
    _write_lineage(destination, lineage)
    _write_lineage(handoff_dir, lineage)
    return generated


def _select_candidates(
    rows: Sequence[IntakeResultRow],
    config: EvaluationConfig,
    include_run_ids: Sequence[str] | None,
    count: int,
):
    passing = [candidate for candidate in evaluate_results(rows, config) if candidate.status == "pass"]
    if not include_run_ids:
        return passing[:count]
    wanted = [run_id.strip().lower() for run_id in include_run_ids if run_id.strip()]
    by_id = {candidate.run_id.lower(): candidate for candidate in passing}
    selected = [by_id[run_id] for run_id in wanted if run_id in by_id]
    return selected[:count]


def _lineage_row(
    row: IntakeResultRow,
    candidate,
    generated_template: Path,
    target_pass: str,
    mode: str,
) -> NextPassLineageRow:
    return NextPassLineageRow(
        source_run_id=row.run_id,
        generated_template=str(generated_template),
        target_pass=target_pass,
        mode=mode,
        score=candidate.score,
        total_net_profit=row.total_net_profit,
        profit_factor=row.profit_factor,
        max_drawdown=row.max_drawdown,
        trades=row.trades,
        start_hour=row.start_hour,
        duration_hours=row.duration_hours,
        average_fast=row.average_fast,
        average_slow=row.average_slow,
        max_stop=row.max_stop,
        max_tp_ratio=row.max_tp_ratio,
        long_enabled=row.long_enabled,
        short_enabled=row.short_enabled,
        reason=candidate.reasons,
    )


def _write_lineage(destination: Path, rows: Sequence[NextPassLineageRow]) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    fieldnames = list(NextPassLineageRow.__dataclass_fields__.keys())
    with (destination / "next_pass_lineage.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))
    payload = {
        "schema_version": 1,
        "row_count": len(rows),
        "rows": [asdict(row) for row in rows],
    }
    (destination / "next_pass_lineage.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    _write_lineage_markdown(destination / "NEXT_PASS_SUMMARY.md", rows)


def _write_lineage_markdown(path: Path, rows: Sequence[NextPassLineageRow]) -> None:
    lines = [
        "# Next Pass Summary",
        "",
        f"Generated templates: {len(rows)}",
        "",
        "| Source Result | Target Pass | Mode | Score | PF | Net Profit | Max DD | Trades | Time | Key Settings |",
        "|---|---|---|---:|---:|---:|---:|---:|---|---|",
    ]
    for row in rows:
        time_window = f"{row.start_hour}:00 for {row.duration_hours}h" if row.start_hour is not None else ""
        settings = (
            f"fast {row.average_fast}, slow {row.average_slow}, "
            f"stop {row.max_stop}, TP ratio {row.max_tp_ratio}, "
            f"long {row.long_enabled}, short {row.short_enabled}"
        )
        lines.append(
            f"| {row.source_run_id} | {row.target_pass} | {row.mode} | {row.score:g} | "
            f"{_fmt(row.profit_factor)} | {_fmt(row.total_net_profit)} | {_fmt(row.max_drawdown)} | "
            f"{_fmt(row.trades)} | {time_window} | {settings} |"
        )
    if rows:
        lines.extend(["", "## Generated Files", ""])
        for row in rows:
            lines.append(f"- {row.source_run_id}: `{row.generated_template}`")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _fmt(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def _clear_generated_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)


def _has_refinement_settings(row: IntakeResultRow) -> bool:
    return (
        row.start_hour is not None
        and row.duration_hours is not None
        and row.average_slow is not None
        and row.max_stop is not None
        and row.max_tp_ratio is not None
    )


def _has_risk_settings(row: IntakeResultRow) -> bool:
    return _has_refinement_settings(row) and row.average_fast is not None


def _safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in value).strip("_") or "candidate"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create next-pass optimization templates from passing result folders.")
    parser.add_argument("--seed-template", required=True, help="Seed Strategy Analyzer XML template.")
    parser.add_argument("--results-dir", required=True, help="Folder containing returned NinjaTrader CSV results.")
    parser.add_argument("--output-dir", required=True, help="Folder where next-pass templates and handoff should be written.")
    parser.add_argument("--target-pass", choices=("pass2", "pass3"), default="pass2")
    parser.add_argument("--count", type=int, default=8)
    parser.add_argument("--include-run-ids", default="", help="Comma-separated run IDs to generate next-pass templates for.")
    parser.add_argument("--average-fast-min", type=int, default=2)
    parser.add_argument("--average-fast-max", type=int, default=10)
    parser.add_argument("--max-drawdown", type=float, default=2500.0)
    parser.add_argument("--min-trades", type=int, default=10)
    parser.add_argument("--min-profit-factor", type=float, default=1.5)
    parser.add_argument("--min-percent-days-traded", type=float, default=20.0)
    args = parser.parse_args(argv)

    config = EvaluationConfig(
        max_drawdown=args.max_drawdown,
        min_trades=args.min_trades,
        min_profit_factor=args.min_profit_factor,
        min_percent_days_traded=args.min_percent_days_traded,
    )
    include_run_ids = tuple(part.strip() for part in args.include_run_ids.split(",") if part.strip())
    if args.target_pass == "pass3":
        generated = create_pass3_risk_handoff(
            args.seed_template,
            args.results_dir,
            args.output_dir,
            count=args.count,
            include_run_ids=include_run_ids,
            config=config,
        )
        label = "pass-3 daily risk"
    else:
        generated = create_pass2_refinement_handoff(
            args.seed_template,
            args.results_dir,
            args.output_dir,
            count=args.count,
            include_run_ids=include_run_ids,
            average_fast_min=args.average_fast_min,
            average_fast_max=args.average_fast_max,
            config=config,
        )
        label = "pass-2 refinement"
    print(f"Generated {len(generated)} {label} templates in {Path(args.output_dir)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
