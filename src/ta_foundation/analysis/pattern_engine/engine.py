from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import itertools
import json
from typing import Any, Dict, List, Optional, Tuple
import inspect

import numpy as np
import pandas as pd


# ======================================================================================
# Diagnostics (JSON-safe)
# ======================================================================================

@dataclass
class SweepDiagnostics:
    ok: bool = True
    issues: Optional[List[str]] = None

    # counts
    n_bars: int = 0
    n_pattern_defs_loaded: int = 0
    n_pattern_defs_enabled: int = 0
    n_patterns_emitted: int = 0
    n_signals_emitted: int = 0

    # context
    bar_tf: Optional[str] = None
    bar_start: Optional[str] = None
    bar_end: Optional[str] = None

    # filter breakdown
    filter_reasons: Optional[Dict[str, int]] = None

    def __post_init__(self) -> None:
        if self.issues is None:
            self.issues = []
        if self.filter_reasons is None:
            self.filter_reasons = {}


def _bars_summary(bars: pd.DataFrame) -> Tuple[int, Optional[str], Optional[str]]:
    if bars is None or not isinstance(bars, pd.DataFrame) or bars.empty:
        return 0, None, None
    if "dt" in bars.columns:
        try:
            return int(len(bars)), str(bars["dt"].iloc[0]), str(bars["dt"].iloc[-1])
        except Exception:
            return int(len(bars)), None, None
    return int(len(bars)), None, None

def _prepare_detect_params(tmpl: Any, params: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    p = dict(params or {})

    # --- common aliases (so YAML can use older names)
    aliases = {
        "orb_minutes": ["or_minutes", "or_mins", "opening_range_minutes", "orb_mins"],
        "retest_bars": ["retest", "retest_n", "retest_bars_count"],
    }
    for canonical, alts in aliases.items():
        if canonical not in p:
            for a in alts:
                if a in p:
                    p[canonical] = p[a]
                    break

    # --- template-provided defaults (optional)
    defaults = {}
    if hasattr(tmpl, "defaults") and isinstance(getattr(tmpl, "defaults"), dict):
        defaults.update(getattr(tmpl, "defaults"))
    if hasattr(tmpl, "params_defaults") and isinstance(getattr(tmpl, "params_defaults"), dict):
        defaults.update(getattr(tmpl, "params_defaults"))
    for k, v in defaults.items():
        p.setdefault(k, v)

    # --- signature-based required kw-only detection
    missing: List[str] = []
    try:
        sig = inspect.signature(tmpl.detect_fn)
        for name, prm in sig.parameters.items():
            if name in ("bars", "direction"):
                continue
            if prm.kind == inspect.Parameter.KEYWORD_ONLY and prm.default is inspect._empty:
                if name not in p:
                    missing.append(name)
    except Exception:
        # If we can't introspect, don't block execution here
        missing = []

    return p, missing

def _finalize_diagnostics(diag: SweepDiagnostics) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "ok": bool(diag.ok),
        "issues": list(diag.issues or []),
        "counts": {
            "n_bars": int(diag.n_bars),
            "n_pattern_defs_loaded": int(diag.n_pattern_defs_loaded),
            "n_pattern_defs_enabled": int(diag.n_pattern_defs_enabled),
            "n_patterns": int(diag.n_patterns_emitted),
            "n_signals": int(diag.n_signals_emitted),
        },
        "context": {
            "bar_tf": diag.bar_tf,
            "bar_start": diag.bar_start,
            "bar_end": diag.bar_end,
        },
        "filters": dict(diag.filter_reasons or {}),
    }

    if diag.n_bars == 0:
        out["issues"].append("No bars available for sweep (bars empty).")
    if diag.n_pattern_defs_loaded == 0:
        out["issues"].append("No pattern definitions configured. Provide options.sweep.patterns.")
    if diag.n_pattern_defs_loaded > 0 and diag.n_pattern_defs_enabled == 0:
        out["issues"].append("Pattern definitions exist but none are enabled (enabled=false everywhere).")

    if out["issues"]:
        out["ok"] = False
    return out


# ======================================================================================
# Canonical IDs / JSON
# ======================================================================================

def json_canonical(x: Any) -> str:
    """
    Canonical JSON encoding for parameter snapshots.
    Stable ordering so pattern_id hashing / params_json comparisons are deterministic.
    """
    try:
        return json.dumps(x, sort_keys=True, separators=(",", ":"), default=str)
    except Exception:
        return json.dumps(str(x), sort_keys=True, separators=(",", ":"))


