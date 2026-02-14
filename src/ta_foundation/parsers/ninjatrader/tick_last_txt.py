from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pandas as pd

from ta_foundation.parsers.base import ParsedArtifact, Parser


@dataclass
class TickLastTxtParser:
    """
    Parses NinjaTrader tick "Last" exports:

      "NQ 03-26 Tick.Last.txt"

    No header. Each row:
      yyyyMMdd HHmmss fffffff;last;bid;ask;volume

    Notes:
    - NinjaTrader often uses 7 digits for fractional seconds. Pandas %f is 1-6 digits.
      We normalize:
        - if > 6 digits => truncate to 6 (floor microseconds) and emit warning
        - if < 6 digits => right-pad with zeros to 6
    - File time is assumed UTC by default and converted to America/Denver.
    """
    kind: str = "market_ticks"
    source_tz: str = "UTC"
    target_tz: str = "America/Denver"

    def can_parse(self, path: Path, header: str) -> bool:
        if not path.name.endswith("Tick.Last.txt"):
            return False

        # Expect: "<INSTR> <MM-YY> Tick.Last.txt"
        # Example: "NQ 03-26 Tick.Last.txt"
        stem = path.name[:-len("Tick.Last.txt")].strip()  # "NQ 03-26"
        parts = stem.split(" ")
        if len(parts) != 2:
            return False
        instrument, contract = parts[0].strip(), parts[1].strip()
        return bool(instrument) and (len(contract) == 5 and contract[2] == "-")

    def parse(self, path: Path, run_id: Optional[str]) -> ParsedArtifact:
        stem = path.name[:-len("Tick.Last.txt")].strip()  # "NQ 03-26"
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
                    ts_raw, last_s, bid_s, ask_s, vol_s = line.split(";")
                except ValueError:
                    warnings.append({"line": i, "message": "Malformed row (field split)", "raw": line})
                    continue

                ts_parts = ts_raw.strip().split()
                if len(ts_parts) != 3:
                    warnings.append({"line": i, "message": "Malformed timestamp token", "raw": line})
                    continue

                date_part, time_part, frac_part = ts_parts[0], ts_parts[1], ts_parts[2]

                frac = "".join(ch for ch in frac_part if ch.isdigit()) or "0"
                if len(frac) > 6:
                    warnings.append(
                        {"line": i, "message": f"Fractional seconds > 6 digits; truncating ({len(frac)} -> 6)", "raw": line}
                    )
                    frac = frac[:6]
                else:
                    frac = frac.ljust(6, "0")

                dt_naive = pd.to_datetime(
                    f"{date_part}{time_part}{frac}",
                    format="%Y%m%d%H%M%S%f",
                    errors="coerce",
                )
                if pd.isna(dt_naive):
                    warnings.append({"line": i, "message": "Bad datetime parse", "raw": line})
                    continue

                try:
                    dt_src = dt_naive.tz_localize(self.source_tz)
                    dt_local = dt_src.tz_convert(self.target_tz)
                except Exception:
                    warnings.append({"line": i, "message": "Timezone localization/convert failed", "raw": line})
                    continue

                try:
                    rows.append((dt_local, float(last_s), float(bid_s), float(ask_s), int(vol_s)))
                except Exception:
                    warnings.append({"line": i, "message": "Bad numeric parse", "raw": line})
                    continue

        df = pd.DataFrame(rows, columns=["dt", "last", "bid", "ask", "volume"])
        if not df.empty:
            df = df.sort_values("dt").drop_duplicates(subset=["dt", "last", "bid", "ask", "volume"]).reset_index(drop=True)

        return ParsedArtifact(
            kind=self.kind,
            run_id=None,  # shared/global artifact
            source_path=path,
            df=df,
            summary={"instrument": instrument, "contract": contract, "source_tz": self.source_tz, "target_tz": self.target_tz},
            warnings=warnings,
        )
