from __future__ import annotations

"""
Session Constants
=================
Single authoritative source for session label taxonomy used across the
strategy discovery pipeline (entry_pattern_bridge, features, evaluation).

Two taxonomies exist in this codebase:

1. **Strategy-discovery labels** (this module) — PantheonMaster-style buckets
   used in signal/trade feature matrices, entry discovery, and evaluation:
     London, NyPre, NyOpen, NyMid, PowerHr, Asia, other

2. **Market-regime labels** (market_regime_store._classify_session) — bar-level
   regime classification for pandas Series ops:
     london, pre_open, us_open, us_morning, us_midday, us_afternoon, globex_evening

Do not merge the two; they serve different consumers. This module owns #1.
"""

# ---------------------------------------------------------------------------
# Strategy-discovery session label map (hour → label, America/Denver local)
# ---------------------------------------------------------------------------

#: Maps each hour of the day to a strategy-discovery session label.
SESSION_HOUR_MAP: dict[int, str] = {
    0: "London",    # 00:00–05:59
    1: "London",
    2: "London",
    3: "London",
    4: "London",
    5: "London",
    6: "NyPre",     # 06:00–06:59
    7: "NyOpen",    # 07:00–08:59
    8: "NyOpen",
    9: "NyMid",     # 09:00–12:59
    10: "NyMid",
    11: "NyMid",
    12: "NyMid",
    13: "PowerHr",  # 13:00–13:59
    # 14–17: no named session
    18: "Asia",     # 18:00–23:59
    19: "Asia",
    20: "Asia",
    21: "Asia",
    22: "Asia",
    23: "Asia",
}


def session_label_from_hour(hour: int) -> str:
    """
    Map an entry hour (America/Denver local, 0-23) to a session label.

    Used by entry_pattern_bridge, features, and evaluation modules.
    Returns ``"other"`` for hours with no named session (14–17).
    """
    return SESSION_HOUR_MAP.get(int(hour), "other")


#: Ordered list of all distinct strategy-discovery session labels.
SESSION_LABEL_ORDER: list[str] = [
    "London",
    "NyPre",
    "NyOpen",
    "NyMid",
    "PowerHr",
    "Asia",
    "other",
]
