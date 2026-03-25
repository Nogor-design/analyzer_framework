from __future__ import annotations

"""
anchor_interaction_hourly_profile.py
-------------------------------------
Shows TP/SL expectancy broken down by hour of day using structural MA-cross
segments.  For each (hour, tp_atr, sl_atr) cell the section computes:

  win  = mfe_atr >= tp_atr  AND  mfe_before_mae == True
  lose = mae_atr >= sl_atr  AND  NOT win
  expectancy = win_rate * tp_atr - (1 - win_rate) * sl_atr

A heatmap (hours × TP/SL combos) and a sample-count bar chart are
embedded as a single base64 PNG per run.
"""

from typing import Any, Dict, List, Optional, Tuple
import html as html_lib

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors

from ta_foundation.reports.html.embed import fig_to_base64_png


# ---------------------------------------------------------------------------
# Computation
# ---------------------------------------------------------------------------

def _compute_hourly_expectancy(
    merged: pd.DataFrame,
    tp_grid: List[float],
    sl_grid: List[float],
    min_decisive: int = 5,
) -> pd.DataFrame:
    """
    For each (hour, tp_atr, sl_atr) compute win_rate and expectancy.

    merged must have columns: hour (int), mfe_atr, mae_atr, mfe_before_mae.
    Returns DataFrame with columns: hour, tp_atr, sl_atr, n, n_decisive, win_rate, expectancy.
    """
    rows: List[Dict] = []
    for hour, hdf in merged.groupby("hour"):
        n_total = len(hdf)
        for tp in tp_grid:
            for sl in sl_grid:
                win_mask = (hdf["mfe_atr"] >= tp) & (hdf["mfe_before_mae"] == True)  # noqa: E712
                lose_mask = (hdf["mae_atr"] >= sl) & (~win_mask)
                n_win = int(win_mask.sum())
                n_lose = int(lose_mask.sum())
                n_decisive = n_win + n_lose
                if n_decisive < min_decisive:
                    win_rate = np.nan
                    expectancy = np.nan
                else:
                    win_rate = n_win / n_decisive
                    expectancy = win_rate * tp - (1.0 - win_rate) * sl
                rows.append({
                    "hour": int(hour),
                    "tp_atr": float(tp),
                    "sl_atr": float(sl),
                    "n": n_total,
                    "n_decisive": n_decisive,
                    "win_rate": win_rate,
                    "expectancy": expectancy,
                })

    return pd.DataFrame(rows)


def _best_pair_label(tp: float, sl: float) -> str:
    return f"TP{tp:.2g}/SL{sl:.2g}"


# ---------------------------------------------------------------------------
# Chart
# ---------------------------------------------------------------------------

