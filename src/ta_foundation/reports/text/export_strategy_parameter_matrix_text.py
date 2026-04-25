from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from ta_foundation.core.model import AnalysisPackage
from ta_foundation.reports.executive_parameter_matrix import (
    build_executive_parameter_matrix,
)


def _fmt_num(value: Any, decimals: int = 2) -> str:
    if value is None or value == "":
        return "-"
    try:
        numeric = float(value)
        if abs(numeric - round(numeric)) < 1e-9:
            return str(int(round(numeric)))
        return f"{numeric:.{decimals}f}"
    except Exception:
        return str(value)


def _fmt_money(value: Any) -> str:
    if value is None or value == "":
        return "-"
    try:
        numeric = float(value)
        sign = "-" if numeric < 0 else ""
        return f"{sign}${abs(numeric):,.0f}"
    except Exception:
        return str(value)


def _fmt_pct(value: Any, decimals: int = 1) -> str:
    if value is None or value == "":
        return "-"
    try:
        return f"{float(value):.{decimals}f}%"
    except Exception:
        return str(value)


def _row_values(row: Dict[str, Any]) -> List[str]:
    return [
        str(row.get("run_id") or "-"),
        str(row.get("period") or "-"),
        str(row.get("instrument") or "-"),
        _fmt_num(row.get("tick_value"), 2),
        str(row.get("direction") or "-"),
        str(row.get("contracts") or "-"),
        str(row.get("active_window") or "-"),
        str(row.get("chart_label") or "-"),
        str(row.get("label") or "-"),
        str(row.get("fast_ma") or "-"),
        str(row.get("slow_ma") or "-"),
        str(row.get("trend_ma") or "-"),
        str(row.get("max_trades") or "-"),
        _fmt_num(row.get("max_stop"), 0),
        _fmt_num(row.get("tp_ratio"), 2),
        _fmt_num(row.get("max_take_profit"), 0),
        _fmt_money(row.get("total_net_profit")),
        _fmt_money(row.get("max_drawdown")),
        _fmt_pct(row.get("win_rate_pct"), 2),
        _fmt_num(row.get("profit_factor"), 2),
        _fmt_num(row.get("total_trades"), 0),
        _fmt_money(row.get("avg_win")),
        _fmt_money(row.get("avg_loss")),
        _fmt_money(row.get("avg_mae")),
        _fmt_money(row.get("avg_mfe")),
        _fmt_money(row.get("avg_etd")),
        _fmt_num(row.get("mae_mfe_ratio"), 2),
        str(row.get("mae_mfe_rating") or "-"),
        _fmt_num(row.get("mfe_etd_ratio"), 2),
        str(row.get("mfe_etd_rating") or "-"),
        _fmt_money(row.get("best_day")),
        _fmt_money(row.get("worst_day")),
        _fmt_money(row.get("max_potential_profit")),
        _fmt_money(row.get("max_potential_loss")),
        _fmt_money(row.get("long_profit")),
        _fmt_pct(row.get("long_win_rate_pct"), 1),
        _fmt_money(row.get("short_profit")),
        _fmt_pct(row.get("short_win_rate_pct"), 1),
    ]


def export_strategy_parameter_matrix_text(
    packages: Dict[str, AnalysisPackage],
    output_path: Path,
    *,
    options: Dict[str, Any] | None = None,
    title: str = "Executive Parameter Matrix",
) -> Path:
    opts = options or {}
    rows = build_executive_parameter_matrix(
        packages,
        sort_by=str(opts.get("sort_by") or "run_id"),
    )

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    generated = datetime.now().strftime("%Y-%m-%d %H:%M")
    headers = [
        "Run",
        "Period",
        "Instrument",
        "Tick",
        "Direction",
        "Contracts",
        "Active Window",
        "Chart",
        "Label",
        "Fast MA",
        "Slow MA",
        "Trend MA",
        "Max Trades",
        "Max Stop",
        "TP Ratio",
        "Max TP",
        "Total Net",
        "Max DD",
        "Win Rate",
        "PF",
        "Total Trades",
        "Avg Win",
        "Avg Loss",
        "Avg MAE",
        "Avg MFE",
        "Avg ETD",
        "MAE/MFE",
        "MAE/MFE Rating",
        "MFE/ETD",
        "MFE/ETD Rating",
        "Best Day",
        "Worst Day",
        "Max Pot Profit",
        "Max Pot Loss",
        "Long Profit",
        "Long WR",
        "Short Profit",
        "Short WR",
    ]

    lines = [
        "EXECUTIVE PARAMETER MATRIX",
        f"Report:\t{title}",
        f"Generated:\t{generated}",
        f"Runs:\t{len(rows)}",
        "",
        "\t".join(headers),
    ]
    lines.extend("\t".join(_row_values(row)) for row in rows)

    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output_path
