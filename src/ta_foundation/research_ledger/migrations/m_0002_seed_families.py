"""Seed the 13 starter hypothesis families.

Each family carries a parameter whitelist (used by family_registry.validate_params
in the agent layer) and a one-paragraph mechanism template that nudges the
Hypothesis Author toward a structural counterparty story when drafting a new
hypothesis.

Adding or modifying a family is a code change with a reviewed migration. The
families are *not* dynamic — that constraint is the master plan §1 rule "agents
cannot invent a new family" turned into data.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

VERSION = 2


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _canonical_json(payload) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


COMMON_STOP = {"type": "int", "min": 1, "max": 200}
COMMON_TARGET = {"type": "int", "min": 1, "max": 800}

FAMILIES: list[dict] = [
    {
        "family_id": "vwap_reject_fade",
        "description": "Mean-reversion fade after price rejects an extended VWAP probe.",
        "mechanism_template": (
            "Trapped breakout participants fade back through VWAP after a momentum "
            "probe fails to hold above the developing volume-weighted reference; "
            "the inefficiency comes from the late chasers exiting their losing "
            "position into the reversion."
        ),
        "params": {
            "min_distance_ticks": {"type": "int", "min": 1, "max": 100},
            "max_distance_ticks": {"type": "int", "min": 1, "max": 300},
            "stop_ticks": COMMON_STOP,
            "target_ticks": COMMON_TARGET,
        },
    },
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
    {
        "family_id": "prior_high_low_failed_breakout",
        "description": "Failed breakout of prior session high or low; reversal back into range.",
        "mechanism_template": (
            "Late breakout chasers above the prior session high (or below the prior "
            "low) get trapped when the move fails to attract follow-through; their "
            "stops fuel the reversal back into range."
        ),
        "params": {
            "level_type": {"type": "enum", "values": ["prior_high", "prior_low", "both"]},
            "break_buffer_ticks": {"type": "int", "min": 0, "max": 50},
            "max_failure_bars": {"type": "int", "min": 1, "max": 30},
            "stop_ticks": COMMON_STOP,
            "target_ticks": COMMON_TARGET,
        },
    },
    {
        "family_id": "overnight_high_low_sweep_reclaim",
        "description": "Sweep of overnight high/low followed by reclaim back into the range.",
        "mechanism_template": (
            "Overnight liquidity pools above and below the prior RTH range are "
            "swept by RTH liquidity-seeking algos; the reclaim into the range traps "
            "the breakout participants and provides the directional impulse the "
            "other way."
        ),
        "params": {
            "level_type": {"type": "enum", "values": ["onh", "onl", "both"]},
            "sweep_min_ticks": {"type": "int", "min": 1, "max": 50},
            "reclaim_within_bars": {"type": "int", "min": 1, "max": 20},
            "stop_ticks": COMMON_STOP,
            "target_ticks": COMMON_TARGET,
        },
    },
    {
        "family_id": "orb_breakout",
        "description": "Opening range breakout entry on close beyond or extreme test.",
        "mechanism_template": (
            "When participation expands at the open and price breaks the initial "
            "consolidation range, the breakout extends as fade-the-open mean-"
            "reversion participants get squeezed; the edge persists when volume and "
            "auction conditions support directional follow-through."
        ),
        "params": {
            "orb_minutes": {"type": "int", "min": 5, "max": 60},
            "signal_type": {"type": "enum", "values": ["break_close", "break_extreme"]},
            "stop_ticks": COMMON_STOP,
            "target_ticks": COMMON_TARGET,
        },
    },
    {
        "family_id": "orb_failure_reclaim",
        "description": "Opening range sweep then reclaim — failed auction reversal.",
        "mechanism_template": (
            "An opening range breakout that fails to find continuation reclaims the "
            "range; trapped breakout participants exit into the reverse move, "
            "providing the impulse for the opposite direction. Body-midpoint and "
            "range-midpoint reentries capture the post-trap pullback."
        ),
        "params": {
            "orb_minutes": {"type": "int", "min": 5, "max": 60},
            "sweep_min_ticks": {"type": "int", "min": 1, "max": 50},
            "reclaim_within_bars": {"type": "int", "min": 1, "max": 20},
            "fill_mode": {
                "type": "enum",
                "values": ["reclaim_close", "body_midpoint", "range_midpoint"],
            },
            "stop_ticks": COMMON_STOP,
            "target_ticks": COMMON_TARGET,
        },
    },
    {
        "family_id": "prior_close_settlement_reaction",
        "description": "Reaction at prior session close / settlement reference level.",
        "mechanism_template": (
            "Algos and overnight inventory anchor on prior settlement; reactions at "
            "this level reflect resting liquidity, risk-management orders, and "
            "cluster-of-interest behavior from positioned participants."
        ),
        "params": {
            "distance_ticks": {"type": "int", "min": 0, "max": 50},
            "reaction_mode": {"type": "enum", "values": ["fade", "continuation"]},
            "stop_ticks": COMMON_STOP,
            "target_ticks": COMMON_TARGET,
        },
    },
    {
        "family_id": "initial_balance_extension",
        "description": "Trend continuation past the initial balance high or low.",
        "mechanism_template": (
            "When the first hour establishes a directional initial balance and the "
            "market extends, the late-to-the-party trend followers provide flow "
            "behind the move; the IB extension trade rides their entries."
        ),
        "params": {
            "ib_minutes": {"type": "int", "min": 30, "max": 120},
            "extension_buffer_ticks": {"type": "int", "min": 0, "max": 30},
            "stop_ticks": COMMON_STOP,
            "target_ticks": COMMON_TARGET,
        },
    },
    {
        "family_id": "initial_balance_reversal",
        "description": "Failed IB extension reversing back into initial balance.",
        "mechanism_template": (
            "An IB extension attempt that fails to find continuation traps the "
            "breakout cohort; reversal back into the initial balance is the path of "
            "least resistance and absorbs the trapped flow."
        ),
        "params": {
            "ib_minutes": {"type": "int", "min": 30, "max": 120},
            "extension_attempt_ticks": {"type": "int", "min": 1, "max": 50},
            "stop_ticks": COMMON_STOP,
            "target_ticks": COMMON_TARGET,
        },
    },
    {
        "family_id": "large_candle_origin_retest",
        "description": "Retest of the origin of an impulsive (large) candle.",
        "mechanism_template": (
            "Impulsive candles leave behind imbalanced order flow; price often "
            "retraces to retest the candle's origin where resting liquidity remains. "
            "The participants who missed the initial move provide entry flow at the "
            "retest."
        ),
        "params": {
            "candle_size_ticks_min": {"type": "int", "min": 1, "max": 200},
            "retrace_pct": {"type": "float", "min": 0.0, "max": 1.0},
            "stop_ticks": COMMON_STOP,
            "target_ticks": COMMON_TARGET,
        },
    },
    {
        "family_id": "compression_then_expansion",
        "description": "Volatility contraction followed by directional expansion.",
        "mechanism_template": (
            "Volatility contraction creates a tightening order book; the expansion "
            "side captures the breakout from coiled liquidity as participants take "
            "directional bias once the regime change is confirmed."
        ),
        "params": {
            "compression_bars": {"type": "int", "min": 3, "max": 60},
            "compression_range_ticks_max": {"type": "int", "min": 1, "max": 100},
            "breakout_buffer_ticks": {"type": "int", "min": 0, "max": 30},
            "stop_ticks": COMMON_STOP,
            "target_ticks": COMMON_TARGET,
        },
    },
    {
        "family_id": "trend_pullback_continuation",
        "description": "Pullback to MA/VWAP in an established directional trend.",
        "mechanism_template": (
            "Established trends attract pullback buyers and sellers at well-known "
            "references (VWAP, EMA20, EMA50); continuation entries fade the pullback "
            "exhaustion when the regime label still confirms direction."
        ),
        "params": {
            "pullback_target": {"type": "enum", "values": ["vwap", "ema20", "ema50"]},
            "pullback_max_ticks": {"type": "int", "min": 1, "max": 100},
            "stop_ticks": COMMON_STOP,
            "target_ticks": COMMON_TARGET,
        },
    },
    {
        "family_id": "exhaustion_into_reference",
        "description": "N-bar one-way move into a reference level — mean-reversion entry.",
        "mechanism_template": (
            "After N bars of one-way movement into a known reference level, "
            "momentum is depleted and the late chase becomes the counterparty for "
            "mean reversion off the reference."
        ),
        "params": {
            "n_bars": {"type": "int", "min": 3, "max": 30},
            "reference_type": {
                "type": "enum",
                "values": ["prior_high", "prior_low", "vwap", "orh", "orl"],
            },
            "exhaustion_threshold_ticks": {"type": "int", "min": 1, "max": 100},
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
