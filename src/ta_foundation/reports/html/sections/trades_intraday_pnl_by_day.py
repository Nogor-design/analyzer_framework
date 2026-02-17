from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import matplotlib.dates as mdates
import matplotlib.pyplot as plt

from ta_foundation.core.model import AnalysisPackage
from ta_foundation.reports.html.embed import fig_to_base64_png


def _find_col(df, candidates: List[str]) -> Optional[str]:
    cols = list(df.columns)
    lower = {c.lower(): c for c in cols}

    for c in candidates:
        if c in cols:
            return c
        lc = c.lower()
        if lc in lower:
            return lower[lc]

    for c in candidates:
        lc = c.lower()
        for col in cols:
            if lc in col.lower():
                return col
    return None


def _direction_to_sign(v: Any) -> int:
    """+1 long, -1 short for common direction fields."""
    if v is None:
        return +1
    s = str(v).strip().lower()
    if s in ("short", "sell", "sellshort", "sell short", "s", "-1"):
        return -1
    if s in ("long", "buy", "b", "1", "+1"):
        return +1
    # fallback
    if "sell short" in s or "sellshort" in s:
        return -1
    if "short" in s and "sell" in s:
        return -1
    if "buy" in s or "long" in s:
        return +1
    return +1


def _entryname_to_sign(v: Any) -> Optional[int]:
    """
    Derive direction from Entry Name / Signal name (user's file uses 'Sell short' / 'Buy').
    Returns None if unknown.
    """
    if v is None:
        return None
    s = str(v).strip().lower()
    if not s:
        return None

    # Typical values: "Sell short", "Buy"
    if "Sell short" in s or "sellshort" in s:
        return -1
    if s == "sell" or s.startswith("sell "):
        # ambiguous sell vs sell short; treat as short if explicitly "sell short" else unknown
        return None
    if "buy" in s:
        return +1
    if "short" in s and "sell" in s:
        return -1
    if "short" in s:
        return -1
    if "long" in s:
        return +1
    return None


@dataclass(frozen=True)
class SessionDef:
    name: str
    start_hm: Tuple[int, int]  # inclusive
    end_hm: Tuple[int, int]    # exclusive


def _parse_hm(v: Any) -> Tuple[int, int]:
    """
    Accept "HH:MM" or [H,M] or (H,M)
    """
    if isinstance(v, (list, tuple)) and len(v) == 2:
        return int(v[0]), int(v[1])
    s = str(v).strip()
    hh, mm = s.split(":")
    return int(hh), int(mm)


def _default_sessions() -> List[SessionDef]:
    """
    Default Denver-local session windows (edit via report.yaml options if needed).
    These do NOT cross midnight to keep 'per-day' charts stable.
    """
    return [
        SessionDef("LONDON", (0, 0), (6, 0)),       # 00:00–06:00
        SessionDef("NY", (6, 0), (15, 0)),    # 06:00–12:00
        SessionDef("ASIA", (16, 0), (23, 0)),       # 12:00–18:00
    ]


def _in_session(dt_naive, sess: SessionDef) -> bool:
    t = dt_naive.time()
    sh, sm = sess.start_hm
    eh, em = sess.end_hm
    # Non-midnight-crossing window only
    return (t.hour, t.minute) >= (sh, sm) and (t.hour, t.minute) < (eh, em)




