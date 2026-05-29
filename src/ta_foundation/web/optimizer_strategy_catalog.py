from __future__ import annotations

"""
NinjaTrader strategy catalog for the /optimizer web UI.

Scans the user's NinjaTrader install for available strategies and saved
optimization seed templates. Extracts per-parameter metadata from .cs source:

- `[NinjaScriptProperty]` marks a parameter as exposed for optimization
- `[Range(min, max)]` declares hard bounds (may use int.MaxValue / double.MaxValue)
- `[Display(Name=..., GroupName=..., Order=..., Description=...)]` UI metadata
- `public <type> <Name> { get; set; }` declares the field and its CLR type
- `State.SetDefaults` block holds default values

The catalog is read-only. It does NOT modify the standalone optimizer code
under src/ta_foundation/optimization/ — when an XML seed template's current
values are needed, the existing parser there is reused.
"""

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ta_foundation.optimization.nt_template import parse_strategy_optimization_template


# ---------------------------------------------------------------------------
# Default install paths — overridable from the web app
# ---------------------------------------------------------------------------

DEFAULT_STRATEGY_SOURCE_DIR = Path(
    r"C:\Users\Owner\Documents\NinjaTrader 8\bin\Custom\Strategies"
)
DEFAULT_STRATEGY_TEMPLATE_DIR = Path(
    r"C:\Users\Owner\Documents\NinjaTrader 8\templates\Strategy"
)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class StrategyParameter:
    name: str
    type_name: str
    default: Any = None
    has_default: bool = False
    range_min: float | None = None
    range_max: float | None = None
    group_name: str = ""
    display_name: str = ""
    order: int | None = None
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SeedTemplateSummary:
    name: str
    path: str
    strategy_type: str
    optimizer_type: str
    optimization_fitness: str
    instrument_or_instrument_list: str
    estimated_combinations: int
    swept_parameter_names: list[str] = field(default_factory=list)
    start_hour: int | None = None
    duration_hours: int | None = None
    mode: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class StrategySummary:
    strategy_id: str
    cs_path: str
    parameter_count: int
    seed_template_count: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class StrategyDetail:
    strategy_id: str
    cs_path: str
    parameters: list[StrategyParameter]
    seed_templates: list[SeedTemplateSummary]
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "cs_path": self.cs_path,
            "parameters": [p.to_dict() for p in self.parameters],
            "seed_templates": [s.to_dict() for s in self.seed_templates],
            "warnings": list(self.warnings),
        }


# ---------------------------------------------------------------------------
# C# parsing
# ---------------------------------------------------------------------------

_PROP_RE = re.compile(
    r"public\s+(?P<type>[A-Za-z0-9_\.<>]+)\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\{\s*get;\s*set;\s*\}"
)
# NinjaTrader's templates commonly split a property declaration across two
# lines: ``public double MaxLoss`` on one line, ``{ get; set; }`` on the next.
# This pattern matches just the head so we can pair it with the following
# brace block.
_PROP_HEAD_RE = re.compile(
    r"^public\s+(?P<type>[A-Za-z0-9_\.<>]+)\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*$"
)
_PROP_BRACE_RE = re.compile(r"^\{\s*get;\s*set;\s*\}\s*$")
_RANGE_RE = re.compile(r"\[Range\s*\(\s*(?P<min>[^,]+?)\s*,\s*(?P<max>[^)]+?)\s*\)\s*\]")
_DISPLAY_KV_RE = re.compile(r'(\w+)\s*=\s*("[^"]*"|[^,\)]+)')
_ASSIGN_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+?)\s*;\s*$")


_CSHARP_PRIMITIVE_CAST_RE = re.compile(
    r"^\(\s*(float|double|decimal|int|long|short|byte|sbyte|uint|ulong|ushort)\s*\)\s*",
    re.IGNORECASE,
)


