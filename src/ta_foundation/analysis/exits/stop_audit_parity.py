"""Stop-audit **trajectory** parity — the reusable comparator core of the automated
C#↔Python parity loop (Phase 1: the backtest leg).

A PantheonMaster run with ``StopAuditCsvPath`` set dumps one row per stop event
(``INIT``/``TRAIL``) with NT's own ``favorable`` extreme and ``currentAtr`` attached
(schema written by ``AppendStopAudit``):

    time,event,policy,dir,entry,market,favorable,atr,peakProfit,oldStop,newStop

This module diffs that ACTUAL stop trajectory against the Python bar-close replica
(:func:`replicate_nt_atr_trail_trajectory`) step by step. It is sharper than the
Trades.csv exit-only diff (``diff_backtest_vs_live_trades``): it pinpoints the exact
bar where the trail first diverges and — because the audit carries NT's favorable +
ATR — decomposes a stop mismatch into *formula* vs *ATR-smoothing* drift (the
long-standing Wilder-vs-SMA question, measured at runtime instead of inferred).

Pure Python, no NinjaTrader dependency. The SAME parse + align + score code serves
the Phase-2 live-tick leg later (swap the bar replica for the tick replica); keeping
it strategy-/path-agnostic is the point — many strategies will reuse it.
"""
from __future__ import annotations

from typing import Any, Optional, Union

import pandas as pd

from ta_foundation.analysis.exits.nt_atr_trail_parity import (
    NtAtrTrailConfig,
    compute_atr,
    replicate_nt_atr_trail_trajectory,
)

# Canonical audit schema (must match PantheonMaster.AppendStopAudit's header).
AUDIT_COLUMNS = [
    "time", "event", "policy", "dir", "entry", "market",
    "favorable", "atr", "peakProfit", "oldStop", "newStop",
]
_NUMERIC = ["entry", "market", "favorable", "atr", "peakProfit", "oldStop", "newStop"]


def assign_trade_ids(df: pd.DataFrame) -> pd.DataFrame:
    """Segment a multi-trade audit into per-trade groups. A new trade starts at an
    ``INIT`` row, a fresh-trade first ``TRAIL`` (oldStop == 0, the bar-close path's
    reset sentinel), or any change in (entry, dir) — so it works for both the live
    path (has INIT rows) and the bar-close backtest path (TRAIL only)."""
    df = df.sort_values("time", kind="stable").reset_index(drop=True)
    boundary = (
        (df["event"] == "INIT")
        | (df["oldStop"].abs() < 1e-9)
        | (df["entry"] != df["entry"].shift(1))
        | (df["dir"] != df["dir"].shift(1))
    )
    if len(boundary):
        boundary.iloc[0] = True
    df = df.copy()
    df["trade_id"] = boundary.cumsum() - 1
    return df


def parse_stop_audit(source: Union[str, "pd.DataFrame"]) -> pd.DataFrame:
    """Read a StopAuditCsvPath dump (path or already-loaded frame) into a normalized
    frame: parsed ``time``, numeric value columns, ``event`` upper, ``dir`` lower,
    a signed ``direction`` (+1/-1), and a ``trade_id``."""
    df = source.copy() if isinstance(source, pd.DataFrame) else pd.read_csv(source)
    df = df.rename(columns=lambda c: str(c).strip())
    df["time"] = pd.to_datetime(df["time"])
    for col in _NUMERIC:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df["event"] = df["event"].astype(str).str.strip().str.upper()
    df["dir"] = df["dir"].astype(str).str.strip().str.lower()
    df["direction"] = df["dir"].map({"long": 1, "short": -1})
    df["policy"] = df["policy"].astype(str).str.strip()
    return assign_trade_ids(df)


