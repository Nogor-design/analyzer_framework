from __future__ import annotations

from ta_foundation.reports.html.sections.large_candle_excursion_downstream_common import (
    cell,
    fmt,
    hdr,
    info_box,
    section_title,
)


def render_large_candle_excursion_time_segments(ctx: dict) -> str:
    lce = None
    for pkg in (ctx.get("packages") or {}).values():
        d = getattr(pkg, "metadata", {}).get("derived", {})
        if "large_candle_excursion" in d:
            lce = d["large_candle_excursion"]
            break

    if not lce:
        return info_box("Time segment analysis unavailable: large_candle_excursion analytics not found.")

    tsa = lce.get("time_segment_analysis") or {}

    if not tsa.get("enabled"):
        reason = tsa.get("reason", "not enabled")
        return info_box(
            f"Time segment analysis not computed: {reason}. "
            "Enable via <code>time_segment_analysis.enabled: true</code> in your YAML config."
        )

    segments = tsa.get("segments") or []
    if not segments:
        return info_box("No segments found in time_segment_analysis results.", color="#f8f9fa", border="#dee2e6")

    baseline = tsa.get("population_baseline") or {}
    baseline_cont = baseline.get("cont_win_rate")
    baseline_rev = baseline.get("rev_win_rate")
    baseline_n = baseline.get("n_events", 0)
    tz = tsa.get("timezone", "America/New_York")
    tgt_pct = tsa.get("primary_target_pct")

    html = '<div style="font-family:Arial,sans-serif">'
    html += section_title(f"Time Segment Analysis ({tz})")

    if tgt_pct is not None:
        html += (
            f'<p style="color:#555;font-size:12px;margin-bottom:12px">'
            f"Primary target: {tgt_pct:.0f}% of signal candle. "
            f"Baseline N={baseline_n}, Cont WR={fmt(baseline_cont, 1, '%')}, Rev WR={fmt(baseline_rev, 1, '%')}. "
            f"Lift columns show percentage-point difference vs. overall population."
            f"</p>"
        )

    # ---- Summary table ----
    has_trade = any(s.get("cont_win_rate") is not None for s in segments if not s.get("skipped"))
    has_lift = has_trade and (baseline_cont is not None or baseline_rev is not None)

    html += '<table style="border-collapse:collapse;width:100%;font-size:12px">'
    html += "<thead><tr>"
    summary_cols = ["Segment", "Window", "N Events", "Mean Fav T", "Med Fav T", "Mean Adv T", "Med Adv T"]
    if has_trade:
        summary_cols += ["Cont WR%", "Rev WR%", "Better"]
    if has_lift:
        summary_cols += ["Cont Lift pp", "Rev Lift pp"]
    for h in summary_cols:
        html += hdr(h)
    html += "</tr></thead><tbody>"

    for i, seg in enumerate(segments):
        bg = "#f9f9f9" if i % 2 == 0 else "#fff"
        label = seg.get("label", "?")
        window = f"{seg.get('start', '')}–{seg.get('end', '')}"
        n = seg.get("n_events", 0)
        skipped = seg.get("skipped", False)

        if skipped:
            colspan = len(summary_cols) - 3
            html += "<tr>"
            html += cell(f"<b>{label}</b>", bg="#fff8dc")
            html += cell(window, bg="#fff8dc")
            html += cell(str(n), bg="#fff8dc")
            html += f'<td colspan="{colspan}" style="background:#fff8dc;text-align:center;padding:5px 8px;border:1px solid #ddd;font-size:11px;color:#888">{seg.get("skip_reason", "skipped")}</td>'
            html += "</tr>"
            continue

        cont_wr = seg.get("cont_win_rate")
        rev_wr = seg.get("rev_win_rate")
        better = seg.get("better_mode")
        cont_lift = seg.get("cont_lift_pp")
        rev_lift = seg.get("rev_lift_pp")

        html += "<tr>"
        html += cell(f"<b>{label}</b>", bg=bg)
        html += cell(window, bg=bg)
        html += cell(str(n), bg=bg)
        html += cell(fmt(seg.get("mean_fav_ticks")), bg=bg)
        html += cell(fmt(seg.get("median_fav_ticks")), bg=bg)
        html += cell(fmt(seg.get("mean_adv_ticks")), bg=bg)
        html += cell(fmt(seg.get("median_adv_ticks")), bg=bg)

        if has_trade:
            cont_bg = bg
            rev_bg = bg
            if cont_wr is not None and rev_wr is not None:
                if float(cont_wr) > float(rev_wr) + 2:
                    cont_bg = "#d4edda"
                elif float(rev_wr) > float(cont_wr) + 2:
                    rev_bg = "#d4edda"
            html += cell(fmt(cont_wr, 1, "%") if cont_wr is not None else "—", bg=cont_bg)
            html += cell(fmt(rev_wr, 1, "%") if rev_wr is not None else "—", bg=rev_bg)
            html += cell(better or "—", bg=bg)

        if has_lift:
            def _lift_cell(lift, bg_):
                if lift is None:
                    return cell("—", bg=bg_)
                color = "#d4edda" if lift > 2 else ("#f8d7da" if lift < -2 else bg_)
                sign = "+" if lift > 0 else ""
                return cell(f"{sign}{lift:.1f}", bg=color)
            html += _lift_cell(cont_lift, bg)
            html += _lift_cell(rev_lift, bg)

        html += "</tr>"

    html += "</tbody></table>"

    # ---- Per-segment detail tables ----
    for seg in segments:
        if seg.get("skipped"):
            continue

        label = seg.get("label", "?")
        window = f"{seg.get('start', '')}–{seg.get('end', '')}"

        # By direction
        by_dir = seg.get("by_direction") or []
        if by_dir:
            html += section_title(f"{label} ({window}) — By Direction")
            html += '<table style="border-collapse:collapse;width:100%;font-size:12px">'
            html += "<thead><tr>"
            dir_cols = ["Direction", "N", "Mean Fav T", "Med Fav T", "Mean Adv T", "Med Adv T"]
            if any(r.get("cont_win_rate") is not None for r in by_dir):
                dir_cols += ["Cont WR%", "Rev WR%"]
            for h in dir_cols:
                html += hdr(h)
            html += "</tr></thead><tbody>"
            for j, r in enumerate(by_dir):
                bg = "#f9f9f9" if j % 2 == 0 else "#fff"
                html += "<tr>"
                html += cell(r.get("direction", "?"), bg=bg)
                html += cell(str(r.get("n_observations", 0)), bg=bg)
                html += cell(fmt(r.get("mean_fav_ticks")), bg=bg)
                html += cell(fmt(r.get("median_fav_ticks")), bg=bg)
                html += cell(fmt(r.get("mean_adv_ticks")), bg=bg)
                html += cell(fmt(r.get("median_adv_ticks")), bg=bg)
                if any(r2.get("cont_win_rate") is not None for r2 in by_dir):
                    html += cell(fmt(r.get("cont_win_rate"), 1, "%") if r.get("cont_win_rate") is not None else "—", bg=bg)
                    html += cell(fmt(r.get("rev_win_rate"), 1, "%") if r.get("rev_win_rate") is not None else "—", bg=bg)
                html += "</tr>"
            html += "</tbody></table>"

        # By candle bucket
        by_cb = seg.get("by_candle_bucket") or []
        if by_cb:
            html += section_title(f"{label} ({window}) — By Candle Size")
            html += '<table style="border-collapse:collapse;width:100%;font-size:12px">'
            html += "<thead><tr>"
            cb_cols = ["Candle Bucket", "N", "Mean Fav T", "Med Fav T", "Mean Adv T", "Med Adv T"]
            if any(r.get("cont_win_rate") is not None for r in by_cb):
                cb_cols += ["Cont WR%", "Rev WR%", "Cont Lift pp", "Rev Lift pp"]
            for h in cb_cols:
                html += hdr(h)
            html += "</tr></thead><tbody>"
            for j, r in enumerate(by_cb):
                bg = "#f9f9f9" if j % 2 == 0 else "#fff"
                html += "<tr>"
                html += cell(r.get("candle_bucket", "?"), bg=bg)
                html += cell(str(r.get("n_observations", 0)), bg=bg)
                html += cell(fmt(r.get("mean_fav_ticks")), bg=bg)
                html += cell(fmt(r.get("median_fav_ticks")), bg=bg)
                html += cell(fmt(r.get("mean_adv_ticks")), bg=bg)
                html += cell(fmt(r.get("median_adv_ticks")), bg=bg)
                if any(r2.get("cont_win_rate") is not None for r2 in by_cb):
                    html += cell(fmt(r.get("cont_win_rate"), 1, "%") if r.get("cont_win_rate") is not None else "—", bg=bg)
                    html += cell(fmt(r.get("rev_win_rate"), 1, "%") if r.get("rev_win_rate") is not None else "—", bg=bg)
                    lift_c = r.get("cont_lift_pp")
                    lift_r = r.get("rev_lift_pp")
                    for lift in (lift_c, lift_r):
                        if lift is None:
                            html += cell("—", bg=bg)
                        else:
                            color = "#d4edda" if lift > 2 else ("#f8d7da" if lift < -2 else bg)
                            html += cell(f"{'+'if lift>0 else ''}{lift:.1f}", bg=color)
                html += "</tr>"
            html += "</tbody></table>"

        # By session bucket within segment
        by_sb = seg.get("by_session_bucket") or []
        if by_sb:
            html += section_title(f"{label} ({window}) — Sub-Session Breakdown")
            html += '<table style="border-collapse:collapse;width:100%;font-size:12px">'
            html += "<thead><tr>"
            sb_cols = ["Session", "N", "Mean Fav T", "Med Fav T", "Mean Adv T", "Med Adv T"]
            if any(r.get("cont_win_rate") is not None for r in by_sb):
                sb_cols += ["Cont WR%", "Rev WR%"]
            for h in sb_cols:
                html += hdr(h)
            html += "</tr></thead><tbody>"
            for j, r in enumerate(by_sb):
                bg = "#f9f9f9" if j % 2 == 0 else "#fff"
                html += "<tr>"
                html += cell(r.get("session_bucket", "?"), bg=bg)
                html += cell(str(r.get("n_observations", 0)), bg=bg)
                html += cell(fmt(r.get("mean_fav_ticks")), bg=bg)
                html += cell(fmt(r.get("median_fav_ticks")), bg=bg)
                html += cell(fmt(r.get("mean_adv_ticks")), bg=bg)
                html += cell(fmt(r.get("median_adv_ticks")), bg=bg)
                if any(r2.get("cont_win_rate") is not None for r2 in by_sb):
                    html += cell(fmt(r.get("cont_win_rate"), 1, "%") if r.get("cont_win_rate") is not None else "—", bg=bg)
                    html += cell(fmt(r.get("rev_win_rate"), 1, "%") if r.get("rev_win_rate") is not None else "—", bg=bg)
                html += "</tr>"
            html += "</tbody></table>"

    html += "</div>"
    return html
