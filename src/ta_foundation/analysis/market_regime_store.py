from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import math
import numpy as np
import pandas as pd


@dataclass(frozen=True)
class MarketRegimeConfig:
    trend_flat_threshold: float = 0.0
    vwap_near_threshold_atr: float = 0.25


# -----------------------------
# core helpers
# -----------------------------

def _safe_num_series(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


def _quantile_or_none(s: pd.Series, q: float) -> float | None:
    z = _safe_num_series(s).dropna()
    if z.empty:
        return None
    return float(z.quantile(q))


def _shrink_mean(hour_value, global_value, n, prior_strength):
    if pd.isna(hour_value):
        return global_value
    if pd.isna(global_value):
        return hour_value
    w = float(n) / float(n + prior_strength) if (n is not None and n >= 0) else 0.0
    return w * float(hour_value) + (1.0 - w) * float(global_value)


def _beta_posterior_mean(k, n, global_rate, prior_strength):
    if pd.isna(global_rate):
        return np.nan
    if n is None or n <= 0:
        return float(global_rate)
    g = min(max(float(global_rate), 1e-6), 1.0 - 1e-6)
    alpha = g * float(prior_strength)
    beta = (1.0 - g) * float(prior_strength)
    return float((float(k) + alpha) / (float(n) + alpha + beta))


def _zscore_series(s: pd.Series) -> pd.Series:
    x = pd.to_numeric(s, errors="coerce")
    mu = x.mean()
    sd = x.std(ddof=0)
    if pd.isna(sd) or sd <= 0:
        return pd.Series(np.zeros(len(x)), index=x.index)
    return (x - mu) / sd


# -----------------------------
# regime classification
# -----------------------------

def _classify_trend(htf_ema_slope: pd.Series, flat_threshold: float) -> pd.Series:
    x = _safe_num_series(htf_ema_slope)
    out = pd.Series(index=x.index, dtype="object")
    out.loc[x > float(flat_threshold)] = "up"
    out.loc[x < -float(flat_threshold)] = "down"
    out.loc[x.notna() & out.isna()] = "flat"
    return out


def _classify_vwap(vwap_dist_atr: pd.Series, near_threshold: float) -> pd.Series:
    x = _safe_num_series(vwap_dist_atr)
    out = pd.Series(index=x.index, dtype="object")
    out.loc[x > float(near_threshold)] = "above"
    out.loc[x < -float(near_threshold)] = "below"
    out.loc[x.notna() & out.isna()] = "near"
    return out


def _classify_session(entry_hour: pd.Series) -> pd.Series:
    h = pd.to_numeric(entry_hour, errors="coerce")
    out = pd.Series(index=h.index, dtype="object")
    out.loc[h.between(0, 6)] = "london"
    out.loc[h == 7] = "pre_open"
    out.loc[h == 8] = "us_open"
    out.loc[h.between(9, 11)] = "us_morning"
    out.loc[h.between(12, 13)] = "us_midday"
    out.loc[h.between(14, 15)] = "us_afternoon"
    out.loc[h.between(16, 23)] = "globex_evening"
    return out


def _classify_volatility_within_run(run_df: pd.DataFrame) -> pd.Series:
    atr = _safe_num_series(run_df.get("atr"))
    out = pd.Series(index=run_df.index, dtype="object")
    valid = atr.dropna()
    if valid.empty:
        return out
    if len(valid) < 4:
        out.loc[valid.index] = "mid"
        return out
    try:
        ranked = valid.rank(method="first")
        q = pd.qcut(ranked, 4, labels=["low", "mid", "mid", "high"], duplicates="drop")
        out.loc[q.index] = q.astype(str)
        return out
    except Exception:
        q25 = float(valid.quantile(0.25))
        q75 = float(valid.quantile(0.75))
        out.loc[valid.index] = "mid"
        out.loc[valid.index[valid <= q25]] = "low"
        out.loc[valid.index[valid >= q75]] = "high"
        return out


# -----------------------------
# frame builder
# -----------------------------

def build_market_regime_frame(
    feats: pd.DataFrame,
    cfg: Optional[MarketRegimeConfig] = None,
) -> pd.DataFrame:
    cfg = cfg or MarketRegimeConfig()
    if feats is None or feats.empty:
        return pd.DataFrame()

    out = feats.copy()
    if "run_id" in out.columns:
        out["run_id"] = out["run_id"].astype(str)
    if "pnl" in out.columns:
        out["pnl"] = _safe_num_series(out["pnl"])
    if "entry_hour" in out.columns:
        out["entry_hour"] = pd.to_numeric(out["entry_hour"], errors="coerce")
    if "mae" in out.columns:
        out["mae"] = _safe_num_series(out["mae"]).abs()

    out["trend_regime"] = _classify_trend(out.get("htf_ema_slope"), cfg.trend_flat_threshold)
    out["vwap_regime"] = _classify_vwap(out.get("vwap_dist_atr"), cfg.vwap_near_threshold_atr)
    out["session_regime"] = _classify_session(out.get("entry_hour"))

    out["vol_regime"] = pd.Series(index=out.index, dtype="object")
    if "run_id" in out.columns:
        for _, idx in out.groupby("run_id", dropna=False).groups.items():
            sub = out.loc[idx]
            out.loc[idx, "vol_regime"] = _classify_volatility_within_run(sub)
    else:
        out["vol_regime"] = _classify_volatility_within_run(out)

    trend = out["trend_regime"].fillna("unknown").astype(str)
    vol = out["vol_regime"].fillna("unknown").astype(str)
    out["combo_regime"] = trend + "|" + vol
    return out


# -----------------------------
# summaries
# -----------------------------

def summarize_regime_performance(
    df: pd.DataFrame,
    regime_col: str,
    *,
    min_trades_bucket: int = 1,
    sort_by_score: bool = True,
) -> pd.DataFrame:
    if df is None or df.empty or regime_col not in df.columns:
        return pd.DataFrame()

    x = df.copy()
    x["pnl"] = _safe_num_series(x.get("pnl"))
    x = x.dropna(subset=[regime_col, "pnl"])
    if x.empty:
        return pd.DataFrame()

    baseline_avg = float(x["pnl"].mean()) if len(x) else 0.0
    g = x.groupby(regime_col, dropna=True)
    out = pd.DataFrame({
        regime_col: g.size().index.astype(str),
        "trades": g.size().values,
        "win_rate": g["pnl"].apply(lambda s: float((s > 0).mean()) if len(s) else 0.0).values,
        "net_pnl": g["pnl"].sum(min_count=1).values,
        "avg_pnl": g["pnl"].mean().values,
        "median_pnl": g["pnl"].median().values,
    })

    out = out[out["trades"] >= int(min_trades_bucket)].copy()
    if out.empty:
        return out

    out["edge_vs_baseline"] = pd.to_numeric(out["avg_pnl"], errors="coerce") - baseline_avg
    out["score"] = out.apply(
        lambda r: float(r["edge_vs_baseline"]) * math.sqrt(float(r["trades"]))
        if pd.notna(r["edge_vs_baseline"]) and pd.notna(r["trades"])
        else float("nan"),
        axis=1,
    )
    if sort_by_score:
        out = out.sort_values("score", ascending=False).reset_index(drop=True)
    else:
        out = out.reset_index(drop=True)
    return out


def summarize_entry_hour_performance(
    df: pd.DataFrame,
    *,
    min_trades_bucket: int = 1,
) -> pd.DataFrame:
    if df is None or df.empty or "entry_hour" not in df.columns:
        return pd.DataFrame()

    x = df.copy()
    x["pnl"] = _safe_num_series(x.get("pnl"))
    x["entry_hour"] = pd.to_numeric(x.get("entry_hour"), errors="coerce")
    x = x.dropna(subset=["entry_hour", "pnl"])
    if x.empty:
        return pd.DataFrame()

    x["entry_hour"] = x["entry_hour"].astype(int)
    baseline_avg = float(x["pnl"].mean())
    g = x.groupby("entry_hour")

    def _avg_loss(s):
        z = s[s < 0]
        return None if z.empty else float(z.mean())

    def _median_loss(s):
        z = s[s < 0]
        return None if z.empty else float(z.median())

    def _worst_loss(s):
        z = s[s < 0]
        return None if z.empty else float(z.min())

    out = pd.DataFrame({
        "entry_hour": g.size().index.astype(int),
        "trades": g.size().values,
        "win_rate": g["pnl"].apply(lambda s: float((s > 0).mean())).values,
        "loss_rate": g["pnl"].apply(lambda s: float((s < 0).mean())).values,
        "net_pnl": g["pnl"].sum(min_count=1).values,
        "avg_pnl": g["pnl"].mean().values,
        "median_pnl": g["pnl"].median().values,
        "avg_loss": g["pnl"].apply(_avg_loss).values,
        "median_loss": g["pnl"].apply(_median_loss).values,
        "worst_loss": g["pnl"].apply(_worst_loss).values,
    })
    out = out[out["trades"] >= int(min_trades_bucket)].copy()
    if out.empty:
        return out

    out["edge_vs_baseline"] = pd.to_numeric(out["avg_pnl"], errors="coerce") - baseline_avg
    out["score"] = out.apply(
        lambda r: float(r["edge_vs_baseline"]) * math.sqrt(float(r["trades"]))
        if pd.notna(r["edge_vs_baseline"]) and pd.notna(r["trades"])
        else float("nan"),
        axis=1,
    )
    out["hour_label"] = out["entry_hour"].map(lambda h: f"{int(h):02d}")
    return out.sort_values("entry_hour").reset_index(drop=True)


def summarize_entry_hour_drawdown_risk(
    df: pd.DataFrame,
    *,
    min_trades_bucket: int = 1,
    mae_col: str = "mae",
    assumed_stop_usd: float | None = None,
    severe_mae_frac: float = 0.50,
) -> pd.DataFrame:
    if df is None or df.empty or "entry_hour" not in df.columns or mae_col not in df.columns:
        return pd.DataFrame()

    x = df.copy()
    x["entry_hour"] = pd.to_numeric(x.get("entry_hour"), errors="coerce")
    x[mae_col] = _safe_num_series(x.get(mae_col))
    x = x.dropna(subset=["entry_hour", mae_col])
    if x.empty:
        return pd.DataFrame()

    x["entry_hour"] = x["entry_hour"].astype(int)
    x[mae_col] = x[mae_col].abs()
    g = x.groupby("entry_hour", dropna=True)

    out = pd.DataFrame({
        "entry_hour": g.size().index.astype(int),
        "trades": g.size().values,
        "mae_obs": g[mae_col].count().values,
        "mae_mean": g[mae_col].mean().values,
        "mae_median": g[mae_col].median().values,
        "mae_p75": g[mae_col].apply(lambda s: _quantile_or_none(s, 0.75)).values,
        "mae_p90": g[mae_col].apply(lambda s: _quantile_or_none(s, 0.90)).values,
        "mae_p95": g[mae_col].apply(lambda s: _quantile_or_none(s, 0.95)).values,
        "mae_max": g[mae_col].max().values,
    })
    out = out[out["trades"] >= int(min_trades_bucket)].copy()
    if out.empty:
        return out

    out["mae_coverage"] = out.apply(
        lambda r: float(r["mae_obs"]) / float(r["trades"]) if float(r["trades"]) > 0 else 0.0,
        axis=1,
    )

    severe_threshold = None
    if assumed_stop_usd is not None and assumed_stop_usd > 0:
        severe_threshold = float(assumed_stop_usd) * float(severe_mae_frac)

    if severe_threshold is not None:
        severe_rate = g[mae_col].apply(lambda s: float((s >= severe_threshold).mean()))
        out["severe_mae_rate"] = out["entry_hour"].map(severe_rate.to_dict()).astype(float)
    else:
        out["severe_mae_rate"] = pd.NA

    if assumed_stop_usd is not None and assumed_stop_usd > 0:
        stop_breach = g[mae_col].apply(lambda s: float((s >= float(assumed_stop_usd)).mean()))
        out["stop_breach_rate"] = out["entry_hour"].map(stop_breach.to_dict()).astype(float)
        out["mae_stop_multiple_p90"] = out["mae_p90"] / float(assumed_stop_usd)
        out["mae_stop_multiple_max"] = out["mae_max"] / float(assumed_stop_usd)
    else:
        out["stop_breach_rate"] = pd.NA
        out["mae_stop_multiple_p90"] = pd.NA
        out["mae_stop_multiple_max"] = pd.NA

    out["risk_score"] = out.apply(
        lambda r: float(r["mae_p90"]) * math.sqrt(float(r["trades"]))
        if pd.notna(r["mae_p90"]) and pd.notna(r["trades"])
        else float("nan"),
        axis=1,
    )
    out["hour_label"] = out["entry_hour"].map(lambda h: f"{int(h):02d}")
    return out.sort_values("entry_hour").reset_index(drop=True)


def summarize_entry_hour_risk(
    df: pd.DataFrame,
    *,
    min_trades_bucket: int = 5,
    pnl_col: str = "pnl",
    mae_col: str = "mae",
    assumed_stop_usd: float | None = None,
    prior_strength_mean: float = 30.0,
    prior_strength_rate: float = 20.0,
    tail_loss_frac: float = 0.50,
    severe_mae_frac: float = 0.50,
) -> pd.DataFrame:
    if df is None or df.empty or "entry_hour" not in df.columns:
        return pd.DataFrame()

    x = df.copy()
    x["entry_hour"] = pd.to_numeric(x.get("entry_hour"), errors="coerce")
    x[pnl_col] = pd.to_numeric(x.get(pnl_col), errors="coerce")
    x[mae_col] = pd.to_numeric(x.get(mae_col), errors="coerce").abs()
    x = x.dropna(subset=["entry_hour", pnl_col])
    if x.empty:
        return pd.DataFrame()

    x["entry_hour"] = x["entry_hour"].astype(int)
    assumed_stop_usd = float(assumed_stop_usd) if assumed_stop_usd else None
    tail_loss_usd = float(assumed_stop_usd * tail_loss_frac) if assumed_stop_usd else None
    severe_mae_usd = float(assumed_stop_usd * severe_mae_frac) if assumed_stop_usd else None

    global_avg_pnl = float(x[pnl_col].mean())
    global_avg_loss = float(x.loc[x[pnl_col] < 0, pnl_col].mean()) if (x[pnl_col] < 0).any() else 0.0
    global_mae_mean = float(x[mae_col].mean()) if mae_col in x.columns and x[mae_col].notna().any() else np.nan
    global_mae_p90 = float(x[mae_col].quantile(0.90)) if mae_col in x.columns and x[mae_col].notna().any() else np.nan
    global_stop_breach_rate = (
        float((x[mae_col] >= assumed_stop_usd).mean())
        if assumed_stop_usd is not None and x[mae_col].notna().any()
        else np.nan
    )
    global_tail_loss_rate = (
        float((x[pnl_col] <= -tail_loss_usd).mean())
        if tail_loss_usd is not None
        else np.nan
    )
    global_severe_mae_rate = (
        float((x[mae_col] >= severe_mae_usd).mean())
        if severe_mae_usd is not None and x[mae_col].notna().any()
        else np.nan
    )

    rows = []
    for hour, g in x.groupby("entry_hour"):
        n = int(len(g))
        if n < int(min_trades_bucket):
            continue
        losses = g.loc[g[pnl_col] < 0, pnl_col]
        maes = g[mae_col].dropna() if mae_col in g.columns else pd.Series(dtype=float)
        avg_pnl = float(g[pnl_col].mean())
        avg_loss = float(losses.mean()) if not losses.empty else 0.0
        worst_loss = float(g[pnl_col].min())
        mae_mean = float(maes.mean()) if not maes.empty else np.nan
        mae_p90 = float(maes.quantile(0.90)) if not maes.empty else np.nan
        mae_max = float(maes.max()) if not maes.empty else np.nan
        stop_breach_count = int((maes >= assumed_stop_usd).sum()) if assumed_stop_usd is not None and not maes.empty else 0
        tail_loss_count = int((g[pnl_col] <= -tail_loss_usd).sum()) if tail_loss_usd is not None else 0
        severe_mae_count = int((maes >= severe_mae_usd).sum()) if severe_mae_usd is not None and not maes.empty else 0

        rows.append({
            "entry_hour": int(hour),
            "hour_label": f"{int(hour):02d}",
            "trades": n,
            "avg_pnl": avg_pnl,
            "avg_loss": avg_loss,
            "worst_loss": worst_loss,
            "mae_mean": mae_mean,
            "mae_p90": mae_p90,
            "mae_max": mae_max,
            "shrunk_avg_pnl": _shrink_mean(avg_pnl, global_avg_pnl, n, prior_strength_mean),
            "shrunk_avg_loss": _shrink_mean(avg_loss, global_avg_loss, n, prior_strength_mean),
            "shrunk_mae_mean": _shrink_mean(mae_mean, global_mae_mean, n, prior_strength_mean) if not pd.isna(mae_mean) else global_mae_mean,
            "shrunk_mae_p90": _shrink_mean(mae_p90, global_mae_p90, n, prior_strength_mean) if not pd.isna(mae_p90) else global_mae_p90,
            "shrunk_stop_breach_rate": _beta_posterior_mean(stop_breach_count, len(maes), global_stop_breach_rate, prior_strength_rate)
            if assumed_stop_usd is not None and not maes.empty else np.nan,
            "shrunk_tail_loss_rate": _beta_posterior_mean(tail_loss_count, n, global_tail_loss_rate, prior_strength_rate)
            if tail_loss_usd is not None else np.nan,
            "shrunk_severe_mae_rate": _beta_posterior_mean(severe_mae_count, len(maes), global_severe_mae_rate, prior_strength_rate)
            if severe_mae_usd is not None and not maes.empty else np.nan,
        })

    out = pd.DataFrame(rows)
    if out.empty:
        return out

    out["z_stop"] = _zscore_series(out["shrunk_stop_breach_rate"].fillna(out["shrunk_stop_breach_rate"].mean()))
    out["z_mae"] = _zscore_series(out["shrunk_mae_p90"].fillna(out["shrunk_mae_p90"].mean()))
    out["z_loss"] = _zscore_series(out["shrunk_avg_loss"].abs())
    out["z_tail"] = _zscore_series(out["shrunk_tail_loss_rate"].fillna(out["shrunk_tail_loss_rate"].mean()))
    out["z_edge"] = _zscore_series(out["shrunk_avg_pnl"])
    out["danger_score"] = (
        0.35 * out["z_stop"]
        + 0.25 * out["z_mae"]
        + 0.20 * out["z_loss"]
        + 0.10 * out["z_tail"]
        - 0.10 * out["z_edge"]
    )

    def _classify(row):
        if assumed_stop_usd is not None:
            if (
                pd.notna(row["shrunk_stop_breach_rate"]) and row["shrunk_stop_breach_rate"] >= 0.15
            ) or (
                pd.notna(row["shrunk_mae_p90"]) and row["shrunk_mae_p90"] >= 0.75 * assumed_stop_usd
            ):
                return "drop"
        if row["danger_score"] >= out["danger_score"].quantile(0.75):
            return "review"
        return "keep"

    out["keep_drop_flag"] = out.apply(_classify, axis=1)
    return out.sort_values("entry_hour").reset_index(drop=True)


# -----------------------------
# session optimizer
# -----------------------------

def optimize_entry_hour_window(
    hour_risk_df: pd.DataFrame,
    *,
    min_hours: int = 1,
    max_hours: int = 24,
    max_stop_breach_rate: float | None = None,
    max_tail_loss_rate: float | None = None,
    max_mae_p90_frac_of_stop: float | None = None,
    assumed_stop_usd: float | None = None,
    allow_wrap: bool = False,
) -> pd.DataFrame:
    """
    Find the best contiguous entry-hour window using shrinkage-adjusted risk metrics.
    Objective: maximize total shrunk edge while respecting downside constraints.
    """
    if hour_risk_df is None or hour_risk_df.empty or "entry_hour" not in hour_risk_df.columns:
        return pd.DataFrame()

    x = hour_risk_df.copy()
    x["entry_hour"] = pd.to_numeric(x.get("entry_hour"), errors="coerce")
    x = x.dropna(subset=["entry_hour"]).sort_values("entry_hour").reset_index(drop=True)
    if x.empty:
        return pd.DataFrame()

    x["entry_hour"] = x["entry_hour"].astype(int)
    n = len(x)
    rows = []

    # contiguous over observed hours only; this is appropriate for already-filtered active hours
    for i in range(n):
        total_edge = 0.0
        total_trades = 0.0
        stop_num = 0.0
        stop_den = 0.0
        tail_num = 0.0
        tail_den = 0.0
        mae_weighted_sum = 0.0
        mae_weighted_den = 0.0
        worst_danger = -np.inf
        start_hour = int(x.loc[i, "entry_hour"])

        for j in range(i, min(i + max_hours, n)):
            r = x.loc[j]
            width = j - i + 1
            total_edge += float(pd.to_numeric(r.get("shrunk_avg_pnl"), errors="coerce") or 0.0) * float(r.get("trades", 0))
            total_trades += float(r.get("trades", 0))

            if pd.notna(r.get("shrunk_stop_breach_rate")):
                stop_num += float(r["shrunk_stop_breach_rate"]) * float(r.get("trades", 0))
                stop_den += float(r.get("trades", 0))
            if pd.notna(r.get("shrunk_tail_loss_rate")):
                tail_num += float(r["shrunk_tail_loss_rate"]) * float(r.get("trades", 0))
                tail_den += float(r.get("trades", 0))
            if pd.notna(r.get("shrunk_mae_p90")):
                mae_weighted_sum += float(r["shrunk_mae_p90"]) * float(r.get("trades", 0))
                mae_weighted_den += float(r.get("trades", 0))

            if pd.notna(r.get("danger_score")):
                worst_danger = max(worst_danger, float(r["danger_score"]))

            if width < int(min_hours):
                continue

            avg_edge = total_edge / total_trades if total_trades > 0 else np.nan
            agg_stop = stop_num / stop_den if stop_den > 0 else np.nan
            agg_tail = tail_num / tail_den if tail_den > 0 else np.nan
            agg_mae_p90 = mae_weighted_sum / mae_weighted_den if mae_weighted_den > 0 else np.nan
            mae_stop_frac = (
                float(agg_mae_p90) / float(assumed_stop_usd)
                if assumed_stop_usd is not None and assumed_stop_usd > 0 and pd.notna(agg_mae_p90)
                else np.nan
            )

            feasible = True
            if max_stop_breach_rate is not None and pd.notna(agg_stop):
                feasible &= float(agg_stop) <= float(max_stop_breach_rate)
            if max_tail_loss_rate is not None and pd.notna(agg_tail):
                feasible &= float(agg_tail) <= float(max_tail_loss_rate)
            if max_mae_p90_frac_of_stop is not None and pd.notna(mae_stop_frac):
                feasible &= float(mae_stop_frac) <= float(max_mae_p90_frac_of_stop)

            rows.append({
                "start_hour": start_hour,
                "end_hour": int(r["entry_hour"]),
                "window_hours": width,
                "window_label": f"{start_hour:02d}-{int(r['entry_hour']):02d}",
                "hours": ",".join([f"{int(h):02d}" for h in x.loc[i:j, "entry_hour"].tolist()]),
                "trades": float(total_trades),
                "expected_net_pnl": float(total_edge),
                "avg_pnl_per_trade": float(avg_edge) if pd.notna(avg_edge) else np.nan,
                "agg_stop_breach_rate": float(agg_stop) if pd.notna(agg_stop) else np.nan,
                "agg_tail_loss_rate": float(agg_tail) if pd.notna(agg_tail) else np.nan,
                "agg_mae_p90": float(agg_mae_p90) if pd.notna(agg_mae_p90) else np.nan,
                "agg_mae_p90_stop_frac": float(mae_stop_frac) if pd.notna(mae_stop_frac) else np.nan,
                "worst_hour_danger_score": worst_danger if worst_danger != -np.inf else np.nan,
                "feasible": bool(feasible),
            })

    out = pd.DataFrame(rows)
    if out.empty:
        return out

    out = out.sort_values(
        ["feasible", "expected_net_pnl", "avg_pnl_per_trade", "trades"],
        ascending=[False, False, False, False],
    ).reset_index(drop=True)
    return out


# -----------------------------
# misc helpers used by report
# -----------------------------

def order_session_summary(df: pd.DataFrame, regime_col: str = "session_regime") -> pd.DataFrame:
    if df is None or df.empty or regime_col not in df.columns:
        return pd.DataFrame() if df is None else df
    order = {
        "london": 0,
        "pre_open": 1,
        "us_open": 2,
        "us_morning": 3,
        "us_midday": 4,
        "us_afternoon": 5,
        "globex_evening": 6,
    }
    z = df.copy()
    z["_order"] = z[regime_col].astype(str).map(order)
    return z.sort_values("_order").drop(columns=["_order"]).reset_index(drop=True)


def pick_best_worst_regimes(df: pd.DataFrame, regime_col: str) -> dict[str, object]:
    if df is None or df.empty or regime_col not in df.columns:
        return {
            "best_regime": None,
            "best_avg_pnl": None,
            "worst_regime": None,
            "worst_avg_pnl": None,
        }
    z = df.copy()
    z["avg_pnl"] = pd.to_numeric(z.get("avg_pnl"), errors="coerce")
    z = z.dropna(subset=["avg_pnl"])
    if z.empty:
        return {
            "best_regime": None,
            "best_avg_pnl": None,
            "worst_regime": None,
            "worst_avg_pnl": None,
        }
    best = z.sort_values("avg_pnl", ascending=False).iloc[0]
    worst = z.sort_values("avg_pnl", ascending=True).iloc[0]
    return {
        "best_regime": best[regime_col],
        "best_avg_pnl": float(best["avg_pnl"]),
        "worst_regime": worst[regime_col],
        "worst_avg_pnl": float(worst["avg_pnl"]),
    }


def build_regime_validation_summary(regime_df: pd.DataFrame) -> pd.DataFrame:
    if regime_df is None or regime_df.empty:
        return pd.DataFrame()
    rows = []
    for rid, sub in regime_df.groupby(regime_df.get("run_id", pd.Series(["run"] * len(regime_df))).astype(str), dropna=False):
        pnl = pd.to_numeric(sub.get("pnl"), errors="coerce")
        rows.append({
            "run_id": str(rid),
            "trades": int(len(sub)),
            "baseline_avg_pnl": float(pnl.mean()) if len(sub) else 0.0,
            "baseline_net_pnl": float(pnl.sum(min_count=1)) if len(sub) else 0.0,
            "trend_missing": int(sub.get("trend_regime", pd.Series(index=sub.index)).isna().sum()),
            "vol_missing": int(sub.get("vol_regime", pd.Series(index=sub.index)).isna().sum()),
            "vwap_missing": int(sub.get("vwap_regime", pd.Series(index=sub.index)).isna().sum()),
            "session_missing": int(sub.get("session_regime", pd.Series(index=sub.index)).isna().sum()),
            "combo_missing": int(sub.get("combo_regime", pd.Series(index=sub.index)).isna().sum()),
            "entry_hour_missing": int(pd.to_numeric(sub.get("entry_hour"), errors="coerce").isna().sum()),
            "mae_missing": int(pd.to_numeric(sub.get("mae"), errors="coerce").isna().sum()) if "mae" in sub.columns else int(len(sub)),
        })
    return pd.DataFrame(rows)
