# src/ta_foundation/parsers/ninjatrader/trades_csv.py
from __future__ import annotations

from pathlib import Path
import pandas as pd

from ta_foundation.parsers.base import ParsedArtifact, Parser
from ta_foundation.utils.parsing import parse_money, parse_int, parse_local_dt, parse_float


class NinjaTraderTradesCsvParser:
    kind = "trades"

    def can_parse(self, path: Path, header: str) -> bool:
        name_ok = path.name.endswith("_Trades.csv") or path.name == "Trades.csv"
        # lightweight signature check
        sig_ok = ("Trade number" in header and "Entry time" in header and "Exit time" in header)
        return name_ok and sig_ok

    def parse(self, path: Path, run_id: str) -> ParsedArtifact:
        df = pd.read_csv(path)

        # Drop trailing unnamed columns
        df = df.loc[:, ~df.columns.str.startswith("Unnamed")]

        # Canonical rename map
        rename = {
            "Trade number": "trade_id",
            "Instrument": "instrument",
            "Account": "account",
            "Strategy": "strategy",
            "Market pos.": "market_pos",
            "Qty": "qty",
            "Entry price": "entry_price",
            "Exit price": "exit_price",
            "Entry time": "entry_time",
            "Exit time": "exit_time",
            "Entry name": "entry_name",
            "Exit name": "exit_name",
            "Profit": "profit",
            "Cum. net profit": "cum_net_profit",
            "Commission": "commission",
            "Clearing fee": "clearing_fee",
            "Exchange fee": "exchange_fee",
            "IP fee": "ip_fee",
            "NFA fee": "nfa_fee",
            "MAE": "mae",
            "MFE": "mfe",
            "ETD": "etd",
            "Bars": "bars",
        }
        df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})

        warnings: list[dict] = []

        # Types / conversions
        if "trade_id" in df.columns:
            df["trade_id"] = df["trade_id"].map(parse_int)

        if "qty" in df.columns:
            df["qty"] = df["qty"].map(parse_int)

        for c in ["entry_price", "exit_price"]:
            if c in df.columns:
                df[c] = df[c].map(parse_float)

        money_cols = [
            "profit", "cum_net_profit", "commission",
            "clearing_fee", "exchange_fee", "ip_fee", "nfa_fee",
            "mae", "mfe", "etd",
        ]
        for c in money_cols:
            if c in df.columns:
                df[c] = df[c].map(parse_money)

        if "bars" in df.columns:
            df["bars"] = df["bars"].map(parse_int)

        # Datetimes → America/Denver-aware
        for c in ["entry_time", "exit_time"]:
            if c in df.columns:
                def _safe_parse(v):
                    if pd.isna(v) or str(v).strip() == "":
                        return pd.NaT
                    try:
                        return parse_local_dt(v)
                    except ValueError:
                        # fallback: pandas parse then localize
                        dt = pd.to_datetime(v, errors="coerce")
                        if pd.isna(dt):
                            return pd.NaT
                        # dt is Timestamp (naive); attach tz
                        return dt.to_pydatetime().replace(tzinfo=parse_local_dt("2026-01-01 00:00").tzinfo)

                df[c] = df[c].map(_safe_parse)
                df[c] = pd.to_datetime(df[c], utc=False)  # keeps tz-aware datetimes

        # Basic validation
        if "entry_time" in df.columns and "exit_time" in df.columns:
            bad = df[(df["entry_time"].notna()) & (df["exit_time"].notna()) & (df["exit_time"] < df["entry_time"])]
            if len(bad) > 0:
                warnings.append({
                    "code": "EXIT_BEFORE_ENTRY",
                    "message": f"{len(bad)} trades have exit_time < entry_time in {path.name}",
                })

        # Ensure run_id column exists for later comparisons when combining
        df["run_id"] = run_id

        return ParsedArtifact(
            kind="trades",
            run_id=run_id,
            source_path=path,
            df=df,
            warnings=warnings,
        )
