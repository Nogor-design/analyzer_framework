# ta_foundation/analysis/pattern_engine/engine.py
from __future__ import annotations

import json
import os
import hashlib
from dataclasses import asdict
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from ta_foundation.analysis.pattern_engine.model import PatternTemplate, PatternInstance


_ENGINE_VERSION = "pe_v1"


def _stable_json(d: Dict[str, Any]) -> str:
    # stable order, compact, deterministic
    return json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
def _json_safe_options(options: Dict[str, Any]) -> Dict[str, Any]:
    """
    Make a best-effort JSON-serializable snapshot of options.
    Avoid embedding large/non-serializable objects (DataFrames, registries, callables, etc.).
    """
    out: Dict[str, Any] = {}
    for k, v in (options or {}).items():
        # Explicitly drop known heavy/non-serializable keys
        if k in ("bars_df_override", "registry", "market_ctx", "ticks_df_override"):
            out[k] = f"<omitted:{k}>"
            continue

        # JSON primitives
        if v is None or isinstance(v, (str, int, float, bool)):
            out[k] = v
            continue

        # Lists/tuples: recurse lightly
        if isinstance(v, (list, tuple)):
            safe_list = []
            for item in v:
                if item is None or isinstance(item, (str, int, float, bool)):
                    safe_list.append(item)
                elif isinstance(item, dict):
                    safe_list.append(_json_safe_options(item))
                else:
                    safe_list.append(f"<{type(item).__name__}>")
            out[k] = safe_list
            continue

        # Dict: recurse
        if isinstance(v, dict):
            out[k] = _json_safe_options(v)
            continue

        # pandas objects or anything else
        out[k] = f"<{type(v).__name__}>"

    return out

