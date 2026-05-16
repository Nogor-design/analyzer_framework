from __future__ import annotations

import argparse
import csv
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence

from ta_foundation.optimization.evaluator import EvaluatedCandidate, EvaluationConfig, write_evaluation
from ta_foundation.optimization.recommendations import Recommendation, write_recommendations
from ta_foundation.optimization.result_intake import write_intake_summary


@dataclass(frozen=True)
class SettingsContractViolation:
    run_id: str
    setting: str
    expected: str
    actual: str
    severity: str
    message: str


@dataclass(frozen=True)
class ReviewManifest:
    schema_version: int
    review_kind: str
    validation_status: str
    input_dir: str
    output_dir: str
    validation_filters: dict[str, float | int]
    settings_contract: dict[str, str]
    artifacts: dict[str, str]
    candidate_count: int
    passed_count: int
    rejected_count: int
    recommendation_count: int
    settings_contract_violation_count: int


def write_result_review(
    input_dir: str | Path,
    output_dir: str | Path,
    *,
    count: int = 8,
    config: EvaluationConfig = EvaluationConfig(),
    review_kind: str = "final-backtest",
) -> Path:
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    write_intake_summary(input_dir, destination)
    candidates = write_evaluation(input_dir, destination, config)
    recommendations = write_recommendations(input_dir, destination, count=count, config=config)
    settings_violations = _settings_contract_violations(candidates, review_kind)
    _write_settings_violations(destination / "settings_contract_violations.csv", settings_violations)
    _write_review_manifest(
        destination / "review_manifest.json",
        input_dir,
        candidates,
        recommendations,
        settings_violations,
        review_kind,
        config,
    )
    _write_review_summary_json(
        destination / "review_summary.json",
        input_dir,
        candidates,
        recommendations,
        settings_violations,
        config,
        review_kind,
    )
    _write_review_summary(
        destination / "REVIEW_SUMMARY.md",
        input_dir,
        candidates,
        recommendations,
        settings_violations,
        config,
        review_kind,
    )
    return destination


def _settings_contract_violations(
    candidates: Sequence[EvaluatedCandidate],
    review_kind: str,
) -> list[SettingsContractViolation]:
    if review_kind != "final-backtest":
        return []

    violations: list[SettingsContractViolation] = []
    expected = {"UseTrend": "false", "UseTrendReverse": "false"}
    for candidate in candidates:
        actual_values = {
            "UseTrend": candidate.use_trend,
            "UseTrendReverse": candidate.use_trend_reverse,
        }
        for setting, expected_value in expected.items():
            actual = str(actual_values.get(setting, "")).strip()
            if actual.lower() != expected_value:
                violations.append(
                    SettingsContractViolation(
                        run_id=candidate.run_id,
                        setting=setting,
                        expected=expected_value,
                        actual=actual,
                        severity="warning",
                        message=(
                            f"{setting} returned as {actual or '<missing>'}; "
                            f"final Backtest validation expects {expected_value}."
                        ),
                    )
                )
    return violations


def _validation_status(
    candidates: Sequence[EvaluatedCandidate],
    recommendations: Sequence[Recommendation],
    settings_violations: Sequence[SettingsContractViolation],
) -> str:
    if settings_violations:
        return "settings_warning"
    if not candidates:
        return "no_results"
    if not recommendations:
        return "no_passing_runs"
    return "valid"


def _write_settings_violations(path: Path, rows: Sequence[SettingsContractViolation]) -> None:
    fieldnames = list(SettingsContractViolation.__dataclass_fields__.keys())
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))


