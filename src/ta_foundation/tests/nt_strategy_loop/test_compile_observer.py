from __future__ import annotations

import csv
import json
from pathlib import Path

from ta_foundation.nt_strategy_loop.compile_observer import install_strategy_source, observation_from_status, parse_compile_errors_csv, write_observe_compile_command


def test_parse_compile_errors_csv_accepts_nt_columns(tmp_path: Path) -> None:
    path = tmp_path / "errors.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["NinjaScript File", "Error", "Code", "Line", "Column", "Source", "Raw"],
        )
        writer.writeheader()
        writer.writerow(
            {
                "NinjaScript File": "BrokenCompileSmoke.cs",
                "Error": "The name Foo does not exist in the current context",
                "Code": "CS0103",
                "Line": "22",
                "Column": "15",
                "Source": "trace.txt",
                "Raw": "raw compiler line",
            }
        )

    errors = parse_compile_errors_csv(path)

    assert len(errors) == 1
    assert errors[0].file == "BrokenCompileSmoke.cs"
    assert errors[0].line == 22
    assert errors[0].column == 15
    assert errors[0].code == "CS0103"
    assert "Foo" in errors[0].message


def test_observation_from_status_loads_errors(tmp_path: Path) -> None:
    errors_csv = tmp_path / "compile_errors.csv"
    errors_csv.write_text(
        "NinjaScript File,Error,Code,Line,Column\n"
        "Demo.cs,; expected,CS1002,7,9\n",
        encoding="utf-8",
    )
    status = {
        "runId": "compile_demo",
        "workerKind": "compile_observer",
        "state": "failed",
        "strategyName": "Demo",
        "sourceFile": r"C:\NT\Demo.cs",
        "compiled": False,
        "errorCount": 1,
        "errorsCsv": str(errors_csv),
        "errorsText": None,
        "lastError": "; expected",
        "heartbeatUtc": "2026-05-19T00:00:00Z",
        "outputRoot": str(tmp_path),
    }

    observed = observation_from_status(status, tmp_path / "nt8_status.json")

    assert not observed.ok
    assert observed.error_count == 1
    assert observed.errors[0].code == "CS1002"
    assert observed.last_error == "; expected"


def test_observation_from_status_reads_compile_block_reason(tmp_path: Path) -> None:
    status = {
        "runId": "compile_peer_block",
        "workerKind": "compile_observer",
        "state": "failed",
        "strategyName": "Demo",
        "sourceFile": r"C:\NT\Demo.cs",
        "compiled": False,
        "errorCount": 0,
        "errorsCsv": None,
        "errorsText": None,
        "lastError": "peer strategy compile error blocking SA: ; expected",
        "compileBlockReason": "peer_strategy_errors",
        "heartbeatUtc": "2026-05-19T00:00:00Z",
        "outputRoot": str(tmp_path),
    }

    observed = observation_from_status(status, tmp_path / "nt8_status.json")

    assert observed.compile_block_reason == "peer_strategy_errors"
    assert observed.last_error and observed.last_error.startswith("peer strategy compile error")


def test_observation_from_status_compile_block_reason_defaults_to_none(tmp_path: Path) -> None:
    status = {
        "runId": "compile_clean",
        "workerKind": "compile_observer",
        "state": "succeeded",
        "strategyName": "Demo",
        "sourceFile": r"C:\NT\Demo.cs",
        "compiled": True,
        "errorCount": 0,
        "errorsCsv": None,
        "errorsText": None,
        "lastError": None,
        "heartbeatUtc": "2026-05-19T00:00:00Z",
        "outputRoot": str(tmp_path),
    }

    observed = observation_from_status(status, tmp_path / "nt8_status.json")

    assert observed.compile_block_reason is None


def test_write_observe_compile_command(tmp_path: Path) -> None:
    command_path = tmp_path / "nt8_command.json"

    command = write_observe_compile_command(
        run_id="compile_001",
        source_file=tmp_path / "Demo.cs",
        strategy_name="Demo",
        output_dir=tmp_path / "errors",
        installed_sha256="abc123",
        command_path=command_path,
        timeout_seconds=12,
        wait_for_quiet_seconds=2,
    )

    payload = json.loads(command_path.read_text(encoding="utf-8"))
    assert payload == command
    assert payload["action"] == "ObserveCompile"
    assert payload["installedSha256"] == "abc123"
    assert payload["timeoutSeconds"] == 12
    assert payload["waitForQuietSeconds"] == 2


def test_install_strategy_source_copies_to_nt_strategy_folder(tmp_path: Path) -> None:
    source = tmp_path / "Demo.cs"
    source.write_text("namespace NinjaTrader.NinjaScript.Strategies { public class Demo {} }\n", encoding="utf-8")
    nt_docs = tmp_path / "ntdocs"
    compile_root = tmp_path / "compile"

    installed = install_strategy_source(
        source,
        nt_documents_dir=nt_docs,
        compile_root=compile_root,
        overwrite=False,
    )

    target = nt_docs / "bin" / "Custom" / "Strategies" / "Demo.cs"
    staging = compile_root / "staging" / "Demo.cs"
    assert target.read_text(encoding="utf-8") == source.read_text(encoding="utf-8")
    assert staging.read_text(encoding="utf-8") == source.read_text(encoding="utf-8")
    assert len(installed.sha256) == 64
