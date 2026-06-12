"""CLI: run the stop-audit **trajectory** parity check (backtest leg of the automated
C#<->Python parity loop) and emit a pass/fail verdict.

Given a PantheonMaster ``StopAuditCsvPath`` dump + the matching NT ``Trades.csv`` (the
authoritative entry anchors) + the instrument minute bars, replay the Python bar-close
trail and diff it against NT's ACTUAL per-event stop trajectory. Exit 0 = parity PASS,
1 = FAIL — a CI-style gate you can run unattended after every backtest.

Usage:
  python scripts/stop_audit_parity.py --audit C:\\temp\\pm_audit.csv \\
      --trades "<...>/Trades.csv" --bars "D:\\MarketData\\NQ 06-26.Export.txt" \\
      [--atr-mode wilder|sma] [--policy AtrTrail] [--stop-tol-ticks 1.0] \\
      [--atr-period 14] [--atr-multiple 2.0] [--stop-ticks 60] [--tick-size 0.25]

--trades is optional: without it the replay anchors on each trade's first audit event
(exact for the live INIT row, approximate for the INIT-less bar-close path).
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import pandas as pd

from ta_foundation.analysis.exits.nt_atr_trail_parity import NtAtrTrailConfig
from ta_foundation.analysis.exits.stop_audit_parity import parity_report
from ta_foundation.parsers.ninjatrader.minute_bars_last_txt import MinuteBarsLastTxtParser


def _num(x) -> float | None:
    try:
        return float(str(x).replace(",", "").replace("$", "").replace("(", "-").replace(")", "").strip())
    except (TypeError, ValueError):
        return None


def load_minute_bars(bars_file: str) -> pd.DataFrame:
    """Instrument minute bars as naive-Denver dt (matches the NT-local audit/trade times)."""
    art = MinuteBarsLastTxtParser().parse(Path(bars_file), run_id=None)
    bars = art.df.copy()
    bars["dt"] = pd.to_datetime(bars["dt"]).dt.tz_localize(None)
    return bars.sort_values("dt").reset_index(drop=True)


def _parse_nt_dt(raw: str | None):
    s = (raw or "").strip()
    if not s:
        return None
    dt = pd.to_datetime(s, format="%m/%d/%Y %I:%M:%S %p", errors="coerce")
    if pd.isna(dt):
        dt = pd.to_datetime(s, errors="coerce")
    return None if pd.isna(dt) else dt.to_pydatetime()


def load_nt_trades_entries(trades_csv: str) -> pd.DataFrame:
    """Per-trade anchors from an NT Trades.csv, time-sorted: entry_dt/entry_price/
    direction plus exit_dt/exit_name (the exit name tells the comparator whether NT
    closed via the trail stop or some other layer — daily-risk flatten, session
    close, opposite signal — which bounds the trajectory diff)."""
    rows: list[dict] = []
    with Path(trades_csv).open("r", encoding="utf-8-sig", newline="") as fh:
        for r in csv.DictReader(fh):
            pos = (r.get("Market pos.") or "").strip().lower()
            dt = _parse_nt_dt(r.get("Entry time"))
            if dt is None or pos not in {"long", "short"}:
                continue
            rows.append({
                "entry_dt": dt,
                "entry_price": _num(r.get("Entry price")),
                "direction": 1 if pos == "long" else -1,
                "exit_dt": _parse_nt_dt(r.get("Exit time")),
                "exit_name": (r.get("Exit name") or "").strip(),
            })
    return pd.DataFrame(rows).sort_values("entry_dt").reset_index(drop=True)


def print_report(report: dict) -> None:
    if report.get("reason") == "no_trades_in_audit":
        print("  NO trades found in the audit for the requested policy — nothing to grade.")
        return
    verdict = "PASS" if report["passed"] else "FAIL"
    th = report["thresholds"]
    print(f"\n  VERDICT: {verdict}  (atr_mode={report['atr_mode']})")
    print(f"  trades={report['n_trades']}  events={report['total_events']}  "
          f"overall_stop_match={report['overall_stop_match_rate']*100:.1f}%  "
          f"worst_trade_median_diff={report['worst_trade_median_diff_ticks']:.2f} ticks")
    print(f"  thresholds: stop_match>={th['min_stop_match_rate']*100:.0f}%  "
          f"median_diff<={th['max_median_diff_ticks']} ticks  (tol {th['stop_tol_ticks']} ticks)")
    cols = ["trade_id", "direction", "entry_dt", "n_events", "stop_match_rate",
            "median_diff_ticks", "max_diff_ticks", "first_divergence_dt", "atr_match_rate"]
    s = report["summary"]
    print("\n  per-trade:")
    with pd.option_context("display.max_rows", 50, "display.width", 200):
        print(s[[c for c in cols if c in s.columns]].to_string(index=False))


def build_cfg(args) -> NtAtrTrailConfig:
    return NtAtrTrailConfig(
        atr_period=args.atr_period, atr_multiple=args.atr_multiple,
        stop_ticks=args.stop_ticks, tick_size=args.tick_size, atr_mode=args.atr_mode,
    )


def load_ticks(ticks_file: str) -> pd.DataFrame:
    """Tick series as naive-Denver dt + last price. Prefers the sibling parquet
    cache (``<file>.parquet``) when present — the raw NQ tick file is ~1.3GB."""
    from ta_foundation.marketdata.tick_cache import TickCacheConfig, load_tick_cache_parquet
    from ta_foundation.parsers.ninjatrader.tick_last_txt import TickLastTxtParser

    path = Path(ticks_file)
    df = None
    pq = path if path.suffix == ".parquet" else path.with_name(path.name + ".parquet")
    if pq.is_file():
        df = load_tick_cache_parquet(pq, cfg=TickCacheConfig())
    if df is None:
        df = TickLastTxtParser().parse(path, run_id=None).df
    df = df.copy()
    df["dt"] = pd.to_datetime(df["dt"])
    try:
        df["dt"] = df["dt"].dt.tz_localize(None)
    except TypeError:
        pass  # already naive
    return df.sort_values("dt").reset_index(drop=True)


def make_tick_replay(ticks: pd.DataFrame, bars_with_atr: pd.DataFrame, cfg: NtAtrTrailConfig):
    """Build the live-leg replay callable for ``parity_report(replay=...)``: per
    trade, slice the Last-tick series from entry over the hold window, asof-join
    the bar-close ATR (NT updates currentAtr at bar close in BOTH paths), and run
    the tick trajectory replica."""
    from ta_foundation.analysis.exits.nt_atr_trail_parity import (
        replicate_nt_atr_trail_tick_trajectory,
    )
    t = ticks.sort_values("dt").reset_index(drop=True)
    tdt = pd.to_datetime(t["dt"]).astype("datetime64[ns]")
    atr_frame = (bars_with_atr[["dt", "atr"]].copy()
                 .assign(dt=lambda d: pd.to_datetime(d["dt"]).astype("datetime64[ns]"))
                 .sort_values("dt").reset_index(drop=True))

    def replay(entry_dt, entry_price: float, direction: int) -> dict:
        start = pd.to_datetime(entry_dt)
        end = start + pd.Timedelta(minutes=cfg.max_hold_minutes)
        sel = t[(tdt >= start) & (tdt <= end)]
        sel_dt = pd.to_datetime(sel["dt"]).astype("datetime64[ns]").to_frame(name="dt")
        atr = pd.merge_asof(sel_dt, atr_frame, on="dt", direction="backward")["atr"].to_numpy()
        return replicate_nt_atr_trail_tick_trajectory(
            entry_price=float(entry_price), direction=int(direction),
            tick_prices=sel["last"].to_numpy(dtype=float),
            tick_dts=sel["dt"].to_numpy(), tick_atr=atr, cfg=cfg,
        )

    return replay


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Stop-audit trajectory parity (backtest or live leg).")
    p.add_argument("--audit", required=True, help="StopAuditCsvPath dump from the NT run")
    p.add_argument("--bars", required=True, help="instrument minute bars (.Last/.Export/.Full .txt)")
    p.add_argument("--trades", default=None, help="NT Trades.csv for entry anchors (recommended)")
    p.add_argument("--ticks", default=None,
                   help="instrument tick file (.txt or .parquet cache) — switches to the LIVE-leg "
                        "tick replica (Playback/live audit vs ManageLiveDynamicStop model)")
    p.add_argument("--atr-mode", default="wilder", choices=["wilder", "sma"])
    p.add_argument("--policy", default="AtrTrail")
    p.add_argument("--stop-tol-ticks", type=float, default=1.0)
    p.add_argument("--atr-period", type=int, default=14)
    p.add_argument("--atr-multiple", type=float, default=2.0)
    p.add_argument("--stop-ticks", type=int, default=60)
    p.add_argument("--tick-size", type=float, default=0.25)
    p.add_argument("--min-stop-match-rate", type=float, default=0.95)
    p.add_argument("--max-median-diff-ticks", type=float, default=1.0)
    args = p.parse_args(argv)

    cfg = build_cfg(args)
    bars = load_minute_bars(args.bars)
    entries = load_nt_trades_entries(args.trades) if args.trades else None

    replay = None
    if args.ticks:
        from ta_foundation.analysis.exits.nt_atr_trail_parity import compute_atr
        ticks = load_ticks(args.ticks)
        work = bars.copy()
        work["atr"] = compute_atr(work, cfg.atr_period, cfg.atr_mode).values
        replay = make_tick_replay(ticks, work, cfg)

    leg = "live-tick leg" if replay else "backtest leg"
    print(f"=== Stop-Audit Trajectory Parity ({args.policy}, {leg}) ===")
    print(f"  audit: {args.audit}")
    print(f"  bars : {bars['dt'].min()}..{bars['dt'].max()} ({len(bars)})"
          + (f"   entries: {len(entries)}" if entries is not None else "   entries: from audit"))

    report = parity_report(
        args.audit, bars, cfg, entries=entries, policy=args.policy,
        stop_tol_ticks=args.stop_tol_ticks,
        min_stop_match_rate=args.min_stop_match_rate,
        max_median_diff_ticks=args.max_median_diff_ticks,
        replay=replay,
    )
    print_report(report)
    return 0 if report.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
