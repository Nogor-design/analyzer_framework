from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd


@dataclass(frozen=True)
class RecommendationConfig:
    min_trades_run: int = 100
    min_trades_bucket: int = 25

    # Decision rules (simple, explainable)
    require_positive_if_better_by: float = 0.0  # if one side is better by this net pnl margin, recommend that side
    max_hour_buckets: int = 4
    max_atr_quartiles: int = 3

    # Which gates to emit
    include_atr: bool = True
    include_hour: bool = True
    include_htf_slope_sign: bool = True
    include_vwap_side: bool = True


def _safe_num(x: Any) -> float:
    try:
        if x is None:
            return float("nan")
        return float(x)
    except Exception:
        return float("nan")


def _bucket_stats(df: pd.DataFrame, group_col: str) -> pd.DataFrame:
    x = df.copy()
    x["pnl"] = pd.to_numeric(x.get("pnl"), errors="coerce")
    g = x.groupby(group_col, dropna=True)
    out = pd.DataFrame({
        "bucket": g.size().index.astype(str),
        "trades": g.size().values,
        "net_pnl": g["pnl"].sum(min_count=1).values,
        "avg_pnl": g["pnl"].mean().values,
        "win_rate": g["pnl"].apply(lambda s: float((s > 0).mean()) if len(s) else 0.0).values,
    })
    return out


def _pick_positive_buckets(
    stats: pd.DataFrame,
    *,
    min_trades_bucket: int,
    max_n: int,
) -> List[str]:
    if stats is None or stats.empty:
        return []
    s = stats.copy()
    s["trades"] = pd.to_numeric(s["trades"], errors="coerce").fillna(0).astype(int)
    s["net_pnl"] = pd.to_numeric(s["net_pnl"], errors="coerce")
    s = s[s["trades"] >= min_trades_bucket]
    s = s[s["net_pnl"] > 0]
    if s.empty:
        return []
    s = s.sort_values("net_pnl", ascending=False).head(max_n)
    return [str(v) for v in s["bucket"].tolist()]


def _choose_side(
    stats: pd.DataFrame,
    *,
    positive_label: str,
    negative_label: str,
    require_better_by: float,
    min_trades_bucket: int,
) -> Optional[str]:
    """
    Return one of:
      - positive_label
      - negative_label
      - None (no strong recommendation)
    """
    if stats is None or stats.empty:
        return None

    s = stats.copy()
    s["trades"] = pd.to_numeric(s["trades"], errors="coerce").fillna(0).astype(int)
    s = s[s["trades"] >= min_trades_bucket]
    if s.empty:
        return None

    m = {str(r["bucket"]): r for _, r in s.iterrows()}
    pos = m.get(str(positive_label))
    neg = m.get(str(negative_label))
    if pos is None or neg is None:
        one = pos or neg
        if one and _safe_num(one.get("net_pnl")) > 0:
            return str(one["bucket"])
        return None

    pos_net = _safe_num(pos.get("net_pnl"))
    neg_net = _safe_num(neg.get("net_pnl"))

    if pd.isna(pos_net) or pd.isna(neg_net):
        return None

    if (pos_net - neg_net) > require_better_by:
        return positive_label
    if (neg_net - pos_net) > require_better_by:
        return negative_label

    return None


def _parse_quartile_label(label: str) -> Optional[int]:
    """
    Accepts:
      "Q1 (low)" -> 1
      "Q2" -> 2
      "Q3" -> 3
      "Q4 (high)" -> 4
    """
    if not label:
        return None
    s = str(label).strip().upper()
    if s.startswith("Q1"):
        return 1
    if s.startswith("Q2"):
        return 2
    if s.startswith("Q3"):
        return 3
    if s.startswith("Q4"):
        return 4
    return None