def _build_hourly_chart(
    run_id: str,
    hourly_df: pd.DataFrame,
    all_hours: List[int],
    tp_grid: List[float],
    sl_grid: List[float],
) -> Optional[str]:
    """
    Build a two-panel figure:
      Top  — heatmap of expectancy (hours × TP/SL combos)
      Bottom — sample count bar chart per hour

    Returns base64 PNG data URI or None on failure.
    """
    if hourly_df.empty:
        return None

    combos: List[Tuple[float, float]] = [(tp, sl) for tp in tp_grid for sl in sl_grid]
    combo_labels = [_best_pair_label(tp, sl) for tp, sl in combos]
    n_combos = len(combos)

    # Build heatmap matrix: rows=hours, cols=combos
    heat = np.full((len(all_hours), n_combos), np.nan)
    sample_counts = np.zeros(len(all_hours), dtype=int)

    for h_idx, hour in enumerate(all_hours):
        h_sub = hourly_df[hourly_df["hour"] == hour]
        if h_sub.empty:
            continue
        # sample count from any row (n is per-hour total, same for all combos)
        sample_counts[h_idx] = int(h_sub["n"].iloc[0]) if "n" in h_sub.columns else 0
        for c_idx, (tp, sl) in enumerate(combos):
            match = h_sub[(h_sub["tp_atr"] == tp) & (h_sub["sl_atr"] == sl)]
            if not match.empty:
                heat[h_idx, c_idx] = float(match["expectancy"].iloc[0])

    # Dark theme
    bg = "#1a1a2e"
    panel_bg = "#16213e"
    text_col = "#e0e0e0"
    grid_col = "#2a2a4a"

    fig_h = max(5, len(all_hours) * 0.35 + 3)
    fig, (ax_heat, ax_bar) = plt.subplots(
        2, 1,
        figsize=(max(10, n_combos * 0.7 + 2), fig_h),
        gridspec_kw={"height_ratios": [4, 1]},
        facecolor=bg,
    )

    for ax in (ax_heat, ax_bar):
        ax.set_facecolor(panel_bg)
        for spine in ax.spines.values():
            spine.set_edgecolor(grid_col)

    # --- heatmap ---
    vmax = np.nanmax(np.abs(heat)) if not np.all(np.isnan(heat)) else 1.0
    vmax = max(vmax, 0.01)

    cmap = mcolors.LinearSegmentedColormap.from_list(
        "rg", ["#c0392b", "#2c2c2c", "#27ae60"]
    )

    im = ax_heat.imshow(
        heat,
        aspect="auto",
        cmap=cmap,
        vmin=-vmax,
        vmax=vmax,
        interpolation="nearest",
    )

    ax_heat.set_xticks(range(n_combos))
    ax_heat.set_xticklabels(combo_labels, rotation=45, ha="right", fontsize=7, color=text_col)
    ax_heat.set_yticks(range(len(all_hours)))
    ax_heat.set_yticklabels([f"{h:02d}:00" for h in all_hours], fontsize=8, color=text_col)
    ax_heat.set_ylabel("Hour of Day", color=text_col, fontsize=9)
    ax_heat.tick_params(colors=text_col)

    # Annotate cells
    for h_idx in range(len(all_hours)):
        for c_idx in range(n_combos):
            val = heat[h_idx, c_idx]
            if not np.isnan(val):
                ax_heat.text(
                    c_idx, h_idx, f"{val:.2f}",
                    ha="center", va="center",
                    fontsize=6.5,
                    color="white" if abs(val) > vmax * 0.4 else text_col,
                )

    cbar = fig.colorbar(im, ax=ax_heat, fraction=0.03, pad=0.02)
    cbar.set_label("Expectancy (ATR)", color=text_col, fontsize=8)
    cbar.ax.yaxis.set_tick_params(color=text_col)
    plt.setp(cbar.ax.yaxis.get_ticklabels(), color=text_col, fontsize=7)

    ax_heat.set_title(
        f"{html_lib.unescape(run_id)} — TP/SL Expectancy by Hour of Day",
        color=text_col, fontsize=10, pad=8,
    )

    # --- sample bar ---
    bar_x = range(len(all_hours))
    bar_colors = ["#3a86ff" if c > 0 else "#555555" for c in sample_counts]
    ax_bar.bar(bar_x, sample_counts, color=bar_colors, width=0.7)
    ax_bar.set_xticks(list(bar_x))
    ax_bar.set_xticklabels([f"{h:02d}" for h in all_hours], fontsize=7, color=text_col)
    ax_bar.set_ylabel("Segments", color=text_col, fontsize=8)
    ax_bar.tick_params(colors=text_col)
    ax_bar.yaxis.grid(True, color=grid_col, linewidth=0.5)
    ax_bar.set_axisbelow(True)
    ax_bar.set_xlabel("Hour (local)", color=text_col, fontsize=8)

    plt.tight_layout(pad=1.2)

    data_uri = fig_to_base64_png(fig)
    plt.close(fig)
    return data_uri


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------