def _stable_hash(s: str, n: int = 10) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:n]


def _make_pattern_id(*, family: str, structure: str, params: Dict[str, Any], direction_mode: str) -> str:
    fam = str(family).strip()
    struct = str(structure).strip()
    dm = str(direction_mode).strip()
    pjson = json_canonical(params or {})
    h = _stable_hash(pjson, 10)
    return f"{fam}:{struct}:{dm}:{h}"


def _make_signal_id(
    *,
    pattern_id: str,
    dt: Any,
    direction: int,
    instrument: str,
    contract: str,
) -> str:
    if hasattr(dt, "to_pydatetime"):
        dt_obj = dt.to_pydatetime()
    elif isinstance(dt, datetime):
        dt_obj = dt
    else:
        try:
            dt_obj = datetime.fromisoformat(str(dt))
        except Exception:
            dt_obj = None

    dt_key = dt_obj.isoformat() if dt_obj else str(dt)
    base = f"{pattern_id}|{dt_key}|{int(direction)}|{instrument}|{contract}"
    return _stable_hash(base, 16)


# ======================================================================================
# Events DF builders (MC input)
# ======================================================================================

def _day_id_from_dt(dt: pd.Series) -> pd.Series:
    d = pd.to_datetime(dt, errors="coerce")
    return d.dt.strftime("%Y-%m-%d")


def build_events_df_from_outcomes(
    outcomes_df: pd.DataFrame,
    *,
    entity_id_col: str = "pattern_id",
    dt_col: str = "dt",
    pnl_col: str = "pnl_ticks",
    horizon_col: str = "horizon",
    regime_col: str = "regime",
    vol_col: str = "vol_metric",
    entity_type: str = "pattern",
) -> pd.DataFrame:
    """
    Convert outcomes rows into MC-ready equity events.

    Output schema (stable):
      dt, day_id, entity_type, entity_id, horizon, pnl_ticks, regime, vol_metric
    """
    cols = ["dt", "day_id", "entity_type", "entity_id", "horizon", "pnl_ticks", "regime", "vol_metric"]
    if outcomes_df is None or not isinstance(outcomes_df, pd.DataFrame) or outcomes_df.empty:
        return pd.DataFrame(columns=cols)

    d = outcomes_df.copy()
    if dt_col not in d.columns or pnl_col not in d.columns or entity_id_col not in d.columns:
        return pd.DataFrame(columns=cols)

    d["dt"] = pd.to_datetime(d[dt_col], errors="coerce")
    d["pnl_ticks"] = pd.to_numeric(d[pnl_col], errors="coerce")
    d["entity_id"] = d[entity_id_col].astype(str)
    d["entity_type"] = str(entity_type)

    if horizon_col in d.columns:
        d["horizon"] = pd.to_numeric(d[horizon_col], errors="coerce").fillna(-1).astype(int)
    else:
        d["horizon"] = -1

    d["day_id"] = _day_id_from_dt(d["dt"])

    if regime_col in d.columns:
        d["regime"] = d[regime_col].astype(str).fillna("UNK")
    else:
        d["regime"] = "UNK"

    if vol_col in d.columns:
        d["vol_metric"] = pd.to_numeric(d[vol_col], errors="coerce")
    else:
        d["vol_metric"] = np.nan


    d = d.dropna(subset=["dt", "pnl_ticks", "entity_id"]).sort_values("dt").reset_index(drop=True)


    return d[cols]


def build_events_df_from_trades(
    trades_df: pd.DataFrame,
    *,
    dt_col: str = "entry_dt",
    pnl_ticks_col: str = "pnl_ticks",
    horizon: int = -1,
    entity_id: str = "actual",
    entity_type: str = "strategy",
) -> pd.DataFrame:
    cols = ["dt", "day_id", "entity_type", "entity_id", "horizon", "pnl_ticks", "regime", "vol_metric"]
    if trades_df is None or not isinstance(trades_df, pd.DataFrame) or trades_df.empty:
        return pd.DataFrame(columns=cols)

    d = trades_df.copy()
    if dt_col not in d.columns or pnl_ticks_col not in d.columns:
        return pd.DataFrame(columns=cols)

    d["dt"] = pd.to_datetime(d[dt_col], errors="coerce")
    d["pnl_ticks"] = pd.to_numeric(d[pnl_ticks_col], errors="coerce")
    d["entity_id"] = str(entity_id)
    d["entity_type"] = str(entity_type)
    d["horizon"] = int(horizon)
    d["day_id"] = _day_id_from_dt(d["dt"])

    # optional passthrough if they exist
    d["regime"] = d["regime"].astype(str) if "regime" in d.columns else "UNK"
    d["vol_metric"] = pd.to_numeric(d["vol_metric"], errors="coerce") if "vol_metric" in d.columns else np.nan

    d = d.dropna(subset=["dt", "pnl_ticks"]).sort_values("dt").reset_index(drop=True)
    return d[cols]