def _plot_bars(
    df_sess,
    *,
    title: str,
    mfe_alpha: float,
    show_cum_line: bool,
    show_mae_bar: bool,
    mae_alpha: float,
    mae_width_ratio: float,
    mae_offset_ratio: float,
    show_etd_line: bool,
    etd_line_alpha: float,
    show_etd_threshold_line: bool,
    etd_threshold: float,

) -> Optional[str]:
    if df_sess is None or len(df_sess) == 0:
        return None

    import matplotlib.dates as mdates

    # Use date floats so we can offset bars "next to" each other reliably.
    x_dt = df_sess["_entry_dt_local_naive"].tolist()
    x = mdates.date2num(x_dt)

    y_real = df_sess["_y_real"].tolist()   # realized pnl (profit up / loss down)
    y_mfe = df_sess["_y_mfe"].tolist()     # potential (upward)
    y_mae = df_sess.get("_y_mae", None)
    if y_mae is not None:
        y_mae = df_sess["_y_mae"].tolist()

    pnl_outcome = df_sess["_pnl"].tolist()
    dir_signs = df_sess["_dir_sign"].tolist()

    # Colors encode direction + outcome
    colors = []
    for p, ds in zip(pnl_outcome, dir_signs):
        p = float(p)
        ds = int(ds)
        if ds >= 0:  # long
            colors.append("#2fb36a" if p >= 0 else "#d04a4a")
        else:        # short
            colors.append("#7b5cff" if p >= 0 else "#ff8c2a")

    fig = plt.figure(figsize=(10.4, 2.9))
    ax = fig.add_subplot(111)

    main_w = 0.0009
    mae_w = main_w * max(0.1, mae_width_ratio)
    mae_off = main_w * mae_offset_ratio

    # MFE behind realized (centered on trade time)
    ax.bar(
        x,
        y_mfe,
        width=main_w,
        alpha=mfe_alpha,
        color="tab:blue",
        linewidth=0,
        zorder=1,
    )

    # MAE thin light bar "next to" each trade (always down)
    if show_mae_bar and y_mae is not None:
        # print(y_mae)
        ax.bar(
            [xi + mae_off for xi in x],
            y_mae,
            width=mae_w,
            alpha=mae_alpha,
            color="#F8BCBD",   # light neutral
            linewidth=0,
            zorder=2,
        )

    # Realized PnL bars
    ax.bar(
        x,
        y_real,
        width=main_w,
        color=colors,
        linewidth=0,
        zorder=3,
    )

    # Pink line: |ETD| magnitude (real risk)
    if show_etd_line and "_etd_abs" in df_sess.columns:
        etd = df_sess["_etd_abs"].tolist()
        ax.plot(x, etd, linewidth=1.3, alpha=etd_line_alpha, color="#ff5bbd", zorder=4)

    # ETD threshold (dashed)
    if show_etd_threshold_line and etd_threshold is not None:
        try:
            thr = float(etd_threshold)
            ax.axhline(thr, linestyle="--", linewidth=1.1, color="#ff5bbd", alpha=0.65, zorder=3)
        except Exception:
            pass

    ax.axhline(0, linewidth=1)

    if show_cum_line:
        try:
            import pandas as pd
            cum = pd.Series(df_sess["_y_real"].values).cumsum().values.tolist()
        except Exception:
            cum = []
        if cum:
            ax.plot(x, cum, linewidth=1.2, zorder=4)

    ax.set_title(title, fontsize=10)
    ax.tick_params(axis="x", labelsize=8)
    ax.tick_params(axis="y", labelsize=8)

    ax.xaxis_date()
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=6, maxticks=14))
    ax.grid(True, axis="y", alpha=0.18)

    fig.tight_layout()
    uri = fig_to_base64_png(fig)
    plt.close(fig)
    return uri




def _plot_hourly_totals(
    hourly_df,
    *,
    title: str,
    show_cum_line: bool,
    mode: str,  # "net" | "direction_split"
) -> Optional[str]:
    if hourly_df is None or len(hourly_df) == 0:
        return None

    fig = plt.figure(figsize=(10.4, 2.6))
    ax = fig.add_subplot(111)

    if mode == "net":
        hours = hourly_df["hour"].tolist()
        pnl_sum = hourly_df["pnl_sum"].tolist()
        colors = ["#2fb36a" if float(v) >= 0 else "#d04a4a" for v in pnl_sum]
        ax.bar(hours, pnl_sum, color=colors, linewidth=0, zorder=2)

        if show_cum_line:
            try:
                import pandas as pd
                cum = pd.Series(pnl_sum).cumsum().tolist()
            except Exception:
                cum = []
            if cum:
                ax.plot(hours, cum, linewidth=1.2, zorder=3)

    else:
        # direction_split stacked:
        # long_profit (green) + short_profit (purple) above 0
        # long_loss (red) + short_loss (orange) below 0
        hours = hourly_df["hour"].tolist()
        lp = hourly_df["long_profit"].tolist()
        sp = hourly_df["short_profit"].tolist()
        ll = hourly_df["long_loss"].tolist()     # negative values
        sl = hourly_df["short_loss"].tolist()    # negative values

        # Positive stacks
        ax.bar(hours, lp, color="#2fb36a", linewidth=0, zorder=2)                # long profit
        ax.bar(hours, sp, bottom=lp, color="#7b5cff", linewidth=0, zorder=2)    # short profit stacked on top

        # Negative stacks (stack downward)
        ax.bar(hours, ll, color="#d04a4a", linewidth=0, zorder=2)                # long loss (negative)
        ax.bar(hours, sl, bottom=ll, color="#ff8c2a", linewidth=0, zorder=2)     # short loss stacked below long loss

        if show_cum_line:
            # cumulative NET across hours (profits + losses)
            try:
                import pandas as pd
                net = (pd.Series(lp) + pd.Series(sp) + pd.Series(ll) + pd.Series(sl)).tolist()
                cum = pd.Series(net).cumsum().tolist()
            except Exception:
                cum = []
            if cum:
                ax.plot(hours, cum, linewidth=1.2, zorder=3)

    ax.axhline(0, linewidth=1)
    ax.set_title(title, fontsize=10)
    ax.tick_params(axis="x", labelsize=8)
    ax.tick_params(axis="y", labelsize=8)

    ax.set_xticks(list(range(0, 24)))
    ax.set_xlim(-0.5, 23.5)
    ax.grid(True, axis="y", alpha=0.18)

    fig.tight_layout()
    uri = fig_to_base64_png(fig)
    plt.close(fig)
    return uri





