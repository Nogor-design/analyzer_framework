from ta_foundation.analysis.large_candle_excursion.target_curve import (
    TargetPoint,
    compute_target_curve_metrics,
    curve_to_dict,
)
from ta_foundation.analysis.large_candle_excursion.trade_analyzer import _target_percents_from_config, _time_split_metrics
import pandas as pd


FINE_TARGETS = [10, 15, 20, 25, 30, 35, 40]


def test_trade_target_config_merges_broad_and_fine_sweep_values() -> None:
    targets = _target_percents_from_config(
        {
            "target": {
                "percents": [20, 25, 50, 100],
                "fine_sweep": {"enabled": True, "percents": FINE_TARGETS},
            }
        }
    )

    assert targets == [10.0, 15.0, 20.0, 25.0, 30.0, 35.0, 40.0, 50.0, 100.0]


def test_fine_target_curve_flags_single_point_optimum() -> None:
    curve = compute_target_curve_metrics(
        [
            TargetPoint(10, 0.55, 100),
            TargetPoint(15, 0.56, 100),
            TargetPoint(20, 0.70, 100),
            TargetPoint(25, 0.55, 100),
            TargetPoint(30, 0.50, 100),
            TargetPoint(35, 0.48, 100),
            TargetPoint(40, 0.45, 100),
            TargetPoint(100, 0.20, 100),
        ],
        fine_sweep_targets=FINE_TARGETS,
        focus_target_pct=20,
    )

    payload = curve_to_dict(curve)
    assert payload["target_stability_label"] == "narrow_fragile_optimum"
    assert payload["fine_plateau_width"] == 1
    assert payload["neighbor_target_stability"] < 0.45
    assert payload["micro_scalp_artifact"] is True


def test_fine_target_curve_recognizes_stable_plateau() -> None:
    curve = compute_target_curve_metrics(
        [
            TargetPoint(10, 0.62, 100),
            TargetPoint(15, 0.64, 100),
            TargetPoint(20, 0.65, 100),
            TargetPoint(25, 0.64, 100),
            TargetPoint(30, 0.63, 100),
            TargetPoint(35, 0.58, 100),
            TargetPoint(40, 0.55, 100),
            TargetPoint(100, 0.45, 100),
        ],
        fine_sweep_targets=FINE_TARGETS,
        focus_target_pct=20,
    )

    payload = curve_to_dict(curve)
    assert payload["target_stability_label"] == "stable_plateau"
    assert payload["fine_plateau_width"] == 5
    assert payload["neighbor_target_stability"] >= 0.70
    assert payload["micro_scalp_artifact"] is False


def test_time_split_metrics_detect_unstable_target_edge() -> None:
    grp = pd.DataFrame(
        {
            "dt": pd.date_range("2026-01-01", periods=30, freq="min"),
            "win": [True] * 10 + [False, True] * 5 + [False] * 10,
        }
    )

    metrics = _time_split_metrics(
        grp,
        {"enabled": True, "n_splits": 3, "min_events_per_split": 5, "stable_drop_pp": 8.0},
    )

    assert metrics["time_split_available"] is True
    assert metrics["time_split_win_rates"] == [100.0, 50.0, 0.0]
    assert metrics["time_split_stability"] == 0.0


def test_target_curve_exposes_focus_time_split_stability() -> None:
    curve = compute_target_curve_metrics(
        [
            TargetPoint(10, 0.58, 90, time_split_win_rates=[58, 57, 59], time_split_stability=0.875, time_split_max_drop_pp=2),
            TargetPoint(15, 0.60, 90, time_split_win_rates=[61, 60, 59], time_split_stability=0.875, time_split_max_drop_pp=2),
            TargetPoint(20, 0.68, 90, time_split_win_rates=[90, 60, 54], time_split_stability=0.0, time_split_max_drop_pp=36),
            TargetPoint(25, 0.60, 90, time_split_win_rates=[60, 59, 61], time_split_stability=0.875, time_split_max_drop_pp=2),
        ],
        fine_sweep_targets=[10, 15, 20, 25],
        focus_target_pct=20,
    )

    payload = curve_to_dict(curve)
    assert payload["target_time_stability"] == 0.0
    assert payload["time_stability_label"] == "time_fragile"
    assert payload["focus_time_split_win_rates"] == [90, 60, 54]