# ======================================================================================
# Pattern sweep core
# ======================================================================================

def _ensure_signal_cols(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or not isinstance(df, pd.DataFrame):
        df = pd.DataFrame()

    required_defaults = {
        "signal_id": "",
        "pattern_id": "",
        "dt": pd.NaT,
        "instrument": "",
        "contract": "",
        "direction": 0,
        "entry_ref_price": float("nan"),
        "session_id": "",
        "day_id": "",
        "tod_bucket": "",
        "regime": "UNK",
        "features_json": "{}",
    }

    out = df.copy()
    for col, default in required_defaults.items():
        if col not in out.columns:
            out[col] = default

    try:
        out["dt"] = pd.to_datetime(out["dt"], errors="coerce")
    except Exception:
        pass
    try:
        out["direction"] = pd.to_numeric(out["direction"], errors="coerce").fillna(0).astype(int)
    except Exception:
        pass

    ordered = list(required_defaults.keys()) + [c for c in out.columns if c not in required_defaults]
    return out[ordered]


def _tod_bucket(dt: Any) -> str:
    if hasattr(dt, "to_pydatetime"):
        dt = dt.to_pydatetime()
    if not isinstance(dt, datetime):
        try:
            dt = datetime.fromisoformat(str(dt))
        except Exception:
            return "UNK"
    minute = (dt.minute // 15) * 15
    return f"{dt.hour:02d}:{minute:02d}"


# ======================================================================================
# Time-of-day window filtering (compute-only)
# ======================================================================================

def _parse_hhmm_to_min(s: Any) -> Optional[int]:
    """
    Parse "HH:MM" into minutes since midnight. Returns None on failure.
    """
    if s is None:
        return None
    try:
        ss = str(s).strip()
        if not ss:
            return None
        hh, mm = ss.split(":")
        h = int(hh)
        m = int(mm)
        if h < 0 or h > 23 or m < 0 or m > 59:
            return None
        return h * 60 + m
    except Exception:
        return None


def _resolve_time_windows(*, options: Dict[str, Any], pattern_cfg: Dict[str, Any]) -> Tuple[Dict[str, Dict[str, Any]], List[str]]:
    """
    Resolve window definitions + selected window IDs for the current pattern.

    Resolution order for selected window IDs:
      1) pattern_cfg["time_windows"] (list[str])
      2) options["market_discovery"]["default_time_windows"] (list[str])
      3) options["sweep"]["default_time_windows"] (list[str])
      4) [] (no filtering)

    Window definitions come from:
      - options["market_discovery"]["time_windows"] if present else options["sweep"]["time_windows"]

    Returns:
      (windows_by_id, selected_ids)
    """
    sweep = options.get("sweep", {}) or {}
    md = options.get("market_discovery", {}) or {}

    # definitions
    defs = md.get("time_windows", None)
    if defs is None:
        defs = sweep.get("time_windows", []) or []
    windows_by_id: Dict[str, Dict[str, Any]] = {}
    for w in defs:
        if not isinstance(w, dict):
            continue
        wid = str(w.get("id", "")).strip()
        if not wid:
            continue
        windows_by_id[wid] = dict(w)

    # selection
    sel = pattern_cfg.get("time_windows", None)
    if sel is None:
        sel = md.get("default_time_windows", None)
    if sel is None:
        sel = sweep.get("default_time_windows", None)

    selected_ids: List[str] = []
    if isinstance(sel, str):
        selected_ids = [s.strip() for s in sel.split(",") if s.strip()]
    elif isinstance(sel, list):
        selected_ids = [str(x).strip() for x in sel if str(x).strip()]
    else:
        selected_ids = []

    return windows_by_id, selected_ids


def _coerce_dt_series(dt_s: pd.Series) -> pd.Series:
    out = pd.to_datetime(dt_s, errors="coerce")
    out = out.dropna()
    # preserve index alignment by reindexing back
    return pd.to_datetime(dt_s, errors="coerce")


def _localize_or_convert(dt_s: pd.Series, *, assumed_tz: str, target_tz: str) -> pd.Series:
    """
    Ensure dt_s is tz-aware then convert to target_tz.
    If dt_s is tz-naive, localize to assumed_tz first.
    """
    d = pd.to_datetime(dt_s, errors="coerce")
    try:
        if getattr(d.dt, "tz", None) is None:
            d = d.dt.tz_localize(assumed_tz)
        return d.dt.tz_convert(target_tz)
    except Exception:
        # If tz operations fail, fall back to naive comparison in original tz.
        return d


def _window_mask(dt_s: pd.Series, *, start_min: int, end_min: int) -> pd.Series:
    """
    Compute boolean mask for dt_s (tz-aware or naive) within [start, end) minutes.
    Supports wraparound windows where start > end (crosses midnight).
    """
    d = pd.to_datetime(dt_s, errors="coerce")
    mins = (d.dt.hour * 60 + d.dt.minute).astype("float")
    ok = mins.notna()
    mins_i = mins.fillna(-1).astype(int)

    if start_min <= end_min:
        m = (mins_i >= int(start_min)) & (mins_i < int(end_min))
    else:
        # crosses midnight
        m = (mins_i >= int(start_min)) | (mins_i < int(end_min))
    return ok & m


def _filter_fired_by_time_windows(
    fired: pd.DataFrame,
    *,
    options: Dict[str, Any],
    pattern_cfg: Dict[str, Any],
    diag: Optional[SweepDiagnostics] = None,
) -> pd.DataFrame:
    """
    Filter fired bars (signals) by configured time windows.

    - No IO
    - Deterministic
    - Supports wraparound windows (e.g., 23:00–02:00)
    - If dt is tz-naive, assumes options.sweep.bars_timezone (default "America/New_York"),
      with market_discovery override options.market_discovery.bars_timezone.

    If all selected windows share the same timezone, we also recompute fired["day_id"]/["session_id"]
    in that timezone to prevent midnight boundary drift.
    """
    if fired is None or not isinstance(fired, pd.DataFrame) or fired.empty:
        return fired

    windows_by_id, selected_ids = _resolve_time_windows(options=options, pattern_cfg=pattern_cfg)
    if not selected_ids:
        return fired
    n_before = int(len(fired))


    md = options.get("market_discovery", {}) or {}
    sweep = options.get("sweep", {}) or {}
    assumed_tz = str(md.get("bars_timezone", sweep.get("bars_timezone", "America/New_York")))

    if "dt" not in fired.columns:
        return fired

    base_dt = pd.to_datetime(fired["dt"], errors="coerce")
    keep_any = pd.Series(False, index=fired.index)

    tzs_used: List[str] = []

    for wid in selected_ids:
        w = windows_by_id.get(wid)
        if not isinstance(w, dict):
            continue
        start_min = _parse_hhmm_to_min(w.get("start"))
        end_min = _parse_hhmm_to_min(w.get("end"))
        if start_min is None or end_min is None:
            continue

        tz = str(w.get("tz", assumed_tz) or assumed_tz)
        tzs_used.append(tz)

        dt_local = _localize_or_convert(base_dt, assumed_tz=assumed_tz, target_tz=tz)
        wmask = _window_mask(dt_local, start_min=int(start_min), end_min=int(end_min))
        keep_any = keep_any | wmask
        if diag is not None:
            try:
                diag.filter_reasons[f"tw:{wid}:kept"] = int(diag.filter_reasons.get(f"tw:{wid}:kept", 0)) + int(wmask.fillna(False).sum())
            except Exception:
                pass

    out = fired.loc[keep_any.fillna(False)].copy()
    if diag is not None:
        try:
            n_after = int(len(out))
            diag.filter_reasons["time_window_dropped"] = int(diag.filter_reasons.get("time_window_dropped", 0)) + int(max(n_before - n_after, 0))
            diag.filter_reasons["time_window_kept"] = int(diag.filter_reasons.get("time_window_kept", 0)) + int(n_after)
        except Exception:
            pass

    if out.empty:
        return out

    # If all windows share same tz, normalize day_id/session_id to that tz.
    uniq_tz = sorted({t for t in tzs_used if t})
    if len(uniq_tz) == 1:
        tz = uniq_tz[0]
        dt_local = _localize_or_convert(pd.to_datetime(out["dt"], errors="coerce"), assumed_tz=assumed_tz, target_tz=tz)
        try:
            out["day_id"] = dt_local.dt.strftime("%Y-%m-%d")
            # preserve existing session_id if present, otherwise derive
            if "session_id" not in out.columns or out["session_id"].isna().all():
                out["session_id"] = out["day_id"].astype(str)
        except Exception:
            pass

    return out

def _compute_fixed_horizon_outcomes(
    *,
    bars: pd.DataFrame,
    signals: pd.DataFrame,
    horizons: List[int],
    tick_size: float,
) -> pd.DataFrame:
    cols = [
        "signal_id", "pattern_id", "dt", "direction", "horizon",
        "exit_dt", "entry_px", "exit_px", "pnl_ticks",
    ]
    if signals is None or not isinstance(signals, pd.DataFrame) or signals.empty:
        return pd.DataFrame(columns=cols)
    if bars is None or not isinstance(bars, pd.DataFrame) or bars.empty:
        return pd.DataFrame(columns=cols)

    b = bars.copy()
    if "dt" not in b.columns:
        raise ValueError("bars missing dt")
    if "close" not in b.columns:
        raise ValueError("bars missing close")

    b["dt"] = pd.to_datetime(b["dt"], errors="coerce")
    b = b.dropna(subset=["dt"]).sort_values("dt").reset_index(drop=True)
    b["_bar_i"] = np.arange(len(b), dtype=int)

    s = signals.copy()
    s["dt"] = pd.to_datetime(s["dt"], errors="coerce")
    s = s.dropna(subset=["dt"]).sort_values("dt")

    merged = pd.merge_asof(
        s.sort_values("dt"),
        b[["dt", "_bar_i", "close"]],
        on="dt",
        direction="backward",
        allow_exact_matches=True,
    ).rename(columns={"close": "entry_px", "_bar_i": "entry_bar_i"})

    merged = merged.dropna(subset=["entry_bar_i", "entry_px"])
    merged["entry_bar_i"] = merged["entry_bar_i"].astype(int)

    rows: List[Dict[str, Any]] = []
    for r in merged.itertuples(index=False):
        entry_i = int(getattr(r, "entry_bar_i"))
        entry_px = float(getattr(r, "entry_px"))
        direction = int(getattr(r, "direction"))
        signal_id = str(getattr(r, "signal_id"))
        pattern_id = str(getattr(r, "pattern_id"))
        dt = getattr(r, "dt")

        for h in horizons:
            exit_i = entry_i + int(h)
            if exit_i >= len(b):
                continue
            exit_dt = b.at[exit_i, "dt"]
            exit_px = float(b.at[exit_i, "close"])

            pnl = (exit_px - entry_px) * direction
            pnl_ticks = pnl / float(tick_size) if tick_size else float("nan")

            rows.append(
                {
                    "signal_id": signal_id,
                    "pattern_id": pattern_id,
                    "dt": dt,
                    "direction": direction,
                    "horizon": int(h),
                    "exit_dt": exit_dt,
                    "entry_px": entry_px,
                    "exit_px": exit_px,
                    "pnl_ticks": float(pnl_ticks),
                }
            )

    return pd.DataFrame(rows, columns=cols)


def _pattern_stats_from_outcomes(outcomes: pd.DataFrame) -> pd.DataFrame:
    cols = ["pattern_id", "horizon", "n_signals", "win_rate", "avg_ticks", "net_ticks", "p10", "p50", "p90"]
    if outcomes is None or not isinstance(outcomes, pd.DataFrame) or outcomes.empty:
        return pd.DataFrame(columns=cols)
    if "pnl_ticks" not in outcomes.columns:
        return pd.DataFrame(columns=cols)

    df = outcomes.copy()

    def q(series: pd.Series, quant: float) -> float:
        try:
            return float(series.quantile(quant))
        except Exception:
            return float("nan")

    rows: List[Dict[str, Any]] = []
    for (pid, h), sub in df.groupby(["pattern_id", "horizon"], dropna=False):
        pnl = pd.to_numeric(sub["pnl_ticks"], errors="coerce").dropna()
        if len(pnl) == 0:
            continue
        n = int(len(pnl))
        wins = int((pnl > 0).sum())
        rows.append(
            {
                "pattern_id": str(pid),
                "horizon": int(h),
                "n_signals": n,
                "win_rate": float(wins / n) if n else float("nan"),
                "avg_ticks": float(pnl.mean()),
                "net_ticks": float(pnl.sum()),
                "p10": q(pnl, 0.10),
                "p50": q(pnl, 0.50),
                "p90": q(pnl, 0.90),
            }
        )

    return pd.DataFrame(rows, columns=cols)


def run_pattern_sweep(
    *,
    pkg: Any,
    market: Any,
    options: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Compute-only (no disk IO). Orchestrator writes parquet + attaches refs.

    Returns:
      patterns_df, signals_df, outcomes_df, pattern_stats_df, events_df, diagnostics
    """
    if not options.get("enabled", False):
        return {
            "patterns_df": pd.DataFrame(),
            "signals_df": pd.DataFrame(),
            "outcomes_df": pd.DataFrame(),
            "pattern_stats_df": pd.DataFrame(),
            "events_df": pd.DataFrame(),
            "diagnostics": {"ok": True, "reason": "disabled"},
        }

    instrument = str(options.get("instrument", "")).strip()
    contract = str(options.get("contract", "")).strip()
    timeframe = str(options.get("timeframe", "1m")).strip()
    tick_size = float(options.get("tick_size", 0.25))
    horizons = [int(x) for x in (options.get("horizons") or [])]

    if not instrument or not contract or not horizons:
        return {
            "patterns_df": pd.DataFrame(),
            "signals_df": pd.DataFrame(),
            "outcomes_df": pd.DataFrame(),
            "pattern_stats_df": pd.DataFrame(),
            "events_df": pd.DataFrame(),
            "diagnostics": {"ok": False, "reason": "missing instrument/contract/horizons"},
        }

    md = options.get("market_discovery", {}) or {}
    bars = market.get_bars(
        instrument_root=instrument,
        contract=contract,
        timeframe=timeframe,
        start=md.get("start", None),
        end=md.get("end", None),
        source=md.get("bars_source", options.get("bars_source", "auto")),
    )

    if bars is None or not isinstance(bars, pd.DataFrame) or len(bars) == 0:
        return {
            "patterns_df": pd.DataFrame(),
            "signals_df": pd.DataFrame(),
            "outcomes_df": pd.DataFrame(),
            "pattern_stats_df": pd.DataFrame(),
            "events_df": pd.DataFrame(),
            "diagnostics": {"ok": False, "reason": "no bars found"},
        }

    bars = bars.copy()
    if "dt" not in bars.columns:
        if "time" in bars.columns:
            bars = bars.rename(columns={"time": "dt"})
        elif "datetime" in bars.columns:
            bars = bars.rename(columns={"datetime": "dt"})
    if "dt" not in bars.columns:
        return {
            "patterns_df": pd.DataFrame(),
            "signals_df": pd.DataFrame(),
            "outcomes_df": pd.DataFrame(),
            "pattern_stats_df": pd.DataFrame(),
            "events_df": pd.DataFrame(),
            "diagnostics": {"ok": False, "reason": "bars missing dt column"},
        }

    bars["dt"] = pd.to_datetime(bars["dt"], errors="coerce")
    bars = bars.dropna(subset=["dt"]).sort_values("dt").reset_index(drop=True)
    if "day_id" not in bars.columns:
        bars["day_id"] = _day_id_from_dt(bars["dt"])
    if "session_id" not in bars.columns:
        bars["session_id"] = bars["day_id"].astype(str)


    # Diagnostics accumulator (JSON-safe)
    diag = SweepDiagnostics(ok=True)
    n_bars, bar_start, bar_end = _bars_summary(bars)
    diag.n_bars = n_bars
    diag.bar_tf = timeframe
    diag.bar_start = bar_start
    diag.bar_end = bar_end

    # Template registry
    try:
        from .templates.builtins import default_template_registry  # type: ignore
    except Exception as e:
        return {
            "patterns_df": pd.DataFrame(),
            "signals_df": pd.DataFrame(),
            "outcomes_df": pd.DataFrame(),
            "pattern_stats_df": pd.DataFrame(),
            "events_df": pd.DataFrame(),
            "diagnostics": {"ok": False, "reason": f"template_registry_import_failed: {type(e).__name__}: {e}"},
        }

    registry = default_template_registry()
    sweep = options.get("sweep", {}) or {}
    patterns_cfg = sweep.get("patterns", []) or []
    diag.n_pattern_defs_loaded = int(len(patterns_cfg))
    if not patterns_cfg:
        return {
            "patterns_df": pd.DataFrame(),
            "signals_df": pd.DataFrame(),
            "outcomes_df": pd.DataFrame(),
            "pattern_stats_df": pd.DataFrame(),
            "events_df": pd.DataFrame(),
            "diagnostics": {"ok": False, "reason": "no sweep patterns configured (pattern_engine.sweep.patterns is empty)"},
        }

    diag.n_pattern_defs_enabled = int(sum(1 for p in patterns_cfg if bool(p.get("enabled", True))))

    patterns_rows: List[Dict[str, Any]] = []
    signals_rows: List[Dict[str, Any]] = []

    for p in patterns_cfg:
        family = str(p.get("family", "")).strip()
        structure = str(p.get("structure", "")).strip()
        params_grid = p.get("params", {}) or {}
        direction_mode = str(p.get("direction_mode", "both")).strip()

        if not family or not structure:
            continue

        tmpl = registry.get(family, structure)

        keys = list(params_grid.keys())
        values: List[List[Any]] = []
        for k in keys:
            v = params_grid.get(k)
            values.append(v if isinstance(v, list) else [v])

        dir_list = [1, -1] if direction_mode == "both" else ([1] if direction_mode == "long" else [-1])

        for combo in itertools.product(*values):
            params = {k: combo[i] for i, k in enumerate(keys)}
            params_json = json_canonical(params)
            pat_id = _make_pattern_id(family=family, structure=structure, params=params, direction_mode=direction_mode)

            patterns_rows.append(
                {
                    "pattern_id": pat_id,
                    "family": family,
                    "structure": structure,
                    "direction_mode": direction_mode,
                    "params_json": params_json,
                    "requires_ticks": bool(getattr(tmpl, "requires_ticks", False)),
                    "version": "pe_v1",
                }
            )

            for d in dir_list:
                prepared_params, missing = _prepare_detect_params(tmpl, params)

                if missing:
                    # Skip this param combo cleanly; DO NOT kill the whole run
                    # If you have a diagnostics accumulator, log it there.
                    # Example:
                    # diag["issues"].append(f"{family}:{structure} missing required params: {missing}")
                    continue

                try:
                    mask = tmpl.detect_fn(bars=bars, direction=d, **prepared_params)
                except TypeError as e:
                    # Skip bad template/param combos without crashing
                    # Example:
                    # diag["issues"].append(f"{family}:{structure} TypeError: {e}")
                    continue
                except Exception as e:
                    # Any other unexpected template failure: skip combo
                    # Example:
                    # diag["issues"].append(f"{family}:{structure} error: {type(e).__name__}: {e}")
                    continue

                if mask is None or len(mask) != len(bars):
                    continue

                fired = bars.loc[np.asarray(mask, dtype=bool)].copy()
                if len(fired) == 0:
                    continue

                # NEW: apply time window filter here
                fired = _filter_fired_by_time_windows(
                    fired,
                    options=options,
                    pattern_cfg=p,  # current pattern config
                    diag=diag,
                )
                if len(fired) == 0:
                    continue

                for r in fired.itertuples(index=False):
                    dt = pd.to_datetime(getattr(r, "dt"))
                    entry_px = float(getattr(r, "close")) if hasattr(r, "close") else np.nan
                    sid = _make_signal_id(pattern_id=pat_id, dt=dt, direction=d, instrument=instrument, contract=contract)

                    day_id = getattr(r, "day_id", _day_id_from_dt(pd.Series([dt])).iloc[0])
                    session_id = getattr(r, "session_id", f"{day_id}_UNK")

                    signals_rows.append(
                        {
                            "signal_id": sid,
                            "pattern_id": pat_id,
                            "dt": dt,
                            "instrument": instrument,
                            "contract": contract,
                            "direction": int(d),
                            "entry_ref_price": entry_px,
                            "session_id": session_id,
                            "day_id": day_id,
                            "tod_bucket": _tod_bucket(dt),
                            "regime": getattr(r, "regime", "UNK"),
                            "features_json": "{}",
                        }
                    )

    patterns_df = (
        pd.DataFrame(patterns_rows).drop_duplicates(subset=["pattern_id"]).reset_index(drop=True)
        if patterns_rows else pd.DataFrame()
    )
    signals_df = pd.DataFrame(signals_rows) if signals_rows else pd.DataFrame()
    signals_df = _ensure_signal_cols(signals_df)
    diag.n_patterns_emitted = int(len(patterns_df)) if isinstance(patterns_df, pd.DataFrame) else 0
    diag.n_signals_emitted = int(len(signals_df)) if isinstance(signals_df, pd.DataFrame) else 0

    outcomes_df = _compute_fixed_horizon_outcomes(
        bars=bars,
        signals=signals_df,
        horizons=horizons,
        tick_size=tick_size,
    )
    pattern_stats_df = _pattern_stats_from_outcomes(outcomes_df)


    # events_df: prefer real trades if present; otherwise use outcomes
    trades_df = None
    try:
        trades_df = getattr(pkg, "trades", None) or getattr(pkg, "trades_df", None)
    except Exception:
        trades_df = None

    if isinstance(trades_df, pd.DataFrame) and len(trades_df) > 0 and "pnl_ticks" in trades_df.columns:
        events_df = build_events_df_from_trades(
            trades_df,
            dt_col="entry_dt" if "entry_dt" in trades_df.columns else ("dt" if "dt" in trades_df.columns else "entry_dt"),
            pnl_ticks_col="pnl_ticks",
            entity_id=str(getattr(pkg, "run_id", "actual")),
            entity_type="strategy",
        )
    else:
        # events_df = build_events_df_from_outcomes(outcomes_df, entity_id_col="pattern_id", entity_type="pattern")
        # after outcomes_df computed
        # ---------------------------------------------------------
        # Build MC events (STRICT single-horizon selection)
        # ---------------------------------------------------------
        mc_cfg = options.get("monte_carlo", {}) or {}
        mc_h = int(mc_cfg.get("mc_horizon", 20))

        if isinstance(outcomes_df, pd.DataFrame) and len(outcomes_df) > 0 and "horizon" in outcomes_df.columns:
            # Safe numeric conversion
            tmp = outcomes_df.copy()
            tmp["_h"] = pd.to_numeric(tmp["horizon"], errors="coerce")

            # Exact horizon match
            filtered = tmp.loc[tmp["_h"] == mc_h].drop(columns=["_h"], errors="ignore")

            # If no exact match, fall back to nearest available horizon (deterministic)
            if len(filtered) == 0:
                avail = tmp["_h"].dropna().unique()
                if len(avail) > 0:
                    nearest = min(avail, key=lambda x: abs(x - mc_h))
                    filtered = tmp.loc[tmp["_h"] == nearest].drop(columns=["_h"], errors="ignore")
                else:
                    filtered = pd.DataFrame(columns=tmp.columns)

            # Defensive: ensure 1 row per signal_id
            if "signal_id" in filtered.columns:
                filtered = (
                    filtered.sort_values(["signal_id", "dt"])
                    .drop_duplicates(subset=["signal_id"], keep="first")
                    .reset_index(drop=True)
                )

            events_df = build_events_df_from_outcomes(
                filtered,
                entity_id_col="pattern_id",
                entity_type="pattern",
            )
        else:
            events_df = pd.DataFrame()


    # ---------------------------------------------------------
    # Optional: trade intake model (to avoid treating every raw signal as a trade)
    # ---------------------------------------------------------
    events_exec_df = events_df
    intake_diag: Dict[str, Any] = {}
    try:
        mc_cfg2 = options.get("monte_carlo", {}) or {}
        intake_cfg = mc_cfg2.get("intake", {}) or {}
        seed = int(mc_cfg2.get("seed", 7))
        events_exec_df, intake_diag = apply_trade_intake_model(events_df, intake_cfg=intake_cfg, seed=seed)
    except Exception as e:
        intake_diag = {"enabled": False, "issue": f"intake_exception: {type(e).__name__}: {e}"}
        events_exec_df = events_df
    return {
        "patterns_df": patterns_df,
        "signals_df": signals_df,
        "outcomes_df": outcomes_df,
        "pattern_stats_df": pattern_stats_df,
        "events_df": events_df,
        "diagnostics": {"ok": True, "reason": "ok"},
    }