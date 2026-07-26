from __future__ import annotations

import argparse
import csv
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence

from ta_foundation.optimization.evaluator import (
    EvaluatedCandidate,
    EvaluationConfig,
    evaluate_results,
)
from ta_foundation.optimization.result_intake import ingest_result_folder


@dataclass(frozen=True)
class Recommendation:
    rank: int
    run_id: str
    mode: str
    session_bucket: str
    score: float
    total_net_profit: float | None
    profit_factor: float | None
    max_drawdown: float | None
    trades: int | None
    percent_days_traded: float | None
    profit_stop: int | None
    loss_stop: int | None
    max_trades: int | None
    use_trend: str
    use_trend_reverse: str
    slow_ma_family: str
    risk_shape: str
    direction: str
    reason: str


def build_recommendations(candidates: Sequence[EvaluatedCandidate], count: int = 8) -> list[Recommendation]:
    passing = [candidate for candidate in candidates if candidate.status == "pass"]
    selected: list[EvaluatedCandidate] = []

    # First pass: prefer one per mode/session bucket so time specialists survive.
    for candidate in passing:
        if len(selected) >= count:
            break
        if not any(c.mode == candidate.mode and c.session_bucket == candidate.session_bucket for c in selected):
            selected.append(candidate)

    # Second pass: add more candidates while avoiding exact strategy-shape clones.
    for candidate in passing:
        if len(selected) >= count:
            break
        if candidate in selected:
            continue
        shape = (candidate.mode, candidate.session_bucket, candidate.slow_ma_family, candidate.risk_shape, candidate.direction)
        if any((c.mode, c.session_bucket, c.slow_ma_family, c.risk_shape, c.direction) == shape for c in selected):
            continue
        selected.append(candidate)

    # Final pass: fill with strongest remaining candidates if the pool is small.
    for candidate in passing:
        if len(selected) >= count:
            break
        if candidate not in selected:
            selected.append(candidate)

    return [_recommendation(rank, candidate) for rank, candidate in enumerate(selected, start=1)]


def write_recommendations(
    input_dir: str | Path,
    output_dir: str | Path,
    *,
    count: int = 8,
    config: EvaluationConfig = EvaluationConfig(),
) -> list[Recommendation]:
    rows = ingest_result_folder(input_dir)
    candidates = evaluate_results(rows, config)
    recommendations = build_recommendations(candidates, count=count)
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    _write_csv(destination / "recommendations.csv", recommendations)
    _write_json(destination / "recommendations.json", recommendations, candidates, config)
    _write_markdown(destination / "recommendations.md", recommendations, candidates)
    return recommendations


def _recommendation(rank: int, candidate: EvaluatedCandidate) -> Recommendation:
    reason = (
        f"Best available {candidate.mode} candidate for {candidate.session_bucket}; "
        f"{candidate.slow_ma_family}, {candidate.risk_shape}, {candidate.direction}; "
        f"{candidate.reasons}."
    )
    return Recommendation(
        rank=rank,
        run_id=candidate.run_id,
        mode=candidate.mode,
        session_bucket=candidate.session_bucket,
        score=candidate.score,
        total_net_profit=candidate.total_net_profit,
        profit_factor=candidate.profit_factor,
        max_drawdown=candidate.max_drawdown,
        trades=candidate.trades,
        percent_days_traded=candidate.percent_days_traded,
        profit_stop=candidate.profit_stop,
        loss_stop=candidate.loss_stop,
        max_trades=candidate.max_trades,
        use_trend=candidate.use_trend,
        use_trend_reverse=candidate.use_trend_reverse,
        slow_ma_family=candidate.slow_ma_family,
        risk_shape=candidate.risk_shape,
        direction=candidate.direction,
        reason=reason,
    )


def _write_csv(path: Path, rows: Sequence[Recommendation]) -> None:
    fieldnames = list(Recommendation.__dataclass_fields__.keys())
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))


def _write_json(
    path: Path,
    recommendations: Sequence[Recommendation],
    candidates: Sequence[EvaluatedCandidate],
    config: EvaluationConfig,
) -> None:
    payload = {
        "schema_version": 1,
        "config": asdict(config),
        "recommended_count": len(recommendations),
        "candidate_count": len(candidates),
        "recommendations": [asdict(row) for row in recommendations],
        "rejected": [asdict(candidate) for candidate in candidates if candidate.status != "pass"],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _write_markdown(
    path: Path,
    recommendations: Sequence[Recommendation],
    candidates: Sequence[EvaluatedCandidate],
) -> None:
    lines = [
        "# Optimizer Recommendations",
        "",
        f"Recommended: {len(recommendations)}",
        f"Rejected: {sum(1 for candidate in candidates if candidate.status != 'pass')}",
        "",
        "| Rank | Template | Mode | Session | Score | PF | Net Profit | Max DD | Trades | Daily Risk | Trend | Shape |",
        "|---:|---|---|---|---:|---:|---:|---:|---:|---|---|---|",
    ]
    for row in recommendations:
        lines.append(
            f"| {row.rank} | {row.run_id} | {row.mode} | {row.session_bucket} | "
            f"{row.score:g} | {_fmt(row.profit_factor)} | {_fmt(row.total_net_profit)} | "
            f"{_fmt(row.max_drawdown)} | {_fmt(row.trades)} | "
            f"P {_fmt(row.profit_stop)} / L {_fmt(row.loss_stop)} / T {_fmt(row.max_trades)} | "
            f"UseTrend {_fmt(row.use_trend)} | "
            f"{row.slow_ma_family}, {row.risk_shape}, {row.direction} |"
        )
    if recommendations:
        lines.extend(["", "## Reasons", ""])
        for row in recommendations:
            lines.append(f"{row.rank}. **{row.run_id}**: {row.reason}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _fmt(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Recommend diverse optimizer candidates from returned NinjaTrader CSV results.")
    parser.add_argument("--input-dir", required=True, help="Folder containing returned NinjaTrader CSV results.")
    parser.add_argument("--output-dir", required=True, help="Folder where recommendation artifacts should be written.")
    parser.add_argument("--count", type=int, default=8)
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
    recommendations = write_recommendations(
        args.input_dir,
        args.output_dir,
        count=args.count,
        config=config,
    )
    print(f"Wrote {len(recommendations)} recommendations to {Path(args.output_dir)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
