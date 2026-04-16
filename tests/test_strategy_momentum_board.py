from __future__ import annotations

from datetime import date

import pandas as pd

from ta_foundation.core.model import AnalysisPackage
from ta_foundation.reports.html.sections.strategy_momentum_board import (
    render_strategy_momentum_board,
)
from ta_foundation.reports.momentum_board import build_strategy_momentum_board
from ta_foundation.reports.text.export_strategy_momentum_text import (
    export_strategy_momentum_text,
)


def _pkg(run_id: str, day_pnls: dict[str, float]) -> AnalysisPackage:
    days = sorted(day_pnls.keys())
    return AnalysisPackage(
        run_id=run_id,
        daily=pd.DataFrame(
            {
                "date": pd.to_datetime(days),
                "net_profit": [day_pnls[d] for d in days],
                "trade_count": [1 if day_pnls[d] != 0 else 0 for d in days],
            }
        ),
    )


def test_strategy_momentum_board_ranks_improving_strategy_first() -> None:
    packages = {
        "Atlas": _pkg(
            "Atlas",
            {
                "2026-04-01": 20.0,
                "2026-04-02": 40.0,
                "2026-04-03": 50.0,
                "2026-04-06": 60.0,
                "2026-04-07": 30.0,
                "2026-04-08": 100.0,
                "2026-04-09": 150.0,
                "2026-04-10": 200.0,
                "2026-04-13": 250.0,
                "2026-04-14": 300.0,
            },
        ),
        "Boreas": _pkg(
            "Boreas",
            {
                "2026-04-01": 200.0,
                "2026-04-02": 200.0,
                "2026-04-03": 150.0,
                "2026-04-06": 180.0,
                "2026-04-07": 170.0,
                "2026-04-08": 50.0,
                "2026-04-09": -50.0,
                "2026-04-10": -80.0,
                "2026-04-13": 40.0,
                "2026-04-14": -60.0,
            },
        ),
        "Idle": _pkg(
            "Idle",
            {
                "2026-03-25": 110.0,
            },
        ),
    }

    board = build_strategy_momentum_board(
        packages,
        as_of=date.fromisoformat("2026-04-14"),
        top_n=10,
    )

    rows = board["rows"]
    assert rows[0]["run_id"] == "Atlas"
    assert rows[0]["status"] == "Improving"

    boreas = next(r for r in rows if r["run_id"] == "Boreas")
    idle = next(r for r in rows if r["run_id"] == "Idle")
    assert boreas["status"] == "Cooling"
    assert idle["status"] == "Inactive"
    assert board["summary"]["best_now"]["run_id"] == "Atlas"
    assert board["summary"]["biggest_improver"]["run_id"] == "Atlas"


def test_strategy_momentum_renderer_contains_table_and_statuses() -> None:
    packages = {
        "LongNamedStrategyInfernoPoseidonAtlas": _pkg(
            "LongNamedStrategyInfernoPoseidonAtlas",
            {
                "2026-04-01": 30.0,
                "2026-04-02": 25.0,
                "2026-04-03": 20.0,
                "2026-04-06": 15.0,
                "2026-04-07": 10.0,
                "2026-04-08": 90.0,
                "2026-04-09": 95.0,
                "2026-04-10": 100.0,
                "2026-04-13": 120.0,
                "2026-04-14": 140.0,
            },
        ),
    }

    html = render_strategy_momentum_board(
        {
            "packages": packages,
            "options": {"as_of_date": "2026-04-14", "top_n": 10},
        }
    )

    assert "Strategy Momentum Board" in html
    assert "LongNamedStrategyInfernoPoseidonAtlas" in html
    assert "Recent strip" in html
    assert "Improving" in html


def test_strategy_momentum_text_export_writes_summary(tmp_path) -> None:
    packages = {
        "Atlas": _pkg(
            "Atlas",
            {
                "2026-04-01": 20.0,
                "2026-04-02": 40.0,
                "2026-04-03": 50.0,
                "2026-04-06": 60.0,
                "2026-04-07": 30.0,
                "2026-04-08": 100.0,
                "2026-04-09": 150.0,
                "2026-04-10": 200.0,
                "2026-04-13": 250.0,
                "2026-04-14": 300.0,
            },
        ),
    }

    out_path = tmp_path / "momentum.txt"
    export_strategy_momentum_text(
        packages,
        out_path,
        options={"as_of_date": "2026-04-14"},
    )

    text = out_path.read_text(encoding="utf-8")
    assert "Strategy Momentum Board" in text
    assert "Best Now:" in text
    assert "#1  Atlas" in text
