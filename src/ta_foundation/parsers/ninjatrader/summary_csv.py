from __future__ import annotations

from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Any, Optional

import pandas as pd

from ta_foundation.parsers.base import ParsedArtifact
from ta_foundation.utils.parsing import parse_money, parse_percent, parse_int, parse_float


class NinjaTraderSummaryCsvParser:
    """
    Parses NinjaTrader Strategy Analyzer "Summary/Summery" export.

    Expected columns:
      - Performance
      - All trades
      - Long trades
      - Short trades

    Also extracts:
      - Start date/time
      - End date/time

    Produces:
      ParsedArtifact(kind="summary", summary={
          "kpis_all": {...},
          "kpis_long": {...},
          "kpis_short": {...},
          "start_dt": <tz-aware datetime or None>,
          "end_dt": <tz-aware datetime or None>,
      })
    """
    kind = "summary"
    _TZ = ZoneInfo("America/Denver")

    def can_parse(self, path: Path, header: str) -> bool:
        name_ok = (
            path.name.endswith("_Summery.csv")
            or path.name.endswith("_Summary.csv")
            or path.name == "Summary.csv"
            or path.name == "Summery.csv"
        )
        sig_ok = ("Performance" in header and "All trades" in header and "Long trades" in header)
        return name_ok and sig_ok

    def parse(self, path: Path, run_id: str) -> ParsedArtifact:
        df = pd.read_csv(path)

        # Drop trailing unnamed columns
        df = df.loc[:, ~df.columns.str.startswith("Unnamed")]

        warnings: list[dict[str, Any]] = []

        required = {"Performance", "All trades", "Long trades", "Short trades"}
        missing = required - set(df.columns)
        if missing:
            warnings.append({
                "code": "MISSING_COLUMNS",
                "message": f"Missing columns {sorted(missing)} in {path.name}",
            })

        def is_blank(x: Any) -> bool:
            if x is None:
                return True
            if isinstance(x, float) and pd.isna(x):
                return True
            s = str(x).strip()
            return s == "" or s.lower() in {"nan", "none", "null"}

        def parse_value(v: Any) -> Any:
            """
            Best-effort typed parsing:
              - money: contains '$' or parentheses
              - percent: endswith '%'
              - float: numeric
              - int: only if it is truly an integer (no decimals)
              - else: string as-is
            """
            if is_blank(v):
                return None

            s = str(v).strip()

            # Money cues
            if "$" in s or (s.startswith("(") and s.endswith(")")):
                m = parse_money(s)
                return m if m is not None else s

            # Percent cues
            if s.endswith("%"):
                p = parse_percent(s)
                return p if p is not None else s

            # Float first (important: prevents 4.85 -> 4)
            f = parse_float(s)
            if f is not None:
                # If it's an integer-valued float, return int
                if abs(f - int(f)) < 1e-12:
                    return int(f)
                return f

            return s

        from ta_foundation.utils.kpi import NormalizedKpiDict

        kpis_all_raw: dict[str, Any] = {}
        kpis_long_raw: dict[str, Any] = {}
        kpis_short_raw: dict[str, Any] = {}

        # Collect start/end pieces (they appear in "All trades" column)
        start_date: Optional[str] = None
        start_time: Optional[str] = None
        end_date: Optional[str] = None
        end_time: Optional[str] = None

        for _, row in df.iterrows():
            perf = row.get("Performance", None)
            if is_blank(perf):
                continue

            key = str(perf).strip()

            all_v = row.get("All trades", None)
            long_v = row.get("Long trades", None)
            short_v = row.get("Short trades", None)

            # Metadata rows
            if key.lower() == "start date" and not is_blank(all_v):
                start_date = str(all_v).strip()
                continue
            if key.lower() == "start time" and not is_blank(all_v):
                start_time = str(all_v).strip()
                continue
            if key.lower() == "end date" and not is_blank(all_v):
                end_date = str(all_v).strip()
                continue
            if key.lower() == "end time" and not is_blank(all_v):
                end_time = str(all_v).strip()
                continue

            # Normal KPI rows
            if not is_blank(all_v):
                kpis_all_raw[key] = parse_value(all_v)
            if not is_blank(long_v):
                kpis_long_raw[key] = parse_value(long_v)
            if not is_blank(short_v):
                kpis_short_raw[key] = parse_value(short_v)

        def combine_dt(d: Optional[str], t: Optional[str]) -> Optional[datetime]:
            if not d or not t:
                return None
            # Matches your file: "11/24/2025" and "12:00 AM"
            try:
                naive = datetime.strptime(f"{d} {t}", "%m/%d/%Y %I:%M %p")
            except ValueError:
                # Fallbacks if NT exports differ slightly
                for fmt in ("%m/%d/%Y %H:%M", "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %I:%M:%S %p"):
                    try:
                        naive = datetime.strptime(f"{d} {t}", fmt)
                        break
                    except ValueError:
                        naive = None
                if naive is None:
                    warnings.append({
                        "code": "BAD_START_END_DATETIME",
                        "message": f"Could not parse date/time pair: {d!r} {t!r} in {path.name}",
                    })
                    return None
            return naive.replace(tzinfo=self._TZ)

        start_dt = combine_dt(start_date, start_time)
        end_dt = combine_dt(end_date, end_time)

        summary = {
            "kpis_all": NormalizedKpiDict(kpis_all_raw),
            "kpis_long": NormalizedKpiDict(kpis_long_raw),
            "kpis_short": NormalizedKpiDict(kpis_short_raw),
            "start_dt": start_dt,
            "end_dt": end_dt,
        }

        return ParsedArtifact(
            kind="summary",
            run_id=run_id,
            source_path=path,
            summary=summary,
            warnings=warnings,
        )
