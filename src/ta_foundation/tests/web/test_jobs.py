from __future__ import annotations

import time
from pathlib import Path

import pytest
import yaml

from ta_foundation.web.jobs import JobManager
from ta_foundation.web.jobs import discover_generated_artifacts
from ta_foundation.web.prediction_jobs import build_prediction_command_args
from ta_foundation.web.report_builder import (
    build_report_command_args,
    load_report_yaml,
    save_report_yaml,
    validate_report_run_request,
)


def _valid_report_yaml() -> str:
    return yaml.safe_dump(
        {
            "report": {
                "title": "UI Report",
                "output_filename": "ui_report.html",
                "timezone": "America/Denver",
            },
            "sections": [{"id": "comparison_overview"}],
        },
        sort_keys=False,
    )


def test_save_report_yaml_validates_and_writes_user_path(tmp_path):
    path = tmp_path / "configs" / "ui_report.yaml"

    saved = save_report_yaml({"report_yaml": _valid_report_yaml(), "save_path": str(path)})

    assert saved.validation["ok"] is True
    assert saved.path == str(path)
    assert path.read_text(encoding="utf-8") == _valid_report_yaml()


def test_save_report_yaml_rejects_unknown_section_before_write(tmp_path):
    path = tmp_path / "bad.yaml"
    saved = save_report_yaml(
        {
            "save_path": str(path),
            "report_yaml": "report:\n  title: Bad\nsections:\n  - id: no_such_section\n",
        }
    )

    assert saved.validation["ok"] is False
    assert "Unknown report section id: no_such_section" in saved.validation["errors"]
    assert not path.exists()


def test_save_report_yaml_requires_yaml_extension(tmp_path):
    path = tmp_path / "ui_report.txt"

    saved = save_report_yaml({"report_yaml": _valid_report_yaml(), "save_path": str(path)})

    assert saved.validation["ok"] is False
    assert "must end in .yaml or .yml" in saved.validation["errors"][0]
    assert not path.exists()


