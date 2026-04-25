from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pandas as pd

from ta_foundation.marketdata.store import MarketDataStore

DENVER_TZ = "America/Denver"


@dataclass
class DailyLevels:
    """
    Key price levels derived from prior day/week/month sessions.

    Call after session close (e.g. 17:00 Denver) so the most recent daily
    session is treated as "prior day" for next-session planning.

    Pivot points are derived from prior-day High/Low/Close.
    Camarilla R3/S3 are high-probability mean-reversion zones;
    R4/S4 are breakout entry levels.
    """
    asof: str
    instrument: str
    contract: str

    pd_open: Optional[float] = None
    pd_high: Optional[float] = None
    pd_low: Optional[float] = None
    pd_close: Optional[float] = None

    pw_open: Optional[float] = None
    pw_high: Optional[float] = None
    pw_low: Optional[float] = None
    pw_close: Optional[float] = None

    pm_open: Optional[float] = None
    pm_high: Optional[float] = None
    pm_low: Optional[float] = None
    pm_close: Optional[float] = None

    current_week_open: Optional[float] = None
    current_month_open: Optional[float] = None

    pivot: Optional[float] = None
    r1: Optional[float] = None
    s1: Optional[float] = None
    r2: Optional[float] = None
    s2: Optional[float] = None
    r3: Optional[float] = None
    s3: Optional[float] = None

    cam_r4: Optional[float] = None
    cam_s4: Optional[float] = None
    cam_r3: Optional[float] = None
    cam_s3: Optional[float] = None

    sessions_available: int = 0
    warnings: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "asof": self.asof,
            "instrument": self.instrument,
            "contract": self.contract,
            "prior_day": {
                "open": self.pd_open,
                "high": self.pd_high,
                "low": self.pd_low,
                "close": self.pd_close,
            },
            "prior_week": {
                "open": self.pw_open,
                "high": self.pw_high,
                "low": self.pw_low,
                "close": self.pw_close,
            },
            "prior_month": {
                "open": self.pm_open,
                "high": self.pm_high,
                "low": self.pm_low,
                "close": self.pm_close,
            },
            "current_week_open": self.current_week_open,
            "current_month_open": self.current_month_open,
            "pivots": {
                "pp": self.pivot,
                "r1": self.r1, "s1": self.s1,
                "r2": self.r2, "s2": self.s2,
                "r3": self.r3, "s3": self.s3,
            },
            "camarilla": {
                "r4": self.cam_r4, "s4": self.cam_s4,
                "r3": self.cam_r3, "s3": self.cam_s3,
            },
            "sessions_available": self.sessions_available,
            "warnings": list(self.warnings),
        }

    def flat_feature_values(self) -> Dict[str, Optional[float]]:
        """Flat dict for injection into RegimeFeatureVector.feature_values."""
        return {
            "level_pd_open": self.pd_open,
            "level_pd_high": self.pd_high,
            "level_pd_low": self.pd_low,
            "level_pd_close": self.pd_close,
            "level_pw_high": self.pw_high,
            "level_pw_low": self.pw_low,
            "level_pm_high": self.pm_high,
            "level_pm_low": self.pm_low,
            "level_pivot": self.pivot,
            "level_r1": self.r1,
            "level_s1": self.s1,
            "level_r2": self.r2,
            "level_s2": self.s2,
            "level_cam_r4": self.cam_r4,
            "level_cam_s4": self.cam_s4,
            "level_current_week_open": self.current_week_open,
            "level_current_month_open": self.current_month_open,
        }


def _to_denver(ts: pd.Timestamp) -> pd.Timestamp:
    ts = pd.Timestamp(ts)
    if ts.tzinfo is None:
        return ts.tz_localize(DENVER_TZ)
    return ts.tz_convert(DENVER_TZ)


def _session_daily(
    bars: pd.DataFrame,
    rth_only: bool,
    rth_start_hour: int,
    rth_end_hour: int,
) -> pd.DataFrame:
    """Aggregate minute bars into one row per calendar date (Denver tz)."""
    df = bars[["dt", "open", "high", "low", "close", "volume"]].copy()
    dt_col = pd.to_datetime(df["dt"], errors="coerce")
    if dt_col.dt.tz is None:
        dt_col = dt_col.dt.tz_localize(DENVER_TZ)
    else:
        dt_col = dt_col.dt.tz_convert(DENVER_TZ)
    df["dt"] = dt_col

    if rth_only:
        h = df["dt"].dt.hour
        df = df[(h >= rth_start_hour) & (h < rth_end_hour)]

    if df.empty:
        return pd.DataFrame(columns=["session_date", "open", "high", "low", "close", "volume"])

    df["session_date"] = df["dt"].dt.date

    out = (
        df.groupby("session_date", sort=True)
        .agg(
            open=("open", "first"),
            high=("high", "max"),
            low=("low", "min"),
            close=("close", "last"),
            volume=("volume", "sum"),
        )
        .reset_index()
    )
    return out


def _classic_pivots(high: float, low: float, close: float) -> Dict[str, float]:
    pp = (high + low + close) / 3.0
    return {
        "pp": pp,
        "r1": 2.0 * pp - low,
        "s1": 2.0 * pp - high,
        "r2": pp + (high - low),
        "s2": pp - (high - low),
        "r3": high + 2.0 * (pp - low),
        "s3": low - 2.0 * (high - pp),
    }