def _parse_literal(raw: str) -> Any:
    """Convert a C# right-hand-side default expression to a Python value.

    Mirrors the cleanup in ``nt_strategy_loop.seed_template._normalize_csharp_default``:
    primitive casts, numeric suffixes, leading-dot floats, digit separators,
    trailing line comments, and wrapping parens are all stripped before
    parsing. Anything unparseable comes back as the raw string so display
    code can still show *something* even if the seed generator can't use it.
    """
    s = raw.strip()
    if not s:
        return s
    comment_idx = s.find("//")
    if comment_idx >= 0:
        s = s[:comment_idx].rstrip()
        if not s:
            return ""
    lowered = s.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if len(s) >= 2 and s.startswith('"') and s.endswith('"'):
        return s[1:-1]
    s = _CSHARP_PRIMITIVE_CAST_RE.sub("", s).strip()
    while s.startswith("(") and s.endswith(")"):
        inner = s[1:-1].strip()
        if not inner:
            break
        s = inner
    cleaned = s.replace("_", "").rstrip("fFdDmMlLuU")
    if cleaned.startswith("."):
        cleaned = "0" + cleaned
    elif cleaned.startswith(("-.", "+.")):
        cleaned = cleaned[0] + "0" + cleaned[1:]
    try:
        if any(ch in cleaned for ch in (".", "e", "E")):
            return float(cleaned)
        return int(cleaned)
    except ValueError:
        return s


def _parse_range_bound(raw: str) -> float | None:
    """Return a numeric bound, or None for sentinels like int.MaxValue."""
    text = raw.strip()
    lowered = text.lower()
    if lowered.endswith("maxvalue") or lowered.endswith("minvalue"):
        return None
    cleaned = text.replace("_", "").rstrip("fFdDmM")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _parse_display_attrs(raw: str) -> dict[str, Any]:
    """Extract Name, GroupName, Order, Description from a [Display(...)] body."""
    out: dict[str, Any] = {}
    for key, value in _DISPLAY_KV_RE.findall(raw):
        value = value.strip()
        if value.startswith('"') and value.endswith('"'):
            value = value[1:-1]
        out[key] = value
    return out


def _extract_defaults(cs_text: str) -> dict[str, Any]:
    defaults: dict[str, Any] = {}
    in_block = False
    for line in cs_text.splitlines():
        if "if (State == State.SetDefaults)" in line:
            in_block = True
            continue
        if in_block and "else if (State ==" in line:
            break
        if not in_block:
            continue
        m = _ASSIGN_RE.match(line)
        if not m:
            continue
        defaults[m.group(1)] = _parse_literal(m.group(2))
    return defaults


def _extract_parameters(cs_text: str) -> list[StrategyParameter]:
    """Walk the file and pair each [NinjaScriptProperty] block with its
    follow-on attributes and property declaration.

    Handles both single-line (``public T Name { get; set; }``) and two-line
    (``public T Name`` / ``{ get; set; }``) property layouts — NinjaTrader's
    own strategy template uses the two-line style for most properties.
    """
    parameters: list[StrategyParameter] = []
    defaults = _extract_defaults(cs_text)
    pending: dict[str, Any] | None = None
    multi_line_attr: list[str] | None = None
    pending_head: tuple[str, str] | None = None  # (type, name) awaiting brace line

    def _emit(name: str, type_name: str) -> None:
        nonlocal pending
        if pending is None:
            return
        display = pending.get("display") or {}
        try:
            order_val = int(display.get("Order")) if display.get("Order") else None
        except (TypeError, ValueError):
            order_val = None

        parameters.append(
            StrategyParameter(
                name=name,
                type_name=type_name,
                default=defaults.get(name),
                has_default=name in defaults,
                range_min=pending.get("range_min"),
                range_max=pending.get("range_max"),
                group_name=str(display.get("GroupName") or "").strip(),
                display_name=str(display.get("Name") or name).strip(),
                order=order_val,
                description=str(display.get("Description") or "").strip(),
            )
        )
        pending = None

    for raw_line in cs_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        # Continue accumulating a multi-line attribute (e.g. a [Display(...)]
        # whose arguments wrap across several lines).
        if multi_line_attr is not None:
            multi_line_attr.append(line)
            if line.endswith(")]") or line.endswith("])"):
                joined = " ".join(multi_line_attr)
                if pending is not None:
                    m = re.search(r"\[Display\s*\((.+)\)\s*\]", joined)
                    if m:
                        pending["display"] = _parse_display_attrs(m.group(1))
                multi_line_attr = None
            continue

        # If a two-line property head is waiting for its brace block, the
        # very next non-empty line must close it. If it doesn't, the head
        # was a false match and we discard it.
        if pending_head is not None:
            if _PROP_BRACE_RE.match(line):
                head_type, head_name = pending_head
                _emit(head_name, head_type)
                pending_head = None
                continue
            pending_head = None
            # fall through and re-process this line normally

        if line.startswith("[NinjaScriptProperty]"):
            pending = {"range_min": None, "range_max": None, "display": {}}
            continue

        if pending is None:
            continue

        if line.startswith("[Range"):
            m = _RANGE_RE.search(line)
            if m:
                pending["range_min"] = _parse_range_bound(m.group("min"))
                pending["range_max"] = _parse_range_bound(m.group("max"))
            continue

        if line.startswith("[Display"):
            if line.endswith(")]"):
                m = re.search(r"\[Display\s*\((.+)\)\s*\]", line)
                if m:
                    pending["display"] = _parse_display_attrs(m.group(1))
            else:
                multi_line_attr = [line]
            continue

        if line.startswith("[XmlIgnore") or line.startswith("[Browsable"):
            continue

        if line.startswith("["):
            continue

        # Single-line property: ``public T Name { get; set; }``
        m = _PROP_RE.search(line)
        if m:
            _emit(m.group("name"), m.group("type"))
            continue

        # Two-line property head: ``public T Name`` (brace on next line)
        head = _PROP_HEAD_RE.match(line)
        if head:
            pending_head = (head.group("type"), head.group("name"))
            continue

        pending = None

    return parameters


