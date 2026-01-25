# src/ta_foundation/utils/parsing.py
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Any, Optional

_TZ = ZoneInfo("America/Denver")

_MONEY_RE = re.compile(r"[,\s\$]")
_PCT_RE = re.compile(r"%\s*$")


def _basename_strategy_type(full: Optional[str]) -> Optional[str]:
    if not full:
        return None
    # Example: NinjaTrader.NinjaScript.Strategies.PantheonMasterBotV01TesterV2
    return full.split(".")[-1] if "." in full else full

def _as_bool(s: Optional[str]) -> Optional[bool]:
    if s is None:
        return None
    v = s.strip().lower()
    if v in ("true", "1", "yes", "y"):
        return True
    if v in ("false", "0", "no", "n"):
        return False
    return None


def _as_int(s: Optional[str]) -> Optional[int]:
    if s is None:
        return None
    try:
        return int(str(s).strip())
    except Exception:
        return None


def _as_float(s: Optional[str]) -> Optional[float]:
    if s is None:
        return None
    try:
        return float(str(s).strip())
    except Exception:
        return None


def _safe_find_text(node: ET.Element, tag: str) -> Optional[str]:
    el = node.find(tag)
    if el is None or el.text is None:
        return None
    return el.text.strip()

def _is_blank(x: Any) -> bool:
    if x is None:
        return True
    s = str(x).strip()
    return s == "" or s.lower() in {"nan", "none", "null"}


def parse_int(x: Any) -> Optional[int]:
    if _is_blank(x):
        return None
    s = str(x).strip()
    s = s.replace(",", "")
    try:
        return int(float(s))
    except ValueError:
        return None


def parse_float(x: Any) -> Optional[float]:
    if _is_blank(x):
        return None
    s = str(x).strip().replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


def parse_money(x: Any) -> Optional[float]:
    """
    Handles:
      $1,234.50
      (1500.00) or ($1,500.00)  -> negative
      -1500.00
    """
    if _is_blank(x):
        return None

    s = str(x).strip()
    neg = False

    if s.startswith("(") and s.endswith(")"):
        neg = True
        s = s[1:-1].strip()

    s = _MONEY_RE.sub("", s)  # remove $, commas, spaces
    if s.startswith("-"):
        neg = True
        s = s[1:]

    try:
        val = float(s)
    except ValueError:
        return None

    return -val if neg else val


def parse_percent(x: Any) -> Optional[float]:
    """
    Converts:
      "71.05%" -> 0.7105
      "0.49%"  -> 0.0049
      "0.7105" -> 0.7105 (assumed already fraction if no % sign)
    """
    if _is_blank(x):
        return None

    s = str(x).strip()
    has_pct = s.endswith("%")
    s = _PCT_RE.sub("", s).strip()
    s = s.replace(",", "")

    try:
        v = float(s)
    except ValueError:
        return None

    return v / 100.0 if has_pct else v


def parse_local_dt(x: Any, tz: ZoneInfo = _TZ) -> Optional[datetime]:
    """
    Parses NinjaTrader-exported local PC time into America/Denver-aware datetime.
    Accepts common timestamp strings; relies on pandas for edge formats in parser layer if needed.
    """
    if _is_blank(x):
        return None

    s = str(x).strip()

    # Try common formats first (fast, explicit)
    fmts = [
        "%m/%d/%Y %I:%M:%S %p",  # 01/24/2026 03:15:22 PM
        "%m/%d/%Y %H:%M:%S",     # 01/24/2026 15:15:22
        "%m/%d/%Y %H:%M",        # 01/24/2026 15:15
        "%Y-%m-%d %H:%M:%S",     # 2026-01-24 15:15:22
        "%Y-%m-%d %H:%M",        # 2026-01-24 15:15
    ]
    for fmt in fmts:
        try:
            naive = datetime.strptime(s, fmt)
            return naive.replace(tzinfo=tz)
        except ValueError:
            continue

    # Fall back: raise a controlled error (parser can choose to use pandas.to_datetime)
    raise ValueError(f"Unrecognized datetime format: {s!r}")



@dataclass(frozen=True)
class ParseWarning:
    code: str
    message: str
    row_count: int | None = None
