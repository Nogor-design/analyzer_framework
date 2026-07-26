from __future__ import annotations

import csv
from pathlib import Path

from ta_foundation.web.optimizer_session import OptimizerSession
from ta_foundation.web.optimizer_weekly_coverage_package import (
    WeeklyCoverageConfig,
    build_weekly_coverage_package,
)


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _template_xml(**values: object) -> str:
    defaults = {
        "StartTimeH": 0,
        "StartTimeM": 0,
        "DurationTimeH": 4,
        "DurationTimeM": 0,
        "averageFast": 5,
        "averageSlow": 100,
        "averageTrend": 300,
        "UseTrend": "false",
        "UseTrendReverse": "false",
        "ProfitStop": 10000,
        "LossStop": 10000,
        "MaxTrades": 500,
        "Contracts": 1,
        "MaxStop": 150,
        "UseMaxStop": "true",
        "MaxTPRatio": 1.5,
        "UseMaxTP": "true",
        "Long": "true",
        "Short": "false",
        "Reverse": "false",
        "UseKill": "false",
        "KillProfitStop": 800,
        "KillLossStop": 400,
    }
    defaults.update(values)
    body = "\n".join(f"      <{key}>{value}</{key}>" for key, value in defaults.items())
    return (
        "<?xml version=\"1.0\" encoding=\"utf-8\"?>\n"
        "<StrategyTemplate>\n"
        "  <Strategy>\n"
        "    <PantheonMasterBotV01TesterV2>\n"
        f"{body}\n"
        "    </PantheonMasterBotV01TesterV2>\n"
        "  </Strategy>\n"
        "</StrategyTemplate>\n"
    )


def _seed_final_backtest_fixture(session_dir: Path) -> None:
    review = (
        session_dir
        / "deployment_package"
        / "final_backtest_handoff"
        / "final_backtest_review"
    )
    templates = (
        session_dir
        / "deployment_package"
        / "final_backtest_handoff"
        / "named_backtest_templates"
        / "recipe"
    )
    templates.mkdir(parents=True, exist_ok=True)

    intake_rows = [
        {
            "run_id": "F_001",
            "total_net_profit": 1000,
            "profit_factor": 2.5,
            "max_drawdown": -500,
            "trades": 10,
            "percent_days_traded": 30,
            "start_hour": 0,
            "duration_hours": 4,
            "reverse": "False",
            "average_fast": 5,
            "average_slow": 100,
            "max_stop": 150,
            "max_tp_ratio": 1.5,
            "profit_stop": 10000,
            "loss_stop": 10000,
            "max_trades": 500,
            "long_enabled": "True",
            "short_enabled": "False",
        },
        {
            "run_id": "F_002",
            "total_net_profit": 900,
            "profit_factor": 2.4,
            "max_drawdown": -500,
            "trades": 10,
            "percent_days_traded": 30,
            "start_hour": 0,
            "duration_hours": 4,
            "reverse": "False",
            "average_fast": 5,
            "average_slow": 100,
            "max_stop": 150,
            "max_tp_ratio": 1.5,
            "profit_stop": 10000,
            "loss_stop": 10000,
            "max_trades": 500,
            "long_enabled": "True",
            "short_enabled": "False",
        },
        {
            "run_id": "F_003",
            "total_net_profit": 600,
            "profit_factor": 1.8,
            "max_drawdown": -300,
            "trades": 2,
            "percent_days_traded": 20,
            "start_hour": 0,
            "duration_hours": 4,
            "reverse": "False",
            "average_fast": 5,
            "average_slow": 100,
            "max_stop": 150,
            "max_tp_ratio": 1.5,
            "profit_stop": 10000,
            "loss_stop": 10000,
            "max_trades": 500,
            "long_enabled": "False",
            "short_enabled": "True",
        },
    ]
    evaluated_rows = [
        {"run_id": "F_001", "status": "pass", "score": 80},
        {"run_id": "F_002", "status": "pass", "score": 79},
        {"run_id": "F_003", "status": "pass", "score": 70},
    ]
    _write_csv(review / "result_intake.csv", intake_rows)
    _write_csv(review / "evaluated_candidates.csv", evaluated_rows)
    _write_csv(review / "recommendations.csv", [])

    templates.joinpath("F_001_StartTimeH_00.xml").write_text(_template_xml(), encoding="utf-8")
    templates.joinpath("F_002_StartTimeH_00.xml").write_text(_template_xml(), encoding="utf-8")
    templates.joinpath("F_003_StartTimeH_00.xml").write_text(
        _template_xml(Long="false", Short="true"),
        encoding="utf-8",
    )


