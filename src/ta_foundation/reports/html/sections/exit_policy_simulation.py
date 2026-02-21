# src/ta_foundation/reports/html/sections/exit_policy_simulation.py
from __future__ import annotations

from typing import Any, Dict, List, Optional
import re
from collections import Counter

import pandas as pd
import matplotlib.pyplot as plt

from ta_foundation.core.model import AnalysisPackage
from ta_foundation.reports.html.embed import fig_to_base64_png

from ta_foundation.analysis.exits.policies import (
    TrailStopTargetPolicy,
    FixedStopTargetPolicy,
    AtrTrailPolicy,
    BreakEvenAtrTrailPolicy,
    ChandelierAtrTrailPolicy,
    TimeStopNoProgressPolicy,
    GivebackAfterMfePolicy,
    FixedAtrThenAtrTrailPolicy,
    FixedAtrThenChandelierPolicy,
)
from ta_foundation.analysis.exits.simulate import ExitSimConfig, simulate_exit_policies_for_run


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
    # substring fallback
    for cand in candidates:
        lc = str(cand).lower()
        for col in cols:
            if lc in str(col).lower():
                return col
    return None


def _render_table(df: pd.DataFrame, max_rows: int = 50) -> str:
    if df is None or df.empty:
        return "<div class='muted'>No data.</div>"
    d = df.head(max_rows).copy()
    out = ["<table class='table'>"]
    out.append("<tr>" + "".join([f"<th>{_h(str(c))}</th>" for c in d.columns]) + "</tr>")
    for _, r in d.iterrows():
        out.append("<tr>" + "".join([f"<td>{_h(str(r[c]))}</td>" for c in d.columns]) + "</tr>")
    out.append("</table>")
    return "\n".join(out)


def _matches(rx: Optional[str], text: str) -> bool:
    if not rx:
        return True
    try:
        return re.search(rx, text) is not None
    except Exception:
        return True


def _infer_ic_from_trades(trades: pd.DataFrame) -> Optional[tuple[str, str]]:
    """
    Match exit_policy_trade_debug behavior: infer instrument/contract from Trades["Instrument"].
    """
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


def _coerce_bool(v: Any, default: bool) -> bool:
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in ("1", "true", "yes", "y", "on"):
        return True
    if s in ("0", "false", "no", "n", "off"):
        return False
    return default


