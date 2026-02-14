from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Sequence, Tuple, Union

import numpy as np
import pandas as pd

from ta_foundation.analysis.features.regime import atr_wilder
from ta_foundation.marketdata.store import MarketDataStore
from ta_foundation.analysis.exits.policies import (
    FixedStopTargetPolicy,
    AtrTrailPolicy,
    BreakEvenAtrTrailPolicy,
)

Policy = Union[FixedStopTargetPolicy, AtrTrailPolicy, BreakEvenAtrTrailPolicy]


def _ensure_series_tz(s: pd.Series, tz: str) -> pd.Series:
    """Ensure datetime Series is tz-aware in tz (localize if naive, convert if aware)."""
    s = pd.to_datetime(s, errors="coerce")
    if getattr(s.dt, "tz", None) is None:
        return s.dt.tz_localize(tz, nonexistent="shift_forward", ambiguous="NaT")
    return s.dt.tz_convert(tz)


def _ensure_ts_tz(ts: pd.Timestamp, tz: str) -> pd.Timestamp:
    """Ensure Timestamp is tz-aware in tz (localize if naive, convert if aware)."""
    ts = pd.Timestamp(ts)
    if ts.tzinfo is None:
        return ts.tz_localize(tz, nonexistent="shift_forward", ambiguous="NaT")
    return ts.tz_convert(tz)