def _write_review_manifest(
    path: Path,
    input_dir: str | Path,
    candidates: Sequence[EvaluatedCandidate],
    recommendations: Sequence[Recommendation],
    settings_violations: Sequence[SettingsContractViolation],
    review_kind: str,
    config: EvaluationConfig,
) -> None:
    passed = sum(1 for candidate in candidates if candidate.status == "pass")
    manifest = ReviewManifest(
        schema_version=2,
        review_kind=review_kind,
        validation_status=_validation_status(candidates, recommendations, settings_violations),
        input_dir=str(Path(input_dir)),
        output_dir=str(path.parent),
        validation_filters=asdict(config),
        settings_contract=_settings_contract(review_kind),
        artifacts={
            "summary": "REVIEW_SUMMARY.md",
            "manifest": "review_manifest.json",
            "result_intake_csv": "result_intake.csv",
            "result_intake_json": "result_intake.json",
            "evaluated_candidates_csv": "evaluated_candidates.csv",
            "evaluated_candidates_json": "evaluated_candidates.json",
            "recommendations_csv": "recommendations.csv",
            "recommendations_json": "recommendations.json",
            "recommendations_md": "recommendations.md",
            "review_summary_json": "review_summary.json",
            "settings_contract_violations_csv": "settings_contract_violations.csv",
        },
        candidate_count=len(candidates),
        passed_count=passed,
        rejected_count=len(candidates) - passed,
        recommendation_count=len(recommendations),
        settings_contract_violation_count=len(settings_violations),
    )
    path.write_text(json.dumps(asdict(manifest), indent=2), encoding="utf-8")


