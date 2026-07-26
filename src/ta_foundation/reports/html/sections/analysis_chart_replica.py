from __future__ import annotations

"""Reproduce NinjaTrader's Analysis-tab chart from ``Analysis.csv``.

The chart is the equity curve + per-day P/L bars that NT renders in
the Analysis tab. The screenshot version (``*_Analysis.png``) used by
older reports required pyautogui-clicking through NT; this section
renders the exact same picture directly from the daily aggregates NT
exports, removing the screenshot dependency entirely.

The data lives in ``Analysis.csv`` for each candidate run. Columns
used (NinjaTrader format, dollar-formatted with parentheses for
negatives):

- ``Period``           — date string (e.g. ``4/15/2026``)
- ``Net profit``       — that day's net dollar P/L
- ``Cum. net profit``  — running equity
- ``Cum. max. drawdown`` (optional shading)

Section contract: pure renderer, returns HTML; reads the candidate
CSV via ``ctx['options']['analysis_csv_path']`` or via a derived path
on the package if pre-injected by the pipeline. Returns an empty
placeholder if no data found, never raises.
"""

import base64
import csv
import io
import math
from pathlib import Path
from typing import Any

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from datetime import datetime


def render_analysis_chart_replica(ctx: dict[str, Any]) -> str:
    options = ctx.get("options") or {}
    packages = ctx.get("packages") or {}

    csv_path = _resolve_csv_path(options, packages)
    if csv_path is None or not csv_path.exists():
        return _placeholder("Analysis.csv not found for this candidate.")

    rows = _read_analysis_csv(csv_path)
    if not rows:
        return _placeholder("Analysis.csv is empty or unparseable.")

    fig = _build_figure(rows)
    if fig is None:
        return _placeholder("Could not render the Analysis chart from the CSV.")

    data_uri = _fig_to_data_uri(fig)
    plt.close(fig)
    return _wrap(data_uri, source=str(csv_path))


# ---------------------------------------------------------------------------
# Resolution / parsing
# ---------------------------------------------------------------------------

def _resolve_csv_path(options: dict[str, Any], packages: dict[str, Any]) -> Path | None:
    # Preferred path: caller sets options['analysis_csv_path'] directly.
    raw = options.get("analysis_csv_path")
    if raw:
        return Path(raw)

    # Fallback: pull a path from the only package (single-run report case).
    if not packages:
        return None
    if len(packages) != 1:
        return None
    pkg = next(iter(packages.values()))
    md = getattr(pkg, "metadata", None) or {}
    derived = md.get("derived") if isinstance(md, dict) else None
    if isinstance(derived, dict):
        ar = derived.get("analysis_csv_path")
        if ar:
            return Path(ar)
    return None


def _read_analysis_csv(path: Path) -> list[dict[str, Any]]:
    try:
        with path.open(encoding="utf-8-sig", newline="") as h:
            reader = csv.DictReader(h)
            rows: list[dict[str, Any]] = []
            for raw in reader:
                period = (raw.get("Period") or "").strip()
                if not period:
                    continue
                date = _parse_period(period)
                if date is None:
                    continue
                rows.append({
                    "date": date,
                    "net": _money(raw.get("Net profit")),
                    "cum": _money(raw.get("Cum. net profit")),
                    "dd": _money(raw.get("Cum. max. drawdown")),
                })
    except OSError:
        return []
    rows.sort(key=lambda r: r["date"])
    return rows


def _parse_period(text: str) -> datetime | None:
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _money(value: Any) -> float | None:
    if value is None:
        return None
    s = str(value).strip()
    if not s or s.lower() in {"nan", "none"}:
        return None
    negative = s.startswith("(") and s.endswith(")")
    s = s.strip("()$ ").replace(",", "")
    if not s:
        return None
    try:
        f = float(s)
    except ValueError:
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return -f if negative else f


# ---------------------------------------------------------------------------
# Figure build (two-panel: equity curve, per-day bars)
# ---------------------------------------------------------------------------

