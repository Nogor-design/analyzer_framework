from __future__ import annotations

"""Install a NinjaScript .cs file into the NinjaTrader Strategies folder.

NinjaTrader auto-compiles the dropped file; observation is handled separately
in `compile_observer.py`. Keeping install isolated lets the repair loop reuse
it without invoking the IPC observer (for example, in dry-run or fixture
modes).
"""

import hashlib
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_NT_DOCUMENTS = Path(r"C:\Users\Owner\Documents\NinjaTrader 8")
DEFAULT_COMPILE_ROOT = Path(r"C:\ta_foundation\nt_compile_loop")


class InstallerError(RuntimeError):
    pass


@dataclass(frozen=True)
class InstalledStrategy:
    installed_at: str
    source_path: str
    target_path: str
    staging_path: str
    sha256: str
    bytes: int
    overwrite: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def install_strategy_source(
    source_path: str | Path,
    *,
    nt_documents_dir: str | Path = DEFAULT_NT_DOCUMENTS,
    compile_root: str | Path = DEFAULT_COMPILE_ROOT,
    overwrite: bool = False,
) -> InstalledStrategy:
    source = Path(source_path)
    if not source.is_file():
        raise InstallerError(f"NinjaScript source does not exist: {source}")
    if source.suffix.lower() != ".cs":
        raise InstallerError(f"NinjaScript source must be a .cs file: {source}")

    nt_dir = Path(nt_documents_dir)
    target = nt_dir / "bin" / "Custom" / "Strategies" / source.name
    staging = Path(compile_root) / "staging" / source.name
    target.parent.mkdir(parents=True, exist_ok=True)
    staging.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and not overwrite:
        raise InstallerError(f"Target exists; pass overwrite=True to replace it: {target}")

    shutil.copy2(source, staging)
    shutil.copy2(source, target)
    digest = sha256_file(target)
    return InstalledStrategy(
        installed_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        source_path=str(source.resolve()),
        target_path=str(target.resolve()),
        staging_path=str(staging.resolve()),
        sha256=digest,
        bytes=target.stat().st_size,
        overwrite=overwrite,
    )


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
