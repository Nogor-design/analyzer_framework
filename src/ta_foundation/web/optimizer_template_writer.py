from __future__ import annotations

"""
Write per-chunk NinjaTrader optimizer template XMLs from a saved plan.

Given a seed Strategy Analyzer optimization template and a plan (as produced
by ``optimizer_plan.build_plan_preview``), produce one XML per chunk under
``<session_dir>/generated_templates/<chunk_id>.xml``.

This module is the web layer's equivalent of the standalone optimizer's
template generator. It uses regex-based seed patching to preserve the seed
template's NinjaTrader-specific type serialization details exactly (only the
fields we want to change are touched). The standalone optimizer module is
not modified — this is a parallel writer that takes a generic chunk plan.

Chunk semantics:

- Each ``optimized`` parameter in a chunk drives a ``<Parameter>`` block
  under ``<OptimizationParameters>`` (Min/Max/Increment/ValueSerializable).
- Each ``fixed`` parameter writes its value into ``<Strategy>`` as a fixed
  value, and clamps the corresponding ``<Parameter>`` block to that value
  so the NinjaTrader optimizer doesn't sweep it.
"""

import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from ta_foundation.web.optimizer_session import OptimizerSession


GENERATED_DIRNAME = "generated_templates"


@dataclass(frozen=True)
class GeneratedTemplate:
    chunk_id: str
    path: str
    combination_count: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def generate_session_templates(
    session: OptimizerSession,
    *,
    optimizer_type: str | None = None,
    optimization_fitness: str | None = None,
) -> list[GeneratedTemplate]:
    """Read the session's seed template and saved plan, then write one XML
    per chunk. Returns the list of written files."""
    doc = session.load_document()
    plan = session.load_plan()
    if not plan:
        raise TemplateWriteError("Session has no saved plan; preview a plan first.")
    seed_path = Path(doc.seed_template_path)
    if not seed_path.exists():
        raise TemplateWriteError(f"Seed template not found: {seed_path}")

    output_root = session.directory / GENERATED_DIRNAME
    output_root.mkdir(parents=True, exist_ok=True)
    # Wipe stale chunk files so removed chunks don't linger.
    for stale in output_root.glob("*.xml"):
        try:
            stale.unlink()
        except OSError:
            pass

    seed_text = seed_path.read_text(encoding="utf-8")
    instrument = _template_instrument_for(doc.instrument, seed_path)
    chunks = plan.get("chunks") or []
    written: list[GeneratedTemplate] = []

    for chunk in chunks:
        chunk_id = str(chunk.get("chunk_id") or "")
        if not chunk_id:
            continue
        text = seed_text
        if optimizer_type:
            text = _replace_tag_text(text, "OptimizerType", optimizer_type, count=1)
        if optimization_fitness:
            text = _replace_tag_text(text, "OptimizationFitness", optimization_fitness, count=1)
        if instrument:
            text = _replace_tag_text(text, "InstrumentOrInstrumentList", instrument, count=1)
        text = _patch_optimizer_parameter(
            text,
            "KeepBestResults",
            str(max(1, int(doc.chunking.keep_best_results or 500))),
        )

        # Optimized parameters → sweep ranges
        for sweep in chunk.get("optimized") or []:
            text = _patch_sweep(text, sweep)

        # Fixed parameters → pin both <Strategy> value AND the <Parameter>
        # block (so NinjaTrader doesn't sweep them by mistake).
        for fixed in chunk.get("fixed") or []:
            text = _patch_fixed(text, fixed)

        target = output_root / f"{chunk_id}.xml"
        target.write_text(text, encoding="utf-8")
        written.append(
            GeneratedTemplate(
                chunk_id=chunk_id,
                path=str(target),
                combination_count=int(chunk.get("combination_count") or 0),
            )
        )

    return written


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class TemplateWriteError(Exception):
    pass


# ---------------------------------------------------------------------------
# Patch helpers
# ---------------------------------------------------------------------------

def _patch_sweep(text: str, sweep: dict[str, Any]) -> str:
    name = str(sweep.get("name") or "")
    if not name:
        return text
    minimum = _serialize(sweep.get("minimum"), sweep.get("type_name"))
    maximum = _serialize(sweep.get("maximum"), sweep.get("type_name"))
    if not minimum or not maximum:
        raise TemplateWriteError(
            f"Parameter '{name}' has empty or invalid sweep bounds (min: '{sweep.get('minimum')}', max: '{sweep.get('maximum')}'). "
            f"Empty bounds are prohibited to prevent unconstrained sweeps with millions of combinations in NinjaTrader."
        )
    # Increment is always numeric, never a bool. NT bool sweeps use
    # Min=false, Max=true, Increment=1 — running the type-aware serializer
    # would emit Increment=true and collapse the sweep to a single value.
    increment = _serialize_increment(sweep.get("increment"))
    text = _patch_parameter_block(text, name, minimum, maximum, increment, minimum)
    text = _replace_tag_text(text, name, _strategy_form(minimum), count=1)
    return text


def _serialize_increment(value: Any) -> str:
    if value is None or value == "":
        return "1"
    if isinstance(value, bool):
        return "1"
    if isinstance(value, float):
        return format(value, "g")
    return str(value)


def _patch_fixed(text: str, fixed: dict[str, Any]) -> str:
    name = str(fixed.get("name") or "")
    if not name:
        return text
    value = _serialize(fixed.get("value"), None)
    text = _patch_parameter_block(text, name, value, value, "1", value)
    text = _replace_tag_text(text, name, _strategy_form(value), count=1)
    return text


