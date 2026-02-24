# ta_foundation/analysis/pattern_engine/cluster.py
from __future__ import annotations

import os
from typing import Any, Dict, Tuple

import numpy as np
import pandas as pd


def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def _parse_params_json(patterns_df: pd.DataFrame) -> pd.DataFrame:
    """
    Minimal embedding: family/structure hashes + numeric params extracted from params_json.
    For the skeleton: we rely on params_json being stable JSON.
    You will likely replace this with a richer embedding pipeline.
    """
    import json
    rows = []
    for _, r in patterns_df.iterrows():
        pj = r.get("params_json", "{}")
        try:
            params = json.loads(pj)
        except Exception:
            params = {}
        row = {"pattern_id": r["pattern_id"]}
        # numeric params only; sort keys for determinism
        for k in sorted(params.keys()):
            v = params[k]
            if isinstance(v, (int, float)) and np.isfinite(v):
                row[f"p__{k}"] = float(v)
        # add family/structure as hashed numeric features
        row["f__family"] = float(abs(hash(str(r.get("family","")))) % 10_000)
        row["f__structure"] = float(abs(hash(str(r.get("structure","")))) % 10_000)
        rows.append(row)
    emb = pd.DataFrame(rows).fillna(0.0)
    return emb


def _kmeans_cluster(emb_df: pd.DataFrame, k: int, random_state: int = 7) -> Tuple[np.ndarray, np.ndarray]:
    X = emb_df.drop(columns=["pattern_id"]).to_numpy(dtype=float)
    # guarded import
    try:
        from sklearn.cluster import KMeans
        km = KMeans(n_clusters=int(k), n_init=10, random_state=int(random_state))
        labels = km.fit_predict(X)
        centers = km.cluster_centers_
        return labels, centers
    except Exception:
        # fallback: naive partition by first feature quantiles
        if X.shape[1] == 0:
            labels = np.zeros(len(X), dtype=int)
            centers = np.zeros((1, 0), dtype=float)
            return labels, centers
        q = np.quantile(X[:, 0], np.linspace(0, 1, int(k)+1))
        labels = np.digitize(X[:, 0], q[1:-1], right=True)
        centers = np.zeros((int(k), X.shape[1]), dtype=float)
        for i in range(int(k)):
            m = labels == i
            centers[i] = X[m].mean(axis=0) if np.any(m) else 0.0
        return labels, centers


def build_pattern_clusters(
    *,
    patterns_df: pd.DataFrame,
    outcomes_df: pd.DataFrame,
    pattern_stats_df: pd.DataFrame,
    options: Dict[str, Any],
) -> Dict[str, pd.DataFrame]:
    """
    Returns:
      embeddings_df, clusters_df, cluster_members_df, cluster_stats_df
    """
    method = str(options.get("method") or "kmeans")
    k = int(options.get("k") or 25)
    random_state = int(options.get("random_state") or 7)
    rep_k = int(options.get("representative_k") or 2)

    emb = _parse_params_json(patterns_df)
    labels, centers = _kmeans_cluster(emb, k=k, random_state=random_state)

    embeddings_df = emb.copy()
    embeddings_df["emb_dim"] = int(embeddings_df.shape[1] - 1)
    # store vector as object list for portability
    X = embeddings_df.drop(columns=["pattern_id","emb_dim"]).to_numpy(dtype=float)
    embeddings_df["emb_v"] = [row.tolist() for row in X]

    clusters = []
    members = []

    for cid in sorted(np.unique(labels).tolist()):
        m = labels == cid
        member_ids = embeddings_df.loc[m, "pattern_id"].tolist()
        n = len(member_ids)
        if n == 0:
            continue
        # dispersion: mean distance to centroid
        centroid = centers[cid] if cid < centers.shape[0] else X[m].mean(axis=0)
        d = np.sqrt(((X[m] - centroid) ** 2).sum(axis=1))
        dispersion = float(np.mean(d)) if len(d) else 0.0
        cluster_id = f"c{cid:04d}"

        clusters.append({
            "cluster_id": cluster_id,
            "cluster_method": method,
            "n_members": int(n),
            "centroid_emb_v": centroid.tolist() if centroid is not None else None,
            "dispersion": dispersion,
            "notes": "",
        })

        # representatives: pick top rep_k by raw rank score aggregated across horizons
        ps = pattern_stats_df[pattern_stats_df["pattern_id"].isin(member_ids)].copy()
        if ps.empty:
            reps = member_ids[:rep_k]
        else:
            score = ps.groupby("pattern_id")["rank_score_raw"].sum().sort_values(ascending=False)
            reps = score.index.tolist()[:rep_k]

        for pid in member_ids:
            members.append({
                "cluster_id": cluster_id,
                "pattern_id": pid,
                "is_representative": bool(pid in reps),
                "rep_rank": int(reps.index(pid) + 1) if pid in reps else 0,
                "member_weight": 1.0,
            })

    clusters_df = pd.DataFrame(clusters) if clusters else pd.DataFrame(columns=[
        "cluster_id","cluster_method","n_members","centroid_emb_v","dispersion","notes"
    ])
    cluster_members_df = pd.DataFrame(members) if members else pd.DataFrame(columns=[
        "cluster_id","pattern_id","is_representative","rep_rank","member_weight"
    ])

    # cluster_stats: aggregate pattern_stats across members by horizon (simple mean weighted by n_signals)
    if clusters_df.empty or cluster_members_df.empty or pattern_stats_df.empty:
        cluster_stats_df = pd.DataFrame(columns=[
            "cluster_id","horizon","n_signals","net_ticks","avg_ticks","win_rate","p10","p50","p90",
            "stability_score","prop_survival_score"
        ])
    else:
        ps = pattern_stats_df.merge(cluster_members_df[["cluster_id","pattern_id"]], on="pattern_id", how="inner")
        # weight by n_signals
        def wavg(x, w):
            x = np.asarray(x, dtype=float)
            w = np.asarray(w, dtype=float)
            if np.sum(w) <= 0:
                return float(np.nanmean(x))
            return float(np.nansum(x * w) / np.nansum(w))

        rows = []
        for (cid, H), df in ps.groupby(["cluster_id","horizon"], sort=False):
            w = df["n_signals"].to_numpy(float)
            rows.append({
                "cluster_id": cid,
                "horizon": int(H),
                "n_signals": int(np.nansum(w)),
                "net_ticks": float(np.nansum(df["net_ticks"].to_numpy(float))),
                "avg_ticks": wavg(df["avg_ticks"], w),
                "win_rate": wavg(df["win_rate"], w),
                "p10": wavg(df["p10"], w),
                "p50": wavg(df["p50"], w),
                "p90": wavg(df["p90"], w),
                "stability_score": np.nan,
                "prop_survival_score": np.nan,
            })
        cluster_stats_df = pd.DataFrame(rows)

    return {
        "embeddings_df": embeddings_df,
        "clusters_df": clusters_df,
        "cluster_members_df": cluster_members_df,
        "cluster_stats_df": cluster_stats_df,
    }