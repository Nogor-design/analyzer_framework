from __future__ import annotations

"""Helpers for final fixed-Backtest NinjaTrader templates.

The deployment package emits final templates under
``final_backtest_handoff/named_backtest_templates``. The Decision page needs
two extra operator affordances:

- give those XMLs a semantic template-naming CLI name before report generation;
- expose the active XML folder/zip so the operator can load them into NT.

The external namer writes semantic filenames without the finalist id, so we
persist a JSON index. That lets report generation continue to map a candidate
run id back to the right XML after the filename changes.
"""

import json
import re
import shutil
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ta_foundation.web.optimizer_namer import NamerError, run_template_namer
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


def list_active_final_templates_filtered(
    session: OptimizerSession,
    run_ids: set[str],
) -> list[Path]:
    """Return only the active XMLs whose decoded run_id is in ``run_ids``.

    Used by the Decision Dashboard's "Download selected" button to ship just
    the operator-picked finalists without re-zipping the full set.
    """
    wanted = {r.strip() for r in run_ids if r and r.strip()}
    if not wanted:
        return []

    indexed = _renamed_templates_by_run_id(session)
    if indexed:
        return [indexed[rid] for rid in sorted(wanted) if rid in indexed and indexed[rid].exists()]

    matched: list[Path] = []
    for path in list_active_final_templates(session):
        rid = _run_id_from_any_template_path(path) or _run_id_from_template_path(path)
        if rid in wanted:
            matched.append(path)
    return matched


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


def final_template_export_name_for_session(session: OptimizerSession, path: Path) -> str:
    """Return the operator-facing export name for a possibly semantic XML path."""
    resolved = path.resolve()
    for _run_id, indexed_path in _renamed_templates_by_run_id(session).items():
        if indexed_path.resolve() == resolved:
            return _safe_filename(path.name)
    if active_final_templates_dir(session) == final_renamed_templates_dir(session):
        return _safe_filename(path.name)
    return final_template_export_name(path)


def rename_final_templates(
    session: OptimizerSession,
    *,
    market: str | None = None,
    template_naming_dir: Path | str | None = None,
    runner: Any | None = None,
) -> FinalTemplateRenameResult:
    source_dir = final_named_templates_dir(session).resolve()
    if not source_dir.exists():
        raise FinalTemplateError(f"No final templates directory found: {source_dir}")

    source_files = sorted(source_dir.rglob("*.xml"))
    if not source_files:
        raise FinalTemplateError(f"No final template XML files found under: {source_dir}")

    output_dir = final_renamed_templates_dir(session).resolve()
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

        try:
            dest = _rename_one_final_template_with_cli(
                source=source,
                output_dir=output_dir,
                market=market_suffix,
                template_naming_dir=template_naming_dir,
                runner=runner,
            )
        except (NamerError, OSError) as exc:
            raise FinalTemplateError(f"template_naming CLI failed for {source.name}: {exc}") from exc

        safe_name = _safe_filename(dest.name)
        records.append(FinalTemplateRecord(
            run_id=run_id,
            source_path=str(source),
            renamed_path=str(dest),
            semantic_name=safe_name,
            decoded={"output_file_name": dest.name, "source_cli": "template_naming.cli rename"},
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


def _rename_one_final_template_with_cli(
    *,
    source: Path,
    output_dir: Path,
    market: str,
    template_naming_dir: Path | str | None,
    runner: Any | None,
) -> Path:
    """Run the external namer with explicit input/output dirs for one XML.

    The CLI processes only top-level XMLs in ``--input-dir``. Final handoff
    templates can live in nested strategy folders, so each source is copied
    into a tiny staging input directory and renamed into the shared output dir.
    Running one file at a time also gives us an exact source -> output mapping
    even when the namer appends V2/V3 for duplicate semantic names.
    """
    before = {p.resolve() for p in output_dir.glob("*.xml")}
    with tempfile.TemporaryDirectory(prefix="final-template-rename-") as tmp:
        input_dir = Path(tmp) / "in"
        input_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, input_dir / source.name)
        result = run_template_namer(
            input_dir=input_dir,
            output_dir=output_dir,
            market=market,
            template_naming_dir=template_naming_dir,
            runner=runner,
        )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or f"exit code {result.returncode}").strip()
        raise NamerError(detail)
    after = {p.resolve() for p in output_dir.glob("*.xml")}
    created = sorted(after - before)
    if len(created) != 1:
        raise NamerError(
            f"expected one renamed output for {source.name}, found {len(created)}"
        )
    return created[0]


def find_renamed_template_for_run_id(session: OptimizerSession, run_id: str) -> Path | None:
    indexed = _renamed_templates_by_run_id(session)
    if run_id in indexed:
        return indexed[run_id]

    renamed_dir = final_renamed_templates_dir(session)
    if not renamed_dir.exists():
        return None
    for xml in sorted(renamed_dir.rglob("*.xml")):
        if xml.name.startswith(f"{run_id}__"):
            return xml
    return None


def _renamed_templates_by_run_id(session: OptimizerSession) -> dict[str, Path]:
    index_path = final_renamed_index_path(session)
    if not index_path.exists():
        return {}
    try:
        payload = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    templates = payload.get("templates") if isinstance(payload, dict) else None
    if not isinstance(templates, dict):
        return {}
    out: dict[str, Path] = {}
    for run_id, record in templates.items():
        if not isinstance(run_id, str) or not isinstance(record, dict):
            continue
        renamed_path = record.get("renamed_path")
        if not renamed_path:
            continue
        path = Path(str(renamed_path))
        if path.exists():
            out[run_id] = path
    return out


def _run_id_from_template_path(path: Path) -> str | None:
    match = re.match(r"^(?P<prefix>[FP])_(?P<number>\d{3})(?:_|$)", path.stem, flags=re.IGNORECASE)
    if match:
        return f"{match.group('prefix').upper()}_{int(match.group('number')):03d}"

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


def _safe_filename(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", name.strip())
    cleaned = cleaned.strip("._")
    return cleaned or "renamed_template.xml"
