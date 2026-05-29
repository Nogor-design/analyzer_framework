from __future__ import annotations

"""Helpers for final fixed-Backtest NinjaTrader templates.

The deployment package emits final templates under
``final_backtest_handoff/named_backtest_templates``. The Decision page needs
two extra operator affordances:

- give those XMLs a semantic template-namer name before report generation;
- expose the active XML folder/zip so the operator can load them into NT.

Renamed files keep the finalist id in the filename (``F_001__...xml``), and
we also persist a JSON index. That lets report generation continue to map a
candidate run id back to the right XML after the filename changes.
"""

import json
import re
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ta_foundation.web.optimizer_session import OptimizerSession


FINAL_HANDOFF_DIRNAME = "final_backtest_handoff"
FINAL_NAMED_TEMPLATES_DIRNAME = "named_backtest_templates"
FINAL_RENAMED_TEMPLATES_DIRNAME = "renamed_backtest_templates"
FINAL_RENAMED_INDEX_FILENAME = "renamed_template_index.json"


class FinalTemplateError(Exception):
    pass


@dataclass(frozen=True)
class FinalTemplateRecord:
    run_id: str
    source_path: str
    renamed_path: str
    semantic_name: str
    decoded: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FinalTemplateRenameResult:
    session_id: str
    source_dir: str
    output_dir: str
    template_count: int
    templates: list[FinalTemplateRecord]
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "source_dir": self.source_dir,
            "output_dir": self.output_dir,
            "template_count": self.template_count,
            "templates": [t.to_dict() for t in self.templates],
            "notes": list(self.notes),
        }


def final_handoff_dir(session: OptimizerSession) -> Path:
    return session.directory / "deployment_package" / FINAL_HANDOFF_DIRNAME


def final_named_templates_dir(session: OptimizerSession) -> Path:
    return final_handoff_dir(session) / FINAL_NAMED_TEMPLATES_DIRNAME


def final_renamed_templates_dir(session: OptimizerSession) -> Path:
    return final_handoff_dir(session) / FINAL_RENAMED_TEMPLATES_DIRNAME


def final_renamed_index_path(session: OptimizerSession) -> Path:
    return final_renamed_templates_dir(session) / FINAL_RENAMED_INDEX_FILENAME


def active_final_templates_dir(session: OptimizerSession) -> Path:
    renamed = final_renamed_templates_dir(session)
    if any(renamed.rglob("*.xml")):
        return renamed
    return final_named_templates_dir(session)


def list_active_final_templates(session: OptimizerSession) -> list[Path]:
    active = active_final_templates_dir(session)
    if not active.exists():
        return []
    return sorted(active.rglob("*.xml"))


def final_template_links(session: OptimizerSession) -> dict[str, Any]:
    active = active_final_templates_dir(session)
    renamed = active == final_renamed_templates_dir(session)
    return {
        "active_dir": str(active),
        "source_dir": str(final_named_templates_dir(session)),
        "renamed_dir": str(final_renamed_templates_dir(session)),
        "count": len(list_active_final_templates(session)),
        "renamed": renamed,
        "list_url": f"/optimizer/sessions/{session.id}/templates/final",
        "download_url": f"/optimizer/sessions/{session.id}/templates/final.zip",
    }


def final_template_export_name(path: Path) -> str:
    """Return a concise NinjaTrader-safe filename for exported final XMLs."""
    run_id = _run_id_from_any_template_path(path) or _run_id_from_template_path(path) or path.stem
    parts = [_safe_filename(run_id)]
    start_hour = _start_hour_from_template(path)
    if start_hour is not None:
        parts.append(f"StartTimeH_{start_hour:02d}")
    return "_".join(parts) + ".xml"


