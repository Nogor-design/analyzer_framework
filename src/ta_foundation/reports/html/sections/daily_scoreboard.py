from __future__ import annotations

from typing import Dict, Any, List, Tuple, Optional
import base64
import io
import warnings

import pandas as pd
import matplotlib.pyplot as plt

from ta_foundation.analysis.daily_matrix import build_daily_matrix, DailyMatrix
from ta_foundation.analysis.combo_selection import top_combos, score_combo, ComboScore


# -----------------------------
# Robust embedded image encoding
# -----------------------------
def _fig_to_data_uri_png(fig: plt.Figure, dpi: int = 150) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight")
    buf.seek(0)
    b64 = base64.b64encode(buf.read()).decode("ascii")
    return f"data:image/png;base64,{b64}"


def _render_img_card(data_uri: str, caption: str) -> str:
    return f"""
    <div style="margin: 14px 0;">
      <div style="font-weight:600; margin-bottom:8px;">{caption}</div>
      <img src="{data_uri}"
           style="max-width:100%; height:auto; border:1px solid #e6e6e6; border-radius:12px;" />
    </div>
    """


def _safe_tight_layout(fig: plt.Figure) -> None:
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="Tight layout not applied.*",
            category=UserWarning,
        )
        fig.tight_layout()


def _assign_bot_numbers(run_ids: List[str]) -> Tuple[List[str], Dict[str, int], Dict[int, str]]:
    sorted_ids = sorted(run_ids)
    to_num = {rid: i + 1 for i, rid in enumerate(sorted_ids)}
    to_rid = {i + 1: rid for i, rid in enumerate(sorted_ids)}
    return sorted_ids, to_num, to_rid


def _outcome_color(pnl: float, traded: bool) -> str:
    if not traded:
        return "0.7"
    if pnl > 0:
        return "g"
    if pnl < 0:
        return "r"
    return "0.4"


def _display_name(packages: Dict[str, Any], run_id: str) -> str:
    pkg = packages.get(run_id)
    derived = (getattr(pkg, "metadata", None) or {}).get("derived", {}) if pkg is not None else {}
    return str(derived.get("display_name_spaced") or derived.get("display_name") or run_id)


def _name_map(packages: Dict[str, Any]) -> Dict[str, str]:
    return {str(run_id): _display_name(packages, str(run_id)) for run_id in packages.keys()}


def _render_bot_number_key(run_ids: List[str], bot_no: Dict[str, int], display_names: Dict[str, str]) -> str:
    items = []
    for rid in run_ids:
        n = bot_no[rid]
        display = display_names.get(rid, rid)
        items.append(
            f"<div style='display:flex; gap:10px; align-items:center; padding:6px 0; border-bottom:1px dashed #eee;'>"
            f"<div style='min-width:34px; font-weight:900;'>{n}</div>"
            f"<div style='color:#333;'>{display}</div>"
            f"</div>"
        )
    return f"""
    <div style="margin-top: 10px;">
      <div style="font-weight:700; margin-bottom:8px;">Bot Number Key</div>
      <div style="border:1px solid #eee; border-radius:12px; padding:10px 12px;">
        {''.join(items)}
      </div>
    </div>
    """


def _subset_daily_matrix(dm: DailyMatrix, run_ids: List[str]) -> DailyMatrix:
    pnl = dm.pnl[run_ids].copy()
    traded = dm.traded[run_ids].copy()

    cum = pnl.fillna(0.0).cumsum()
    combined_pnl = pnl.fillna(0.0).sum(axis=1)
    combined_cum = combined_pnl.cumsum()
    active_count = traded.fillna(False).sum(axis=1).astype(int)

    return DailyMatrix(
        dates=dm.dates,
        pnl=pnl,
        traded=traded,
        cum=cum,
        combined_pnl=combined_pnl,
        combined_cum=combined_cum,
        active_count=active_count,
    )


