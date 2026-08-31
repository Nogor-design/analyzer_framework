"""Bounded adapter from ta_foundation to the external Strategy Factory.

This module only realizes a canonical Strategy Factory spec into proposed
artifacts. It does not install NinjaScript, contact NinjaTrader, run the
optimizer, or make a promotion decision. Those responsibilities remain with
the existing nt_strategy_loop commands.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Sequence


DEFAULT_FACTORY_ROOT = Path(
    os.environ.get("NINJATRADER_STRATEGY_FACTORY_ROOT", r"D:\ninjatrader-strategy-factory")
)
EXPECTED_OUTPUTS = (
    "normalized_spec",
    "ta_foundation_template",
    "ninjascript",
    "ninjascript_template",
)


class StrategyFactoryBridgeError(RuntimeError):
    """Raised when the external factory cannot produce a valid artifact set."""


@dataclass(frozen=True)
class StrategyFactoryBuild:
    factory_root: str
    source_spec: str
    output_dir: str
    manifest: str
    normalized_spec: str
    ta_foundation_template: str
    ninjascript: str
    ninjascript_template: str

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


Runner = Callable[..., subprocess.CompletedProcess[str]]


def build_with_strategy_factory(
    spec_path: str | Path,
    output_dir: str | Path,
    *,
    factory_root: str | Path = DEFAULT_FACTORY_ROOT,
    python_executable: str | Path | None = None,
    timeout_seconds: float = 120.0,
    runner: Runner = subprocess.run,
) -> StrategyFactoryBuild:
    """Generate the factory artifact set and validate its manifest contract."""
    root = Path(factory_root).resolve()
    source_spec = Path(spec_path).resolve()
    destination = Path(output_dir).resolve()
    python = Path(python_executable).resolve() if python_executable else _default_factory_python(root)

    if not root.is_dir():
        raise StrategyFactoryBridgeError(f"Strategy Factory root does not exist: {root}")
    if not source_spec.is_file():
        raise StrategyFactoryBridgeError(f"Strategy Factory spec does not exist: {source_spec}")
    if not python.is_file():
        raise StrategyFactoryBridgeError(f"Strategy Factory Python executable does not exist: {python}")

    destination.mkdir(parents=True, exist_ok=True)
    command = [
        str(python),
        "-m",
        "strategy_factory.factory",
        "--spec",
        str(source_spec),
        "--out-dir",
        str(destination),
    ]
    environment = os.environ.copy()
    environment.setdefault("PYTHONUTF8", "1")
    completed = runner(
        command,
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_seconds,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "no process output"
        raise StrategyFactoryBridgeError(
            f"Strategy Factory exited with code {completed.returncode}: {detail}"
        )

    manifest_path = destination / "manifest.json"
    manifest = _load_manifest(manifest_path)
    outputs = manifest.get("outputs")
    if not isinstance(outputs, dict):
        raise StrategyFactoryBridgeError(f"Factory manifest has no outputs object: {manifest_path}")

    resolved_outputs: dict[str, str] = {}
    for name in EXPECTED_OUTPUTS:
        raw_path = outputs.get(name)
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise StrategyFactoryBridgeError(
                f"Factory manifest is missing required output {name!r}: {manifest_path}"
            )
        path = Path(raw_path)
        if not path.is_absolute():
            path = (root / path).resolve()
        if not path.is_file():
            raise StrategyFactoryBridgeError(f"Factory output {name!r} does not exist: {path}")
        resolved_outputs[name] = str(path)

    return StrategyFactoryBuild(
        factory_root=str(root),
        source_spec=str(source_spec),
        output_dir=str(destination),
        manifest=str(manifest_path),
        normalized_spec=resolved_outputs["normalized_spec"],
        ta_foundation_template=resolved_outputs["ta_foundation_template"],
        ninjascript=resolved_outputs["ninjascript"],
        ninjascript_template=resolved_outputs["ninjascript_template"],
    )


def _default_factory_python(root: Path) -> Path:
    configured = os.environ.get("NINJATRADER_STRATEGY_FACTORY_PYTHON")
    if configured:
        return Path(configured).resolve()
    if os.name == "nt":
        return (root / ".venv" / "Scripts" / "python.exe").resolve()
    return (root / ".venv" / "bin" / "python").resolve()


def _load_manifest(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise StrategyFactoryBridgeError(f"Strategy Factory did not write its manifest: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StrategyFactoryBridgeError(f"Could not read Strategy Factory manifest {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise StrategyFactoryBridgeError(f"Strategy Factory manifest must be a JSON object: {path}")
    return payload


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate proposed NinjaScript artifacts through the external Strategy Factory."
    )
    parser.add_argument("--spec", required=True, help="Canonical Strategy Factory spec JSON.")
    parser.add_argument("--out-dir", required=True, help="Destination for the generated artifact set.")
    parser.add_argument("--factory-root", default=str(DEFAULT_FACTORY_ROOT))
    parser.add_argument("--factory-python", default="")
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args(argv)
    result = build_with_strategy_factory(
        args.spec,
        args.out_dir,
        factory_root=args.factory_root,
        python_executable=args.factory_python or None,
        timeout_seconds=args.timeout_seconds,
    )
    print(json.dumps(result.as_dict(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
