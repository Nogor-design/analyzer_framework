from __future__ import annotations

import io

import pandas as pd
import pytest

from ta_foundation.analysis.economic_calendar import (
    EconomicCalendar,
    EconomicEvent,
    compute_event_proximity_features,
)

NY_TZ = "America/New_York"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_EVENTS_LIST = [
    {"dt": "2026-04-20 08:30", "name": "Core CPI m/m", "currency": "USD", "impact": "high",
     "actual": "0.2%", "forecast": "0.3%", "previous": "0.2%"},
    {"dt": "2026-04-20 14:00", "name": "FOMC Statement", "currency": "USD", "impact": "high"},
    {"dt": "2026-04-21 08:30", "name": "Jobless Claims", "currency": "USD", "impact": "medium"},
    {"dt": "2026-04-21 10:00", "name": "ECB Rate Decision", "currency": "EUR", "impact": "high"},
    {"dt": "2026-04-22 08:30", "name": "Durable Goods", "currency": "USD", "impact": "low"},
]

_CSV_CONTENT = """\
Date,Time,Currency,Impact,Event,Actual,Forecast,Previous
Apr 20 2026,8:30am,USD,High,Core CPI m/m,0.2%,0.3%,0.2%
Apr 20 2026,2:00pm,USD,High,FOMC Statement,,,
Apr 21 2026,8:30am,USD,Medium,Jobless Claims,220K,215K,218K
Apr 21 2026,10:00am,EUR,High,ECB Rate Decision,,,
Apr 22 2026,8:30am,USD,Low,Durable Goods,,,
"""


def _cal() -> EconomicCalendar:
    return EconomicCalendar.from_list(_EVENTS_LIST, event_tz=NY_TZ)


# ---------------------------------------------------------------------------
# from_list
# ---------------------------------------------------------------------------


def test_from_list_event_count():
    cal = _cal()
    assert len(cal.events) == len(_EVENTS_LIST)


def test_from_list_fields():
    cal = _cal()
    ev = cal.events[0]
    assert ev.name == "Core CPI m/m"
    assert ev.impact == "high"
    assert ev.currency == "USD"
    assert ev.actual == "0.2%"
    assert ev.forecast == "0.3%"


def test_from_list_tz_localized():
    cal = _cal()
    for ev in cal.events:
        assert ev.dt.tzinfo is not None


# ---------------------------------------------------------------------------
# from_csv
# ---------------------------------------------------------------------------


def test_from_csv_event_count(tmp_path):
    p = tmp_path / "cal.csv"
    p.write_text(_CSV_CONTENT)
    cal = EconomicCalendar.from_csv(str(p))
    assert len(cal.events) == 5


def test_from_csv_impact_parsed(tmp_path):
    p = tmp_path / "cal.csv"
    p.write_text(_CSV_CONTENT)
    cal = EconomicCalendar.from_csv(str(p))
    assert cal.events[0].impact == "high"
    assert cal.events[2].impact == "medium"
    assert cal.events[4].impact == "low"


def test_from_csv_actual_parsed(tmp_path):
    p = tmp_path / "cal.csv"
    p.write_text(_CSV_CONTENT)
    cal = EconomicCalendar.from_csv(str(p))
    assert cal.events[0].actual == "0.2%"
    assert cal.events[1].actual is None  # blank actual


def test_from_csv_time_parsed(tmp_path):
    p = tmp_path / "cal.csv"
    p.write_text(_CSV_CONTENT)
    cal = EconomicCalendar.from_csv(str(p))
    assert cal.events[0].dt.hour == 8
    assert cal.events[0].dt.minute == 30
    assert cal.events[1].dt.hour == 14
    assert cal.events[1].dt.minute == 0


