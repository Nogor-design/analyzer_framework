"""
Option A — exit-logic discovery on a FIXED entry set (pure Python, NO NinjaTrader).

Holds entries fixed and varies the EXIT policy, then ranks exit policies by
PF / net / drawdown / trades / MFE-capture to recommend the best stop logic.

Entries
-------
The PantheonBotV2 entry signal is a plain SMA fast/slow cross on the primary
bar series (PantheonBotV2.cs:407-409, 519/556, Calculate.OnBarClose,
EntriesPerDirection=1). We reproduce that here on real NQ 06-26 1-minute bars
from D:\\MarketData. To isolate the EXIT variable we deliberately run the
*bare* cross (UseTrend=false, no regime filter) so the entry set is a clean,
reproducible constant across all exit policies. Both long and short enabled,
one position at a time (flat-to-flat), entry filled at the next bar's open.

Exits
-----
Each exit policy is applied to that single fixed trade set via the existing
tick-path simulator (analysis/exits/simulate.py) on the 16.8M-tick NQ 06-26
tick file. We run UNBOUNDED (bounded_to_original_exit=False) with a 180-minute
max hold so every policy is free to exit on its own logic — a true exit
comparison rather than "exit no later than the seed trade".

Caveats
-------
* ATR in the simulator is Wilder (analysis/features/regime.atr_wilder), NOT the
  SMA-based ATR NinjaTrader uses. ATR-multiple-based policies will differ
  slightly from an NT run; tick-based policies (fixed_rr, giveback, trail) are
  parity-clean. Reported as a caveat, not corrected here.
* Tick data covers 2026-03-12 .. 2026-05-28. Entries after the last tick get no
  fill window and are dropped by the simulator.

Usage
-----
    python scripts/exit_discovery_optionA.py
Outputs CSV + JSON under outputs/exit_discovery_optionA/.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ta_foundation.marketdata.store import MarketDataStore  # noqa: E402
from ta_foundation.analysis.exits.simulate import (  # noqa: E402
    ExitSimConfig,
    simulate_exit_policies_for_run,
)
from ta_foundation.analysis.exits.policies import (  # noqa: E402
    FixedStopTargetPolicy,
    AtrTrailPolicy,
    BreakEvenAtrTrailPolicy,
    ChandelierAtrTrailPolicy,
    GivebackAfterMfePolicy,
    FixedAtrThenAtrTrailPolicy,
    FixedAtrThenChandelierPolicy,
    TimeStopNoProgressPolicy,
)
from ta_foundation.analysis.strategy_discovery.exit_discovery import (  # noqa: E402
    _aggregate_policy_metrics,
    _family_of,
)

MARKETDATA = Path(r"D:\MarketData")
INSTRUMENT = "NQ"
CONTRACT = "06-26"
TICK_SIZE = 0.25
TICK_VALUE = 5.0  # $ per tick, NQ
TARGET_TZ = "America/Denver"

# Entry signal params (PantheonBotV2 defaults, PantheonBotV2.cs:268-270)
FAST_PERIOD = 50
SLOW_PERIOD = 200
TREND_PERIOD = 300  # unused: UseTrend=false for a clean isolated entry set
MAX_HOLD_MIN = 180

OUT_DIR = REPO / "outputs" / "exit_discovery_optionA"


# ---------------------------------------------------------------------------
# Fast data loaders (UTC file time -> America/Denver, matching NT parsers)
# ---------------------------------------------------------------------------

def load_minute_bars(path: Path) -> pd.DataFrame:
    df = pd.read_csv(
        path,
        sep=";",
        header=None,
        names=["ts", "open", "high", "low", "close", "volume"],
        dtype={"open": float, "high": float, "low": float, "close": float, "volume": float},
    )
    dt = pd.to_datetime(df["ts"], format="%Y%m%d %H%M%S", utc=True)
    df["dt"] = dt.dt.tz_convert(TARGET_TZ)
    df = df.drop(columns=["ts"]).sort_values("dt").reset_index(drop=True)
    return df[["dt", "open", "high", "low", "close", "volume"]]


def load_ticks(path: Path) -> pd.DataFrame:
    """Parse ticks, with a one-time parquet cache (parse is the 1.34GB cost)."""
    cache = path.with_suffix(path.suffix + ".parquet")
    if cache.exists() and cache.stat().st_mtime >= path.stat().st_mtime:
        print(f"       [cache] {cache.name}", flush=True)
        return pd.read_parquet(cache)

    # Row: "yyyyMMdd HHmmss fffffff;last;bid;ask;volume"  (UTC)
    df = pd.read_csv(
        path,
        sep=";",
        header=None,
        names=["ts", "last", "bid", "ask", "volume"],
        dtype={"last": float, "bid": float, "ask": float, "volume": float},
    )
    # ts like "20260312 060018 6960000" -> split into date, time, 7-digit frac
    parts = df["ts"].str.split(" ", n=2, expand=True)
    frac = parts[2].str.slice(0, 6).str.ljust(6, "0")
    stamp = parts[0] + parts[1] + frac
    dt = pd.to_datetime(stamp, format="%Y%m%d%H%M%S%f", utc=True)
    df["dt"] = dt.dt.tz_convert(TARGET_TZ)
    df = df.drop(columns=["ts"]).sort_values("dt").reset_index(drop=True)
    df = df[["dt", "last", "bid", "ask", "volume"]]
    try:
        df.to_parquet(cache, index=False)
        print(f"       [cache written] {cache.name}", flush=True)
    except Exception as exc:
        print(f"       [cache write failed: {exc}]", flush=True)
    return df


# ---------------------------------------------------------------------------
# Fixed entry generation: SMA fast/slow cross, flat-to-flat, next-bar-open fill
# ---------------------------------------------------------------------------

def generate_entries(bars: pd.DataFrame) -> pd.DataFrame:
    b = bars.copy().reset_index(drop=True)
    fast = b["close"].rolling(FAST_PERIOD).mean()
    slow = b["close"].rolling(SLOW_PERIOD).mean()

    diff = fast - slow
    prev = diff.shift(1)
    cross_up = (prev <= 0) & (diff > 0)
    cross_dn = (prev >= 0) & (diff < 0)

    n = len(b)
    open_px = b["open"].to_numpy()
    dt = b["dt"].to_numpy()

    trades = []
    in_pos = 0  # 0 flat, +1 long, -1 short
    entry_i = None
    entry_px = None
    cu = cross_up.to_numpy()
    cd = cross_dn.to_numpy()

    warmup = max(FAST_PERIOD, SLOW_PERIOD)

    for i in range(warmup, n - 1):
        signal = 0
        if cu[i]:
            signal = +1
        elif cd[i]:
            signal = -1
        if signal == 0:
            continue
        # Signal computed on bar i (close); fill at bar i+1 open (NT next-bar).
        fill_i = i + 1
        # Close any open position first (cross is a reversal signal).
        if in_pos != 0 and signal != in_pos:
            trades.append(
                {
                    "instrument": f"{INSTRUMENT} {CONTRACT}",
                    "Market pos.": "Long" if in_pos > 0 else "Short",
                    "entry_dt": pd.Timestamp(dt[entry_i]),
                    "entry_price": float(entry_px),
                    "exit_dt": pd.Timestamp(dt[fill_i]),
                    "exit_price": float(open_px[fill_i]),
                }
            )
            in_pos = 0
        # Open the new position (EntriesPerDirection=1 -> only when flat).
        if in_pos == 0:
            in_pos = signal
            entry_i = fill_i
            entry_px = float(open_px[fill_i])

    # Close the final dangling position at last bar.
    if in_pos != 0 and entry_i is not None:
        trades.append(
            {
                "instrument": f"{INSTRUMENT} {CONTRACT}",
                "Market pos.": "Long" if in_pos > 0 else "Short",
                "entry_dt": pd.Timestamp(dt[entry_i]),
                "entry_price": float(entry_px),
                "exit_dt": pd.Timestamp(dt[n - 1]),
                "exit_price": float(b["close"].iloc[n - 1]),
            }
        )

    return pd.DataFrame(trades)


# ---------------------------------------------------------------------------
# Exit policy roster
# ---------------------------------------------------------------------------

def build_policies() -> list:
    pols: list = []
    # Fixed R:R (tick-based) — parity-clean
    for s in (16, 24, 32):
        for t in (s, int(s * 1.5), s * 2, s * 3):
            pols.append(FixedStopTargetPolicy(name=f"fixed_s{s}_t{t}", stop_ticks=s, target_ticks=t))
    # ATR trail
    for sm in (1.0, 1.5):
        for tm in (1.5, 2.0, 2.5):
            pols.append(AtrTrailPolicy(name=f"atrtrail_s{sm}_t{tm}", stop_atr_mult=sm, trail_atr_mult=tm))
    # Break-even then ATR trail
    for trig in (8, 16, 24):
        for tm in (1.5, 2.0):
            pols.append(
                BreakEvenAtrTrailPolicy(
                    name=f"be_trig{trig}_t{tm}",
                    stop_atr_mult=1.5,
                    trail_atr_mult=tm,
                    be_trigger_atr_mult=99.0,  # disable ATR-arming; use tick-arming below
                    be_trigger_ticks=float(trig),
                    be_offset_ticks=2.0,
                )
            )
    # Chandelier
    for sm in (1.0, 1.5):
        for tm in (2.0, 2.5):
            pols.append(ChandelierAtrTrailPolicy(name=f"chand_s{sm}_t{tm}", stop_atr_mult=sm, trail_atr_mult=tm))
    # Fixed ATR stop then ATR trail (stop-in-profit, arm by MFE ticks)
    for arm in (16, 32):
        for tm in (1.5, 2.0):
            pols.append(
                FixedAtrThenAtrTrailPolicy(
                    name=f"fixedthentrail_arm{arm}_t{tm}",
                    stop_atr_mult=1.5,
                    trail_atr_mult=tm,
                    arm_mfe_ticks=float(arm),
                )
            )
    # Fixed ATR stop then chandelier
    for arm in (16, 32):
        pols.append(
            FixedAtrThenChandelierPolicy(
                name=f"fixedthenchand_arm{arm}",
                stop_atr_mult=1.5,
                trail_atr_mult=2.0,
                arm_mfe_ticks=float(arm),
            )
        )
    # Giveback after MFE (tick-based) — parity-clean
    for arm in (20, 40):
        for gb in (8, 16):
            if gb < arm:
                pols.append(GivebackAfterMfePolicy(name=f"giveback_arm{arm}_gb{gb}", arm_mfe_ticks=float(arm), giveback_ticks=float(gb)))
    # Time stop (no progress)
    for mins in (30, 60):
        pols.append(TimeStopNoProgressPolicy(name=f"timestop_{mins}m", max_minutes=float(mins), min_mfe_ticks=8.0))
    return pols


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    bars_path = MARKETDATA / f"{INSTRUMENT} {CONTRACT}.Last.txt"
    ticks_path = MARKETDATA / f"{INSTRUMENT} {CONTRACT} Tick.Last.txt"

    print(f"[load] minute bars {bars_path.name}")
    bars = load_minute_bars(bars_path)
    print(f"       {len(bars):,} bars  {bars['dt'].min()} .. {bars['dt'].max()}")

    print("[entries] generating fixed SMA-cross entry set ...")
    trades = generate_entries(bars)
    print(f"          {len(trades)} trades  "
          f"{trades['entry_dt'].min()} .. {trades['entry_dt'].max()}")

    print(f"[load] ticks {ticks_path.name} (1.34 GB, streaming via read_csv) ...")
    tk0 = time.time()
    ticks = load_ticks(ticks_path)
    print(f"       {len(ticks):,} ticks  {ticks['dt'].min()} .. {ticks['dt'].max()}  "
          f"({time.time()-tk0:.0f}s)")

    market = MarketDataStore()
    market.put_minute_bars(INSTRUMENT, CONTRACT, bars)
    market.put_ticks(INSTRUMENT, CONTRACT, ticks)

    policies = build_policies()
    print(f"[sim] {len(policies)} exit policies x {len(trades)} trades (unbounded, {MAX_HOLD_MIN}m max hold)")

    cfg = ExitSimConfig(
        tick_size=TICK_SIZE,
        atr_tf="5m",
        atr_period=14,
        bounded_to_original_exit=False,   # let each policy exit on its own logic
        max_minutes_unbounded=MAX_HOLD_MIN,
        use_bid_ask_triggers=True,
    )

    sim_df = simulate_exit_policies_for_run(
        run_id="optionA_pantheon_botv2_NQ0626",
        trades=trades,
        market=market,
        instrument=INSTRUMENT,
        contract=CONTRACT,
        policies=policies,
        cfg=cfg,
    )

    if "policy" in sim_df.columns and (sim_df["policy"] == "DIAGNOSTIC").all():
        print("[ERROR] simulator returned only DIAGNOSTIC rows:")
        print(sim_df[["exit_reason", "detail"]].to_string())
        return

    sim_path = OUT_DIR / "sim_rows.csv"
    sim_df.to_csv(sim_path, index=False)

    metrics = _aggregate_policy_metrics(sim_df)

    # MFE-capture: net pnl_ticks / total favorable excursion ticks per policy
    mfe_cap = {}
    df = sim_df[~sim_df["policy"].isin(["DIAGNOSTIC", "actual"])].copy()
    df["pnl_ticks"] = pd.to_numeric(df["pnl_ticks"], errors="coerce")
    df["mfe_ticks"] = pd.to_numeric(df.get("mfe_ticks"), errors="coerce")
    for name, grp in df.groupby("policy"):
        tot_mfe = float(grp["mfe_ticks"].clip(lower=0).sum())
        net = float(grp["pnl_ticks"].sum())
        mfe_cap[name] = (net / tot_mfe) if tot_mfe > 0 else None

    for row in metrics:
        row["family"] = _family_of(row["policy_name"])
        row["mfe_capture"] = mfe_cap.get(row["policy_name"])
        nt = row.get("net_ticks") or 0.0
        row["net_usd"] = nt * TICK_VALUE
        dd = row.get("max_dd_ticks")
        row["max_dd_usd"] = (dd * TICK_VALUE) if dd is not None else None

    # actual (seed) baseline for context
    actual = sim_df[sim_df["policy"] == "actual"]
    actual_net = float(pd.to_numeric(actual["pnl_ticks"], errors="coerce").sum()) if not actual.empty else None

    # Rank by net_ticks (already sorted), assign rank
    for i, row in enumerate(metrics, start=1):
        row["rank"] = i

    rank_df = pd.DataFrame(metrics)
    cols = ["rank", "policy_name", "family", "n_trades", "win_rate", "profit_factor",
            "net_ticks", "net_usd", "avg_ticks", "max_dd_ticks", "max_dd_usd", "mfe_capture"]
    cols = [c for c in cols if c in rank_df.columns]
    rank_df = rank_df[cols + [c for c in rank_df.columns if c not in cols]]
    rank_csv = OUT_DIR / "policy_ranking.csv"
    rank_df.to_csv(rank_csv, index=False)

    summary = {
        "instrument": f"{INSTRUMENT} {CONTRACT}",
        "n_trades_seed": int(len(trades)),
        "entry_signal": f"SMA cross fast={FAST_PERIOD}/slow={SLOW_PERIOD} on 1m, UseTrend=false, both directions",
        "tick_coverage": [str(ticks["dt"].min()), str(ticks["dt"].max())],
        "n_policies": len(policies),
        "actual_seed_net_ticks": actual_net,
        "atr_definition": "Wilder (simulator) — NT uses SMA ATR; caveat for ATR-mult policies",
        "ranking": metrics,
        "elapsed_sec": round(time.time() - t0, 1),
    }
    (OUT_DIR / "summary.json").write_text(json.dumps(summary, indent=2, default=str))

    print(f"\n[done] {time.time()-t0:.0f}s  -> {rank_csv}")
    print("\nTOP 15 EXIT POLICIES (ranked by net ticks):")
    show = ["rank", "policy_name", "family", "n_trades", "win_rate",
            "profit_factor", "net_ticks", "max_dd_ticks", "mfe_capture"]
    show = [c for c in show if c in rank_df.columns]
    with pd.option_context("display.width", 200, "display.max_columns", 30):
        print(rank_df[show].head(15).to_string(index=False))
    if actual_net is not None:
        print(f"\nseed (actual cross-to-cross) net ticks baseline: {actual_net:.0f}")


if __name__ == "__main__":
    main()
