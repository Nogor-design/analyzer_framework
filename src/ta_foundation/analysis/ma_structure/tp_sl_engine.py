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

def _clip01(value: float) -> float:
    if pd.isna(value):
        return np.nan
    return float(max(0.0, min(1.0, value)))


def _tail_dependency_share(outcomes: list[float]) -> float:
    gains = sorted(float(x) for x in outcomes if x > 0)
    if not gains:
        return np.nan
    total_gain = float(sum(gains))
    if total_gain <= 0:
        return np.nan
    top_n = max(1, int(np.ceil(len(gains) * 0.10)))
    tail_gain = float(sum(gains[-top_n:]))
    return _clip01(tail_gain / total_gain)


def _clip01(value: float) -> float:
    if pd.isna(value):
        return np.nan
    return float(max(0.0, min(1.0, value)))


def _tail_dependency_share(outcomes: list[float]) -> float:
    gains = sorted(float(x) for x in outcomes if x > 0)
    if not gains:
        return np.nan
    total_gain = float(sum(gains))
    if total_gain <= 0:
        return np.nan
    top_n = max(1, int(np.ceil(len(gains) * 0.10)))
    tail_gain = float(sum(gains[-top_n:]))
    return _clip01(tail_gain / total_gain)


def _score_candidate(df, tp, sl):
    outcomes = []
    tp_first = 0
    sl_first = 0

    for _, r in df.iterrows():
        outcome = _compute_outcome(r, tp, sl)

        if outcome is None:
            continue

        outcomes.append(float(outcome))

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
        "tail_dependency_share": _tail_dependency_share(outcomes),
    }


def _fold_metrics(
    df,
    folds_df: pd.DataFrame,
    tp,
    sl,
):
    if folds_df is None or folds_df.empty:
        return {
            "fold_mean_expectancy": np.nan,
            "fold_std_expectancy": np.nan,
            "fold_agreement": np.nan,
            "fold_count": 0,
        }

    d = df.sort_values("entry_ts")
    scores = []

    for _, fold in folds_df.iterrows():
        test_start = fold.get("test_start_ts")
        test_end = fold.get("test_end_ts")
        chunk = d[(d["entry_ts"] >= test_start) & (d["entry_ts"] <= test_end)]
        s = _score_candidate(chunk, tp, sl)
        if not s:
            continue
        scores.append(float(s["expectancy"]))

    if not scores:
        return {
            "fold_mean_expectancy": np.nan,
            "fold_std_expectancy": np.nan,
            "fold_agreement": np.nan,
            "fold_count": 0,
        }

    mean_score = float(np.mean(scores))
    std_score = float(np.std(scores))
    mean_sign = np.sign(mean_score)
    if mean_sign == 0:
        agreement = float(np.mean([1.0 if np.sign(s) == 0 else 0.0 for s in scores]))
    else:
        agreement = float(np.mean([1.0 if np.sign(s) == mean_sign else 0.0 for s in scores]))

    return {
        "fold_mean_expectancy": mean_score,
        "fold_std_expectancy": std_score,
        "fold_agreement": agreement,
        "fold_count": int(len(scores)),
    }


def _sample_quality_flag(n_decisive: int, fold_count: int, min_test_segments: int) -> str:
    if n_decisive < max(1, int(min_test_segments)):
        return "thin"
    if fold_count == 0:
        return "unvalidated"
    if n_decisive < max(int(min_test_segments), MIN_SAMPLE):
        return "thin"
    return "ok"


