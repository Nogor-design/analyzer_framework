from __future__ import annotations

"""
Bridge from /optimizer to the external ``template-namer`` CLI.

The naming project lives at ``D:\\templateNaming`` and exposes:

    template-namer rename --input-dir <in> --output-dir <out> --market NQ

When the executable isn't on PATH, fall back to running the module via the
project's own Python:

    python -m template_naming.cli rename --input-dir <in> --output-dir <out>

The bridge is intentionally thin — it shells out, captures stdout/stderr,
and returns the list of XML files written to the output directory.
"""

import shutil
import subprocess
import sys
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Sequence


DEFAULT_TEMPLATE_NAMING_DIR = Path(r"D:\templateNaming")


@dataclass
class NamerResult:
    returncode: int
    command: list[str]
    stdout: str
    stderr: str
    output_files: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class NamerError(Exception):
    pass


def _resolve_command(
    *,
    input_dir: Path,
    output_dir: Path,
    market: str,
    template_naming_dir: Path,
) -> list[str]:
    """Prefer the installed entry point if present; otherwise invoke the
    module from its source tree via ``python -m``."""
    exe = shutil.which("template-namer")
    if exe:
        return [
            exe,
            "rename",
            "--input-dir", str(input_dir),
            "--output-dir", str(output_dir),
            "--market", market,
        ]
    if template_naming_dir.exists():
        return [
            sys.executable,
            "-m", "template_naming.cli",
            "rename",
            "--input-dir", str(input_dir),
            "--output-dir", str(output_dir),
            "--market", market,
        ]
    raise NamerError(
        "template-namer not found on PATH and "
        f"{template_naming_dir} does not exist. Install template-naming or pass "
        "an explicit template_naming_dir."
    )


def run_template_namer(
    *,
    input_dir: Path | str,
    output_dir: Path | str,
    market: str = "NQ",
    template_naming_dir: Path | str | None = None,
    timeout_seconds: int = 120,
    runner: callable | None = None,
) -> NamerResult:
    """Run template-namer over ``input_dir`` and return its result.

    ``runner`` is injectable so tests can stub the subprocess call without
    relying on a real install. It must accept ``(cmd: list[str],
    cwd: Path, timeout: int)`` and return a tuple
    ``(returncode, stdout, stderr)``.
    """
    src = Path(input_dir)
    dst = Path(output_dir)
    naming_root = Path(template_naming_dir) if template_naming_dir else DEFAULT_TEMPLATE_NAMING_DIR

    if not src.exists() or not src.is_dir():
        raise NamerError(f"input_dir does not exist: {src}")
    dst.mkdir(parents=True, exist_ok=True)

    cmd = _resolve_command(
        input_dir=src,
        output_dir=dst,
        market=market,
        template_naming_dir=naming_root,
    )

    cwd = naming_root if naming_root.exists() else Path.cwd()

    if runner is None:
        runner = _default_runner

    returncode, stdout, stderr = runner(cmd, cwd, timeout_seconds)

    output_files = sorted(str(p) for p in dst.glob("*.xml"))
    return NamerResult(
        returncode=int(returncode),
        command=list(cmd),
        stdout=stdout or "",
        stderr=stderr or "",
        output_files=output_files,
    )


def _default_runner(cmd: Sequence[str], cwd: Path, timeout_seconds: int) -> tuple[int, str, str]:
    env = os.environ.copy()
    src_dir = cwd / "src"
    if src_dir.exists():
        existing = env.get("PYTHONPATH")
        env["PYTHONPATH"] = str(src_dir) if not existing else f"{src_dir}{os.pathsep}{existing}"
    try:
        completed = subprocess.run(
            list(cmd),
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        raise NamerError(f"template-namer timed out after {timeout_seconds}s") from exc
    return completed.returncode, completed.stdout or "", completed.stderr or ""