def render_trades_intraday_pnl_by_day(ctx: Dict[str, Any]) -> str:
    """
    Per-run, per-day, per-session intraday trade bars:
      - Realized PnL is direction-adjusted so:
          * Long win => green up
          * Long loss => red down
          * Short win => green down
          * Short loss => red up
      - Blue overlay: direction-adjusted MFE

    Options (report.yaml section options):
      - max_days_per_run: int (default 10)   # most recent first
      - max_trades_per_session: int (default 250)
      - mfe_alpha: float (default 0.22)
      - show_run_card: bool (default True)
      - show_legend_hint: bool (default True)
      - show_cum_line: bool (default True)
      - weekdays_include: list[int|str] (optional)
          ints: Monday=0 ... Sunday=6
          strings: "mon","tue","wed","thu","fri","sat","sun"
      - sessions: optional list of dicts:
          - name: str
          - start: "HH:MM" (inclusive)
          - end:   "HH:MM" (exclusive)
        Example:
          sessions:
            - {name: "Asia", start: "00:00", end: "06:00"}
            - {name: "London", start: "06:00", end: "12:00"}
            - {name: "NY", start: "12:00", end: "18:00"}
    """
    packages: Dict[str, AnalysisPackage] = ctx.get("packages", {}) or {}
    options: Dict[str, Any] = ctx.get("options") or {}
    show_trade_charts = bool(options.get("show_trade_charts", True))
    show_hourly_totals = bool(options.get("show_hourly_totals", True))
    show_only_totals = bool(options.get("show_only_totals", False))  # convenience

    show_mae_bar = bool(options.get("show_mae_bar", True))
    mae_alpha = float(options.get("mae_alpha", 0.18))
    mae_width_ratio = float(options.get("mae_width_ratio", 0.55))  # relative to main bar width
    mae_offset_ratio = float(options.get("mae_offset_ratio", 0.75))  # how far "next to" the main bar

    show_explanation = bool(options.get("show_explanation", True))
    show_ledger = bool(options.get("show_ledger", True))

    risk_threshold = float(options.get("risk_threshold", 2500.0))
    show_risk_line = bool(options.get("show_risk_line", True))
    show_risk_threshold_line = bool(options.get("show_risk_threshold_line", True))
    risk_line_alpha = float(options.get("risk_line_alpha", 0.90))

    etd_threshold = float(options.get("etd_threshold", 2500.0))
    show_etd_line = bool(options.get("show_etd_line", True))
    show_etd_threshold_line = bool(options.get("show_etd_threshold_line", True))
    etd_line_alpha = float(options.get("etd_line_alpha", 0.90))

    # If show_only_totals is true, it overrides
    if show_only_totals:
        show_trade_charts = False
        show_hourly_totals = True

    hourly_totals_mode = str(options.get("hourly_totals_mode", "net")).strip().lower()
    # allowed: "net", "direction_split"
    if hourly_totals_mode not in ("net", "direction_split"):
        hourly_totals_mode = "net"

    max_days_per_run = int(options.get("max_days_per_run", 10))
    max_trades_per_session = int(options.get("max_trades_per_session", 250))
    mfe_alpha = float(options.get("mfe_alpha", 0.22))
    show_run_card = bool(options.get("show_run_card", True))
    show_legend_hint = bool(options.get("show_legend_hint", True))
    show_cum_line = bool(options.get("show_cum_line", True))

    weekdays_include_raw = options.get("weekdays_include", None)
    weekdays_include: Optional[set[int]] = None
    if weekdays_include_raw:
        weekdays_include = set()
        map_str = {
            "mon": 0, "monday": 0,
            "tue": 1, "tues": 1, "tuesday": 1,
            "wed": 2, "wednesday": 2,
            "thu": 3, "thurs": 3, "thursday": 3,
            "fri": 4, "friday": 4,
            "sat": 5, "saturday": 5,
            "sun": 6, "sunday": 6,
        }
        for v in weekdays_include_raw:
            if isinstance(v, int):
                weekdays_include.add(int(v))
            else:
                s = str(v).strip().lower()
                if s in map_str:
                    weekdays_include.add(map_str[s])

    # sessions
    sessions_cfg = options.get("sessions", None)
    sessions: List[SessionDef] = []
    if isinstance(sessions_cfg, list) and sessions_cfg:
        for s in sessions_cfg:
            try:
                name = str(s.get("name") or "").strip() or "Session"
                start = _parse_hm(s.get("start", "00:00"))
                end = _parse_hm(s.get("end", "23:59"))
                sessions.append(SessionDef(name=name, start_hm=start, end_hm=end))
            except Exception:
                continue
    if not sessions:
        sessions = _default_sessions()

    css = """
    <style>
      .tf-trades-byday { display:flex; flex-direction:column; gap:14px; }
      .tf-trades-run {
        border-radius: 16px;
        overflow: hidden;
        background: rgba(255,255,255,0.035);
        border: 1px solid rgba(255,255,255,0.06);
      }
      .tf-trades-runhead {
        padding: 12px 14px;
        display:flex;
        justify-content:space-between;
        gap:12px;
        flex-wrap:wrap;
        background: rgba(0,0,0,0.14);
        border-bottom: 1px solid rgba(255,255,255,0.06);
      }
      .tf-trades-runid { font-weight: 800; font-size: 1.0rem; }
      .tf-trades-note { opacity: 0.82; font-size: 0.86rem; }

      .tf-trades-cardimg { width:100%; height:auto; display:block; }

      .tf-trades-days {
        padding: 12px 14px 14px 14px;
        display:grid;
        grid-template-columns: 1fr;
        gap: 12px;
      }

      .tf-dayblock {
        border-radius: 14px;
        overflow:hidden;
        border: 1px solid rgba(255,255,255,0.06);
        background: rgba(255,255,255,0.02);
      }
      .tf-dayhead {
        padding: 10px 12px;
        display:flex;
        justify-content:space-between;
        gap:10px;
        flex-wrap:wrap;
        background: rgba(0,0,0,0.12);
        border-bottom: 1px solid rgba(255,255,255,0.06);
      }
      .tf-daytitle { font-weight: 800; font-size: 0.92rem; }
      .tf-daysub { opacity: 0.82; font-size: 0.82rem; }

      .tf-sessions {
        padding: 10px 12px 12px 12px;
        display:grid;
        grid-template-columns: 1fr;
        gap: 10px;
      }
      .tf-session {
        border-radius: 12px;
        overflow:hidden;
        border: 1px solid rgba(255,255,255,0.06);
        background: rgba(0,0,0,0.10);
      }
      .tf-sessionhead {
        padding: 8px 10px;
        display:flex;
        justify-content:space-between;
        gap:10px;
        flex-wrap:wrap;
        border-bottom: 1px solid rgba(255,255,255,0.06);
      }
      .tf-sessionname { font-weight: 800; font-size: 0.86rem; }
      .tf-sessionsub { opacity: 0.82; font-size: 0.80rem; }
      .tf-session img { width:100%; height:auto; display:block; }
      .tf-trades-empty { padding: 12px 14px; opacity: 0.85; }
            .tf-hourly {
        margin-top: 10px;
        padding: 12px 14px 14px 14px;
        border-top: 1px solid rgba(255,255,255,0.06);
      }
      .tf-hourly-title { font-weight: 900; font-size: 0.95rem; margin-bottom: 8px; }
      .tf-hourly-grid {
        display:grid;
        grid-template-columns: 1fr;
        gap: 10px;
      }
      .tf-hourly-card {
        border-radius: 12px;
        overflow:hidden;
        border: 1px solid rgba(255,255,255,0.06);
        background: rgba(0,0,0,0.10);
      }
      .tf-hourly-head {
        padding: 8px 10px;
        display:flex;
        justify-content:space-between;
        gap:10px;
        flex-wrap:wrap;
        border-bottom: 1px solid rgba(255,255,255,0.06);
      }
      .tf-hourly-name { font-weight: 800; font-size: 0.86rem; }
      .tf-hourly-sub { opacity: 0.82; font-size: 0.80rem; }
      .tf-hourly-card img { width:100%; height:auto; display:block; }

          .tf-badge {
        display:inline-block;
        padding: 2px 8px;
        border-radius: 999px;
        font-size: 0.78rem;
        font-weight: 900;
        border: 1px solid rgba(255,255,255,0.10);
        background: rgba(255,255,255,0.06);
      }
      .tf-badge--over {
        border-color: rgba(255,80,80,0.55);
        background: rgba(255,80,80,0.16);
        color: #ffb2b2;
      }

      .tf-ledger {
        padding: 10px 14px 14px 14px;
      }
      .tf-ledger-title {
        font-weight: 900;
        font-size: 0.95rem;
        margin: 8px 0;
      }
      .tf-ledger table {
        width: 100%;
        border-collapse: collapse;
        font-size: 0.84rem;
        overflow:hidden;
        border-radius: 12px;
        border: 1px solid rgba(255,255,255,0.06);
      }
      .tf-ledger th, .tf-ledger td {
        text-align: left;
        padding: 8px 10px;
        border-bottom: 1px solid rgba(255,255,255,0.06);
      }
      .tf-ledger th {
        background: rgba(0,0,0,0.12);
        font-weight: 900;
      }
      .tf-ledger tr:last-child td { border-bottom: none; }
      .tf-ledger .num { text-align: right; font-variant-numeric: tabular-nums; }

    </style>
    """

    html: List[str] = [css, '<div class="tf-trades-byday">']

    if show_legend_hint:
        html.append(
            "<div class='tf-trades-note'>"
            "<b>Legend:</b> Realized PnL bars are direction-adjusted so <b>short wins plot downward</b> "
            "and <b>short losses plot upward</b>. Blue bars show direction-adjusted <b>MFE (potential)</b>. "
            + ("Cumulative line (dir-adjusted) is enabled." if show_cum_line else "")
            + "</div>"
        )
    if show_explanation:
        html.append(
            """
            <div class="tf-trades-note" style="line-height:1.45">
              <div style="font-weight:900; font-size:1.02rem; margin-bottom:6px;">
                How to read these charts
              </div>
              <ul style="margin:0; padding-left:18px;">
                <li><b>X-axis</b> is trade <b>Entry Time</b> in America/Denver local time.</li>
                <li><b>Bars</b> show realized trade PnL (profit above 0, loss below 0). Color encodes direction/outcome:
                    <b>Long Win</b>=Green, <b>Long Loss</b>=Red, <b>Short Win</b>=Purple, <b>Short Loss</b>=Orange.</li>
                <li><b>Blue bar</b> shows <b>MFE</b> (maximum favorable excursion) as “what it could have made”, always plotted upward.</li>
                <li><b>Light bar</b> next to each trade shows <b>MAE</b> (maximum adverse excursion) as a downward magnitude from 0.</li>
                <li><b>Pink line</b> overlays <b>|MAE| + |MFE|</b> per trade (a combined excursion magnitude).</li>
                <li>A <b>dashed threshold</b> line marks a configurable limit (default: <b>$2500</b>). If a day’s max(|MAE|+|MFE|) exceeds it,
                    the day header is flagged in red and the ledger marks it as OVER.</li>
              </ul>
            </div>
            """
        )

    for run_id in sorted(packages.keys()):
        pkg = packages[run_id]
        trades = getattr(pkg, "trades", None)
        if trades is None or len(trades) == 0:
            continue

        # print(trades)
        entry_time_col = _find_col(trades, ["entry_time", "Entry time", "Entry Time"])
        pnl_col = _find_col(trades, ["profit", "Profit", "Profit $", "PnL", "Pnl"])
        mfe_col = _find_col(trades, ["mfe", "MFE", "Max favorable excursion", "Max Favorable Excursion"])

        # Direction columns (NinjaTrader exports)
        market_pos_col = _find_col(trades, ["Market pos.", "market_pos", "Market position", "Market Position"])
        entry_name_col = _find_col(trades,["Entry name", "Entry Name", "Entry signal", "Entry Signal", "Signal", "Signal name","Signal Name"])
        dir_col = _find_col(trades, ["Direction", "direction", "Side", "side"])

        mae_col = _find_col(trades, ["mae"])
        # print(mfe_col)
        if not mae_col:
            mae_col = _find_col(trades, ["MAE", "Max adverse excursion", "Max Adverse Excursion"])

        etd_col = _find_col(trades, ["etd"])
        if not etd_col:
            etd_col = _find_col(trades, ["ETD", "Etd", "End trade drawdown", "End Trade Drawdown"])

        if not entry_time_col or not pnl_col:
            continue

        derived = (getattr(pkg, "metadata", None) or {}).get("derived", {})
        card_uri = derived.get("card_image_uri")

        import pandas as pd

        df = trades.copy()
        df["_entry_dt"] = pd.to_datetime(df[entry_time_col], errors="coerce")
        df = df[df["_entry_dt"].notna()].copy()
        if len(df) == 0:
            continue

        # Convert to America/Denver local naive for plotting/grouping
        try:
            df["_entry_dt_local_naive"] = (
                df["_entry_dt"].dt.tz_convert("America/Denver").dt.tz_localize(None)
            )
        except Exception:
            df["_entry_dt_local_naive"] = df["_entry_dt"]

        # Numeric series
        df["_pnl"] = pd.to_numeric(df[pnl_col], errors="coerce").fillna(0.0)
        if mfe_col:
            df["_mfe"] = pd.to_numeric(df[mfe_col], errors="coerce").fillna(0.0)
        else:
            df["_mfe"] = 0.0


        # MAE is adverse excursion; visualize as downward magnitude from 0
        if mae_col:
            df["_mae"] = pd.to_numeric(df[mae_col], errors="coerce").fillna(0.0)
            # print(df["_mae"])
        else:
            df["_mae"] = 0.0

        df["_y_mae"] = -df["_mae"].abs()

        # Risk/potential combined metric per trade: |MAE| + |MFE|
        df["_risk_potential"] = df["_mae"].abs() + df["_mfe"].abs()

        # --- Direction sign priority ---
        # 1) Market pos. (most reliable in NT exports: Long/Short)
        # 2) Entry name (Buy / Sell short)
        # 3) Direction/Side fallback

        # ETD: real risk (use abs for plotting as magnitude)
        if etd_col:
            df["_etd"] = pd.to_numeric(df[etd_col], errors="coerce").fillna(0.0)
        else:
            df["_etd"] = 0.0

        df["_etd_abs"] = df["_etd"].abs()

        df["_dir_sign"] = None

        if market_pos_col:
            # print(df[market_pos_col])
            def _mp_to_sign(v: Any) -> Optional[int]:
                if v is None:
                    return None
                s = str(v).strip().lower()
                if s.startswith("short"):
                    return -1
                if s.startswith("long"):
                    return +1
                return None
            df["_dir_sign"] = df[market_pos_col].apply(_mp_to_sign)

        if entry_name_col:
            # print(df[entry_name_col])
            # only fill where still unknown
            df["_dir_sign"] = df["_dir_sign"].fillna(df[entry_name_col].apply(_entryname_to_sign))

        if dir_col:
            df["_dir_sign"] = df["_dir_sign"].fillna(df[dir_col].apply(_direction_to_sign))

        df["_dir_sign"] = df["_dir_sign"].fillna(+1).astype(int)



        # ✅ Plot values: profit always up, loss always down (no direction-adjust)
        df["_y_real"] = df["_pnl"]

        # ✅ MFE "potential" should always extend upward (what it could have made)
        # Some exports store MFE as positive already; abs() makes it robust.
        df["_y_mfe"] = df["_mfe"].abs()

        # group by local date
        df["_day"] = df["_entry_dt_local_naive"].dt.date
        days = sorted(df["_day"].dropna().unique())
        if not days:
            continue

        # Most recent first
        days = list(reversed(days))
        if max_days_per_run > 0:
            days = days[:max_days_per_run]

        html.append('<div class="tf-trades-run">')
        html.append('<div class="tf-trades-runhead">')
        html.append(f'<div class="tf-trades-runid">{run_id}</div>')
        html.append(f'<div class="tf-trades-note">Days shown: {len(days)} | Sessions/day: {len(sessions)}</div>')
        html.append("</div>")

        if show_run_card and card_uri:
            html.append(f'<img class="tf-trades-cardimg" src="{card_uri}" alt="{run_id} card" />')

        html.append('<div class="tf-trades-days">')
        ledger_rows:  List[Dict[str, Any]] = []


        any_day = False
        if show_trade_charts:
        # existing per-day block rendering here

            for d in days:
                # weekday filter
                if weekdays_include is not None:
                    try:
                        wd = d.weekday()  # Monday=0
                        if wd not in weekdays_include:
                            continue
                    except Exception:
                        pass

                day_df = df[df["_day"] == d].sort_values("_entry_dt_local_naive")
                if len(day_df) == 0:
                    continue

                any_day = True
                # raw pnl totals (not direction-adjusted)
                day_real_pnl = float(day_df["_pnl"].sum())
                n_trades = int(len(day_df))

                html.append('<div class="tf-dayblock">')
                # html.append('<div class="tf-dayhead">')
                # html.append(f'<div class="tf-daytitle">{d.isoformat()}</div>')
                # html.append(f'<div class="tf-daysub">Trades: {n_trades} | Net PnL: {day_real_pnl:,.0f}</div>')
                # html.append("</div>")
                max_etd = float(day_df["_etd_abs"].max()) if "_etd_abs" in day_df.columns and len(day_df) else 0.0
                is_over = (etd_threshold is not None) and (max_etd > float(etd_threshold))

                ledger_rows.append({
                    "day": d.isoformat(),
                    "trades": n_trades,
                    "net_pnl": day_real_pnl,
                    "max_etd": max_etd,
                    "over": is_over,
                })

                badge = ""
                if is_over:
                    badge = f' <span class="tf-badge tf-badge--over">OVER {etd_threshold:,.0f}</span>'
                else:
                    badge = f' <span class="tf-badge">OK</span>'

                html.append('<div class="tf-dayhead">')
                html.append(f'<div class="tf-daytitle">{d.isoformat()}{badge}</div>')
                html.append(
                    f'<div class="tf-daysub">Trades: {n_trades} | Net PnL: {day_real_pnl:,.0f} | '
                    f'Max(|ETD|): {max_etd:,.0f}</div>'
                )
                html.append("</div>")

                html.append('<div class="tf-sessions">')

                for sess in sessions:
                    sess_df = day_df[day_df["_entry_dt_local_naive"].apply(lambda x: _in_session(x, sess))].copy()
                    if len(sess_df) == 0:
                        continue

                    if max_trades_per_session > 0 and len(sess_df) > max_trades_per_session:
                        sess_df = sess_df.iloc[-max_trades_per_session:]

                    sess_pnl = float(sess_df["_pnl"].sum())
                    sess_n = int(len(sess_df))

                    title = f"{run_id} — {d.isoformat()} — {sess.name}"
                    uri = _plot_bars(
                        sess_df.sort_values("_entry_dt_local_naive"),
                        title=title,
                        mfe_alpha=mfe_alpha,
                        show_cum_line=show_cum_line,
                        show_mae_bar=show_mae_bar,
                        mae_alpha=mae_alpha,
                        mae_width_ratio=mae_width_ratio,
                        mae_offset_ratio=mae_offset_ratio,
                        show_etd_line=show_etd_line,
                        etd_line_alpha=etd_line_alpha,
                        show_etd_threshold_line=show_etd_threshold_line,
                        etd_threshold=etd_threshold,
                    )

                    if not uri:
                        continue

                    html.append('<div class="tf-session">')
                    html.append('<div class="tf-sessionhead">')
                    html.append(f'<div class="tf-sessionname">{sess.name}</div>')
                    html.append(f'<div class="tf-sessionsub">Trades: {sess_n} | Net PnL: {sess_pnl:,.0f}</div>')
                    html.append("</div>")
                    html.append(f'<img src="{uri}" alt="{run_id} trades {d.isoformat()} {sess.name}" />')
                    html.append("</div>")

                html.append("</div>")  # tf-sessions
                html.append("</div>")  # tf-dayblock

        if not any_day:
            html.append('<div class="tf-trades-empty"><em>No plottable trade days found for this run.</em></div>')

        html.append("</div>")  # tf-trades-days

        if show_hourly_totals:
            try:
                import pandas as pd

                # Only include the days that actually rendered (respect max_days_per_run + weekday filter)
                shown_days_set = set()
                for d in days:
                    if weekdays_include is not None:
                        try:
                            if d.weekday() not in weekdays_include:
                                continue
                        except Exception:
                            pass
                    shown_days_set.add(d)

                df_shown = df[df["_day"].isin(list(shown_days_set))].copy()
                if len(df_shown) > 0:
                    df_shown["hour"] = df_shown["_entry_dt_local_naive"].dt.hour

                    html.append('<div class="tf-hourly">')
                    html.append('<div class="tf-hourly-title">Hourly Totals (All Shown Days)</div>')
                    html.append('<div class="tf-hourly-grid">')

                    for sess in sessions:
                        sess_df = df_shown[
                            df_shown["_entry_dt_local_naive"].apply(lambda x: _in_session(x, sess))].copy()
                        if len(sess_df) == 0:
                            continue

                        all_hours = pd.DataFrame({"hour": list(range(0, 24))})

                        if hourly_totals_mode == "net":
                            hourly = (
                                sess_df.groupby("hour", as_index=False)["_pnl"]
                                .sum()
                                .rename(columns={"_pnl": "pnl_sum"})
                                .sort_values("hour")
                            )
                            hourly = all_hours.merge(hourly, on="hour", how="left").fillna({"pnl_sum": 0.0})
                            sess_total = float(hourly["pnl_sum"].sum())

                            uri = _plot_hourly_totals(
                                hourly,
                                title=f"{run_id} — Hourly Totals — {sess.name}",
                                show_cum_line=show_cum_line,
                                mode="net",
                            )

                        else:
                            # direction_split:
                            # _dir_sign: +1 long, -1 short
                            # _pnl: positive win, negative loss
                            tmp = sess_df.copy()
                            tmp["long_profit"] = ((tmp["_dir_sign"] >= 0) & (tmp["_pnl"] >= 0)) * tmp["_pnl"]
                            tmp["short_profit"] = ((tmp["_dir_sign"] < 0) & (tmp["_pnl"] >= 0)) * tmp["_pnl"]
                            tmp["long_loss"] = ((tmp["_dir_sign"] >= 0) & (tmp["_pnl"] < 0)) * tmp["_pnl"]  # negative
                            tmp["short_loss"] = ((tmp["_dir_sign"] < 0) & (tmp["_pnl"] < 0)) * tmp["_pnl"]  # negative

                            hourly = (
                                tmp.groupby("hour", as_index=False)[
                                    ["long_profit", "short_profit", "long_loss", "short_loss"]]
                                .sum()
                                .sort_values("hour")
                            )
                            hourly = all_hours.merge(hourly, on="hour", how="left").fillna(
                                {"long_profit": 0.0, "short_profit": 0.0, "long_loss": 0.0, "short_loss": 0.0}
                            )
                            sess_total = float(
                                hourly["long_profit"].sum()
                                + hourly["short_profit"].sum()
                                + hourly["long_loss"].sum()
                                + hourly["short_loss"].sum()
                            )

                            uri = _plot_hourly_totals(
                                hourly,
                                title=f"{run_id} — Hourly Totals — {sess.name}",
                                show_cum_line=show_cum_line,
                                mode="direction_split",
                            )

                        if not uri:
                            continue

                        html.append('<div class="tf-hourly-card">')
                        html.append('<div class="tf-hourly-head">')
                        html.append(f'<div class="tf-hourly-name">{sess.name}</div>')
                        html.append(f'<div class="tf-hourly-sub">Net PnL: {sess_total:,.0f}</div>')
                        html.append("</div>")
                        html.append(f'<img src="{uri}" alt="{run_id} hourly totals {sess.name}" />')
                        html.append("</div>")

                    html.append("</div>")  # tf-hourly-grid
                    html.append("</div>")  # tf-hourly
            except Exception:
                pass

        html.append("</div>")  # tf-trades-run
        if show_ledger and ledger_rows:
            html.append('<div class="tf-ledger">')
            html.append('<div class="tf-ledger-title">Daily Ledger (Shown Days)</div>')
            html.append(
                "<table><thead><tr>"
                "<th>Day</th><th class='num'>Trades</th><th class='num'>Net PnL</th>"
                "<th class='num'>Max(|ETD|)</th><th>Status</th>"
                "</tr></thead><tbody>"
            )
            for r in ledger_rows:
                status = "OVER" if r["over"] else "OK"
                badge_cls = "tf-badge tf-badge--over" if r["over"] else "tf-badge"
                html.append(
                    "<tr>"
                    f"<td>{r['day']}</td>"
                    f"<td class='num'>{int(r['trades'])}</td>"
                    f"<td class='num'>{float(r['net_pnl']):,.0f}</td>"
                    f"<td class='num'>{float(r["max_etd"]):,.0f}</td>"
                    f"<td><span class='{badge_cls}'>{status}</span></td>"
                    "</tr>"
                )
            html.append("</tbody></table>")
            html.append("</div>")

    html.append("</div>")  # tf-trades-byday
    return "\n".join(html)
