from __future__ import annotations

"""
Strategy Clustering
===================
Phase 8 — Groups behaviorally similar strategies so that parameter-neighbour
variants don't dominate the top of the ranked list.

Algorithm
---------
1. Build a feature vector per run from evaluation/exit_discovery data:
     - by_regime PFs (up to 4 values)
     - by_session PFs (up to 3 values)
     - overall PF, WR, avg_duration_min
     - exit family preference (one-hot over 5 families)

2. Z-score normalise each column across all runs (robust to missing data).

3. Compute pairwise Euclidean distance matrix (pure numpy, no sklearn).

4. Single-linkage agglomerative clustering with a configurable distance
   threshold (default = 1.5 in normalised space).

5. For each cluster keep the highest-ranked run as the *representative*.
   All cluster members record cluster_id, cluster_size, cluster_rank,
   and is_cluster_representative.

6. Attach cluster fields to every row in cross_run_ranking["ranked"].
   Also write a cluster_map list to cross_run_ranking["clustering"].

Wire-in: called from run_clustering(packages) in orchestrator after run_ranking().

Entry point: run_clustering(packages) → None (modifies packages in-place)
"""

from typing import Any, Dict, List, Optional, Tuple

import numpy as np


# ---------------------------------------------------------------------------
# Default configuration
# ---------------------------------------------------------------------------

DEFAULT_DISTANCE_THRESHOLD: float = 1.5   # in z-score normalised space
_REGIME_KEYS  = ["trending", "ranging", "volatile", "quiet"]
_SESSION_KEYS = ["RTH", "ONH", "ETH"]
_EXIT_FAMILIES = ["fixed_rr", "atr_trail", "be_atr_trail", "chandelier", "giveback"]


# ---------------------------------------------------------------------------
# Feature vector construction
# ---------------------------------------------------------------------------

def _safe(v: Any, default: float = 0.0) -> float:
    if v is None:
        return default
    try:
        f = float(v)
        return default if not np.isfinite(f) else f
    except Exception:
        return default


def _build_feature_vector(sd: Dict[str, Any]) -> Dict[str, float]:
    """
    Build a dict of named float features for a single run's strategy_discovery block.
    Missing values are filled with 0.0 so every run has the same keys.
    """
    evaluation = sd.get("evaluation") or {}
    exit_disc  = sd.get("exit_discovery") or {}

    vec: Dict[str, float] = {}

    # --- Overall metrics ---
    vec["pf"]           = _safe(evaluation.get("profit_factor"), 1.0)
    vec["win_rate"]     = _safe(evaluation.get("win_rate"), 0.5)
    vec["avg_duration"] = _safe(evaluation.get("avg_duration_min"), 15.0)

    # --- By-regime profit factors ---
    by_regime = evaluation.get("by_regime") or {}
    for rkey in _REGIME_KEYS:
        cell = by_regime.get(rkey) or {}
        vec[f"regime_pf_{rkey}"] = _safe(cell.get("profit_factor"), 1.0)

    # --- By-session profit factors ---
    by_session = evaluation.get("by_session") or {}
    for skey in _SESSION_KEYS:
        cell = by_session.get(skey) or {}
        vec[f"session_pf_{skey}"] = _safe(cell.get("profit_factor"), 1.0)

    # --- Exit family preference (one-hot of best exit family) ---
    # Use the top-ranked exit policy's family if available
    best_family: Optional[str] = None
    policy_ranking = exit_disc.get("policy_ranking") or []
    if policy_ranking and isinstance(policy_ranking, list):
        top_policy = policy_ranking[0] if policy_ranking else {}
        if isinstance(top_policy, dict):
            best_family = top_policy.get("family")

    for fam in _EXIT_FAMILIES:
        vec[f"exit_{fam}"] = 1.0 if (best_family == fam) else 0.0

    return vec


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

def _normalise_vectors(
    run_ids: List[str],
    raw_vecs: Dict[str, Dict[str, float]],
) -> Dict[str, np.ndarray]:
    """
    Z-score normalise each feature column across runs.
    Columns with zero std are left as zeros (constant feature = no discriminating power).
    Returns a dict mapping run_id → 1-D numpy array.
    """
    if not run_ids:
        return {}

    # Collect all keys in insertion order
    all_keys: List[str] = list(next(iter(raw_vecs.values())).keys())
    n = len(run_ids)
    d = len(all_keys)

    matrix = np.zeros((n, d), dtype=float)
    for i, rid in enumerate(run_ids):
        v = raw_vecs[rid]
        for j, k in enumerate(all_keys):
            matrix[i, j] = v.get(k, 0.0)

    col_mean = matrix.mean(axis=0)
    col_std  = matrix.std(axis=0)
    col_std[col_std == 0] = 1.0   # avoid division by zero

    normed = (matrix - col_mean) / col_std

    return {rid: normed[i] for i, rid in enumerate(run_ids)}