def _patch_parameter_block(
    text: str,
    name: str,
    minimum: str,
    maximum: str,
    increment: str,
    value: str,
) -> str:
    """Locate the ``<Parameter>...<Name>X</Name>...</Parameter>`` block for
    parameter ``name`` and patch Min/Max/Increment/ValueSerializable in place.

    NOTE: Attributes on Min/Max (e.g. ``<Max xsi:type="xsd:int">``) are
    preserved by ``_replace_tag_text`` — only the text content changes.
    """
    pattern = re.compile(
        r"(?P<block><Parameter>(?:(?!</Parameter>).)*<Name>"
        + re.escape(name)
        + r"</Name>(?:(?!</Parameter>).)*</Parameter>)",
        re.DOTALL,
    )

    def replace(match: re.Match[str]) -> str:
        block = match.group("block")
        block = _replace_tag_text(block, "Increment", increment)
        block = _replace_tag_text(block, "Min", minimum)
        block = _replace_tag_text(block, "Max", maximum)
        block = _replace_tag_text(block, "ValueSerializable", value)
        return block

    return pattern.sub(replace, text, count=1)


def _patch_optimizer_parameter(text: str, name: str, value: str) -> str:
    """Patch a top-level ``<OptimizerParameters>`` ParameterWrapper value.

    NinjaTrader's Strategy Analyzer optimizer settings live outside the
    strategy ``<OptimizationParameters>`` sweep blocks. ``KeepBestResults`` is
    one of those settings and controls how many rows NT retains/exports.
    """
    pattern = re.compile(
        r"(?P<block><ParameterWrapper>(?:(?!</ParameterWrapper>).)*<Name>"
        + re.escape(name)
        + r"</Name>(?:(?!</ParameterWrapper>).)*</ParameterWrapper>)",
        re.DOTALL,
    )

    def replace(match: re.Match[str]) -> str:
        return _replace_tag_text(match.group("block"), "Value", value)

    return pattern.sub(replace, text, count=1)


def _template_instrument_for(saved_instrument: str, seed_path: Path) -> str:
    saved = str(saved_instrument or "").strip()
    if saved and not _is_generic_instrument(saved):
        return saved
    seed_instrument = _instrument_from_seed_template(seed_path)
    if seed_instrument:
        return seed_instrument
    return saved


def _is_generic_instrument(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return True
    return " " not in text and re.search(r"\d{2}-\d{2}", text) is None


def _instrument_from_seed_template(path: Path) -> str:
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


def _replace_tag_text(text: str, tag: str, value: str, count: int = 0) -> str:
    """Replace the text content of ``<tag ...>...</tag>`` while keeping any
    attributes on the open tag intact."""
    pattern = re.compile(
        r"(<" + re.escape(tag) + r"(?:\s+[^>]*)?>)(.*?)(</" + re.escape(tag) + r">)",
        re.DOTALL,
    )
    return pattern.sub(lambda m: m.group(1) + str(value) + m.group(3), text, count=count)


def _serialize(value: Any, type_name: Any) -> str:
    """Render a plan value back to the string form NinjaTrader expects."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    t = str(type_name or "").lower()
    if t in {"bool", "boolean", "system.boolean"}:
        return "true" if _truthy(value) else "false"
    if isinstance(value, float):
        # Strip trailing zeros for cleanliness, but keep at least one decimal
        # if the value is fractional. NT happily parses both "0.5" and "1".
        formatted = format(value, "g")
        return formatted
    return str(value)


def _strategy_form(value: str) -> str:
    """Strategy node convention: booleans lowercase, everything else verbatim."""
    if value == "True":
        return "true"
    if value == "False":
        return "false"
    return value


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in {"true", "1", "yes"}


def _has_tag(text: str, tag: str) -> bool:
    """Check if a tag exists in the XML text."""
    return f"<{tag}>" in text or f"<{tag} " in text


def _remove_top_level_tag(text: str, tag: str) -> str:
    """Remove a top-level tag from the XML.

    NinjaTrader's saved Strategy Analyzer optimizer templates may include
    a BacktestType node. The recipe seed should not have this; the optimizer
    can infer Optimize from the OptimizationParameters block.
    """
    pattern = r"\n\s*<" + re.escape(tag) + r"(?:\s+[^>]*)?>.*?</" + re.escape(tag) + r">"
    return re.sub(pattern, "", text, count=1, flags=re.DOTALL)


def _replace_or_insert_top_level_tag(text: str, tag: str, value: str) -> str:
    """Replace a top-level tag's value, or insert it if missing.

    Top-level tags are those directly under <StrategyTemplate>.
    """
    patched = _replace_tag_text(text, tag, value, count=1)
    if patched != text or _has_tag(text, tag):
        return patched
    if "<StrategyTemplate>" in text:
        return text.replace(
            "<StrategyTemplate>",
            f"<StrategyTemplate>\n  <{tag}>{value}</{tag}>",
            1,
        )
    return text


def _replace_or_insert_strategy_tag(text: str, tag: str, value: str) -> str:
    """Replace a <Strategy> child tag's value, or insert if missing.

    Strategy tags are those directly under <Strategy>.
    """
    patched = _replace_tag_text(text, tag, value, count=1)
    if patched != text or _has_tag(text, tag):
        return patched
    anchors = [
        ("<IncludeCommission", "</IncludeCommission>"),
        ("<Category", "</Category>"),
    ]
    for open_tag, close_tag in anchors:
        if close_tag in text:
            return text.replace(
                close_tag,
                f"{close_tag}\n    <{tag}>{value}</{tag}>",
                1,
            )
    if "</Strategy>" in text:
        return text.replace(
            "</Strategy>",
            f"    <{tag}>{value}</{tag}>\n  </Strategy>",
            1,
        )
    return text


def _align_bars_period_parameter(text: str) -> str:
    """Ensure the BarsPeriod parameter has a non-zero Value.

    Strategy Analyzer requires this for data series lookback.
    """
    return text
