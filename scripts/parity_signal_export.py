#!/usr/bin/env python
"""
CLI for the C#↔Python entry-signal parity harness.

Export Python discovery signal bars for a structure, and (optionally) diff them
against a NinjaTrader Output-window dump containing [SDF-SIGNAL] lines printed
by StrategyDiscoveryFilter (EnableDebugPrint = true).

Examples
--------
Export 5-minute engulfing signals to CSV:

    python scripts/parity_signal_export.py export \
        --bars D:/MarketData/NQ_1m.parquet \
        --structure engulfing_bullish \
        --timeframe 5 \
        --out nq_engulf_5m.csv

Diff against an NT log:

    python scripts/parity_signal_export.py diff \
        --bars D:/MarketData/NQ_1m.parquet \
        --structure engulfing_bullish --timeframe 5 \
        --nt-log nt_output.txt
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

from ta_foundation.analysis.strategy_discovery.parity_harness import (
    diff_signals,
    export_signal_bars,
    parse_nt_signal_log,
)


def _load_bars(path: str) -> pd.DataFrame:
    p = Path(path)
    if not p.exists():
        sys.exit(f"bars file not found: {p}")
    if p.suffix.lower() == ".parquet":
        df = pd.read_parquet(p)
    else:
        df = pd.read_csv(p, parse_dates=["dt"])
    if "dt" not in df.columns:
        sys.exit("bars file must have a 'dt' column")
    df["dt"] = pd.to_datetime(df["dt"])
    return df


def _parse_params(pairs: list[str]) -> dict:
    out: dict = {}
    for pair in pairs or []:
        if "=" not in pair:
            sys.exit(f"--param expects key=value, got: {pair!r}")
        k, v = pair.split("=", 1)
        try:
            out[k] = float(v) if any(c in v for c in ".eE") else int(v)
        except ValueError:
            out[k] = v
    return out


def _build(args) -> pd.DataFrame:
    bars = _load_bars(args.bars)
    return export_signal_bars(
        bars,
        structure=args.structure,
        params=_parse_params(args.param),
        timeframe_minutes=args.timeframe,
        tick_size=args.tick_size,
        warmup_bars=args.warmup_bars,
        account_tz=args.account_tz,
    )


def main() -> None:
    ap = argparse.ArgumentParser(
        description="C#/Python entry-signal parity harness: export Python "
                    "discovery signal bars and diff them against a NinjaTrader "
                    "[SDF-SIGNAL] log.",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add_common(p):
        p.add_argument("--bars", required=True, help="OHLCV parquet/CSV with a dt column")
        p.add_argument("--structure", required=True, help="candle PATTERN_REGISTRY key")
        p.add_argument("--timeframe", type=int, default=1, help="bar period in minutes")
        p.add_argument("--tick-size", type=float, default=0.25)
        p.add_argument("--warmup-bars", type=int, default=50, help="match BarsRequiredToTrade")
        p.add_argument("--account-tz", default="America/Denver")
        p.add_argument("--param", action="append", default=[], help="detector param key=value")

    pe = sub.add_parser("export", help="write Python signal bars to CSV / stdout")
    add_common(pe)
    pe.add_argument("--out", help="CSV output path (default: stdout)")

    pd_ = sub.add_parser("diff", help="diff Python signals against an NT log")
    add_common(pd_)
    pd_.add_argument("--nt-log", required=True, help="text dump containing [SDF-SIGNAL] lines")

    args = ap.parse_args()
    df = _build(args)

    if args.cmd == "export":
        if args.out:
            df.to_csv(args.out, index=False)
            print(f"wrote {len(df)} signal rows to {args.out}")
        else:
            print(df.to_string(index=False))
        return

    # diff
    nt_text = Path(args.nt_log).read_text(encoding="utf-8", errors="ignore")
    nt_df = parse_nt_signal_log(nt_text)
    summary = diff_signals(df, nt_df)
    print(f"python signals : {summary['py_count']}")
    print(f"NT signals     : {summary['nt_count']}")
    print(f"matched        : {summary['matched']}")
    print(f"match rate     : {summary['match_rate']:.3f}")
    print(f"CLEAN          : {summary['clean']}")
    if summary["missing_in_nt"]:
        print(f"\nMISSING IN NT (python fired, NT did not) — {len(summary['missing_in_nt'])}:")
        for t in summary["missing_in_nt"][:50]:
            print(f"  {t}")
    if summary["extra_in_nt"]:
        print(f"\nEXTRA IN NT (NT fired, python did not) — {len(summary['extra_in_nt'])}:")
        for t in summary["extra_in_nt"][:50]:
            print(f"  {t}")
    sys.exit(0 if summary["clean"] else 1)


if __name__ == "__main__":
    main()
