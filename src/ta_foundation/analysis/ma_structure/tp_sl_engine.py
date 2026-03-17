from __future__ import annotations

import numpy as np
import pandas as pd


MIN_SAMPLE = 40


def _compute_outcome(row, tp, sl):
    mfe = row.get("mfe_atr")
    mae = row.get("mae_atr")

    if pd.isna(mfe) or pd.isna(mae):
        return None

    tp_hit = mfe >= tp
    sl_hit = mae >= sl

    if tp_hit and sl_hit:
        if bool(row.get("mfe_before_mae", True)):
            return tp
        return -sl

    if tp_hit:
        return tp

    if sl_hit:
        return -sl

    return None


def _score_candidate(df, tp, sl):
    outcomes = []
    tp_first = 0
    sl_first = 0

    for _, r in df.iterrows():
        outcome = _compute_outcome(r, tp, sl)

        if outcome is None:
            continue

        outcomes.append(outcome)

        if outcome > 0:
            tp_first += 1
        else:
            sl_first += 1

    decisive = tp_first + sl_first

    if decisive == 0:
        return None

    tp_prob = tp_first / decisive
    sl_prob = sl_first / decisive

    expectancy = tp_prob * tp - sl_prob * sl

    return {
        "tp_prob": tp_prob,
        "sl_prob": sl_prob,
        "expectancy": expectancy,
        "decisive": decisive,
        "tp_hits": tp_first,
        "sl_hits": sl_first,
    }


def _fold_expectancy(
    df,
    tp,
    sl,
    *,
    folds=4,
    fold_mode: str = "blocked_kfold",
    min_train_segments: int = 120,
    min_test_segments: int = MIN_SAMPLE,
):
    mode = str(fold_mode or "blocked_kfold").lower()
    if mode in {"off", "none", "disabled"}:
        return np.nan, np.nan

    if len(df) < max(1, int(min_train_segments)) + max(1, int(min_test_segments)):
        return np.nan, np.nan

    df = df.sort_values("entry_ts")

    # chunks = np.array_split(df, folds)

    # Keep fold chunks as DataFrame objects across pandas/numpy versions.
    # np.array_split(df, folds) may yield ndarray chunks in some environments,
    # which then breaks _score_candidate(...).iterrows().
    index_chunks = np.array_split(df.index.to_numpy(), folds)
    chunks = [df.loc[idx] for idx in index_chunks if len(idx) > 0]

    scores = []

    for chunk in chunks:
        if len(chunk) < max(1, int(min_test_segments)):
            continue
        s = _score_candidate(chunk, tp, sl)
        if not s:
            continue
        scores.append(s["expectancy"])

    if not scores:
        return np.nan, np.nan

    return float(np.mean(scores)), float(np.std(scores))


def score_tp_sl_candidates(
    segments: pd.DataFrame,
    path_stats: pd.DataFrame,
    *,
    tp_grid: list[float],
    sl_grid: list[float],
    fold_mode: str = "blocked_kfold",
    min_train_segments: int = 120,
    min_test_segments: int = MIN_SAMPLE,
) -> pd.DataFrame:

    if segments.empty or path_stats.empty:
        return pd.DataFrame()

    df = segments.merge(path_stats, on="segment_id", how="left")

    rows = []

    for anchor_id, g in df.groupby("anchor_id", dropna=False):

        for tp in tp_grid:
            for sl in sl_grid:

                score = _score_candidate(g, tp, sl)

                if not score:
                    continue

                fold_mean, fold_std = _fold_expectancy(
                    g,
                    tp,
                    sl,
                    fold_mode=fold_mode,
                    min_train_segments=min_train_segments,
                    min_test_segments=min_test_segments,
                )

                n = score["decisive"]

                rows.append(
                    {
                        "anchor_id": anchor_id,
                        "tp_atr": float(tp),
                        "sl_atr": float(sl),

                        "n_decisive": int(n),
                        "tp_prob": score["tp_prob"],
                        "sl_prob": score["sl_prob"],

                        "expectancy_score": score["expectancy"],

                        "fold_mean_expectancy": fold_mean,
                        "fold_std_expectancy": fold_std,

                        "sample_quality_flag":
                        "ok" if n >= MIN_SAMPLE else "thin",
                    }
                )

    out = pd.DataFrame(rows)

    if out.empty:
        return out

    out["robust_score"] = (
        out["expectancy_score"]
        * (1 - out["fold_std_expectancy"].fillna(0))
    )

    return out.sort_values(
        ["anchor_id", "robust_score", "n_decisive"],
        ascending=[True, False, False],
    ).reset_index(drop=True)