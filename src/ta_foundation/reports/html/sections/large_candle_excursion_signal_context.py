from __future__ import annotations

"""
Signal Candle Context Report Section
======================================
Renders four context-dimension tables:
  1. Directional Context Effects     (prev direction, streak, engulf)
  2. MA / VWAP Location Effects      (above/below/extended per MA/VWAP)
  3. Key Level Interaction Effects   (nearest level, interaction label)
  4. Trend Alignment Effects         (MA slopes, stacking, alignment)

Plus new interaction tables:
  - prev_direction × candle_size
  - vwap_ext × candle_size
  - ma100_location × trend_alignment
  - session × level_interaction
  - prev_direction × level_interaction
"""

from typing import Any, Dict, List, Optional

from ta_foundation.reports.html.sections.large_candle_excursion_downstream_common import (
    cell, fmt, hdr, info_box, section_title,
)


def _get_lce(ctx: dict):
    """Extract large_candle_excursion derived payload from ctx."""
    for pkg in (ctx.get("packages") or {}).values():
        d = getattr(pkg, "metadata", {}).get("derived", {})
        if "large_candle_excursion" in d:
            return d["large_candle_excursion"]
    # Also check top-level ctx for standalone use
    return ctx.get("large_candle_excursion")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        import math
        f = float(v)
        return default if math.isnan(f) else f
    except Exception:
        return default


def _lift_cell(lift: Optional[float]) -> str:
    """Return a cell with colour coding for lift vs baseline."""
    if lift is None:
        return cell("—")
    color = "#1b5e20" if lift >= 3 else ("#c62828" if lift <= -3 else "#555")
    sign  = "+" if lift >= 0 else ""
    return cell(f'<span style="color:{color};font-weight:bold">{sign}{lift:.1f} pp</span>')


def _render_context_table(
    title: str,
    rows: List[Dict[str, Any]],
    bucket_key: str,
) -> str:
    """Generic context table: Bucket | N | Cont WR | Rev WR | Edge | Lift | Better | 95% CI"""
    if not rows:
        return f'<p style="color:#888;font-size:12px">{title}: no data.</p>'

    headers = ["Bucket", "N", "Cont WR", "Rev WR", "Edge pp",
               "Cont Lift", "Rev Lift", "95% CI lo–hi", "Better"]
    hdr_row = "".join(hdr(h) for h in headers)
    body = []
    for r in rows:
        bucket = r.get(bucket_key, "—")
        n      = r.get("n_observations", 0)
        cwr    = r.get("cont_win_rate")
        rwr    = r.get("rev_win_rate")
        edge   = abs(_safe_float(cwr) - _safe_float(rwr)) if cwr is not None and rwr is not None else None
        ci_lo  = r.get("cont_wr_ci_lo")
        ci_hi  = r.get("cont_wr_ci_hi")
        ci_str = f"{ci_lo:.1f}–{ci_hi:.1f}%" if ci_lo is not None and ci_hi is not None else "—"
        cont_lift = r.get("cont_lift_pp")
        rev_lift  = r.get("rev_lift_pp")
        better = r.get("better_mode", "—")

        row_bg = "#f1f8e9" if (cont_lift or 0) >= 3 else ("#fce4ec" if (cont_lift or 0) <= -3 else "")
        row_style = f' style="background:{row_bg}"' if row_bg else ""

        body.append(
            f"<tr{row_style}>"
            + cell(str(bucket), bold=True)
            + cell(str(n))
            + cell(fmt(cwr, 1, "%"))
            + cell(fmt(rwr, 1, "%"))
            + cell(fmt(edge, 1, " pp") if edge is not None else "—", bg="#e8f5e9" if edge and edge >= 4 else "")
            + _lift_cell(cont_lift)
            + _lift_cell(rev_lift)
            + cell(f'<span style="font-size:10px;color:#777">{ci_str}</span>')
            + cell(str(better))
            + "</tr>"
        )

    return (
        section_title(title)
        + '<div style="overflow-x:auto"><table style="border-collapse:collapse;width:100%;font-size:12px">'
        + f"<thead><tr>{hdr_row}</tr></thead><tbody>{''.join(body)}</tbody></table></div>"
        + '<p style="font-size:10px;color:#888;margin-top:4px">'
        + 'Lift = WR vs population baseline (green ≥ +3 pp, red ≤ −3 pp). '
        + '95% CI = Wilson interval for continuation win rate. '
        + 'Edge = |Cont WR − Rev WR|.</p>'
    )