def _atr_quantile_edges(atr: pd.Series) -> Optional[Dict[str, float]]:
    """
    Returns dict of ATR quantile edges:
      q0, q25, q50, q75, q100

    Uses quantile on the *run's trade-level ATR values* (already aligned to entries).
    """
    s = pd.to_numeric(atr, errors="coerce").dropna()
    if s.empty:
        return None

    qs = s.quantile([0.0, 0.25, 0.50, 0.75, 1.0], interpolation="linear")
    # Sometimes all values identical -> edges all same. Still useful.
    return {
        "q0": float(qs.loc[0.0]),
        "q25": float(qs.loc[0.25]),
        "q50": float(qs.loc[0.50]),
        "q75": float(qs.loc[0.75]),
        "q100": float(qs.loc[1.0]),
    }


def _quartile_to_bounds(q: int, edges: Dict[str, float]) -> Tuple[float, float]:
    """
    Given q in {1,2,3,4} return inclusive-ish bounds [min,max] for that quartile.
    """
    if q == 1:
        return edges["q0"], edges["q25"]
    if q == 2:
        return edges["q25"], edges["q50"]
    if q == 3:
        return edges["q50"], edges["q75"]
    if q == 4:
        return edges["q75"], edges["q100"]
    raise ValueError(q)


def _allowed_quartiles_to_atr_min_max(
    allowed_labels: List[str],
    edges: Dict[str, float],
) -> Optional[Dict[str, float]]:
    """
    Converts allowed quartile labels into numeric atr_min/atr_max bounds.

    Policy:
    - Find min quartile index and max quartile index among allowed.
    - Return atr_min = lower bound of min quartile
             atr_max = upper bound of max quartile
    This yields a single contiguous band (simple to implement in NT).
    """
    qs = []
    for lab in allowed_labels or []:
        q = _parse_quartile_label(lab)
        if q:
            qs.append(q)
    if not qs:
        return None

    q_lo, q_hi = min(qs), max(qs)
    lo_min, _ = _quartile_to_bounds(q_lo, edges)
    _, hi_max = _quartile_to_bounds(q_hi, edges)

    return {"atr_min": float(lo_min), "atr_max": float(hi_max), "quartile_min": q_lo, "quartile_max": q_hi}