def _write_review_summary_json(
    path: Path,
    input_dir: str | Path,
    candidates: Sequence[EvaluatedCandidate],
    recommendations: Sequence[Recommendation],
    settings_violations: Sequence[SettingsContractViolation],
    config: EvaluationConfig,
    review_kind: str,
) -> None:
    passed = [candidate for candidate in candidates if candidate.status == "pass"]
    payload = {
        "schema_version": 1,
        "review_kind": review_kind,
        "validation_status": _validation_status(candidates, recommendations, settings_violations),
        "input_dir": str(Path(input_dir)),
        "output_dir": str(path.parent),
        "validation_filters": asdict(config),
        "settings_contract": _settings_contract(review_kind),
        "counts": {
            "candidates": len(candidates),
            "passed": len(passed),
            "rejected": len(candidates) - len(passed),
            "recommendations": len(recommendations),
            "settings_contract_violations": len(settings_violations),
        },
        "top_recommendation": asdict(recommendations[0]) if recommendations else None,
        "recommendations": [asdict(row) for row in recommendations],
        "settings_contract_violations": [asdict(row) for row in settings_violations],
        "performance_groups": _performance_groups(recommendations),
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _write_review_summary(
    path: Path,
    input_dir: str | Path,
    candidates: Sequence[EvaluatedCandidate],
    recommendations: Sequence[Recommendation],
    settings_violations: Sequence[SettingsContractViolation],
    config: EvaluationConfig,
    review_kind: str,
) -> None:
    title = "Final Backtest Validation" if review_kind == "final-backtest" else "Optimizer Result Review"
    passed = sum(1 for candidate in candidates if candidate.status == "pass")
    trend_enabled = [candidate for candidate in candidates if str(candidate.use_trend).strip().lower() == "true"]
    lines = [
        f"# {title}",
        "",
        f"Input results: `{Path(input_dir)}`",
        f"Runs reviewed: {len(candidates)}",
        f"Passed validation filters: {passed}",
        f"Rejected by validation filters: {len(candidates) - passed}",
        f"Runs with UseTrend enabled: {len(trend_enabled)}",
        f"Settings contract violations: {len(settings_violations)}",
        f"Validation status: `{_validation_status(candidates, recommendations, settings_violations)}`",
        "",
        "This report ranks returned NinjaTrader Backtest results. It does not create a new optimizer phase and does not replace the phase-1 through phase-3 optimizer lineage artifacts.",
        "",
        "## Validation Filters",
        "",
        f"- Max drawdown: {config.max_drawdown:g}",
        f"- Min trades: {config.min_trades}",
        f"- Min profit factor: {config.min_profit_factor:g}",
        f"- Min percent days traded: {config.min_percent_days_traded:g}%",
        "",
        "## Top Validated Runs",
        "",
        "| Rank | Run | Score | PF | Net Profit | Max DD | Trades | Daily Risk |",
        "|---:|---|---:|---:|---:|---:|---:|---|",
    ]
    for row in recommendations:
        lines.append(
            f"| {row.rank} | {row.run_id} | {row.score:g} | {_fmt(row.profit_factor)} | "
            f"{_fmt(row.total_net_profit)} | {_fmt(row.max_drawdown)} | {_fmt(row.trades)} | "
            f"ProfitStop {_fmt(row.profit_stop)}, LossStop {_fmt(row.loss_stop)}, MaxTrades {_fmt(row.max_trades)} |"
        )
    if not recommendations:
        lines.append("|  | No passing runs |  |  |  |  |  |  |")
    performance_groups = [group for group in _performance_groups(recommendations) if len(group["run_ids"]) > 1]
    if performance_groups:
        lines.extend(["", "## Performance Groups", ""])
        for index, group in enumerate(performance_groups, start=1):
            run_ids = ", ".join(str(run_id) for run_id in group["run_ids"])
            lines.append(
                f"{index}. Score {_fmt(group['score'])}, PF {_fmt(group['profit_factor'])}, "
                f"net {_fmt(group['total_net_profit'])}, max DD {_fmt(group['max_drawdown'])}, "
                f"trades {_fmt(group['trades'])}: {run_ids}"
            )
    if trend_enabled:
        lines.extend(
            [
                "",
                "## Settings Warning",
                "",
                "One or more returned Backtest runs have `UseTrend=True`. If the intended validation contract requires `UseTrend=False`, rerun the fixed templates before using these rankings for deployment decisions.",
            ]
        )
    lines.extend(
        [
            "",
            "## Artifacts",
            "",
            "- `result_intake.csv` and `result_intake.json`: parsed NinjaTrader Summary, Settings, and Trades output.",
            "- `evaluated_candidates.csv` and `evaluated_candidates.json`: validation score, pass/reject status, and reasons.",
            "- `recommendations.csv`, `recommendations.json`, and `recommendations.md`: top validated candidates for deployment review.",
            "- `review_summary.json`: compact UI-facing status, counts, recommendations, and performance groups.",
            "- `settings_contract_violations.csv`: final Backtest settings contract warnings, if any.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _performance_groups(recommendations: Sequence[Recommendation]) -> list[dict[str, object]]:
    grouped: dict[tuple[object, ...], list[Recommendation]] = {}
    for row in recommendations:
        key = (
            row.score,
            row.total_net_profit,
            row.profit_factor,
            row.max_drawdown,
            row.trades,
            row.percent_days_traded,
        )
        grouped.setdefault(key, []).append(row)

    groups: list[dict[str, object]] = []
    for rows in grouped.values():
        first = rows[0]
        groups.append(
            {
                "score": first.score,
                "total_net_profit": first.total_net_profit,
                "profit_factor": first.profit_factor,
                "max_drawdown": first.max_drawdown,
                "trades": first.trades,
                "percent_days_traded": first.percent_days_traded,
                "run_ids": [row.run_id for row in rows],
                "daily_risk_variants": [
                    {
                        "run_id": row.run_id,
                        "profit_stop": row.profit_stop,
                        "loss_stop": row.loss_stop,
                        "max_trades": row.max_trades,
                    }
                    for row in rows
                ],
            }
        )
    return sorted(groups, key=lambda group: (-float(group["score"] or 0), str(group["run_ids"])))


def _settings_contract(review_kind: str) -> dict[str, str]:
    if review_kind != "final-backtest":
        return {}
    return {"UseTrend": "false", "UseTrendReverse": "false"}


def _fmt(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a full optimizer result review from returned NinjaTrader CSV results.")
    parser.add_argument("--input-dir", required=True, help="Folder containing returned NinjaTrader CSV results.")
    parser.add_argument("--output-dir", required=True, help="Folder where review artifacts should be written.")
    parser.add_argument("--count", type=int, default=8)
    parser.add_argument("--max-drawdown", type=float, default=2500.0)
    parser.add_argument("--min-trades", type=int, default=10)
    parser.add_argument("--min-profit-factor", type=float, default=1.5)
    parser.add_argument("--min-percent-days-traded", type=float, default=20.0)
    parser.add_argument(
        "--review-kind",
        choices=("final-backtest", "optimizer"),
        default="final-backtest",
        help="Labeling/context for the review summary. Does not change scoring behavior.",
    )
    args = parser.parse_args(argv)

    config = EvaluationConfig(
        max_drawdown=args.max_drawdown,
        min_trades=args.min_trades,
        min_profit_factor=args.min_profit_factor,
        min_percent_days_traded=args.min_percent_days_traded,
    )
    destination = write_result_review(args.input_dir, args.output_dir, count=args.count, config=config, review_kind=args.review_kind)
    print(f"Wrote {args.review_kind} result review to {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
