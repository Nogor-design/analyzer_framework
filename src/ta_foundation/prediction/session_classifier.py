"""
Session classifier for the horizon prediction system.

Sessions are defined in `America/New_York` clock-time so they survive DST
transitions automatically. Defaults match common futures conventions; users
can override per-instrument by passing a `SessionConfig`.

The classifier looks at timestamps only — it must never read outcomes,
otherwise the labels would leak future information into the conditioning set.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time as dtime
from typing import List, Tuple

import pandas as pd

NY_TZ = "America/New_York"


# ---------------------------------------------------------------------------
# Default session boundaries (NY clock)
# ---------------------------------------------------------------------------
# Each row: (label, start, end). Half-open intervals [start, end).
# `end < start` means the session wraps midnight (e.g. asia 18:00 → 03:00).
# Order matters only for tie-breaking; the first matching window wins.

_DEFAULT_WINDOWS: List[Tuple[str, dtime, dtime]] = [
    ("ny_open",   dtime(9, 30),  dtime(11, 30)),
    ("ny_midday", dtime(11, 30), dtime(14, 0)),
    ("ny_close",  dtime(14, 0),  dtime(16, 15)),
    ("london",    dtime(3, 0),   dtime(9, 30)),
    ("asia",      dtime(18, 0),  dtime(3, 0)),    # wraps midnight
    # anything not covered above falls through to "overnight"
]


@dataclass
class SessionConfig:
    """
    Configurable session boundaries.

    `windows` is a list of (label, start, end) tuples in NY clock time.
    A window with `end < start` wraps midnight.

    `fallback_label` is used when a timestamp does not fall in any window.
    """
    windows: List[Tuple[str, dtime, dtime]] = field(
        default_factory=lambda: list(_DEFAULT_WINDOWS)
    )
    fallback_label: str = "overnight"
    timezone: str = NY_TZ

    @classmethod
    def default(cls) -> "SessionConfig":
        return cls()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def label_session(ts: pd.Timestamp, config: SessionConfig | None = None) -> str:
    """
    Return the session label for a single timestamp.

    `ts` must be tz-aware (any zone). Naive timestamps raise ValueError to keep
    the contract explicit — naive datetimes are forbidden in this codebase.
    """
    if config is None:
        config = SessionConfig.default()

    ts = pd.Timestamp(ts)
    if ts.tzinfo is None:
        raise ValueError(
            "label_session requires a tz-aware timestamp; got naive."
        )

    ts_local = ts.tz_convert(config.timezone)
    t = ts_local.time()

    for label, start, end in config.windows:
        if _in_window(t, start, end):
            return label

    return config.fallback_label


def label_sessions_for_index(
    timestamps: pd.DatetimeIndex | pd.Series,
    config: SessionConfig | None = None,
) -> pd.Series:
    """
    Vectorized labeller. Returns a Series of session labels aligned to the
    input index. Useful when bucketing many bars at once for the
    conditional-frequency baseline.
    """
    if config is None:
        config = SessionConfig.default()

    if isinstance(timestamps, pd.Series):
        idx = pd.DatetimeIndex(timestamps.values)
    elif isinstance(timestamps, pd.DatetimeIndex):
        idx = timestamps
    else:
        idx = pd.DatetimeIndex(timestamps)

    if idx.tz is None:
        raise ValueError(
            "label_sessions_for_index requires a tz-aware DatetimeIndex; got naive."
        )

    local = idx.tz_convert(config.timezone)
    times = local.time
    labels = [_classify_time(t, config) for t in times]
    return pd.Series(labels, index=idx, name="session_label")


def _classify_time(t: dtime, config: SessionConfig) -> str:
    for label, start, end in config.windows:
        if _in_window(t, start, end):
            return label
    return config.fallback_label


def _in_window(t: dtime, start: dtime, end: dtime) -> bool:
    """
    Half-open [start, end). When end < start the window wraps midnight.
    A window of (start == end) matches nothing (zero-length).
    """
    if start == end:
        return False
    if start < end:
        return start <= t < end
    # wraps midnight
    return t >= start or t < end