# ---------------------------------------------------------------------------
# Seed template discovery
# ---------------------------------------------------------------------------

def _summarize_seed_template(path: Path) -> SeedTemplateSummary | None:
    try:
        tmpl = parse_strategy_optimization_template(path)
    except Exception:
        return None
    return SeedTemplateSummary(
        name=path.stem,
        path=str(path),
        strategy_type=tmpl.strategy_type,
        optimizer_type=tmpl.optimizer_type,
        optimization_fitness=tmpl.optimization_fitness,
        instrument_or_instrument_list=tmpl.instrument_or_instrument_list,
        estimated_combinations=tmpl.estimated_combinations,
        swept_parameter_names=[p.name for p in tmpl.swept_parameters],
        start_hour=tmpl.start_hour,
        duration_hours=tmpl.duration_hours,
        mode=tmpl.mode,
    )


def _seed_templates_for(strategy_id: str, template_dir: Path) -> list[SeedTemplateSummary]:
    folder = template_dir / strategy_id
    if not folder.exists() or not folder.is_dir():
        return []
    summaries: list[SeedTemplateSummary] = []
    for xml_path in sorted(folder.glob("*.xml")):
        summary = _summarize_seed_template(xml_path)
        if summary is not None:
            summaries.append(summary)
    return summaries


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def list_strategies(
    *,
    source_dir: Path | str | None = None,
    template_dir: Path | str | None = None,
) -> list[StrategySummary]:
    src = Path(source_dir) if source_dir else DEFAULT_STRATEGY_SOURCE_DIR
    tpl = Path(template_dir) if template_dir else DEFAULT_STRATEGY_TEMPLATE_DIR

    if not src.exists() or not src.is_dir():
        return []

    out: list[StrategySummary] = []
    for cs_path in sorted(src.glob("*.cs")):
        if cs_path.name.startswith("@"):
            continue
        strategy_id = cs_path.stem
        try:
            cs_text = cs_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        parameters = _extract_parameters(cs_text)
        seed_count = 0
        seed_folder = tpl / strategy_id if tpl.exists() else None
        if seed_folder is not None and seed_folder.is_dir():
            seed_count = sum(1 for _ in seed_folder.glob("*.xml"))
        out.append(
            StrategySummary(
                strategy_id=strategy_id,
                cs_path=str(cs_path),
                parameter_count=len(parameters),
                seed_template_count=seed_count,
            )
        )
    return out


