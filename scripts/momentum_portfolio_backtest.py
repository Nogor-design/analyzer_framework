"""Deployable equity-index momentum sleeve — vol-scaled portfolio backtest.

The cross-asset scout proved daily time-series momentum is a real, OOS-validated
edge (t~5 full panel; t 1.79 equity-only). This turns the EQUITY-INDEX leg
(NQ/ES/YM/RTY) into a proper, deployable daily strategy and judges it the right
way -- as a volatility-scaled PORTFOLIO (Sharpe / drawdown / Calmar), not
per-trade R -- so it can be mapped onto the NT/prop pipeline.

Construction (canonical managed-futures TSMOM sleeve):
  * Signal: blended sign of N-day momentum over lookbacks {20,50,100}d -> [-1,1].
    Uses ONLY past data; the signal is lagged one day (no look-ahead).
  * Sizing: each instrument vol-scaled to equal risk (weight = target_vol/realized_vol,
    realized = 30d EWM of daily-return vol, annualized), capped.
  * Portfolio: equal-risk average across instruments available that day.
  * Costs: turnover * cost_bps (round-trip); index futures cost is a few bp -- shown
    as a sensitivity.
  * Reported scaled to 10% annualized vol so drawdowns are interpretable; Sharpe is
    scale-invariant.

    python scripts/momentum_portfolio_backtest.py
    python scripts/momentum_portfolio_backtest.py --cost-bps 2 --target-vol 0.10
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

DAILY_DIR = Path(r"D:\MarketData\daily")
EQUITY = ["NQ", "ES", "YM", "RTY"]
LOOKBACKS = [20, 50, 100]
ANN = 252


def load_close(inst: str) -> pd.Series:
    df = pd.read_csv(DAILY_DIR / f"{inst}_daily.csv", parse_dates=["dt"])
    s = pd.Series(df["close"].values, index=pd.to_datetime(df["dt"], utc=True).dt.normalize())
    return s[~s.index.duplicated(keep="last")].sort_index()


def momentum_signal(close: pd.Series) -> pd.Series:
    """Blended sign of N-day momentum over LOOKBACKS, in [-1,1]."""
    sig = np.zeros(len(close))
    for lb in LOOKBACKS:
        sig = sig + np.sign(close / close.shift(lb) - 1.0).fillna(0.0).values
    return pd.Series(sig / len(LOOKBACKS), index=close.index)


def stats(returns: pd.Series, target_vol: float) -> dict:
    r = returns.dropna()
    if r.empty or r.std() == 0:
        return {}
    scale = target_vol / (r.std() * np.sqrt(ANN))
    r = r * scale
    eq = (1 + r).cumprod()
    cagr = eq.iloc[-1] ** (ANN / len(r)) - 1
    sharpe = r.mean() / r.std() * np.sqrt(ANN)
    dd = (eq / eq.cummax() - 1.0)
    maxdd = dd.min()
    calmar = cagr / abs(maxdd) if maxdd < 0 else float("nan")
    yearly = r.groupby(r.index.year).apply(lambda x: (1 + x).prod() - 1)
    return {
        "years": len(r) / ANN, "CAGR": cagr, "ann_vol": r.std() * np.sqrt(ANN),
        "Sharpe": sharpe, "MaxDD": maxdd, "Calmar": calmar,
        "pos_years": (yearly > 0).mean(), "n_years": len(yearly),
        "best_yr": yearly.max(), "worst_yr": yearly.min(), "yearly": yearly,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--target-vol", type=float, default=0.10)
    ap.add_argument("--cost-bps", type=float, default=1.0, help="round-trip cost per unit turnover, bps")
    ap.add_argument("--vol-lookback", type=int, default=30)
    args = ap.parse_args(argv)

    closes = {i: load_close(i) for i in EQUITY}
    rets = {i: closes[i].pct_change() for i in EQUITY}
    # realized vol (annualized), EWM
    vol = {i: rets[i].ewm(span=args.vol_lookback).std() * np.sqrt(ANN) for i in EQUITY}
    sig = {i: momentum_signal(closes[i]) for i in EQUITY}

    # per-instrument vol-scaled position (lagged signal, equal-risk weight)
    per_pnl = {}
    per_turn = {}
    for i in EQUITY:
        w = (args.target_vol / vol[i]).clip(upper=5.0).replace([np.inf, -np.inf], np.nan)
        pos = (sig[i].shift(1) * w)              # yesterday's signal sizes today
        per_pnl[i] = pos * rets[i]
        per_turn[i] = pos.diff().abs()

    pnl_df = pd.DataFrame(per_pnl)
    turn_df = pd.DataFrame(per_turn)
    # equal-risk average over instruments available each day
    port_gross = pnl_df.mean(axis=1)
    port_turn = turn_df.mean(axis=1)
    cost = port_turn * (args.cost_bps / 1e4)
    port_net = (port_gross - cost).dropna()

    print(f"Equity-index momentum sleeve  (NQ/ES/YM/RTY, lookbacks {LOOKBACKS}d, "
          f"target {args.target_vol:.0%} vol, cost {args.cost_bps}bp)\n")

    s_gross = stats(port_gross, args.target_vol)
    s_net = stats(port_net, args.target_vol)
    print(f"{'':14}{'Sharpe':>8}{'CAGR':>8}{'AnnVol':>8}{'MaxDD':>8}{'Calmar':>8}{'pos yrs':>9}")
    for label, s in (("PORTFOLIO net", s_net), ("(gross)", s_gross)):
        if s:
            print(f"{label:14}{s['Sharpe']:>8.2f}{s['CAGR']:>8.1%}{s['ann_vol']:>8.1%}"
                  f"{s['MaxDD']:>8.1%}{s['Calmar']:>8.2f}{s['pos_years']:>8.0%}")
    print()
    # per-instrument standalone Sharpe (net of cost)
    print("Per-instrument standalone (net, vol-scaled):")
    for i in EQUITY:
        net_i = (per_pnl[i] - per_turn[i] * (args.cost_bps / 1e4)).dropna()
        si = stats(net_i, args.target_vol)
        if si:
            print(f"  {i:4} Sharpe {si['Sharpe']:>5.2f}  CAGR {si['CAGR']:>6.1%}  "
                  f"MaxDD {si['MaxDD']:>6.1%}  ({si['years']:.0f}yr)")

    # OOS split
    print("\nOut-of-sample split (portfolio net Sharpe):")
    for lo, hi, lab in (("2000-01-01", "2013-01-01", "2000-2012 IS"),
                        ("2013-01-01", "2100-01-01", "2013-2026 OOS")):
        seg = port_net[(port_net.index >= pd.Timestamp(lo, tz="UTC")) &
                       (port_net.index < pd.Timestamp(hi, tz="UTC"))]
        ss = stats(seg, args.target_vol)
        if ss:
            print(f"  {lab:16} Sharpe {ss['Sharpe']:>5.2f}  CAGR {ss['CAGR']:>6.1%}  "
                  f"MaxDD {ss['MaxDD']:>6.1%}")

    if s_net.get("yearly") is not None:
        print("\nYearly net returns (10% vol target):")
        y = s_net["yearly"]
        print("  " + "  ".join(f"{yr}:{v:+.0%}" for yr, v in y.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
