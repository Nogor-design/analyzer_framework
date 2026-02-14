from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import pandas as pd

from ta_foundation.parsers.base import ParsedArtifact
from ta_foundation.utils.parsing import parse_money, parse_percent, parse_int, parse_float  # parity / available helpers


class NinjaTraderSettingsCsvParser:
    """
    Parses NinjaTrader Strategy Analyzer *_Settings.csv export.

    Typical structure:
      Item,Value
      General,
      Account Size,$50,000
      Risk %,1.5%
      Stops,
      Stop Loss,12
      Take Profit,24

    Produces:
      ParsedArtifact(kind="settings", settings=<DataFrame with columns:
        ["section","item","value"]> )
    """
    kind = "settings"

    def can_parse(self, path: Path, header: str) -> bool:
        name_ok = path.name.endswith("_Settings.csv")
        sig_ok = ("Item" in header and "Value" in header)
        return name_ok and sig_ok

    def parse(self, path: Path, run_id: str) -> ParsedArtifact:
        df = pd.read_csv(path)

        # Drop trailing unnamed columns (common in NT exports)
        df = df.loc[:, ~df.columns.str.startswith("Unnamed")]

        warnings: list[dict[str, Any]] = []

        # Normalize expected columns
        # Some NT exports use exact "Item"/"Value" casing; keep tolerant fallback
        col_item = None
        col_value = None
        for c in df.columns:
            cl = str(c).strip().lower()
            if cl == "item":
                col_item = c
            elif cl == "value":
                col_value = c

        if col_item is None or col_value is None:
            # Fallback: first two columns if headers are slightly different
            cols = list(df.columns)
            if len(cols) < 2:
                warnings.append({
                    "code": "MISSING_COLUMNS",
                    "message": f"Settings CSV missing required columns in {path.name}",
                })
                settings_df = pd.DataFrame(columns=["section", "item", "value"])
                return ParsedArtifact(
                    kind="settings",
                    run_id=run_id,
                    source_path=path,
                    settings=settings_df,
                    warnings=warnings,
                )
            col_item, col_value = cols[0], cols[1]
            warnings.append({
                "code": "NONSTANDARD_COLUMNS",
                "message": f"Nonstandard settings headers in {path.name}; using {col_item!r}, {col_value!r}",
            })

        def is_blank(x: Any) -> bool:
            if x is None:
                return True
            if isinstance(x, float) and pd.isna(x):
                return True
            s = str(x).strip()
            return s == "" or s.lower() in {"nan", "none", "null"}

        # Settings values are best treated as strings for display,
        # but we can do best-effort typing (optional) similar to summary_csv.py.
        def parse_value(v: Any) -> Any:
            if is_blank(v):
                return ""

            s = str(v).strip()

            # Money cues
            if "$" in s or (s.startswith("(") and s.endswith(")")):
                m = parse_money(s)
                return m if m is not None else s

            # Percent cues
            if s.endswith("%"):
                p = parse_percent(s)
                return p if p is not None else s

            # Float first
            f = parse_float(s)
            if f is not None:
                if abs(f - int(f)) < 1e-12:
                    return int(f)
                return f

            return s

        current_section: Optional[str] = None
        rows: list[dict[str, Any]] = []

        for _, row in df.iterrows():
            item = row.get(col_item, None)
            value = row.get(col_value, None)

            if is_blank(item):
                continue

            item_s = str(item).strip()

            # Section header row convention: item present, value blank
            if is_blank(value):
                current_section = item_s
                continue

            rows.append({
                "section": current_section or "General",
                "item": item_s,
                "value": parse_value(value),
            })

        settings_df = pd.DataFrame(rows, columns=["section", "item", "value"])
        if not settings_df.empty:
            settings_df = settings_df.sort_values(["section", "item"], kind="stable").reset_index(drop=True)

        return ParsedArtifact(
            kind="settings",
            run_id=run_id,
            source_path=path,
            df=settings_df,  # ✅ use the generic dataframe payload field
            warnings=warnings,
        )
