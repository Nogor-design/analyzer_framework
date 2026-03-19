from __future__ import annotations

import numpy as np
import pandas as pd


MIN_SAMPLE = 50

def build_validation_folds(
    df: pd.DataFrame,
    *,
    fold_mode: str = "anchored_walk_forward",
    min_train_segments: int = 150,
    min_test_segments: int = 50,
    k_folds: int = 4,
) -> pd.DataFrame:
    mode = str(fold_mode or "anchored_walk_forward").lower()
    out_rows = []

    if df is None or df.empty:
        return pd.DataFrame()

    d = df.sort_values("entry_ts").reset_index(drop=True)
    n = len(d)
    min_train = max(1, int(min_train_segments))
    min_test = max(1, int(min_test_segments))

    if mode == "anchored_walk_forward":
        start = min_train
        fold_id = 0
        while (start + min_test) <= n:
            train_idx = d.index[:start]
            test_idx = d.index[start : start + min_test]
            if len(train_idx) < min_train or len(test_idx) < min_test:
                break

            out_rows.append(
                {
                    "fold_id": fold_id,
                    "fold_mode": mode,
                    "train_start_ts": d.loc[int(train_idx.min()), "entry_ts"],
                    "train_end_ts": d.loc[int(train_idx.max()), "entry_ts"],
                    "test_start_ts": d.loc[int(test_idx.min()), "entry_ts"],
                    "test_end_ts": d.loc[int(test_idx.max()), "entry_ts"],
                    "n_train": int(len(train_idx)),
                    "n_test": int(len(test_idx)),
                }
            )
            fold_id += 1
            start += min_test

        return pd.DataFrame(out_rows)

    # Fallback: blocked k-fold test windows for compatibility
    index_chunks = np.array_split(d.index.to_numpy(), max(1, int(k_folds)))
    for fold_id, idx in enumerate(index_chunks):
        if len(idx) < min_test:
            continue
        test_idx = d.index.isin(idx)
        train_idx = ~test_idx
        n_train = int(train_idx.sum())
        n_test = int(test_idx.sum())
        if n_train < min_train or n_test < min_test:
            continue
        test_pos = np.where(test_idx)[0]
        train_pos = np.where(train_idx)[0]
        out_rows.append(
            {
                "fold_id": fold_id,
                "fold_mode": mode,
                "train_start_ts": d.loc[int(train_pos.min()), "entry_ts"],
                "train_end_ts": d.loc[int(train_pos.max()), "entry_ts"],
                "test_start_ts": d.loc[int(test_pos.min()), "entry_ts"],
                "test_end_ts": d.loc[int(test_pos.max()), "entry_ts"],
                "n_train": n_train,
                "n_test": n_test,
            }
        )
    return pd.DataFrame(out_rows)

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
    mode = str(fold_mode or "anchored_walk_forward").lower()
    if mode in {"off", "none", "disabled"}:
        return np.nan, np.nan

    if len(df) < max(1, int(min_train_segments)) + max(1, int(min_test_segments)):
        return np.nan, np.nan

    folds_df = build_validation_folds(
        df,
        fold_mode=mode,
        min_train_segments=min_train_segments,
        min_test_segments=min_test_segments,
        k_folds=folds,
    )
    if folds_df.empty:
        return np.nan, np.nan

    # chunks = np.array_split(df, folds)

    # Keep fold chunks as DataFrame objects across pandas/numpy versions.
    # np.array_split(df, folds) may yield ndarray chunks in some environments,
    # which then breaks _score_candidate(...).iterrows().
    # index_chunks = np.array_split(df.index.to_numpy(), folds)
    # chunks = [df.loc[idx] for idx in index_chunks if len(idx) > 0]

    df = df.sort_values("entry_ts")

    scores = []

    for _, fold in folds_df.iterrows():
        test_start = fold.get("test_start_ts")
        test_end = fold.get("test_end_ts")
        chunk = df[(df["entry_ts"] >= test_start) & (df["entry_ts"] <= test_end)]
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
) -> tuple[pd.DataFrame, pd.DataFrame]:

    if segments.empty or path_stats.empty:
        return pd.DataFrame(), pd.DataFrame()

    df = segments.merge(path_stats, on="segment_id", how="left")

    rows = []
    fold_rows = []

    for anchor_id, g in df.groupby("anchor_id", dropna=False):
        anchor_folds = build_validation_folds(
            g,
            fold_mode=fold_mode,
            min_train_segments=min_train_segments,
            min_test_segments=min_test_segments,
        )
        if not anchor_folds.empty:
            d_folds = anchor_folds.copy()
            d_folds.insert(0, "anchor_id", anchor_id)
            fold_rows.extend(d_folds.to_dict(orient="records"))

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
                        "ok" if n >= max(int(min_test_segments), MIN_SAMPLE) else "thin",
                    }
                )

    out = pd.DataFrame(rows)
    folds_out = pd.DataFrame(fold_rows)

    if out.empty:
        return out, folds_out

    out["robust_score"] = (
        out["expectancy_score"]
        * (1 - out["fold_std_expectancy"].fillna(0))
    )

    out = out.sort_values(
        ["anchor_id", "robust_score", "n_decisive"],
        ascending=[True, False, False],
    ).reset_index(drop=True)
    return out, folds_out