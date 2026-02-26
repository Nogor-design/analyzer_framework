from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

import html
import pandas as pd


def _get_pkg(packages: Dict[str, Any], run_id: Optional[str]) -> Tuple[Optional[str], Optional[Any]]:
    if not packages:
        return None, None
    if run_id and run_id in packages:
        return run_id, packages[run_id]
    first = next(iter(packages.items()))
    return first[0], first[1]


def _pe_assets(pkg: Any) -> Dict[str, Any]:
    assets = getattr(pkg, "assets", None) or {}
    pe = assets.get("pattern_engine", {}) or {}
    return pe if isinstance(pe, dict) else {}


def _pe_meta(pkg: Any) -> Optional[Dict[str, Any]]:
    try:
        md = getattr(pkg, "metadata", None) or {}
        derived = md.get("derived", {}) or {}
        pe = derived.get("pattern_engine")
        return pe if isinstance(pe, dict) else None
    except Exception:
        return None


def _df(pe_assets: Dict[str, Any], key: str) -> pd.DataFrame:
    v = pe_assets.get(key)
    return v if isinstance(v, pd.DataFrame) else pd.DataFrame()


def _html_table(df: pd.DataFrame, *, max_rows: int = 50, title: Optional[str] = None) -> str:
    if df is None or df.empty:
        return "<div class='pe-muted'>No rows.</div>"
    d2 = df.head(max_rows).copy()
    for c in d2.columns:
        if d2[c].dtype == "object":
            d2[c] = d2[c].astype(str).map(lambda s: s[:220] + ("…" if len(s) > 220 else ""))
    tbl = d2.to_html(index=False, escape=True, classes="pe-table")
    return f"<h3>{html.escape(title)}</h3>\n{tbl}" if title else tbl


