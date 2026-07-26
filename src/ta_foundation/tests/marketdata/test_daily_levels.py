from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from ta_foundation.marketdata.daily_levels import (
    DailyLevels,
    compute_daily_levels,
    compute_daily_levels_from_store,
)
from ta_foundation.marketdata.store import MarketDataStore

TZ = "America/Denver"
RNG = np.random.default_rng(42)


def _make_bars(n_days: int = 5, start: str = "2026-03-02 08:00") -> pd.DataFrame:
    """Generate deterministic minute bars for n_days × 6-hour sessions (8am–2pm Denver)."""
    rows = []
    base_dt = pd.Timestamp(start, tz=TZ)
    base_price = 19000.0
    for d in range(n_days):
        day_open = base_price + d * 50.0
        for m in range(360):  # 8:00–13:59
            dt = base_dt + pd.Timedelta(days=d, minutes=m)
            price = day_open + m * 0.1
            rows.append({
                "dt": dt,
                "open": round(price, 2),
                "high": round(price + 0.5, 2),
                "low": round(price - 0.5, 2),
                "close": round(price, 2),
                "volume": 100.0,
            })
    return pd.DataFrame(rows)


def _make_multimonth_bars() -> pd.DataFrame:
    """Bars spanning February and March 2026."""
    rows = []
    base_price = 19000.0
    for month, n_days in [(2, 20), (3, 5)]:
        for d in range(n_days):
            base_dt = pd.Timestamp(f"2026-{month:02d}-{d + 1:02d} 08:00", tz=TZ)
            for m in range(60):
                price = base_price + d * 10 + m * 0.1
                rows.append({
                    "dt": base_dt + pd.Timedelta(minutes=m),
                    "open": round(price, 2),
                    "high": round(price + 0.5, 2),
                    "low": round(price - 0.5, 2),
                    "close": round(price, 2),
                    "volume": 100.0,
                })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Prior day extraction
# ---------------------------------------------------------------------------


def test_prior_day_fields_populated():
    bars = _make_bars(3)
    asof = pd.Timestamp("2026-03-04 17:00", tz=TZ)
    result = compute_daily_levels(bars, asof, instrument="NQ", contract="03-26")

    assert isinstance(result, DailyLevels)
    assert result.pd_high is not None
    assert result.pd_low is not None
    assert result.pd_close is not None
    assert result.pd_open is not None
    assert result.pd_high >= result.pd_low


def test_prior_day_high_gte_close():
    bars = _make_bars(5)
    asof = pd.Timestamp("2026-03-06 17:00", tz=TZ)
    result = compute_daily_levels(bars, asof)
    assert result.pd_high >= result.pd_close >= result.pd_low


# ---------------------------------------------------------------------------
# Pivot point formulas
# ---------------------------------------------------------------------------


def test_classic_pivot_formula():
    bars = _make_bars(2)
    asof = pd.Timestamp("2026-03-03 17:00", tz=TZ)
    r = compute_daily_levels(bars, asof)

    expected_pp = (r.pd_high + r.pd_low + r.pd_close) / 3.0
    assert abs(r.pivot - expected_pp) < 1e-6
    assert abs(r.r1 - (2.0 * r.pivot - r.pd_low)) < 1e-6
    assert abs(r.s1 - (2.0 * r.pivot - r.pd_high)) < 1e-6
    assert abs(r.r2 - (r.pivot + (r.pd_high - r.pd_low))) < 1e-6
    assert abs(r.s2 - (r.pivot - (r.pd_high - r.pd_low))) < 1e-6


def test_camarilla_pivot_formula():
    bars = _make_bars(2)
    asof = pd.Timestamp("2026-03-03 17:00", tz=TZ)
    r = compute_daily_levels(bars, asof)

    rng = r.pd_high - r.pd_low
    assert abs(r.cam_r4 - (r.pd_close + rng * 1.1 / 2.0)) < 1e-6
    assert abs(r.cam_s4 - (r.pd_close - rng * 1.1 / 2.0)) < 1e-6
    assert abs(r.cam_r3 - (r.pd_close + rng * 1.1 / 4.0)) < 1e-6
    assert abs(r.cam_s3 - (r.pd_close - rng * 1.1 / 4.0)) < 1e-6


def test_pivot_levels_ordered():
    """R3 > R2 > R1 > PP > S1 > S2 > S3 for a normal day."""
    bars = _make_bars(5)
    asof = pd.Timestamp("2026-03-06 17:00", tz=TZ)
    r = compute_daily_levels(bars, asof)
    assert r.r3 > r.r2 > r.r1 > r.pivot > r.s1 > r.s2 > r.s3


# ---------------------------------------------------------------------------
# Prior week
# ---------------------------------------------------------------------------


