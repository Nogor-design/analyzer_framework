from __future__ import annotations

import json
from datetime import date, timedelta

import pandas as pd

from ta_foundation.core.model import AnalysisPackage
from ta_foundation.reports.html.sections.strategy_lifecycle_board import (
    render_strategy_lifecycle_board,
)
from ta_foundation.reports.strategy_lifecycle_board import (
    build_strategy_lifecycle_board,
)
from ta_foundation.reports.text.export_strategy_lifecycle_text import (
    export_strategy_lifecycle_json,
    export_strategy_lifecycle_text,
)


def _weekday_series(end: date, values: list[float]) -> dict[str, float]:
    days: list[date] = []
    cursor = end
    while len(days) < len(values):
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor -= timedelta(days=1)
    days.reverse()
    return {d.isoformat(): v for d, v in zip(days, values)}


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


def test_lifecycle_board_prefers_recent_in_favor_strategy() -> None:
    as_of = date.fromisoformat("2026-05-14")
    hot_values = [
        -300, -200, -100, -150, -50,
        100, 120, 90, 130, 80,
        180, 220, -90, 250, 200,
        260, -120, 300, 280, 240,
    ]
    stale_values = [
        300, 280, 250, 240, 220,
        200, 180, 160, 120, 100,
        -80, -120, -90, -110, -70,
        -50, -60, -40, -80, -30,
    ]

    board = build_strategy_lifecycle_board(
        {
            "HotNow": _pkg("HotNow", _weekday_series(as_of, hot_values)),
            "StaleFormerWinner": _pkg("StaleFormerWinner", _weekday_series(as_of, stale_values)),
        },
        as_of=as_of,
        risk_budget=2500,
    )

    rows = board["rows"]
    hot = next(r for r in rows if r["run_id"] == "HotNow")
    stale = next(r for r in rows if r["run_id"] == "StaleFormerWinner")

    assert rows[0]["run_id"] == "HotNow"
    assert hot["lifecycle_state"] == "in_favor"
    assert hot["tradability"] == "trade_candidate"
    assert hot["best_window"].label in {"2w", "3w", "4w"}
    assert stale["lifecycle_state"] == "cooling"
    assert stale["tradability"] == "pause_or_reduce"


def test_lifecycle_board_blocks_unacceptable_drawdown() -> None:
    as_of = date.fromisoformat("2026-05-14")
    volatile = _weekday_series(
        as_of,
        [900, -2600, 1200, 1100, 900, 800, -100, 700, 600, 500],
    )

    board = build_strategy_lifecycle_board(
        {"Volatile": _pkg("Volatile", volatile)},
        as_of=as_of,
        risk_budget=2500,
    )

    row = board["rows"][0]
    assert row["risk_category"] == "blocked"
    assert row["tradability"] == "do_not_trade"


def test_lifecycle_board_defaults_to_latest_available_outcome_date() -> None:
    as_of = date.fromisoformat("2026-04-30")
    packages = {
        "HotNow": _pkg(
            "HotNow",
            _weekday_series(as_of, [50, 60, 70, 80, 90, 120, -50, 150, 160, 170]),
        )
    }

    board = build_strategy_lifecycle_board(packages, risk_budget=2500)

    assert board["as_of"] == as_of
    assert board["rows"][0]["tradability"] == "trade_candidate"


def test_lifecycle_text_and_html_renderers(tmp_path) -> None:
    as_of = date.fromisoformat("2026-05-14")
    packages = {
        "HotNow": _pkg(
            "HotNow",
            _weekday_series(as_of, [50, 60, 70, 80, 90, 120, -50, 150, 160, 170]),
        )
    }

    out_path = tmp_path / "lifecycle.txt"
    export_strategy_lifecycle_text(
        packages,
        out_path,
        options={"as_of_date": as_of.isoformat(), "risk_budget": 2500},
    )
    text = out_path.read_text(encoding="utf-8")
    assert "Strategy Lifecycle Board" in text
    assert "trade_candidate" in text

    json_path = tmp_path / "lifecycle.json"
    export_strategy_lifecycle_json(
        packages,
        json_path,
        options={"as_of_date": as_of.isoformat(), "risk_budget": 2500},
    )
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["summary"]["trade_candidates"] == 1
    assert payload["rows"][0]["tradability"] == "trade_candidate"
    assert payload["rows"][0]["windows"]["2w"]["pnl"] > 0

    html = render_strategy_lifecycle_board(
        {
            "packages": packages,
            "options": {"as_of_date": as_of.isoformat(), "risk_budget": 2500},
        }
    )
    assert "Strategy Lifecycle Board" in html
    assert "trade_candidate" in html
