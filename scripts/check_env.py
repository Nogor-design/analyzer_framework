from __future__ import annotations

import importlib
import importlib.util
import re
import sys
from pathlib import Path


def _compiled_tag_mismatches(site_packages: Path) -> list[Path]:
    expected_tag = f"cp{sys.version_info.major}{sys.version_info.minor}"
    mismatches: list[Path] = []
    tag_pattern = re.compile(r"(cp\d{2,3})")

    for path in site_packages.rglob("*.pyd"):
        match = tag_pattern.search(path.name)
        if match and match.group(1) != expected_tag:
            mismatches.append(path)

    return mismatches


def _check_import(name: str) -> tuple[bool, str | None]:
    if importlib.util.find_spec(name) is None:
        return False, None
    try:
        importlib.import_module(name)
    except Exception as exc:  # pragma: no cover - defensive guard for local env drift
        return False, f"{type(exc).__name__}: {exc}"
    return True, None


def main() -> int:
    site_packages = Path(sys.prefix) / "Lib" / "site-packages"
    if not site_packages.exists():
        print(f"[env-check] site-packages not found under {site_packages}")
        return 1

    mismatches = _compiled_tag_mismatches(site_packages)
    if mismatches:
        print(
            f"[env-check] Found compiled extensions that do not match Python "
            f"{sys.version_info.major}.{sys.version_info.minor}:"
        )
        for path in mismatches[:20]:
            print(f"  - {path}")
        if len(mismatches) > 20:
            print(f"  ... and {len(mismatches) - 20} more")
        print("[env-check] Recreate the virtualenv instead of reusing it across Python versions.")
        return 1

    required_modules = [
        "pandas",
        "matplotlib",
        "yaml",
        "numpy",
        "scipy",
        "sklearn",
    ]
    optional_modules = [
        "pyarrow",
        "playwright",
        "pytest",
    ]

    failures: list[str] = []
    for name in required_modules:
        ok, error = _check_import(name)
        if not ok:
            if error is None:
                failures.append(f"{name} is not installed")
            else:
                failures.append(f"{name} failed to import: {error}")

    optional_notes: list[str] = []
    for name in optional_modules:
        ok, error = _check_import(name)
        if error:
            optional_notes.append(f"{name} failed to import: {error}")
        elif not ok:
            optional_notes.append(f"{name} not installed")

    if failures:
        print("[env-check] Environment validation failed:")
        for failure in failures:
            print(f"  - {failure}")
        print("[env-check] Run scripts/bootstrap.ps1 to rebuild a clean virtualenv.")
        return 1

    print(
        f"[env-check] OK for Python {sys.version_info.major}.{sys.version_info.minor} "
        f"at {Path(sys.executable).resolve()}"
    )
    if optional_notes:
        print("[env-check] Optional components:")
        for note in optional_notes:
            print(f"  - {note}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
