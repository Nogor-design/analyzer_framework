from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import pandas as pd

from ta_foundation.marketdata.store import MarketDataStore
from ta_foundation.analysis.features.regime import ema, atr_wilder, ema_slope, session_vwap
from ta_foundation.analysis.features.microstructure import MicroWindow, micro_features_for_time


def _norm(s: str) -> str:
    s = (s or "").lower()
    return "".join([c for c in s if c.isalnum()])


def _find_col(df: pd.DataFrame, candidates: list[str]) -> Optional[str]:
    if df is None or df.empty:
        return None
    m = {_norm(c): c for c in df.columns}
    for cand in candidates:
        k = _norm(cand)
        if k in m:
            return m[k]
    return None


def infer_instrument_contract_from_trades(trades: pd.DataFrame) -> Optional[tuple[str, str]]:
    """
    Best-effort inference from trades['instrument'] like: "NQ 03-26"
    """
    if trades is None or trades.empty:
        return None
    c = _find_col(trades, ["instrument"])
    if c is None:
        return None
    v = trades[c].dropna()
    if v.empty:
        return None
    parts = str(v.iloc[0]).strip().split()
    if len(parts) >= 2:
        return parts[0], parts[1]
    return None


@dataclass(frozen=True)
class FeatureStoreConfig:
    bar_tf: str = "5m"
    htf_tf: str = "15m"
    ema_period: int = 50
    atr_period: int = 14
    slope_lookback: int = 3
    micro_window: MicroWindow = MicroWindow(5, 5)
    include_micro: bool = True


def build_trade_feature_frame_for_run(
    run_id: str,
    trades: pd.DataFrame,
    market: MarketDataStore,
    instrument: str,
    contract: str,
    cfg: FeatureStoreConfig,
) -> pd.DataFrame:
    """
    Returns one row per trade with regime + microstructure features.
    """
    if trades is None or trades.empty:
        return pd.DataFrame()

    entry_dt_col = _find_col(trades, ["entry_dt", "entry time", "entry_time", "entrydatetime", "entry date/time"])
    exit_dt_col = _find_col(trades, ["exit_dt", "exit time", "exit_time", "exitdatetime", "exit date/time"])
    pnl_col = _find_col(trades, ["profit", "pnl", "net profit", "netprofit"])

    if entry_dt_col is None:
        return pd.DataFrame()

    t = trades.copy()
    t[entry_dt_col] = pd.to_datetime(t[entry_dt_col], errors="coerce")
    if exit_dt_col is not None:
        t[exit_dt_col] = pd.to_datetime(t[exit_dt_col], errors="coerce")

    bars = market.get_bars(instrument, contract, timeframe=cfg.bar_tf, source="auto")
    htf = market.get_bars(instrument, contract, timeframe=cfg.htf_tf, source="auto")

    if bars is None or bars.empty or htf is None or htf.empty:
        return pd.DataFrame()

    bars = bars.copy().sort_values("dt")
    htf = htf.copy().sort_values("dt")

    # Indicators on bar_tf
    bars["atr"] = atr_wilder(bars, period=cfg.atr_period)
    bars["ema"] = ema(bars["close"], period=cfg.ema_period)
    bars["ema_slope"] = ema_slope(bars["ema"], lookback=cfg.slope_lookback)
    bars["vwap"] = session_vwap(bars)

    # HTF slope / bias
    htf["ema"] = ema(htf["close"], period=cfg.ema_period)
    htf["ema_slope"] = ema_slope(htf["ema"], lookback=cfg.slope_lookback)

    # asof join entry snapshots
    t_sorted = t.sort_values(entry_dt_col).reset_index(drop=False).rename(columns={"index": "_row"})

    j = pd.merge_asof(
        t_sorted[["_row", entry_dt_col]].dropna().rename(columns={entry_dt_col: "ts"}),
        bars,
        left_on="ts",
        right_on="dt",
        direction="backward",
        allow_exact_matches=True,
    ).set_index("_row")

    j2 = pd.merge_asof(
        t_sorted[["_row", entry_dt_col]].dropna().rename(columns={entry_dt_col: "ts"}),
        htf[["dt", "close", "ema", "ema_slope"]],
        left_on="ts",
        right_on="dt",
        direction="backward",
        allow_exact_matches=True,
    ).set_index("_row")

    out = t_sorted[["_row"]].copy().set_index("_row")
    out["run_id"] = run_id  # critical: per-bot grouping key
    out["instrument"] = instrument
    out["contract"] = contract
    out["entry_dt"] = t_sorted.set_index("_row")[entry_dt_col]
    out["exit_dt"] = t_sorted.set_index("_row")[exit_dt_col] if exit_dt_col else pd.NaT
    out["pnl"] = pd.to_numeric(t_sorted.set_index("_row")[pnl_col], errors="coerce") if pnl_col else pd.NA

    out["bar_dt"] = j.get("dt")
    out["bar_close"] = j.get("close")
    out["atr"] = j.get("atr")
    out["ema_slope"] = j.get("ema_slope")
    out["vwap"] = j.get("vwap")
    out["vwap_dist"] = (pd.to_numeric(out["bar_close"], errors="coerce") - pd.to_numeric(out["vwap"], errors="coerce"))
    out["vwap_dist_atr"] = out["vwap_dist"] / pd.to_numeric(out["atr"], errors="coerce")

    out["htf_ema_slope"] = j2.get("ema_slope")
    out["htf_close"] = j2.get("close")

    ed = pd.to_datetime(out["entry_dt"], errors="coerce")
    out["entry_hour"] = ed.dt.hour
    out["entry_dow"] = ed.dt.dayofweek

    if cfg.include_micro:
        ticks = market.get_ticks(instrument, contract)
        if ticks is not None and not ticks.empty:
            feats = []
            out_reset = out.reset_index()
            for _, row in out_reset.iterrows():
                feats.append(micro_features_for_time(ticks, row["entry_dt"], cfg.micro_window))
            mf = pd.DataFrame(feats, index=out.index)
            out = pd.concat([out, mf], axis=1)

    out = out.reset_index(drop=True)
    out["is_win"] = (pd.to_numeric(out["pnl"], errors="coerce") > 0).astype("Int64")

    return out


def build_trade_feature_frame(
    packages: dict[str, Any],
    market: Optional[MarketDataStore],
    cfg: FeatureStoreConfig,
) -> pd.DataFrame:
    """
    Build combined trade feature frame across all runs. Group by run_id in reports.
    """
    if market is None:
        return pd.DataFrame()

    frames = []
    for run_id, pkg in (packages or {}).items():
        trades = getattr(pkg, "trades", None)
        if trades is None or trades.empty:
            continue

        ic = infer_instrument_contract_from_trades(trades)
        if ic is None:
            continue
        instrument, contract = ic

        df = build_trade_feature_frame_for_run(run_id, trades, market, instrument, contract, cfg)
        if df is not None and not df.empty:
            frames.append(df)

    if not frames:
        return pd.DataFrame()

    out = pd.concat(frames, ignore_index=True)
    # defensive: ensure run_id exists
    if "run_id" not in out.columns:
        out["run_id"] = None
    return out
