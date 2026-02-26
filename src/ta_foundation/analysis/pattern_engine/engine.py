from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
# from .templates.registry import TemplateRegistry  # assuming this exists
# from .util import json_canonical  # assuming this exists
import pandas as pd


@dataclass
class SweepDiagnostics:
    ok: bool = True
    issues: List[str] = None

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
    filter_reasons: Dict[str, int] = None

    def __post_init__(self):
        if self.issues is None:
            self.issues = []
        if self.filter_reasons is None:
            self.filter_reasons = {}


def _bars_summary(bars: pd.DataFrame) -> Tuple[int, Optional[str], Optional[str]]:
    if bars is None or not isinstance(bars, pd.DataFrame) or bars.empty:
        return 0, None, None
    dt_col = "dt" if "dt" in bars.columns else None
    if dt_col:
        try:
            return int(len(bars)), str(bars[dt_col].iloc[0]), str(bars[dt_col].iloc[-1])
        except Exception:
            return int(len(bars)), None, None
    return int(len(bars)), None, None


def _load_pattern_defs(options: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Support both shapes:
      - options["patterns"]  (legacy)
      - options["sweep"]["patterns"] (intended config shape)

    Normalize dict -> list and ensure list[dict].
    """
    if not isinstance(options, dict):
        return []

    raw = None
    sweep = options.get("sweep")
    if isinstance(sweep, dict) and sweep.get("patterns") is not None:
        raw = sweep.get("patterns")
    else:
        raw = options.get("patterns")

    if raw is None:
        return []

    if isinstance(raw, dict):
        raw = [{"id": k, **(v or {})} for k, v in raw.items()]

    if not isinstance(raw, list):
        return []

    out: List[Dict[str, Any]] = []
    for item in raw:
        if isinstance(item, dict):
            out.append(item)
    return out


def _finalize_diagnostics(diag: SweepDiagnostics) -> Dict[str, Any]:
    """
    Convert to JSON-safe dict for orchestrator attachment.
    """
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

    # Surface actionable issues
    if diag.n_bars == 0:
        out["issues"].append("No bars available for sweep (bars empty).")

    if diag.n_pattern_defs_loaded == 0:
        out["issues"].append(
            "No pattern definitions configured. Provide options.patterns or options.sweep.patterns."
        )

    if diag.n_pattern_defs_loaded > 0 and diag.n_pattern_defs_enabled == 0:
        out["issues"].append(
            "Pattern definitions exist but none are enabled (enabled=false everywhere)."
        )

    # Critical: your evaluator is placeholder right now
    if diag.n_pattern_defs_enabled > 0 and diag.n_patterns_emitted == 0 and diag.n_signals_emitted == 0:
        out["issues"].append(
            "Pattern evaluation is currently a placeholder (engine emits no patterns/signals). "
            "Wire run_pattern_sweep() to the real pattern evaluator/registry."
        )

    # If there are any issues, mark ok False (so the report doesn't show validation ok)
    if out["issues"]:
        out["ok"] = False

    return out

import json
from typing import Any
import hashlib
from datetime import datetime
from typing import Any, Dict, Optional


def _stable_hash(s: str, n: int = 10) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:n]


def _make_pattern_id(*, family: str, structure: str, params: Dict[str, Any], direction_mode: str) -> str:
    """
    Deterministic, human-readable pattern id.
    Uses a short stable hash of canonical params to avoid long ids.
    """
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
    """
    Deterministic signal id: pattern + timestamp + direction + instrument/contract.
    """
    # dt may be pandas Timestamp / datetime / str
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
def _compute_fixed_horizon_outcomes(
    *,
    bars: pd.DataFrame,
    signals: pd.DataFrame,
    horizons: List[int],
    tick_size: float,
) -> pd.DataFrame:
    """
    Compute forward outcomes for each signal at multiple horizons using bar close-to-close.
    Returns rows:
      signal_id, pattern_id, dt, direction, horizon, exit_dt, entry_px, exit_px, pnl_ticks
    Assumes bars['dt'] is datetime-like and bars has 'close'.
    """
    if signals is None or not isinstance(signals, pd.DataFrame) or signals.empty:
        return pd.DataFrame(columns=[
            "signal_id", "pattern_id", "dt", "direction", "horizon",
            "exit_dt", "entry_px", "exit_px", "pnl_ticks",
        ])

    if bars is None or not isinstance(bars, pd.DataFrame) or bars.empty:
        return pd.DataFrame(columns=[
            "signal_id", "pattern_id", "dt", "direction", "horizon",
            "exit_dt", "entry_px", "exit_px", "pnl_ticks",
        ])

    b = bars.copy()
    if "dt" not in b.columns:
        raise ValueError("bars missing dt")
    if "close" not in b.columns:
        raise ValueError("bars missing close")

    b["dt"] = pd.to_datetime(b["dt"], errors="coerce")
    b = b.dropna(subset=["dt"]).sort_values("dt").reset_index(drop=True)
    b["_bar_i"] = range(len(b))

    # map dt -> bar index via merge_asof (nearest previous bar)
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

    rows = []
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

            rows.append({
                "signal_id": signal_id,
                "pattern_id": pattern_id,
                "dt": dt,
                "direction": direction,
                "horizon": int(h),
                "exit_dt": exit_dt,
                "entry_px": entry_px,
                "exit_px": exit_px,
                "pnl_ticks": float(pnl_ticks),
            })

    return pd.DataFrame(rows)


def _pattern_stats_from_outcomes(outcomes: pd.DataFrame) -> pd.DataFrame:
    """
    Aggregate outcomes to pattern-level stats.
    Returns rows per (pattern_id, horizon):
      n, win_rate, avg_ticks, net_ticks, p10/p50/p90
    """
    if outcomes is None or not isinstance(outcomes, pd.DataFrame) or outcomes.empty:
        return pd.DataFrame(columns=[
            "pattern_id", "horizon", "n_signals",
            "win_rate", "avg_ticks", "net_ticks",
            "p10", "p50", "p90",
        ])

    df = outcomes.copy()
    if "pnl_ticks" not in df.columns:
        return pd.DataFrame(columns=[
            "pattern_id", "horizon", "n_signals",
            "win_rate", "avg_ticks", "net_ticks",
            "p10", "p50", "p90",
        ])

    def q(series: pd.Series, quant: float) -> float:
        try:
            return float(series.quantile(quant))
        except Exception:
            return float("nan")

    g = df.groupby(["pattern_id", "horizon"], dropna=False)

    rows = []
    for (pid, h), sub in g:
        pnl = pd.to_numeric(sub["pnl_ticks"], errors="coerce").dropna()
        if len(pnl) == 0:
            continue
        n = int(len(pnl))
        wins = int((pnl > 0).sum())
        rows.append({
            "pattern_id": pid,
            "horizon": int(h),
            "n_signals": n,
            "win_rate": float(wins / n) if n else float("nan"),
            "avg_ticks": float(pnl.mean()),
            "net_ticks": float(pnl.sum()),
            "p10": q(pnl, 0.10),
            "p50": q(pnl, 0.50),
            "p90": q(pnl, 0.90),
        })

    return pd.DataFrame(rows)

def _ensure_signal_cols(df: pd.DataFrame) -> pd.DataFrame:
    """
    Ensure signals dataframe has a stable schema even when empty/partial.
    This keeps downstream discovery/HTML sections simple and predictable.
    """
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

    # Normalize dt to datetime if present
    try:
        out["dt"] = pd.to_datetime(out["dt"], errors="coerce")
    except Exception:
        pass

    # Normalize direction to int where possible
    try:
        out["direction"] = pd.to_numeric(out["direction"], errors="coerce").fillna(0).astype(int)
    except Exception:
        pass

    # Make ordering stable
    ordered = list(required_defaults.keys()) + [c for c in out.columns if c not in required_defaults]
    return out[ordered]

def _tod_bucket(dt: Any) -> str:
    """
    Simple time-of-day bucket (local time assumed already tz-aware in bars).
    Returns HH:MM rounded to 15-minute buckets.
    """
    # accept pandas Timestamp
    if hasattr(dt, "to_pydatetime"):
        dt = dt.to_pydatetime()
    if not isinstance(dt, datetime):
        try:
            dt = datetime.fromisoformat(str(dt))
        except Exception:
            return "UNK"

    minute = (dt.minute // 15) * 15
    return f"{dt.hour:02d}:{minute:02d}"

def json_canonical(x: Any) -> str:
    """
    Canonical JSON encoding for parameter snapshots.
    Stable ordering so pattern_id hashing / params_json comparisons are deterministic.
    """
    try:
        return json.dumps(x, sort_keys=True, separators=(",", ":"), default=str)
    except Exception:
        # very defensive fallback
        return json.dumps(str(x), sort_keys=True, separators=(",", ":"))

def run_pattern_sweep(
    *,
    pkg: Any,
    market: Any,
    options: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Returns dict of DataFrames (not written here) per redesign.
    Compute-only (no disk IO). Orchestrator writes parquet + attaches refs.

    options: cfg.raw["pattern_engine"]
    """
    if not options.get("enabled", False):
        return {
            "patterns_df": pd.DataFrame(),
            "signals_df": pd.DataFrame(),
            "outcomes_df": pd.DataFrame(),
            "pattern_stats_df": pd.DataFrame(),
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
            "diagnostics": {"ok": False, "reason": "missing instrument/contract/horizons"},
        }

    # Get bars from shared market store (no disk IO)
    bars = market.get_bars(
        instrument_root=instrument,
        contract=contract,
        timeframe=timeframe,
        start=(options.get("market_discovery", {}) or {}).get("start", None),
        end=(options.get("market_discovery", {}) or {}).get("end", None),
        source=(options.get("market_discovery", {}) or {}).get("bars_source", options.get("bars_source", "auto")),
    )
    if len(bars) == 0:
        return {
            "patterns_df": pd.DataFrame(),
            "signals_df": pd.DataFrame(),
            "outcomes_df": pd.DataFrame(),
            "pattern_stats_df": pd.DataFrame(),
            "diagnostics": {"ok": False, "reason": "no bars found"},
        }

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
            "diagnostics": {"ok": False, "reason": "bars missing dt column"},
        }

    bars = bars.copy()
    bars["dt"] = pd.to_datetime(bars["dt"])

    if "day_id" not in bars.columns:
        bars["day_id"] = bars["dt"].dt.date
    if "session_id" not in bars.columns:
        bars["session_id"] = bars["day_id"].astype(str)

    # Template registry (local import to avoid NameError if module-level imports drift)
    try:
        from .templates.builtins import default_template_registry  # type: ignore
    except Exception as e:
        return {
            "patterns_df": pd.DataFrame(),
            "signals_df": pd.DataFrame(),
            "outcomes_df": pd.DataFrame(),
            "pattern_stats_df": pd.DataFrame(),
            "diagnostics": {
                "ok": False,
                "reason": f"template_registry_import_failed: {type(e).__name__}: {e}",
            },
        }

    registry = default_template_registry()
    sweep = options.get("sweep", {}) or {}
    patterns_cfg = sweep.get("patterns", []) or []

    # ✅ CRITICAL: if no sweep patterns are configured, do NOT pretend this is "ok".
    if not patterns_cfg:
        return {
            "patterns_df": pd.DataFrame(),
            "signals_df": pd.DataFrame(),
            "outcomes_df": pd.DataFrame(),
            "pattern_stats_df": pd.DataFrame(),
            "diagnostics": {
                "ok": False,
                "reason": "no sweep patterns configured (pattern_engine.sweep.patterns is empty)",
            },
        }

    patterns_rows: List[Dict[str, Any]] = []
    signals_rows: List[Dict[str, Any]] = []

    import itertools

    for p in patterns_cfg:
        family = str(p.get("family", "")).strip()
        structure = str(p.get("structure", "")).strip()
        params_grid = p.get("params", {}) or {}
        direction_mode = str(p.get("direction_mode", "both")).strip()

        if not family or not structure:
            continue

        tmpl = registry.get(family, structure)

        keys = list(params_grid.keys())
        values = []
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
                mask = tmpl.detect_fn(bars=bars, direction=d, **params)
                if mask is None or len(mask) != len(bars):
                    continue
                fired = bars.loc[mask.astype(bool)].copy()
                if len(fired) == 0:
                    continue

                for r in fired.itertuples(index=False):
                    dt = pd.to_datetime(getattr(r, "dt"))
                    entry_px = float(getattr(r, "close")) if hasattr(r, "close") else np.nan
                    sid = _make_signal_id(pattern_id=pat_id, dt=dt, direction=d, instrument=instrument, contract=contract)

                    day_id = dt.date()
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

    patterns_df = pd.DataFrame(patterns_rows).drop_duplicates(subset=["pattern_id"]) if patterns_rows else pd.DataFrame()
    signals_df = pd.DataFrame(signals_rows) if signals_rows else pd.DataFrame()
    signals_df = _ensure_signal_cols(signals_df)

    outcomes_df = _compute_fixed_horizon_outcomes(
        bars=bars,
        signals=signals_df,
        horizons=horizons,
        tick_size=tick_size,
    )
    pattern_stats_df = _pattern_stats_from_outcomes(outcomes_df)

    return {
        "patterns_df": patterns_df,
        "signals_df": signals_df,
        "outcomes_df": outcomes_df,
        "pattern_stats_df": pattern_stats_df,
        "diagnostics": {"ok": True, "reason": "ok"},
    }