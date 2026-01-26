from __future__ import annotations

import html
from typing import Dict

import pandas as pd

from ta_foundation.core.model import AnalysisPackage


def _render_table(df: pd.DataFrame) -> str:
    # df expected cols: section, item, value
    out = []
    out.append('<table class="ta-table">')
    out.append("<thead><tr><th>Section</th><th>Item</th><th>Value</th></tr></thead>")
    out.append("<tbody>")

    for _, r in df.iterrows():
        sec = html.escape(str(r.get("section", "")))
        item = html.escape(str(r.get("item", "")))
        val = html.escape(str(r.get("value", "")))
        out.append(f"<tr><td>{sec}</td><td>{item}</td><td>{val}</td></tr>")

    out.append("</tbody></table>")
    return "\n".join(out)


def render_run_settings_table(ctx: dict) -> str:
    """
    Per-run settings display (collapsible) to verify *_Settings.csv ingest.

    ctx expects:
      - packages: dict[str, AnalysisPackage]
      - options (optional):
          - max_rows: int (default 500)
    """
    packages: Dict[str, AnalysisPackage] = ctx.get("packages", {}) or {}
    options = ctx.get("options", {}) or {}
    max_rows = int(options.get("max_rows", 500))

    show_run_image = bool(options.get("show_run_image", False))
    run_image_max_width = int(options.get("run_image_max_width", 420))

    run_ids = sorted(packages.keys())
    parts = []
    parts.append('<div class="ta-grid">')

    for run_id in run_ids:
        pkg = packages[run_id]
        df = getattr(pkg, "settings", None)

        parts.append('<div class="ta-card">')
        parts.append(f"<h3>{html.escape(run_id)}</h3>")

        # NEW: optional image
        if show_run_image:
            assets = getattr(pkg, "assets", None) or {}
            uri = assets.get("run_image_uri")

            if uri:
                parts.append(
                    f'<div style="margin: 8px 0 10px 0;">'
                    f'<img src="{uri}" alt="{html.escape(run_id)} image" '
                    f'style="max-width:{run_image_max_width}px; width:100%; height:auto; border-radius:8px;" />'
                    f"</div>"
                )
        if df is None or not isinstance(df, pd.DataFrame) or df.empty:
            parts.append('<div class="ta-muted">No settings data found for this run.</div>')
            parts.append("</div>")
            continue

        view = df[["section", "item", "value"]].copy()
        if len(view) > max_rows:
            view = view.iloc[:max_rows].copy()
            parts.append(
                f'<div class="ta-muted">Showing first {max_rows} rows (of {len(df)}).</div>'
            )

        parts.append("<details>")
        parts.append("<summary>View settings</summary>")
        parts.append(_render_table(view))
        parts.append("</details>")

        parts.append("</div>")

    parts.append("</div>")
    return "\n".join(parts)
