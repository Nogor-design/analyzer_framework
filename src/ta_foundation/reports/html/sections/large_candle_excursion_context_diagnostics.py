from __future__ import annotations

"""
Signal Context Coverage Diagnostics Section
============================================
Renders a per-module diagnostic table showing:
  - whether each context module was enabled
  - how many events were classified
  - coverage percentage
  - distinct buckets produced
  - failure reasons where applicable

This section makes it immediately obvious why a context module has empty output.
"""

from typing import Any, Dict, List

from ta_foundation.reports.html.sections.large_candle_excursion_downstream_common import (
    cell, hdr, info_box, section_title,
)


def _get_context_analysis(ctx: dict) -> dict:
    for pkg in (ctx.get("packages") or {}).values():
        d = getattr(pkg, "metadata", {}).get("derived", {})
        if "large_candle_excursion" in d:
            lce = d["large_candle_excursion"]
            return lce.get("context_analysis") or {}
    lce = ctx.get("large_candle_excursion") or {}
    return lce.get("context_analysis") or {}


def _status_badge(contributed: bool, enabled: bool) -> str:
    if contributed:
        return '<span style="background:#1b5e20;color:#fff;border-radius:3px;padding:2px 6px;font-size:10px">OK</span>'
    if enabled:
        return '<span style="background:#e65100;color:#fff;border-radius:3px;padding:2px 6px;font-size:10px">NO DATA</span>'
    return '<span style="background:#888;color:#fff;border-radius:3px;padding:2px 6px;font-size:10px">DISABLED</span>'


def render_large_candle_excursion_context_diagnostics(ctx: dict) -> str:
    context = _get_context_analysis(ctx)
    if not context:
        return info_box("Context diagnostics: no LCE context analysis found. Run the large_candle_excursion report first.")

    diagnostics = context.get("diagnostics") or {}
    if not diagnostics:
        return info_box(
            "Context diagnostics not available. "
            "Re-run the large_candle_excursion analysis to generate diagnostics."
        )

    html = '<div style="font-family:Arial,sans-serif">'
    html += section_title("Signal Context Coverage Diagnostics")

    # Summary bar
    summary = diagnostics.get("_summary") or {}
    n_total  = summary.get("total_events_in_combined_fwd", 0)
    n_dedup  = summary.get("total_deduplicated_events", 0)
    n_contrib = summary.get("modules_contributing", 0)
    n_enabled = summary.get("modules_enabled", 0)

    html += (
        f'<div style="background:#e3f2fd;border:1px solid #90caf9;border-radius:5px;'
        f'padding:8px 14px;margin-bottom:12px;font-size:12px">'
        f'<b>Analysis coverage</b>: '
        f'{n_total:,} total events in pipeline &rarr; {n_dedup:,} deduplicated &nbsp;|&nbsp; '
        f'{n_enabled} modules enabled &nbsp;|&nbsp; '
        f'<b style="color:{"#1b5e20" if n_contrib > 0 else "#c62828"}">'
        f'{n_contrib} modules contributing results</b>'
        f'</div>'
    )

    # Per-module table
    module_order = [
        "directional_context", "ma_vwap_context",
        "key_level_context", "trend_state_context", "volume_context",
    ]
    module_labels = {
        "directional_context":  "Directional Context",
        "ma_vwap_context":      "MA / VWAP Location",
        "key_level_context":    "Key Level Interaction",
        "trend_state_context":  "Trend State",
        "volume_context":       "Volume Context",
    }

    headers = ["Module", "Status", "Config Enabled", "Events Examined", "Events Classified",
               "Coverage %", "Distinct Buckets", "Failure Reasons"]
    hdr_row = "".join(hdr(h) for h in headers)
    body_rows = []

    for mod_key in module_order:
        d = diagnostics.get(mod_key)
        if not d:
            continue
        contributed  = bool(d.get("contributed_results", False))
        cfg_enabled  = bool(d.get("enabled_in_config", False))
        n_examined   = d.get("n_events_examined", 0)
        n_classified = d.get("n_events_classified", 0)
        coverage     = d.get("coverage_pct", 0.0)
        buckets      = d.get("distinct_buckets", [])
        reasons      = d.get("failure_reasons") or []

        reason_html = ""
        if reasons:
            reason_html = "<br>".join(
                f'<span style="color:#c62828;font-size:10px">&bull; {r}</span>'
                for r in reasons
            )
        elif contributed:
            reason_html = '<span style="color:#1b5e20;font-size:10px">&mdash; running normally</span>'

        bucket_html = ""
        if buckets:
            bucket_html = ", ".join(f"<code>{b}</code>" for b in buckets[:8])
            if len(buckets) > 8:
                bucket_html += f" +{len(buckets)-8} more"

        row_bg = "#f1f8e9" if contributed else ("#fff3e0" if cfg_enabled else "#f5f5f5")
        body_rows.append(
            f'<tr style="background:{row_bg}">'
            + cell(f"<b>{module_labels.get(mod_key, mod_key)}</b>")
            + cell(_status_badge(contributed, cfg_enabled))
            + cell("Yes" if cfg_enabled else "No")
            + cell(f"{n_examined:,}")
            + cell(f"{n_classified:,}")
            + cell(
                f'<span style="color:{"#1b5e20" if coverage >= 70 else ("#e65100" if coverage < 30 else "#555")}">'
                f"{coverage:.1f}%</span>"
            )
            + cell(bucket_html if bucket_html else "—")
            + cell(reason_html if reason_html else "—")
            + "</tr>"
        )

    if body_rows:
        html += (
            '<div style="overflow-x:auto">'
            '<table style="border-collapse:collapse;width:100%;font-size:12px">'
            f"<thead><tr>{hdr_row}</tr></thead>"
            f"<tbody>{''.join(body_rows)}</tbody>"
            "</table></div>"
        )
    else:
        html += info_box("No module diagnostics available.")

    # Enriched sample preview
    enriched_sample = _get_enriched_sample(ctx)
    if enriched_sample:
        html += section_title("Enriched Signal Event Sample (first 20 rows)")
        html += _render_enriched_sample_table(enriched_sample[:20])

    html += (
        '<p style="font-size:10px;color:#888;margin-top:8px">'
        "Green = module ran and produced classified events. "
        "Orange = enabled but no output (check failure reasons). "
        "Grey = disabled in config YAML.</p>"
    )
    html += "</div>"
    return html


