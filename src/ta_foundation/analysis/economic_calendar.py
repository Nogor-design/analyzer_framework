from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pandas as pd

NY_TZ = "America/New_York"
DENVER_TZ = "America/Denver"

# Blended weight used in event_risk_score calculation
IMPACT_WEIGHTS: Dict[str, float] = {
    "high": 1.0,
    "medium": 0.4,
    "low": 0.1,
    "holiday": 0.15,
    "non-economic": 0.05,
}

_IMPACT_ORDER: Dict[str, int] = {
    "high": 3,
    "medium": 2,
    "low": 1,
    "holiday": 1,
    "non-economic": 0,
}


@dataclass
class EconomicEvent:
    """A single scheduled economic event."""
    dt: pd.Timestamp        # tz-aware
    name: str
    currency: str           # "USD", "EUR", etc.
    impact: str             # "high", "medium", "low", "holiday", "non-economic"
    actual: Optional[str] = None
    forecast: Optional[str] = None
    previous: Optional[str] = None
    source: str = "unknown"

    def as_dict(self) -> Dict[str, Any]:
        return {
            "dt": self.dt.isoformat(),
            "name": self.name,
            "currency": self.currency,
            "impact": self.impact,
            "actual": self.actual,
            "forecast": self.forecast,
            "previous": self.previous,
            "source": self.source,
        }