def _build_figure(rows: list[dict[str, Any]]):
    dates = [r["date"] for r in rows]
    nets = [r["net"] if r["net"] is not None else 0.0 for r in rows]
    cums = [r["cum"] if r["cum"] is not None else 0.0 for r in rows]
    dds = [r["dd"] if r["dd"] is not None else 0.0 for r in rows]

    if not dates:
        return None

    fig, (ax_eq, ax_bar) = plt.subplots(
        2, 1, figsize=(11, 6.5), sharex=True,
        gridspec_kw={"height_ratios": [3, 2]},
        facecolor="#111827",
    )

    # ---- Top panel: cumulative equity curve + drawdown shading
    ax_eq.set_facecolor("#1f2937")
    ax_eq.plot(dates, cums, color="#fbbf24", linewidth=2.0, marker="o",
               markersize=3, markerfacecolor="#fbbf24", label="Cum. net profit")
    # Drawdown shading from peak to peak-dd, plotted under the equity curve.
    peak: list[float] = []
    running = float("-inf")
    for c in cums:
        running = max(running, c)
        peak.append(running)
    ax_eq.fill_between(dates, peak, cums, where=[p > c for p, c in zip(peak, cums)],
                       color="#7f1d1d", alpha=0.35, label="Drawdown")
    ax_eq.axhline(0, color="#374151", linewidth=0.8)
    ax_eq.set_ylabel("Cum. Net Profit ($)", color="#f3f4f6", fontsize=10)
    ax_eq.tick_params(colors="#9ca3af", labelsize=9)
    for spine in ax_eq.spines.values():
        spine.set_color("#374151")
    ax_eq.grid(True, color="#374151", alpha=0.4, linewidth=0.5)
    ax_eq.legend(facecolor="#1f2937", edgecolor="#374151",
                 labelcolor="#f3f4f6", fontsize=9, loc="upper left")

    # Annotate final value to mirror NT's bottom-right label.
    if cums:
        last = cums[-1]
        ax_eq.annotate(f"${last:,.0f}",
                       xy=(dates[-1], last), xytext=(8, 0),
                       textcoords="offset points",
                       color="#fbbf24", fontsize=10, fontweight="bold",
                       va="center")

    # ---- Bottom panel: per-day P/L bars
    ax_bar.set_facecolor("#1f2937")
    colors = ["#10b981" if n > 0 else "#ef4444" if n < 0 else "#6b7280" for n in nets]
    ax_bar.bar(dates, nets, width=0.7, color=colors, edgecolor="none")
    ax_bar.axhline(0, color="#374151", linewidth=0.8)
    ax_bar.set_ylabel("Daily P/L ($)", color="#f3f4f6", fontsize=10)
    ax_bar.tick_params(colors="#9ca3af", labelsize=9)
    for spine in ax_bar.spines.values():
        spine.set_color("#374151")
    ax_bar.grid(True, axis="y", color="#374151", alpha=0.4, linewidth=0.5)

    # X-axis formatting
    ax_bar.xaxis.set_major_locator(mdates.AutoDateLocator(maxticks=10))
    ax_bar.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d"))
    for label in ax_bar.get_xticklabels():
        label.set_rotation(30)
        label.set_ha("right")

    fig.suptitle("Analysis (replicated from CSV)", color="#f3f4f6",
                 fontsize=12, fontweight="bold", y=0.98)
    fig.tight_layout(rect=(0.0, 0.0, 1.0, 0.96))
    return fig


# ---------------------------------------------------------------------------
# HTML wrappers
# ---------------------------------------------------------------------------

def _fig_to_data_uri(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=140, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    buf.seek(0)
    b64 = base64.b64encode(buf.read()).decode("ascii")
    return f"data:image/png;base64,{b64}"


def _wrap(data_uri: str, *, source: str) -> str:
    return (
        '<section style="margin:14px 0;">'
        f'<img src="{data_uri}" alt="Analysis chart replica" '
        'style="max-width:100%;height:auto;border-radius:8px;'
        'border:1px solid #374151;" />'
        f'<div style="margin-top:6px;font-size:11px;color:#6b7280;font-family:ui-monospace,Menlo,Consolas,monospace;">'
        f'rendered from <code>{_html_escape(source)}</code></div>'
        '</section>'
    )


def _placeholder(reason: str) -> str:
    return (
        '<section style="margin:14px 0;padding:12px;border:1px dashed #374151;'
        'border-radius:8px;color:#9ca3af;font-size:12px;">'
        f'<strong>Analysis chart unavailable:</strong> {_html_escape(reason)}'
        '</section>'
    )


def _html_escape(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
              .replace('"', "&quot;").replace("'", "&#39;"))
