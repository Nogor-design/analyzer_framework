"""Migration 0009 — register the cash-open first-bar continuation family."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

VERSION = 9


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def apply(conn: sqlite3.Connection) -> None:
    params = {
        "CashOpenHour": {"type": "int", "min": 0, "max": 23},
        "CashOpenMinute": {"type": "int", "min": 0, "max": 59},
        "MinBodyTicks": {"type": "int", "min": 1, "max": 100},
        "TargetBodyMultiple": {"type": "float", "min": 0.1, "max": 10.0},
        "StopBodyMultiple": {"type": "float", "min": 0.1, "max": 10.0},
        "MaxBarsInTrade": {"type": "int", "min": 1, "max": 600},
    }
    conn.execute(
        """
        INSERT INTO families (
            family_id, description, legitimate_params_json,
            mechanism_template, seeded_at
        ) VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(family_id) DO UPDATE SET
            description = excluded.description,
            legitimate_params_json = excluded.legitimate_params_json,
            mechanism_template = excluded.mechanism_template
        """,
        (
            "cash_open_first_bar_follow_through",
            (
                "Continuation in the direction of a meaningful first cash-session "
                "bar, with body-scaled risk and a bounded holding period."
            ),
            json.dumps(params, sort_keys=True, separators=(",", ":")),
            (
                "The first cash-session bar incorporates accumulated overnight "
                "positioning and opening-auction demand. When its body is large "
                "enough to represent a genuine imbalance, institutional price "
                "discovery can continue in that direction during the next hour."
            ),
            _now_iso(),
        ),
    )
