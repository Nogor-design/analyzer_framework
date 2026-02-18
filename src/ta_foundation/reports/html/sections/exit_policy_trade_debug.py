# src/ta_foundation/reports/html/sections/exit_policy_trade_debug.py
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple, Union

import pandas as pd
import matplotlib.pyplot as plt

from ta_foundation.core.model import AnalysisPackage
from ta_foundation.reports.html.embed import fig_to_base64_png

from ta_foundation.analysis.exits.simulate import (
    ExitSimConfig,
    simulate_exit_policies_for_run,
    simulate_exit_policy_for_trade_debug,
)
from ta_foundation.analysis.exits.policies import (
    TrailStopTargetPolicy,
    FixedStopTargetPolicy,
    AtrTrailPolicy,
    BreakEvenAtrTrailPolicy,
    ChandelierAtrTrailPolicy,
    FixedAtrThenAtrTrailPolicy,
    FixedAtrThenChandelierPolicy,
    TimeStopNoProgressPolicy,
)


def _h(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _find_col(df: pd.DataFrame, candidates: List[str]) -> Optional[str]:
    if df is None or df.empty:
        return None
    cols = list(df.columns)
    lower = {str(c).lower(): c for c in cols}
    for c in candidates:
        if c in cols:
            return c
        lc = str(c).lower()
        if lc in lower:
            return lower[lc]
    for cand in candidates:
        lc = str(cand).lower()
        for col in cols:
            if lc in str(col).lower():
                return col
    return None


def _infer_ic_from_trades(trades: pd.DataFrame) -> Optional[Tuple[str, str]]:
    inst_col = _find_col(trades, ["instrument", "Instrument"])
    if not inst_col:
        return None
    v = trades[inst_col].dropna()
    if v.empty:
        return None
    parts = str(v.iloc[0]).strip().split()
    if len(parts) >= 2:
        return parts[0], parts[1]
    return None


def _direction_sign(tr: pd.Series) -> int:
    for key in ("Market pos.", "market_pos", "Market position", "Market Position"):
        if key in tr.index and pd.notna(tr[key]):
            s = str(tr[key]).strip().lower()
            if s.startswith("short"):
                return -1
            if s.startswith("long"):
                return +1
    return +1


def _ensure_tz(ts: pd.Timestamp, tz: str = "America/Denver") -> pd.Timestamp:
    ts = pd.to_datetime(ts, errors="coerce")
    if pd.isna(ts):
        return ts
    if getattr(ts, "tzinfo", None) is None:
        return ts.tz_localize(tz)
    return ts.tz_convert(tz)


def _parse_trade_idx_option(v: Any) -> Union[int, str, None]:
    """
    Accept:
      - int
      - string int
      - "all"
    """
    if v is None:
        return None
    if isinstance(v, int):
        return v
    s = str(v).strip().lower()
    if s == "all":
        return "all"
    try:
        return int(s)
    except Exception:
        return None


def _make_policies(opts: Dict[str, Any]) -> List[Any]:
    """
    Build policy objects from ctx["options"].

    Supported keys in opts["policies"] (strings):
      - "trail"
      - "fixed_atr"
      - "atr_trail"
      - "be_atr_trail"
      - "chandelier_atr_trail"
      - "fixed_then_trail"         -> FixedAtrThenAtrTrailPolicy
      - "fixed_then_chandelier"    -> FixedAtrThenChandelierPolicy
      - "time_stop_no_progress"    -> TimeStopNoProgressPolicy

    IMPORTANT:
      - This function is the ONLY policy factory in this file. (No duplicates.)
      - The string list in opts["policies"] is treated case-insensitively.
    """
    selected = opts.get("policies", None)
    if selected is not None:
        if isinstance(selected, str):
            selected = [s.strip() for s in selected.split(",") if s.strip()]
        selected = [str(s).strip().lower() for s in (selected or [])]
    else:
        selected = ["trail", "fixed_atr", "atr_trail", "be_atr_trail", "chandelier_atr_trail"]

    # Shared defaults/overrides
    start_trail_ticks = float(opts.get("start_trail_ticks", 50))
    trail_amount = float(opts.get("trail_amount", 20))

    stop_ticks = float(opts.get("stop_ticks", 200))
    target_ticks = float(opts.get("target_ticks", 400))

    # Fixed ATR stop/target overrides
    fixed_stop_atr_mult = opts.get("fixed_stop_atr_mult", None)
    fixed_target_atr_mult = opts.get("fixed_target_atr_mult", None)

    # ATR trail
    trail_stop_atr_mult = float(opts.get("trail_stop_atr_mult", 1.0))
    trail_atr_mult = float(opts.get("trail_atr_mult", 1.25))

    # BE + ATR trail
    be_trigger_atr_mult = float(opts.get("be_trigger_atr_mult", 0.8))
    be_trigger_ticks = opts.get("be_trigger_ticks", None)
    be_offset_ticks = float(opts.get("be_offset_ticks", 4.0))
    be_trail_atr_mult = float(opts.get("be_trail_atr_mult", 1.25))

    # Chandelier
    chandelier_stop_atr_mult = float(opts.get("chandelier_stop_atr_mult", 1.0))
    chandelier_trail_atr_mult = float(opts.get("chandelier_trail_atr_mult", 1.75))

    # Fixed-then arm + trail
    fixed_then_arm_mfe_ticks = float(opts.get("fixed_then_arm_mfe_ticks", 40.0))
    fixed_then_stop_atr_mult = float(opts.get("fixed_then_stop_atr_mult", 1.0))
    fixed_then_trail_atr_mult = float(opts.get("fixed_then_trail_atr_mult", 1.25))

    # Fixed-then arm + chandelier
    fixed_then_chand_arm_mfe_ticks = float(opts.get("fixed_then_chand_arm_mfe_ticks", fixed_then_arm_mfe_ticks))
    fixed_then_chand_stop_atr_mult = float(opts.get("fixed_then_chand_stop_atr_mult", fixed_then_stop_atr_mult))
    fixed_then_chand_trail_atr_mult = float(opts.get("fixed_then_chand_trail_atr_mult", chandelier_trail_atr_mult))

    # Time stop no progress
    time_stop_minutes = float(opts.get("time_stop_minutes", 8))
    time_stop_min_mfe_ticks = float(opts.get("time_stop_min_mfe_ticks", 6.0))

    def mk_trail() -> TrailStopTargetPolicy:
        return TrailStopTargetPolicy(
            name="trail",
            start_trail_ticks=start_trail_ticks,
            trail_amount=trail_amount,
            stop_ticks=float(opts.get("trail_stop_ticks", stop_ticks)),
        )

    def mk_fixed_atr() -> FixedStopTargetPolicy:
        return FixedStopTargetPolicy(
            name="fixed_atr",
            stop_ticks=float(opts.get("fixed_stop_ticks", stop_ticks)),
            target_ticks=float(opts.get("fixed_target_ticks", target_ticks)),
            stop_atr_mult=(float(fixed_stop_atr_mult) if fixed_stop_atr_mult is not None else None),
            target_atr_mult=(float(fixed_target_atr_mult) if fixed_target_atr_mult is not None else None),
        )

    def mk_atr_trail() -> AtrTrailPolicy:
        return AtrTrailPolicy(
            name="atr_trail",
            stop_atr_mult=trail_stop_atr_mult,
            trail_atr_mult=trail_atr_mult,
            profit_target_atr_mult=None,
        )

    def mk_be_atr_trail() -> BreakEvenAtrTrailPolicy:
        return BreakEvenAtrTrailPolicy(
            name="be_atr_trail",
            stop_atr_mult=trail_stop_atr_mult,
            trail_atr_mult=be_trail_atr_mult,
            be_trigger_atr_mult=be_trigger_atr_mult,
            be_trigger_ticks=(float(be_trigger_ticks) if be_trigger_ticks is not None else None),
            be_offset_ticks=be_offset_ticks,
            profit_target_atr_mult=None,
        )

    def mk_chandelier() -> ChandelierAtrTrailPolicy:
        return ChandelierAtrTrailPolicy(
            name="chandelier_atr_trail",
            stop_atr_mult=chandelier_stop_atr_mult,
            trail_atr_mult=chandelier_trail_atr_mult,
            profit_target_atr_mult=None,
        )

    def mk_fixed_then_trail() -> FixedAtrThenAtrTrailPolicy:
        return FixedAtrThenAtrTrailPolicy(
            name="fixed_then_trail",
            stop_atr_mult=fixed_then_stop_atr_mult,
            trail_atr_mult=fixed_then_trail_atr_mult,
            arm_mfe_ticks=fixed_then_arm_mfe_ticks,
            profit_target_atr_mult=None,
        )

    def mk_fixed_then_chandelier() -> FixedAtrThenChandelierPolicy:
        return FixedAtrThenChandelierPolicy(
            name="fixed_then_chandelier",
            stop_atr_mult=fixed_then_chand_stop_atr_mult,
            trail_atr_mult=fixed_then_chand_trail_atr_mult,
            arm_mfe_ticks=fixed_then_chand_arm_mfe_ticks,
            profit_target_atr_mult=None,
        )

    def mk_time_stop_no_progress() -> TimeStopNoProgressPolicy:
        return TimeStopNoProgressPolicy(
            name="time_stop_no_progress",
            max_minutes=time_stop_minutes,
            min_mfe_ticks=time_stop_min_mfe_ticks,
        )

    builders = {
        "trail": mk_trail,
        "fixed_atr": mk_fixed_atr,
        "atr_trail": mk_atr_trail,
        "be_atr_trail": mk_be_atr_trail,
        "chandelier_atr_trail": mk_chandelier,
        "fixed_then_trail": mk_fixed_then_trail,
        "fixed_then_chandelier": mk_fixed_then_chandelier,
        "time_stop_no_progress": mk_time_stop_no_progress,
    }

    out: List[Any] = []
    for key in selected:
        k = str(key).strip().lower()
        if k in builders:
            out.append(builders[k]())
    return out


def render_exit_policy_trade_debug(ctx: Dict[str, Any]) -> str:
    """
    Debug plot for one trade OR all trades.

    Adds:
      - Second chart: PnL ticks (LAST-based) vs time from simulator trace
      - Stop lock-in shading: stop PnL >= 0 vs < 0 relative to entry
      - Option to render all trades: per-trade chart set
      - All-trades navigation index (anchors)

    Contract:
      - options come from ctx["options"]
      - market data from ctx["market"]
      - packages are AnalysisPackage objects (no reloads)
    """
    packages: Dict[str, AnalysisPackage] = ctx.get("packages", {}) or {}
    market = ctx.get("market")
    opts = ctx.get("options") or {}

    html: List[str] = []
    html.append("<div class='section'>")
    html.append("<div class='card'>")
    html.append("<h3>Exit Policy Trade Debug</h3>")

    if market is None:
        html.append("<div class='muted'>No market data loaded (ctx['market'] missing). Provide --market-data.</div>")
        html.append("</div></div>")
        return "\n".join(html)

    run_id = opts.get("run_id")
    if run_id is None:
        html.append("<div class='muted'>Missing section option: <code>run_id</code></div>")
        html.append("</div></div>")
        return "\n".join(html)

    pkg = packages.get(str(run_id))
    if pkg is None:
        html.append(f"<div class='muted'>run_id not found in packages: <code>{_h(str(run_id))}</code></div>")
        html.append("</div></div>")
        return "\n".join(html)

    trades = getattr(pkg, "trades", None)
    if trades is None or trades.empty:
        html.append("<div class='muted'>No trades dataframe in this package.</div>")
        html.append("</div></div>")
        return "\n".join(html)

    ic = _infer_ic_from_trades(trades)
    if not ic:
        html.append("<div class='muted'>Could not infer instrument/contract from trades. (Missing Instrument column?)</div>")
        html.append("</div></div>")
        return "\n".join(html)
    instrument, contract = ic

    entry_dt_col = _find_col(trades, ["Entry time", "Entry Time", "entry_time", "entry_dt"])
    exit_dt_col = _find_col(trades, ["Exit time", "Exit Time", "exit_time", "exit_dt"])
    entry_px_col = _find_col(trades, ["Entry price", "Entry Price", "entry_price"])
    exit_px_col = _find_col(trades, ["Exit price", "Exit Price", "exit_price"])
    if not entry_dt_col or not entry_px_col:
        html.append("<div class='muted'>Trades missing required columns for plotting (Entry time / Entry price).</div>")
        html.append("</div></div>")
        return "\n".join(html)

    # ---- Options ----
    show_minutes = bool(opts.get("show_minutes", True))
    candle_tf = str(opts.get("candle_tf", "1m"))
    pad_seconds = int(opts.get("pad_seconds", 30))

    tick_size = float(opts.get("tick_size", 0.25))
    atr_tf = str(opts.get("atr_tf", "5m"))
    atr_period = int(opts.get("atr_period", 14))
    bounded = bool(opts.get("bounded_to_original_exit", True))
    max_minutes = int(opts.get("max_minutes_unbounded", 180))
    use_bid_ask = bool(opts.get("use_bid_ask_triggers", True))

    # Trace / overlays
    overlay_stops = bool(opts.get("overlay_stops", True))
    overlay_targets = bool(opts.get("overlay_targets", True))
    overlay_be_arm = bool(opts.get("overlay_be_arm", True))
    show_trace_events = bool(opts.get("show_trace_events", True))
    stop_alpha = float(opts.get("stop_alpha", 0.85))
    stop_lw = float(opts.get("stop_linewidth", 1.2))

    # PnL chart controls
    show_pnl_chart = bool(opts.get("show_pnl_chart", True))
    pnl_lockin_shading = bool(opts.get("pnl_lockin_shading", True))
    pnl_chart_height = float(opts.get("pnl_chart_height", 3.4))

    # All-trades mode
    trade_idx_opt = _parse_trade_idx_option(opts.get("trade_idx"))
    run_all_trades = bool(opts.get("run_all_trades", False)) or (trade_idx_opt == "all")

    # Limits for all-trades mode
    max_trades = opts.get("max_trades", None)
    start_trade_idx = int(opts.get("start_trade_idx", 0))
    if max_trades is not None:
        try:
            max_trades = int(max_trades)
        except Exception:
            max_trades = None

    # Index controls
    show_index = bool(opts.get("show_index", True))
    index_show_actual_pnl = bool(opts.get("index_show_actual_pnl", True))

    # Trace sampling: default higher sampling if rendering all trades
    default_trace_every_n = 5 if run_all_trades else 1
    trace_every_n = int(opts.get("trace_every_n", default_trace_every_n))

    policies = _make_policies(opts)

    sim_cfg = ExitSimConfig(
        tick_size=tick_size,
        atr_tf=atr_tf,
        atr_period=atr_period,
        bounded_to_original_exit=bounded,
        max_minutes_unbounded=max_minutes,
        use_bid_ask_triggers=use_bid_ask,
        trace_every_n=trace_every_n,
    )

    ticks_all = market.get_ticks(instrument, contract)
    if ticks_all is None or ticks_all.empty:
        html.append(f"<div class='muted'>No ticks loaded for {instrument} {contract}.</div>")
        html.append("</div></div>")
        return "\n".join(html)

    ticks_all = ticks_all.copy()
    ticks_all["dt"] = pd.to_datetime(ticks_all["dt"], errors="coerce")
    if getattr(ticks_all["dt"].dt, "tz", None) is None:
        ticks_all["dt"] = ticks_all["dt"].dt.tz_localize("America/Denver")

    # Bars for ATR-at-entry (same as simulator)
    bars = market.get_bars(instrument, contract, timeframe=atr_tf, source="ticks")
    atr_bars = None
    if bars is not None and not bars.empty and "dt" in bars.columns:
        bars = bars.copy()
        bars["dt"] = pd.to_datetime(bars["dt"], errors="coerce")
        if getattr(bars["dt"].dt, "tz", None) is None:
            bars["dt"] = bars["dt"].dt.tz_localize("America/Denver")
        bars = bars.sort_values("dt").reset_index(drop=True)
        try:
            from ta_foundation.analysis.features.regime import atr_wilder

            bars["atr"] = atr_wilder(bars, period=atr_period)
            atr_bars = bars.dropna(subset=["atr"])
        except Exception:
            atr_bars = None

    # Run-level sim table (for per-trade summary rows table)
    sim_all = simulate_exit_policies_for_run(
        run_id=str(run_id),
        trades=trades,
        market=market,
        instrument=instrument,
        contract=contract,
        policies=policies,
        cfg=sim_cfg,
    )

    def _atr_at_entry(entry_dt: pd.Timestamp) -> float:
        override = float(opts.get("atr_entry_override", 0.0)) if opts.get("atr_entry_override") is not None else 0.0
        if override > 0:
            return float(override)
        if atr_bars is None or atr_bars.empty:
            return 0.0
        b = atr_bars[atr_bars["dt"] <= entry_dt]
        if b.empty:
            return 0.0
        return float(b["atr"].iloc[-1])

    def _trade_anchor(trade_idx: int) -> str:
        return f"trade-{trade_idx}"

    def _actual_pnl_ticks(trade_idx: int) -> Optional[float]:
        """
        Prefer the simulator "actual" row if present (direction-correct).
        Fallback: compute from trades entry/exit if available.
        """
        try:
            if sim_all is not None and not sim_all.empty and "trade_idx" in sim_all.columns and "policy" in sim_all.columns:
                s = sim_all[(sim_all["trade_idx"] == trade_idx) & (sim_all["policy"] == "actual")]
                if not s.empty and "pnl_ticks" in s.columns and pd.notna(s["pnl_ticks"].iloc[0]):
                    return float(s["pnl_ticks"].iloc[0])
        except Exception:
            pass

        if exit_px_col is None:
            return None
        tr = trades.iloc[trade_idx]
        if pd.isna(tr.get(exit_px_col)):
            return None
        direction = _direction_sign(tr)
        entry_px = float(tr[entry_px_col])
        exit_px = float(tr[exit_px_col])
        return float(((exit_px - entry_px) * direction) / float(tick_size))

    def _render_index(idxs: List[int]) -> str:
        if not show_index or not idxs:
            return ""

        parts: List[str] = []
        parts.append("<div style='margin:10px 0 14px 0;'>")
        parts.append("<b>Trade Index</b>")
        parts.append("<div class='muted' style='margin-top:4px;'>Click a trade to jump to its charts.</div>")
        parts.append("<div style='display:flex; flex-wrap:wrap; gap:6px; margin-top:8px;'>")

        for i in idxs:
            a = _trade_anchor(i)
            pnl = _actual_pnl_ticks(i) if index_show_actual_pnl else None

            label = f"{i}"
            title = f"trade_idx={i}"
            if pnl is not None:
                label = f"{i} ({pnl:.0f}t)"
                title = f"trade_idx={i} actual_pnl_ticks={pnl:.2f}"

            parts.append(
                f"<a href='#{_h(a)}' title='{_h(title)}' "
                "style='display:inline-block; padding:4px 8px; border:1px solid rgba(0,0,0,0.15); "
                "border-radius:999px; text-decoration:none;'>"
                f"{_h(label)}</a>"
            )

        parts.append("</div>")
        parts.append("</div>")
        return "\n".join(parts)

    def _render_one_trade(trade_idx: int) -> str:
        if trade_idx < 0 or trade_idx >= len(trades):
            return f"<div class='muted'>trade_idx out of range: {trade_idx} (trades={len(trades)})</div>"

        tr = trades.iloc[trade_idx]

        entry_dt = _ensure_tz(tr[entry_dt_col])
        exit_dt = _ensure_tz(tr[exit_dt_col]) if exit_dt_col and pd.notna(tr[exit_dt_col]) else pd.NaT
        entry_px = float(tr[entry_px_col])
        exit_px = float(tr[exit_px_col]) if exit_px_col and pd.notna(tr[exit_px_col]) else None
        direction = _direction_sign(tr)
        side = "LONG" if direction > 0 else "SHORT"

        end_dt = exit_dt if (pd.notna(exit_dt) and bounded) else (entry_dt + pd.Timedelta(minutes=max_minutes))
        start_dt = entry_dt - pd.Timedelta(seconds=pad_seconds)
        end_dt2 = end_dt + pd.Timedelta(seconds=pad_seconds)

        w = ticks_all[(ticks_all["dt"] >= start_dt) & (ticks_all["dt"] <= end_dt2)]
        if w.empty:
            return (
                f"<div id='{_h(_trade_anchor(trade_idx))}' style='margin-top:14px;'>"
                f"<div class='muted'>No tick rows in window for trade_idx={trade_idx}. "
                f"entry={_h(str(entry_dt))} end={_h(str(end_dt))}</div></div>"
            )

        atr_entry = _atr_at_entry(entry_dt)

        traces_by_policy: Dict[str, pd.DataFrame] = {}
        if atr_entry > 0 and overlay_stops:
            for p in policies:
                _, trace = simulate_exit_policy_for_trade_debug(
                    ticks=w,
                    entry_dt=entry_dt,
                    entry_price=entry_px,
                    direction=direction,
                    atr_entry=float(atr_entry),
                    policy=p,
                    cfg=sim_cfg,
                )
                traces_by_policy[getattr(p, "name", p.__class__.__name__)] = trace

        sim_trade = (
            sim_all[(sim_all["trade_idx"] == trade_idx)].copy()
            if (sim_all is not None and not sim_all.empty and "trade_idx" in sim_all.columns)
            else pd.DataFrame()
        )

        out: List[str] = []
        out.append(f"<div id='{_h(_trade_anchor(trade_idx))}' style='margin-top:18px;'>")
        out.append(
            f"<div class='muted'>Instrument: <b>{_h(instrument)} { _h(contract) }</b> • "
            f"trade_idx: <b>{trade_idx}</b> • side: <b>{side}</b> • window ticks: <b>{len(w):,}</b> • "
            f"entry: <b>{_h(str(entry_dt))}</b> → end: <b>{_h(str(end_dt))}</b> • atr_entry: <b>{atr_entry:.4f}</b> "
            f"• <a href='#top' style='text-decoration:none;'>↑ top</a></div>"
        )

        # ---- Chart 1: Price + stops/targets/BE/exit ----
        fig = plt.figure(figsize=(12, 5.2))
        ax = fig.add_subplot(111)

        if "last" in w.columns:
            ax.plot(w["dt"], w["last"], label="tick last", linewidth=1.0)
        else:
            if "bid" in w.columns and "ask" in w.columns:
                ax.plot(w["dt"], (w["bid"] + w["ask"]) / 2.0, label="tick mid", linewidth=1.0)

        if show_minutes:
            try:
                mb = market.get_bars(instrument, contract, timeframe=candle_tf, source="minute")
                if mb is None or mb.empty:
                    mb = market.get_bars(instrument, contract, timeframe=candle_tf, source="ticks")
                if mb is not None and not mb.empty and "dt" in mb.columns and "close" in mb.columns:
                    mb = mb.copy()
                    mb["dt"] = pd.to_datetime(mb["dt"], errors="coerce")
                    if getattr(mb["dt"].dt, "tz", None) is None:
                        mb["dt"] = mb["dt"].dt.tz_localize("America/Denver")
                    mbw = mb[(mb["dt"] >= start_dt) & (mb["dt"] <= end_dt2)]
                    if not mbw.empty:
                        ax.plot(mbw["dt"], mbw["close"], label=f"{candle_tf} close", linestyle="--", linewidth=1.0)
            except Exception:
                pass

        ax.axhline(entry_px, linestyle=":", label="entry")
        if exit_px is not None:
            ax.axhline(exit_px, linestyle=":", label="actual exit")

        if overlay_stops and traces_by_policy:
            for name, trc in traces_by_policy.items():
                if trc is None or trc.empty or "dt" not in trc.columns:
                    continue
                if "stop_price" in trc.columns and trc["stop_price"].notna().any():
                    ax.step(trc["dt"], trc["stop_price"], where="post", linewidth=stop_lw, alpha=stop_alpha, label=f"{name} stop")
                if overlay_targets and "target_price" in trc.columns and trc["target_price"].notna().any():
                    ax.step(trc["dt"], trc["target_price"], where="post", linewidth=1.0, alpha=0.6, label=f"{name} target")
                if overlay_be_arm and "be_armed_now" in trc.columns:
                    arms = trc[trc["be_armed_now"] == True]
                    if not arms.empty and "stop_price" in arms.columns:
                        ax.scatter(arms["dt"], arms["stop_price"], s=35, label=f"{name} BE arm")

        if not sim_trade.empty and "exit_dt" in sim_trade.columns and "exit_price" in sim_trade.columns:
            for _, r in sim_trade.iterrows():
                if pd.isna(r.get("exit_dt")) or pd.isna(r.get("exit_price")):
                    continue
                ax.scatter([r["exit_dt"]], [r["exit_price"]], s=55, label=f"{r.get('policy')} exit ({r.get('exit_reason')})")

        ax.set_title(f"{run_id} • {instrument} {contract} • trade_idx={trade_idx} • {side}")
        ax.set_xlabel("dt")
        ax.set_ylabel("price")
        ax.grid(True)
        ax.legend(loc="best", fontsize=8, ncol=2)

        uri = fig_to_base64_png(fig)
        plt.close(fig)
        out.append(f"<img src='{uri}' style='max-width:100%; border-radius:10px;'/>")

        # ---- Chart 2: PnL ticks (trace) + stop lock-in shading ----
        if show_pnl_chart and traces_by_policy:
            fig2 = plt.figure(figsize=(12, pnl_chart_height))
            ax2 = fig2.add_subplot(111)

            pnl_policy_name = str(opts.get("pnl_policy", "")).strip()
            if pnl_policy_name and pnl_policy_name in traces_by_policy:
                label_base = pnl_policy_name
                trc = traces_by_policy[pnl_policy_name]
            else:
                label_base, trc = next(iter(traces_by_policy.items()))

            if trc is not None and not trc.empty and "dt" in trc.columns and "pnl_ticks_last" in trc.columns:
                ax2.plot(trc["dt"], trc["pnl_ticks_last"], label=f"{label_base} pnl_ticks_last", linewidth=1.0)
                ax2.axhline(0.0, linestyle=":", label="breakeven")

                if pnl_lockin_shading and "stop_price" in trc.columns and trc["stop_price"].notna().any():
                    stop_pnl_ticks = ((trc["stop_price"] - entry_px) * direction) / float(tick_size)
                    profit_mask = stop_pnl_ticks >= 0
                    loss_mask = stop_pnl_ticks < 0
                    ax2.fill_between(trc["dt"], 0.0, stop_pnl_ticks, where=profit_mask, alpha=0.15, label="stop lock-in (profit)")
                    ax2.fill_between(trc["dt"], 0.0, stop_pnl_ticks, where=loss_mask, alpha=0.15, label="stop lock-in (loss)")

                if "exit_now" in trc.columns and trc["exit_now"].any():
                    ex = trc[trc["exit_now"] == True]
                    ax2.scatter(ex["dt"], ex["pnl_ticks_last"], s=45, label="exit (trace)")

                if overlay_be_arm and "be_armed_now" in trc.columns and trc["be_armed_now"].any():
                    arms = trc[trc["be_armed_now"] == True]
                    ax2.scatter(arms["dt"], arms["pnl_ticks_last"], s=35, label="BE arm (trace)")

            ax2.set_title(f"PnL ticks vs time (trace) • trade_idx={trade_idx}")
            ax2.set_xlabel("dt")
            ax2.set_ylabel("pnl_ticks_last")
            ax2.grid(True)
            ax2.legend(loc="best", fontsize=8, ncol=2)

            uri2 = fig_to_base64_png(fig2)
            plt.close(fig2)
            out.append(f"<img src='{uri2}' style='max-width:100%; border-radius:10px; margin-top:10px;'/>")

        # Sim rows table for this trade
        if not sim_trade.empty:
            cols = [
                c
                for c in ["policy", "exit_reason", "exit_dt", "exit_price", "pnl_ticks", "mfe_ticks", "mae_ticks", "be_armed", "stop_at_arm"]
                if c in sim_trade.columns
            ]
            if cols:
                out.append("<div style='margin-top:10px;'>")
                out.append("<b>Simulation rows (this trade)</b>")
                out.append("<table class='table'>")
                out.append("<tr>" + "".join([f"<th>{_h(c)}</th>" for c in cols]) + "</tr>")
                for _, r in sim_trade[cols].iterrows():
                    out.append("<tr>" + "".join([f"<td>{_h(str(r[c]))}</td>" for c in cols]) + "</tr>")
                out.append("</table>")
                out.append("</div>")

        # Trace events table
        if show_trace_events and traces_by_policy:
            out.append("<div style='margin-top:10px;'>")
            out.append("<b>Trace events (stop moves / BE arm / exits)</b>")
            out.append("<table class='table'>")
            out.append("<tr><th>policy</th><th>dt</th><th>stop_price</th><th>event</th></tr>")
            for name, trc in traces_by_policy.items():
                if trc is None or trc.empty:
                    continue
                ev = trc[(trc.get("stop_changed") == True) | (trc.get("be_armed_now") == True) | (trc.get("exit_now") == True)].copy()
                if ev.empty:
                    continue
                for _, r in ev.iterrows():
                    event = "stop_move" if bool(r.get("stop_changed")) else ""
                    if bool(r.get("be_armed_now")):
                        event = (event + "|be_arm").strip("|")
                    if bool(r.get("exit_now")):
                        event = (event + f"|exit:{r.get('exit_reason')}").strip("|")
                    out.append(
                        "<tr>"
                        f"<td>{_h(str(name))}</td>"
                        f"<td>{_h(str(r.get('dt')))}</td>"
                        f"<td>{_h(str(r.get('stop_price')))}</td>"
                        f"<td>{_h(event)}</td>"
                        "</tr>"
                    )
            out.append("</table>")
            out.append("</div>")

        out.append("</div>")  # anchor wrapper
        return "\n".join(out)

    # ---- Render one trade or many trades ----
    html.append("<div id='top'></div>")

    if run_all_trades:
        end_idx = len(trades)
        idxs = list(range(start_trade_idx, end_idx))
        if max_trades is not None:
            idxs = idxs[: max(0, max_trades)]

        html.append(
            "<div class='muted'>All-trades mode enabled. "
            f"Rendering {len(idxs)} trade(s) starting at trade_idx={start_trade_idx}. "
            f"(trace_every_n={trace_every_n})</div>"
        )

        html.append(_render_index(idxs))

        for i in idxs:
            html.append(_render_one_trade(i))
    else:
        if trade_idx_opt is None or isinstance(trade_idx_opt, str):
            html.append(
                "<div class='muted'>Missing/invalid <code>trade_idx</code>. "
                "Provide an int, or set <code>run_all_trades: true</code>.</div>"
            )
            html.append("</div></div>")
            return "\n".join(html)
        html.append(_render_one_trade(int(trade_idx_opt)))

    html.append("</div>")  # card
    html.append("</div>")  # section
    return "\n".join(html)