# -----------------------------
# Core charts (bundle)
# -----------------------------
def _make_dot_scoreboard(
    dm: DailyMatrix,
    run_ids: List[str],
    bot_no: Dict[str, int],
    title: str,
    display_names: Dict[str, str] | None = None,
) -> plt.Figure:
    fig = plt.figure(figsize=(max(10, len(dm.dates) * 0.35), max(4, len(run_ids) * 0.55)))
    ax = fig.add_subplot(1, 1, 1)

    y_labels = []
    for yi, run_id in enumerate(run_ids):
        pnl_s = dm.pnl[run_id].reindex(dm.dates)
        trd_s = dm.traded[run_id].reindex(dm.dates).fillna(False)

        xs, ys, cs = [], [], []
        for i, dt in enumerate(dm.dates):
            pnl = pnl_s.iloc[i]
            traded = bool(trd_s.iloc[i])
            pnl_val = 0.0 if pd.isna(pnl) else float(pnl)
            xs.append(dt)
            ys.append(yi)
            cs.append(_outcome_color(pnl_val, traded))

        ax.scatter(xs, ys, s=60, c=cs, marker="o")
        y_labels.append(f"{bot_no[run_id]}  {(display_names or {}).get(run_id, run_id)}")

    ax.set_yticks(range(len(run_ids)))
    ax.set_yticklabels(y_labels)
    ax.set_xlabel("Date")
    ax.set_title(title)
    ax.grid(True, axis="x", alpha=0.2)
    fig.autofmt_xdate(rotation=45)
    _safe_tight_layout(fig)
    return fig


def _make_combined_daily_pnl(dm: DailyMatrix, title: str) -> plt.Figure:
    fig = plt.figure(figsize=(12, 4))
    ax = fig.add_subplot(1, 1, 1)
    ax.bar(dm.dates, dm.combined_pnl.values)
    ax.set_title(title)
    ax.set_xlabel("Date")
    ax.set_ylabel("Net PnL")
    ax.grid(True, axis="y", alpha=0.2)
    fig.autofmt_xdate(rotation=45)
    _safe_tight_layout(fig)
    return fig


def _make_equity_curves(
    dm: DailyMatrix,
    show_individual: bool,
    title: str,
    display_names: Dict[str, str] | None = None,
) -> plt.Figure:
    fig = plt.figure(figsize=(12, 5))
    ax = fig.add_subplot(1, 1, 1)

    if show_individual:
        for run_id in dm.cum.columns:
            ax.plot(
                dm.dates,
                dm.cum[run_id].values,
                linewidth=1.5,
                label=(display_names or {}).get(run_id, run_id),
            )

    ax.plot(dm.dates, dm.combined_cum.values, linewidth=3.0, label="COMBINED")
    ax.set_title(title)
    ax.set_xlabel("Date")
    ax.set_ylabel("Cumulative Net PnL")
    ax.grid(True, alpha=0.2)
    ax.legend(loc="best", fontsize=9)
    fig.autofmt_xdate(rotation=45)
    _safe_tight_layout(fig)
    return fig


def _make_active_count(dm: DailyMatrix, title: str) -> plt.Figure:
    fig = plt.figure(figsize=(12, 3.5))
    ax = fig.add_subplot(1, 1, 1)
    ax.plot(dm.dates, dm.active_count.values, linewidth=2.0)
    ax.set_title(title)
    ax.set_xlabel("Date")
    ax.set_ylabel("Active bots")
    ax.grid(True, alpha=0.2)
    fig.autofmt_xdate(rotation=45)
    _safe_tight_layout(fig)
    return fig


def _render_combo_metrics(cs: ComboScore, bot_no: Dict[str, int], display_names: Dict[str, str]) -> str:
    nums = [bot_no[r] for r in cs.run_ids]
    names = [display_names.get(r, r) for r in cs.run_ids]
    return f"""
    <div style="margin: 10px 0; border:1px solid #eee; border-radius:12px; padding:10px 12px;">
      <div style="font-weight:900; margin-bottom:6px;">
        Combo: {', '.join(map(str, nums))}
        <span style="color:#666; font-weight:600;">({', '.join(names)})</span>
      </div>
      <div style="display:flex; gap:18px; flex-wrap:wrap; font-size:13px; color:#222;">
        <div><b>Any Co-Loss (≥2 losers)</b>: {cs.any_coloss_rate*100:.1f}%</div>
        <div><b>All-Loss</b>: {cs.all_loss_rate*100:.1f}%</div>
        <div><b>Traded Days</b>: {cs.traded_days}</div>
        <div><b>Combo PnL</b>: {cs.combo_cum_end:,.2f}</div>
      </div>
    </div>
    """