# ---------------------------------------------------------------------------
# Pairwise distance + single-linkage clustering
# ---------------------------------------------------------------------------

def _pairwise_distance(
    run_ids: List[str],
    normed: Dict[str, np.ndarray],
) -> np.ndarray:
    """Euclidean distance matrix, shape (n, n)."""
    n = len(run_ids)
    D = np.zeros((n, n), dtype=float)
    for i in range(n):
        vi = normed[run_ids[i]]
        for j in range(i + 1, n):
            vj = normed[run_ids[j]]
            d  = float(np.linalg.norm(vi - vj))
            D[i, j] = d
            D[j, i] = d
    return D


def _single_linkage_clusters(
    run_ids: List[str],
    D: np.ndarray,
    threshold: float,
) -> List[List[str]]:
    """
    Greedy single-linkage agglomerative clustering.

    Two runs belong to the same cluster if ANY member of the cluster is within
    `threshold` distance of the candidate.  We merge in ascending distance
    order (single-linkage criterion).

    Returns a list of clusters, each cluster being a list of run_ids.
    """
    n = len(run_ids)
    if n == 0:
        return []

    # Start: each run is its own cluster
    cluster_of: List[int] = list(range(n))   # cluster_of[i] = cluster label for run i

    # Collect all (distance, i, j) pairs sorted ascending
    pairs: List[Tuple[float, int, int]] = []
    for i in range(n):
        for j in range(i + 1, n):
            pairs.append((D[i, j], i, j))
    pairs.sort(key=lambda x: x[0])

    # Merge clusters when distance ≤ threshold (union-find via cluster relabelling)
    for dist, i, j in pairs:
        if dist > threshold:
            break
        ci, cj = cluster_of[i], cluster_of[j]
        if ci == cj:
            continue
        # Merge cluster cj into ci (relabel all cj → ci)
        for k in range(n):
            if cluster_of[k] == cj:
                cluster_of[k] = ci

    # Group by cluster label
    clusters_dict: Dict[int, List[str]] = {}
    for i, rid in enumerate(run_ids):
        lbl = cluster_of[i]
        clusters_dict.setdefault(lbl, []).append(rid)

    return list(clusters_dict.values())


# ---------------------------------------------------------------------------
# Attach cluster info to cross_run_ranking rows
# ---------------------------------------------------------------------------

