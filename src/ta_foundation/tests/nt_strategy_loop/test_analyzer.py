from __future__ import annotations

import csv
from pathlib import Path

from ta_foundation.nt_strategy_loop.analyzer import Guardrails, analyze_optimization_csv


def _write_csv(path: Path, rows: list[list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "Instrument",
        "Performance",
        "Parameters",
        "Total net profit",
        "Gross profit",
        "Gross loss",
        "Profit factor",
        "Max. drawdown",
        "Total # of trades",
        "Percent profitable",
        "",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(fieldnames)
        writer.writerows(rows)


def test_analyze_optimization_csv_flags_no_passing_rows(tmp_path: Path) -> None:
    path = tmp_path / "X_Optimization.csv"
    _write_csv(
        path,
        [
            ["NQ", "1.10", "9/21/24/16/false (FastPeriod SlowPeriod ProfitTargetTicks StopLossTicks Reverse )",
             "100", "500", "-400", "1.10", "-1200", "7", "45%", ""],
        ],
    )

    result = analyze_optimization_csv(path, Guardrails())
    assert result["row_count"] == 1
    assert result["passing_rows"] == 0
    assert "profit factor below 1.5" in result["reject_reasons"]


def test_analyze_optimization_csv_passes_when_thresholds_met(tmp_path: Path) -> None:
    path = tmp_path / "Y_Optimization.csv"
    _write_csv(
        path,
        [
            ["NQ", "2.10", "9/21/24/16/false (FastPeriod SlowPeriod ProfitTargetTicks StopLossTicks Reverse )",
             "1500", "3000", "-1500", "2.10", "-800", "30", "55%", ""],
        ],
    )

    result = analyze_optimization_csv(path, Guardrails(min_profit_factor=1.5, min_trades=10, max_drawdown=2500))
    assert result["passing_rows"] == 1
    assert result["best_row"]["passes_guardrails"] is True
