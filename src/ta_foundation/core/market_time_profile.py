from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pandas as pd
from zoneinfo import ZoneInfo

TZ_DENVER = ZoneInfo("America/Denver")
TZ_ET = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class SessionDef:
    key: str
    label: str
    start_et: time  # ET wall-clock
    end_et: time    # ET wall-clock (can be <= start -> crosses midnight)
    color: str      # hex


DEFAULT_SESSIONS: List[SessionDef] = [
    # Futures daily maintenance break:
    # User requested: show 3pm–4pm Colorado (America/Denver).
    # That corresponds to 17:00–18:00 ET (America/New_York).
    SessionDef("closed", "Closed", time(17, 0), time(18, 0), "#0b0f1a"),

    # Asia extended to midnight ET
    SessionDef("asia", "Asia", time(19, 0), time(0, 0), "#6b7280"),  # 7pm–12am ET

    # London begins at midnight ET now
    SessionDef("london", "London", time(0, 0), time(8, 0), "#a78bfa"),  # 12am–8am ET

    SessionDef("ny_premarket", "NY Pre", time(8, 0), time(9, 30), "#f59e0b"),  # 8:00–9:30 ET
    SessionDef("ny_rth", "NY RTH", time(9, 30), time(16, 0), "#22c55e"),        # 9:30–16:00 ET
    SessionDef("ny_after", "NY After", time(16, 0), time(17, 0), "#60a5fa"),    # 16:00–17:00 ET
    # Note: 17:00–18:00 ET is "Closed" above
    SessionDef("ny_evening", "NY Eve", time(18, 0), time(19, 0), "#93c5fd"),    # 18:00–19:00 ET
]


def _find_col(df: pd.DataFrame, candidates: Iterable[str]) -> Optional[str]:
    if df is None or df.empty:
        return None

    def norm(s: str) -> str:
        return "".join(ch for ch in s.lower().strip() if ch.isalnum())

    cols = {norm(c): c for c in df.columns}
    for cand in candidates:
        key = norm(cand)
        if key in cols:
            return cols[key]
    return None


def _pick_anchor_date(pkg: Any) -> date:
    """
    Pick a representative date for DST-correct session conversion.
    Preference order:
      - pkg.metadata["derived"]["run_start_date"] (ISO)
      - first trade exit date
      - today (Denver)
    """
    derived = (getattr(pkg, "metadata", {}) or {}).get("derived", {}) or {}

    iso = derived.get("run_start_date")
    if isinstance(iso, str):
        try:
            return datetime.fromisoformat(iso).date()
        except Exception:
            pass

    trades = getattr(pkg, "trades", None)
    if isinstance(trades, pd.DataFrame) and not trades.empty:
        exit_col = _find_col(trades, ["Exit time", "ExitTime", "Exit date", "ExitDate", "Exit"])
        if exit_col:
            s = pd.to_datetime(trades[exit_col], errors="coerce")
            s = s.dropna()
            if not s.empty:
                # times already localized to America/Denver per contract
                dt = s.iloc[0]
                if hasattr(dt, "date"):
                    return dt.date()

    return datetime.now(TZ_DENVER).date()