def _attach_cluster_info(
    clusters: List[List[str]],
    ranked_rows: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Attach cluster_id, cluster_size, cluster_rank, is_cluster_representative
    to each row in ranked_rows (sorted by rank ascending, rank 1 = best).

    The *representative* of each cluster is the row with the lowest rank value
    (highest score) — i.e. the one that was already ranked #1 in the cluster.

    Returns an updated cluster_map:
      [{cluster_id, representative_run_id, members, cluster_size}]
    """
    # Build rank lookup
    rank_of: Dict[str, int] = {r["run_id"]: r.get("rank", 9999) for r in ranked_rows}
    row_of:  Dict[str, Dict[str, Any]] = {r["run_id"]: r for r in ranked_rows}

    cluster_map: List[Dict[str, Any]] = []

    for cluster_idx, members in enumerate(clusters, start=1):
        # Sort members by rank (ascending = best first)
        members_ranked = sorted(members, key=lambda rid: rank_of.get(rid, 9999))
        representative = members_ranked[0]

        cluster_map.append({
            "cluster_id":            cluster_idx,
            "representative_run_id": representative,
            "members":               members_ranked,
            "cluster_size":          len(members_ranked),
        })

        for within_rank, rid in enumerate(members_ranked, start=1):
            row = row_of.get(rid)
            if row is None:
                continue
            row["cluster_id"]                = cluster_idx
            row["cluster_size"]              = len(members_ranked)
            row["cluster_rank"]              = within_rank   # 1 = representative
            row["is_cluster_representative"] = within_rank == 1

    return cluster_map


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_clustering(
    packages: Dict[str, Any],
    distance_threshold: float = DEFAULT_DISTANCE_THRESHOLD,
) -> None:
    """
    Cluster strategies by behavioural similarity and annotate cross_run_ranking.

    Called after run_ranking() in the orchestrator.

    Modifies packages in-place:
      - Adds cluster fields to every row in cross_run_ranking["ranked"]
      - Adds cross_run_ranking["clustering"] with cluster_map and diagnostics

    Parameters
    ----------
    packages           : the full packages dict from the pipeline
    distance_threshold : max normalised Euclidean distance to merge two runs
    """
    # ---- find the package that holds cross_run_ranking ----
    cross_run: Optional[Dict[str, Any]] = None
    host_sd:   Optional[Dict[str, Any]] = None

    for run_id, pkg in (packages or {}).items():
        if str(run_id).startswith("__"):
            continue
        meta = getattr(pkg, "metadata", None)
        if not isinstance(meta, dict):
            continue
        sd = meta.get("derived", {}).get("strategy_discovery")
        if isinstance(sd, dict) and "cross_run_ranking" in sd:
            cross_run = sd["cross_run_ranking"]
            host_sd   = sd
            break

    if cross_run is None:
        return   # ranking hasn't run yet; nothing to cluster

    ranked_rows: List[Dict[str, Any]] = cross_run.get("ranked") or []
    if len(ranked_rows) < 2:
        # Single run — trivially its own cluster
        if ranked_rows:
            ranked_rows[0].update({
                "cluster_id": 1,
                "cluster_size": 1,
                "cluster_rank": 1,
                "is_cluster_representative": True,
            })
            cross_run["clustering"] = {
                "cluster_map": [{
                    "cluster_id": 1,
                    "representative_run_id": ranked_rows[0]["run_id"],
                    "members": [ranked_rows[0]["run_id"]],
                    "cluster_size": 1,
                }],
                "diagnostics": {
                    "n_runs":      1,
                    "n_clusters":  1,
                    "threshold":   distance_threshold,
                    "method":      "single_linkage",
                    "note":        "trivial — single run",
                },
            }
        return

    # ---- collect run_ids and SD blocks for feature extraction ----
    run_ids: List[str] = [r["run_id"] for r in ranked_rows]

    # Build a quick lookup: run_id → strategy_discovery dict
    sd_of: Dict[str, Dict[str, Any]] = {}
    for run_id, pkg in (packages or {}).items():
        if str(run_id).startswith("__"):
            continue
        meta = getattr(pkg, "metadata", None)
        if not isinstance(meta, dict):
            continue
        sd = meta.get("derived", {}).get("strategy_discovery")
        if isinstance(sd, dict) and str(run_id) in run_ids:
            sd_of[str(run_id)] = sd

    # ---- feature vectors ----
    raw_vecs: Dict[str, Dict[str, float]] = {}
    for rid in run_ids:
        sd = sd_of.get(rid) or {}
        raw_vecs[rid] = _build_feature_vector(sd)

    # ---- normalise ----
    try:
        normed = _normalise_vectors(run_ids, raw_vecs)
    except Exception:
        normed = {}

    if not normed:
        # Fall back: every run is its own cluster
        for row in ranked_rows:
            row.update({
                "cluster_id":                row.get("rank", 0),
                "cluster_size":              1,
                "cluster_rank":              1,
                "is_cluster_representative": True,
            })
        cross_run["clustering"] = {
            "cluster_map": [],
            "diagnostics": {"error": "normalisation failed", "n_runs": len(run_ids)},
        }
        return

    # ---- distance matrix ----
    try:
        D = _pairwise_distance(run_ids, normed)
    except Exception:
        D = np.zeros((len(run_ids), len(run_ids)))

    # ---- cluster ----
    try:
        clusters = _single_linkage_clusters(run_ids, D, threshold=distance_threshold)
    except Exception:
        clusters = [[rid] for rid in run_ids]

    # ---- annotate rows ----
    cluster_map = _attach_cluster_info(clusters, ranked_rows)

    # ---- diagnostics ----
    n_singletons  = sum(1 for c in cluster_map if c["cluster_size"] == 1)
    n_multi       = sum(1 for c in cluster_map if c["cluster_size"] > 1)
    largest_size  = max((c["cluster_size"] for c in cluster_map), default=0)

    cross_run["clustering"] = {
        "cluster_map": cluster_map,
        "diagnostics": {
            "n_runs":        len(run_ids),
            "n_clusters":    len(cluster_map),
            "n_singletons":  n_singletons,
            "n_multi_run_clusters": n_multi,
            "largest_cluster_size": largest_size,
            "threshold":     distance_threshold,
            "method":        "single_linkage",
        },
    }