def test_from_csv_date_forward_filled(tmp_path):
    """Date column blank on second row is forward-filled from first."""
    csv = (
        "Date,Time,Currency,Impact,Event\n"
        "Apr 20 2026,8:30am,USD,High,CPI\n"
        ",2:00pm,USD,High,FOMC\n"
    )
    p = tmp_path / "fwd.csv"
    p.write_text(csv)
    cal = EconomicCalendar.from_csv(str(p))
    assert len(cal.events) == 2
    assert cal.events[1].dt.date() == cal.events[0].dt.date()


# ---------------------------------------------------------------------------
# events_in_window
# ---------------------------------------------------------------------------


def test_events_in_window_returns_correct_events():
    cal = _cal()
    start = pd.Timestamp("2026-04-20 00:00", tz=NY_TZ)
    end = pd.Timestamp("2026-04-20 23:59", tz=NY_TZ)
    evs = cal.events_in_window(start, end)
    assert len(evs) == 2
    assert all(ev.dt.date() == pd.Timestamp("2026-04-20", tz=NY_TZ).date() for ev in evs)


def test_events_in_window_currency_filter():
    cal = _cal()
    start = pd.Timestamp("2026-04-21 00:00", tz=NY_TZ)
    end = pd.Timestamp("2026-04-21 23:59", tz=NY_TZ)
    usd_evs = cal.events_in_window(start, end, currencies=["USD"])
    assert all(e.currency == "USD" for e in usd_evs)
    assert len(usd_evs) == 1


def test_events_in_window_min_impact_filter():
    cal = _cal()
    start = pd.Timestamp("2026-04-22 00:00", tz=NY_TZ)
    end = pd.Timestamp("2026-04-22 23:59", tz=NY_TZ)
    high_only = cal.events_in_window(start, end, min_impact="high")
    assert len(high_only) == 0
    all_ev = cal.events_in_window(start, end, min_impact="low")
    assert len(all_ev) == 1


def test_events_in_window_sorted_chronologically():
    cal = _cal()
    start = pd.Timestamp("2026-04-20 00:00", tz=NY_TZ)
    end = pd.Timestamp("2026-04-22 23:59", tz=NY_TZ)
    evs = cal.events_in_window(start, end)
    dts = [e.dt for e in evs]
    assert dts == sorted(dts)


# ---------------------------------------------------------------------------
# next_event / prev_event
# ---------------------------------------------------------------------------


def test_next_event_returns_nearest_upcoming():
    cal = _cal()
    asof = pd.Timestamp("2026-04-20 10:00", tz=NY_TZ)
    ev = cal.next_event(asof)
    assert ev is not None
    assert ev.name == "FOMC Statement"


def test_prev_event_returns_most_recent_past():
    cal = _cal()
    asof = pd.Timestamp("2026-04-20 10:00", tz=NY_TZ)
    ev = cal.prev_event(asof)
    assert ev is not None
    assert ev.name == "Core CPI m/m"


def test_next_event_returns_none_when_no_upcoming():
    cal = _cal()
    asof = pd.Timestamp("2026-05-01 00:00", tz=NY_TZ)
    assert cal.next_event(asof) is None


# ---------------------------------------------------------------------------
# compute_event_proximity_features — empty calendar
# ---------------------------------------------------------------------------


def test_proximity_empty_calendar():
    cal = EconomicCalendar()
    asof = pd.Timestamp("2026-04-19 17:00", tz=NY_TZ)
    f = compute_event_proximity_features(cal, asof)

    assert f["hours_to_next_event"] == float("inf")
    assert f["hours_to_next_high_impact"] == float("inf")
    assert f["event_risk_score"] == 0.0
    assert f["high_impact_count_next_24h"] == 0
    assert f["next_event_name"] == "none"


# ---------------------------------------------------------------------------
# compute_event_proximity_features — upcoming events
# ---------------------------------------------------------------------------


