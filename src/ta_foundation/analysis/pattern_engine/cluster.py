from __future__ import annotations

from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd


def _kmeans_fallback(X: np.ndarray, k: int, n_iter: int = 30, seed: int = 7) -> np.ndarray:
    rng = np.random.default_rng(seed)
    n = X.shape[0]
    k = max(1, min(k, n))
    centroids = X[rng.choice(n, size=k, replace=False)]
    labels = np.zeros(n, dtype=int)

    for _ in range(n_iter):
        # assign
        d2 = ((X[:, None, :] - centroids[None, :, :]) ** 2).sum(axis=2)
        new_labels = d2.argmin(axis=1)
        if np.all(new_labels == labels):
            break
        labels = new_labels
        # update
        for j in range(k):
            m = labels == j
            if np.any(m):
                centroids[j] = X[m].mean(axis=0)
    return labels


def build_pattern_clusters(
    *,
    signals_df: pd.DataFrame,
    outcomes_df: pd.DataFrame,
    pattern_stats_df: pd.DataFrame,
    options: Dict[str, Any],
) -> Dict[str, pd.DataFrame]:
    """
    Returns:
      clusters_df, cluster_members_df, cluster_stats_df, embeddings_df
    """
    method = str(options.get("method", "kmeans"))
    k = int(options.get("k", 25))

    if pattern_stats_df is None or len(pattern_stats_df) == 0:
        empty = pd.DataFrame()
        return {
            "clusters_df": empty,
            "cluster_members_df": empty,
            "cluster_stats_df": empty,
            "embeddings_df": empty,
        }

    # Build one embedding per pattern: aggregate across horizons into a fixed vector
    # Use stable columns: avg_ticks, win_rate, p10, p50, p90, n_signals
    cols = ["avg_ticks", "win_rate", "p10", "p50", "p90", "n_signals"]
    for c in cols:
        if c not in pattern_stats_df.columns:
            pattern_stats_df[c] = np.nan  # no setdefault()

    emb = (
        pattern_stats_df.groupby("pattern_id", sort=False)[cols]
        .mean(numeric_only=True)
        .reset_index()
    )

    # numeric matrix
    X = emb[cols].to_numpy(dtype=float)
    X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)

    # run clustering
    labels: np.ndarray
    if method == "kmeans":
        try:
            from sklearn.cluster import KMeans  # type: ignore
            km = KMeans(n_clusters=max(1, min(k, len(X))), random_state=7, n_init="auto")
            labels = km.fit_predict(X)
        except Exception:
            labels = _kmeans_fallback(X, k=max(1, min(k, len(X))))
    else:
        # fallback: single cluster
        labels = np.zeros(len(X), dtype=int)

    # cluster ids
    cluster_ids = [f"c{int(i):03d}" for i in labels]
    emb["cluster_id"] = cluster_ids

    # clusters_df
    clusters_df = (
        emb.groupby("cluster_id", sort=True)
        .agg(n_members=("pattern_id", "count"))
        .reset_index()
    )
    clusters_df["cluster_method"] = method
    clusters_df["dispersion"] = 0.0
    clusters_df["notes"] = ""

    # members
    cluster_members_df = emb[["cluster_id", "pattern_id"]].copy()
    cluster_members_df["is_representative"] = False
    cluster_members_df["rep_rank"] = np.nan
    cluster_members_df["member_weight"] = 1.0

    # choose representative per cluster: highest mean avg_ticks across horizons
    rep_score = (
        pattern_stats_df.groupby("pattern_id", sort=False)["avg_ticks"]
        .mean()
        .reset_index()
        .rename(columns={"avg_ticks": "rep_score"})
    )
    cluster_members_df = cluster_members_df.merge(rep_score, on="pattern_id", how="left")

    reps = []
    for cid, df in cluster_members_df.groupby("cluster_id", sort=True):
        d2 = df.sort_values(["rep_score", "pattern_id"], ascending=[False, True]).head(1).copy()
        d2["is_representative"] = True
        d2["rep_rank"] = 1
        reps.append(d2)

    reps_df = pd.concat(reps, ignore_index=True) if reps else pd.DataFrame()
    cluster_members_df = cluster_members_df.drop(columns=["rep_score"])
    if len(reps_df) > 0:
        # mark reps
        rep_keys = set(zip(reps_df["cluster_id"], reps_df["pattern_id"]))
        is_rep = [(cid, pid) in rep_keys for cid, pid in zip(cluster_members_df["cluster_id"], cluster_members_df["pattern_id"])]
        cluster_members_df["is_representative"] = is_rep
        cluster_members_df.loc[cluster_members_df["is_representative"], "rep_rank"] = 1

    # cluster_stats: aggregate outcomes by cluster_id via mapping
    map_df = emb[["pattern_id", "cluster_id"]]
    o2 = outcomes_df.merge(map_df, on="pattern_id", how="inner") if outcomes_df is not None else pd.DataFrame()
    if len(o2) == 0:
        cluster_stats_df = pd.DataFrame()
    else:
        g = o2.groupby(["cluster_id", "horizon"], sort=False)
        rows: List[Dict[str, Any]] = []
        for (cid, H), df in g:
            rets = df["ret_ticks"].astype(float)
            n = int(len(rets))
            rows.append(
                {
                    "cluster_id": cid,
                    "horizon": int(H),
                    "n_signals": n,
                    "net_ticks": float(rets.sum()),
                    "avg_ticks": float(rets.mean()) if n else 0.0,
                    "win_rate": float((rets > 0).mean()) if n else 0.0,
                    "p10": float(np.nanpercentile(rets, 10)) if n else np.nan,
                    "p50": float(np.nanpercentile(rets, 50)) if n else np.nan,
                    "p90": float(np.nanpercentile(rets, 90)) if n else np.nan,
                    "stability_score": np.nan,
                    "prop_survival_score": np.nan,
                }
            )
        cluster_stats_df = pd.DataFrame(rows)

    # embeddings_df (contract)
    embeddings_df = emb[["pattern_id"] + cols].copy()
    embeddings_df["emb_dim"] = len(cols)
    embeddings_df["emb_v"] = embeddings_df[cols].apply(lambda r: [float(x) for x in r.values], axis=1)
    embeddings_df = embeddings_df[["pattern_id", "emb_dim", "emb_v"]]

    return {
        "clusters_df": clusters_df,
        "cluster_members_df": cluster_members_df,
        "cluster_stats_df": cluster_stats_df,
        "embeddings_df": embeddings_df,
    }