@dataclass
class EconomicCalendar:
    """
    Collection of scheduled economic events with query and feature helpers.

    Load from CSV (ForexFactory-style export) or from a list of dicts.
    Feed the output of compute_event_proximity_features() into
    RegimeFeatureVector.feature_values for next-day prediction context.

    ForexFactory CSV column format (case-insensitive):
        Date, Time, Currency, Impact, Event[, Actual, Forecast, Previous]
    Date examples:  "Apr 20 2026", "2026-04-20", "20-Apr-26"
    Time examples:  "8:30am", "2:00pm", "Tentative", "All Day"
    Impact values:  "High", "Medium", "Low", "Non-Economic", "Holiday"
    """
    events: List[EconomicEvent] = field(default_factory=list)

    def add_event(self, event: EconomicEvent) -> None:
        self.events.append(event)

    def events_in_window(
        self,
        start: pd.Timestamp,
        end: pd.Timestamp,
        currencies: Optional[List[str]] = None,
        min_impact: str = "low",
    ) -> List[EconomicEvent]:
        """Return events in [start, end], sorted chronologically."""
        min_order = _IMPACT_ORDER.get(min_impact.lower(), 0)
        upper_currencies = {c.upper() for c in currencies} if currencies else None
        result = []
        for ev in self.events:
            if ev.dt < start or ev.dt > end:
                continue
            if upper_currencies and ev.currency.upper() not in upper_currencies:
                continue
            if _IMPACT_ORDER.get(ev.impact.lower(), 0) < min_order:
                continue
            result.append(ev)
        return sorted(result, key=lambda e: e.dt)

    def next_event(
        self,
        asof: pd.Timestamp,
        currencies: Optional[List[str]] = None,
        min_impact: str = "low",
    ) -> Optional[EconomicEvent]:
        min_order = _IMPACT_ORDER.get(min_impact.lower(), 0)
        upper_currencies = {c.upper() for c in currencies} if currencies else None
        candidates = [
            ev for ev in self.events
            if ev.dt > asof
            and (upper_currencies is None or ev.currency.upper() in upper_currencies)
            and _IMPACT_ORDER.get(ev.impact.lower(), 0) >= min_order
        ]
        return min(candidates, key=lambda e: e.dt) if candidates else None

    def prev_event(
        self,
        asof: pd.Timestamp,
        currencies: Optional[List[str]] = None,
        min_impact: str = "low",
    ) -> Optional[EconomicEvent]:
        min_order = _IMPACT_ORDER.get(min_impact.lower(), 0)
        upper_currencies = {c.upper() for c in currencies} if currencies else None
        candidates = [
            ev for ev in self.events
            if ev.dt <= asof
            and (upper_currencies is None or ev.currency.upper() in upper_currencies)
            and _IMPACT_ORDER.get(ev.impact.lower(), 0) >= min_order
        ]
        return max(candidates, key=lambda e: e.dt) if candidates else None

    @classmethod
    def from_csv(
        cls,
        path: str,
        event_tz: str = NY_TZ,
        source_label: str = "csv",
    ) -> "EconomicCalendar":
        """
        Load from a ForexFactory-style CSV export.

        The Date column may be blank for events sharing the same date as the
        preceding row — it is forward-filled automatically.
        """
        df = pd.read_csv(path)
        df.columns = [c.strip().lower() for c in df.columns]
        return cls._parse_df(df, event_tz, source_label)

    @classmethod
    def from_list(
        cls,
        events: List[Dict[str, Any]],
        event_tz: str = NY_TZ,
        source_label: str = "user",
    ) -> "EconomicCalendar":
        """
        Create from a list of dicts with keys:
          dt (ISO string or pd.Timestamp), name, currency, impact,
          [actual, forecast, previous]
        """
        cal = cls()
        for item in events:
            raw_dt = item.get("dt") or item.get("datetime") or item.get("timestamp")
            if raw_dt is None:
                continue
            ts = pd.Timestamp(raw_dt)
            if ts.tzinfo is None:
                ts = ts.tz_localize(event_tz)
            cal.add_event(EconomicEvent(
                dt=ts,
                name=str(item.get("name") or item.get("event") or ""),
                currency=str(item.get("currency") or "USD").upper(),
                impact=str(item.get("impact") or "medium").lower(),
                actual=_clean_val(item.get("actual")),
                forecast=_clean_val(item.get("forecast")),
                previous=_clean_val(item.get("previous")),
                source=source_label,
            ))
        return cal

    @classmethod
    def _parse_df(cls, df: pd.DataFrame, event_tz: str, source_label: str) -> "EconomicCalendar":
        cal = cls()
        if "date" not in df.columns:
            return cal

        # Forward-fill date column (ForexFactory omits date on same-day rows)
        df = df.copy()
        df["date"] = df["date"].astype(str).replace({"": pd.NA, "nan": pd.NA}).ffill()

        current_date: Optional[pd.Timestamp] = None
        for _, row in df.iterrows():
            date_str = str(row.get("date") or "").strip()
            if date_str and date_str.lower() not in ("nan", ""):
                parsed = _parse_date(date_str)
                if parsed is not None:
                    current_date = parsed

            if current_date is None:
                continue

            time_str = str(row.get("time") or "").strip()
            ts = current_date + _parse_time(time_str)
            if ts.tzinfo is None:
                ts = ts.tz_localize(event_tz)

            name = str(row.get("event") or row.get("name") or "").strip()
            currency = str(row.get("currency") or "USD").strip().upper()
            impact = str(row.get("impact") or "low").strip().lower()

            cal.add_event(EconomicEvent(
                dt=ts,
                name=name,
                currency=currency,
                impact=impact,
                actual=_clean_val(row.get("actual")),
                forecast=_clean_val(row.get("forecast")),
                previous=_clean_val(row.get("previous")),
                source=source_label,
            ))
        return cal


# ---------------------------------------------------------------------------
# Feature computation
# ---------------------------------------------------------------------------

