from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from ta_foundation.core.daily_outcomes import derive_daily_outcomes_for_package
from ta_foundation.core.model import AnalysisPackage
from ta_foundation.reports.html.sections._session_timeline import render_session_timeline
from ta_foundation.reports.html.sections._wlr_strip import compute_shared_trading_days
from ta_foundation.reports.html.sections.weekly_leaderboard_cards import (
    _recent_trading_days,
    _resolve_dashboard_window,
    _snapshot_lists_for_pkg,
)

TZ_DENVER = ZoneInfo("America/Denver")


def test_daily_outcomes_use_analysis_daily_without_trades() -> None:
    pkg = AnalysisPackage(
        run_id="RisePoseidonHunterB-NQ",
        daily=pd.DataFrame(
            {
                "date": pd.to_datetime(
                    ["2026-04-10", "2026-04-11", "2026-04-12"]
                ).tz_localize(TZ_DENVER),
                "net_profit": [1120.0, -220.0, 0.0],
                "trade_count": [1, 1, 0],
            }
        ),
    )

    outcomes = derive_daily_outcomes_for_package(pkg)

    assert outcomes["source"] == "daily"
    assert outcomes["by_date"]["2026-04-10"]["status"] == "WIN"
    assert outcomes["by_date"]["2026-04-11"]["status"] == "LOSS"
    assert outcomes["by_date"]["2026-04-12"]["status"] == "NO_TRADE"


def test_shared_trading_days_end_on_today() -> None:
    pkg = AnalysisPackage(run_id="RisePoseidonHunterB-NQ")
    pkg.metadata = {
        "derived": {
            "daily_outcomes": {
                "by_date": {
                    "2026-04-08": {"status": "WIN", "net_profit": 100.0, "trades": 1},
                    "2026-04-10": {"status": "LOSS", "net_profit": -50.0, "trades": 1},
                }
            }
        }
    }

    shared_days = compute_shared_trading_days({"RisePoseidonHunterB-NQ": pkg}, days_back=5)

    assert len(shared_days) == 5
    assert shared_days[-1] == datetime.now(TZ_DENVER).date().isoformat()


def test_session_timeline_prefers_settings_window() -> None:
    pkg = AnalysisPackage(
        run_id="RisePoseidonHunterB-NQ",
        settings=pd.DataFrame(
            [
                {"section": "Strategy parameters", "item": "Start_Time_(HH)", "value": 2},
                {"section": "Strategy parameters", "item": "Start_Time_(mm)", "value": 0},
                {"section": "Strategy parameters", "item": "Duration_Time_(HH)", "value": 3},
                {"section": "Strategy parameters", "item": "Duration_Time_(mm)", "value": 0},
            ]
        ),
    )

    html = render_session_timeline(
        pkg.run_id,
        pkg,
        render_bin_minutes=60,
        show_summary=False,
        prefer_settings_window=True,
    )

    assert html.count('title="RisePoseidonHunterB-NQ active"') == 3
    assert "02" in html
    assert "05" in html


def test_recent_trading_days_skip_weekends_and_end_on_today() -> None:
    days = _recent_trading_days(5, today=datetime(2026, 4, 14, tzinfo=TZ_DENVER).date())

    assert days == [
        "2026-04-08",
        "2026-04-09",
        "2026-04-10",
        "2026-04-13",
        "2026-04-14",
    ]


def test_weekly_snapshot_lists_fall_back_to_daily_analysis() -> None:
    pkg = AnalysisPackage(
        run_id="RisePoseidonHunterB-NQ",
        daily=pd.DataFrame(
            {
                "date": pd.to_datetime(["2026-04-13", "2026-04-14"]).tz_localize(TZ_DENVER),
                "net_profit": [-220.0, 1120.0],
                "avg_mae": [445.0, 390.0],
                "avg_mfe": [170.0, 1120.0],
                "trade_count": [1, 1],
            }
        ),
    )

    week_days = [
        datetime(2026, 4, 12, tzinfo=TZ_DENVER).date(),
        datetime(2026, 4, 13, tzinfo=TZ_DENVER).date(),
        datetime(2026, 4, 14, tzinfo=TZ_DENVER).date(),
        datetime(2026, 4, 15, tzinfo=TZ_DENVER).date(),
        datetime(2026, 4, 16, tzinfo=TZ_DENVER).date(),
        datetime(2026, 4, 17, tzinfo=TZ_DENVER).date(),
    ]
    prev_week_days = [
        datetime(2026, 4, 5, tzinfo=TZ_DENVER).date(),
        datetime(2026, 4, 6, tzinfo=TZ_DENVER).date(),
        datetime(2026, 4, 7, tzinfo=TZ_DENVER).date(),
        datetime(2026, 4, 8, tzinfo=TZ_DENVER).date(),
        datetime(2026, 4, 9, tzinfo=TZ_DENVER).date(),
        datetime(2026, 4, 10, tzinfo=TZ_DENVER).date(),
    ]

    snaps_week, snaps_prev = _snapshot_lists_for_pkg(
        pkg,
        week_days=week_days,
        prev_week_days=prev_week_days,
        start_balance=50000.0,
        trailing_dd=2500.0,
        baseline_mode="fresh_week",
    )

    assert snaps_prev == [None, None, None, None, None, None]
    assert snaps_week[0] is None
    assert snaps_week[1] is not None
    assert snaps_week[1].equity_close == 49780.0
    assert snaps_week[2] is not None
    assert snaps_week[2].equity_close == 50900.0


def test_weekly_dashboard_uses_rolling_window_when_requested_end_is_future() -> None:
    pkg = AnalysisPackage(
        run_id="RisePoseidonHunterB-NQ",
        daily=pd.DataFrame(
            {
                "date": pd.to_datetime(["2026-04-10", "2026-04-13", "2026-04-14"]).tz_localize(TZ_DENVER),
                "net_profit": [1120.0, -220.0, 0.0],
                "trade_count": [1, 1, 0],
            }
        ),
    )

    days, prev_days, labels, descriptor = _resolve_dashboard_window(
        {"week_ending": "2026-04-17", "rolling_days_count": 6, "rolling_trading_days_only": True},
        {"RisePoseidonHunterB-NQ": pkg},
    )

    today = datetime.now(TZ_DENVER).date()
    expected_days = []
    cursor = today
    while len(expected_days) < 6:
        if cursor.weekday() < 5:
            expected_days.append(cursor)
        cursor = cursor - timedelta(days=1)
    expected_days.reverse()

    assert [d.isoformat() for d in days] == [d.isoformat() for d in expected_days]
    assert labels == [d.strftime("%a")[:3] for d in expected_days]
    assert descriptor == f"Rolling 6-day window ending {today.isoformat()}"
    assert prev_days[-1].isoformat() == (expected_days[0] - timedelta(days=1)).isoformat()
