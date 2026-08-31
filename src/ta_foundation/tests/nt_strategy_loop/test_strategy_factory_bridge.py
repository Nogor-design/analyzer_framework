from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from ta_foundation.nt_strategy_loop.strategy_factory_bridge import (
    StrategyFactoryBridgeError,
    build_with_strategy_factory,
)


def _write_factory_outputs(destination: Path, source_spec: Path, *, omit: str = "") -> None:
    destination.mkdir(parents=True, exist_ok=True)
    paths = {
        "normalized_spec": destination / "Example.strategy_spec.normalized.json",
        "ta_foundation_template": destination / "Example.ta_template.json",
        "ninjascript": destination / "Example.cs",
        "ninjascript_template": destination / "ExampleTemplate.xml",
    }
    for name, path in paths.items():
        if name != omit:
            path.write_text(name, encoding="utf-8")
    manifest_paths = {name: str(path) for name, path in paths.items() if name != omit}
    (destination / "manifest.json").write_text(
        json.dumps({"source_spec": str(source_spec), "outputs": manifest_paths}),
        encoding="utf-8",
    )


def test_build_with_strategy_factory_returns_validated_artifacts(tmp_path: Path) -> None:
    factory_root = tmp_path / "factory"
    factory_root.mkdir()
    spec = tmp_path / "strategy.json"
    spec.write_text("{}", encoding="utf-8")
    destination = tmp_path / "artifacts"
    observed: dict[str, object] = {}

    def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        observed["command"] = command
        observed["cwd"] = kwargs["cwd"]
        _write_factory_outputs(destination, spec)
        return subprocess.CompletedProcess(command, 0, "generated", "")

    result = build_with_strategy_factory(
        spec,
        destination,
        factory_root=factory_root,
        python_executable=sys.executable,
        runner=runner,
    )

    assert observed["command"] == [
        str(Path(sys.executable).resolve()),
        "-m",
        "strategy_factory.factory",
        "--spec",
        str(spec.resolve()),
        "--out-dir",
        str(destination.resolve()),
    ]
    assert observed["cwd"] == factory_root.resolve()
    assert Path(result.manifest).is_file()
    assert Path(result.ninjascript).name == "Example.cs"
    assert Path(result.ninjascript_template).name == "ExampleTemplate.xml"


def test_build_with_strategy_factory_rejects_incomplete_manifest(tmp_path: Path) -> None:
    factory_root = tmp_path / "factory"
    factory_root.mkdir()
    spec = tmp_path / "strategy.json"
    spec.write_text("{}", encoding="utf-8")
    destination = tmp_path / "artifacts"

    def runner(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        _write_factory_outputs(destination, spec, omit="ninjascript_template")
        return subprocess.CompletedProcess(command, 0, "generated", "")

    with pytest.raises(StrategyFactoryBridgeError, match="ninjascript_template"):
        build_with_strategy_factory(
            spec,
            destination,
            factory_root=factory_root,
            python_executable=sys.executable,
            runner=runner,
        )


def test_build_with_strategy_factory_reports_process_failure(tmp_path: Path) -> None:
    factory_root = tmp_path / "factory"
    factory_root.mkdir()
    spec = tmp_path / "strategy.json"
    spec.write_text("{}", encoding="utf-8")

    def runner(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 7, "", "factory failed")

    with pytest.raises(StrategyFactoryBridgeError, match="factory failed"):
        build_with_strategy_factory(
            spec,
            tmp_path / "artifacts",
            factory_root=factory_root,
            python_executable=sys.executable,
            runner=runner,
        )