def test_load_report_yaml_reads_and_validates_user_path(tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    path = tmp_path / "configs" / "ui_report.yaml"
    path.parent.mkdir()
    path.write_text(_valid_report_yaml(), encoding="utf-8")

    loaded = load_report_yaml(
        {
            "load_path": str(path),
            "input_folder": str(input_dir),
            "output_folder": str(tmp_path / "output"),
            "report_config_path": str(path),
            "capability_ids": ["core_comparison_report"],
        }
    )

    assert loaded.validation["ok"] is True
    assert loaded.report_yaml == _valid_report_yaml()
    assert loaded.path == str(path)
    assert str(path) in loaded.command_preview


def test_load_report_yaml_rejects_non_yaml_extension(tmp_path):
    path = tmp_path / "ui_report.txt"
    path.write_text(_valid_report_yaml(), encoding="utf-8")

    loaded = load_report_yaml({"load_path": str(path)})

    assert loaded.validation["ok"] is False
    assert "must end in .yaml or .yml" in loaded.validation["errors"][0]
    assert loaded.report_yaml == ""


def test_report_run_validation_reads_saved_yaml_and_checks_inputs(tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    config_path = tmp_path / "report.yaml"
    config_path.write_text(_valid_report_yaml(), encoding="utf-8")

    validation = validate_report_run_request(
        {
            "input_folder": str(input_dir),
            "output_folder": str(tmp_path / "output"),
            "report_config_path": str(config_path),
            "capability_ids": ["core_comparison_report"],
        }
    )

    assert validation["ok"] is True


def test_report_command_args_target_existing_cli_entry_point(tmp_path):
    args = build_report_command_args(
        {
            "input_folder": str(tmp_path / "input"),
            "output_folder": str(tmp_path / "output"),
            "market_data_folder": str(tmp_path / "market"),
            "recursive": True,
            "include_run_images": True,
            "no_tick_data": True,
        },
        tmp_path / "report.yaml",
    )

    assert args[1:4] == ["-m", "ta_foundation.cli.main", "--input"]
    assert "--report-config" in args
    assert str(tmp_path / "report.yaml") in args
    assert "--no-tick-data" in args


class _FakeProcess:
    returncode = 0

    def communicate(self):
        return ("hello from job\n", None)


def test_job_manager_dispatches_and_records_output():
    manager = JobManager(popen_factory=lambda *args, **kwargs: _FakeProcess())

    job = manager.start(kind="report", command=["python", "-m", "example"])

    for _ in range(20):
        current = manager.get(job.id)
        if current and current.status != "queued" and current.finished_at:
            break
        time.sleep(0.01)

    current = manager.get(job.id)
    assert current is not None
    assert current.status == "succeeded"
    assert current.output == "hello from job\n"


def test_job_manager_discovers_report_artifacts(tmp_path):
    cards = tmp_path / "cards"
    cards.mkdir()
    card = cards / "Run_Card.png"
    card.write_text("png", encoding="utf-8")
    output = "\n".join(
        [
            f"Wrote: {tmp_path / 'report.html'}",
            f"Wrote: {tmp_path / 'manifest.json'}",
            f"Wrote: {tmp_path / 'report_summary.txt'}",
            f"  Wrote per-rule template: rule1 Example -> {tmp_path / 'rule1.xml'}",
            f"[ta_foundation] Exported 1 exec cards to: {cards}",
        ]
    )

    artifacts = discover_generated_artifacts(output)
    by_name = {artifact.label: artifact for artifact in artifacts}

    assert [artifact.kind for artifact in artifacts[:4]] == ["html", "json", "txt", "xml"]
    assert any(artifact.path == str(card.resolve()) for artifact in artifacts)
    assert by_name["Manifest"].kind == "json"
    assert by_name["Summary report_summary.txt"].kind == "txt"
    assert by_name["Template rule1.xml"].kind == "xml"
    assert by_name["Card Run_Card.png"].kind == "png"


def test_job_artifact_discovery_infers_report_outputs_from_command(tmp_path):
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    config_path = tmp_path / "reports.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "reports": [
                    {
                        "report": {
                            "title": "One",
                            "output_filename": "one.html",
                        },
                        "sections": [{"id": "comparison_overview"}],
                    },
                    {
                        "report": {
                            "title": "Two",
                            "output_filename": "two.html",
                        },
                        "sections": [{"id": "run_kpi_cards"}],
                    },
                ]
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    artifacts = discover_generated_artifacts(
        "",
        command=[
            "python",
            "-m",
            "ta_foundation.cli.main",
            "--input",
            str(tmp_path / "input"),
            "--output",
            str(output_dir),
            "--report-config",
            str(config_path),
        ],
    )

    paths = {Path(artifact.path).name for artifact in artifacts}
    assert {"one.html", "two.html", "manifest.json", "reports.yaml"} <= paths
    assert any(artifact.kind == "yaml" and artifact.label == "YAML reports.yaml" for artifact in artifacts)


def test_job_artifact_discovery_infers_default_exec_cards_dir(tmp_path):
    output_dir = tmp_path / "outputs"
    cards_dir = output_dir / "cards"
    cards_dir.mkdir(parents=True)
    card = cards_dir / "Run_Card.png"
    card.write_text("png", encoding="utf-8")

    artifacts = discover_generated_artifacts(
        "",
        command=[
            "python",
            "-m",
            "ta_foundation.cli.main",
            "--output",
            str(output_dir),
            "--export-exec-cards-png",
        ],
    )

    assert any(artifact.path == str(cards_dir.resolve()) and artifact.is_directory for artifact in artifacts)
    assert any(artifact.path == str(card.resolve()) and artifact.kind == "png" for artifact in artifacts)


def test_prediction_command_args_use_existing_prediction_entry_points(tmp_path):
    config_path = tmp_path / "prediction.yaml"
    config_path.write_text("instrument: NQ\ncontract: 06-26\nmarket_data: data\n", encoding="utf-8")

    args = build_prediction_command_args(
        {"prediction_job_type": "run_prediction", "config_path": str(config_path), "dry_run": True}
    )

    assert args[1:4] == ["-m", "ta_foundation.prediction.run_prediction", "--config"]
    assert "--dry-run" in args


def test_prediction_command_args_validate_horizon_backtest_inputs(tmp_path):
    bars = tmp_path / "NQ 06-26.Last.txt"
    bars.write_text("header\n", encoding="utf-8")

    args = build_prediction_command_args(
        {
            "prediction_job_type": "backtest_horizon",
            "minute_bars_file": str(bars),
            "store_dir": str(tmp_path / "horizon"),
            "timeframes": "5m",
            "horizons": "3",
            "print_report": True,
        }
    )

    assert args[1:4] == ["-m", "ta_foundation.prediction.backtest_horizon_predictions", "--minute-bars-file"]
    assert "--print-report" in args


def test_report_run_endpoint_starts_job_with_saved_yaml(tmp_path, monkeypatch):
    flask = pytest.importorskip("flask")
    assert flask is not None

    from ta_foundation.web import app as web_app
    from ta_foundation.web.jobs import JobRecord

    class _FakeManager:
        def start(self, *, kind, command):
            return JobRecord(id="job-1", kind=kind, command=command, status="running")

        def get(self, job_id):
            return JobRecord(id=job_id, kind="report", command=["x"], status="succeeded", output="done")

        def list(self):
            return []

    input_dir = tmp_path / "input"
    input_dir.mkdir()
    config_path = tmp_path / "report.yaml"
    config_path.write_text(_valid_report_yaml(), encoding="utf-8")

    monkeypatch.setattr(web_app, "_job_manager", _FakeManager())
    app = web_app.create_app()
    app.testing = True

    with app.test_client() as client:
        response = client.post(
            "/api/report-builder/run",
            json={
                "input_folder": str(input_dir),
                "output_folder": str(tmp_path / "output"),
                "report_config_path": str(config_path),
                "capability_ids": ["core_comparison_report"],
            },
        )

    payload = response.get_json()
    assert response.status_code == 200
    assert payload["job"]["kind"] == "report"
    assert "ta_foundation.cli.main" in payload["job"]["command"]


def test_report_run_endpoint_saves_preview_yaml_before_starting_job(tmp_path, monkeypatch):
    flask = pytest.importorskip("flask")
    assert flask is not None

    from ta_foundation.web import app as web_app
    from ta_foundation.web.jobs import JobRecord

    class _FakeManager:
        def start(self, *, kind, command):
            return JobRecord(id="job-1", kind=kind, command=command, status="running")

        def get(self, job_id):
            return None

        def list(self):
            return []

    input_dir = tmp_path / "input"
    input_dir.mkdir()
    config_path = tmp_path / "generated" / "edited_report.yaml"
    edited_yaml = yaml.safe_dump(
        {
            "report": {
                "title": "Edited In Preview",
                "output_filename": "edited.html",
                "timezone": "America/Denver",
            },
            "sections": [{"id": "comparison_overview"}],
        },
        sort_keys=False,
    )

    monkeypatch.setattr(web_app, "_job_manager", _FakeManager())
    app = web_app.create_app()
    app.testing = True

    with app.test_client() as client:
        response = client.post(
            "/api/report-builder/run",
            json={
                "input_folder": str(input_dir),
                "output_folder": str(tmp_path / "output"),
                "report_config_path": str(config_path),
                "report_yaml": edited_yaml,
                "capability_ids": ["core_comparison_report"],
            },
        )

    payload = response.get_json()
    assert response.status_code == 200
    assert config_path.read_text(encoding="utf-8") == edited_yaml
    assert str(config_path) in payload["job"]["command"]


def test_report_load_endpoint_loads_saved_yaml(tmp_path, monkeypatch):
    flask = pytest.importorskip("flask")
    assert flask is not None

    from ta_foundation.web import app as web_app

    input_dir = tmp_path / "input"
    input_dir.mkdir()
    config_path = tmp_path / "report.yaml"
    config_path.write_text(_valid_report_yaml(), encoding="utf-8")

    monkeypatch.setattr(web_app, "_job_manager", None)
    app = web_app.create_app()
    app.testing = True

    with app.test_client() as client:
        response = client.post(
            "/api/report-builder/load",
            json={
                "load_path": str(config_path),
                "input_folder": str(input_dir),
                "output_folder": str(tmp_path / "output"),
                "report_config_path": str(config_path),
                "capability_ids": ["core_comparison_report"],
            },
        )

    payload = response.get_json()
    assert response.status_code == 200
    assert payload["report_yaml"] == _valid_report_yaml()
    assert payload["validation"]["ok"] is True


def test_artifact_endpoint_only_serves_web_job_artifacts(tmp_path, monkeypatch):
    flask = pytest.importorskip("flask")
    assert flask is not None

    from ta_foundation.web import app as web_app
    from ta_foundation.web.jobs import GeneratedArtifact, JobRecord

    allowed = tmp_path / "report.html"
    allowed.write_text("<html></html>", encoding="utf-8")
    blocked = tmp_path / "secret.txt"
    blocked.write_text("nope", encoding="utf-8")

    class _FakeManager:
        def is_allowed_artifact(self, path):
            return str(Path(path).resolve()) == str(allowed.resolve())

        def start(self, *, kind, command):
            return JobRecord(id="job-1", kind=kind, command=command)

        def get(self, job_id):
            return JobRecord(
                id=job_id,
                kind="report",
                command=["x"],
                status="succeeded",
                artifacts=[GeneratedArtifact(path=str(allowed), kind="html", label="HTML report.html")],
            )

        def list(self):
            return []

    monkeypatch.setattr(web_app, "_job_manager", _FakeManager())
    app = web_app.create_app()
    app.testing = True

    with app.test_client() as client:
        ok_response = client.get("/api/artifact", query_string={"path": str(allowed)})
        blocked_response = client.get("/api/artifact", query_string={"path": str(blocked)})

    assert ok_response.status_code == 200
    assert blocked_response.status_code == 403