def _hash_str(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


def _canonical_pattern_id(family: str, structure: str, params_json: str, version: str) -> str:
    # Example: ORB.orb_break_retest.v1.ab12cd34ef...
    core = f"{family}::{structure}::{params_json}::{version}"
    return f"{family}.{structure}.{version}.{_hash_str(core)}"


def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


class TemplateRegistry:
    """
    Minimal registry. You can wire this into your existing registry pattern later.
    """
    def __init__(self) -> None:
        self._templates: Dict[str, PatternTemplate] = {}

    def register(self, t: PatternTemplate) -> str:
        key = f"{t.family}::{t.structure}"
        if key in self._templates:
            raise ValueError(f"Duplicate template key: {key}")
        if t.detect_fn is None:
            raise ValueError(f"Template {key} missing detect_fn")
        self._templates[key] = t
        return key

    def items(self):
        return self._templates.items()

    def get(self, key: str) -> PatternTemplate:
        if key not in self._templates:
            avail = ", ".join(sorted(self._templates.keys()))
            raise KeyError(f"{key} (available templates: {avail})")
        return self._templates[key]


def default_template_registry() -> TemplateRegistry:
    r = TemplateRegistry()
    # Register built-in templates
    from ta_foundation.analysis.pattern_engine.templates.builtins import register_builtin_templates
    register_builtin_templates(r)
    return r


def _build_instances_from_sweep_options(
    *,
    registry: TemplateRegistry,
    sweep_options: Dict[str, Any],
) -> List[PatternInstance]:
    """
    sweep_options schema (recommended):
      patterns:
        - family: "ORB"
          structure: "orb_break_retest"
          params:
            orb_minutes: [5, 10]
            retest_bars: [1, 2]
            min_range_atr: [0.5, 1.0]
    """
    patterns = sweep_options.get("patterns") or []
    out: List[PatternInstance] = []

    for p in patterns:
        family = str(p["family"])
        structure = str(p["structure"])
        key = f"{family}::{structure}"
        tmpl = registry.get(key)

        params_grid: Dict[str, List[Any]] = p.get("params") or {}
        # expand grid deterministically (sorted keys)
        keys = sorted(params_grid.keys())
        values = [list(params_grid[k]) for k in keys]
        if not keys:
            params_list = [({}, _stable_json({}))]
        else:
            params_list = []
            for combo in _cartesian(values):
                params = {k: combo[i] for i, k in enumerate(keys)}
                pj = _stable_json(params)
                params_list.append((params, pj))

        for params, params_json in params_list:
            pid = _canonical_pattern_id(family, structure, params_json, _ENGINE_VERSION)
            out.append(
                PatternInstance(
                    pattern_id=pid,
                    template_key=key,
                    family=family,
                    structure=structure,
                    direction_mode=tmpl.direction_mode,
                    requires_ticks=tmpl.requires_ticks,
                    params=params,
                    params_json=params_json,
                    version=_ENGINE_VERSION,
                )
            )
    return out


def _cartesian(lists: List[List[Any]]) -> List[Tuple[Any, ...]]:
    if not lists:
        return [tuple()]
    out = [tuple()]
    for L in lists:
        out = [prev + (x,) for prev in out for x in L]
    return out


def _validate_bars_df(bars_df: pd.DataFrame) -> None:
    required = {"dt", "open", "high", "low", "close"}
    missing = required - set(bars_df.columns)
    if missing:
        raise ValueError(f"bars_df missing columns: {sorted(missing)}")
    if not pd.api.types.is_datetime64_any_dtype(bars_df["dt"]):
        raise ValueError("bars_df['dt'] must be datetime64[ns, tz]")


def _compute_fixed_horizon_outcomes(
    signals_df: pd.DataFrame,
    bars_df: pd.DataFrame,
    *,
    horizons: List[int],
    tick_size: float,
) -> pd.DataFrame:
    """
    Vectorized-ish outcome computation based on bar closes/high/low.
    Assumes entry_ref_price exists and direction in {+1, -1}.
    """
    if signals_df.empty:
        return pd.DataFrame(columns=[
            "signal_id","pattern_id","dt","horizon","ret_ticks","mfe_ticks","mae_ticks","exit_ref_price"
        ])

    bars = bars_df.sort_values("dt").reset_index(drop=True)
    # Map dt -> bar index
    dt_to_idx = pd.Series(index=bars["dt"].values, data=np.arange(len(bars), dtype=int))
    # For speed, create a dict-like lookup using pandas Index
    bars_dt_index = pd.Index(bars["dt"].values)

    s = signals_df.copy()
    # locate bar index for each signal dt (exact match required)
    locs = bars_dt_index.get_indexer(s["dt"].values)
    if (locs < 0).any():
        # drop unmatched signals rather than crashing (but flag it)
        s["bar_idx"] = locs
        s = s[s["bar_idx"] >= 0].copy()
    else:
        s["bar_idx"] = locs

    out_rows = []
    highs = bars["high"].to_numpy(dtype=float)
    lows = bars["low"].to_numpy(dtype=float)
    closes = bars["close"].to_numpy(dtype=float)

    for H in horizons:
        exit_idx = s["bar_idx"].to_numpy(dtype=int) + int(H)
        valid = exit_idx < len(bars)
        if not np.any(valid):
            continue

        sH = s.loc[valid].copy()
        exit_i = exit_idx[valid]

        entry = sH["entry_ref_price"].to_numpy(dtype=float)
        direction = sH["direction"].to_numpy(dtype=int)

        exit_price = closes[exit_i]

        # MFE/MAE over the window (bar_idx..bar_idx+H inclusive).
        # For simplicity in skeleton, use loop per row (optimize later).
        mfe_ticks = np.empty(len(sH), dtype=float)
        mae_ticks = np.empty(len(sH), dtype=float)
        ret_ticks = ((exit_price - entry) * direction) / tick_size

        for j, (bi, ei, d, eprice) in enumerate(zip(sH["bar_idx"].to_numpy(int), exit_i, direction, entry)):
            hi = highs[bi:ei+1]
            lo = lows[bi:ei+1]
            if d > 0:
                mfe = (np.max(hi) - eprice) / tick_size
                mae = (np.min(lo) - eprice) / tick_size
            else:
                mfe = (eprice - np.min(lo)) / tick_size
                mae = (eprice - np.max(hi)) / tick_size
            mfe_ticks[j] = mfe
            mae_ticks[j] = mae

        out_rows.append(pd.DataFrame({
            "signal_id": sH["signal_id"].values,
            "pattern_id": sH["pattern_id"].values,
            "dt": sH["dt"].values,
            "horizon": int(H),
            "ret_ticks": ret_ticks,
            "mfe_ticks": mfe_ticks,
            "mae_ticks": mae_ticks,
            "exit_ref_price": exit_price,
        }))

    if not out_rows:
        return pd.DataFrame(columns=[
            "signal_id","pattern_id","dt","horizon","ret_ticks","mfe_ticks","mae_ticks","exit_ref_price"
        ])
    return pd.concat(out_rows, ignore_index=True)


def _compute_pattern_stats(outcomes_df: pd.DataFrame) -> pd.DataFrame:
    if outcomes_df.empty:
        return pd.DataFrame(columns=[
            "pattern_id","horizon","n_signals","net_ticks","avg_ticks","win_rate","p10","p50","p90",
            "mfe_p50","mae_p50","rank_score_raw","quality_flags"
        ])
    g = outcomes_df.groupby(["pattern_id","horizon"], sort=False)

    def q(x, p): return float(np.nanpercentile(x, p)) if len(x) else np.nan

    rows = []
    for (pid, H), df in g:
        r = df["ret_ticks"].to_numpy(float)
        mfe = df["mfe_ticks"].to_numpy(float)
        mae = df["mae_ticks"].to_numpy(float)
        n = int(np.isfinite(r).sum())
        if n <= 0:
            continue
        net = float(np.nansum(r))
        avg = float(np.nanmean(r))
        win = float(np.nanmean(r > 0))
        row = {
            "pattern_id": pid,
            "horizon": int(H),
            "n_signals": n,
            "net_ticks": net,
            "avg_ticks": avg,
            "win_rate": win,
            "p10": q(r, 10),
            "p50": q(r, 50),
            "p90": q(r, 90),
            "mfe_p50": q(mfe, 50),
            "mae_p50": q(mae, 50),
            # placeholder: raw score is avg * sqrt(n) (cheap signal-to-noise proxy)
            "rank_score_raw": float(avg * np.sqrt(max(n, 1))),
            "quality_flags": "",
        }
        rows.append(row)
    return pd.DataFrame(rows)


def run_pattern_sweep(
    *,
    pkg: Any,
    market: Dict[str, Any],
    options: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Returns dict suitable to attach to pkg.metadata["derived"]["pattern_engine"].

    Required options keys (recommended):
      output_dir: str
      instrument: str
      contract: str
      tick_size: float
      horizons: [int,...]
      sweep: {patterns:[...]}
      registry: optional TemplateRegistry (if not provided, default empty)
    """
    output_dir = str(options.get("output_dir") or "").strip()
    if not output_dir:
        raise ValueError("pattern_engine options missing output_dir")
    _ensure_dir(output_dir)

    instrument = str(options.get("instrument") or "")
    contract = str(options.get("contract") or "")
    tick_size = float(options.get("tick_size") or 0.0)
    horizons = list(options.get("horizons") or [])
    if tick_size <= 0:
        raise ValueError("tick_size must be > 0")
    if not horizons:
        raise ValueError("horizons must be non-empty list[int]")

    # ---- Acquire bars_df from pkg/market (project-specific)
    # This is intentionally abstract: wire it to your existing AnalysisPackage outputs.
    bars_df = options.get("bars_df_override")
    if bars_df is None:
        bars_df = market.get("bars_1m")  # expected convention; change in your integration layer
    if bars_df is None:
        raise ValueError("No bars_df provided (expected market['bars_1m'] or bars_df_override)")
    bars_df = bars_df.copy()
    _validate_bars_df(bars_df)

    ticks_df = market.get("ticks")  # optional

    registry = options.get("registry") or default_template_registry()

    # ---- Build instances
    instances = _build_instances_from_sweep_options(
        registry=registry,
        sweep_options=options.get("sweep") or {},
    )

    patterns_df = pd.DataFrame([{
        "pattern_id": i.pattern_id,
        "family": i.family,
        "structure": i.structure,
        "direction_mode": i.direction_mode,
        "params_json": i.params_json,
        "requires_ticks": bool(i.requires_ticks),
        "version": i.version,
    } for i in instances])

    # ---- Detect signals per instance (template-specific)
    signal_rows: List[pd.DataFrame] = []
    for inst in instances:
        tmpl = registry.get(inst.template_key)
        if tmpl.requires_ticks and ticks_df is None:
            continue  # cannot run; optionally add flagging
        sig_df = tmpl.detect_fn(
            bars_df=bars_df,
            ticks_df=ticks_df,
            params=inst.params,
            market_ctx=market,
            options=options,
        )
        if sig_df is None or len(sig_df) == 0:
            continue
        sig_df = sig_df.copy()
        # enforce minimal columns
        for col in ("dt", "direction", "entry_ref_price"):
            if col not in sig_df.columns:
                raise ValueError(f"detect_fn for {inst.template_key} missing column: {col}")
        sig_df["pattern_id"] = inst.pattern_id
        sig_df["instrument"] = instrument
        sig_df["contract"] = contract

        # build deterministic signal_id
        # include direction and entry price to avoid collisions if multiple triggers same dt
        sig_df["signal_id"] = [
            f"{inst.pattern_id}::{pd.Timestamp(dt).isoformat()}::{int(d)}::{float(p):.4f}"
            for dt, d, p in zip(sig_df["dt"].values, sig_df["direction"].values, sig_df["entry_ref_price"].values)
        ]

        # optional enrichers (session/regime buckets) may already exist; ensure columns exist
        if "session_id" not in sig_df.columns:
            sig_df["session_id"] = ""
        if "day_id" not in sig_df.columns:
            sig_df["day_id"] = pd.to_datetime(sig_df["dt"]).dt.date
        if "tod_bucket" not in sig_df.columns:
            sig_df["tod_bucket"] = ""
        if "regime" not in sig_df.columns:
            sig_df["regime"] = ""
        if "spread_pct" not in sig_df.columns:
            sig_df["spread_pct"] = np.nan
        if "liq_bucket" not in sig_df.columns:
            sig_df["liq_bucket"] = ""
        if "features_json" not in sig_df.columns:
            sig_df["features_json"] = _stable_json({})

        signal_rows.append(sig_df[[
            "signal_id","pattern_id","dt","instrument","contract","direction","entry_ref_price",
            "session_id","day_id","tod_bucket","regime","spread_pct","liq_bucket","features_json"
        ]])

    signals_df = pd.concat(signal_rows, ignore_index=True) if signal_rows else pd.DataFrame(columns=[
        "signal_id","pattern_id","dt","instrument","contract","direction","entry_ref_price",
        "session_id","day_id","tod_bucket","regime","spread_pct","liq_bucket","features_json"
    ])

    outcomes_df = _compute_fixed_horizon_outcomes(
        signals_df=signals_df,
        bars_df=bars_df,
        horizons=horizons,
        tick_size=tick_size,
    )

    pattern_stats_df = _compute_pattern_stats(outcomes_df)

    # ---- Persist artifacts
    paths = {}
    for name, df in [
        ("patterns", patterns_df),
        ("signals", signals_df),
        ("outcomes", outcomes_df),
        ("pattern_stats", pattern_stats_df),
    ]:
        p = os.path.join(output_dir, f"{name}.parquet")
        df.to_parquet(p, index=False)
        paths[name] = p

    # ---- Diagnostics
    diag = {
        "validation": {"ok": True, "issues": []},
        "counts": {
            "n_patterns": int(len(patterns_df)),
            "n_signals": int(len(signals_df)),
            "n_outcomes": int(len(outcomes_df)),
            "n_clusters": 0,
        }
    }

    payload = {
        "version": _ENGINE_VERSION,
        "engine": {
            "tick_size": tick_size,
            "instrument": instrument,
            "contract": contract,
            "bar_tf": str(options.get("bar_tf") or "1m"),
            "tz": str(options.get("tz") or "America/Denver"),
        },
        "options_snapshot": _json_safe_options(options), # safe-serialize snapshot
        "artifacts": {
            "patterns": {"type": "parquet", "path": paths["patterns"]},
            "signals": {"type": "parquet", "path": paths["signals"]},
            "outcomes": {"type": "parquet", "path": paths["outcomes"]},
            "pattern_stats": {"type": "parquet", "path": paths["pattern_stats"]},
        },
        "diagnostics": diag,
    }
    return payload