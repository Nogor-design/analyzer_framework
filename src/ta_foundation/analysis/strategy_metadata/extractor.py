from __future__ import annotations

from pathlib import Path
import re
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional

from .models import StrategyProfile, TemplatePreset


_ASSIGNMENT_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+?)\s*;\s*$")
_PROPERTY_RE = re.compile(r"public\s+[A-Za-z0-9_<>\.]+\s+([A-Za-z_][A-Za-z0-9_]*)\s*\{\s*get;\s*set;\s*\}")


def _parse_literal(raw: str) -> Any:
    s = raw.strip()
    if s.lower() == "true":
        return True
    if s.lower() == "false":
        return False
    if s.startswith('"') and s.endswith('"') and len(s) >= 2:
        return s[1:-1]

    # numeric parse
    cleaned = s.replace("_", "")
    try:
        if any(ch in cleaned for ch in [".", "e", "E"]):
            return float(cleaned)
        return int(cleaned)
    except Exception:
        return s


def _extract_defaults_from_cs(cs_text: str) -> Dict[str, Any]:
    defaults: Dict[str, Any] = {}
    in_setdefaults = False

    for line in cs_text.splitlines():
        if "if (State == State.SetDefaults)" in line:
            in_setdefaults = True
            continue
        if in_setdefaults and "else if (State ==" in line:
            break
        if not in_setdefaults:
            continue

        m = _ASSIGNMENT_RE.match(line)
        if not m:
            continue
        key, raw_val = m.group(1), m.group(2)
        defaults[key] = _parse_literal(raw_val)

    return defaults


def _extract_properties_from_cs(cs_text: str) -> List[str]:
    props: List[str] = []
    for m in _PROPERTY_RE.finditer(cs_text):
        props.append(m.group(1))
    return sorted(set(props))


def _parse_template_values(xml_path: Path) -> TemplatePreset:
    tree = ET.parse(xml_path)
    root = tree.getroot()

    strategy_type_node = root.find("./StrategyType")
    strategy_type = (strategy_type_node.text or "").strip() if strategy_type_node is not None else ""

    container = root.find("./Strategy")
    strategy_node = None
    if container is not None:
        for child in container:
            strategy_node = child
            break

    values: Dict[str, Any] = {}
    if strategy_node is not None:
        for child in strategy_node:
            if list(child):
                continue
            tag = child.tag.split("}")[-1]
            text = (child.text or "").strip()
            if not text:
                continue
            values[tag] = _parse_literal(text)

    return TemplatePreset(
        name=xml_path.stem,
        strategy_type=strategy_type,
        path=str(xml_path),
        values=values,
    )


def _extract_annotations_from_md(md_text: str) -> Dict[str, Any]:
    low = md_text.lower()
    return {
        "mentions": {
            "crossover": "cross" in low,
            "trend_filter": "usetrend" in low or "trend filter" in low,
            "reverse": "reverse" in low,
            "time_filter": "time filter" in low or "usetimefilter" in low,
            "kill_switch": "kill" in low,
        }
    }


def build_strategy_profile(strategy_id: str, strategies_root: Optional[Path] = None) -> Dict[str, Any]:
    root = Path(strategies_root) if strategies_root is not None else Path(__file__).resolve().parents[2] / "strategies"
    strategy_dir = root / strategy_id

    profile = StrategyProfile(
        strategy_id=strategy_id,
        strategy_dir=str(strategy_dir),
        source_files={},
    )

    if not strategy_dir.exists():
        profile.warnings.append(f"strategy_dir_missing: {strategy_dir}")
        return profile.as_dict()

    cs_path = strategy_dir / f"{strategy_id}.cs"
    md_path = strategy_dir / f"{strategy_id}.md"
    templates_dir = strategy_dir / "templates"

    if cs_path.exists():
        cs_text = cs_path.read_text(encoding="utf-8", errors="ignore")
        profile.source_files["cs"] = str(cs_path)
        profile.defaults = _extract_defaults_from_cs(cs_text)

        prop_names = _extract_properties_from_cs(cs_text)
        profile.parameters = {
            name: {
                "name": name,
                "default": profile.defaults.get(name),
                "has_default": name in profile.defaults,
                "source": str(cs_path),
            }
            for name in prop_names
        }
    else:
        profile.warnings.append(f"cs_missing: {cs_path}")

    if md_path.exists():
        md_text = md_path.read_text(encoding="utf-8", errors="ignore")
        profile.source_files["md"] = str(md_path)
        profile.annotations = _extract_annotations_from_md(md_text)

    if templates_dir.exists():
        xml_files = sorted(templates_dir.glob("*.xml"))
        for xml_path in xml_files:
            try:
                preset = _parse_template_values(xml_path)
                profile.template_presets.append(preset)
            except Exception as e:
                profile.warnings.append(f"template_parse_failed:{xml_path.name}:{type(e).__name__}:{e}")
        profile.source_files["templates_dir"] = str(templates_dir)
    else:
        profile.warnings.append(f"templates_missing: {templates_dir}")

    return profile.as_dict()
