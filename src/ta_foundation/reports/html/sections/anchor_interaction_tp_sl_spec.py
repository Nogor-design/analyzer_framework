from __future__ import annotations

from typing import Any, Dict, List, Optional
import html

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _cfg_get(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _safe_str(x: Any) -> str:
    if x is None:
        return "—"
    s = str(x).strip()
    return html.escape(s) if s else "—"


def _safe_int(x: Any) -> str:
    try:
        if x is None or x == "":
            return "—"
        return str(int(x))
    except Exception:
        return html.escape(str(x))


def _fmt_f(x: Any, decimals: int = 3) -> str:
    try:
        if x is None or (isinstance(x, float) and np.isnan(x)):
            return "—"
        return f"{float(x):.{decimals}f}"
    except Exception:
        return "—"


def _fmt_pct(x: Any) -> str:
    try:
        if x is None or (isinstance(x, float) and np.isnan(x)):
            return "—"
        return f"{float(x) * 100:.1f}%"
    except Exception:
        return "—"


def _colour_cell(value: Any, fmt: str = "3f") -> str:
    """Return value string wrapped in a colour span (green>0, red<0)."""
    try:
        v = float(value)
        if np.isnan(v):
            return "—"
        s = f"{v:.{fmt}}"
        colour = "#4caf50" if v > 0 else ("#f44336" if v < 0 else "#aaa")
        return f"<span style='color:{colour};font-weight:bold'>{s}</span>"
    except Exception:
        return "—"


def _table(headers: List[str], rows: List[List[str]]) -> str:
    th = "".join(f"<th>{html.escape(h)}</th>" for h in headers)
    body = "".join(f"<tr>{''.join(f'<td>{c}</td>' for c in row)}</tr>" for row in rows)
    return (
        "<div style='overflow-x:auto'>"
        "<table class='table table-sm'>"
        f"<thead><tr>{th}</tr></thead>"
        f"<tbody>{body}</tbody>"
        "</table></div>"
    )


def _muted(msg: str) -> str:
    return f"<p style='color:#888;font-style:italic;padding:6px 0'>{html.escape(msg)}</p>"


def _resolve_anchor_cfg(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """
    Find the anchor_interaction config dict.  Priority:
      1. ctx['all_options'] — the full merged YAML (authoritative for top-level blocks)
      2. ctx['report_config'].raw — same data, alternative accessor
      3. Empty dict fallback
    """
    # 1. all_options is the canonical source for top-level YAML blocks
    all_opts = ctx.get("all_options") or {}
    cfg = all_opts.get("anchor_interaction")
    if cfg and isinstance(cfg, dict):
        return cfg

    # 2. fall back to report_config.raw
    report_config = ctx.get("report_config")
    if report_config is not None:
        raw = getattr(report_config, "raw", None)
        if isinstance(raw, dict):
            cfg = raw.get("anchor_interaction")
            if cfg and isinstance(cfg, dict):
                return cfg

    return {}


# ---------------------------------------------------------------------------
# Trade-time results panel (one run)
# ---------------------------------------------------------------------------

def _render_trade_time_panel(
    run_id: str,
    trade_time_candidates: pd.DataFrame,
    diagnostics: Dict[str, Any],
    max_rows: int = 40,
) -> str:
    out: List[str] = []
    n_trades = diagnostics.get("trade_time_per_trade", 0)
    n_cands = diagnostics.get("trade_time_candidates", 0)

    out.append(
        f"<h4 style='margin-top:20px'>"
        f"Trade-Entry Simulation — <em>{html.escape(str(run_id))}</em></h4>"
    )

    if trade_time_candidates is None or not isinstance(trade_time_candidates, pd.DataFrame) or trade_time_candidates.empty:
        out.append(_muted("No trade-time candidates produced for this run."))
        return "\n".join(out)

    out.append(
        f"<p style='font-size:0.85em;color:#aaa'>"
        f"Simulated from {n_trades} trade entries across "
        f"{n_cands} (group × tp × sl) combinations."
        f"</p>"
    )

    headers = [
        "TP (ATR)", "SL (ATR)", "n", "Decisive",
        "Win Rate", "Expectancy (ATR)", "Avg bars→TP", "Avg bars→SL", "% Neither",
    ]

    groups = trade_time_candidates["group"].unique().tolist()
    group_order = [g for g in ("all", "long", "short") if g in groups]
    group_order += [g for g in groups if g not in group_order]

    for group in group_order:
        gdf = (
            trade_time_candidates[trade_time_candidates["group"] == group]
            .sort_values("expectancy_atr", ascending=False)
            .head(max_rows)
            .reset_index(drop=True)
        )
        out.append(
            f"<h5 style='margin-top:12px;text-transform:capitalize'>"
            f"{html.escape(group)}</h5>"
        )
        rows: List[List[str]] = []
        for _, r in gdf.iterrows():
            rows.append([
                _fmt_f(r.get("tp_atr"), 2),
                _fmt_f(r.get("sl_atr"), 2),
                _safe_int(r.get("sample_n")),
                _safe_int(r.get("n_decisive")),
                _fmt_pct(r.get("win_rate")),
                _colour_cell(r.get("expectancy_atr")),
                _fmt_f(r.get("avg_bars_to_tp"), 1),
                _fmt_f(r.get("avg_bars_to_sl"), 1),
                _fmt_pct(r.get("pct_neither")),
            ])
        out.append(_table(headers, rows))

    return "\n".join(out)


# ---------------------------------------------------------------------------
# Main renderer
# ---------------------------------------------------------------------------

def render_anchor_interaction_tp_sl_spec(ctx: Dict[str, Any]) -> str:
    packages = ctx.get("packages", {}) or {}
    # section-local options (from sections[].options in YAML)
    options = ctx.get("options") or {}

    # --- run_ids filter: if specified, only show those runs ---
    run_ids_filter: Optional[List[str]] = options.get("run_ids")
    if run_ids_filter and isinstance(run_ids_filter, list):
        packages = {k: v for k, v in packages.items() if k in run_ids_filter}

    max_candidate_rows = int(options.get("max_candidate_rows_per_run", 40) or 40)

    # --- resolve anchor_interaction config from full YAML (top-level block) ---
    cfg = _resolve_anchor_cfg(ctx)
    tp_sl = cfg.get("tp_sl", {}) or {}
    folds = tp_sl.get("folds", {}) or {}
    trade_time_cfg = cfg.get("trade_time_tp_sl", {}) or {}
    mode = str(cfg.get("mode", "discovery") or "discovery").lower()

    # When mode is strategy_aware, treat trade_time as implicitly enabled
    # even if the sub-block is absent (engine handles this; show a note here)
    trade_time_implicit = (not trade_time_cfg) and (mode == "strategy_aware")

    # Pull validation actuals from first available package
    derived_validation: Dict[str, Any] = {}
    for pkg in packages.values():
        try:
            md = getattr(pkg, "metadata", {}) or {}
            ai = (md.get("derived") or {}).get("anchor_interaction", {}) or {}
            derived_validation = (ai.get("diagnostics") or {}).get("validation", {}) or {}
            if derived_validation:
                break
        except Exception:
            pass

    html_out: List[str] = ["<h3>MA Anchor TP/SL Specification</h3>"]

    # -----------------------------------------------------------------------
    # Panel 1 — Engine config summary
    # -----------------------------------------------------------------------
    if cfg:
        engine_rows = [
            ["mode", _safe_str(cfg.get("mode", "discovery"))],
            ["anchor_source", _safe_str(cfg.get("anchor_source", "yaml"))],
            ["strategy_family", _safe_str(cfg.get("strategy_family") or "—")],
            ["instrument", _safe_str(cfg.get("instrument"))],
            ["contract", _safe_str(cfg.get("contract"))],
            ["timeframe", _safe_str(cfg.get("timeframe", "1m"))],
        ]
        # Show which anchors were resolved (from first pkg diagnostics if available)
        for pkg in packages.values():
            try:
                md = getattr(pkg, "metadata", {}) or {}
                ai = (md.get("derived") or {}).get("anchor_interaction", {}) or {}
                eng = ai.get("engine", {}) or {}
                src_used = eng.get("anchor_source_used", "")
                resolved = eng.get("anchors_resolved", [])
                if src_used:
                    engine_rows.append(["anchor_source_used", _safe_str(src_used)])
                if resolved:
                    engine_rows.append(["anchors_resolved", _safe_str(", ".join(resolved))])
                break
            except Exception:
                pass
        html_out.append("<h4>Engine Configuration</h4>")
        html_out.append(_table(["parameter", "value"], engine_rows))

    # -----------------------------------------------------------------------
    # Panel 2 — Structural TP/SL config
    # -----------------------------------------------------------------------
    html_out.append("<h4 style='margin-top:20px'>Structural TP/SL (Segment-Based)</h4>")
    if not tp_sl:
        html_out.append(_muted(
            "No anchor_interaction.tp_sl block in report.yaml. "
            "Structural TP/SL scoring is disabled."
        ))
    else:
        rows = [
            ["enabled", "yes" if bool(_cfg_get(tp_sl, "enabled", False)) else "no"],
            ["unit", _safe_str(_cfg_get(tp_sl, "unit", "atr"))],
            ["tp_grid", _safe_str(", ".join(str(x) for x in (_cfg_get(tp_sl, "tp_grid", []) or [])))],
            ["sl_grid", _safe_str(", ".join(str(x) for x in (_cfg_get(tp_sl, "sl_grid", []) or [])))],
        ]
        html_out.append(_table(["parameter", "value"], rows))

        val_rows = [
            [
                "fold_mode",
                _safe_str(
                    _cfg_get(derived_validation, "fold_mode",
                             _cfg_get(folds, "mode", "anchored_walk_forward"))
                ),
            ],
            [
                "min_train_segments",
                _safe_int(
                    _cfg_get(derived_validation, "min_train_segments",
                             _cfg_get(folds, "min_train_segments", 150))
                ),
            ],
            [
                "min_test_segments",
                _safe_int(
                    _cfg_get(derived_validation, "min_test_segments",
                             _cfg_get(folds, "min_test_segments", 50))
                ),
            ],
        ]
        html_out.append("<h5 style='margin-top:10px'>Validation Controls</h5>")
        html_out.append(_table(["parameter", "value"], val_rows))

    # -----------------------------------------------------------------------
    # Panel 3 — Trade-time TP/SL config
    # -----------------------------------------------------------------------
    html_out.append("<h4 style='margin-top:24px'>Trade-Entry Simulation (Trade-Time TP/SL)</h4>")
    if trade_time_implicit:
        html_out.append(_muted(
            "mode: strategy_aware — trade-time simulation is enabled automatically "
            "when pkg.trades is present. Add a trade_time_tp_sl: block to customise grids."
        ))
    elif not trade_time_cfg:
        html_out.append(_muted(
            "No trade_time_tp_sl block found in anchor_interaction config. "
            "Add trade_time_tp_sl: {enabled: true} or set mode: strategy_aware."
        ))
    else:
        tt_rows = [
            ["enabled", "yes" if bool(_cfg_get(trade_time_cfg, "enabled", False)) else "no"],
            ["atr_period", _safe_int(_cfg_get(trade_time_cfg, "atr_period", 14))],
            ["max_bars_forward", _safe_int(_cfg_get(trade_time_cfg, "max_bars_forward", 120))],
            [
                "tp_grid",
                _safe_str(", ".join(str(x) for x in (_cfg_get(trade_time_cfg, "tp_grid", []) or []))),
            ],
            [
                "sl_grid",
                _safe_str(", ".join(str(x) for x in (_cfg_get(trade_time_cfg, "sl_grid", []) or []))),
            ],
        ]
        html_out.append(_table(["parameter", "value"], tt_rows))

    html_out.append(
        "<p style='font-size:0.82em;color:#999;margin-top:8px'>"
        "<strong>Structural</strong>: scores (tp, sl) grids against MA cross-segment paths across all history. "
        "<strong>Trade-Entry</strong>: simulates forward from each actual trade's entry bar — "
        "what TP/SL would have worked on those specific entries."
        "</p>"
    )

    # -----------------------------------------------------------------------
    # Panel 4 — Trade-time results per run
    # -----------------------------------------------------------------------
    has_any_trade_time = False
    for run_id, pkg in packages.items():
        try:
            ai_assets = (getattr(pkg, "assets", {}) or {}).get("anchor_interaction", {}) or {}
            ttc = ai_assets.get("trade_time_candidates")
            md = getattr(pkg, "metadata", {}) or {}
            ai_diag = (
                ((md.get("derived") or {}).get("anchor_interaction") or {})
                .get("diagnostics", {}) or {}
            )
            if isinstance(ttc, pd.DataFrame) and not ttc.empty:
                has_any_trade_time = True
                html_out.append(
                    _render_trade_time_panel(run_id, ttc, ai_diag, max_rows=max_candidate_rows)
                )
        except Exception:
            continue

    if not has_any_trade_time and packages:
        # Determine why and give a precise message
        any_ran = False
        any_disabled = False
        any_failed = False
        failure_reasons: List[str] = []

        for pkg in packages.values():
            try:
                md = getattr(pkg, "metadata", {}) or {}
                ai = (md.get("derived") or {}).get("anchor_interaction", {}) or {}
                diag = ai.get("diagnostics", {}) or {}
                if diag.get("trade_time_tp_sl_run"):
                    any_ran = True
                if ai.get("disabled"):
                    any_disabled = True
                    reason = ai.get("reason", "")
                    if reason:
                        failure_reasons.append(f"{getattr(pkg, 'run_id', '?')}: {reason}")
            except Exception:
                pass

        if any_disabled:
            msg = "Anchor analysis did not complete for one or more runs."
            if failure_reasons:
                details = "; ".join(failure_reasons[:3])
                msg += f" Reasons: {details}"
            html_out.append(_muted(msg))
            html_out.append(_muted(
                "Common causes: no anchors resolved from settings "
                "(check strategy_family name and that _Settings.csv files are present), "
                "or market bars not loaded for the configured instrument/contract."
            ))
        elif any_ran:
            html_out.append(_muted(
                "Trade-time simulation ran but produced no candidates. "
                "Check that trades have tz-aware entry times and bars cover the trade window."
            ))
        else:
            html_out.append(_muted(
                "Trade-time simulation did not run. "
                "Possible causes: no _Trades.csv loaded for these runs, "
                "or pkg.trades is empty."
            ))

    return "\n".join(html_out)