def render_anchor_interaction_hourly_profile(ctx: Dict[str, Any]) -> str:
    packages = ctx.get("packages", {}) or {}
    options = ctx.get("options") or {}
    all_options = ctx.get("all_options") or {}

    if not packages:
        return "<h3>TP/SL by Hour of Day</h3><div class='muted'>No packages loaded.</div>"

    selected_run_ids = options.get("run_ids") or []
    if isinstance(selected_run_ids, str):
        selected_run_ids = [selected_run_ids]
    selected_run_ids = {str(x).strip() for x in selected_run_ids if str(x).strip()}

    min_decisive = int(options.get("min_decisive", 5) or 5)

    # Pull TP/SL grid from anchor_interaction config
    ai_cfg = all_options.get("anchor_interaction") or {}
    tp_sl_cfg = ai_cfg.get("tp_sl") or {}
    tp_grid: List[float] = [float(v) for v in (tp_sl_cfg.get("tp_grid") or [1.0, 1.5, 2.0])]
    sl_grid: List[float] = [float(v) for v in (tp_sl_cfg.get("sl_grid") or [0.5, 0.8, 1.0])]

    parts: List[str] = ["<h3>TP/SL Recommendations by Hour of Day</h3>"]
    parts.append(
        "<p class='muted' style='font-size:0.85em'>"
        "Expectancy (ATR) per hour using structural MA-cross segments. "
        "Cells with fewer than "
        f"<b>{min_decisive}</b> decisive outcomes are blank. "
        "Green = positive expectancy, red = negative."
        "</p>"
    )

    rendered = 0
    for run_id, pkg in packages.items():
        if selected_run_ids and run_id not in selected_run_ids:
            continue

        assets = getattr(pkg, "assets", {}) or {}
        ai_assets = assets.get("anchor_interaction") or {}

        segments: pd.DataFrame = ai_assets.get("segments")
        seg_stats: pd.DataFrame = ai_assets.get("segment_path_stats")

        if not isinstance(segments, pd.DataFrame) or segments.empty:
            continue
        if not isinstance(seg_stats, pd.DataFrame) or seg_stats.empty:
            continue

        # Filter out censored segments
        if "censored" in segments.columns:
            seg_sub = segments[segments["censored"] == False].copy()  # noqa: E712
        else:
            seg_sub = segments.copy()

        if seg_sub.empty:
            continue

        # Extract hour from entry_ts
        if "entry_ts" not in seg_sub.columns:
            continue

        try:
            entry_ts = pd.to_datetime(seg_sub["entry_ts"], utc=True).dt.tz_convert("America/Denver")
            seg_sub = seg_sub.copy()
            seg_sub["hour"] = entry_ts.dt.hour
        except Exception:
            continue

        # Join with path stats on segment_id
        needed_cols = ["segment_id", "mfe_atr", "mae_atr", "mfe_before_mae"]
        avail_cols = [c for c in needed_cols if c in seg_stats.columns]
        if "segment_id" not in avail_cols or "mfe_atr" not in avail_cols or "mae_atr" not in avail_cols:
            continue

        merge_stats = seg_stats[avail_cols].copy()
        if "segment_id" not in seg_sub.columns:
            continue

        merged = seg_sub[["segment_id", "hour"]].merge(merge_stats, on="segment_id", how="inner")
        merged = merged.dropna(subset=["mfe_atr", "mae_atr"])

        if merged.empty:
            continue

        if "mfe_before_mae" not in merged.columns:
            merged["mfe_before_mae"] = True

        hourly_df = _compute_hourly_expectancy(merged, tp_grid, sl_grid, min_decisive=min_decisive)
        if hourly_df.empty:
            continue

        all_hours = sorted(merged["hour"].unique().tolist())

        data_uri = _build_hourly_chart(
            run_id=str(run_id),
            hourly_df=hourly_df,
            all_hours=all_hours,
            tp_grid=tp_grid,
            sl_grid=sl_grid,
        )

        if data_uri:
            parts.append(
                f"<div style='margin:16px 0'>"
                f"<img src='{data_uri}' style='max-width:100%;border-radius:6px'>"
                f"</div>"
            )
            rendered += 1
        else:
            parts.append(
                f"<div class='muted'>{html_lib.escape(str(run_id))}: "
                "chart could not be rendered (insufficient data).</div>"
            )

    if rendered == 0:
        parts.append(
            "<div class='muted'>No hourly profile data available. "
            "Ensure MA Anchor analysis ran successfully and segments were detected.</div>"
        )

    return "\n".join(parts)