def _get_enriched_sample(ctx: dict) -> List[Dict]:
    """Get the events_sample list from LCE results."""
    for pkg in (ctx.get("packages") or {}).values():
        d = getattr(pkg, "metadata", {}).get("derived", {})
        if "large_candle_excursion" in d:
            return d["large_candle_excursion"].get("events_sample") or []
    lce = ctx.get("large_candle_excursion") or {}
    return lce.get("events_sample") or []


def _render_enriched_sample_table(rows: List[Dict]) -> str:
    if not rows:
        return '<p style="color:#888;font-size:12px">No enriched sample available.</p>'

    # Detect which context columns are actually present
    context_cols = [
        ("dt",                    "Time"),
        ("direction",             "Dir"),
        ("tf_minutes",            "TF"),
        ("candle_bucket",         "Size Bucket"),
        ("session_bucket",        "Session"),
        ("prev_direction_bucket", "Prev Dir"),
        ("streak_bucket",         "Streak"),
        ("engulf_bucket",         "Engulf"),
        ("vwap_location_bucket",  "VWAP Loc"),
        ("ma100_location_bucket", "MA100 Loc"),
        ("nearest_level_type",    "Nearest Level"),
        ("level_interaction_label", "Level Interact"),
        ("trend_alignment_label", "Trend State"),
        ("fav_ticks",             "Fav T"),
        ("adv_ticks",             "Adv T"),
    ]

    # Only include columns that are actually present in at least one row
    present = []
    all_keys = set()
    for r in rows:
        all_keys.update(r.keys())
    for col_key, col_label in context_cols:
        if col_key in all_keys:
            present.append((col_key, col_label))

    if not present:
        return '<p style="color:#888;font-size:12px">No context columns found in sample.</p>'

    hdr_row = "".join(hdr(label) for _, label in present)
    body_rows = []
    for r in rows:
        cells = []
        for col_key, _ in present:
            v = r.get(col_key)
            if v is None:
                cells.append(cell("—"))
            elif col_key == "dt":
                cells.append(cell(str(v)[:19] if v else "—", bold=False))
            elif col_key == "direction":
                cells.append(cell("Bull" if v == 1 else "Bear"))
            elif col_key in ("fav_ticks", "adv_ticks"):
                bg = "#e8f5e9" if col_key == "fav_ticks" and isinstance(v, (int, float)) and v > 10 else ""
                cells.append(cell(f"{v:.1f}" if isinstance(v, float) else str(v), bg=bg))
            else:
                cells.append(cell(str(v)))
        body_rows.append("<tr>" + "".join(cells) + "</tr>")

    return (
        '<div style="overflow-x:auto">'
        '<table style="border-collapse:collapse;width:100%;font-size:11px">'
        f"<thead><tr>{hdr_row}</tr></thead>"
        f"<tbody>{''.join(body_rows)}</tbody>"
        "</table></div>"
        '<p style="font-size:10px;color:#888;margin-top:4px">'
        "Sample of enriched signal events with context columns. "
        "Confirms context enrichment is working end-to-end.</p>"
    )