def _render_combo_block(
    dm_all: DailyMatrix,
    cs: ComboScore,
    bot_no: Dict[str, int],
    show_individual_equity: bool,
    display_names: Dict[str, str],
) -> str:
    run_ids = list(cs.run_ids)
    dm = _subset_daily_matrix(dm_all, run_ids)

    fig_dot = _make_dot_scoreboard(dm, run_ids, bot_no, title="Daily Win/Loss Scoreboard (Combo)", display_names=display_names)
    fig_pnl = _make_combined_daily_pnl(dm, title="Combined Daily Net PnL (Combo)")
    fig_eq = _make_equity_curves(dm, show_individual=show_individual_equity, title="Running Net PnL (Combo)", display_names=display_names)
    fig_act = _make_active_count(dm, title="Bots Active Per Day (Combo)")

    html = []
    html.append(_render_combo_metrics(cs, bot_no, display_names))
    html.append(_render_img_card(_fig_to_data_uri_png(fig_dot), "Per-bot daily outcomes (combo)"))
    html.append(_render_img_card(_fig_to_data_uri_png(fig_pnl), "Combined daily PnL (combo)"))
    html.append(_render_img_card(_fig_to_data_uri_png(fig_eq), "Running PnL (combo)"))
    html.append(_render_img_card(_fig_to_data_uri_png(fig_act), "Participation (combo)"))

    plt.close(fig_dot)
    plt.close(fig_pnl)
    plt.close(fig_eq)
    plt.close(fig_act)

    return "\n".join(html)


# -----------------------------
# YAML-driven combo selection
# -----------------------------
def _coerce_int_list(x) -> Optional[List[int]]:
    if x is None:
        return None
    if isinstance(x, list):
        out = []
        for v in x:
            try:
                out.append(int(v))
            except Exception:
                return None
        return out
    return None


def _coerce_str_list(x) -> Optional[List[str]]:
    if x is None:
        return None
    if isinstance(x, list):
        out = []
        for v in x:
            if v is None:
                return None
            out.append(str(v))
        return out
    return None


def _explicit_combos_from_options(spec: Dict[str, Any], botnum_to_runid: Dict[int, str], all_run_ids: List[str]) -> List[Tuple[str, ...]]:
    """
    Supports any of:
      - combos: [ { run_ids:[...]} , { bot_numbers:[...]} ]
      - combo_run_ids: [[...],[...]]
      - combo_bot_numbers: [[...],[...]]
    """
    combos: List[Tuple[str, ...]] = []

    # 1) combos: list[dict]
    if isinstance(spec.get("combos"), list):
        for item in spec["combos"]:
            if not isinstance(item, dict):
                continue
            rids = _coerce_str_list(item.get("run_ids"))
            bns = _coerce_int_list(item.get("bot_numbers"))

            if rids:
                picked = [r for r in rids if r in all_run_ids]
                if len(picked) == len(rids):
                    combos.append(tuple(sorted(picked)))
            elif bns:
                try:
                    picked = [botnum_to_runid[n] for n in bns]
                except KeyError:
                    continue
                combos.append(tuple(sorted(picked)))

    # 2) combo_run_ids: list[list[str]]
    if isinstance(spec.get("combo_run_ids"), list):
        for arr in spec["combo_run_ids"]:
            rids = _coerce_str_list(arr)
            if rids and all(r in all_run_ids for r in rids):
                combos.append(tuple(sorted(rids)))

    # 3) combo_bot_numbers: list[list[int]]
    if isinstance(spec.get("combo_bot_numbers"), list):
        for arr in spec["combo_bot_numbers"]:
            bns = _coerce_int_list(arr)
            if not bns:
                continue
            try:
                picked = [botnum_to_runid[n] for n in bns]
            except KeyError:
                continue
            combos.append(tuple(sorted(picked)))

    # Dedup
    combos = sorted(set(combos))
    return combos


