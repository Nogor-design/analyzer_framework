from __future__ import annotations

import pandas as pd

from ta_foundation.analysis.ma_structure.tp_sl_engine import score_tp_sl_candidates


def _segments() -> pd.DataFrame:
    idx = pd.date_range("2025-02-03 08:00", periods=12, freq="min", tz="America/Denver")
    rows = []
    for i, ts in enumerate(idx):
        rows.append(
            {
                "segment_id": f"seg_{i}",
                "anchor_id": "EMA_21_close",
                "entry_ts": ts,
                "minutes_held": 5.0,
                "censored": False,
            }
        )
    return pd.DataFrame(rows)


def _path_stats() -> pd.DataFrame:
    mfe = [0.8, 1.0, 1.2, 1.4, 1.6, 1.8, 0.9, 1.1, 1.3, 1.5, 1.7, 1.9]
    mae = [0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.45, 0.55, 0.65, 0.75, 0.85, 0.95]
    before = [True, True, True, False, True, False, True, True, False, True, False, True]
    return pd.DataFrame(
        {
            "segment_id": [f"seg_{i}" for i in range(12)],
            "mfe_atr": mfe,
            "mae_atr": mae,
            "mfe_before_mae": before,
        }
    )


def test_score_tp_sl_candidates_adds_stability_metrics() -> None:
    candidates, folds = score_tp_sl_candidates(
        _segments(),
        _path_stats(),
        tp_grid=[0.8, 1.0, 1.2],
        sl_grid=[0.4, 0.6, 0.8],
        fold_mode="blocked_kfold",
        min_train_segments=6,
        min_test_segments=3,
    )

    assert not candidates.empty
    assert not folds.empty
    assert {"fold_agreement", "neighbor_consistency", "tail_dependency_share", "sensitivity_penalty", "stability_score"}.issubset(candidates.columns)
    assert candidates["stability_score"].between(0.0, 1.0).all()
    assert candidates["fold_count"].ge(0).all()
    assert candidates["sample_quality_flag"].isin(["ok", "thin", "unvalidated"]).all()


def test_score_tp_sl_candidates_sorts_best_candidate_by_stability_then_robust_score() -> None:
    candidates, _ = score_tp_sl_candidates(
        _segments(),
        _path_stats(),
        tp_grid=[0.8, 1.0, 1.2],
        sl_grid=[0.4, 0.6, 0.8],
        fold_mode="blocked_kfold",
        min_train_segments=6,
        min_test_segments=3,
    )

    top = candidates.iloc[0]
    assert top["stability_score"] >= candidates["stability_score"].iloc[-1]
    assert pd.notna(top["robust_score"])