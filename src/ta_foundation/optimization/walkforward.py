from __future__ import annotations

"""Walk-forward window planning.

Given an anchor date, a window length, an optional gap, and a count,
produces a list of (from_date, to_date) tuples representing rolling
historical windows. The convention is **backwards from the anchor**: the
first window ends at the anchor date and stretches back ``window_days``.
The next window ends ``gap_days`` before that, and so on.

This module is pure-function and standalone — no NinjaTrader, no session
state. The web layer wraps it to drive per-window template generation.

Conventions
-----------

- Dates are ``datetime.date`` (or ISO ``YYYY-MM-DD`` strings on the
  public ``plan_walk_forward_windows`` entry point).
- Windows are **non-overlapping** by default. A positive ``gap_days``
  inserts a gap between consecutive windows; ``gap_days=0`` makes them
  adjacent.
- All windows have the same length (``window_days``).
- Windows are returned in chronological order (oldest first).
- An optional ``skip_overlap_with`` (from, to) tuple drops any planned
  window that overlaps the given range — used to exclude the original
  backtest's IS window when planning OOS comparison windows.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Iterable


class WalkForwardError(Exception):
    pass


@dataclass(frozen=True)
class WalkForwardWindow:
    index: int
    from_date: str
    to_date: str

    def to_dict(self) -> dict:
        return {"index": self.index, "from_date": self.from_date, "to_date": self.to_date}


def plan_walk_forward_windows(
    *,
    anchor_date: str | date,
    window_days: int,
    count: int,
    gap_days: int = 0,
    skip_overlap_with: tuple[str | date, str | date] | None = None,
) -> list[WalkForwardWindow]:
    """Generate ``count`` rolling windows backwards from ``anchor_date``.

    Parameters
    ----------
    anchor_date : str | date
        The most-recent date the windows should reach back from. The
        first (most-recent) window ends here.
    window_days : int
        Length of each window in days. Must be >= 1.
    count : int
        Number of windows to plan. Must be >= 1.
    gap_days : int
        Days between consecutive windows. 0 = adjacent.
    skip_overlap_with : (str|date, str|date), optional
        If supplied, any planned window overlapping this range is
        excluded from the result. Used to drop the original IS window
        so the planner returns OOS-only windows.

    Returns
    -------
    list[WalkForwardWindow]
        In chronological order (oldest first). May be shorter than
        ``count`` if ``skip_overlap_with`` filtered some out.
    """
    if window_days < 1:
        raise WalkForwardError(f"window_days must be >= 1, got {window_days}")
    if count < 1:
        raise WalkForwardError(f"count must be >= 1, got {count}")
    if gap_days < 0:
        raise WalkForwardError(f"gap_days must be >= 0, got {gap_days}")

    anchor = _coerce_date(anchor_date)
    skip = None
    if skip_overlap_with is not None:
        skip_from = _coerce_date(skip_overlap_with[0])
        skip_to = _coerce_date(skip_overlap_with[1])
        if skip_from > skip_to:
            raise WalkForwardError(
                f"skip_overlap_with[0] ({skip_from}) must precede [1] ({skip_to})"
            )
        skip = (skip_from, skip_to)

    windows: list[WalkForwardWindow] = []
    # We walk backwards but emit chronologically, so build in reverse and reverse at end.
    cursor_to = anchor
    seen = 0
    while seen < count:
        cursor_from = cursor_to - timedelta(days=window_days)
        if cursor_from < date(1900, 1, 1):
            break
        if skip is None or not _ranges_overlap((cursor_from, cursor_to), skip):
            windows.append(WalkForwardWindow(
                index=seen,
                from_date=cursor_from.isoformat(),
                to_date=cursor_to.isoformat(),
            ))
        seen += 1
        cursor_to = cursor_from - timedelta(days=gap_days)

    # Reverse to chronological order and re-number.
    windows.reverse()
    return [
        WalkForwardWindow(index=i, from_date=w.from_date, to_date=w.to_date)
        for i, w in enumerate(windows)
    ]


def _coerce_date(value: str | date) -> date:
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        raise WalkForwardError("date is empty")
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError as exc:
        raise WalkForwardError(f"date must be YYYY-MM-DD: {text!r}") from exc


def _ranges_overlap(a: tuple[date, date], b: tuple[date, date]) -> bool:
    return a[0] <= b[1] and b[0] <= a[1]
