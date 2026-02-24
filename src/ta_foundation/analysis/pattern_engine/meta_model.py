# ta_foundation/analysis/pattern_engine/meta_model.py
from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd


def train_cluster_meta_model(
    *,
    cluster_features_df: pd.DataFrame,
    target_df: pd.DataFrame,
    options: Dict[str, Any],
) -> Dict[str, Any]:
    """
    cluster_features_df: rows by cluster_id
    target_df: must include cluster_id and target col (default prop_survival_score)
    """
    enabled = bool(options.get("enabled") or False)
    if not enabled:
        return {
            "model_artifact": None,
            "predictions_df": pd.DataFrame(columns=["cluster_id","pred_score"]),
            "feature_importance_df": pd.DataFrame(columns=["feature","importance"]),
        }

    target_col = str(options.get("target_col") or "prop_survival_score")
    model_type = str(options.get("model_type") or "gbrt")
    random_state = int(options.get("random_state") or 7)

    df = cluster_features_df.merge(target_df[["cluster_id", target_col]], on="cluster_id", how="inner").copy()
    if df.empty:
        return {
            "model_artifact": None,
            "predictions_df": pd.DataFrame(columns=["cluster_id","pred_score"]),
            "feature_importance_df": pd.DataFrame(columns=["feature","importance"]),
        }

    # choose features: numeric columns excluding id + target
    drop = {"cluster_id", target_col}
    feat_cols = [c for c in df.columns if c not in drop and np.issubdtype(df[c].dtype, np.number)]
    X = df[feat_cols].to_numpy(float)
    y = df[target_col].to_numpy(float)

    # guarded sklearn
    try:
        if model_type == "ridge":
            from sklearn.linear_model import Ridge
            m = Ridge(alpha=1.0, random_state=random_state)
        else:
            from sklearn.ensemble import GradientBoostingRegressor
            m = GradientBoostingRegressor(random_state=random_state)
        m.fit(X, y)
        pred = m.predict(X)

        # feature importance
        if hasattr(m, "feature_importances_"):
            imp = m.feature_importances_
        elif hasattr(m, "coef_"):
            imp = np.abs(np.asarray(m.coef_, dtype=float))
        else:
            imp = np.zeros(len(feat_cols), dtype=float)

        fi = pd.DataFrame({"feature": feat_cols, "importance": imp}).sort_values("importance", ascending=False)

        artifact = {
            "model_type": model_type,
            "target_col": target_col,
            "feature_cols": feat_cols,
            "random_state": random_state,
        }
        # NOTE: serialize full sklearn model elsewhere if needed; keep metadata light here.

        predictions_df = pd.DataFrame({
            "cluster_id": df["cluster_id"].values,
            "pred_score": pred,
        }).sort_values("pred_score", ascending=False)

        return {
            "model_artifact": artifact,
            "predictions_df": predictions_df,
            "feature_importance_df": fi,
        }
    except Exception:
        # no sklearn available
        return {
            "model_artifact": {"error": "sklearn_not_available_or_failed"},
            "predictions_df": pd.DataFrame(columns=["cluster_id","pred_score"]),
            "feature_importance_df": pd.DataFrame(columns=["feature","importance"]),
        }