def derive_trade_time_profile_for_package(
    pkg: Any,
    *,
    bin_minutes: int = 15,
) -> Dict[str, Any]:
    """
    Derive a time-of-day activity profile from trades.

    Output (JSON-safe):
      {
        "tz": "America/Denver",
        "bin_minutes": 15,
        "bins": 96,
        "active": [0|1 ...],   # length=bins; 1 if any trade overlaps that bin
        "anchor_date": "YYYY-MM-DD"
      }

    Notes:
      - Uses entry/exit timestamps if present; otherwise uses exit timestamps as instant events.
      - Assumes timestamps have already been localized to America/Denver on ingest.
    """
    if bin_minutes <= 0 or 1440 % bin_minutes != 0:
        raise ValueError(f"bin_minutes must divide 1440; got {bin_minutes}")

    bins = 1440 // bin_minutes
    active = [0] * bins

    trades = getattr(pkg, "trades", None)
    if not isinstance(trades, pd.DataFrame) or trades.empty:
        return {
            "tz": "America/Denver",
            "bin_minutes": bin_minutes,
            "bins": bins,
            "active": active,
            "anchor_date": _pick_anchor_date(pkg).isoformat(),
        }

    entry_col = _find_col(trades, ["Entry time", "EntryTime", "Entry", "Entry date", "EntryDate"])
    exit_col = _find_col(trades, ["Exit time", "ExitTime", "Exit", "Exit date", "ExitDate"])

    if not exit_col and not entry_col:
        return {
            "tz": "America/Denver",
            "bin_minutes": bin_minutes,
            "bins": bins,
            "active": active,
            "anchor_date": _pick_anchor_date(pkg).isoformat(),
        }

    ent = pd.to_datetime(trades[entry_col], errors="coerce") if entry_col else None
    ex = pd.to_datetime(trades[exit_col], errors="coerce") if exit_col else None

    # Iterate rows (vectorizing interval overlap cleanly is possible, but explicit is debuggable).
    n = len(trades)
    for i in range(n):
        start = ent.iloc[i] if ent is not None else pd.NaT
        end = ex.iloc[i] if ex is not None else pd.NaT

        if pd.isna(start) and pd.isna(end):
            continue

        # If one side missing, treat as instant event at the available timestamp.
        if pd.isna(start):
            start = end
        if pd.isna(end):
            end = start

        # Guard: if inverted, swap
        if end < start:
            start, end = end, start

        # We only care about time-of-day; mark all bins overlapped by [start, end].
        # If duration is 0, mark the bin containing start.
        start_min = start.hour * 60 + start.minute
        end_min = end.hour * 60 + end.minute

        if start_min == end_min:
            idx = min(bins - 1, start_min // bin_minutes)
            active[idx] = 1
            continue

        # Handle intervals that might cross midnight in local time
        if end.date() != start.date() or end_min < start_min:
            # mark from start to end-of-day
            for m in range(start_min, 1440, bin_minutes):
                active[min(bins - 1, m // bin_minutes)] = 1
            # mark from start-of-day to end_min
            for m in range(0, end_min + 1, bin_minutes):
                active[min(bins - 1, m // bin_minutes)] = 1
            continue

        for m in range(start_min, end_min + 1, bin_minutes):
            active[min(bins - 1, m // bin_minutes)] = 1

    return {
        "tz": "America/Denver",
        "bin_minutes": bin_minutes,
        "bins": bins,
        "active": active,
        "anchor_date": _pick_anchor_date(pkg).isoformat(),
    }


def sessions_as_denver_bins(
    anchor_date_iso: str,
    *,
    bin_minutes: int,
    sessions: List[SessionDef],
) -> List[Tuple[SessionDef, List[int]]]:
    """
    Convert ET sessions into Denver-local bin masks for a given anchor date (DST-correct).

    Returns: list of (SessionDef, mask[int]) where mask length=bins and entries are 0/1.
    """
    if bin_minutes <= 0 or 1440 % bin_minutes != 0:
        raise ValueError(f"bin_minutes must divide 1440; got {bin_minutes}")

    bins = 1440 // bin_minutes
    anchor_date = datetime.fromisoformat(anchor_date_iso).date()

    result: List[Tuple[SessionDef, List[int]]] = []

    for s in sessions:
        mask = [0] * bins

        start_et_dt = datetime.combine(anchor_date, s.start_et, tzinfo=TZ_ET)
        end_et_dt = datetime.combine(anchor_date, s.end_et, tzinfo=TZ_ET)
        if end_et_dt <= start_et_dt:
            end_et_dt = end_et_dt + timedelta(days=1)

        # Convert endpoints to Denver
        start_den = start_et_dt.astimezone(TZ_DENVER)
        end_den = end_et_dt.astimezone(TZ_DENVER)

        # We want bins for a single Denver day. Use anchor_date in Denver as the “day canvas”.
        day_start_den = datetime.combine(anchor_date, time(0, 0), tzinfo=TZ_DENVER)
        day_end_den = day_start_den + timedelta(days=1)

        # Intersect [start_den, end_den) with [day_start_den, day_end_den)
        a = max(start_den, day_start_den)
        b = min(end_den, day_end_den)

        if b <= a:
            # It might fully land outside this Denver day; try shifting anchor date by +/-1 day.
            # This keeps rendering sane around DST and midnight conversions.
            for shift in (-1, 1):
                shifted = anchor_date + timedelta(days=shift)
                day_start_den = datetime.combine(shifted, time(0, 0), tzinfo=TZ_DENVER)
                day_end_den = day_start_den + timedelta(days=1)
                a2 = max(start_den, day_start_den)
                b2 = min(end_den, day_end_den)
                if b2 > a2:
                    a, b = a2, b2
                    break
            else:
                result.append((s, mask))
                continue

        start_min = a.hour * 60 + a.minute
        end_min = b.hour * 60 + b.minute

        for m in range(start_min, max(start_min, end_min) + 1, bin_minutes):
            idx = min(bins - 1, m // bin_minutes)
            mask[idx] = 1

        result.append((s, mask))

    return result


def summarize_overlap(
    active: List[int],
    session_masks: List[Tuple[SessionDef, List[int]]],
) -> List[Tuple[str, float]]:
    """
    For each session, compute % of active bins that fall within that session.
    Returns list of (session_label, pct) sorted desc.
    """
    active_bins = sum(1 for v in active if v)
    if active_bins == 0:
        return [(s.label, 0.0) for s, _ in session_masks]

    out: List[Tuple[str, float]] = []
    for s, mask in session_masks:
        within = 0
        for a, m in zip(active, mask):
            if a and m:
                within += 1
        out.append((s.label, within / active_bins))
    out.sort(key=lambda x: x[1], reverse=True)
    return out