def _render_combo_set(
    dm_all: DailyMatrix,
    run_ids_sorted: List[str],
    bot_no: Dict[str, int],
    botnum_to_runid: Dict[int, str],
    spec: Dict[str, Any],
    show_individual_equity: bool,
    display_names: Dict[str, str],
) -> str:
    """
    spec fields (all optional):
      - name: str
      - mode: "top" | "explicit" | "both" | "off"   (default "top")
      - k: int (required for top; also used to filter explicit if provided)
      - top_n: int (default 5)
      - beam_width: int (default 200)
      - max_render: int (cap charts rendered; default = top_n)
      - combos / combo_run_ids / combo_bot_numbers (for explicit)
    """
    name = str(spec.get("name") or "Combo Set").strip()
    mode = str(spec.get("mode") or "top").strip().lower()
    k = spec.get("k", None)

    try:
        k_i = int(k) if k is not None else None
    except Exception:
        k_i = None

    top_n = int(spec.get("top_n", 5))
    beam_width = int(spec.get("beam_width", 200))
    max_render = int(spec.get("max_render", top_n))

    pnl = dm_all.pnl
    traded = dm_all.traded.fillna(False)

    combos_scores: List[ComboScore] = []

    # Explicit first (if mode includes explicit)
    if mode in ("explicit", "both"):
        explicit = _explicit_combos_from_options(spec, botnum_to_runid, run_ids_sorted)
        if k_i is not None:
            explicit = [c for c in explicit if len(c) == k_i]
        for c in explicit:
            combos_scores.append(score_combo(pnl, traded, c))

    # Top-N (if mode includes top)
    if mode in ("top", "both"):
        if k_i is None:
            return f"<div style='margin-top:16px; color:#b00;'><b>{name}:</b> missing required 'k' for mode=top.</div>"
        if len(run_ids_sorted) < k_i:
            return f"<div style='margin-top:16px;'><b>{name}:</b> not enough bots for k={k_i}.</div>"
        combos_scores.extend(top_combos(pnl, traded, run_ids_sorted, k=k_i, top_n=top_n, beam_width=beam_width))

    # Dedup by run_ids keeping best sort order
    def sort_key(cs: ComboScore):
        return (cs.any_coloss_rate, cs.all_loss_rate, -cs.combo_cum_end, -cs.traded_days)

    best: Dict[Tuple[str, ...], ComboScore] = {}
    for cs in combos_scores:
        prev = best.get(cs.run_ids)
        if prev is None or sort_key(cs) < sort_key(prev):
            best[cs.run_ids] = cs

    ranked = list(best.values())
    ranked.sort(key=sort_key)

    if not ranked:
        return f"<div style='margin-top:16px;'><b>{name}:</b> no combos resolved.</div>"

    # Summary table
    rows = []
    for idx, cs in enumerate(ranked[: max_render], start=1):
        nums = [bot_no[r] for r in cs.run_ids]
        rows.append(
            f"<tr>"
            f"<td style='padding:8px; border-bottom:1px solid #eee; font-weight:900;'>{idx}</td>"
            f"<td style='padding:8px; border-bottom:1px solid #eee; font-weight:900;'>{', '.join(map(str, nums))}</td>"
            f"<td style='padding:8px; border-bottom:1px solid #eee;'>{cs.any_coloss_rate*100:.1f}%</td>"
            f"<td style='padding:8px; border-bottom:1px solid #eee;'>{cs.all_loss_rate*100:.1f}%</td>"
            f"<td style='padding:8px; border-bottom:1px solid #eee; text-align:right;'>{cs.combo_cum_end:,.2f}</td>"
            f"<td style='padding:8px; border-bottom:1px solid #eee; text-align:right;'>{cs.traded_days}</td>"
            f"</tr>"
        )

    html = []
    html.append(f"<h3 style='margin-top:18px;'>{name}</h3>")
    html.append(f"""
      <div style="overflow-x:auto; border:1px solid #eee; border-radius:12px;">
        <table style="width:100%; border-collapse:collapse; font-size:13px;">
          <thead>
            <tr style="background:#fafafa;">
              <th style="text-align:left; padding:8px; border-bottom:1px solid #eee;">Rank</th>
              <th style="text-align:left; padding:8px; border-bottom:1px solid #eee;">Bot #s</th>
              <th style="text-align:left; padding:8px; border-bottom:1px solid #eee;">Any Co-Loss</th>
              <th style="text-align:left; padding:8px; border-bottom:1px solid #eee;">All-Loss</th>
              <th style="text-align:right; padding:8px; border-bottom:1px solid #eee;">Combo PnL</th>
              <th style="text-align:right; padding:8px; border-bottom:1px solid #eee;">Traded Days</th>
            </tr>
          </thead>
          <tbody>
            {''.join(rows)}
          </tbody>
        </table>
      </div>
    """)

    # Full chart blocks for each ranked combo (capped)
    for cs in ranked[: max_render]:
        html.append(_render_combo_block(dm_all, cs, bot_no, show_individual_equity, display_names))

    return "\n".join(html)