def get_strategy_detail(
    strategy_id: str,
    *,
    source_dir: Path | str | None = None,
    template_dir: Path | str | None = None,
) -> StrategyDetail | None:
    src = Path(source_dir) if source_dir else DEFAULT_STRATEGY_SOURCE_DIR
    tpl = Path(template_dir) if template_dir else DEFAULT_STRATEGY_TEMPLATE_DIR

    cs_path = src / f"{strategy_id}.cs"
    if not cs_path.exists():
        return None

    warnings: list[str] = []
    cs_text = cs_path.read_text(encoding="utf-8", errors="ignore")
    parameters = _extract_parameters(cs_text)
    if not parameters:
        warnings.append("no_parameters_extracted")

    seed_templates = _seed_templates_for(strategy_id, tpl) if tpl.exists() else []
    if not seed_templates:
        warnings.append(f"no_seed_templates_in:{tpl / strategy_id}")

    return StrategyDetail(
        strategy_id=strategy_id,
        cs_path=str(cs_path),
        parameters=parameters,
        seed_templates=seed_templates,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Regenerate a recipe-ready seed from the baseline template
# ---------------------------------------------------------------------------

REPO_STRATEGIES_DIR = Path(__file__).resolve().parents[1] / "strategies"
RECIPE_SEED_FILENAME_SUFFIX = "_recipe_seed.xml"


class RecipeSeedRegenerationError(Exception):
    """Raised when seed regeneration fails."""
    pass


def regenerate_recipe_seed(
    strategy_id: str,
    *,
    instrument: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    template_dir: Path | str | None = None,
    source_dir: Path | str | None = None,
) -> SeedTemplateSummary:
    """Produce a fresh, recipe-ready Optimize seed for ``strategy_id``.

    Implements a 3-tier fallback chain to ensure seed generation works for any strategy:
    1. In-repo baseline template:
       Looks for ``src/ta_foundation/strategies/<strategy_id>/templates/sampleTemplate.xml``.
    2. Existing NinjaTrader template:
       Looks for any saved XML in ``C:\\Users\\Owner\\Documents\\NinjaTrader 8\\templates\\Strategy\\<strategy_id>\\*.xml``
       excluding our generated recipe seeds.
    3. Auto-generate from C# source:
       Extracts ``[NinjaScriptProperty]`` parameters from ``<strategy_id>.cs`` in the C# source directory
       and builds a fully valid, rich optimization template.
    """
    from ta_foundation.web.optimizer_template_writer import (
        _align_bars_period_parameter,
        _remove_top_level_tag,
        _replace_or_insert_strategy_tag,
        _replace_or_insert_top_level_tag,
        _replace_tag_text,
    )

    if not template_dir:
        template_dir = DEFAULT_STRATEGY_TEMPLATE_DIR
    if not source_dir:
        source_dir = DEFAULT_STRATEGY_SOURCE_DIR

    src_dir = Path(source_dir)
    tpl_dir = Path(template_dir)
    target_dir = tpl_dir / strategy_id
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / f"{strategy_id}{RECIPE_SEED_FILENAME_SUFFIX}"

    text = None
    source_used = None

    # Tier 1: Try in-repo baseline
    baseline = REPO_STRATEGIES_DIR / strategy_id / "templates" / "sampleTemplate.xml"
    if baseline.exists():
        try:
            text = baseline.read_text(encoding="utf-8")
            source_used = f"in-repo baseline: {baseline}"
        except OSError:
            pass

    # Tier 2: Try existing NinjaTrader saved template
    if text is None:
        nt_template_dir = tpl_dir / strategy_id
        if nt_template_dir.exists() and nt_template_dir.is_dir():
            for xml_file in sorted(nt_template_dir.glob("*.xml")):
                if not xml_file.name.endswith(RECIPE_SEED_FILENAME_SUFFIX):
                    try:
                        text = xml_file.read_text(encoding="utf-8")
                        source_used = f"existing NinjaTrader template: {xml_file}"
                        break
                    except OSError:
                        pass

    # Tier 3: Auto-generate from C# source code
    if text is None:
        cs_path = src_dir / f"{strategy_id}.cs"
        if cs_path.exists():
            from ta_foundation.nt_strategy_loop.seed_template import (
                generate_seed_template_from_source,
                SeedTemplateError,
            )
            fd = f"{from_date}T00:00:00" if from_date else "2026-04-14T00:00:00"
            td = f"{to_date}T00:00:00" if to_date else "2026-05-14T00:00:00"
            inst = instrument if instrument else "NQ 06-26"
            try:
                # generate template directly into target path
                generate_seed_template_from_source(
                    source_path=cs_path,
                    output_path=target_path,
                    strategy_name=strategy_id,
                    instrument=inst,
                    from_date=fd,
                    to_date=td,
                )
                # Load back the generated XML so any subsequent standard patching runs
                text = target_path.read_text(encoding="utf-8")
                source_used = f"auto-generated from C# source: {cs_path}"
            except Exception as exc:
                raise RecipeSeedRegenerationError(
                    f"Failed to auto-generate baseline template from C# source {cs_path}: {exc}"
                )

    if text is None:
        raise RecipeSeedRegenerationError(
            f"No baseline template found for strategy '{strategy_id}'. "
            f"Paths checked:\n"
            f"1. In-repo baseline: {baseline}\n"
            f"2. Saved templates in: {tpl_dir / strategy_id}\n"
            f"3. C# source file: {src_dir / f'{strategy_id}.cs'}"
        )

    # Apply standard recipe overrides/patching to whatever XML text we resolved
    text = _replace_or_insert_top_level_tag(
        text, "OptimizerType",
        "NinjaTrader.NinjaScript.Optimizers.DefaultOptimizer",
    )
    text = _replace_or_insert_top_level_tag(
        text, "OptimizationFitness",
        "NinjaTrader.NinjaScript.OptimizationFitnesses.MaxProfitFactor",
    )
    text = _remove_top_level_tag(text, "BacktestType")
    text = _replace_or_insert_strategy_tag(text, "Category", "Optimize")

    if instrument:
        text = _replace_or_insert_strategy_tag(
            text, "InstrumentOrInstrumentList", instrument,
        )
    if from_date:
        text = _replace_tag_text(text, "From", f"{from_date}T00:00:00", count=1)
    if to_date:
        text = _replace_tag_text(text, "To", f"{to_date}T00:00:00", count=1)

    # Ensure non-zero Value for the data series
    text = _align_bars_period_parameter(text)

    # Ensure at least one swept parameter for optimization classification.
    # If the template lacks proper OptimizationParameters or only contains a placeholder,
    # we dynamically enrich it from the C# source file if available!
    cs_path = src_dir / f"{strategy_id}.cs"
    if "<OptimizationParameters>" not in text or "__recipe_placeholder__" in text:
        if cs_path.exists():
            text = _populate_optimization_parameters_from_source(text, cs_path)

    # Fall back to standard placeholder if still missing
    if "<OptimizationParameters>" not in text:
        text = _insert_placeholder_optimization_parameters(text)
    else:
        # Normalize the baseline: pin every <Parameter> block to its current
        # ValueSerializable so the recipe is the sole source of sweep ranges.
        # Without this, any baseline carrying wide Min/Max (auto-generated from
        # C# Range attributes, or user-saved NT templates) multiplies into
        # millions of combinations once Stage 1 sends the template to NT.
        text = _pin_all_optimization_parameters(text)
        # NT still requires at least one Min<Max param for the seed to count as
        # an Optimize template, so inject the __recipe_placeholder__ row that
        # ``_strip_recipe_placeholder_parameter`` later removes from per-stage
        # generated templates.
        text = _ensure_placeholder_parameter(text)

    # Save out to NinjaTrader templates directory
    target_path.write_text(text, encoding="utf-8")

    summary = _summarize_seed_template(target_path)
    if summary is None:
        raise RecipeSeedRegenerationError(
            f"Generated seed at {target_path} (source: {source_used}) could not be parsed back. "
            "Inspect the file manually."
        )

    print(f"Regenerated seed for {strategy_id} using {source_used} successfully.")
    return summary


def _populate_optimization_parameters_from_source(xml_text: str, cs_path: Path) -> str:
    """Extract parameters from the C# source file and inject them into `<OptimizationParameters>`."""
    try:
        from ta_foundation.nt_strategy_loop.seed_template import (
            _is_optimizable_type,
            extract_strategy_parameters,
        )
        cs_text = cs_path.read_text(encoding="utf-8", errors="ignore")
        parameters = extract_strategy_parameters(cs_text)
        # Drop non-numeric/non-bool properties before emitting Parameter
        # blocks. NinjaTrader's optimizer cannot enumerate String, DateTime,
        # enum or Brush parameters — it calls .GetType() on the enumerated
        # value and throws NullReferenceException, which takes the whole
        # Strategy Analyzer process down.
        parameters = [p for p in parameters if _is_optimizable_type(p.type_name)]
        if not parameters:
            return xml_text

        param_xml_list = []
        for p in parameters:
            xsd = {
                "System.Int32": "xsd:int",
                "System.Int64": "xsd:long",
                "System.Int16": "xsd:short",
                "System.Double": "xsd:double",
                "System.Single": "xsd:float",
                "System.Decimal": "xsd:decimal",
                "System.Boolean": "xsd:boolean",
            }.get(p.type_name, "xsd:double")

            full_type = f"{p.type_name}, mscorlib, Version=4.0.0.0, Culture=neutral, PublicKeyToken=b77a5c561934e089"

            p_xml = (
                "      <Parameter>\n"
                "        <EnumValuesSerializable />\n"
                f"        <Increment>{p.increment}</Increment>\n"
                f"        <Max xsi:type=\"{xsd}\">{p.maximum}</Max>\n"
                f"        <Min xsi:type=\"{xsd}\">{p.minimum}</Min>\n"
                f"        <Name>{p.name}</Name>\n"
                f"        <ParameterTypeSerializable>{full_type}</ParameterTypeSerializable>\n"
                f"        <ValueSerializable>{p.default}</ValueSerializable>\n"
                "      </Parameter>"
            )
            param_xml_list.append(p_xml)

        block = (
            "  <OptimizationParameters>\n"
            "    <ArrayOfParameter xmlns:xsd=\"http://www.w3.org/2001/XMLSchema\" "
            "xmlns:xsi=\"http://www.w3.org/2001/XMLSchema-instance\">\n"
            + "\n".join(param_xml_list) + "\n"
            "    </ArrayOfParameter>\n"
            "  </OptimizationParameters>"
        )

        # Remove any existing <OptimizationParameters> block
        import re
        xml_text = re.sub(r"\s*<OptimizationParameters>.*?</OptimizationParameters>", "", xml_text, flags=re.DOTALL)

        # Insert our rich block before </StrategyTemplate> or after </Strategy>
        if "</StrategyTemplate>" in xml_text:
            return xml_text.replace("</StrategyTemplate>", block + "\n</StrategyTemplate>", 1)
        return xml_text + "\n" + block
    except Exception as exc:
        print(f"Warning: Failed to dynamically enrich template from C# source: {exc}")
        return xml_text


def _pin_all_optimization_parameters(text: str) -> str:
    """Force ``Min`` and ``Max`` to match ``ValueSerializable`` on every
    ``<Parameter>`` block inside ``<OptimizationParameters>``, and strip any
    block whose ``ParameterTypeSerializable`` declares a type the NT
    optimizer cannot enumerate (String, DateTime, enum, Brush, etc.).

    Two invariants in one pass:

    * Recipe planner is the only thing that should introduce sweep ranges
      — a baseline seed sweeping each parameter multiplies the recipe's
      own combinations into hundreds of millions.
    * Only numeric and boolean parameters belong in ``<OptimizationParameters>``
      — a String <Parameter> crashes NT's Strategy Analyzer with
      NullReferenceException at WaitForIterationsCompleted.
    """
    import re
    from ta_foundation.nt_strategy_loop.seed_template import _is_optimizable_type

    array_match = re.search(
        r"<OptimizationParameters>(.*?)</OptimizationParameters>",
        text,
        re.DOTALL,
    )
    if not array_match:
        return text

    block = array_match.group(0)

    def _pin(parameter_match: re.Match[str]) -> str:
        chunk = parameter_match.group(0)
        if "__recipe_placeholder__" in chunk:
            return chunk
        type_match = re.search(
            r"<ParameterTypeSerializable>(.*?)</ParameterTypeSerializable>",
            chunk,
            re.DOTALL,
        )
        if type_match:
            dotnet_type = type_match.group(1).split(",", 1)[0].strip()
            if not _is_optimizable_type(dotnet_type):
                # Drop the whole <Parameter>...</Parameter> block. The
                # strategy still gets the property value from the <Strategy>
                # section above; only the optimizer parameter goes away.
                return ""
        value_match = re.search(
            r"<ValueSerializable>(.*?)</ValueSerializable>", chunk, re.DOTALL,
        )
        if not value_match:
            return chunk
        value = value_match.group(1).strip()
        chunk = re.sub(
            r"(<Min(?:\s+[^>]*)?>)(.*?)(</Min>)",
            lambda m: m.group(1) + value + m.group(3),
            chunk,
            count=1,
            flags=re.DOTALL,
        )
        chunk = re.sub(
            r"(<Max(?:\s+[^>]*)?>)(.*?)(</Max>)",
            lambda m: m.group(1) + value + m.group(3),
            chunk,
            count=1,
            flags=re.DOTALL,
        )
        return chunk

    pinned = re.sub(
        r"<Parameter>.*?</Parameter>", _pin, block, flags=re.DOTALL,
    )
    # Drop empty lines left behind by removed blocks.
    pinned = re.sub(r"\n\s*\n", "\n", pinned)
    return text.replace(block, pinned, 1)


def _ensure_placeholder_parameter(text: str) -> str:
    """Append the synthetic ``__recipe_placeholder__`` sweep into an existing
    ``<OptimizationParameters>`` block when no real swept parameter is present.

    The placeholder is stripped at recipe-stage generation time
    (see ``_strip_recipe_placeholder_parameter``) so it never reaches NT in
    a stage template.
    """
    if "__recipe_placeholder__" in text:
        return text
    placeholder = (
        "      <Parameter>\n"
        "        <EnumValuesSerializable />\n"
        "        <Increment>1</Increment>\n"
        "        <Max xsi:type=\"xsd:int\">2</Max>\n"
        "        <Min xsi:type=\"xsd:int\">1</Min>\n"
        "        <Name>__recipe_placeholder__</Name>\n"
        "        <ParameterTypeSerializable>System.Int32, mscorlib, Version=4.0.0.0, Culture=neutral, PublicKeyToken=b77a5c561934e089</ParameterTypeSerializable>\n"
        "        <ValueSerializable>1</ValueSerializable>\n"
        "      </Parameter>\n    </ArrayOfParameter>"
    )
    if "</ArrayOfParameter>" not in text:
        return text
    return text.replace("</ArrayOfParameter>", placeholder, 1)


def _insert_placeholder_optimization_parameters(text: str) -> str:
    """Insert a placeholder OptimizationParameters block if missing.

    Min < Max is required for the template parser to count this as swept.
    The recipe stage code rewrites this block to reflect the user's real
    matrix and sweep configuration; the placeholder just opens the door.
    """
    block = (
        "  <OptimizationParameters>\n"
        "    <ArrayOfParameter xmlns:xsd=\"http://www.w3.org/2001/XMLSchema\" "
        "xmlns:xsi=\"http://www.w3.org/2001/XMLSchema-instance\">\n"
        "      <Parameter>\n"
        "        <EnumValuesSerializable />\n"
        "        <Increment>1</Increment>\n"
        "        <Max xsi:type=\"xsd:int\">2</Max>\n"
        "        <Min xsi:type=\"xsd:int\">1</Min>\n"
        "        <Name>__recipe_placeholder__</Name>\n"
        "        <ParameterTypeSerializable>System.Int32, mscorlib, Version=4.0.0.0, Culture=neutral, PublicKeyToken=b77a5c561934e089</ParameterTypeSerializable>\n"
        "        <ValueSerializable>1</ValueSerializable>\n"
        "      </Parameter>\n"
        "    </ArrayOfParameter>\n"
        "  </OptimizationParameters>"
    )
    if "</Strategy>" in text:
        return text.replace("</Strategy>", "</Strategy>\n" + block, 1)
    if "</StrategyTemplate>" in text:
        return text.replace(
            "</StrategyTemplate>", block + "\n</StrategyTemplate>", 1,
        )
    return text + "\n" + block