def _render_interaction_table(
    title: str,
    rows: List[Dict[str, Any]],
    col_a: str,
    col_b: str,
) -> str:
    if not rows:
        return ""
    headers = [col_a.replace("_", " ").title(), col_b.replace("_", " ").title(),
               "N", "Cont WR", "Rev WR", "Edge pp", "Better"]
    hdr_row = "".join(hdr(h) for h in headers)
    body = []
    for r in rows:
        n   = r.get("n_observations", 0)
        if n < 5:
            continue
        cwr = r.get("cont_win_rate")
        rwr = r.get("rev_win_rate")
        edge = abs(_safe_float(cwr) - _safe_float(rwr)) if cwr is not None and rwr is not None else None
        body.append(
            "<tr>"
            + cell(str(r.get(col_a, "—")), bold=True)
            + cell(str(r.get(col_b, "—")))
            + cell(str(n))
            + cell(fmt(cwr, 1, "%"))
            + cell(fmt(rwr, 1, "%"))
            + cell(fmt(edge, 1, " pp") if edge is not None else "—",
                   bg="#e8f5e9" if edge and edge >= 4 else "")
            + cell(str(r.get("better_mode", "—")))
            + "</tr>"
        )
    if not body:
        return ""
    return (
        section_title(title)
        + '<div style="overflow-x:auto"><table style="border-collapse:collapse;width:100%;font-size:12px">'
        + f"<thead><tr>{hdr_row}</tr></thead><tbody>{''.join(body)}</tbody></table></div>"
    )


# ---------------------------------------------------------------------------
# Section renderer
# ---------------------------------------------------------------------------