# -----------------------------
# Summary table (same as your earlier working one)
# -----------------------------
def _render_summary_table(dm: DailyMatrix, display_names: Dict[str, str]) -> str:
    rows = []
    for run_id in dm.pnl.columns:
        pnl_s = dm.pnl[run_id]
        trd_s = dm.traded[run_id].fillna(False)

        traded_days = int(trd_s.sum())
        wins = int(((pnl_s.fillna(0.0) > 0) & trd_s).sum())
        losses = int(((pnl_s.fillna(0.0) < 0) & trd_s).sum())
        flats = int(((pnl_s.fillna(0.0) == 0) & trd_s).sum())
        no_trade = int((~trd_s).sum())

        total = float(pnl_s.fillna(0.0).sum())
        win_rate = (wins / traded_days * 100.0) if traded_days > 0 else 0.0
        rows.append((run_id, total, traded_days, wins, losses, flats, no_trade, win_rate))

    combined_total = float(dm.combined_pnl.sum())
    combined_traded_days = int((dm.active_count > 0).sum())
    combined_win_days = int((dm.combined_pnl > 0).sum())
    combined_loss_days = int((dm.combined_pnl < 0).sum())
    combined_flat_days = int(((dm.combined_pnl == 0) & (dm.active_count > 0)).sum())
    combined_no_trade = int((dm.active_count == 0).sum())
    combined_win_rate = (combined_win_days / combined_traded_days * 100.0) if combined_traded_days > 0 else 0.0

    rows.append(("COMBINED", combined_total, combined_traded_days, combined_win_days, combined_loss_days, combined_flat_days, combined_no_trade, combined_win_rate))

    def fmt_money(x: float) -> str:
        return f"{x:,.2f}"

    html_rows = []
    for run_id, total, traded_days, wins, losses, flats, no_trade, win_rate in rows:
        label = "COMBINED" if run_id == "COMBINED" else display_names.get(run_id, run_id)
        html_rows.append(
            f"<tr>"
            f"<td style='text-align:left; font-weight:600;'>{label}</td>"
            f"<td style='text-align:right;'>{fmt_money(total)}</td>"
            f"<td style='text-align:right;'>{traded_days}</td>"
            f"<td style='text-align:right;'>{wins}</td>"
            f"<td style='text-align:right;'>{losses}</td>"
            f"<td style='text-align:right;'>{flats}</td>"
            f"<td style='text-align:right;'>{no_trade}</td>"
            f"<td style='text-align:right;'>{win_rate:.1f}%</td>"
            f"</tr>"
        )

    return f"""
    <div style="margin-top: 10px;">
      <div style="font-weight:700; margin-bottom:8px;">Summary</div>
      <div style="overflow-x:auto;">
        <table style="border-collapse: collapse; width: 100%; font-size: 13px;">
          <thead>
            <tr>
              <th style="text-align:left; border-bottom:1px solid #ddd; padding:6px;">Run</th>
              <th style="text-align:right; border-bottom:1px solid #ddd; padding:6px;">Total Net PnL</th>
              <th style="text-align:right; border-bottom:1px solid #ddd; padding:6px;">Traded Days</th>
              <th style="text-align:right; border-bottom:1px solid #ddd; padding:6px;">Wins</th>
              <th style="text-align:right; border-bottom:1px solid #ddd; padding:6px;">Losses</th>
              <th style="text-align:right; border-bottom:1px solid #ddd; padding:6px;">Flats</th>
              <th style="text-align:right; border-bottom:1px solid #ddd; padding:6px;">No-Trade</th>
              <th style="text-align:right; border-bottom:1px solid #ddd; padding:6px;">Win Rate</th>
            </tr>
          </thead>
          <tbody>
            {''.join(html_rows)}
          </tbody>
        </table>
      </div>
    </div>
    """