def _make_policies(opts: Dict[str, Any]) -> List[Any]:
    """
    Single policy factory (aligned with exit_policy_trade_debug).

    opts["policies"] accepts:
      - list[str]
      - comma-separated string

    Supported strings:
      - "trail"
      - "fixed_atr"
      - "atr_trail"
      - "be_atr_trail"
      - "chandelier_atr_trail"
      - "fixed_then_trail"         -> FixedAtrThenAtrTrailPolicy
      - "fixed_then_chandelier"    -> FixedAtrThenChandelierPolicy
      - "time_stop_no_progress"    -> TimeStopNoProgressPolicy
      - "giveback_after_mfe"       -> GivebackAfterMfePolicy
      - "nt8_dynamic"              -> bundle approximating your new NT8 DynamicStop stack

    Default selection (if not provided) mirrors debug:
      ["trail","fixed_atr","atr_trail","be_atr_trail","chandelier_atr_trail"]
    """
    selected = opts.get("policies", None) or opts.get("policy_set", None)
    if selected is not None:
        if isinstance(selected, str):
            selected = [s.strip() for s in selected.split(",") if s.strip()]
        selected = [str(s).strip().lower() for s in (selected or [])]
    else:
        selected = ["trail", "fixed_atr", "atr_trail", "be_atr_trail", "chandelier_atr_trail"]

    # Shared defaults/overrides (keep names consistent with trade_debug)
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

    # Giveback
    giveback_arm_mfe_ticks = float(opts.get("giveback_arm_mfe_ticks", 18.0))
    giveback_ticks = float(opts.get("giveback_ticks", 10.0))

    # --- NT8 DynamicStop bundle (MaxStop + LockIn + Giveback + optional Trail)
    # These are expressed in *ticks* (not ATR) to match your NinjaTrader strategy behavior.
    nt8_max_stop_ticks = float(opts.get("nt8_max_stop_ticks", stop_ticks))

    nt8_use_lockin = _coerce_bool(opts.get("nt8_use_lockin", True), True)
    nt8_lockin_trigger_ticks = float(opts.get("nt8_lockin_trigger_ticks", 25.0))
    nt8_lockin_plus_ticks = float(opts.get("nt8_lockin_plus_ticks", 3.0))

    nt8_use_giveback = _coerce_bool(opts.get("nt8_use_giveback", True), True)
    nt8_giveback_start_ticks = float(opts.get("nt8_giveback_start_ticks", 70.0))
    nt8_giveback_ticks = float(opts.get("nt8_giveback_ticks", 25.0))

    nt8_use_trail = _coerce_bool(opts.get("nt8_use_trail", False), False)
    nt8_trail_start_ticks = float(opts.get("nt8_trail_start_ticks", 30.0))
    nt8_trail_distance_ticks = float(opts.get("nt8_trail_distance_ticks", 12.0))

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

    def mk_giveback_after_mfe() -> GivebackAfterMfePolicy:
        return GivebackAfterMfePolicy(
            name="giveback_after_mfe",
            arm_mfe_ticks=giveback_arm_mfe_ticks,
            giveback_ticks=giveback_ticks,
        )

    def mk_nt8_dynamic_bundle() -> List[Any]:
        """\
        Approximate your NT8 DynamicStop stack using existing tick-based policies:

        - MaxStop: Fixed stop, huge target (effectively stop-only)
        - LockIn: modelled as GivebackAfterMfe with (arm = LockInTriggerTicks) and
                  (giveback = LockInTriggerTicks - LockInPlusTicks)
                  This sets the stop to entry+plus at arming, and then ratchets with MFE.
        - Giveback: GivebackAfterMfe with (arm = GivebackStartTicks, giveback = GivebackTicks)
        - Optional Trail: TrailStopTargetPolicy (close-based trail), if enabled

        Notes:
          * This is a close approximation for research/comparison. Your NT8 code uses
            order-state constraints and may clamp stop vs. market; slippage isn't modelled here.
        """
        out: List[Any] = []

        out.append(
            FixedStopTargetPolicy(
                name="nt8_maxstop",
                stop_ticks=nt8_max_stop_ticks,
                target_ticks=float(opts.get("nt8_target_ticks", 1e9)),
                stop_atr_mult=None,
                target_atr_mult=None,
            )
        )

        if nt8_use_lockin:
            # Lock-in is "arm at trigger, stop to entry + plus".
            # Using GivebackAfterMfe: stop = entry + (MFE - giveback).
            # At arming MFE≈trigger => stop = entry + plus when giveback = trigger - plus.
            giveback_for_lock = max(1.0, nt8_lockin_trigger_ticks - nt8_lockin_plus_ticks)
            out.append(
                GivebackAfterMfePolicy(
                    name="nt8_lockin",
                    arm_mfe_ticks=nt8_lockin_trigger_ticks,
                    giveback_ticks=giveback_for_lock,
                )
            )

        if nt8_use_giveback:
            out.append(
                GivebackAfterMfePolicy(
                    name="nt8_giveback",
                    arm_mfe_ticks=nt8_giveback_start_ticks,
                    giveback_ticks=nt8_giveback_ticks,
                )
            )

        if nt8_use_trail:
            out.append(
                TrailStopTargetPolicy(
                    name="nt8_trail",
                    start_trail_ticks=nt8_trail_start_ticks,
                    trail_amount=nt8_trail_distance_ticks,
                    stop_ticks=nt8_max_stop_ticks,
                )
            )

        return out

    builders = {
        "trail": mk_trail,
        "fixed_atr": mk_fixed_atr,
        "atr_trail": mk_atr_trail,
        "be_atr_trail": mk_be_atr_trail,
        "chandelier_atr_trail": mk_chandelier,
        "fixed_then_trail": mk_fixed_then_trail,
        "fixed_then_chandelier": mk_fixed_then_chandelier,
        "time_stop_no_progress": mk_time_stop_no_progress,
        "giveback_after_mfe": mk_giveback_after_mfe,
        "nt8_dynamic": mk_nt8_dynamic_bundle,
    }

    out: List[Any] = []
    for key in selected:
        k = str(key).strip().lower()
        if k not in builders:
            continue
        built = builders[k]()
        if isinstance(built, list):
            out.extend(built)
        else:
            out.append(built)
    return out