def test_weekly_coverage_package_drops_exact_duplicate_but_keeps_operational_difference(tmp_path: Path):
    session_dir = tmp_path / "opt_weekly"
    session_dir.mkdir()
    _seed_final_backtest_fixture(session_dir)
    session = OptimizerSession(session_dir)

    result = build_weekly_coverage_package(
        session,
        config=WeeklyCoverageConfig(start_hours=[0], slow_ma_values=[100], target_per_lane=2),
    )

    assert result.validated_template_count == 2
    assert result.duplicate_drop_count == 1
    assert result.full_lane_count == 1

    manifest = _read_csv(session_dir / "deployment_package" / "weekly_coverage_package" / "data" / "operationally_diverse_validated_selection.csv")
    assert [row["run_id"] for row in manifest] == ["F_001", "F_003"]
    assert {row["direction_shape"] for row in manifest} == {"long_only", "short_only"}

    audit = _read_csv(session_dir / "deployment_package" / "weekly_coverage_package" / "data" / "operational_diversity_duplicate_audit.csv")
    assert [row["dropped_run_id"] for row in audit] == ["F_002"]
    assert audit[0]["drop_reason"] == "exact_parameter_duplicate"

    assert (session_dir / "deployment_package" / "weekly_coverage_package.zip").exists()
    assert "Long/Short direction flags" in Path(result.report_path).read_text(encoding="utf-8")


def _seed_empty_lane_fixture(session_dir: Path) -> None:
    """Lane (0,False,100) has a passing candidate; lane (4,False,100) has only a
    rejected candidate so it must be covered by a best-effort fallback."""
    review = session_dir / "deployment_package" / "final_backtest_handoff" / "final_backtest_review"
    templates = session_dir / "deployment_package" / "final_backtest_handoff" / "named_backtest_templates" / "recipe"
    templates.mkdir(parents=True, exist_ok=True)

    intake_rows = [
        {
            "run_id": "F_001", "total_net_profit": 1000, "profit_factor": 2.5, "max_drawdown": -500,
            "trades": 10, "percent_days_traded": 30, "start_hour": 0, "duration_hours": 4,
            "reverse": "False", "average_fast": 5, "average_slow": 100, "max_stop": 150,
            "max_tp_ratio": 1.5, "profit_stop": 10000, "loss_stop": 10000, "max_trades": 500,
            "long_enabled": "True", "short_enabled": "False",
        },
        {
            "run_id": "F_010", "total_net_profit": 200, "profit_factor": 1.1, "max_drawdown": -900,
            "trades": 3, "percent_days_traded": 8, "start_hour": 4, "duration_hours": 4,
            "reverse": "False", "average_fast": 5, "average_slow": 100, "max_stop": 150,
            "max_tp_ratio": 1.5, "profit_stop": 10000, "loss_stop": 10000, "max_trades": 500,
            "long_enabled": "True", "short_enabled": "False",
        },
    ]
    evaluated_rows = [
        {"run_id": "F_001", "status": "pass", "score": 80},
        {"run_id": "F_010", "status": "reject", "score": 20},
    ]
    _write_csv(review / "result_intake.csv", intake_rows)
    _write_csv(review / "evaluated_candidates.csv", evaluated_rows)
    _write_csv(review / "recommendations.csv", [])
    templates.joinpath("F_001_StartTimeH_00.xml").write_text(_template_xml(), encoding="utf-8")
    templates.joinpath("F_010_StartTimeH_04.xml").write_text(_template_xml(StartTimeH=4), encoding="utf-8")


def test_empty_lane_gets_best_effort_fallback(tmp_path: Path):
    session_dir = tmp_path / "opt_weekly_fb"
    session_dir.mkdir()
    _seed_empty_lane_fixture(session_dir)
    session = OptimizerSession(session_dir)

    result = build_weekly_coverage_package(
        session,
        config=WeeklyCoverageConfig(start_hours=[0, 4], slow_ma_values=[100], target_per_lane=2),
    )

    # The rejected lane (04) gets exactly one fallback; the passing lane (00) does not.
    assert result.fallback_template_count == 1
    # 2 lanes total, both now covered (one validated, one fallback).
    assert result.covered_lane_count == 2

    pkg = session_dir / "deployment_package" / "weekly_coverage_package"
    fb = _read_csv(pkg / "data" / "best_effort_fallback_selection.csv")
    assert [row["run_id"] for row in fb] == ["F_010"]
    assert "fallback" in fb[0]["selection_reasons"]
    # Fallback template copied into its own folder, separate from validated.
    assert (pkg / "best_effort_fallback_named_templates").exists()
    assert any((pkg / "best_effort_fallback_named_templates").iterdir())

    report = Path(result.report_path).read_text(encoding="utf-8")
    assert "Best-Effort Fallbacks" in report