def compute_event_proximity_features(
    calendar: EconomicCalendar,
    asof: pd.Timestamp,
    lookahead_hours: float = 24.0,
    lookback_hours: float = 24.0,
    currencies: Optional[List[str]] = None,
    min_impact: str = "low",
) -> Dict[str, Any]:
    """
    Compute event-proximity features for injection into RegimeFeatureVector.

    Returns a flat dict:
      hours_to_next_event          — hours until next event (inf if none)
      hours_to_next_high_impact    — hours until next high-impact event (inf if none)
      next_event_name              — name of next upcoming event
      next_event_impact            — impact tier of next event
      hours_since_last_event       — hours since last past event (inf if none)
      last_event_impact            — impact tier of last past event
      high_impact_count_next_24h   — high-impact events in lookahead window
      medium_impact_count_next_24h — medium-impact events in lookahead window
      event_risk_score             — blended 0–1 score (higher = more near-term risk)
      upcoming_events              — list of raw event dicts in lookahead window
    """
    if asof.tzinfo is None:
        asof = asof.tz_localize(NY_TZ)

    window_end = asof + pd.Timedelta(hours=lookahead_hours)
    window_start = asof - pd.Timedelta(hours=lookback_hours)

    upcoming = calendar.events_in_window(asof, window_end, currencies=currencies, min_impact=min_impact)
    past = calendar.events_in_window(window_start, asof, currencies=currencies, min_impact=min_impact)

    next_ev = upcoming[0] if upcoming else None
    hours_to_next = ((next_ev.dt - asof).total_seconds() / 3600.0) if next_ev else float("inf")

    next_high = next((e for e in upcoming if e.impact == "high"), None)
    hours_to_next_high = ((next_high.dt - asof).total_seconds() / 3600.0) if next_high else float("inf")

    last_ev = past[-1] if past else None
    hours_since_last = ((asof - last_ev.dt).total_seconds() / 3600.0) if last_ev else float("inf")

    high_count = sum(1 for e in upcoming if e.impact == "high")
    medium_count = sum(1 for e in upcoming if e.impact == "medium")

    # Risk score: additive over upcoming events, decayed by time distance, capped at 1.0.
    # Events happening now get full weight; events at the edge of the window get near-zero weight.
    risk_score = 0.0
    for ev in upcoming:
        hrs = max(0.0, (ev.dt - asof).total_seconds() / 3600.0)
        time_factor = (1.0 - hrs / lookahead_hours) ** 0.5
        weight = IMPACT_WEIGHTS.get(ev.impact.lower(), 0.0)
        risk_score = min(1.0, risk_score + weight * time_factor)

    return {
        "hours_to_next_event": hours_to_next,
        "hours_to_next_high_impact": hours_to_next_high,
        "next_event_name": next_ev.name if next_ev else "none",
        "next_event_impact": next_ev.impact if next_ev else "none",
        "hours_since_last_event": hours_since_last,
        "last_event_impact": last_ev.impact if last_ev else "none",
        "high_impact_count_next_24h": high_count,
        "medium_impact_count_next_24h": medium_count,
        "event_risk_score": float(risk_score),
        "upcoming_events": [e.as_dict() for e in upcoming],
    }


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _parse_date(s: str) -> Optional[pd.Timestamp]:
    """Try multiple date formats common in ForexFactory exports."""
    for fmt in (
        "%b %d %Y", "%b %d, %Y",
        "%Y-%m-%d",
        "%d-%b-%Y", "%d-%b-%y",
        "%m/%d/%Y",
    ):
        try:
            return pd.Timestamp(pd.to_datetime(s.strip(), format=fmt))
        except Exception:
            continue
    try:
        return pd.Timestamp(pd.to_datetime(s.strip(), dayfirst=False))
    except Exception:
        return None


def _parse_time(s: str) -> pd.Timedelta:
    """Parse a time string like '8:30am' or '2:00pm' into a Timedelta offset.

    Non-time strings ('Tentative', 'All Day', blank) map to 00:00.
    """
    s = (s or "").strip().lower()
    if not s or s in ("tentative", "all day", "day 1", "day 2", "day 3", "nan", ""):
        return pd.Timedelta(0)
    for fmt in ("%I:%M%p", "%I%p", "%H:%M"):
        try:
            t = pd.to_datetime(s, format=fmt).time()
            return pd.Timedelta(hours=t.hour, minutes=t.minute)
        except Exception:
            continue
    return pd.Timedelta(0)


def _clean_val(v: Any) -> Optional[str]:
    if v is None:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    s = str(v).strip()
    return s if s and s.lower() != "nan" else None