def render_exit_policy_simulation(ctx: Dict[str, Any]) -> str:
    """
    Exit policy simulation across runs (tick-path).

    Contracts:
      - options from ctx["options"]
      - market from ctx["market"]
      - packages are AnalysisPackage objects (no reloads)
    """
    packages: Dict[str, AnalysisPackage] = ctx.get("packages", {}) or {}
    market = ctx.get("market")
    opts = ctx.get("options") or {}

    css = """
    <style>
      .tf-exit-sim { display:flex; flex-direction:column; gap:14px; }
      .tf-exit-card { border-radius:16px; padding:12px 14px; background:rgba(255,255,255,0.035); border:1px solid rgba(255,255,255,0.06); }
      .tf-exit-title { font-weight:900; font-size:1.02rem; margin-bottom:6px; }
      .tf-exit-sub { opacity:0.82; font-size:0.86rem; }
      .tf-exit-grid { display:grid; grid-template-columns: 1fr; gap:12px; margin-top:10px; }
      .tf-exit-img { width:100%; height:auto; display:block; border-radius:10px; }
      .tf-exit-kv { display:grid; grid-template-columns: 240px 1fr; gap:6px 12px; font-size:0.9rem; margin-top:8px; }
      .muted { opacity:0.75; }
      code { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace; }
      .table { border-collapse: collapse; width:100%; }
      .table th, .table td { border-bottom: 1px solid rgba(255,255,255,0.08); padding: 6px 8px; text-align:left; }
    </style>
    """

    html: List[str] = [css, "<div class='tf-exit-sim'>"]

    if market is None:
        html.append(
            "<div class='tf-exit-card'><div class='muted'>No market store found in report context (<code>market</code>). Provide --market-data.</div></div>"
        )
        html.append("</div>")
        return "\n".join(html)

    # Which runs to show
    top_n = int(opts.get("top_n_runs", 12))
    min_trades = int(opts.get("min_trades", 50))
    include_regex = opts.get("include_run_id_regex")
    exclude_regex = opts.get("exclude_run_id_regex")

    # Tick settings
    tick_size = float(opts.get("tick_size", 0.25))  # NQ default
    atr_tf = str(opts.get("atr_tf", "5m"))
    atr_period = int(opts.get("atr_period", 14))

    bounded = _coerce_bool(opts.get("bounded_to_original_exit", True), True)
    max_minutes = int(opts.get("max_minutes_unbounded", 180))
    use_bid_ask = _coerce_bool(opts.get("use_bid_ask_triggers", True), True)

    policies = _make_policies(opts)

    sim_cfg = ExitSimConfig(
        tick_size=tick_size,
        atr_tf=atr_tf,
        atr_period=atr_period,
        bounded_to_original_exit=bounded,
        max_minutes_unbounded=max_minutes,
        use_bid_ask_triggers=use_bid_ask,
    )

    instr_opt = opts.get("instrument")
    contract_opt = opts.get("contract")

    # Determine a target instrument/contract to PROBE ticks for diagnostics
    probe_ic = None
    if instr_opt and contract_opt:
        probe_ic = (str(instr_opt).strip(), str(contract_opt).strip())
    else:
        for _, pkg in packages.items():
            tr = getattr(pkg, "trades", None)
            if tr is not None and not tr.empty:
                probe_ic = _infer_ic_from_trades(tr)
                if probe_ic:
                    break

    probe_ticks_rows = None
    probe_ic_str = None
    if probe_ic:
        pi, pc = probe_ic
        probe_ic_str = f"{pi} {pc}"
        try:
            _ticks_probe = market.get_ticks(pi, pc)
            probe_ticks_rows = int(len(_ticks_probe)) if _ticks_probe is not None else 0
        except Exception:
            probe_ticks_rows = None

    # Header card
    html.append("<div class='tf-exit-card'>")
    html.append("<div class='tf-exit-title'>Exit Policy Simulation (tick-path, results in ticks)</div>")
    html.append(
        f"<div class='tf-exit-sub'>ATR: { _h(atr_tf) } x {atr_period} • tick_size: {tick_size} • "
        f"{'bounded to original exit' if bounded else f'unbounded max {max_minutes}m'} • "
        f"{'bid/ask triggers' if use_bid_ask else 'last-price triggers'}</div>"
    )
    html.append("<div class='tf-exit-kv'>")
    html.append(f"<div class='muted'>Packages (runs)</div><div>{len(packages):,}</div>")
    html.append(
        f"<div class='muted'>Tick probe</div><div>{_h(probe_ic_str) if probe_ic_str else '—'} • rows: <b>{probe_ticks_rows if probe_ticks_rows is not None else '—'}</b></div>"
    )
    html.append(f"<div class='muted'>min_trades / top_n</div><div>{min_trades} / {top_n}</div>")
    html.append(
        f"<div class='muted'>Policies</div><div><code>{_h(', '.join([getattr(p, 'name', p.__class__.__name__) for p in policies]))}</code></div>"
    )
    if include_regex:
        html.append(f"<div class='muted'>include regex</div><div><code>{_h(str(include_regex))}</code></div>")
    if exclude_regex:
        html.append(f"<div class='muted'>exclude regex</div><div><code>{_h(str(exclude_regex))}</code></div>")
    html.append("</div>")
    html.append("</div>")

    # Rank runs by trade count, track skip reasons
    skip = Counter()
    run_rows = []

    for run_id, pkg in packages.items():
        if not _matches(include_regex, run_id):
            skip["FILTERED_BY_INCLUDE_REGEX"] += 1
            continue
        if exclude_regex and _matches(exclude_regex, run_id):
            skip["FILTERED_BY_EXCLUDE_REGEX"] += 1
            continue

        trades = getattr(pkg, "trades", None)
        if trades is None or trades.empty:
            skip["NO_TRADES"] += 1
            continue

        n = len(trades)
        if n < min_trades:
            skip["MIN_TRADES"] += 1
            continue

        ic = _infer_ic_from_trades(trades)
        if not ic:
            skip["BAD_INSTRUMENT_FORMAT"] += 1
            continue

        if instr_opt and contract_opt:
            if str(ic[0]).strip() != str(instr_opt).strip() or str(ic[1]).strip() != str(contract_opt).strip():
                skip["NOT_TARGET_INSTRUMENT_CONTRACT"] += 1
                continue

        run_rows.append((run_id, n))

    run_rows.sort(key=lambda x: x[1], reverse=True)
    run_rows = run_rows[:top_n]

    if not run_rows:
        html.append("<div class='tf-exit-card'>")
        html.append("<div class='tf-exit-title'>No runs produced exit simulations</div>")
        html.append("<div class='muted'>Skip summary:</div>")
        if skip:
            df = pd.DataFrame([{"reason": k, "count": int(v)} for k, v in skip.most_common()])
            html.append(_render_table(df, max_rows=50))
        else:
            html.append("<div class='muted'>No skip reasons recorded (unexpected).</div>")
        html.append("</div>")
        html.append("</div>")
        return "\n".join(html)

    # Render per-run cards
    all_sim_rows: List[pd.DataFrame] = []
    for run_id, n_trades in run_rows:
        pkg = packages[run_id]
        trades = getattr(pkg, "trades", None)
        if trades is None or trades.empty:
            continue

        ic = _infer_ic_from_trades(trades)
        if not ic:
            html.append("<div class='tf-exit-card'><div class='muted'>Could not infer instrument/contract.</div></div>")
            continue
        instrument, contract = ic

        sim = simulate_exit_policies_for_run(
            run_id=run_id,
            trades=trades,
            market=market,
            instrument=instrument,
            contract=contract,
            policies=policies,
            cfg=sim_cfg,
        )

        if sim is None or sim.empty:
            html.append("<div class='tf-exit-card'>")
            html.append(f"<div class='tf-exit-title'>{_h(run_id)}</div>")
            html.append(
                f"<div class='muted'>Simulation returned no rows. Likely missing trade columns (Entry time/price) or missing ticks for {instrument} {contract}.</div>"
            )
            html.append("</div>")
            continue

        # Track for cross-run comparison (only non-diagnostic)
        try:
            if "policy" in sim.columns and not (sim["policy"] == "DIAGNOSTIC").any():
                tmp = sim.copy()
                if "run_id" not in tmp.columns:
                    tmp["run_id"] = run_id
                all_sim_rows.append(tmp)
        except Exception:
            pass

        # If we got DIAGNOSTIC rows, render them explicitly (do not try to aggregate)
        if "policy" in sim.columns and (sim["policy"] == "DIAGNOSTIC").any():
            diag = sim.loc[sim["policy"] == "DIAGNOSTIC"].copy()

            cols = [c for c in ["exit_reason", "detail", "run_id", "trade_idx", "entry_dt", "exit_dt"] if c in diag.columns]
            if not cols:
                cols = list(diag.columns)

            html.append("<div class='tf-exit-card'>")
            html.append(f"<div class='tf-exit-title'>{_h(run_id)}</div>")
            html.append(
                f"<div class='tf-exit-sub'>Instrument: {_h(instrument)} {_h(contract)} • "
                f"trades: {n_trades:,} • sim rows: {len(sim):,}</div>"
            )
            html.append("<h4 style='margin:8px 0 6px 0;'>DIAGNOSTIC</h4>")
            html.append(_render_table(diag[cols], max_rows=25))
            html.append("</div>")
            continue

        # Aggregate per policy
        sim["pnl_ticks"] = pd.to_numeric(sim["pnl_ticks"], errors="coerce")
        agg = (
            sim.groupby("policy", as_index=False)
            .agg(
                trades=("pnl_ticks", "size"),
                net_ticks=("pnl_ticks", "sum"),
                avg_ticks=("pnl_ticks", "mean"),
                win_rate=("pnl_ticks", lambda s: float((s > 0).mean()) if len(s) else 0.0),
                p10=("pnl_ticks", lambda s: float(s.quantile(0.10)) if len(s) else 0.0),
                p90=("pnl_ticks", lambda s: float(s.quantile(0.90)) if len(s) else 0.0),
            )
        ).sort_values("net_ticks", ascending=False)

        # Optional: Armed rate (only meaningful for BE-like policies)
        if "be_armed" in sim.columns:
            armed = sim.groupby("policy")["be_armed"].mean().reset_index().rename(columns={"be_armed": "armed_rate"})
            agg = agg.merge(armed, on="policy", how="left")

        # Chart: net ticks by policy
        uri = None
        try:
            fig = plt.figure(figsize=(8.5, 2.8))
            ax = fig.add_subplot(111)
            ax.bar(agg["policy"].tolist(), agg["net_ticks"].tolist())
            ax.axhline(0, linewidth=1)
            ax.set_title(f"{run_id} — Net ticks by policy")
            ax.set_ylabel("Net ticks")
            fig.tight_layout()
            uri = fig_to_base64_png(fig)
            plt.close(fig)
        except Exception:
            uri = None

        html.append("<div class='tf-exit-card'>")
        html.append(f"<div class='tf-exit-title'>{_h(run_id)}</div>")
        html.append(
            f"<div class='tf-exit-sub'>Instrument: {_h(instrument)} {_h(contract)} • trades: {n_trades:,} • sim rows: {len(sim):,}</div>"
        )

        html.append("<div class='tf-exit-grid'>")
        html.append("<div>")
        html.append("<h4 style='margin:8px 0 6px 0;'>Policy comparison (ticks)</h4>")
        html.append(_render_table(agg.round(2), max_rows=20))
        html.append("</div>")

        if uri:
            html.append("<div>")
            html.append(f"<img class='tf-exit-img' src='{uri}' alt='net ticks by policy' />")
            html.append("</div>")

        html.append("</div>")  # grid
        html.append("</div>")  # card

    # Cross-run comparison (all displayed runs)
    if all_sim_rows:
        try:
            big = pd.concat(all_sim_rows, axis=0, ignore_index=True)
            big["pnl_ticks"] = pd.to_numeric(big.get("pnl_ticks"), errors="coerce")

            # Overall per-policy summary across runs
            pol = (
                big.groupby("policy", as_index=False)
                .agg(
                    trades=("pnl_ticks", "size"),
                    net_ticks=("pnl_ticks", "sum"),
                    avg_ticks=("pnl_ticks", "mean"),
                    win_rate=("pnl_ticks", lambda s: float((s > 0).mean()) if len(s) else 0.0),
                    p10=("pnl_ticks", lambda s: float(s.quantile(0.10)) if len(s) else 0.0),
                    p90=("pnl_ticks", lambda s: float(s.quantile(0.90)) if len(s) else 0.0),
                )
                .sort_values("net_ticks", ascending=False)
            )

            # Per-run, per-policy net ticks (wide table)
            wide = (
                big.groupby(["run_id", "policy"], as_index=False)
                .agg(net_ticks=("pnl_ticks", "sum"), trades=("pnl_ticks", "size"))
            )
            piv = wide.pivot(index="run_id", columns="policy", values="net_ticks").fillna(0.0)
            piv = piv.loc[piv.sum(axis=1).sort_values(ascending=False).index]

            # Chart overall net ticks per policy
            uri2 = None
            try:
                fig = plt.figure(figsize=(9.0, 3.0))
                ax = fig.add_subplot(111)
                ax.bar(pol["policy"].tolist(), pol["net_ticks"].tolist())
                ax.axhline(0, linewidth=1)
                ax.set_title("All displayed runs — Net ticks by policy")
                ax.set_ylabel("Net ticks")
                fig.tight_layout()
                uri2 = fig_to_base64_png(fig)
                plt.close(fig)
            except Exception:
                uri2 = None

            html.append("<div class='tf-exit-card'>")
            html.append("<div class='tf-exit-title'>Cross-run comparison</div>")
            html.append(
                "<div class='tf-exit-sub muted'>Aggregated across the displayed runs above. Use this to compare policies and bots in one view.</div>"
            )

            html.append("<h4 style='margin:8px 0 6px 0;'>Overall policy summary (ticks)</h4>")
            html.append(_render_table(pol.round(2), max_rows=50))

            if uri2:
                html.append("<div style='margin-top:10px;'>")
                html.append(f"<img class='tf-exit-img' src='{uri2}' alt='overall net ticks by policy' />")
                html.append("</div>")

            html.append("<h4 style='margin:10px 0 6px 0;'>Bots (runs) × policies (net ticks)</h4>")
            html.append(_render_table(piv.round(1).reset_index(), max_rows=top_n))
            html.append("</div>")
        except Exception:
            # Never break the report section because of aggregation
            pass

    html.append("</div>")  # wrapper
    return "\n".join(html)