def test_proximity_hours_to_next():
    cal = EconomicCalendar.from_list([
        {"dt": "2026-04-20 08:30", "name": "CPI", "currency": "USD", "impact": "high"},
    ], event_tz=NY_TZ)
    asof = pd.Timestamp("2026-04-19 20:30", tz=NY_TZ)  # 12h before CPI
    f = compute_event_proximity_features(cal, asof)

    assert abs(f["hours_to_next_event"] - 12.0) < 0.01
    assert abs(f["hours_to_next_high_impact"] - 12.0) < 0.01
    assert f["next_event_name"] == "CPI"
    assert f["high_impact_count_next_24h"] == 1


def test_proximity_risk_score_higher_for_imminent():
    asof = pd.Timestamp("2026-04-19 17:00", tz=NY_TZ)
    cal_close = EconomicCalendar.from_list([
        {"dt": "2026-04-19 18:00", "name": "Fed Speak", "currency": "USD", "impact": "high"},
    ], event_tz=NY_TZ)
    cal_far = EconomicCalendar.from_list([
        {"dt": "2026-04-20 16:00", "name": "Fed Speak", "currency": "USD", "impact": "high"},
    ], event_tz=NY_TZ)
    score_close = compute_event_proximity_features(cal_close, asof)["event_risk_score"]
    score_far = compute_event_proximity_features(cal_far, asof)["event_risk_score"]
    assert score_close > score_far


def test_proximity_risk_score_bounded_0_1():
    asof = pd.Timestamp("2026-04-20 08:00", tz=NY_TZ)
    cal = EconomicCalendar.from_list([
        {"dt": "2026-04-20 08:30", "name": "NFP", "currency": "USD", "impact": "high"},
        {"dt": "2026-04-20 09:00", "name": "CPI", "currency": "USD", "impact": "high"},
        {"dt": "2026-04-20 10:00", "name": "FOMC", "currency": "USD", "impact": "high"},
        {"dt": "2026-04-20 14:00", "name": "PMI", "currency": "USD", "impact": "high"},
    ], event_tz=NY_TZ)
    f = compute_event_proximity_features(cal, asof)
    assert 0.0 <= f["event_risk_score"] <= 1.0


def test_proximity_currency_filter():
    asof = pd.Timestamp("2026-04-20 07:00", tz=NY_TZ)
    cal = EconomicCalendar.from_list([
        {"dt": "2026-04-20 08:30", "name": "US CPI", "currency": "USD", "impact": "high"},
        {"dt": "2026-04-20 09:30", "name": "ECB Rate", "currency": "EUR", "impact": "high"},
    ], event_tz=NY_TZ)
    f = compute_event_proximity_features(cal, asof, currencies=["USD"])
    assert f["next_event_name"] == "US CPI"
    assert f["high_impact_count_next_24h"] == 1


def test_proximity_upcoming_events_list():
    asof = pd.Timestamp("2026-04-19 17:00", tz=NY_TZ)
    cal = EconomicCalendar.from_list([
        {"dt": "2026-04-20 08:30", "name": "CPI", "currency": "USD", "impact": "high"},
    ], event_tz=NY_TZ)
    f = compute_event_proximity_features(cal, asof)
    assert isinstance(f["upcoming_events"], list)
    assert len(f["upcoming_events"]) == 1
    assert f["upcoming_events"][0]["name"] == "CPI"


def test_proximity_naive_asof_is_localized():
    cal = EconomicCalendar()
    asof_naive = pd.Timestamp("2026-04-19 17:00")  # no tz
    f = compute_event_proximity_features(cal, asof_naive)  # must not raise
    assert f["event_risk_score"] == 0.0


# ---------------------------------------------------------------------------
# EconomicEvent.as_dict
# ---------------------------------------------------------------------------


def test_event_as_dict_keys():
    ev = EconomicEvent(
        dt=pd.Timestamp("2026-04-20 08:30", tz=NY_TZ),
        name="CPI",
        currency="USD",
        impact="high",
        actual="0.2%",
    )
    d = ev.as_dict()
    for key in ("dt", "name", "currency", "impact", "actual", "forecast", "previous", "source"):
        assert key in d
