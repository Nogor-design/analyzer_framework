from __future__ import annotations

import argparse
import csv
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence

from ta_foundation.optimization.result_intake import IntakeResultRow, ingest_result_folder


@dataclass(frozen=True)
class EvaluationConfig:
    max_drawdown: float = 2500.0
    min_trades: int = 10
    min_profit_factor: float = 1.5
    min_percent_days_traded: float = 20.0


@dataclass(frozen=True)
class EvaluatedCandidate:
    run_id: str
    status: str
    score: float
    mode: str
    session_bucket: str
    start_hour: int | None
    duration_hours: int | None
    total_net_profit: float | None
    profit_factor: float | None
    max_drawdown: float | None
    trades: int | None
    percent_days_traded: float | None
    recent_trade_delta: float | None
    average_fast: int | None
    average_slow: int | None
    use_trend: str
    use_trend_reverse: str
    slow_ma_family: str
    max_stop: int | None
    max_tp_ratio: float | None
    profit_stop: int | None
    loss_stop: int | None
    max_trades: int | None
    risk_shape: str
    direction: str
    bot_name: str
    reasons: str


def evaluate_results(rows: Sequence[IntakeResultRow], config: EvaluationConfig = EvaluationConfig()) -> list[EvaluatedCandidate]:
    return sorted(
        (_evaluate_row(row, config) for row in rows),
        key=lambda candidate: (candidate.status != "pass", -candidate.score, candidate.run_id.lower()),
    )


def write_evaluation(input_dir: str | Path, output_dir: str | Path, config: EvaluationConfig = EvaluationConfig()) -> list[EvaluatedCandidate]:
    rows = ingest_result_folder(input_dir)
    candidates = evaluate_results(rows, config)
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    _write_csv(destination / "evaluated_candidates.csv", candidates)
    _write_json(destination / "evaluated_candidates.json", candidates, config)
    return candidates


def _evaluate_row(row: IntakeResultRow, config: EvaluationConfig) -> EvaluatedCandidate:
    rejections: list[str] = []
    notes: list[str] = []
    drawdown_abs = abs(row.max_drawdown) if row.max_drawdown is not None else None

    if row.total_net_profit is None or row.total_net_profit <= 0:
        rejections.append("non-positive net profit")
    if row.profit_factor is None or row.profit_factor < config.min_profit_factor:
        rejections.append(f"profit factor below {config.min_profit_factor:g}")
    if drawdown_abs is None or drawdown_abs > config.max_drawdown:
        rejections.append(f"drawdown above {config.max_drawdown:g}")
    if row.trades is None or row.trades < config.min_trades:
        rejections.append(f"trades below {config.min_trades}")
    if row.percent_days_traded is not None and row.percent_days_traded < config.min_percent_days_traded:
        rejections.append(f"days traded below {config.min_percent_days_traded:g}%")
    if row.recent_trade_delta is not None and row.recent_trade_delta < 0:
        notes.append("recent trade profit fading")

    score = _score(row, config)
    status = "reject" if rejections else "pass"
    reasons = "; ".join(rejections + notes) if (rejections or notes) else "passed hard filters"

    return EvaluatedCandidate(
        run_id=row.run_id,
        status=status,
        score=round(score, 2),
        mode="regression" if str(row.reverse).lower() == "true" else "breakout",
        session_bucket=_session_bucket(row.start_hour),
        start_hour=row.start_hour,
        duration_hours=row.duration_hours,
        total_net_profit=row.total_net_profit,
        profit_factor=row.profit_factor,
        max_drawdown=row.max_drawdown,
        trades=row.trades,
        percent_days_traded=row.percent_days_traded,
        recent_trade_delta=row.recent_trade_delta,
        average_fast=row.average_fast,
        average_slow=row.average_slow,
        use_trend=row.use_trend,
        use_trend_reverse=row.use_trend_reverse,
        slow_ma_family=_slow_ma_family(row.average_slow),
        max_stop=row.max_stop,
        max_tp_ratio=row.max_tp_ratio,
        profit_stop=row.profit_stop,
        loss_stop=row.loss_stop,
        max_trades=row.max_trades,
        risk_shape=_risk_shape(row.max_stop, row.max_tp_ratio),
        direction=_direction(row.long_enabled, row.short_enabled),
        bot_name=row.bot_name,
        reasons=reasons,
    )


def _score(row: IntakeResultRow, config: EvaluationConfig) -> float:
    net_profit = max(0.0, row.total_net_profit or 0.0)
    profit_factor = max(0.0, row.profit_factor or 0.0)
    drawdown_abs = abs(row.max_drawdown or config.max_drawdown)
    trades = max(0.0, float(row.trades or 0))
    days = max(0.0, row.percent_days_traded or 0.0)
    recent = row.recent_trade_delta or 0.0

    profit_score = min(35.0, net_profit / 500.0)
    pf_score = min(25.0, profit_factor * 7.0)
    dd_score = max(0.0, 20.0 * (1.0 - min(drawdown_abs, config.max_drawdown) / config.max_drawdown))
    trade_score = min(10.0, trades / max(1.0, config.min_trades) * 5.0)
    days_score = min(10.0, days / 10.0)
    recent_score = max(-10.0, min(10.0, recent / 500.0))
    return profit_score + pf_score + dd_score + trade_score + days_score + recent_score


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


def _slow_ma_family(average_slow: int | None) -> str:
    if average_slow is None:
        return "unknown"
    if average_slow <= 150:
        return "fast_trend"
    if average_slow <= 300:
        return "middle_trend"
    return "slow_trend"


def _risk_shape(max_stop: int | None, max_tp_ratio: float | None) -> str:
    stop = max_stop or 0
    ratio = max_tp_ratio or 0.0
    stop_label = "tight_stop" if stop and stop <= 100 else "wide_stop" if stop >= 200 else "mid_stop"
    ratio_label = "high_rr" if ratio >= 1.5 else "low_rr" if ratio and ratio < 1.0 else "mid_rr"
    return f"{stop_label}_{ratio_label}"


def _direction(long_enabled: str, short_enabled: str) -> str:
    long_on = str(long_enabled).strip().lower() == "true"
    short_on = str(short_enabled).strip().lower() == "true"
    if long_on and short_on:
        return "both"
    if long_on:
        return "long"
    if short_on:
        return "short"
    return "off"


def _write_csv(path: Path, rows: Sequence[EvaluatedCandidate]) -> None:
    fieldnames = list(EvaluatedCandidate.__dataclass_fields__.keys())
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))


def _write_json(path: Path, rows: Sequence[EvaluatedCandidate], config: EvaluationConfig) -> None:
    payload = {
        "schema_version": 1,
        "config": asdict(config),
        "candidate_count": len(rows),
        "rows": [asdict(row) for row in rows],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate optimizer handoff result folders.")
    parser.add_argument("--input-dir", required=True, help="Folder containing returned NinjaTrader CSV results.")
    parser.add_argument("--output-dir", required=True, help="Folder where evaluated candidate artifacts should be written.")
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
    candidates = write_evaluation(args.input_dir, args.output_dir, config)
    passed = sum(1 for candidate in candidates if candidate.status == "pass")
    print(f"Evaluated {len(candidates)} candidates ({passed} passed) into {Path(args.output_dir)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
