from __future__ import annotations

import base64
import hashlib
import re
import shutil
from pathlib import Path


DEFAULT_REPORT_ASSET_MODE = "embedded"
_VALID_REPORT_ASSET_MODES = {"embedded", "external"}
_DATA_IMAGE_RE = re.compile(
    r"data:image/(?P<subtype>[\w.+-]+);base64,(?P<data>[A-Za-z0-9+/=\r\n]+)"
)


def normalize_report_asset_mode(value: str | None) -> str:
    raw = str(value or DEFAULT_REPORT_ASSET_MODE).strip().lower()
    if raw in {"", "embedded", "inline"}:
        return "embedded"
    if raw in {"external", "linked", "files", "file"}:
        return "external"
    raise ValueError(
        "report asset mode must be one of: embedded, external"
    )


def report_asset_dir(html_path: Path) -> Path:
    return html_path.parent / f"{html_path.stem}_assets"


def clear_report_asset_dir(html_path: Path) -> None:
    asset_dir = report_asset_dir(html_path)
    if asset_dir.exists():
        shutil.rmtree(asset_dir, ignore_errors=True)


def finalize_report_html(
    html: str,
    html_path: Path,
    *,
    asset_mode: str | None = None,
) -> tuple[str, list[str]]:
    mode = normalize_report_asset_mode(asset_mode)
    notes: list[str] = []
    if mode == "embedded":
        clear_report_asset_dir(html_path)
        return html, notes

    asset_dir = report_asset_dir(html_path)
    clear_report_asset_dir(html_path)
    asset_dir.mkdir(parents=True, exist_ok=True)

    written: dict[str, str] = {}
    writes = 0

    def _replace(match: re.Match[str]) -> str:
        nonlocal writes
        subtype = match.group("subtype").lower()
        payload = re.sub(r"\s+", "", match.group("data"))
        digest = hashlib.sha256(f"{subtype}:{payload}".encode("ascii")).hexdigest()
        filename = written.get(digest)
        if filename is None:
            ext = _extension_for_subtype(subtype)
            filename = f"img-{len(written) + 1:04d}-{digest[:16]}.{ext}"
            data = base64.b64decode(payload)
            (asset_dir / filename).write_bytes(data)
            written[digest] = filename
            writes += 1
        return f"{asset_dir.name}/{filename}"

    updated_html = _DATA_IMAGE_RE.sub(_replace, html)
    if writes == 0:
        clear_report_asset_dir(html_path)
        return html, notes

    notes.append(
        f"Externalized {writes} embedded image(s) to {asset_dir.name}/ for online-friendly report serving."
    )
    return updated_html, notes


def resolve_report_asset_path(html_path: Path, filename: str) -> Path | None:
    return resolve_path_under_root(report_asset_dir(html_path), filename)


def resolve_path_under_root(root: Path, relative_name: str) -> Path | None:
    if not relative_name:
        return None
    candidate = Path(relative_name)
    if candidate.is_absolute() or ".." in candidate.parts:
        return None
    root_resolved = root.resolve()
    target = (root / candidate).resolve()
    if target != root_resolved and root_resolved not in target.parents:
        return None
    if not target.exists() or not target.is_file():
        return None
    return target


def _extension_for_subtype(subtype: str) -> str:
    cleaned = subtype.lower()
    if cleaned == "svg+xml":
        return "svg"
    if cleaned in {"jpeg", "jpg", "png", "gif", "webp", "bmp", "avif"}:
        return "jpg" if cleaned == "jpeg" else cleaned
    return re.sub(r"[^a-z0-9]+", "_", cleaned).strip("_") or "bin"