def diff_trade_stop_trajectory(
    audit_trade: pd.DataFrame,
    replica_traj: pd.DataFrame,
    *,
    tick_size: float,
    stop_tol_ticks: float = 1.0,
) -> dict[str, Any]:
    """Step-by-step stop comparison for ONE trade. Compares the *active* stop on each
    side at the union of both sides' TRAIL-event times (asof-backward), so a stop the
    replica never proposed, or a one-bar ratchet-timing lead/lag, both surface as a
    divergence with the bar it first appears."""
    a = (audit_trade[audit_trade["event"] == "TRAIL"][["time", "newStop"]]
         .rename(columns={"time": "dt", "newStop": "nt_stop"})
         .dropna(subset=["nt_stop"]).sort_values("dt").reset_index(drop=True))
    r = (replica_traj[replica_traj["event"] == "TRAIL"][["dt", "stop"]]
         .rename(columns={"stop": "rep_stop"})
         .sort_values("dt").reset_index(drop=True))

    if a.empty and r.empty:
        return _empty_stop_diff()

    # Coerce keys to a single datetime unit even when a side is empty (object dtype):
    # merge_asof rejects object keys AND mismatched datetime resolutions (us vs s).
    a["dt"] = pd.to_datetime(a["dt"]).astype("datetime64[ns]")
    r["dt"] = pd.to_datetime(r["dt"]).astype("datetime64[ns]")
    union = pd.DataFrame(
        {"dt": pd.to_datetime(sorted(set(a["dt"]).union(set(r["dt"])))).astype("datetime64[ns]")}
    )
    m = pd.merge_asof(union, a, on="dt", direction="backward")
    m = pd.merge_asof(m, r, on="dt", direction="backward")
    m["diff_ticks"] = (m["nt_stop"] - m["rep_stop"]).abs() / tick_size
    # Either side still NaN (event before that side started) == a real divergence.
    m["match"] = (m["diff_ticks"] <= stop_tol_ticks).fillna(False)

    diffs = m["diff_ticks"].dropna()
    n = int(len(m))
    n_match = int(m["match"].sum())
    first_div = m.loc[~m["match"], "dt"].min() if (~m["match"]).any() else None
    return {
        "n_events": n,
        "n_match": n_match,
        "stop_match_rate": (n_match / n) if n else 1.0,
        "median_diff_ticks": float(diffs.median()) if len(diffs) else 0.0,
        "max_diff_ticks": float(diffs.max()) if len(diffs) else 0.0,
        "first_divergence_dt": first_div,
        "n_audit_events": int(len(a)),
        "n_replica_events": int(len(r)),
        "detail": m,
    }


def _empty_stop_diff() -> dict[str, Any]:
    return {
        "n_events": 0, "n_match": 0, "stop_match_rate": 1.0,
        "median_diff_ticks": 0.0, "max_diff_ticks": 0.0, "first_divergence_dt": None,
        "n_audit_events": 0, "n_replica_events": 0, "detail": pd.DataFrame(),
    }


def diff_trade_atr(
    audit_trade: pd.DataFrame,
    bars_with_atr: pd.DataFrame,
    *,
    rel_tol: float = 0.05,
) -> dict[str, Any]:
    """Compare NT's logged ``currentAtr`` at each TRAIL event against the Python ATR
    at the same bar. Directly tests the ATR-smoothing parity (Wilder vs SMA) at
    runtime — a high atr_match_rate confirms the replica's ``atr_mode`` is right."""
    a = (audit_trade[audit_trade["event"] == "TRAIL"][["time", "atr"]]
         .rename(columns={"time": "dt", "atr": "nt_atr"})
         .dropna(subset=["nt_atr"]).sort_values("dt").reset_index(drop=True))
    if a.empty:
        return {"n": 0, "atr_match_rate": None, "median_abs_diff": None, "median_rel_diff": None}
    b = (bars_with_atr[["dt", "atr"]].rename(columns={"atr": "py_atr"})
         .sort_values("dt").reset_index(drop=True))
    m = pd.merge_asof(a, b, on="dt", direction="backward")
    m["abs_diff"] = (m["nt_atr"] - m["py_atr"]).abs()
    m["rel_diff"] = m["abs_diff"] / m["nt_atr"].abs()
    match = (m["rel_diff"] <= rel_tol)
    n = int(len(m))
    return {
        "n": n,
        "atr_match_rate": float(match.mean()) if n else None,
        "median_abs_diff": float(m["abs_diff"].median()) if n else None,
        "median_rel_diff": float(m["rel_diff"].median()) if n else None,
    }


