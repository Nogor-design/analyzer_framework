from __future__ import annotations

from typing import Any, Optional, Tuple

import pandas as pd
import matplotlib.pyplot as plt

from ta_foundation.reports.html.embed import fig_to_base64_png


def _h(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _fmt_dt(v: Any) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    try:
        ts = pd.to_datetime(v)
        if pd.isna(ts):
            return "—"
        return str(ts)
    except Exception:
        return str(v)


def _parse_any_contract_from_packages(packages: dict) -> Optional[Tuple[str, str]]:
    # Best-effort: inspect first pkg.trades Instrument = "NQ 03-26"
    for _, pkg in (packages or {}).items():
        trades = getattr(pkg, "trades", None)
        if trades is None or len(trades) == 0:
            continue
        # try a few common col names
        cols = {c.lower(): c for c in trades.columns}
        c_instr = cols.get("instrument")
        if not c_instr:
            continue
        v = trades[c_instr].dropna()
        if v.empty:
            continue
        parts = str(v.iloc[0]).strip().split()
        if len(parts) >= 2:
            return parts[0], parts[1]
    return None


def render_tick_data_diagnostics(ctx: dict[str, Any]) -> str:
    packages = ctx.get("packages", {}) or {}
    options = ctx.get("options") or {}
    market = ctx.get("market")
    report_config = ctx.get("report_config")

    # Options (section-level)
    # sections:
    #  - id: tick_data_diagnostics
    #    options:
    #      instrument: NQ
    #      contract: "03-26"
    #      show_sample_rows: 20
    #      compare_to_minute: true
    #      compare_window_minutes: 240
    sec = ctx.get("section") or {}
    sec_opts = (sec.get("options") or {}) if isinstance(sec, dict) else {}
    instr_opt = sec_opts.get("instrument")
    contract_opt = sec_opts.get("contract")
    show_rows = int(sec_opts.get("show_sample_rows", 15))
    compare = bool(sec_opts.get("compare_to_minute", True))
    window_minutes = int(sec_opts.get("compare_window_minutes", 240))

    html: list[str] = []
    html.append("<div class='section'>")

    if market is None:
        html.append("<div class='muted'>No market data loaded. Provide --market-data containing Tick.Last.txt files.</div>")
        html.append("</div>")
        return "\n".join(html)

    # Determine target instrument/contract
    target = None
    if instr_opt and contract_opt:
        target = (str(instr_opt).strip(), str(contract_opt).strip())
    else:
        target = _parse_any_contract_from_packages(packages)

    # Summary table of all loaded tick streams
    html.append("<div class='card'>")
    html.append("<h3>Loaded tick streams</h3>")

    if not getattr(market, "ticks", None):
        html.append("<div class='muted'>No ticks loaded into MarketDataStore.</div>")
    else:
        html.append("<table class='table'>")
        html.append("<tr><th>Instrument</th><th>Contract</th><th>Rows</th><th>First dt</th><th>Last dt</th></tr>")
        for (instr, contract), df in sorted(market.ticks.items(), key=lambda kv: (kv[0][0], kv[0][1])):
            first_dt = df["dt"].iloc[0] if (df is not None and not df.empty and "dt" in df.columns) else None
            last_dt = df["dt"].iloc[-1] if (df is not None and not df.empty and "dt" in df.columns) else None
            n = int(len(df)) if df is not None else 0
            html.append(
                "<tr>"
                f"<td>{_h(str(instr))}</td>"
                f"<td>{_h(str(contract))}</td>"
                f"<td>{n:,}</td>"
                f"<td>{_h(_fmt_dt(first_dt))}</td>"
                f"<td>{_h(_fmt_dt(last_dt))}</td>"
                "</tr>"
            )
        html.append("</table>")
    html.append("</div>")

    if target is None:
        html.append("<div class='card'><div class='muted'>Could not infer instrument/contract from trades. Set section options instrument/contract.</div></div>")
        html.append("</div>")
        return "\n".join(html)

    instr, contract = target
    ticks = market.get_ticks(instr, contract)

    html.append("<div class='card'>")
    html.append(f"<h3>Tick sample — { _h(instr) } { _h(contract) }</h3>")

    if ticks is None or ticks.empty:
        html.append("<div class='muted'>No ticks for this instrument/contract.</div>")
        html.append("</div></div>")
        return "\n".join(html)

    # Sample rows
    sample = ticks.head(show_rows).copy()
    cols = [c for c in ["dt", "last", "bid", "ask", "volume"] if c in sample.columns]
    sample = sample[cols]
    html.append("<table class='table'>")
    html.append("<tr>" + "".join([f"<th>{_h(c)}</th>" for c in cols]) + "</tr>")
    for _, r in sample.iterrows():
        html.append("<tr>" + "".join([f"<td>{_h(str(r[c]))}</td>" for c in cols]) + "</tr>")
    html.append("</table>")

    # Optional compare tick-derived 1m to minute 1m
    if compare:
        mb = market.get_minute_bars(instr, contract)
        if mb is None or mb.empty:
            html.append("<div class='muted' style='margin-top:10px;'>No minute bars found for comparison.</div>")
        else:
            diag = market.validate_minute_vs_ticks(instr, contract, max_close_diff=0.25)
            html.append("<div style='margin-top:10px;'>")
            html.append("<b>Minute vs tick-derived (1m) diagnostic</b><br/>")
            if not diag.get("ok"):
                html.append(f"<div class='muted'>{_h(str(diag.get('message')))}</div>")
            else:
                html.append(
                    f"<div>Overlap minutes: <b>{int(diag.get('overlap_minutes',0)):,}</b> • "
                    f"Bad minutes: <b>{int(diag.get('bad_minutes',0)):,}</b> • "
                    f"Max close diff: <b>{diag.get('max_close_diff')}</b></div>"
                )
            html.append("</div>")

            # chart last N minutes of overlap for close series
            try:
                # derive tick 1m via store.get_bars(source="ticks")
                tick_1m = market.get_bars(instr, contract, timeframe="1m", source="ticks")
                min_1m = market.get_bars(instr, contract, timeframe="1m", source="minute")

                if tick_1m is not None and min_1m is not None and not tick_1m.empty and not min_1m.empty:
                    # overlap window = last window_minutes
                    end_dt = min(min_1m["dt"].max(), tick_1m["dt"].max())
                    start_dt = end_dt - pd.Timedelta(minutes=window_minutes)

                    a = min_1m[(min_1m["dt"] >= start_dt) & (min_1m["dt"] <= end_dt)][["dt", "close"]].copy()
                    b = tick_1m[(tick_1m["dt"] >= start_dt) & (tick_1m["dt"] <= end_dt)][["dt", "close"]].copy()
                    m = a.merge(b, on="dt", how="inner", suffixes=("_minute", "_tick"))

                    if not m.empty:
                        fig = plt.figure(figsize=(10, 3))
                        ax = fig.add_subplot(111)
                        ax.plot(m["dt"], m["close_minute"], label="minute close")
                        ax.plot(m["dt"], m["close_tick"], label="tick-derived close", linestyle="--")
                        ax.set_title(f"Close comparison (last {window_minutes} minutes)")
                        ax.set_xlabel("dt")
                        ax.set_ylabel("close")
                        ax.legend(loc="best")
                        uri = fig_to_base64_png(fig)
                        plt.close(fig)

                        html.append("<div style='margin-top:10px;'>")
                        html.append(f"<img src='{uri}' style='max-width:100%; border-radius:10px;'/>")
                        html.append("</div>")
            except Exception as e:
                html.append(f"<div class='muted' style='margin-top:10px;'>Chart failed: {_h(str(e))}</div>")

    html.append("</div>")  # card
    html.append("</div>")  # section
    return "\n".join(html)
