"""Migration 0007 — register the Pantheon SMA-crossover research family.

PantheonMaster is an existing strategy whose entry mechanism is a fast/slow
SMA crossover. Its r2 campaign keeps that entry fixed and tests only ATR /
Chandelier exit geometry plus named-session inclusion. This deserves an
explicit forward-authoring family rather than misusing ``legacy_imported`` or
pretending the entry is a pullback.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

VERSION = 7


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def apply(conn: sqlite3.Connection) -> None:
    params = {
        "AtrTrailMultiple": {"type": "float", "min": 0.5, "max": 6.0},
        "ChandelierLookback": {"type": "int", "min": 3, "max": 100},
        "AllowLondon": {"type": "bool"},
        "AllowNyPre": {"type": "bool"},
        "AllowNyOpen": {"type": "bool"},
        "AllowNyMid": {"type": "bool"},
        "AllowMyPowerHr": {"type": "bool"},
        "AllowAsia": {"type": "bool"},
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
            "pantheon_sma_crossover",
            (
                "PantheonMaster fast/slow SMA crossover with fixed entry logic; "
                "research varies ATR/Chandelier exit geometry and named-session gates."
            ),
            json.dumps(params, sort_keys=True, separators=(",", ":")),
            (
                "A fast/slow SMA crossover represents a change in short-horizon trend "
                "leadership. The hypothesis is that continuation after the crossover "
                "persists selectively by named session, while ATR-scaled trailing exits "
                "capture that continuation without relying on a single fixed tick target."
            ),
            _now_iso(),
        ),
    )
