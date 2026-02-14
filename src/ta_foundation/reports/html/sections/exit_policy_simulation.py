from __future__ import annotations

from typing import Any, Dict, List, Optional
import re
from collections import Counter

import pandas as pd
import matplotlib.pyplot as plt

from ta_foundation.core.model import AnalysisPackage
from ta_foundation.reports.html.embed import fig_to_base64_png
from ta_foundation.analysis.exits.policies import FixedStopTargetPolicy, AtrTrailPolicy, BreakEvenAtrTrailPolicy
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


def _parse_instrument_contract(inst_val: str) -> Optional[tuple[str, str]]:
    """
    More forgiving than naive split.
    Accepts strings like:
      "NQ 03-26"
      "NQ 03-26 (Rithmic)"
      "NQ 03-26 Last"
    Strategy: first token = instrument, find the first token that looks like MM-YY.
    """
    if not inst_val:
        return None
    parts = str(inst_val).strip().split()
    if len(parts) < 2:
        return None
    instr = parts[0].strip()
    contract = None
    for p in parts[1:]:
        p = p.strip()
        if len(p) == 5 and p[2] == "-" and p[:2].isdigit() and p[3:].isdigit():
            contract = p
            break
    if not instr or not contract:
        return None
    return instr, contract

# helper: infer instrument/contract from a trades df (same semantics as your tick diagnostic)
def _infer_ic_from_trades(trades: pd.DataFrame) -> Optional[tuple[str, str]]:
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


