from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pandas as pd

from ta_foundation.parsers.base import ParsedArtifact, Parser


@dataclass
class MinuteBarsLastTxtParser:
    """
    Parses NinjaTrader export minute bars:
      "NQ 03-26.Last.txt"

    No header. Each row:
      yyyyMMdd HHmmss;open;high;low;close;volume

    Time in file is assumed UTC by default and converted to America/Denver.
    """
    kind: str = "market_minute_bars"
    source_tz: str = "UTC"
    target_tz: str = "America/Denver"

    def can_parse(self, path: Path, header: str) -> bool:
        if not path.name.endswith(".Last.txt"):
            return False
        stem = path.name[:-len(".Last.txt")]
        parts = stem.split(" ")
        if len(parts) != 2:
            return False
        contract = parts[1]
        return len(contract) == 5 and contract[2] == "-"

    def parse(self, path: Path, run_id: Optional[str]) -> ParsedArtifact:
        stem = path.name[:-len(".Last.txt")]
        instrument, contract = stem.split(" ", 1)
        instrument = instrument.strip()
        contract = contract.strip()

        rows: list[tuple] = []
        warnings: list[dict] = []

        with path.open("r", encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    date_part, rest = line.split(" ", 1)
                    time_part, o, h, l, c, v = rest.split(";")
                except ValueError:
                    warnings.append({"line": i, "message": "Malformed row", "raw": line})
                    continue

                dt_utc = pd.to_datetime(
                    f"{date_part}{time_part}",
                    format="%Y%m%d%H%M%S",
                    utc=True,
                    errors="coerce",
                )
                if pd.isna(dt_utc):
                    warnings.append({"line": i, "message": "Bad datetime parse", "raw": line})
                    continue

                dt_local = dt_utc.tz_convert(self.target_tz)

                try:
                    rows.append((dt_local, float(o), float(h), float(l), float(c), int(v)))
                except Exception:
                    warnings.append({"line": i, "message": "Bad numeric parse", "raw": line})
                    continue

        df = pd.DataFrame(rows, columns=["dt", "open", "high", "low", "close", "volume"])
        if not df.empty:
            df = df.sort_values("dt").drop_duplicates(subset=["dt"], keep="last").reset_index(drop=True)

        # Shared/global artifact: run_id=None
        return ParsedArtifact(
            kind=self.kind,
            run_id=None,
            source_path=path,
            df=df,
            summary={"instrument": instrument, "contract": contract, "source_tz": self.source_tz, "target_tz": self.target_tz},
            warnings=warnings,
        )
