from __future__ import annotations

"""Preflight checks for NinjaTrader optimizer web sessions."""

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from ta_foundation.web.optimizer_session import OptimizerSession


GENERATED_DIRNAME = "generated_templates"


@dataclass(frozen=True)
class TemplateInstrumentCheck:
    path: str
    name: str
    instrument: str
    status: str
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class OptimizerPreflight:
    ok: bool
    session_id: str
    seed_template_path: str
    session_instrument: str
    seed_instrument: str
    resolved_instrument: str
    generated_dir: str
    template_count: int
    blocking_errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    templates: list[TemplateInstrumentCheck] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def run_preflight(session: OptimizerSession) -> OptimizerPreflight:
    doc = session.load_document()
    seed_path = Path(doc.seed_template_path) if doc.seed_template_path else Path()
    seed_instrument = _instrument_from_xml(seed_path) if seed_path else ""
    resolved = resolve_optimizer_instrument(doc.instrument, seed_path)
    generated_dir = (session.directory / GENERATED_DIRNAME).resolve()
    xmls = sorted(generated_dir.glob("*.xml")) if generated_dir.exists() else []

    warnings: list[str] = []
    errors: list[str] = []
    checks: list[TemplateInstrumentCheck] = []

    if not xmls:
        errors.append(f"No generated templates in {generated_dir}; generate XMLs before running.")
    if not resolved:
        warnings.append("No full command instrument could be resolved from the session or seed template.")
    elif _is_generic_instrument(resolved):
        errors.append(f"Resolved command instrument is generic: {resolved!r}.")

    for xml in xmls:
        instrument = _instrument_from_xml(xml)
        status = "ok"
        message = ""
        if not instrument:
            status = "warning"
            message = "No InstrumentOrInstrumentList tag found; NinjaTrader may rely on command instrument."
            warnings.append(f"{xml.name}: missing InstrumentOrInstrumentList")
        elif _is_generic_instrument(instrument):
            status = "error"
            message = f"Generic instrument {instrument!r}; expected full NinjaTrader contract."
            errors.append(f"{xml.name}: {message}")
        elif resolved and instrument != resolved:
            status = "warning"
            message = f"Template instrument {instrument!r} differs from command instrument {resolved!r}."
            warnings.append(f"{xml.name}: {message}")
        checks.append(
            TemplateInstrumentCheck(
                path=str(xml),
                name=xml.name,
                instrument=instrument,
                status=status,
                message=message,
            )
        )

    return OptimizerPreflight(
        ok=not errors,
        session_id=session.id,
        seed_template_path=str(seed_path) if doc.seed_template_path else "",
        session_instrument=str(doc.instrument or "").strip(),
        seed_instrument=seed_instrument,
        resolved_instrument=resolved,
        generated_dir=str(generated_dir),
        template_count=len(xmls),
        blocking_errors=errors,
        warnings=warnings,
        templates=checks,
    )


def resolve_optimizer_instrument(saved_instrument: str, seed_path: str | Path | None) -> str:
    saved = str(saved_instrument or "").strip()
    if saved and not _is_generic_instrument(saved):
        return saved
    seed_instrument = _instrument_from_xml(Path(seed_path)) if seed_path else ""
    if seed_instrument:
        return seed_instrument
    return saved


def _is_generic_instrument(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return True
    return " " not in text and re.search(r"\d{2}-\d{2}", text) is None


def _instrument_from_xml(path: Path) -> str:
    if not path.exists() or not path.is_file():
        return ""
    try:
        root = ET.parse(path).getroot()
    except Exception:
        return ""
    node = root.find(".//InstrumentOrInstrumentList")
    if node is None or node.text is None:
        return ""
    return node.text.strip()