def _seed_close_param_fixture(session_dir: Path) -> None:
    """One lane, same direction, max_stop 200 vs 210 (close) plus a short-only.
    The close pair should collapse; the short-only should survive."""
    review = session_dir / "deployment_package" / "final_backtest_handoff" / "final_backtest_review"
    templates = session_dir / "deployment_package" / "final_backtest_handoff" / "named_backtest_templates" / "recipe"
    templates.mkdir(parents=True, exist_ok=True)

    def row(run_id, stop, long_en, short_en, net):
        return {
            "run_id": run_id, "total_net_profit": net, "profit_factor": 2.0, "max_drawdown": -500,
            "trades": 10, "percent_days_traded": 30, "start_hour": 0, "duration_hours": 4,
            "reverse": "False", "average_fast": 5, "average_slow": 100, "max_stop": stop,
            "max_tp_ratio": 1.5, "profit_stop": 10000, "loss_stop": 10000, "max_trades": 500,
            "long_enabled": long_en, "short_enabled": short_en,
        }

    intake_rows = [
        row("F_001", 200, "True", "False", 1000),
        row("F_002", 210, "True", "False", 950),   # close stop, same direction -> collapse
        row("F_003", 200, "False", "True", 800),   # different direction -> kept
    ]
    _write_csv(review / "result_intake.csv", intake_rows)
    _write_csv(review / "evaluated_candidates.csv", [
        {"run_id": "F_001", "status": "pass", "score": 80},
        {"run_id": "F_002", "status": "pass", "score": 78},
        {"run_id": "F_003", "status": "pass", "score": 70},
    ])
    _write_csv(review / "recommendations.csv", [])
    templates.joinpath("F_001_StartTimeH_00.xml").write_text(_template_xml(MaxStop=200), encoding="utf-8")
    templates.joinpath("F_002_StartTimeH_00.xml").write_text(_template_xml(MaxStop=210), encoding="utf-8")
    templates.joinpath("F_003_StartTimeH_00.xml").write_text(_template_xml(MaxStop=200, Long="false", Short="true"), encoding="utf-8")


def test_close_numeric_params_collapse_but_direction_is_kept(tmp_path: Path):
    session_dir = tmp_path / "opt_band"
    session_dir.mkdir()
    _seed_close_param_fixture(session_dir)
    session = OptimizerSession(session_dir)

    result = build_weekly_coverage_package(
        session,
        config=WeeklyCoverageConfig(start_hours=[0], slow_ma_values=[100], target_per_lane=3, stop_band=50.0),
    )

    # F_001 (long) + F_003 (short) kept; F_002 collapsed into F_001's banded shape.
    assert result.validated_template_count == 2
    pkg = session_dir / "deployment_package" / "weekly_coverage_package"
    kept = {r["run_id"] for r in _read_csv(pkg / "data" / "operationally_diverse_validated_selection.csv")}
    assert kept == {"F_001", "F_003"}
    audit = _read_csv(pkg / "data" / "operational_diversity_duplicate_audit.csv")
    assert any(r["dropped_run_id"] == "F_002" and r["drop_reason"] == "same_operational_shape" for r in audit)


def test_from_session_reads_lane_grid_from_recipe_base_matrix(tmp_path: Path):
    import json
    from ta_foundation.web.optimizer_weekly_coverage_package import WeeklyCoverageConfig

    session_dir = tmp_path / "opt_grid"
    session_dir.mkdir()
    recipe = {
        "base_matrix": [
            {"param": "StartTimeH", "role": "matrix_axis", "values": [0, 4, 8]},
            {"param": "averageSlow", "role": "matrix_axis", "values": [200, 20, 50, 200]},
        ],
        "stages": [],
    }
    (session_dir / "recipe.json").write_text(json.dumps(recipe), encoding="utf-8")
    session = OptimizerSession(session_dir)

    cfg = WeeklyCoverageConfig.from_session(session)
    # Sorted + deduped, taken from the actual swept axis (not the widened default).
    assert cfg.slow_ma_values == [20, 50, 200]
    assert cfg.start_hours == [0, 4, 8]


def test_from_session_prefers_explicit_coverage_grid(tmp_path: Path):
    import json
    from ta_foundation.web.optimizer_weekly_coverage_package import WeeklyCoverageConfig

    session_dir = tmp_path / "opt_cov"
    session_dir.mkdir()
    recipe = {
        "base_matrix": [
            {"param": "averageSlow", "role": "matrix_axis", "values": [999]},
        ],
        "stages": [
            {"selection": {"coverage_grid": {"StartTimeH": [12], "averageSlow": [100, 300]}}},
        ],
    }
    (session_dir / "recipe.json").write_text(json.dumps(recipe), encoding="utf-8")
    session = OptimizerSession(session_dir)

    cfg = WeeklyCoverageConfig.from_session(session)
    assert cfg.slow_ma_values == [100, 300]  # coverage_grid wins over base_matrix
    assert cfg.start_hours == [12]


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))