def render_large_candle_excursion_signal_context(ctx: dict) -> str:
    lce = _get_lce(ctx)
    if lce is None:
        return info_box("Signal candle context: source analytics missing.")

    context = lce.get("context_analysis") or {}
    if not context.get("enabled"):
        return info_box(
            "Signal candle context analysis is disabled or produced no output. "
            "Add these keys under <code>large_candle_excursion:</code> in your YAML:<br>"
            "<pre style='font-size:10px;background:#f5f5f5;padding:6px'>"
            "directional_context:\n  enabled: true\n"
            "ma_vwap_context:\n  enabled: true\n"
            "key_level_context:\n  enabled: true\n"
            "trend_state_context:\n  enabled: true</pre>"
            "See the <b>Context Coverage Diagnostics</b> section for per-module details."
        )

    interactions = context.get("interactions") or {}

    html = '<div style="font-family:Arial,sans-serif">'

    # Baseline banner
    baseline = context.get("population_baseline") or {}
    if baseline:
        cont_wr = baseline.get("cont_win_rate")
        rev_wr  = baseline.get("rev_win_rate")
        n_ev    = baseline.get("n_events", 0)
        html += (
            f'<div style="background:#e3f2fd;border:1px solid #90caf9;border-radius:5px;'
            f'padding:8px 14px;margin-bottom:12px;font-size:12px">'
            f'<b>Population baseline</b> — N={n_ev} deduplicated events | '
            f'Continuation WR: {fmt(cont_wr, 1, "%")} | Reversal WR: {fmt(rev_wr, 1, "%")} '
            f'<span style="color:#777">(lift values are relative to these baselines)</span>'
            f'</div>'
        )

    # ------------------------------------------------------------------
    # 1. Directional context
    # ------------------------------------------------------------------
    dir_ctx = context.get("directional_context") or {}
    if dir_ctx.get("enabled"):
        html += section_title("Directional Context Effects")
        html += (
            '<p style="font-size:12px;color:#555;margin-bottom:8px">'
            "How the signal candle's relationship to the <em>previous</em> candle affects "
            "forward continuation vs reversal odds. "
            "Same = signal in same direction as prev candle (continuation impulse). "
            "Opposite = against prev candle (potential reversal). "
            "Streak = consecutive same-direction bars before signal. "
            "Engulf = signal bar's range fully covers prev bar.</p>"
        )
        html += _render_context_table(
            "Prev Direction vs Signal Direction",
            dir_ctx.get("by_prev_direction_bucket") or dir_ctx.get("by_prev_direction") or [],
            "prev_direction_bucket",
        )
        html += _render_context_table(
            "Direction Streak",
            dir_ctx.get("by_streak_bucket") or dir_ctx.get("by_streak") or [],
            "streak_bucket",
        )
        html += _render_context_table(
            "Engulf Effect",
            dir_ctx.get("by_engulf_bucket") or dir_ctx.get("by_engulf") or [],
            "engulf_bucket",
        )
        if "prev_direction_x_size" in interactions:
            html += _render_interaction_table(
                "Prev Direction × Candle Size",
                interactions["prev_direction_x_size"],
                "prev_direction_bucket", "candle_bucket",
            )

    # ------------------------------------------------------------------
    # 2. MA / VWAP location context
    # ------------------------------------------------------------------
    ma_ctx = context.get("ma_vwap_context") or {}
    if ma_ctx.get("enabled"):
        html += section_title("MA / VWAP Location Effects")
        html += (
            '<p style="font-size:12px;color:#555;margin-bottom:8px">'
            "Whether price is above, at, or below key MAs/VWAP at the time of the signal candle. "
            "Extension buckets show how far price has stretched from the indicator (ATR-normalized): "
            "near = &lt;0.25 ATR, moderate = 0.25–0.75 ATR, extended = &gt;0.75 ATR.</p>"
        )
        for col, label in [
            ("ma100_location_bucket", "MA100 Location"),
            ("ma200_location_bucket", "MA200 Location"),
            ("vwap_location_bucket",  "VWAP Location"),
            ("ma100_ext_bucket",      "MA100 Extension"),
            ("ma200_ext_bucket",      "MA200 Extension"),
            ("vwap_ext_bucket",       "VWAP Extension"),
        ]:
            rows = ma_ctx.get(f"by_{col}") or []
            if rows:
                html += _render_context_table(label, rows, col)

        if "vwap_ext_x_size" in interactions:
            html += _render_interaction_table(
                "VWAP Extension × Candle Size",
                interactions["vwap_ext_x_size"],
                "vwap_ext_bucket", "candle_bucket",
            )
        if "ma100_location_x_trend" in interactions:
            html += _render_interaction_table(
                "MA100 Location × Trend Alignment",
                interactions["ma100_location_x_trend"],
                "ma100_location_bucket", "trend_alignment_label",
            )

    # ------------------------------------------------------------------
    # 3. Key level context
    # ------------------------------------------------------------------
    level_ctx = context.get("key_level_context") or {}
    if level_ctx.get("enabled"):
        html += section_title("Key Level Interaction Effects")
        html += (
            '<p style="font-size:12px;color:#555;margin-bottom:8px">'
            "Classifies each signal candle's proximity to prior-day H/L/C, session H/L, "
            "overnight H/L, VWAP, and MAs. "
            "<b>at_level</b> (&lt;0.10 ATR) = candle hits the level; "
            "<b>approaching</b> (0.10–0.30 ATR) = within striking range; "
            "<b>nearby</b> (0.30–0.60 ATR) = in the vicinity; "
            "<b>departing</b> (&gt;0.60 ATR) = moving away from levels.</p>"
        )
        for col, label in [
            ("nearest_level_type",      "Nearest Level Type"),
            ("level_interaction_label", "Level Interaction"),
        ]:
            rows = level_ctx.get(f"by_{col}") or []
            if rows:
                html += _render_context_table(label, rows, col)

        stab = level_ctx.get("temporal_stability")
        if stab is not None:
            stab_color = "#1b5e20" if stab >= 0.65 else ("#e65100" if stab < 0.45 else "#555")
            html += (
                f'<p style="font-size:11px;margin-top:4px">'
                f'<b>Level interaction temporal stability:</b> '
                f'<span style="color:{stab_color};font-weight:bold">{stab:.2f}</span> '
                f'(0 = unstable, 1 = perfectly stable across time splits)</p>'
            )

        if "session_x_level_interaction" in interactions:
            html += _render_interaction_table(
                "Session × Level Interaction",
                interactions["session_x_level_interaction"],
                "session_bucket", "level_interaction_label",
            )
        if "prev_direction_x_level" in interactions:
            html += _render_interaction_table(
                "Prev Direction × Level Interaction",
                interactions["prev_direction_x_level"],
                "prev_direction_bucket", "level_interaction_label",
            )

    # ------------------------------------------------------------------
    # 4. Trend state context
    # ------------------------------------------------------------------
    trend_ctx = context.get("trend_state_context") or {}
    if trend_ctx.get("enabled"):
        html += section_title("Trend Alignment Effects")
        html += (
            '<p style="font-size:12px;color:#555;margin-bottom:8px">'
            "Classifies the market regime at signal time based on MA slopes and price stacking. "
            "<b>strong_bull</b> = price above both MAs, both rising steeply; "
            "<b>bull</b> = broadly bullish structure; "
            "<b>neutral</b> = mixed or flat; "
            "<b>bear/strong_bear</b> = bearish structure. "
            "Slope labels reflect per-bar slope normalized by ATR.</p>"
        )
        for col, label in [
            ("trend_alignment_label", "Trend Alignment"),
            ("ma100_slope_label",     "MA100 Slope"),
            ("ma200_slope_label",     "MA200 Slope"),
        ]:
            rows = trend_ctx.get(f"by_{col}") or []
            if rows:
                html += _render_context_table(label, rows, col)

    # Per-module failure hints when modules have no data
    sections_rendered = any([
        dir_ctx.get("enabled"), ma_ctx.get("enabled"),
        level_ctx.get("enabled"), trend_ctx.get("enabled"),
    ])
    if not sections_rendered:
        # Show per-module status from diagnostics if available
        diag = context.get("diagnostics") or {}
        if diag:
            html += section_title("Module Status")
            module_map = {
                "directional_context": "Directional Context",
                "ma_vwap_context":     "MA / VWAP Location",
                "key_level_context":   "Key Level Interaction",
                "trend_state_context": "Trend State",
            }
            for mod_key, mod_label in module_map.items():
                d = diag.get(mod_key) or {}
                cfg_enabled = d.get("enabled_in_config", False)
                reasons = d.get("failure_reasons") or []
                if not cfg_enabled:
                    reason_str = "disabled in config — add <code>{}: enabled: true</code> to your YAML".format(mod_key)
                elif reasons:
                    reason_str = "; ".join(reasons)
                else:
                    reason_str = "enabled but no classified events — check data availability"
                color = "#888" if not cfg_enabled else "#c62828"
                html += (
                    f'<p style="font-size:12px;margin:4px 0">'
                    f'<b>{mod_label}:</b> '
                    f'<span style="color:{color}">{reason_str}</span></p>'
                )
        else:
            html += info_box(
                "No signal candle context modules produced results. "
                "Ensure directional_context, ma_vwap_context, key_level_context, and "
                "trend_state_context are set to <code>enabled: true</code> in your "
                "<code>large_candle_excursion:</code> YAML block. "
                "See the Context Coverage Diagnostics section for details."
            )

    html += "</div>"
    return html