def _annotate_anchor_stability(anchor_df: pd.DataFrame) -> pd.DataFrame:
    if anchor_df.empty:
        return anchor_df

    out = anchor_df.copy().reset_index(drop=True)
    out["neighbor_consistency"] = np.nan
    out["sensitivity_penalty"] = np.nan
    out["stability_score"] = np.nan

    tp_levels = sorted(out["tp_atr"].dropna().unique().tolist())
    sl_levels = sorted(out["sl_atr"].dropna().unique().tolist())
    tp_index = {float(v): i for i, v in enumerate(tp_levels)}
    sl_index = {float(v): i for i, v in enumerate(sl_levels)}
    lookup = {(float(r.tp_atr), float(r.sl_atr)): r.expectancy_score for r in out.itertuples()}

    for idx, row in out.iterrows():
        tp = float(row["tp_atr"])
        sl = float(row["sl_atr"])
        neighbors = []

        tp_pos = tp_index.get(tp)
        sl_pos = sl_index.get(sl)
        if tp_pos is not None:
            for delta in (-1, 1):
                pos = tp_pos + delta
                if 0 <= pos < len(tp_levels):
                    key = (float(tp_levels[pos]), sl)
                    if key in lookup:
                        neighbors.append(float(lookup[key]))
        if sl_pos is not None:
            for delta in (-1, 1):
                pos = sl_pos + delta
                if 0 <= pos < len(sl_levels):
                    key = (tp, float(sl_levels[pos]))
                    if key in lookup:
                        neighbors.append(float(lookup[key]))

        current_sign = np.sign(float(row["expectancy_score"]))
        if neighbors:
            if current_sign == 0:
                consistency = float(np.mean([1.0 if np.sign(v) == 0 else 0.0 for v in neighbors]))
            else:
                consistency = float(np.mean([1.0 if np.sign(v) == current_sign else 0.0 for v in neighbors]))
            out.at[idx, "neighbor_consistency"] = consistency
        else:
            out.at[idx, "neighbor_consistency"] = np.nan

        std = row.get("fold_std_expectancy")
        if pd.isna(std):
            penalty = np.nan
        else:
            penalty = _clip01(float(std) / (abs(float(row["expectancy_score"])) + 1e-9))
        out.at[idx, "sensitivity_penalty"] = penalty

    fold_component = out["fold_agreement"].fillna(0.5)
    neighbor_component = out["neighbor_consistency"].fillna(0.5)
    tail_component = (1.0 - out["tail_dependency_share"].fillna(1.0)).clip(lower=0.0, upper=1.0)
    sensitivity_component = (1.0 - out["sensitivity_penalty"].fillna(1.0)).clip(lower=0.0, upper=1.0)

    out["stability_score"] = (
        0.40 * fold_component
        + 0.30 * neighbor_component
        + 0.20 * sensitivity_component
        + 0.10 * tail_component
    ).clip(lower=0.0, upper=1.0)

    return out

    return out

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

                fold_metrics = _fold_metrics(g, anchor_folds, tp, sl)
                n = int(score["decisive"])
                fold_count = int(fold_metrics["fold_count"])

                rows.append(
                    {
                        "anchor_id": anchor_id,
                        "tp_atr": float(tp),
                        "sl_atr": float(sl),
                        "n_decisive": n,
                        "tp_prob": score["tp_prob"],
                        "sl_prob": score["sl_prob"],
                        "expectancy_score": score["expectancy"],
                        "fold_mean_expectancy": fold_metrics["fold_mean_expectancy"],
                        "fold_std_expectancy": fold_metrics["fold_std_expectancy"],
                        "fold_agreement": fold_metrics["fold_agreement"],
                        "fold_count": fold_count,
                        "tail_dependency_share": score["tail_dependency_share"],
                        "sample_quality_flag": _sample_quality_flag(n, fold_count, min_test_segments),
                    }
                )

    out = pd.DataFrame(rows)
    folds_out = pd.DataFrame(fold_rows)

    if out.empty:
        return out, folds_out

    scored_groups = []
    for _, anchor_df in out.groupby("anchor_id", dropna=False):
        scored_groups.append(_annotate_anchor_stability(anchor_df))
    out = pd.concat(scored_groups, ignore_index=True)

    out["robust_score"] = out["expectancy_score"] * out["stability_score"].fillna(0.0)

    out = out.sort_values(
        ["anchor_id", "stability_score", "robust_score", "n_decisive"],
        ascending=[True, False, False, False],
    ).reset_index(drop=True)
    return out, folds_out
