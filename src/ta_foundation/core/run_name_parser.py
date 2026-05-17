"""
core/run_name_parser.py
───────────────────────
Single source of truth for:
  - Artifact suffix stripping  (filename → run_id)
  - Market/instrument inference (run_id → market symbol + tick value)

Parsing precedence (three steps):
  A. Dash-suffix market code  — CoilingAresFireB-NQ  → NQ
  B. Legacy first-token match — GranitPhoenixInferno → YM  (first CamelCase word)
  C. Default                  — everything else      → NQ

Step B uses the *first CamelCase word* of the run_id (e.g. "Granit" from
"GranitPhoenixInferno"), not a substring search of the whole name.  This
matches the documented convention ("the first CamelCase token at the front of
the run name is a market prefix") and prevents false positives such as
"Coiling" accidentally matching the "oil" token.

To add a new explicit market code: add it to EXPLICIT_MARKET_CODES and TICK_VALUE_USD.
To add a new legacy token mapping: append to TOKEN_TO_SYMBOL (first match wins).
"""

from __future__ import annotations

import re as _re
from dataclasses import dataclass
from pathlib import Path
from typing import Tuple


# ─────────────────────────────────────────────────────────────────────────────
# Artifact suffixes
# ─────────────────────────────────────────────────────────────────────────────

# All recognized file suffixes that belong to a run-scoped artifact.
# A filename that ends with one of these has the run_id as its leading stem.
# Add new suffixes here as new artifact types are introduced.
ARTIFACT_SUFFIXES: tuple[str, ...] = (
    "_Trades.csv",
    "_Analysis.csv",
    "_Summary.csv",    # canonical spelling
    "_Summery.csv",    # NinjaTrader typo variant (keep for legacy data)
    "_Settings.csv",
    "_Optimization.csv",
    "_Summary.png",
    "_Analysis.png",
    "_Summery.png",    # typo image variant
)

# Bare artifact filenames used by the folder-based layout, where each run
# lives in its own subfolder (e.g. ``output/<run_id>/Trades.csv``).
# When a path's filename matches one of these, the run_id is the parent
# folder name rather than a stripped prefix of the filename.
BARE_ARTIFACT_NAMES: frozenset[str] = frozenset({
    "Trades.csv",
    "Analysis.csv",
    "Summary.csv",
    "Summery.csv",   # tolerate the same typo variant as the legacy layout
    "Settings.csv",
})


def is_bare_artifact_name(filename: str) -> bool:
    """True if *filename* uses the folder-based bare naming convention."""
    return filename in BARE_ARTIFACT_NAMES


def strip_artifact_suffix(filename: str) -> str:
    """
    Given a bare filename (not a full path), strip the first matching artifact
    suffix and return the run_id prefix.

    Examples
    --------
    >>> strip_artifact_suffix("GranetPhoenixInferno_Trades.csv")
    'GranetPhoenixInferno'
    >>> strip_artifact_suffix("CoilingAresFireB-NQ_Summary.csv")
    'CoilingAresFireB-NQ'
    >>> strip_artifact_suffix("unknown.txt")
    'unknown'
    """
    for suffix in ARTIFACT_SUFFIXES:
        if filename.endswith(suffix):
            return filename[: -len(suffix)]
    # Fallback: strip file extension only (preserves existing pipeline.py behaviour)
    return Path(filename).stem


def derive_run_id_from_path(path: Path) -> str:
    """
    Resolve a run_id from a full file path, supporting both layouts:

    * Folder-based:   ``.../<run_id>/Trades.csv``  → parent folder name
    * Suffix-based:   ``.../<run_id>_Trades.csv``  → stripped prefix
    """
    if is_bare_artifact_name(path.name):
        return path.parent.name
    return strip_artifact_suffix(path.name)


# ─────────────────────────────────────────────────────────────────────────────
# Market / instrument mappings
# ─────────────────────────────────────────────────────────────────────────────

# USD value of one tick for each instrument.
TICK_VALUE_USD: dict[str, float] = {
    "NQ":  5.0,
    "MNQ": 0.5,
    "ES":  12.5,
    "RTY": 5.0,
    "CL":  10.0,
    "NG":  10.0,
    "GC":  10.0,
    "YM":  5.0,
}

# Explicit market codes recognised in the new dash-suffix convention.
# Checked as a trailing "-<CODE>" on the run_id.  Case-sensitive (always upper).
# To add a new instrument, add it here AND in TICK_VALUE_USD.
EXPLICIT_MARKET_CODES: frozenset[str] = frozenset(TICK_VALUE_USD.keys())

# Legacy token → symbol mapping for the old CamelCase-prefix convention.
# Each entry is (lowercase_first_token, market_symbol).
# The token is matched against the FIRST CamelCase word of the run_id only,
# not against the whole name — this prevents false positives such as
# "Coiling" accidentally matching "oil".
# First match wins; keep the most-specific tokens earlier in the list.
TOKEN_TO_SYMBOL: list[tuple[str, str]] = [
    ("gassious", "NG"),
    ("gass",     "NG"),
    ("oily",     "CL"),
    ("oil",      "CL"),
    ("slate",    "RTY"),
    ("granit",   "YM"),   # "Granit..." naming family
    ("granet",   "YM"),   # alternate spelling used in some run names
    ("iron",     "ES"),
    ("brass",    "MNQ"),
    ("gold",     "GC"),
    ("bronze",   "NQ"),
]