# -----------------------------
# Section entrypoint
# -----------------------------
def render_daily_scoreboard(ctx: Dict[str, Any]) -> str:
    """
    report.yaml options (new + existing):
      - max_runs: int|null
      - show_individual_equity: bool (default true)
      - include_summary_table: bool (default true)
      - include_all_bot_charts: bool (default true)

      - combo_sets: list[dict]
        Each dict supports:
          - name: str
          - mode: "top" | "explicit" | "both" | "off"
          - k: int
          - top_n: int
          - beam_width: int
          - max_render: int
          - combos: [ { run_ids:[...]} | { bot_numbers:[...] } ... ]
          - combo_run_ids: [[...],[...]]
          - combo_bot_numbers: [[...],[...]]
    """
    packages = ctx.get("packages") or {}
    options = ctx.get("options") or {}

    max_runs = options.get("max_runs", None)
    show_individual_equity = bool(options.get("show_individual_equity", True))
    include_summary_table = bool(options.get("include_summary_table", True))
    include_all_bot_charts = bool(options.get("include_all_bot_charts", True))

    dm = build_daily_matrix(packages)
    if dm.dates is None or len(dm.dates) == 0 or dm.pnl.shape[1] == 0:
        return "<div>No daily data found.</div>"

    run_ids = list(dm.pnl.columns)
    if max_runs is not None:
        try:
            run_ids = run_ids[: int(max_runs)]
        except Exception:
            pass

    run_ids_sorted, bot_no, botnum_to_runid = _assign_bot_numbers(run_ids)
    display_names = _name_map(packages)

    parts: List[str] = []
    parts.append(_render_bot_number_key(run_ids_sorted, bot_no, display_names))

    # Baseline “all-bots” charts
    if include_all_bot_charts:
        fig_dot = _make_dot_scoreboard(dm, run_ids_sorted, bot_no, title="Daily Win/Loss Scoreboard (All bots)", display_names=display_names)
        fig_pnl = _make_combined_daily_pnl(dm, title="Combined Daily Net PnL (All bots)")
        fig_eq = _make_equity_curves(dm, show_individual=show_individual_equity, title="Running Net PnL (All bots)", display_names=display_names)
        fig_act = _make_active_count(dm, title="Bots Active Per Day (All bots)")

        parts.append(_render_img_card(_fig_to_data_uri_png(fig_dot), "All bots: Per-bot daily outcomes"))
        parts.append(_render_img_card(_fig_to_data_uri_png(fig_pnl), "All bots: Combined daily PnL"))
        parts.append(_render_img_card(_fig_to_data_uri_png(fig_eq), "All bots: Running PnL"))
        parts.append(_render_img_card(_fig_to_data_uri_png(fig_act), "All bots: Participation"))

        plt.close(fig_dot)
        plt.close(fig_pnl)
        plt.close(fig_eq)
        plt.close(fig_act)

    # Combo sets (YAML-driven)
    combo_sets = options.get("combo_sets")
    if isinstance(combo_sets, list) and combo_sets:
        parts.append("<h2 style='margin-top:22px;'>Combo Analysis</h2>")
        for spec in combo_sets:
            if not isinstance(spec, dict):
                continue
            mode = str(spec.get("mode") or "top").lower()
            if mode == "off":
                continue
            parts.append(_render_combo_set(
                dm_all=dm,
                run_ids_sorted=run_ids_sorted,
                bot_no=bot_no,
                botnum_to_runid=botnum_to_runid,
                spec=spec,
                show_individual_equity=show_individual_equity,
                display_names=display_names,
            ))

    if include_summary_table:
        parts.append(_render_summary_table(dm, display_names))

    return "\n".join(parts)