def test_prior_week_fields_populated():
    # 15 days starting 2026-03-02 spans at least 3 ISO weeks
    bars = _make_bars(15)
    asof = pd.Timestamp("2026-03-16 17:00", tz=TZ)
    r = compute_daily_levels(bars, asof)

    assert r.pw_high is not None
    assert r.pw_low is not None
    assert r.pw_open is not None
    assert r.pw_close is not None
    assert r.current_week_open is not None
    assert "insufficient_data_for_prior_week" not in r.warnings


def test_prior_week_warning_when_only_one_week():
    bars = _make_bars(3)  # all within same week
    asof = pd.Timestamp("2026-03-04 17:00", tz=TZ)
    r = compute_daily_levels(bars, asof)

    assert "insufficient_data_for_prior_week" in r.warnings
    assert r.pw_high is None


# ---------------------------------------------------------------------------
# Prior month
# ---------------------------------------------------------------------------


def test_prior_month_fields_populated():
    bars = _make_multimonth_bars()
    asof = pd.Timestamp("2026-03-05 17:00", tz=TZ)
    r = compute_daily_levels(bars, asof)

    assert r.pm_high is not None
    assert r.pm_low is not None
    assert r.current_month_open is not None
    assert "insufficient_data_for_prior_month" not in r.warnings


def test_prior_month_warning_when_single_month():
    bars = _make_bars(5)
    asof = pd.Timestamp("2026-03-06 17:00", tz=TZ)
    r = compute_daily_levels(bars, asof)

    assert "insufficient_data_for_prior_month" in r.warnings
    assert r.pm_high is None


# ---------------------------------------------------------------------------
# RTH filtering
# ---------------------------------------------------------------------------


def test_rth_only_restricts_to_session_hours():
    bars = _make_bars(3)
    asof = pd.Timestamp("2026-03-04 17:00", tz=TZ)

    full = compute_daily_levels(bars, asof)
    rth = compute_daily_levels(bars, asof, rth_only=True, rth_start_hour=10, rth_end_hour=12)

    # RTH range is narrower (only 2h of bars)
    assert rth.pd_high <= full.pd_high
    assert rth.pd_low >= full.pd_low


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_empty_bars_returns_warning():
    empty = pd.DataFrame(columns=["dt", "open", "high", "low", "close", "volume"])
    asof = pd.Timestamp("2026-03-04 17:00", tz=TZ)
    r = compute_daily_levels(empty, asof)

    assert r.warnings
    assert r.pd_high is None
    assert r.pivot is None


def test_asof_before_all_bars_returns_warning():
    bars = _make_bars(3)
    asof = pd.Timestamp("2026-01-01 17:00", tz=TZ)
    r = compute_daily_levels(bars, asof)

    assert "no_bars_before_asof" in r.warnings
    assert r.pd_high is None


# ---------------------------------------------------------------------------
# Output contracts
# ---------------------------------------------------------------------------


def test_flat_feature_values_keys():
    bars = _make_bars(5)
    asof = pd.Timestamp("2026-03-06 17:00", tz=TZ)
    r = compute_daily_levels(bars, asof)
    fv = r.flat_feature_values()

    for key in ("level_pd_high", "level_pd_low", "level_pivot", "level_cam_r4", "level_r1"):
        assert key in fv


def test_as_dict_is_json_serializable():
    bars = _make_bars(15)
    bars_mm = _make_multimonth_bars()
    for b, asof_str in [
        (bars, "2026-03-16 17:00"),
        (bars_mm, "2026-03-05 17:00"),
    ]:
        asof = pd.Timestamp(asof_str, tz=TZ)
        r = compute_daily_levels(b, asof)
        json.dumps(r.as_dict())  # must not raise


def test_sessions_available_count():
    bars = _make_bars(5)
    asof = pd.Timestamp("2026-03-06 17:00", tz=TZ)
    r = compute_daily_levels(bars, asof)

    assert r.sessions_available == 5


# ---------------------------------------------------------------------------
# MarketDataStore integration
# ---------------------------------------------------------------------------


def test_from_store_basic():
    market = MarketDataStore()
    bars = _make_bars(5)
    market.put_minute_bars("NQ", "03-26", bars)

    asof = pd.Timestamp("2026-03-06 17:00", tz=TZ)
    r = compute_daily_levels_from_store(market, "NQ", "03-26", asof)

    assert r.instrument == "NQ"
    assert r.contract == "03-26"
    assert r.pd_high is not None


def test_from_store_missing_instrument_returns_warning():
    market = MarketDataStore()
    asof = pd.Timestamp("2026-03-06 17:00", tz=TZ)
    r = compute_daily_levels_from_store(market, "NQ", "03-26", asof)

    assert r.warnings
    assert r.pd_high is None