def parity_report(
    audit: Union[str, "pd.DataFrame"],
    bars: pd.DataFrame,
    cfg: NtAtrTrailConfig,
    *,
    entries: Optional[pd.DataFrame] = None,
    policy: str = "AtrTrail",
    stop_tol_ticks: float = 1.0,
    atr_rel_tol: float = 0.05,
    min_stop_match_rate: float = 0.95,
    max_median_diff_ticks: float = 1.0,
) -> dict[str, Any]:
    """Run the full backtest-leg parity check: parse the audit, replay the Python
    bar trajectory per trade, diff, and emit an overall pass/fail.

    ``bars``: dt-sorted OHLC frame (``dt/high/low/close``) — ATR is computed here via
    ``cfg.atr_mode``; must share the audit's timezone/naive-ness and bar timestamping.
    ``entries`` (optional): one row per trade with ``entry_dt``/``entry_price``/
    ``direction``, matched to audit trades by time order — the orchestrator sources
    these from the authoritative Trades.csv so the replay anchors on the true entry
    bar. If omitted, the anchor is taken from each trade's first audit event (exact
    for the live INIT row; approximate for the INIT-less bar-close path)."""
    audit_df = parse_stop_audit(audit)
    if policy:
        audit_df = audit_df[audit_df["policy"].str.lower() == policy.lower()].copy()

    work = bars.sort_values("dt").reset_index(drop=True).copy()
    if "atr" not in work.columns:
        work["atr"] = compute_atr(work, cfg.atr_period, cfg.atr_mode).values

    anchors = None
    if entries is not None and len(entries):
        anchors = entries.copy()
        anchors["entry_dt"] = pd.to_datetime(anchors["entry_dt"])
        anchors = anchors.sort_values("entry_dt").reset_index(drop=True)

    rows: list[dict[str, Any]] = []
    for i, (tid, grp) in enumerate(audit_df.groupby("trade_id", sort=True)):
        grp = grp.sort_values("time")
        if anchors is not None and i < len(anchors):
            entry_dt = anchors["entry_dt"].iloc[i]
            entry_price = float(anchors["entry_price"].iloc[i])
            direction = int(anchors["direction"].iloc[i])
        else:
            entry_dt = grp["time"].iloc[0]
            entry_price = float(grp["entry"].iloc[0])
            direction = int(grp["direction"].iloc[0])

        rep = replicate_nt_atr_trail_trajectory(
            entry_dt=entry_dt, entry_price=entry_price, direction=direction, bars=work, cfg=cfg,
        )
        sd = diff_trade_stop_trajectory(
            grp, rep["trajectory"], tick_size=cfg.tick_size, stop_tol_ticks=stop_tol_ticks,
        )
        ad = diff_trade_atr(grp, work, rel_tol=atr_rel_tol)
        rows.append({
            "trade_id": int(tid), "direction": direction,
            "entry_dt": entry_dt, "entry_price": entry_price,
            "n_events": sd["n_events"], "stop_match_rate": sd["stop_match_rate"],
            "median_diff_ticks": sd["median_diff_ticks"], "max_diff_ticks": sd["max_diff_ticks"],
            "first_divergence_dt": sd["first_divergence_dt"],
            "n_audit_events": sd["n_audit_events"], "n_replica_events": sd["n_replica_events"],
            "atr_match_rate": ad["atr_match_rate"], "atr_median_abs_diff": ad["median_abs_diff"],
        })

    summary = pd.DataFrame(rows)
    if summary.empty:
        return {"passed": False, "reason": "no_trades_in_audit", "n_trades": 0,
                "summary": summary, "atr_mode": cfg.atr_mode}

    # Weight per-trade match rate by event count for an honest overall number.
    tot_events = int(summary["n_events"].sum())
    overall_match = (
        float((summary["stop_match_rate"] * summary["n_events"]).sum() / tot_events)
        if tot_events else 1.0
    )
    worst_median = float(summary["median_diff_ticks"].max())
    passed = bool(
        (summary["stop_match_rate"] >= min_stop_match_rate).all()
        and (summary["median_diff_ticks"] <= max_median_diff_ticks).all()
    )
    return {
        "passed": passed,
        "n_trades": int(len(summary)),
        "overall_stop_match_rate": overall_match,
        "worst_trade_median_diff_ticks": worst_median,
        "total_events": tot_events,
        "atr_mode": cfg.atr_mode,
        "thresholds": {
            "min_stop_match_rate": min_stop_match_rate,
            "max_median_diff_ticks": max_median_diff_ticks,
            "stop_tol_ticks": stop_tol_ticks,
        },
        "summary": summary,
    }
