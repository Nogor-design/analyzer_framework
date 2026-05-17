"""Migration 0006 — adds the vwap_continuation family.

This family focuses on trend continuation after price reclaims VWAP and then
moves further in that direction.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

VERSION = 6


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _canonical_json(payload) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


COMMON_STOP = {"type": "int", "min": 1, "max": 200}
COMMON_TARGET = {"type": "int", "min": 1, "max": 800}

FAMILIES: list[dict] = [
    {
        "family_id": "vwap_reclaim_continuation",
        "description": "Continuation after price reclaims VWAP from below or above.",
        "mechanism_template": (
            "After a thrust below or above VWAP fails to attract continuation flow, "
            "mean-reversion participants reclaim the volume-weighted reference and "
            "the trend in the original direction resumes; counterparties are the "
            "would-be reversal traders who initiated against the reclaim."
        ),
        "params": {
            "reclaim_max_bars": {"type": "int", "min": 1, "max": 30},
            "stop_ticks": COMMON_STOP,
            "target_ticks": COMMON_TARGET,
        },
    },
]


def apply(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    seeded_at = _now_iso()
    for fam in FAMILIES:
        cur.execute(
            """
            INSERT INTO families (family_id, description, legitimate_params_json,
                                  mechanism_template, seeded_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(family_id) DO UPDATE SET
                description = excluded.description,
                legitimate_params_json = excluded.legitimate_params_json,
                mechanism_template = excluded.mechanism_template
            """,
            (
                fam["family_id"],
                fam["description"],
                _canonical_json(fam["params"]),
                fam["mechanism_template"],
                seeded_at,
            ),
        )