# ─────────────────────────────────────────────────────────────────────────────
# Parsed result
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class RunNameInfo:
    """
    Parsed representation of a run_id.

    Attributes
    ----------
    raw_run_id : str
        The original run_id as derived from the filename, unchanged.
        This is the key used in AnalysisPackage / packages dict.
        Example: ``"CoilingAresFireB-NQ"``
    strategy_name : str
        Logical strategy name with any trailing market suffix removed.
        For legacy names this equals ``raw_run_id``.
        Example: ``"CoilingAresFireB"``
    market : str
        Inferred futures symbol.
        Example: ``"NQ"``
    tick_value : float
        USD value of one tick for this instrument.
    instrument_source : str
        How the market was determined.  One of:
        ``"dash_suffix"``, ``"token:<token>"``, ``"default"``
    """
    raw_run_id: str
    strategy_name: str
    market: str
    tick_value: float
    instrument_source: str


# ─────────────────────────────────────────────────────────────────────────────
# Internal helper
# ─────────────────────────────────────────────────────────────────────────────

def _first_camel_token(run_id: str) -> str:
    """
    Return the first CamelCase word of *run_id* as a lowercase string.

    Examples
    --------
    >>> _first_camel_token("GranitPhoenixInferno")
    'granit'
    >>> _first_camel_token("BronzeBot")
    'bronze'
    >>> _first_camel_token("CoilingAresFireB-NQ")
    'coiling'
    >>> _first_camel_token("allLowerCase")
    'alllowercase'   # fallback: whole name lowercased
    """
    m = _re.match(r"[A-Z][a-z]*", run_id)
    return m.group(0).lower() if m else run_id.lower()


# ─────────────────────────────────────────────────────────────────────────────
# Core parsing function
# ─────────────────────────────────────────────────────────────────────────────

def parse_run_name(run_id: str) -> RunNameInfo:
    """
    Determine the market and logical strategy name from a run_id.

    Precedence
    ----------
    A. Dash-suffix market code (new convention)
       ``CoilingAresFireB-NQ``   →  market=NQ, strategy_name=CoilingAresFireB
    B. First CamelCase token (legacy CamelCase-prefix convention)
       ``GranitPhoenixInferno``  →  market=YM  (first token "Granit" → "granit")
       ``GranetPhoenixInferno``  →  market=YM  (first token "Granet" → "granet")
    C. Default
       ``CoilingAresFireB``      →  market=NQ  ("Coiling" matches no token)

    Parameters
    ----------
    run_id : str
        The run identifier as produced by ``strip_artifact_suffix``.

    Returns
    -------
    RunNameInfo
    """
    # ── Step A: explicit dash-suffix code ────────────────────────────────────
    # Check longest codes first so that e.g. "MNQ" is matched before "NQ".
    for code in sorted(EXPLICIT_MARKET_CODES, key=len, reverse=True):
        dash_suffix = f"-{code}"
        if run_id.endswith(dash_suffix) and len(run_id) > len(dash_suffix):
            strategy_name = run_id[: -len(dash_suffix)]
            tick = TICK_VALUE_USD.get(code, 5.0)
            return RunNameInfo(
                raw_run_id=run_id,
                strategy_name=strategy_name,
                market=code,
                tick_value=tick,
                instrument_source="dash_suffix",
            )

    # ── Step B: first CamelCase token ────────────────────────────────────────
    # Extract the very first CamelCase word and compare it to known tokens.
    # Matching only the first word (not anywhere in the name) prevents false
    # positives such as "Coiling" matching the "oil" token.
    first_token = _first_camel_token(run_id)
    for token, symbol in TOKEN_TO_SYMBOL:
        if token == first_token:
            tick = TICK_VALUE_USD.get(symbol, 5.0)
            return RunNameInfo(
                raw_run_id=run_id,
                strategy_name=run_id,   # legacy names: strategy_name == run_id
                market=symbol,
                tick_value=tick,
                instrument_source=f"token:{token}",
            )

    # ── Step C: default NQ ───────────────────────────────────────────────────
    return RunNameInfo(
        raw_run_id=run_id,
        strategy_name=run_id,
        market="NQ",
        tick_value=TICK_VALUE_USD["NQ"],
        instrument_source="default",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Convenience shim — keeps callers using the old signature working
# ─────────────────────────────────────────────────────────────────────────────

def infer_instrument_from_run_id(run_id: str) -> Tuple[str, float, str]:
    """
    Shim for callers that still use the old (symbol, tick_value, source) tuple.
    Delegates to parse_run_name().
    """
    info = parse_run_name(run_id)
    return info.market, info.tick_value, info.instrument_source