def render_exit_policy_simulation(ctx: Dict[str, Any]) -> str:
    packages: Dict[str, AnalysisPackage] = ctx.get("packages", {}) or {}
    market = ctx.get("market")
    section = ctx.get("section") or {}
    opts = (section.get("options") or {}) if isinstance(section, dict) else {}

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
    </style>
    """

    html: List[str] = [css, "<div class='tf-exit-sim'>"]

    if market is None:
        html.append("<div class='tf-exit-card'><div class='muted'>No market store found in report context (<code>market</code>). Provide --market-data.</div></div>")
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

    bounded = bool(opts.get("bounded_to_original_exit", True))
    max_minutes = int(opts.get("max_minutes_unbounded", 180))
    use_bid_ask = bool(opts.get("use_bid_ask_triggers", True))

    # Policies
    fixed_stop_atr = float(opts.get("fixed_stop_atr_mult", 1.0))
    fixed_target_atr = float(opts.get("fixed_target_atr_mult", 1.5))

    trail_stop_atr = float(opts.get("trail_stop_atr_mult", 1.0))
    trail_trail_atr = float(opts.get("trail_atr_mult", 1.0))
    trail_target_atr = opts.get("trail_profit_target_atr_mult", None)
    trail_target_atr = float(trail_target_atr) if trail_target_atr is not None else None

    be_enable = bool(opts.get("be_enable", True))
    be_trigger_atr = float(opts.get("be_trigger_atr_mult", 1.0))
    be_offset_ticks = float(opts.get("be_offset_ticks", 0.0))
    be_trail_atr = float(opts.get("be_trail_atr_mult", 1.0))
    be_target_atr = opts.get("be_profit_target_atr_mult", None)
    be_target_atr = float(be_target_atr) if be_target_atr is not None else None

    policies = [
        FixedStopTargetPolicy(name="fixed_atr", stop_atr_mult=fixed_stop_atr, target_atr_mult=fixed_target_atr),
        AtrTrailPolicy(name="atr_trail", stop_atr_mult=trail_stop_atr, trail_atr_mult=trail_trail_atr, profit_target_atr_mult=trail_target_atr),
    ]
    if be_enable:
        policies.append(
            BreakEvenAtrTrailPolicy(
                name="be_atr_trail",
                stop_atr_mult=trail_stop_atr,
                be_trigger_atr_mult=be_trigger_atr,
                be_offset_ticks=be_offset_ticks,
                trail_atr_mult=be_trail_atr,
                profit_target_atr_mult=be_target_atr,
            )
        )

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
        # best-effort: use first package trades
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

    # Diagnostics about market store
    tick_keys = []
    try:
        if getattr(market, "ticks", None):
            tick_keys = list(market.ticks.keys())
    except Exception:
        tick_keys = []

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
    # html.append(f"<div class='muted'>Market tick streams</div><div>{len(tick_keys):,} { _h(str(tick_keys[:5])) if tick_keys else '' }</div>")
    html.append(
        f"<div class='muted'>Tick probe</div><div>{_h(probe_ic_str) if probe_ic_str else '—'} • rows: <b>{probe_ticks_rows if probe_ticks_rows is not None else '—'}</b></div>")

    html.append(f"<div class='muted'>min_trades / top_n</div><div>{min_trades} / {top_n}</div>")
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

        inst_col = _find_col(trades, ["instrument", "Instrument"])
        if not inst_col:
            skip["MISSING_INSTRUMENT_COL"] += 1
            continue

        inst_val = str(trades[inst_col].dropna().iloc[0]) if trades[inst_col].dropna().any() else ""
        ic = _parse_instrument_contract(inst_val)
        if not ic:
            skip["BAD_INSTRUMENT_FORMAT"] += 1
            continue

        instrument, contract = ic
        # If ticks store has no key, you can still try sim (store might load via get_ticks),
        # but this is a helpful early indicator
        if tick_keys and (instrument, contract) not in tick_keys:
            skip["NO_TICKS_FOR_INSTRUMENT_CONTRACT_KEY"] += 1
            # still allow; do not continue

        ic = None
        if instr_opt and contract_opt:
            # If user pins instrument/contract, only include runs that match
            ic = _infer_ic_from_trades(trades)
            if not ic:
                skip["BAD_INSTRUMENT_FORMAT"] += 1
                continue
            if str(ic[0]).strip() != str(instr_opt).strip() or str(ic[1]).strip() != str(contract_opt).strip():
                skip["NOT_TARGET_INSTRUMENT_CONTRACT"] += 1
                continue
        else:
            # current behavior: infer per run
            ic = _infer_ic_from_trades(trades)
            if not ic:
                skip["BAD_INSTRUMENT_FORMAT"] += 1
                continue

        instrument, contract = ic
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
    for run_id, n_trades in run_rows:
        pkg = packages[run_id]
        trades = getattr(pkg, "trades", None)
        if trades is None or trades.empty:
            continue

        # inst_col = _find_col(trades, ["instrument", "Instrument"])
        # inst_val = str(trades[inst_col].dropna().iloc[0]) if inst_col and trades[inst_col].dropna().any() else ""
        # ic = _parse_instrument_contract(inst_val)
        # if not ic:
        #     continue
        # instrument, contract = ic

        ic = _infer_ic_from_trades(trades)
        if not ic:
            html.append("<div class='tf-exit-card'>...</div>")
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
            html.append(f"<div class='muted'>Simulation returned no rows. Likely missing trade columns (Entry time/price) or missing ticks for {instrument} {contract}.</div>")
            html.append("</div>")
            continue

        # If we only got DIAGNOSTIC rows (or any DIAGNOSTIC), render them explicitly
        if "policy" in sim.columns and (sim["policy"] == "DIAGNOSTIC").any():
            diag = sim.loc[sim["policy"] == "DIAGNOSTIC"].copy()

            # Prefer the most informative columns if they exist
            cols = []
            for c in ["exit_reason", "detail", "run_id", "trade_idx", "entry_dt", "exit_dt"]:
                if c in diag.columns:
                    cols.append(c)
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

        # Chart net ticks
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
        html.append(f"<div class='tf-exit-sub'>Instrument: {_h(instrument)} {_h(contract)} • trades: {n_trades:,} • sim rows: {len(sim):,}</div>")

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

    html.append("</div>")  # wrapper
    return "\n".join(html)
