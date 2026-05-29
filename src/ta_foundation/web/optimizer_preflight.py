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
    from_date: str = ""
    to_date: str = ""
    backtest_type: str = ""
    category: str = ""

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
        from_date, to_date = _date_range_from_xml(xml)
        backtest_type, category = _analyzer_type_from_xml(xml)
        status = "ok"
        messages: list[str] = []
        if not instrument:
            status = "error"
            messages.append("No InstrumentOrInstrumentList tag found; generated optimizer chunks must carry a full contract.")
            errors.append(f"{xml.name}: missing InstrumentOrInstrumentList")
        elif _is_generic_instrument(instrument):
            status = "error"
            messages.append(f"Generic instrument {instrument!r}; expected full NinjaTrader contract.")
            errors.append(f"{xml.name}: {messages[-1]}")
        elif resolved and instrument != resolved:
            status = "warning"
            messages.append(f"Template instrument {instrument!r} differs from command instrument {resolved!r}.")
            warnings.append(f"{xml.name}: {messages[-1]}")
        if (backtest_type or category) != "Optimize":
            status = "error"
            found = backtest_type or category or "<missing>"
            messages.append(f"Template analyzer category is {found!r}; optimizer chunks must use 'Optimize'.")
            errors.append(f"{xml.name}: {messages[-1]}")
        date_error = _date_range_error(from_date, to_date)
        if date_error:
            status = "error"
            messages.append(date_error)
            errors.append(f"{xml.name}: {date_error}")
        checks.append(
            TemplateInstrumentCheck(
                path=str(xml),
                name=xml.name,
                instrument=instrument,
                status=status,
                message=" ".join(messages),
                from_date=from_date,
                to_date=to_date,
                backtest_type=backtest_type,
                category=category,
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
    for elem in root.iter():
        tag = elem.tag.split("}")[-1]
        if tag == "InstrumentOrInstrumentList":
            return (elem.text or "").strip()
    return ""


def _date_range_from_xml(path: Path) -> tuple[str, str]:
    if not path.exists() or not path.is_file():
        return "", ""
    try:
        root = ET.parse(path).getroot()
    except Exception:
        return "", ""
    from_date = ""
    to_date = ""
    for elem in root.iter():
        tag = elem.tag.split("}")[-1]
        if tag == "From":
            from_date = (elem.text or "").strip()
        elif tag == "To":
            to_date = (elem.text or "").strip()
    return from_date, to_date


def _analyzer_type_from_xml(path: Path) -> tuple[str, str]:
    if not path.exists() or not path.is_file():
        return "", ""
    try:
        root = ET.parse(path).getroot()
    except Exception:
        return "", ""
    backtest_type = ""
    category = ""
    for elem in root.iter():
        tag = elem.tag.split("}")[-1]
        if tag == "BacktestType":
            backtest_type = (elem.text or "").strip()
        elif tag == "Category":
            category = (elem.text or "").strip()
    return backtest_type, category


def _date_range_error(from_date: str, to_date: str) -> str:
    if not from_date or not to_date:
        return ""
    try:
        from_dt = _parse_xml_date(from_date)
        to_dt = _parse_xml_date(to_date)
    except ValueError:
        return ""
    if to_dt <= from_dt:
        return f"Invalid Strategy Analyzer date range: From {from_date!r} must be before To {to_date!r}."
    return ""


def _parse_xml_date(value: str):
    from datetime import datetime

    text = str(value or "").strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return datetime.fromisoformat(text)
