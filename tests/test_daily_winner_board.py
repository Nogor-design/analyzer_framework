from __future__ import annotations

from pathlib import Path

import pandas as pd

from ta_foundation.core.model import AnalysisPackage
from ta_foundation.reports.daily_winner_board import build_daily_winner_board
from ta_foundation.reports.html.sections.daily_winner_spotlight import (
    render_daily_winner_spotlight,
)
from ta_foundation.reports.text.export_daily_winner_text import export_daily_winner_text


def _pkg(run_id: str, day_pnls: dict[str, float], *, start_hour: int = 2, duration_hour: int = 3) -> AnalysisPackage:
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
        settings=pd.DataFrame(
            [
                {"item": "Start_Time_(HH)", "value": start_hour},
                {"item": "Start_Time_(mm)", "value": 0},
                {"item": "Duration_Time_(HH)", "value": duration_hour},
                {"item": "Duration_Time_(mm)", "value": 0},
            ]
        ),
    )


def test_daily_winner_board_ranks_target_day_profit() -> None:
    packages = {
        "RisePoseidon-NQ": _pkg(
            "RisePoseidon-NQ",
            {
                "2026-04-09": 300.0,
                "2026-04-10": 200.0,
                "2026-04-13": 400.0,
                "2026-04-14": 900.0,
            },
            start_hour=2,
        ),
        "CloseAtlas-NQ": _pkg(
            "CloseAtlas-NQ",
            {
                "2026-04-09": 100.0,
                "2026-04-10": 600.0,
                "2026-04-13": 200.0,
                "2026-04-14": 700.0,
            },
            start_hour=13,
        ),
    }

    board = build_daily_winner_board(
        packages,
        target_date=pd.Timestamp("2026-04-14").date(),
        top_n=10,
        strip_days=5,
    )

    assert board["winner"]["run_id"] == "RisePoseidon-NQ"
    assert board["runner_up"]["run_id"] == "CloseAtlas-NQ"
    assert board["summary"]["lead_amount"] == 200.0
    assert board["rows"][0]["active_window"]["label"] == "02:00-05:00 MT"


def test_daily_winner_renderer_includes_insight_columns() -> None:
    packages = {
        "RisePoseidon-NQ": _pkg(
            "RisePoseidon-NQ",
            {
                "2026-04-09": 300.0,
                "2026-04-10": 200.0,
                "2026-04-13": 400.0,
                "2026-04-14": 900.0,
            },
            start_hour=2,
        ),
        "CloseAtlas-NQ": _pkg(
            "CloseAtlas-NQ",
            {
                "2026-04-09": 100.0,
                "2026-04-10": 600.0,
                "2026-04-13": 200.0,
                "2026-04-14": 700.0,
            },
            start_hour=13,
        ),
    }

    html = render_daily_winner_spotlight(
        {
            "packages": packages,
            "options": {"target_date": "2026-04-14", "top_n": 10},
        }
    )

    assert "Daily Winner Insight" in html
    assert "Lead Over #2" in html
    assert "Gap To #1" in html
    assert "02:00-05:00 MT" in html


def test_daily_winner_text_export_writes_summary(tmp_path: Path) -> None:
    packages = {
        "RisePoseidon-NQ": _pkg(
            "RisePoseidon-NQ",
            {
                "2026-04-09": 300.0,
                "2026-04-10": 200.0,
                "2026-04-13": 400.0,
                "2026-04-14": 900.0,
            },
            start_hour=2,
        ),
        "CloseAtlas-NQ": _pkg(
            "CloseAtlas-NQ",
            {
                "2026-04-09": 100.0,
                "2026-04-10": 600.0,
                "2026-04-13": 200.0,
                "2026-04-14": 700.0,
            },
            start_hour=13,
        ),
    }

    out_path = tmp_path / "daily-winner.txt"
    export_daily_winner_text(
        packages,
        out_path,
        options={"target_date": "2026-04-14"},
    )

    text = out_path.read_text(encoding="utf-8")
    assert "Winner:" in text
    assert "Lead Over #2:" in text
    assert "RisePoseidon-NQ" in text
    assert "02:00-05:00 MT" in text