def _html_kv(d: Dict[str, Any], *, title: str, max_items: int = 80) -> str:
    if not isinstance(d, dict) or not d:
        return "<div class='pe-muted'>No diagnostics.</div>"
    items = list(d.items())[:max_items]
    rows = []
    for k, v in items:
        rows.append(
            f"<tr><td><code>{html.escape(str(k))}</code></td>"
            f"<td style='white-space:pre-wrap;word-break:break-word'>{html.escape(str(v))}</td></tr>"
        )
    return (
        f"<h3>{html.escape(title)}</h3>"
        "<table class='pe-table'><thead><tr><th>Key</th><th>Value</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def _pick(d: Dict[str, Any], keys: list[str]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if not isinstance(d, dict):
        return out
    for k in keys:
        if k in d:
            out[k] = d[k]
    return out


def render_pattern_market_discovery(ctx: Dict[str, Any]) -> str:
    packages = ctx.get("packages") or {}
    sec = ctx.get("options") or {}

    run_id = (sec.get("run_id") or "").strip() or None
    horizon = sec.get("horizon", None)
    horizon = int(horizon) if horizon is not None and str(horizon).strip() != "" else None
    top_n = int(sec.get("top_n", 25))
    sort_by = str(sec.get("sort_by", "ir_atr_adj_ret"))
    focus_pid = (sec.get("pattern_id") or "").strip() or None

    rid, pkg = _get_pkg(packages, run_id)
    pe_a = _pe_assets(pkg) if pkg else {}
    pe_m = _pe_meta(pkg) if pkg else None

    stats = _df(pe_a, "discovery_stats")
    stab = _df(pe_a, "discovery_stability")
    reg = _df(pe_a, "discovery_regime_stats")
    events = _df(pe_a, "discovery_events")
    print(sec)
    print(pkg)
    css = """
    <style>
      .pe-block{padding:16px;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif;line-height:1.35;color:#111827}
      .pe-card{border:1px solid #d1d5db;border-radius:12px;padding:14px;background:#fff;box-shadow:0 1px 2px rgba(0,0,0,0.06);margin-bottom:14px}
      .pe-muted{color:#4b5563;font-size:12px}
      table.pe-table{width:100%;border-collapse:collapse;font-size:12px;background:#fff}
      .pe-table th, .pe-table td{border:1px solid #d1d5db;padding:8px;vertical-align:top;word-break:break-word}
      .pe-table th{background:#f3f4f6;font-weight:700}
      .pe-table tr:nth-child(even) td{background:#fafafa}
      code{font-family:ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,"Liberation Mono","Courier New",monospace;font-size:11px}
      .pe-callout{border:1px solid #e5e7eb;background:#f9fafb;border-radius:10px;padding:10px;margin-top:10px}
    </style>
    """

    # Metadata-derived diagnostics
    diagnostics = (pe_m.get("diagnostics", {}) or {}) if isinstance(pe_m, dict) else {}
    counts = (diagnostics.get("counts", {}) or {}) if isinstance(diagnostics, dict) else {}
    options_snapshot = (pe_m.get("options_snapshot", {}) or {}) if isinstance(pe_m, dict) else {}
    scopes = options_snapshot.get("scopes", None) if isinstance(options_snapshot, dict) else None

    # Better empty state messaging
    has_stats_key = "discovery_stats" in pe_a
    assets_keys = sorted(list(pe_a.keys())) if isinstance(pe_a, dict) else []

    if stats.empty:
        # Determine why it's empty
        n_patterns = int(counts.get("n_patterns", 0) or 0) if isinstance(counts, dict) else 0
        n_signals = int(counts.get("n_signals", 0) or 0) if isinstance(counts, dict) else 0
        n_disc = int(counts.get("n_discovery_stats", 0) or 0) if isinstance(counts, dict) else 0

        if not has_stats_key:
            headline = "Missing discovery_stats artifact in memory."
        else:
            headline = "discovery_stats is present but has 0 rows."

        # Show most relevant knobs without dumping the entire config
        md = (options_snapshot.get("market_discovery", {}) or {}) if isinstance(options_snapshot, dict) else {}
        engine = (pe_m.get("engine", {}) or {}) if isinstance(pe_m, dict) else {}

        key_knobs = {
            "scopes": scopes,
            "instrument": md.get("instrument", engine.get("instrument")),
            "contract": md.get("contract", engine.get("contract")),
            "timeframe": md.get("timeframe", engine.get("bar_tf")),
            "start": md.get("start"),
            "end": md.get("end"),
            "bars_source": md.get("bars_source"),
        }

        return f"""{css}<div class="pe-block">
          <h2>Pattern Engine — Market Discovery</h2>

          <div class="pe-card">
            <div><b>run_id:</b> {html.escape(str(rid))}</div>

            <div class="pe-callout">
              <b>{html.escape(headline)}</b>
              <div class="pe-muted" style="margin-top:6px">
                counts: n_patterns={n_patterns}, n_signals={n_signals}, n_discovery_stats={n_disc}
              </div>
              <div class="pe-muted" style="margin-top:6px">
                This is not a wiring problem anymore — the engine ran, but produced zero signals/patterns
                for the configured discovery window and pattern set.
              </div>
            </div>

            <h3>Available in-memory keys</h3>
            <div class="pe-muted">{html.escape(", ".join(assets_keys) if assets_keys else "(none)")}</div>

            {_html_kv(key_knobs, title="Key config knobs (snapshot)")}

            {_html_kv(diagnostics if isinstance(diagnostics, dict) else {}, title="Diagnostics dict")}
          </div>
        </div>"""

    # Join stability for ranking
    out = stats.copy()
    if not stab.empty:
        join_cols = [c for c in ["pattern_id", "horizon"] if c in out.columns and c in stab.columns]
        if join_cols:
            out = out.merge(stab, on=join_cols, how="left")

    if horizon is not None and "horizon" in out.columns:
        out = out[out["horizon"] == horizon]

    # Rank / sort
    if sort_by in out.columns:
        out = out.sort_values(sort_by, ascending=False)
    else:
        for c in ["ir_atr_adj_ret", "ir_ticks", "avg_ticks"]:
            if c in out.columns:
                out = out.sort_values(c, ascending=False)
                break

    out = out.head(top_n).copy()

    # Focus detail panels
    focus = focus_pid

    body = [
        "<div class='pe-card'>"
        f"<div><b>run_id:</b> {html.escape(str(rid))}</div>"
        f"<div class='pe-muted'>top_n={top_n} sort_by={html.escape(sort_by)} horizon={html.escape(str(horizon))}</div>"
        "</div>",
        "<div class='pe-card'>" + _html_table(out, max_rows=top_n, title="Discovery Stats (ranked)") + "</div>",
    ]

    if not reg.empty:
        body.append("<div class='pe-card'>" + _html_table(reg, max_rows=50, title="Regime Stats") + "</div>")

    if not events.empty:
        ev = events
        if focus and "pattern_id" in ev.columns:
            ev = ev[ev["pattern_id"] == focus]
        body.append("<div class='pe-card'>" + _html_table(ev, max_rows=50, title="Discovery Events (sample)") + "</div>")

    # Always include diagnostics footer
    body.append("<div class='pe-card'>" + _html_kv(diagnostics if isinstance(diagnostics, dict) else {}, title="Diagnostics dict") + "</div>")

    return f"{css}<div class='pe-block'><h2>Pattern Engine — Market Discovery</h2>{''.join(body)}</div>"