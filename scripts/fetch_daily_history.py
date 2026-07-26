"""Fetch multi-year DAILY futures OHLCV from Yahoo Finance (free, no key).

Lever-1 data source after confirming NT's intraday horizon is only ~6 months.
Daily moves are large, so transaction cost is negligible relative to the move
-- the right frequency to test the only viable mechanical direction
(lower-frequency / bigger-move). Saves one CSV per instrument to
D:\\MarketData\\daily\\<INST>_daily.csv with columns dt,open,high,low,close,volume
(dt tz-aware America/Denver, to match the rest of the pipeline).

    python scripts/fetch_daily_history.py
"""
from __future__ import annotations

import json
import urllib.request
from pathlib import Path

import pandas as pd

OUT_DIR = Path(r"D:\MarketData\daily")
# Continuous back-adjusted futures on Yahoo. Diversified cross-asset panel --
# time-series momentum (TSMOM) is an ALL-asset phenomenon, so the rigorous test
# pools equities + commodities + rates + FX, not just the equity indices.
SYMBOLS = {
    # Equity indices
    "NQ": "NQ=F", "ES": "ES=F", "YM": "YM=F", "RTY": "RTY=F",
    # Commodities
    "GC": "GC=F", "SI": "SI=F", "HG": "HG=F", "CL": "CL=F", "NG": "NG=F",
    "ZC": "ZC=F", "ZS": "ZS=F", "ZW": "ZW=F",
    # Rates
    "ZB": "ZB=F", "ZN": "ZN=F", "ZF": "ZF=F",
    # FX
    "6E": "6E=F", "6J": "6J=F", "6B": "6B=F", "6A": "6A=F",
}
START = 946684800   # 2000-01-01 UTC


def fetch_one(yahoo_sym: str) -> pd.DataFrame:
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_sym}"
           f"?period1={START}&period2=9999999999&interval=1d")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    raw = urllib.request.urlopen(req, timeout=30).read()
    r = json.loads(raw)["chart"]["result"][0]
    ts = r["timestamp"]
    q = r["indicators"]["quote"][0]
    df = pd.DataFrame({
        "dt": pd.to_datetime(ts, unit="s", utc=True).tz_convert("America/Denver"),
        "open": q["open"], "high": q["high"], "low": q["low"],
        "close": q["close"], "volume": q["volume"],
    })
    df = df.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)
    # Normalize timestamp to the session date at midnight Denver (daily bars).
    df["dt"] = df["dt"].dt.normalize()
    return df.drop_duplicates(subset=["dt"], keep="last").reset_index(drop=True)


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for inst, ysym in SYMBOLS.items():
        try:
            df = fetch_one(ysym)
        except Exception as exc:
            print(f"{inst} ({ysym}): ERR {type(exc).__name__}: {exc}")
            continue
        path = OUT_DIR / f"{inst}_daily.csv"
        df.to_csv(path, index=False)
        print(f"{inst} ({ysym}): {len(df):,} daily bars  "
              f"{df['dt'].min().date()} -> {df['dt'].max().date()}  -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
