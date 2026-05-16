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
_RANGE_RE = re.compile(r"\[Range\s*\(\s*(?P<min>[^,]+?)\s*,\s*(?P<max>[^)]+?)\s*\)\s*\]")
_DISPLAY_KV_RE = re.compile(r'(\w+)\s*=\s*("[^"]*"|[^,\)]+)')
_ASSIGN_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+?)\s*;\s*$")


def _parse_literal(raw: str) -> Any:
    s = raw.strip()
    if s.lower() == "true":
        return True
    if s.lower() == "false":
        return False
    if len(s) >= 2 and s.startswith('"') and s.endswith('"'):
        return s[1:-1]
    cleaned = s.replace("_", "").rstrip("fFdDmM")
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
    follow-on attributes and property declaration."""
    parameters: list[StrategyParameter] = []
    defaults = _extract_defaults(cs_text)
    pending: dict[str, Any] | None = None
    multi_line_attr: list[str] | None = None

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

        m = _PROP_RE.search(line)
        if not m:
            pending = None
            continue

        name = m.group("name")
        type_name = m.group("type")
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
