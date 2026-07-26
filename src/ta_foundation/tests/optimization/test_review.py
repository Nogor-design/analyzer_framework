from pathlib import Path
import json

from ta_foundation.optimization.evaluator import EvaluationConfig
from ta_foundation.optimization.review import write_result_review
from ta_foundation.tests.optimization.test_next_pass import _write_result_files


def test_write_result_review_outputs_intake_evaluation_and_recommendations(tmp_path: Path):
    results = tmp_path / "results"
    _write_result_files(results)

    output = write_result_review(
        results,
        tmp_path / "review",
        config=EvaluationConfig(min_percent_days_traded=0),
    )

    assert (output / "result_intake.csv").exists()
    assert (output / "evaluated_candidates.csv").exists()
    assert (output / "recommendations.md").exists()
    assert (output / "review_summary.json").exists()
    assert (output / "settings_contract_violations.csv").exists()
    summary = (output / "REVIEW_SUMMARY.md").read_text(encoding="utf-8")
    assert "Final Backtest Validation" in summary
    assert "does not create a new optimizer phase" in summary
    assert "review_summary.json" in summary
    assert "Performance Groups" not in summary
    manifest = json.loads((output / "review_manifest.json").read_text(encoding="utf-8"))
    assert manifest["review_kind"] == "final-backtest"
    assert manifest["schema_version"] == 2
    assert manifest["validation_status"] == "valid"
    assert manifest["validation_filters"]["min_trades"] == 10
    assert manifest["settings_contract"] == {"UseTrend": "false", "UseTrendReverse": "false"}
    assert manifest["artifacts"]["recommendations_csv"] == "recommendations.csv"
    assert manifest["artifacts"]["review_summary_json"] == "review_summary.json"
    assert manifest["artifacts"]["settings_contract_violations_csv"] == "settings_contract_violations.csv"
    assert manifest["settings_contract_violation_count"] == 0
    review_summary = json.loads((output / "review_summary.json").read_text(encoding="utf-8"))
    assert review_summary["validation_status"] == "valid"
    assert review_summary["counts"]["settings_contract_violations"] == 0
    assert review_summary["top_recommendation"]["rank"] == 1
    assert review_summary["performance_groups"][0]["run_ids"] == ["BotA"]


def test_write_result_review_flags_final_backtest_settings_contract_mismatch(tmp_path: Path):
    results = tmp_path / "results"
    _write_result_files(results)
    settings = results / "BotA_Settings.csv"
    settings.write_text(
        settings.read_text(encoding="utf-8").replace("UseTrend ,False", "UseTrend ,True"),
        encoding="utf-8",
    )

    output = write_result_review(
        results,
        tmp_path / "review",
        config=EvaluationConfig(min_percent_days_traded=0),
    )

    violations = (output / "settings_contract_violations.csv").read_text(encoding="utf-8")
    assert "UseTrend" in violations
    assert "final Backtest validation expects false" in violations
    manifest = json.loads((output / "review_manifest.json").read_text(encoding="utf-8"))
    assert manifest["validation_status"] == "settings_warning"
    assert manifest["settings_contract_violation_count"] == 1
    assert manifest["settings_contract"]["UseTrend"] == "false"
    review_summary = json.loads((output / "review_summary.json").read_text(encoding="utf-8"))
    assert review_summary["validation_status"] == "settings_warning"
    assert review_summary["settings_contract_violations"][0]["setting"] == "UseTrend"
    summary = (output / "REVIEW_SUMMARY.md").read_text(encoding="utf-8")
    assert "Settings contract violations: 1" in summary
    assert "Validation status: `settings_warning`" in summary
