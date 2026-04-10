from __future__ import annotations

"""
Session Classifier
==================
Deterministic mapping from a tz-aware timestamp to a named trading session.

Session boundaries are defined in America/New_York time.

Sessions
--------
  asia              18:00 – 00:00  NY (pre-midnight)
  london            00:00 – 03:00  NY
  london_ny_overlap 03:00 – 06:30  NY
  ny_pre            06:30 – 09:30  NY
  ny_open           09:30 – 10:30  NY
  mid_ny            10:30 – 14:00  NY
  power_hour        14:00 – 16:00  NY
  after_hours       16:00 – 18:00  NY  (gap not assigned above)

The mapping is deterministic and stateless — no external data required.
"""

from typing import Optional

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Boundary constants (minutes from midnight, America/New_York)
# ---------------------------------------------------------------------------

_LONDON_START           = 0
_LONDON_END             = 3 * 60           # 180
_LDN_NY_END             = 6 * 60 + 30      # 390
_NY_PRE_END             = 9 * 60 + 30      # 570
_NY_OPEN_END            = 10 * 60 + 30     # 630
_MID_NY_END             = 14 * 60          # 840
_POWER_HOUR_END         = 16 * 60          # 960
_ASIA_START             = 18 * 60          # 1080

SESSION_ORDER = [
    "london",
    "london_ny_overlap",
    "ny_pre",
    "ny_open",
    "mid_ny",
    "power_hour",
    "after_hours",
    "asia",
]


# ---------------------------------------------------------------------------
# Core logic (operates on minutes-from-midnight integer)
# ---------------------------------------------------------------------------

def _minutes_to_session(m: int) -> str:
    """
    Map total_minutes (0 – 1439) in America/New_York to a session label.
    This is the single source of truth for all session classification.
    """
    if m >= _ASIA_START:                   return "asia"              # 18:00+
    if m < _LONDON_END:                    return "london"            # 00:00–03:00
    if m < _LDN_NY_END:                    return "london_ny_overlap" # 03:00–06:30
    if m < _NY_PRE_END:                    return "ny_pre"            # 06:30–09:30
    if m < _NY_OPEN_END:                   return "ny_open"           # 09:30–10:30
    if m < _MID_NY_END:                    return "mid_ny"            # 10:30–14:00
    if m < _POWER_HOUR_END:                return "power_hour"        # 14:00–16:00
    return "after_hours"                                               # 16:00–18:00


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def classify_session(dt: pd.Timestamp) -> str:
    """
    Return the session name for a single tz-aware timestamp.

    Parameters
    ----------
    dt : tz-aware Timestamp (any timezone; will be converted to America/New_York)

    Returns
    -------
    One of: "asia", "london", "london_ny_overlap", "ny_pre",
            "ny_open", "mid_ny", "power_hour", "after_hours"
    """
    if dt is None or not isinstance(dt, pd.Timestamp):
        return "unknown"
    try:
        ny = dt.tz_convert("America/New_York") if dt.tzinfo else dt.tz_localize("America/New_York")
        return _minutes_to_session(ny.hour * 60 + ny.minute)
    except Exception:
        return "unknown"


def classify_session_vectorized(dt_series: pd.Series) -> pd.Series:
    """
    Vectorized session classification for a pandas Series of Timestamps.

    Parameters
    ----------
    dt_series : Series of tz-aware Timestamps

    Returns
    -------
    String Series aligned to dt_series.index
    """
    if dt_series is None or len(dt_series) == 0:
        return pd.Series(dtype=str)

    s = pd.to_datetime(dt_series)
    try:
        if s.dt.tz is not None:
            ny = s.dt.tz_convert("America/New_York")
        else:
            ny = s.dt.tz_localize("UTC").dt.tz_convert("America/New_York")
    except Exception:
        return pd.Series(["unknown"] * len(dt_series), index=dt_series.index, dtype=str)

    total_min = (ny.dt.hour * 60 + ny.dt.minute).values

    result = np.where(total_min >= _ASIA_START,   "asia",
             np.where(total_min < _LONDON_END,     "london",
             np.where(total_min < _LDN_NY_END,     "london_ny_overlap",
             np.where(total_min < _NY_PRE_END,      "ny_pre",
             np.where(total_min < _NY_OPEN_END,     "ny_open",
             np.where(total_min < _MID_NY_END,      "mid_ny",
             np.where(total_min < _POWER_HOUR_END,  "power_hour",
                                                     "after_hours")))))))

    return pd.Series(result, index=dt_series.index, dtype=str)


def add_session_bucket_to_events(events: pd.DataFrame) -> pd.DataFrame:
    """
    Add a 'session_bucket' column to an events DataFrame in-place.

    Uses the 'dt' column (must be tz-aware).  Returns the modified DataFrame.
    """
    if events is None or events.empty or "dt" not in events.columns:
        return events
    events = events.copy()
    events["session_bucket"] = classify_session_vectorized(events["dt"])
    return events