def _camarilla_pivots(high: float, low: float, close: float) -> Dict[str, float]:
    rng = high - low
    return {
        "r4": close + rng * 1.1 / 2.0,
        "s4": close - rng * 1.1 / 2.0,
        "r3": close + rng * 1.1 / 4.0,
        "s3": close - rng * 1.1 / 4.0,
    }


def compute_daily_levels(
    bars: pd.DataFrame,
    asof: pd.Timestamp,
    instrument: str = "",
    contract: str = "",
    rth_only: bool = False,
    rth_start_hour: int = 8,
    rth_end_hour: int = 14,
) -> DailyLevels:
    """
    Compute prior-day/week/month OHLC levels and pivot points from minute bars.

    Call after session close so the most recent daily session counts as
    "prior day". For CME equity-index futures the approximate RTH window
    in Denver time is rth_start_hour=8, rth_end_hour=14 (≈ 9:30am–4pm ET).

    Uses ISO weeks (Monday–Sunday) for weekly aggregation.
    Uses calendar months for monthly aggregation.
    """
    asof_den = _to_denver(asof)
    result = DailyLevels(
        asof=asof_den.isoformat(),
        instrument=instrument,
        contract=contract,
    )

    if bars is None or bars.empty:
        result.warnings.append("no_bars_provided")
        return result

    # Filter bars up to and including asof
    dt_col = pd.to_datetime(bars["dt"], errors="coerce")
    if dt_col.dt.tz is None:
        dt_col = dt_col.dt.tz_localize(DENVER_TZ)
    else:
        dt_col = dt_col.dt.tz_convert(DENVER_TZ)
    bars_filtered = bars[dt_col <= asof_den].copy()

    if bars_filtered.empty:
        result.warnings.append("no_bars_before_asof")
        return result

    daily = _session_daily(bars_filtered, rth_only, rth_start_hour, rth_end_hour)

    if daily.empty:
        result.warnings.append("daily_aggregation_empty")
        return result

    result.sessions_available = len(daily)

    # Prior day (most recent completed session)
    if len(daily) >= 1:
        row = daily.iloc[-1]
        result.pd_open = float(row["open"])
        result.pd_high = float(row["high"])
        result.pd_low = float(row["low"])
        result.pd_close = float(row["close"])

        p = _classic_pivots(result.pd_high, result.pd_low, result.pd_close)
        result.pivot = p["pp"]
        result.r1 = p["r1"]
        result.s1 = p["s1"]
        result.r2 = p["r2"]
        result.s2 = p["s2"]
        result.r3 = p["r3"]
        result.s3 = p["s3"]

        c = _camarilla_pivots(result.pd_high, result.pd_low, result.pd_close)
        result.cam_r4 = c["r4"]
        result.cam_s4 = c["s4"]
        result.cam_r3 = c["r3"]
        result.cam_s3 = c["s3"]
    else:
        result.warnings.append("no_prior_day_data")

    # Weekly aggregation via ISO week (year, week_number)
    daily["_iso"] = [
        (pd.Timestamp(d).isocalendar()[0], pd.Timestamp(d).isocalendar()[1])
        for d in daily["session_date"]
    ]
    weekly = (
        daily.groupby("_iso", sort=True)
        .agg(open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last"))
        .reset_index()
    )
    if len(weekly) >= 1:
        result.current_week_open = float(weekly.iloc[-1]["open"])
    if len(weekly) >= 2:
        pw = weekly.iloc[-2]
        result.pw_open = float(pw["open"])
        result.pw_high = float(pw["high"])
        result.pw_low = float(pw["low"])
        result.pw_close = float(pw["close"])
    else:
        result.warnings.append("insufficient_data_for_prior_week")

    # Monthly aggregation
    daily["_ym"] = [(pd.Timestamp(d).year, pd.Timestamp(d).month) for d in daily["session_date"]]
    monthly = (
        daily.groupby("_ym", sort=True)
        .agg(open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last"))
        .reset_index()
    )
    if len(monthly) >= 1:
        result.current_month_open = float(monthly.iloc[-1]["open"])
    if len(monthly) >= 2:
        pm = monthly.iloc[-2]
        result.pm_open = float(pm["open"])
        result.pm_high = float(pm["high"])
        result.pm_low = float(pm["low"])
        result.pm_close = float(pm["close"])
    else:
        result.warnings.append("insufficient_data_for_prior_month")

    return result


def compute_daily_levels_from_store(
    market: MarketDataStore,
    instrument: str,
    contract: str,
    asof: pd.Timestamp,
    lookback_days: int = 45,
    rth_only: bool = False,
    rth_start_hour: int = 8,
    rth_end_hour: int = 14,
) -> DailyLevels:
    """Convenience wrapper that fetches bars from MarketDataStore."""
    asof_den = _to_denver(asof)
    start = asof_den - pd.Timedelta(days=lookback_days)
    bars = market.get_bars(instrument, contract, timeframe="1m", start=start, end=asof_den)
    if bars is None or bars.empty:
        result = DailyLevels(
            asof=asof_den.isoformat(),
            instrument=instrument,
            contract=contract,
        )
        result.warnings.append(f"no_market_data:{instrument}/{contract}")
        return result
    return compute_daily_levels(
        bars,
        asof_den,
        instrument=instrument,
        contract=contract,
        rth_only=rth_only,
        rth_start_hour=rth_start_hour,
        rth_end_hour=rth_end_hour,
    )
