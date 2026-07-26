from __future__ import annotations

from datetime import datetime

import pandas as pd

from ta_foundation.core.model import AnalysisPackage, SummaryBlock
from ta_foundation.reports.executive_parameter_matrix import (
    build_executive_parameter_matrix,
)
from ta_foundation.reports.html.sections.strategy_parameter_matrix import (
    render_strategy_parameter_matrix,
)
from ta_foundation.reports.text.export_strategy_parameter_matrix_text import (
    export_strategy_parameter_matrix_text,
)


def _pkg(run_id: str, *, total_profit: float, win_rate: float, direction: tuple[bool, bool]) -> AnalysisPackage:
    long_enabled, short_enabled = direction
    summary = SummaryBlock(
        kpis_all={
            "total_net_profit": total_profit,
            "max_drawdown": -600.0,
            "profit_factor": 1.73,
            "total_number_of_trades": 42,
            "avg_winning_trade": 180.0,
            "avg_losing_trade": -120.0,
            "avg_mae": -45.0,
            "avg_mfe": 150.0,
            "avg_etd": 40.0,
            "percent_profitable": win_rate,
        },
        kpis_long={
            "total_net_profit": total_profit * 0.6,
            "percent_profitable": 62.5,
        },
        kpis_short={
            "total_net_profit": total_profit * 0.4,
            "percent_profitable": 57.5,
        },
        start_dt=datetime(2026, 4, 1),
        end_dt=datetime(2026, 4, 15),
    )
    pkg = AnalysisPackage(
        run_id=run_id,
        daily=pd.DataFrame(
            {
                "date": pd.to_datetime(["2026-04-14", "2026-04-15"]),
                "net_profit": [250.0, -100.0],
            }
        ),
        summary=summary,
        settings=pd.DataFrame(
            [
                {"item": "AverageFast", "value": 9},
                {"item": "AverageSlow", "value": 21},
                {"item": "AverageTrend", "value": 50},
                {"item": "Long", "value": long_enabled},
                {"item": "Short", "value": short_enabled},
                {"item": "Contracts", "value": 2},
                {"item": "Start_Time_(HH)", "value": 2},
                {"item": "Start_Time_(mm)", "value": 0},
                {"item": "Duration_Time_(HH)", "value": 3},
                {"item": "Duration_Time_(mm)", "value": 0},
                {"item": "Type", "value": "Minute"},
                {"item": "Value", "value": 5},
                {"item": "Label", "value": "Trend"},
                {"item": "MaxTrades", "value": 1},
                {"item": "MaxStop", "value": 40},
                {"item": "MaxTPRatio", "value": 1.5},
            ]
        ),
        metadata={
            "derived": {
                "instrument": "NQ",
                "tick_value_usd": 5.0,
                "max_potential_profit_usd": 300.0,
                "max_potential_loss_usd": -200.0,
            }
        },
    )
    return pkg


def test_build_executive_parameter_matrix_collects_expected_fields() -> None:
    rows = build_executive_parameter_matrix(
        {
            "RiseAtlas-NQ": _pkg("RiseAtlas-NQ", total_profit=3200.0, win_rate=66.7, direction=(True, False)),
        }
    )

    assert len(rows) == 1
    row = rows[0]
    assert row["run_id"] == "RiseAtlas-NQ"
    assert row["direction"] == "Long Only"
    assert row["active_window"] == "02:00 - 05:00 Colorado"
    assert row["max_take_profit"] == 60.0
    assert row["total_net_profit"] == 3200.0
    assert row["mae_mfe_rating"] == "Excellent"


def test_render_strategy_parameter_matrix_contains_grouped_table() -> None:
    html = render_strategy_parameter_matrix(
        {
            "packages": {
                "RiseAtlas-NQ": _pkg("RiseAtlas-NQ", total_profit=3200.0, win_rate=66.7, direction=(True, False)),
                "CoilHermes-NQ": _pkg("CoilHermes-NQ", total_profit=1800.0, win_rate=58.2, direction=(True, True)),
            },
            "options": {"sort_by": "run_id"},
        }
    )

    assert "Executive Parameter Matrix" in html
    assert "One-row-per-bot reference sheet" in html
    assert "Identity" in html
    assert "Trade Quality" in html
    assert "RiseAtlas-NQ" in html
    assert "CoilHermes-NQ" in html
    assert "02:00 - 05:00 Colorado" in html


def test_export_strategy_parameter_matrix_text_writes_tabular_output(tmp_path) -> None:
    out_path = tmp_path / "matrix.txt"
    export_strategy_parameter_matrix_text(
        {
            "RiseAtlas-NQ": _pkg("RiseAtlas-NQ", total_profit=3200.0, win_rate=66.7, direction=(True, False)),
        },
        out_path,
        title="Executive Parameter Matrix",
    )

    text = out_path.read_text(encoding="utf-8")
    assert "EXECUTIVE PARAMETER MATRIX" in text
    assert "Run\tPeriod\tInstrument" in text
    assert "RiseAtlas-NQ" in text
    assert "$3,200" in text
