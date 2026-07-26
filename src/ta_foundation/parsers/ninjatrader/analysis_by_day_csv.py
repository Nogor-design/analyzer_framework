from __future__ import annotations

from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from ta_foundation.parsers.base import ParsedArtifact
from ta_foundation.utils.parsing import parse_money, parse_percent, parse_int, parse_float


class NinjaTraderDailyAnalysisCsvParser:
    """
    Parses NinjaTrader Strategy Analyzer "Analysis" export that is grouped by day (Period).
    Example filename: bot1_Analysis.csv
    """
    kind = "daily"

    _TZ = ZoneInfo("America/Denver")

    def can_parse(self, path: Path, header: str) -> bool:
        name_ok = path.name.endswith("_Analysis.csv") or path.name == "Analysis.csv"
        sig_ok = ("Period" in header and "Cum. net profit" in header and "% Trade" in header)
        return name_ok and sig_ok

    def parse(self, path: Path, run_id: str) -> ParsedArtifact:
        df = pd.read_csv(path)

        # Drop trailing unnamed columns
        df = df.loc[:, ~df.columns.str.startswith("Unnamed")]

        # Canonical rename map
        rename = {
            "Period": "date",
            "#": "trade_count",
            "Cum. net profit": "cum_net_profit",
            "Net profit": "net_profit",
            "Gross profit": "gross_profit",
            "Gross loss": "gross_loss",
            "Commission": "commission",
            "Cum. max. drawdown": "cum_max_drawdown",
            "Max. drawdown": "max_drawdown",
            "% Win": "win_rate",
            "Avg. trade": "avg_trade",
            "Avg. winner": "avg_winner",
            "Avg. loser": "avg_loser",
            "Lrg. winner": "lrg_winner",
            "Lrg. loser": "lrg_loser",
            "MTR": "mtr",
            "Avg. MAE": "avg_mae",
            "Avg. MFE": "avg_mfe",
            "Avg. ETD": "avg_etd",
            "% Trade": "pct_trade",
        }
        df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})

        warnings: list[dict] = []

        # Parse Period -> tz-aware midnight America/Denver
        if "date" in df.columns:
            # NinjaTrader Period typically appears as M/D/YYYY; pandas handles it well.
            d = pd.to_datetime(df["date"], errors="coerce")

            bad_dates = d.isna().sum()
            if bad_dates:
                warnings.append({
                    "code": "BAD_PERIOD_DATE",
                    "message": f"{bad_dates} rows have unparseable Period values in {path.name}",
                })

            # Normalize to date-only (midnight) then localize
            d = d.dt.normalize()

            # Localize naive timestamps to America/Denver
            # (If pandas ever returns tz-aware here, we preserve it.)
            if getattr(d.dt, "tz", None) is None:
                d = d.dt.tz_localize(self._TZ)
            else:
                d = d.dt.tz_convert(self._TZ)

            df["date"] = d

        # Counts / ints
        if "trade_count" in df.columns:
            df["trade_count"] = df["trade_count"].map(parse_int)

        # Money columns
        money_cols = [
            "cum_net_profit", "net_profit", "gross_profit", "gross_loss", "commission",
            "cum_max_drawdown", "max_drawdown",
            "avg_trade", "avg_winner", "avg_loser",
            "lrg_winner", "lrg_loser",
            "avg_mae", "avg_mfe", "avg_etd",
        ]
        for c in money_cols:
            if c in df.columns:
                df[c] = df[c].map(parse_money)

        # Percent columns (stored as fractions: 71.05% -> 0.7105)
        for c in ["win_rate", "pct_trade"]:
            if c in df.columns:
                df[c] = df[c].map(parse_percent)

        # MTR is typically numeric (not currency)
        if "mtr" in df.columns:
            df["mtr"] = df["mtr"].map(parse_float)

        # Ensure run_id for easy comparisons later
        df["run_id"] = run_id

        return ParsedArtifact(
            kind="daily",
            run_id=run_id,
            source_path=path,
            df=df,
            warnings=warnings,
        )
