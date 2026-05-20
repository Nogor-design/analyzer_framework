"""
Parser for PantheonBotV2 EnableDebugPrint output captured from the
NinjaTrader Output window.

Expected line format (PantheonBotV2.cs:418-426):

    2026-05-15 09:30:00 | Trend=Up slope=0.0421 | VWAP=Above distATR=0.342 | Vol=Mid pct=54.2%

Returns a tz-aware DataFrame with one row per parsed bar. Malformed lines
are skipped silently and counted in the returned `n_skipped` attribute.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Union

import pandas as pd


_NUM = r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?|NaN"

_LINE_RE = re.compile(
    r"^(?P<dt>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s*\|\s*"
    r"Trend=(?P<trend>\w+)\s+slope=(?P<slope>" + _NUM + r")\s*\|\s*"
    r"VWAP=(?P<vwap>\w+)\s+distATR=(?P<dist>" + _NUM + r")\s*\|\s*"
    r"Vol=(?P<vol>\w+)\s+pct=(?P<pct>-?\d+(?:\.\d+)?)\s*%"
)

# Optional appendix added by the v2 debug print. Captures the upstream
# intermediates so parity tests can name the first divergent quantity.
_EXTRA_RE = re.compile(
    r"ema_htf=(?P<ema_htf>" + _NUM + r")\s+"
    r"atr_htf=(?P<atr_htf>" + _NUM + r")\s+"
    r"atr_pri=(?P<atr_pri>" + _NUM + r")\s+"
    r"vwap=(?P<vwap_v>" + _NUM + r")\s+"
    r"atr_vol=(?P<atr_vol>" + _NUM + r")"
)


def _maybe_float(s: str) -> float:
    return float("nan") if s == "NaN" else float(s)


@dataclass(frozen=True)
class ParseResult:
    df: pd.DataFrame
    n_parsed: int
    n_skipped: int
    skipped_samples: tuple[str, ...]


def parse_debug_log(
    source: Union[str, Path, Iterable[str]],
    tz: str = "America/Denver",
    max_skipped_samples: int = 5,
) -> ParseResult:
    if isinstance(source, (str, Path)):
        text = Path(source).read_text(encoding="utf-8", errors="replace")
        lines: Iterable[str] = text.splitlines()
    else:
        lines = source

    rows: list[dict] = []
    skipped: list[str] = []
    n_parsed = 0
    n_skipped = 0

    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        m = _LINE_RE.match(line)
        if m is None:
            n_skipped += 1
            if len(skipped) < max_skipped_samples:
                skipped.append(line)
            continue

        row = {
            "dt": m.group("dt"),
            "trend_cs": m.group("trend").lower(),
            "slope": _maybe_float(m.group("slope")),
            "vwap_cs": m.group("vwap").lower(),
            "dist_atr": _maybe_float(m.group("dist")),
            "vol_cs": m.group("vol").lower(),
            "pct": float(m.group("pct")) / 100.0,
            "ema_htf_cs": float("nan"),
            "atr_htf_cs": float("nan"),
            "atr_pri_cs": float("nan"),
            "vwap_cs_v": float("nan"),
            "atr_vol_cs": float("nan"),
        }
        extra = _EXTRA_RE.search(line, m.end())
        if extra is not None:
            row["ema_htf_cs"] = _maybe_float(extra.group("ema_htf"))
            row["atr_htf_cs"] = _maybe_float(extra.group("atr_htf"))
            row["atr_pri_cs"] = _maybe_float(extra.group("atr_pri"))
            row["vwap_cs_v"]  = _maybe_float(extra.group("vwap_v"))
            row["atr_vol_cs"] = _maybe_float(extra.group("atr_vol"))
        rows.append(row)
        n_parsed += 1

    if not rows:
        df = pd.DataFrame(
            columns=[
                "dt", "trend_cs", "slope", "vwap_cs", "dist_atr", "vol_cs", "pct",
                "ema_htf_cs", "atr_htf_cs", "atr_pri_cs", "vwap_cs_v", "atr_vol_cs",
            ]
        )
    else:
        df = pd.DataFrame(rows)
        df["dt"] = pd.to_datetime(df["dt"], errors="coerce").dt.tz_localize(
            tz, nonexistent="shift_forward", ambiguous="NaT"
        )
        df = df.dropna(subset=["dt"]).reset_index(drop=True)

    return ParseResult(
        df=df,
        n_parsed=n_parsed,
        n_skipped=n_skipped,
        skipped_samples=tuple(skipped),
    )
