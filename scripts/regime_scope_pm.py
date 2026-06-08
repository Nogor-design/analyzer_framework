"""Offline regime test on completed-run output — does conditioning on market
regime add edge on top of the AtrTrail exit, or is the PantheonMaster pool
already durable across regimes?

Answers the "test in Python before spending an NT regime-specialized matrix"
question. Reuses the existing pipeline end-to-end (no new regime logic):
  - parsers/ninjatrader/minute_bars_last_txt  -> tz-correct (Denver) NQ bars
  - analysis/strategy_discovery/regime.compute_bar_regime -> bar regime labels
    (Wilder ADX + EMA/DI trend + ATR-percentile vol; same vocabulary as
    PantheonMaster.cs: trending_up / trending_down / ranging_* / *_vol_*)
  - analysis/strategy_discovery/regime_scoping.run_regime_scoping -> per-regime
    honest re-price, edge-regime selection, durable/regime-limited/none track,
    adaptation_alpha

Reads the 36 passing AtrTrail survivors of a completed deployment session,
labels every realised trade by entry regime, and reports:
  (a) the track distribution across survivors (how many are regime-limited), and
  (b) the pooled per-regime expectancy / PF / n + pooled adaptation_alpha.

Decision rule: regime-limited + positive pooled adaptation_alpha => a RegimeMode
pin is predicted to help => worth one confirming NT matrix. Durable / marginal
=> the regime filter adds nothing here; skip the NT spend.

Usage: python scripts/regime_scope_pm.py [session_id]
"""
from __future__ import annotations

import csv
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

import pandas as pd

from ta_foundation.parsers.ninjatrader.minute_bars_last_txt import MinuteBarsLastTxtParser
from ta_foundation.analysis.strategy_discovery.regime import compute_bar_regime
from ta_foundation.analysis.strategy_discovery.regime_scoping import run_regime_scoping

SESSIONS = Path(".ta_artifacts/web_optimizer/sessions")
# Override with TA_BARS_FILE to use a freshly-exported (fuller-coverage) file.
BARS_FILE = Path(os.environ.get("TA_BARS_FILE", r"D:\MarketData\NQ 06-26.Last.txt"))
COST_MODEL = {"commission_per_side": 2.09, "tick_value": 5.0, "slippage_ticks": 1}
# NQ: 1 point = $20, tick = 0.25pt = $5. Honest re-price uses tick_value for slippage.


def _money(x: str) -> float:
    """NinjaTrader currency cell -> float. '($300.00)' -> -300.0, '$1,234' -> 1234."""
    s = (x or "").strip()
    if not s:
        return 0.0
    neg = s.startswith("(") and s.endswith(")")
    s = re.sub(r"[(),$\s]", "", s)
    try:
        v = float(s)
    except ValueError:
        return 0.0
    return -v if neg else v


