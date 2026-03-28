from __future__ import annotations

"""
Feature Importance
==================
Phase 1b — Ranks features in the feature matrix by their association with
trade profitability.

Uses only numpy/pandas (no sklearn required). Optionally upgrades to
sklearn RandomForest importance when the package is available.

Three association measures:
  1. Point-biserial correlation (numeric feature vs binary win/loss label)
  2. Cramér's V (categorical feature vs binary win/loss label)
  3. Mean profit by group (for categorical features — actionable insight)

Optional:
  4. RandomForest feature importance (numeric + one-hot categoricals via sklearn)

Entry point: compute_feature_importance(feature_df, profit_col) → dict (JSON-safe)

Attaches results to:
  pkg.metadata["derived"]["strategy_discovery"]["importance"]  (JSON-safe)
"""

from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
        return None if not np.isfinite(f) else f
    except Exception:
        return None


def _make_json_safe(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _make_json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_make_json_safe(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        v = float(obj)
        return None if not np.isfinite(v) else v
    if isinstance(obj, float):
        return None if not np.isfinite(obj) else obj
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    return obj


# ---------------------------------------------------------------------------
# Association: numeric → binary outcome (point-biserial correlation)
# ---------------------------------------------------------------------------

def _point_biserial_corr(feature: pd.Series, binary_outcome: pd.Series) -> Optional[float]:
    """
    Point-biserial correlation between a numeric feature and a binary outcome.
    Equivalent to Pearson r when one variable is binary.
    Returns None if insufficient data.
    """
    mask = feature.notna() & binary_outcome.notna()
    x = feature[mask].astype(float)
    y = binary_outcome[mask].astype(float)
    n = len(x)
    if n < 5:
        return None
    try:
        std_x = float(x.std())
        if std_x == 0:
            return None
        r = float(np.corrcoef(x.values, y.values)[0, 1])
        return _safe_float(r)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Association: categorical → binary outcome (Cramér's V)
# ---------------------------------------------------------------------------

def _cramers_v(feature: pd.Series, binary_outcome: pd.Series) -> Optional[float]:
    """
    Cramér's V between a categorical feature and a binary outcome.
    Range: [0, 1] — 0 = no association, 1 = perfect association.
    """
    mask = feature.notna() & binary_outcome.notna()
    x = feature[mask].astype(str)
    y = binary_outcome[mask].astype(int)
    n = len(x)
    if n < 5:
        return None
    try:
        contingency = pd.crosstab(x, y)
        chi2 = 0.0
        expected = np.outer(contingency.sum(axis=1), contingency.sum(axis=0)) / n
        for i in range(contingency.shape[0]):
            for j in range(contingency.shape[1]):
                obs = float(contingency.iloc[i, j])
                exp = float(expected[i, j])
                if exp > 0:
                    chi2 += (obs - exp) ** 2 / exp
        min_dim = min(contingency.shape) - 1
        if min_dim <= 0 or n <= 0:
            return None
        v = float(np.sqrt(chi2 / (n * min_dim)))
        return _safe_float(v)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Effect size: mean profit by category group
# ---------------------------------------------------------------------------

def _mean_profit_by_group(
    feature: pd.Series,
    profit: pd.Series,
    min_n: int = 5,
    max_groups: int = 20,
) -> Dict[str, Any]:
    """
    For each category value, compute mean profit, win rate, n_trades.
    Returns dict keyed by category value.
    """
    mask = feature.notna() & profit.notna()
    x = feature[mask].astype(str)
    y = profit[mask]
    result: Dict[str, Any] = {}
    for group_val in x.unique()[:max_groups]:
        sub = y[x == group_val]
        if len(sub) < min_n:
            continue
        mean_p = _safe_float(float(sub.mean()))
        win_r  = _safe_float(float((sub > 0).mean()))
        result[str(group_val)] = {
            "n_trades":   int(len(sub)),
            "mean_profit": mean_p,
            "win_rate":    win_r,
        }
    return result


# ---------------------------------------------------------------------------
# Optional: sklearn RandomForest importance
# ---------------------------------------------------------------------------

def _rf_importance(
    feature_df: pd.DataFrame,
    numeric_cols: List[str],
    categorical_cols: List[str],
    win_label: pd.Series,
    n_estimators: int = 100,
    max_depth: int = 4,
    random_state: int = 42,
) -> Optional[List[Dict[str, Any]]]:
    """
    Compute RandomForest feature importance if sklearn is available.
    Returns None when sklearn is absent or on any failure.
    """
    try:
        from sklearn.ensemble import RandomForestClassifier
        from sklearn.preprocessing import LabelEncoder
    except ImportError:
        return None

    try:
        frames = []
        col_names = []

        # Numeric columns
        for col in numeric_cols:
            s = pd.to_numeric(feature_df[col], errors="coerce")
            median_fill = float(s.median()) if s.notna().any() else 0.0
            frames.append(s.fillna(median_fill).values.reshape(-1, 1))
            col_names.append(col)

        # Categorical columns (label-encode)
        for col in categorical_cols:
            le = LabelEncoder()
            encoded = le.fit_transform(feature_df[col].fillna("__missing__").astype(str))
            frames.append(encoded.reshape(-1, 1))
            col_names.append(col)

        if not frames:
            return None

        X = np.hstack(frames)
        y = win_label.values

        mask = ~np.isnan(y).astype(bool)
        X = X[mask]
        y = y[mask].astype(int)

        if len(X) < 20 or len(np.unique(y)) < 2:
            return None

        rf = RandomForestClassifier(
            n_estimators=n_estimators,
            max_depth=max_depth,
            random_state=random_state,
            n_jobs=1,
        )
        rf.fit(X, y)

        importances = rf.feature_importances_
        result = sorted(
            [
                {"feature": col, "importance": _safe_float(float(imp))}
                for col, imp in zip(col_names, importances)
            ],
            key=lambda r: r["importance"] or 0.0,
            reverse=True,
        )
        for i, row in enumerate(result, start=1):
            row["rank"] = i
        return result

    except Exception:
        return None


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

# Columns always present in feature matrix that are not predictive features
_SKIP_COLS = frozenset({
    "entry_time", "exit_time", "entry_price", "exit_price",
    "profit", "profit_net", "mae", "mfe", "etd",
    "trade_id", "instrument", "market_pos",
    "entry_hour",  # entry_hour is encoded as session_label — skip raw
})

# Numeric features to analyze
_NUMERIC_FEATURES = [
    "adx", "atr", "pattern_score", "n_patterns_fired", "direction",
]

# Categorical features to analyze
_CATEGORICAL_FEATURES = [
    "regime", "session_label", "vol_regime", "trend_direction", "pattern_verdict",
]


def compute_feature_importance(
    feature_df: pd.DataFrame,
    profit_col: str = "profit_net",
    *,
    min_trades: int = 20,
    include_pattern_cols: bool = True,
    use_rf: bool = True,
    rf_n_estimators: int = 100,
) -> Dict[str, Any]:
    """
    Compute feature importance from a feature matrix (output of build_feature_matrix).

    Parameters
    ----------
    feature_df         : output of build_feature_matrix (trades + context columns)
    profit_col         : profit column to use for win/loss labeling
    min_trades         : minimum rows required for analysis
    include_pattern_cols: include pat_* bool columns in RF analysis
    use_rf             : attempt sklearn RF importance if available

    Returns
    -------
    JSON-safe dict with keys:
      numeric_correlations  : [{feature, correlation, abs_correlation, rank}]
      categorical_effects   : [{feature, cramers_v, rank, group_means}]
      rf_importance         : [{feature, importance, rank}] or null
      top_features          : [str] — top 5 by combined rank
      diagnostics           : {n_trades, n_winners, ...}
    """
    diag: Dict[str, Any] = {
        "n_trades": 0,
        "n_winners": 0,
        "n_features_analyzed": 0,
        "profit_col": profit_col,
        "issues": [],
    }

    # Guard: need a valid DataFrame with profit column
    if feature_df is None or not isinstance(feature_df, pd.DataFrame) or len(feature_df) == 0:
        diag["issues"].append("feature_df is empty or None")
        return {"numeric_correlations": [], "categorical_effects": [], "rf_importance": None,
                "top_features": [], "diagnostics": diag}

    if profit_col not in feature_df.columns:
        # Fall back to 'profit' if profit_net not present
        if "profit" in feature_df.columns:
            profit_col = "profit"
            diag["profit_col_used"] = profit_col
        else:
            diag["issues"].append(f"profit column '{profit_col}' not found")
            return {"numeric_correlations": [], "categorical_effects": [], "rf_importance": None,
                    "top_features": [], "diagnostics": diag}

    profit = pd.to_numeric(feature_df[profit_col], errors="coerce")
    n = int(profit.notna().sum())
    diag["n_trades"] = n

    if n < min_trades:
        diag["issues"].append(f"insufficient trades: {n} < {min_trades}")

    win_label = (profit > 0).astype(float)
    win_label[profit.isna()] = np.nan
    diag["n_winners"] = int((profit > 0).sum())

    # Resolve actual numeric features present in df
    numeric_cols_present = [
        c for c in _NUMERIC_FEATURES
        if c in feature_df.columns
    ]

    # Resolve categorical features present
    cat_cols_present = [
        c for c in _CATEGORICAL_FEATURES
        if c in feature_df.columns
    ]

    # pat_* bool columns (for RF only — individual patterns may have too few samples for stats)
    pat_cols = (
        [c for c in feature_df.columns if str(c).startswith("pat_")]
        if include_pattern_cols else []
    )

    diag["n_features_analyzed"] = len(numeric_cols_present) + len(cat_cols_present) + len(pat_cols)

    # ------------------------------------------------------------------
    # 1. Numeric correlations (point-biserial)
    # ------------------------------------------------------------------
    numeric_rows: List[Dict[str, Any]] = []
    for col in numeric_cols_present:
        series = pd.to_numeric(feature_df[col], errors="coerce")
        corr = _point_biserial_corr(series, win_label)
        if corr is not None:
            numeric_rows.append({
                "feature":         col,
                "correlation":     round(corr, 4),
                "abs_correlation": round(abs(corr), 4),
            })

    # Include pat_* columns in numeric correlation too (they're 0/1 booleans)
    for col in pat_cols[:30]:  # cap at 30 to keep output manageable
        series = pd.to_numeric(feature_df[col].astype(float), errors="coerce")
        corr = _point_biserial_corr(series, win_label)
        if corr is not None and abs(corr) > 0.02:  # filter near-zero
            numeric_rows.append({
                "feature":         col,
                "correlation":     round(corr, 4),
                "abs_correlation": round(abs(corr), 4),
            })

    numeric_rows.sort(key=lambda r: r["abs_correlation"], reverse=True)
    for i, row in enumerate(numeric_rows, start=1):
        row["rank"] = i

    # ------------------------------------------------------------------
    # 2. Categorical effects (Cramér's V + mean profit by group)
    # ------------------------------------------------------------------
    cat_rows: List[Dict[str, Any]] = []
    for col in cat_cols_present:
        series = feature_df[col].astype(str)
        v = _cramers_v(series, win_label)
        group_means = _mean_profit_by_group(series, profit)
        if v is not None or group_means:
            cat_rows.append({
                "feature":     col,
                "cramers_v":   round(v, 4) if v is not None else None,
                "group_means": group_means,
            })

    cat_rows.sort(key=lambda r: r.get("cramers_v") or 0.0, reverse=True)
    for i, row in enumerate(cat_rows, start=1):
        row["rank"] = i

    # ------------------------------------------------------------------
    # 3. Optional sklearn RF importance
    # ------------------------------------------------------------------
    rf_result = None
    if use_rf and n >= min_trades:
        rf_result = _rf_importance(
            feature_df=feature_df,
            numeric_cols=numeric_cols_present + pat_cols[:30],
            categorical_cols=cat_cols_present,
            win_label=win_label,
            n_estimators=rf_n_estimators,
        )
        if rf_result is not None:
            diag["rf_used"] = True
            diag["rf_n_estimators"] = rf_n_estimators
        else:
            diag["rf_used"] = False

    # ------------------------------------------------------------------
    # 4. Top features (combined rank across all methods)
    # ------------------------------------------------------------------
    combined: Dict[str, float] = {}

    # Numeric: higher abs_corr = better
    for row in numeric_rows[:10]:
        feat = row["feature"]
        combined[feat] = combined.get(feat, 0.0) + (row.get("abs_correlation") or 0.0) * 0.5

    # Categorical: higher Cramér's V = better
    for row in cat_rows[:10]:
        feat = row["feature"]
        combined[feat] = combined.get(feat, 0.0) + (row.get("cramers_v") or 0.0) * 0.5

    # RF: add importance scores if available
    if rf_result:
        for row in rf_result[:15]:
            feat = row["feature"]
            combined[feat] = combined.get(feat, 0.0) + (row.get("importance") or 0.0)

    top_features = [feat for feat, _ in sorted(combined.items(), key=lambda x: x[1], reverse=True)][:8]

    return _make_json_safe({
        "numeric_correlations": numeric_rows,
        "categorical_effects":  cat_rows,
        "rf_importance":        rf_result,
        "top_features":         top_features,
        "diagnostics":          diag,
    })
