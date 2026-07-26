from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pandas as pd

from ta_foundation.parsers.base import ParsedArtifact


@dataclass
class MinuteBarsLastTxtParser:
    """
    Parses NinjaTrader export minute bars:
      "NQ 03-26.Last.txt"

    Common observed variants:
      1) No header:
         yyyyMMdd HHmmss;open;high;low;close;volume
      2) With header line (skip it)
      3) Date/time separated by ';':
         yyyyMMdd;HHmmss;open;high;low;close;volume
      4) Time contains ':' (HH:MM:SS)

    Time in file is assumed UTC by default and converted to America/Denver.
    """
    kind: str = "market_minute_bars"
    source_tz: str = "UTC"
    target_tz: str = "America/Denver"

    def can_parse(self, path: Path, header: str) -> bool:
        stem = self._strip_suffix(path.name)
        if stem is None:
            return False
        parts = stem.split(" ")
        if len(parts) != 2:
            return False
        contract = parts[1].strip()
        return len(contract) == 5 and contract[2] == "-"

    @staticmethod
    def _strip_suffix(name: str) -> Optional[str]:
        """Accept the three NT-export naming patterns:
        * ``.Last.txt`` -- live streaming Indicator
        * ``.Full.txt`` -- Indicator BackfillOnce one-shot
        * ``.Export.txt`` -- TaFoundationDataExportStrategy Strategy Analyzer dump
        """
        for suffix in (".Last.txt", ".Full.txt", ".Export.txt"):
            if name.endswith(suffix):
                return name[:-len(suffix)]
        return None

    def parse(self, path: Path, run_id: Optional[str]) -> ParsedArtifact:
        stem = self._strip_suffix(path.name) or path.stem
        instrument, contract = stem.split(" ", 1)
        instrument = instrument.strip()
        contract = contract.strip()

        rows: list[tuple] = []
        warnings: list[dict] = []

        def _parse_dt(date_part: str, time_part: str) -> Optional[pd.Timestamp]:
            date_part = (date_part or "").strip()
            time_part = (time_part or "").strip().replace(":", "")
            # Expect yyyyMMdd + HHmmss
            dt_utc = pd.to_datetime(
                f"{date_part}{time_part}",
                format="%Y%m%d%H%M%S",
                utc=True,
                errors="coerce",
            )
            if pd.isna(dt_utc):
                return None
            return dt_utc.tz_convert(self.target_tz)

        # NinjaTrader's Strategy Analyzer exporter writes UTF-8 with a BOM.
        # ``utf-8-sig`` removes that marker when present and behaves like
        # regular UTF-8 for files produced without one.
        with path.open("r", encoding="utf-8-sig", errors="replace") as f:
            for i, line in enumerate(f, start=1):
                raw = line
                line = line.strip()
                if not line:
                    continue

                # Skip obvious header lines
                low = line.lower()
                if ("date" in low and "open" in low) or low.startswith("time") or low.startswith("datetime"):
                    continue

                # Variant A: "yyyyMMdd HHmmss;O;H;L;C;V"
                if " " in line and ";" in line:
                    try:
                        date_part, rest = line.split(" ", 1)
                        fields = rest.split(";")
                        if len(fields) < 6:
                            warnings.append({"line": i, "message": "Malformed row", "raw": raw.strip()})
                            continue
                        time_part = fields[0]
                        o, h, l, c, v = fields[1:6]
                        dt_local = _parse_dt(date_part, time_part)
                        if dt_local is None:
                            warnings.append({"line": i, "message": "Bad datetime parse", "raw": raw.strip()})
                            continue
                        rows.append((dt_local, float(o), float(h), float(l), float(c), int(float(v))))
                        continue
                    except Exception:
                        warnings.append({"line": i, "message": "Parse failure (space+semicolon)", "raw": raw.strip()})
                        continue

                # Variant B: "yyyyMMdd;HHmmss;O;H;L;C;V"
                if ";" in line:
                    parts = line.split(";")
                    if len(parts) >= 7:
                        try:
                            date_part = parts[0]
                            time_part = parts[1]
                            o, h, l, c, v = parts[2:7]
                            dt_local = _parse_dt(date_part, time_part)
                            if dt_local is None:
                                warnings.append({"line": i, "message": "Bad datetime parse", "raw": raw.strip()})
                                continue
                            rows.append((dt_local, float(o), float(h), float(l), float(c), int(float(v))))
                            continue
                        except Exception:
                            warnings.append({"line": i, "message": "Parse failure (semicolon)", "raw": raw.strip()})
                            continue

                warnings.append({"line": i, "message": "Unrecognized format", "raw": raw.strip()})

        df = pd.DataFrame(rows, columns=["dt", "open", "high", "low", "close", "volume"])
        if not df.empty:
            df = df.sort_values("dt").drop_duplicates(subset=["dt"], keep="last").reset_index(drop=True)

        return ParsedArtifact(
            kind=self.kind,
            run_id=None,  # shared/global
            source_path=path,
            df=df,
            summary={
                "instrument": instrument,
                "contract": contract,
                "source_tz": self.source_tz,
                "target_tz": self.target_tz,
                "n_rows": int(len(df)),
            },
            warnings=warnings,
        )