def load_trades(result_dir: Path) -> pd.DataFrame:
    """Parse an NT Trades.csv into entry_time (naive Denver) + gross profit."""
    p = result_dir / "Trades.csv"
    if not p.exists():
        return pd.DataFrame(columns=["entry_time", "profit"])
    with p.open("r", encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    out = []
    for r in rows:
        et = (r.get("Entry time") or "").strip()
        if not et:
            continue
        out.append({"entry_time": et, "profit": _money(r.get("Profit", ""))})
    df = pd.DataFrame(out)
    if not df.empty:
        # NT Trades.csv entry time: "M/D/YYYY h:mm:ss AM/PM" (naive Denver local).
        df["entry_time"] = pd.to_datetime(df["entry_time"], format="%m/%d/%Y %I:%M:%S %p",
                                          errors="coerce")
        df = df.dropna(subset=["entry_time"])
    return df


def load_bars_with_regime() -> pd.DataFrame:
    art = MinuteBarsLastTxtParser().parse(BARS_FILE, run_id=None)
    bars = art.df.copy()
    # Strip tz -> naive Denver so it shares the Trades.csv (NT local) clock.
    bars["dt"] = pd.to_datetime(bars["dt"]).dt.tz_localize(None)
    bars = bars.sort_values("dt").reset_index(drop=True)
    print(f"  bars: {len(bars)} rows, {bars['dt'].min()} .. {bars['dt'].max()}", flush=True)
    return compute_bar_regime(bars)


def _honest_metrics(d: dict) -> dict:
    """per_regime[label]['honest'] is the full apply_honest_execution result;
    its re-priced metrics live under the nested ['honest'] key."""
    return ((d or {}).get("honest") or {}).get("honest") or {}


def passing_runs(review_dir: Path) -> list[dict]:
    p = review_dir / "evaluated_candidates.csv"
    rows = list(csv.DictReader(p.open(encoding="utf-8-sig")))
    return [r for r in rows if "pass" in (r.get("status") or "").lower()]


def fmt(v, nd=2):
    return "n/a" if v is None else f"{float(v):.{nd}f}"


def main() -> int:
    session_id = sys.argv[1] if len(sys.argv) > 1 else "opt_a09359e6b60b"
    sdir = SESSIONS / session_id
    handoff = sdir / "deployment_package" / "final_backtest_handoff"
    review = handoff / "final_backtest_review"
    results_root = handoff / "nt8_backtest_results"

    print(f"=== regime scoping: {session_id} ===", flush=True)
    bars_with_regime = load_bars_with_regime()
    bars_end = pd.to_datetime(bars_with_regime["dt"]).max()
    survivors = passing_runs(review)
    print(f"  {len(survivors)} passing survivors; bar coverage ends {bars_end} "
          f"(trades after this are dropped to avoid stale backward-fill)", flush=True)

    tracks = Counter()
    pooled_frames = []
    per_run = []
    dropped_total = kept_total = 0
    for r in survivors:
        rid = r["run_id"]
        trades = load_trades(results_root / rid)
        if trades.empty:
            continue
        before = len(trades)
        trades = trades[trades["entry_time"] <= bars_end].reset_index(drop=True)
        dropped_total += before - len(trades)
        kept_total += len(trades)
        if trades.empty:
            continue
        pooled_frames.append(trades)
        res = run_regime_scoping(trades, bars_with_regime=bars_with_regime,
                                 cost_model=COST_MODEL, options={})
        tracks[res.get("track")] += 1
        per_run.append((rid, res.get("track"), res.get("edge_regimes"),
                        res.get("n_trades"), res.get("n_unlabeled")))

    print(f"\n  kept {kept_total} in-coverage trades; dropped {dropped_total} "
          f"post-{bars_end.date()} (stale-regime)", flush=True)
    print("\n--- per-survivor track distribution ---", flush=True)
    for t, n in tracks.most_common():
        print(f"  {t:>16}: {n}", flush=True)

    # Pooled read across the whole honest pool.
    pooled = pd.concat(pooled_frames, ignore_index=True) if pooled_frames else pd.DataFrame()
    print(f"\n--- pooled across survivors: {len(pooled)} trades ---", flush=True)
    pres = run_regime_scoping(pooled, bars_with_regime=bars_with_regime,
                              cost_model=COST_MODEL, options={})
    print(f"  track={pres.get('track')}  n_trades={pres.get('n_trades')}  "
          f"n_unlabeled={pres.get('n_unlabeled')}  edge_regimes={pres.get('edge_regimes')}", flush=True)
    print("  per-regime (honest re-price):", flush=True)
    for label, d in sorted((pres.get("per_regime") or {}).items()):
        h = _honest_metrics(d)
        print(f"    {label:>22}: n={int(d.get('n_trades') or 0):>4}  exp={fmt(h.get('expectancy')):>8}  "
              f"PF={fmt(h.get('profit_factor')):>6}  net={fmt(h.get('net_profit'),0):>9}  "
              f"passed={d.get('passed')}", flush=True)
    aa = pres.get("adaptation_alpha") or {}
    print("  adaptation_alpha (scope-to-edge minus trade-all):", flush=True)
    print(f"    expectancy_delta={fmt(aa.get('expectancy_delta'))}  "
          f"net_profit_delta={fmt(aa.get('net_profit_delta'),0)}  "
          f"PF_delta={fmt(aa.get('profit_factor_delta'))}  "
          f"n_trades_delta={aa.get('n_trades_delta')}", flush=True)

    out = {"session": session_id, "tracks": dict(tracks), "pooled": pres, "per_run": per_run}
    outp = review / "regime_scoping_offline.json"
    outp.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(f"\nwrote {outp}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