def build_recommendations(
    feats: pd.DataFrame,
    *,
    cfg: RecommendationConfig,
    feature_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Build per-run_id recommendations from trade feature rows.

    Expects feats columns (best-effort):
      - run_id
      - pnl
      - atr_q (e.g., "Q1 (low)"..."Q4 (high)")
      - atr (numeric; used to compute quantile edges and numeric thresholds)
      - hour_bucket (e.g., "7-9")
      - htf_slope_sign (e.g., "<=0 (down/flat)", ">0 (up)")
      - vwap_side ("Below VWAP", "Above VWAP")
    """
    if feats is None or feats.empty or "run_id" not in feats.columns:
        return {"version": 2, "recommendations": {}, "meta": {"error": "no_features"}}

    f = feats.copy()
    f["run_id"] = f["run_id"].astype(str)
    f["pnl"] = pd.to_numeric(f.get("pnl"), errors="coerce")

    recs: Dict[str, Any] = {}
    for run_id, df_run in f.groupby("run_id"):
        if df_run.empty:
            continue

        trades = int(len(df_run))
        if trades < cfg.min_trades_run:
            continue

        net_pnl = float(df_run["pnl"].sum(min_count=1)) if df_run["pnl"].notna().any() else None
        avg_pnl = float(df_run["pnl"].mean()) if df_run["pnl"].notna().any() else None
        win_rate = float((df_run["pnl"] > 0).mean()) if df_run["pnl"].notna().any() else None

        r: Dict[str, Any] = {
            "summary": {
                "trades": trades,
                "net_pnl": net_pnl,
                "avg_pnl": avg_pnl,
                "win_rate": win_rate,
            },
            "filters": {},
            "notes": [],
        }

        # ---- ATR quartiles + numeric conversion ----
        if cfg.include_atr and ("atr_q" in df_run.columns):
            st = _bucket_stats(df_run.dropna(subset=["atr_q"]), "atr_q")
            allowed = _pick_positive_buckets(st, min_trades_bucket=cfg.min_trades_bucket, max_n=cfg.max_atr_quartiles)
            r["filters"]["atr_quartiles_allowed"] = allowed
            r["filters"]["atr_quartiles_stats"] = st.to_dict(orient="records")

            # Quantile edges from raw atr values (trade-level)
            if "atr" in df_run.columns:
                edges = _atr_quantile_edges(df_run["atr"])
                if edges:
                    r["filters"]["atr_quantile_edges"] = edges
                    band = _allowed_quartiles_to_atr_min_max(allowed, edges)
                    if band:
                        r["filters"]["atr_thresholds"] = band
                    else:
                        r["filters"]["atr_thresholds"] = None

        # ---- Hour buckets ----
        if cfg.include_hour and "hour_bucket" in df_run.columns:
            st = _bucket_stats(df_run.dropna(subset=["hour_bucket"]), "hour_bucket")
            allowed = _pick_positive_buckets(st, min_trades_bucket=cfg.min_trades_bucket, max_n=cfg.max_hour_buckets)
            r["filters"]["hour_buckets_allowed"] = allowed
            r["filters"]["hour_buckets_stats"] = st.to_dict(orient="records")

        # ---- HTF slope sign ----
        if cfg.include_htf_slope_sign and "htf_slope_sign" in df_run.columns:
            st = _bucket_stats(df_run.dropna(subset=["htf_slope_sign"]), "htf_slope_sign")
            choice = _choose_side(
                st,
                positive_label=">0 (up)",
                negative_label="<=0 (down/flat)",
                require_better_by=cfg.require_positive_if_better_by,
                min_trades_bucket=cfg.min_trades_bucket,
            )
            r["filters"]["htf_slope_sign_recommendation"] = choice
            r["filters"]["htf_slope_sign_stats"] = st.to_dict(orient="records")

        # ---- VWAP side ----
        if cfg.include_vwap_side and "vwap_side" in df_run.columns:
            st = _bucket_stats(df_run.dropna(subset=["vwap_side"]), "vwap_side")
            choice = _choose_side(
                st,
                positive_label="Above VWAP",
                negative_label="Below VWAP",
                require_better_by=cfg.require_positive_if_better_by,
                min_trades_bucket=cfg.min_trades_bucket,
            )
            r["filters"]["vwap_side_recommendation"] = choice
            r["filters"]["vwap_side_stats"] = st.to_dict(orient="records")

        # Notes
        if cfg.include_atr and r["filters"].get("atr_quartiles_allowed") == []:
            r["notes"].append("No ATR quartiles had positive net PnL above min bucket trades; consider disabling ATR gate or lowering min_trades_bucket.")
        if cfg.include_hour and r["filters"].get("hour_buckets_allowed") == []:
            r["notes"].append("No hour buckets had positive net PnL above min bucket trades; consider disabling hour gate or lowering min_trades_bucket.")
        if cfg.include_atr and r["filters"].get("atr_thresholds") is None and r["filters"].get("atr_quartiles_allowed"):
            r["notes"].append("ATR quartiles allowed but numeric thresholds could not be computed (missing or invalid atr values).")

        recs[run_id] = r

    return {
        "version": 2,
        "meta": {
            "feature_config": feature_config or {},
            "recommendation_config": {
                "min_trades_run": cfg.min_trades_run,
                "min_trades_bucket": cfg.min_trades_bucket,
                "require_positive_if_better_by": cfg.require_positive_if_better_by,
                "max_hour_buckets": cfg.max_hour_buckets,
                "max_atr_quartiles": cfg.max_atr_quartiles,
                "include_atr": cfg.include_atr,
                "include_hour": cfg.include_hour,
                "include_htf_slope_sign": cfg.include_htf_slope_sign,
                "include_vwap_side": cfg.include_vwap_side,
            },
        },
        "recommendations": recs,
    }