def rename_final_templates(
    session: OptimizerSession,
    *,
    market: str | None = None,
) -> FinalTemplateRenameResult:
    source_dir = final_named_templates_dir(session)
    if not source_dir.exists():
        raise FinalTemplateError(f"No final templates directory found: {source_dir}")

    source_files = sorted(source_dir.rglob("*.xml"))
    if not source_files:
        raise FinalTemplateError(f"No final template XML files found under: {source_dir}")

    output_dir = final_renamed_templates_dir(session)
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    market_suffix = (market or _market_from_session(session)).strip().upper()
    records: list[FinalTemplateRecord] = []
    notes: list[str] = []

    for source in source_files:
        run_id = _run_id_from_template_path(source)
        if run_id is None:
            notes.append(f"Skipped template without leading candidate number: {source.name}")
            continue

        decoded: dict[str, Any] = {}
        try:
            from template_naming import analyze_template
            decision = analyze_template(source)
            decoded = _decision_to_dict(decision)
            semantic_name = (
                str(decoded.get("output_file_name") or "").strip()
                or f"{decoded.get('compact_name') or source.stem}.xml"
            )
        except Exception as exc:
            semantic_name = final_template_export_name(source)
            notes.append(f"template_naming failed for {source.name}; kept original stem: {exc}")

        semantic_name = _with_market_suffix(semantic_name, market_suffix)
        safe_name = _safe_filename(semantic_name)
        dest = output_dir / f"{run_id}__{safe_name}"
        shutil.copy2(source, dest)
        records.append(FinalTemplateRecord(
            run_id=run_id,
            source_path=str(source),
            renamed_path=str(dest),
            semantic_name=safe_name,
            decoded=decoded,
        ))

    if not records:
        raise FinalTemplateError("No final templates could be renamed.")

    index = {
        "schema_version": 1,
        "session_id": session.id,
        "market": market_suffix,
        "templates": {record.run_id: record.to_dict() for record in records},
    }
    final_renamed_index_path(session).write_text(
        json.dumps(index, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    return FinalTemplateRenameResult(
        session_id=session.id,
        source_dir=str(source_dir),
        output_dir=str(output_dir),
        template_count=len(records),
        templates=records,
        notes=notes,
    )


def find_renamed_template_for_run_id(session: OptimizerSession, run_id: str) -> Path | None:
    index_path = final_renamed_index_path(session)
    if index_path.exists():
        try:
            payload = json.loads(index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
        record = (payload.get("templates") or {}).get(run_id) if isinstance(payload, dict) else None
        if isinstance(record, dict) and record.get("renamed_path"):
            path = Path(str(record["renamed_path"]))
            if path.exists():
                return path

    renamed_dir = final_renamed_templates_dir(session)
    if not renamed_dir.exists():
        return None
    for xml in sorted(renamed_dir.rglob("*.xml")):
        if xml.name.startswith(f"{run_id}__"):
            return xml
    return None


def _run_id_from_template_path(path: Path) -> str | None:
    head = path.stem.split("_", 1)[0]
    try:
        number = int(head)
    except ValueError:
        return None
    return f"F_{number:03d}"


def _run_id_from_any_template_path(path: Path) -> str | None:
    match = re.search(r"(?:^|__)F_(\d{3})(?:__|_|$)", path.stem)
    if match:
        return f"F_{int(match.group(1)):03d}"
    return _run_id_from_template_path(path)


def _start_hour_from_template(path: Path) -> int | None:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    match = re.search(r"<StartTimeH(?:\s+[^>]*)?>\s*(-?\d+)\s*</StartTimeH>", text)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _market_from_session(session: OptimizerSession) -> str:
    doc = session.load_document()
    instrument = (doc.instrument or "").strip()
    if instrument:
        return instrument.split()[0]
    return (doc.market_suffix or "NQ").strip() or "NQ"


def _decision_to_dict(decision: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name in (
        "phase", "ma_name", "descriptor", "direction", "compact_name",
        "output_file_name", "long_name", "plain_english",
    ):
        value = getattr(decision, name, None)
        if value is not None:
            out[name] = value if isinstance(value, (str, int, float, bool)) else str(value)
    return out


def _with_market_suffix(name: str, market: str) -> str:
    path_name = name if name.lower().endswith(".xml") else f"{name}.xml"
    if not market:
        return path_name
    stem = path_name[:-4]
    if stem.upper().endswith(f"-{market.upper()}"):
        return path_name
    return f"{stem}-{market}.xml"


def _safe_filename(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", name.strip())
    cleaned = cleaned.strip("._")
    return cleaned or "renamed_template.xml"