def _ticks_window_from_ns(ticks: pd.DataFrame, tick_ns: np.ndarray, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    if ticks is None or ticks.empty:
        return ticks

    s_ns = int(pd.Timestamp(start).value)
    e_ns = int(pd.Timestamp(end).value)
    if e_ns <= s_ns:
        return ticks.iloc[0:0]

    s = np.searchsorted(tick_ns, s_ns, side="left")
    e = np.searchsorted(tick_ns, e_ns, side="right")
    if e <= s:
        return ticks.iloc[0:0]
    return ticks.iloc[s:e]


@dataclass(frozen=True)
class ExitSimConfig:
    tick_size: float = 0.25
    atr_tf: str = "5m"
    atr_period: int = 14

    bounded_to_original_exit: bool = True
    max_minutes_unbounded: int = 180

    use_bid_ask_triggers: bool = True
    price_col_last: str = "last"
    price_col_bid: str = "bid"
    price_col_ask: str = "ask"


def _diag_row(run_id: str, instrument: str, contract: str, reason: str, detail: str) -> pd.DataFrame:
    return pd.DataFrame([{
        "run_id": run_id,
        "trade_idx": -1,
        "policy": "DIAGNOSTIC",
        "entry_dt": pd.NaT,
        "exit_dt": pd.NaT,
        "entry_price": np.nan,
        "exit_price": np.nan,
        "exit_reason": reason,
        "pnl_ticks": np.nan,
        "mae_ticks": np.nan,
        "mfe_ticks": np.nan,
        "atr_entry": np.nan,
        "detail": f"{reason}: {detail} | instrument={instrument} contract={contract}",
    }])






def _simulate_one_trade(
    *,
    ticks: pd.DataFrame,
    entry_dt: pd.Timestamp,
    entry_price: float,
    direction: int,
    atr_entry: float,
    policy: Any,
    cfg: ExitSimConfig,
) -> dict:
    """
    Simulate one trade against a tick-path window.

    If no stop/target/trail is hit before the window ends, we EXIT AT THE WINDOW END
    using the last available trigger price in the window (bounded semantics).
    """
    if ticks is None or ticks.empty:
        return {
            "exit_dt": pd.NaT,
            "exit_price": np.nan,
            "exit_reason": "no_ticks",
            "pnl_ticks": np.nan,
            "mae_ticks": np.nan,
            "mfe_ticks": np.nan,
        }

    name = getattr(policy, "name", policy.__class__.__name__).lower()

    stop_pts = None
    target_pts = None
    trail_mult = None
    be_trigger_mult = None

    # Fixed stop/target
    if ("fixed" in name) or hasattr(policy, "stop_atr_mult") or hasattr(policy, "target_atr_mult"):
        stop_mult = float(getattr(policy, "stop_atr_mult", 1.0))
        target_mult = float(getattr(policy, "target_atr_mult", 1.0))
        stop_pts = atr_entry * stop_mult
        target_pts = atr_entry * target_mult

    # ATR trail
    if ("trail" in name) or hasattr(policy, "trail_atr_mult"):
        trail_mult = float(getattr(policy, "trail_atr_mult", 1.0))

    # Break-even trigger
    if ("be" in name) or hasattr(policy, "be_trigger_atr_mult"):
        be_trigger_mult = float(getattr(policy, "be_trigger_atr_mult", 1.0))

    best_favorable_pts = 0.0
    worst_adverse_pts = 0.0

    trail_distance_pts = (atr_entry * trail_mult) if trail_mult is not None else None
    stop_price = None
    target_price = None
    be_armed = False

    if stop_pts is not None:
        stop_price = entry_price - (stop_pts * direction)
    if target_pts is not None:
        target_price = entry_price + (target_pts * direction)

    exit_dt = pd.NaT
    exit_price = np.nan
    exit_reason = None

    last_dt = pd.NaT
    last_trigger_px = np.nan

    for _, row in ticks.iterrows():
        dt = row["dt"]
        last = row.get(cfg.price_col_last, np.nan)
        bid = row.get(cfg.price_col_bid, np.nan)
        ask = row.get(cfg.price_col_ask, np.nan)

        if pd.isna(bid):
            bid = last
        if pd.isna(ask):
            ask = last

        if cfg.use_bid_ask_triggers:
            trigger_px = bid if direction > 0 else ask
        else:
            trigger_px = last

        if pd.isna(trigger_px):
            continue

        last_dt = dt
        last_trigger_px = float(trigger_px)

        pnl_pts = (last_trigger_px - entry_price) * direction

        if pnl_pts > best_favorable_pts:
            best_favorable_pts = pnl_pts
        if pnl_pts < worst_adverse_pts:
            worst_adverse_pts = pnl_pts

        # Break-even arming
        if be_trigger_mult is not None and not be_armed:
            if pnl_pts >= atr_entry * be_trigger_mult:
                be_armed = True
                if stop_price is None:
                    stop_price = entry_price
                else:
                    if direction > 0:
                        stop_price = max(stop_price, entry_price)
                    else:
                        stop_price = min(stop_price, entry_price)

        # Trailing stop update
        if trail_distance_pts is not None:
            best_price = entry_price + (best_favorable_pts * direction)
            new_trail_stop = best_price - (trail_distance_pts * direction)
            if stop_price is None:
                stop_price = new_trail_stop
            else:
                if direction > 0:
                    stop_price = max(stop_price, new_trail_stop)
                else:
                    stop_price = min(stop_price, new_trail_stop)

        # Conservative ordering: stop before target
        if stop_price is not None:
            if direction > 0 and last_trigger_px <= stop_price:
                exit_dt = dt
                exit_price = last_trigger_px
                exit_reason = "stop"
                break
            if direction < 0 and last_trigger_px >= stop_price:
                exit_dt = dt
                exit_price = last_trigger_px
                exit_reason = "stop"
                break

        if target_price is not None:
            if direction > 0 and last_trigger_px >= target_price:
                exit_dt = dt
                exit_price = last_trigger_px
                exit_reason = "target"
                break
            if direction < 0 and last_trigger_px <= target_price:
                exit_dt = dt
                exit_price = last_trigger_px
                exit_reason = "target"
                break

    # If never hit an exit trigger, EXIT AT WINDOW END (bounded semantics)
    if exit_reason is None:
        if pd.notna(last_dt) and not pd.isna(last_trigger_px):
            pnl_ticks = (last_trigger_px - entry_price) * direction / float(cfg.tick_size)
            return {
                "exit_dt": last_dt,
                "exit_price": float(last_trigger_px),
                "exit_reason": "time",
                "pnl_ticks": float(pnl_ticks),
                "mae_ticks": float(worst_adverse_pts / float(cfg.tick_size)),
                "mfe_ticks": float(best_favorable_pts / float(cfg.tick_size)),
            }

        return {
            "exit_dt": pd.NaT,
            "exit_price": np.nan,
            "exit_reason": "no_valid_ticks",
            "pnl_ticks": np.nan,
            "mae_ticks": np.nan,
            "mfe_ticks": np.nan,
        }

    pnl_ticks = (float(exit_price) - entry_price) * direction / float(cfg.tick_size)
    mae_ticks = float(worst_adverse_pts / float(cfg.tick_size))
    mfe_ticks = float(best_favorable_pts / float(cfg.tick_size))

    return {
        "exit_dt": exit_dt,
        "exit_price": float(exit_price),
        "exit_reason": exit_reason,
        "pnl_ticks": float(pnl_ticks),
        "mae_ticks": mae_ticks,
        "mfe_ticks": mfe_ticks,
    }


def simulate_exit_policies_for_run(
    *,
    run_id: str,
    trades: pd.DataFrame,
    market: MarketDataStore,
    instrument: str,
    contract: str,
    policies: Sequence[Policy],
    cfg: ExitSimConfig,
) -> pd.DataFrame:
    if trades is None or trades.empty:
        return _diag_row(run_id, instrument, contract, "no_trades", "Trades dataframe was empty.")

    # --- existing _find_col logic unchanged ---
    def _norm(s: str) -> str:
        s = (s or "").lower()
        return "".join([c for c in s if c.isalnum()])

    def _find_col(df: pd.DataFrame, candidates: Sequence[str]) -> Optional[str]:
        if df is None or df.empty:
            return None
        m = {_norm(c): c for c in df.columns}
        for cand in candidates:
            k = _norm(cand)
            if k in m:
                return m[k]
        cols = list(df.columns)
        for cand in candidates:
            lc = str(cand).lower()
            for c in cols:
                if lc in str(c).lower():
                    return c
        return None

    def _direction_sign(trade_row: pd.Series) -> int:
        for key in ("Market pos.", "market_pos", "Market position", "Market Position"):
            if key in trade_row.index and pd.notna(trade_row[key]):
                s = str(trade_row[key]).strip().lower()
                if s.startswith("short"):
                    return -1
                if s.startswith("long"):
                    return +1
        for key in ("Entry name", "Entry Name", "Entry signal", "Entry Signal", "Signal", "Signal name", "Signal Name"):
            if key in trade_row.index and pd.notna(trade_row[key]):
                s = str(trade_row[key]).strip().lower()
                if "sell short" in s or "sellshort" in s:
                    return -1
                if "buy" in s:
                    return +1
        for key in ("Direction", "direction", "Side", "side"):
            if key in trade_row.index and pd.notna(trade_row[key]):
                s = str(trade_row[key]).strip().lower()
                if s in ("short", "sell", "sellshort", "sell short", "s", "-1"):
                    return -1
                if s in ("long", "buy", "b", "1", "+1"):
                    return +1
                if "short" in s:
                    return -1
                if "buy" in s or "long" in s:
                    return +1
        return +1

    entry_dt_col = _find_col(trades, ["entry_dt", "entry_time", "Entry time", "Entry Time"])
    exit_dt_col = _find_col(trades, ["exit_dt", "exit_time", "Exit time", "Exit Time"])
    entry_px_col = _find_col(trades, ["entry_price", "Entry price", "Entry Price"])
    exit_px_col = _find_col(trades, ["exit_price", "Exit price", "Exit Price"])

    if entry_dt_col is None or entry_px_col is None:
        return _diag_row(
            run_id, instrument, contract,
            "missing_trade_columns",
            f"Could not find entry_dt/entry_px. cols={list(trades.columns)}"
        )

    t = trades.copy()

    # Enforce tz-aware America/Denver for trades
    t["_entry_dt"] = _ensure_series_tz(t[entry_dt_col], "America/Denver")
    t["_exit_dt"] = _ensure_series_tz(t[exit_dt_col], "America/Denver") if exit_dt_col else pd.NaT
    t["_entry_px"] = pd.to_numeric(t[entry_px_col], errors="coerce")

    if exit_px_col:
        t["_exit_px"] = pd.to_numeric(t[exit_px_col], errors="coerce")
    else:
        t["_exit_px"] = np.nan

    t = t[t["_entry_dt"].notna() & t["_entry_px"].notna()].copy()
    if t.empty:
        return _diag_row(run_id, instrument, contract, "no_valid_trades",
                         "All trades missing entry_dt or entry_px after parsing.")

    # Pull ticks first (we need them anyway)
    ticks_all = market.get_ticks(instrument, contract)
    if ticks_all is None or ticks_all.empty:
        return _diag_row(run_id, instrument, contract, "missing_ticks",
                         "MarketDataStore has no ticks for this instrument/contract.")

    if "dt" not in ticks_all.columns:
        return _diag_row(run_id, instrument, contract, "ticks_missing_dt",
                         f"Tick dataframe missing 'dt'. cols={list(ticks_all.columns)}")

    ticks_all = ticks_all.copy()
    ticks_all["dt"] = _ensure_series_tz(ticks_all["dt"], "America/Denver")
    ticks_all = ticks_all[ticks_all["dt"].notna()].sort_values("dt").reset_index(drop=True)

    # Build tick time axis in *nanoseconds* (UTC-naive datetime64[ns])
    dt_utc_ns = (
        ticks_all["dt"]
        .dt.tz_convert("UTC")
        .dt.tz_localize(None)
        .astype("datetime64[ns]")
    )



    # Precompute tick time axis in UTC-naive int64 ns (aligned to ticks_all rows)
    dt_utc_naive = ticks_all["dt"].dt.tz_convert("UTC").dt.tz_localize(None)
    tick_ns = dt_utc_naive.astype("int64").to_numpy(dtype="int64", copy=False)
    # Guard against unit drift (microseconds vs nanoseconds)
    if tick_ns.size and tick_ns.max() < 10 ** 17:
        # If values are ~1e15, they are microseconds — convert to nanoseconds
        tick_ns = tick_ns * 1000

    # IMPORTANT: use tick-derived bars (minute bars may be unreliable)
    bars = market.get_bars(instrument, contract, timeframe=cfg.atr_tf, source="ticks")
    if bars is None or bars.empty:
        return _diag_row(
            run_id, instrument, contract,
            "missing_bars",
            f"get_bars(timeframe={cfg.atr_tf}, source='ticks') returned empty. "
            f"ticks_dt=[{ticks_all['dt'].min()}..{ticks_all['dt'].max()}]"
        )

    bars = bars.copy()
    if "dt" not in bars.columns:
        return _diag_row(run_id, instrument, contract, "bars_missing_dt",
                         f"Bars columns={list(bars.columns)}")

    bars["dt"] = _ensure_series_tz(bars["dt"], "America/Denver")
    bars = bars.sort_values("dt").reset_index(drop=True)

    # ATR (compute ONCE)
    bars["atr"] = atr_wilder(bars, period=cfg.atr_period)
    if "atr" not in bars.columns:
        return _diag_row(run_id, instrument, contract, "atr_failed",
                         "atr_wilder did not produce 'atr' column.")

    if bars["atr"].dropna().empty:
        return _diag_row(
            run_id, instrument, contract,
            "atr_all_nan",
            f"ATR all NaN. bars_rows={len(bars)} timeframe={cfg.atr_tf} period={cfg.atr_period}"
        )

    # --- Coverage diagnostics: trade time range vs tick time range ---
    tr_entry_min = t["_entry_dt"].min()
    tr_entry_max = t["_entry_dt"].max()
    tr_exit_max = t["_exit_dt"].max() if "_exit_dt" in t.columns else pd.NaT

    tk_min = ticks_all["dt"].min()
    tk_max = ticks_all["dt"].max()

    tr_max = tr_exit_max if pd.notna(tr_exit_max) else tr_entry_max

    if pd.notna(tr_entry_min) and pd.notna(tr_max) and pd.notna(tk_min) and pd.notna(tk_max):
        no_overlap = (tr_max < tk_min) or (tr_entry_min > tk_max)
        if no_overlap:
            return pd.DataFrame([{
                "run_id": run_id,
                "trade_idx": -1,
                "policy": "DIAGNOSTIC",
                "entry_dt": tr_entry_min,
                "exit_dt": tr_max,
                "entry_price": np.nan,
                "exit_price": np.nan,
                "exit_reason": "tick_coverage_missing",
                "pnl_ticks": np.nan,
                "mae_ticks": np.nan,
                "mfe_ticks": np.nan,
                "atr_entry": np.nan,
                "detail": (
                    f"NO_TICK_OVERLAP trades[{tr_entry_min}..{tr_max}] "
                    f"ticks[{tk_min}..{tk_max}] instrument={instrument} contract={contract}"
                ),
            }])

    # asof join ATR at entry
    atr_asof = pd.merge_asof(
        t[["_entry_dt"]].sort_values("_entry_dt").rename(columns={"_entry_dt": "ts"}),
        bars[["dt", "atr"]].dropna().sort_values("dt"),
        left_on="ts",
        right_on="dt",
        direction="backward",
        allow_exact_matches=True,
    )

    if pd.isna(atr_asof["atr"]).all():
        return pd.DataFrame([{
            "run_id": run_id,
            "trade_idx": -1,
            "policy": "DIAGNOSTIC",
            "entry_dt": t["_entry_dt"].min(),
            "exit_dt": t["_entry_dt"].max(),
            "entry_price": np.nan,
            "exit_price": np.nan,
            "exit_reason": "atr_asof_all_nan",
            "pnl_ticks": np.nan,
            "mae_ticks": np.nan,
            "mfe_ticks": np.nan,
            "atr_entry": np.nan,
            "detail": (
                f"ATR merge_asof produced all NaN. "
                f"trades_dt=[{t['_entry_dt'].min()}..{t['_entry_dt'].max()}] "
                f"bars_dt=[{bars['dt'].min()}..{bars['dt'].max()}] "
                f"ticks_dt=[{ticks_all['dt'].min()}..{ticks_all['dt'].max()}]"
            ),
        }])

    t = t.sort_values("_entry_dt").reset_index(drop=True)
    t["_atr_entry"] = pd.to_numeric(atr_asof["atr"], errors="coerce").values

    rows = []
    empty_windows = 0

    for i, tr in t.iterrows():
        entry_dt = tr["_entry_dt"]
        entry_px = float(tr["_entry_px"])
        direction = _direction_sign(tr)

        if cfg.bounded_to_original_exit and pd.notna(tr.get("_exit_dt")):
            end_dt = tr["_exit_dt"]
        else:
            end_dt = entry_dt + pd.Timedelta(minutes=int(cfg.max_minutes_unbounded))

        if pd.isna(end_dt) or end_dt <= entry_dt:
            continue

        atr_entry = float(tr["_atr_entry"]) if pd.notna(tr["_atr_entry"]) else 0.0
        if atr_entry <= 0:
            continue

        win = _ticks_window_from_ns(ticks_all, tick_ns, entry_dt, end_dt)

        if win is None or win.empty:
            empty_windows += 1
            continue

        # Actual reference
        if pd.notna(tr.get("_exit_px")) and pd.notna(tr.get("_exit_dt")):
            pnl_pts_actual = (float(tr["_exit_px"]) - entry_px) * direction
            rows.append({
                "run_id": run_id,
                "trade_idx": int(i),
                "policy": "actual",
                "entry_dt": entry_dt,
                "exit_dt": tr["_exit_dt"],
                "entry_price": entry_px,
                "exit_price": float(tr["_exit_px"]),
                "exit_reason": "time",
                "pnl_ticks": pnl_pts_actual / float(cfg.tick_size),
                "atr_entry": atr_entry,
            })

        # Sim policies
        for p in policies:
            res = _simulate_one_trade(
                ticks=win,
                entry_dt=entry_dt,
                entry_price=entry_px,
                direction=direction,
                atr_entry=atr_entry,
                policy=p,
                cfg=cfg,
            )
            rows.append({
                "run_id": run_id,
                "trade_idx": int(i),
                "policy": getattr(p, "name", p.__class__.__name__),
                "entry_dt": entry_dt,
                "exit_dt": res.get("exit_dt"),
                "entry_price": entry_px,
                "exit_price": res.get("exit_price"),
                "exit_reason": res.get("exit_reason"),
                "pnl_ticks": res.get("pnl_ticks"),
                "mae_ticks": res.get("mae_ticks"),
                "mfe_ticks": res.get("mfe_ticks"),
                "atr_entry": atr_entry,
            })

    if not rows:
        tick_min = ticks_all["dt"].min()
        tick_max = ticks_all["dt"].max()
        tr_min = t["_entry_dt"].min()
        tr_max = (t["_exit_dt"].max() if pd.notna(t["_exit_dt"]).any() else t["_entry_dt"].max())

        # Show one example of searchsorted behavior
        ex = t.iloc[0]
        ex_entry = ex["_entry_dt"]
        ex_end = ex_entry + pd.Timedelta(minutes=int(cfg.max_minutes_unbounded))
        s_ns = int(pd.Timestamp(ex_entry).value)
        e_ns = int(pd.Timestamp(ex_end).value)
        s = int(np.searchsorted(tick_ns, s_ns, side="left"))
        e = int(np.searchsorted(tick_ns, e_ns, side="right"))
        around = []
        for j in [s - 1, s, s + 1]:
            if 0 <= j < len(ticks_all):
                around.append(f"{j}:{ticks_all['dt'].iloc[j]}")
        around_str = " | ".join(around) if around else "(none)"

        return _diag_row(
            run_id, instrument, contract,
            "no_sim_rows",
            f"No simulation rows produced. trades_valid={len(t)} empty_tick_windows={empty_windows}. "
            f"ticks_dt=[{tick_min}..{tick_max}] trades_dt=[{tr_min}..{tr_max}]. "
            f"example_trade entry={ex_entry} end={ex_end} searchsorted s={s} e={e} around={around_str} "
            f"tick_ns[min]={int(tick_ns[0])} tick_ns[max]={int(tick_ns[-1])} "
            f"entry_ns={s_ns} end_ns={e_ns}"
        )

    out = pd.DataFrame(rows)

    # Attach a single diagnostic row if we are failing broadly
    if len(t) > 0 and empty_windows > 0 and (empty_windows / float(len(t))) > 0.20:
        out = pd.concat([out, pd.DataFrame([{
            "run_id": run_id,
            "trade_idx": -1,
            "policy": "DIAGNOSTIC",
            "entry_dt": t["_entry_dt"].min(),
            "exit_dt": t["_entry_dt"].max(),
            "entry_price": np.nan,
            "exit_price": np.nan,
            "exit_reason": "empty_tick_windows",
            "pnl_ticks": np.nan,
            "mae_ticks": np.nan,
            "mfe_ticks": np.nan,
            "atr_entry": np.nan,
            "detail": f"Empty tick windows for {empty_windows}/{len(t)} trades. "
                      f"Check tick coverage vs trade timestamps.",
        }])], ignore_index=True)

    return out
