from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Mapping, Sequence
from xml.etree import ElementTree as ET


XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"
XSD_NS = "http://www.w3.org/2001/XMLSchema"

ET.register_namespace("xsi", XSI_NS)
ET.register_namespace("xsd", XSD_NS)


@dataclass(frozen=True)
class ParameterRange:
    minimum: str
    maximum: str
    increment: str
    value: str | None = None


DEFAULT_PASS1_RANGES: dict[str, ParameterRange] = {
    "UseTimeFilter": ParameterRange("true", "true", "1", "True"),
    "DurationTimeH": ParameterRange("2", "4", "2", "2"),
    "DurationTimeM": ParameterRange("0", "0", "1", "0"),
    "averageFast": ParameterRange("5", "5", "1", "5"),
    "averageSlow": ParameterRange("50", "500", "50", "200"),
    "MaxStop": ParameterRange("50", "350", "50", "200"),
    "MaxTPRatio": ParameterRange("0.5", "2", "0.5", "0.5"),
    "Long": ParameterRange("true", "true", "1", "True"),
    "Short": ParameterRange("true", "true", "1", "True"),
    "UseMaxStop": ParameterRange("true", "true", "1", "True"),
    "UseMaxTP": ParameterRange("true", "true", "1", "True"),
}


def generate_broad_discovery_templates(
    seed_template: str | Path,
    output_dir: str | Path,
    *,
    start_hours: Sequence[int] = (0, 4, 8, 12, 16, 20),
    modes: Sequence[str] = ("breakout", "regression"),
    ranges: Mapping[str, ParameterRange] | None = None,
    optimizer_type: str | None = None,
    optimization_fitness: str | None = None,
) -> list[Path]:
    seed = Path(seed_template)
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    parameter_ranges = dict(DEFAULT_PASS1_RANGES)
    if ranges:
        parameter_ranges.update(ranges)

    generated: list[Path] = []
    for mode in modes:
        reverse = mode.lower() == "regression"
        mode_dir = destination / mode.lower()
        mode_dir.mkdir(parents=True, exist_ok=True)
        for start_hour in start_hours:
            output_path = mode_dir / f"Pass1_{mode.capitalize()}_Start{start_hour:02d}.xml"
            ranges_for_template = dict(parameter_ranges)
            ranges_for_template.update(
                {
                    "StartTimeH": ParameterRange(str(start_hour), str(start_hour), "1", str(start_hour)),
                    "StartTimeM": ParameterRange("0", "0", "1", "0"),
                    "Reverse": ParameterRange(str(reverse).lower(), str(reverse).lower(), "1", str(reverse)),
                }
            )
            _write_patched_template(
                seed,
                output_path,
                ranges_for_template,
                optimizer_type=optimizer_type,
                optimization_fitness=optimization_fitness,
            )
            generated.append(output_path)
    return generated


def generate_candidate_refinement_template(
    seed_template: str | Path,
    output_path: str | Path,
    *,
    start_hour: int,
    duration_hours: int,
    reverse: bool,
    average_slow: int,
    max_stop: int,
    max_tp_ratio: float,
    average_fast_min: int = 2,
    average_fast_max: int = 10,
    optimizer_type: str | None = None,
    optimization_fitness: str | None = None,
) -> Path:
    tp_ratio = Decimal(str(max_tp_ratio))

    ranges = {
        "StartTimeH": ParameterRange(str(start_hour), str(start_hour), "1", str(start_hour)),
        "StartTimeM": ParameterRange("0", "0", "1", "0"),
        "DurationTimeH": ParameterRange(str(duration_hours), str(duration_hours), "1", str(duration_hours)),
        "DurationTimeM": ParameterRange("0", "0", "1", "0"),
        "averageFast": ParameterRange(str(average_fast_min), str(average_fast_max), "1", str(average_fast_min)),
        "averageSlow": ParameterRange(str(max(2, average_slow - 20)), str(average_slow + 20), "10", str(average_slow)),
        "MaxStop": ParameterRange(str(max(1, max_stop - 20)), str(max_stop + 20), "20", str(max_stop)),
        "MaxTPRatio": ParameterRange(
            _decimal_str(max(Decimal("0.1"), tp_ratio - Decimal("0.3"))),
            _decimal_str(tp_ratio + Decimal("0.3")),
            "0.1",
            _decimal_str(tp_ratio),
        ),
        "Long": ParameterRange("false", "true", "1", "True"),
        "Short": ParameterRange("false", "true", "1", "True"),
        "Reverse": ParameterRange(str(reverse).lower(), str(reverse).lower(), "1", str(reverse)),
    }
    target = Path(output_path)
    _write_patched_template(
        Path(seed_template),
        target,
        ranges,
        optimizer_type=optimizer_type,
        optimization_fitness=optimization_fitness,
    )
    return target


def generate_daily_risk_template(
    seed_template: str | Path,
    output_path: str | Path,
    *,
    start_hour: int,
    duration_hours: int,
    reverse: bool,
    average_fast: int,
    average_slow: int,
    max_stop: int,
    max_tp_ratio: float,
    long_enabled: bool,
    short_enabled: bool,
    optimizer_type: str | None = None,
    optimization_fitness: str | None = None,
) -> Path:
    fixed = {
        "StartTimeH": ParameterRange(str(start_hour), str(start_hour), "1", str(start_hour)),
        "StartTimeM": ParameterRange("0", "0", "1", "0"),
        "DurationTimeH": ParameterRange(str(duration_hours), str(duration_hours), "1", str(duration_hours)),
        "DurationTimeM": ParameterRange("0", "0", "1", "0"),
        "averageFast": ParameterRange(str(average_fast), str(average_fast), "1", str(average_fast)),
        "averageSlow": ParameterRange(str(average_slow), str(average_slow), "1", str(average_slow)),
        "MaxStop": ParameterRange(str(max_stop), str(max_stop), "1", str(max_stop)),
        "MaxTPRatio": ParameterRange(_decimal_str(Decimal(str(max_tp_ratio))), _decimal_str(Decimal(str(max_tp_ratio))), "0.1", _decimal_str(Decimal(str(max_tp_ratio)))),
        "Long": ParameterRange(str(long_enabled).lower(), str(long_enabled).lower(), "1", str(long_enabled)),
        "Short": ParameterRange(str(short_enabled).lower(), str(short_enabled).lower(), "1", str(short_enabled)),
        "Reverse": ParameterRange(str(reverse).lower(), str(reverse).lower(), "1", str(reverse)),
        "ProfitStop": ParameterRange("1", "1001", "1000", "1001"),
        "LossStop": ParameterRange("1", "801", "800", "801"),
        "MaxTrades": ParameterRange("1", "20", "1", "3"),
    }
    target = Path(output_path)
    _write_patched_template(
        Path(seed_template),
        target,
        fixed,
        optimizer_type=optimizer_type,
        optimization_fitness=optimization_fitness,
    )
    return target


def _write_patched_template(
    seed: Path,
    target: Path,
    ranges: Mapping[str, ParameterRange],
    *,
    optimizer_type: str | None = None,
    optimization_fitness: str | None = None,
) -> None:
    text = seed.read_text(encoding="utf-8")
    if optimizer_type:
        text = _replace_tag_text(text, "OptimizerType", optimizer_type, count=1)
    if optimization_fitness:
        text = _replace_tag_text(text, "OptimizationFitness", optimization_fitness, count=1)
    for name, parameter_range in ranges.items():
        text = _patch_parameter_text(text, name, parameter_range)
        value = parameter_range.value if parameter_range.value is not None else parameter_range.minimum
        text = _patch_strategy_value_text(text, name, _strategy_value(value))
    text = _prune_fixed_optimization_parameters_text(text, ranges)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def _patch_parameter_text(text: str, name: str, parameter_range: ParameterRange) -> str:
    pattern = re.compile(
        r"(?P<block><Parameter>(?:(?!</Parameter>).)*<Name>"
        + re.escape(name)
        + r"</Name>(?:(?!</Parameter>).)*</Parameter>)",
        re.DOTALL,
    )

    def replace(match: re.Match[str]) -> str:
        block = match.group("block")
        block = _replace_tag_text(block, "Increment", parameter_range.increment)
        block = _replace_tag_text(block, "Min", parameter_range.minimum)
        block = _replace_tag_text(block, "Max", parameter_range.maximum)
        if parameter_range.value is not None:
            block = _replace_tag_text(block, "ValueSerializable", parameter_range.value)
        return block

    return pattern.sub(replace, text, count=1)


def _patch_strategy_value_text(text: str, name: str, value: str) -> str:
    return _replace_tag_text(text, name, value, count=1)


def _prune_fixed_optimization_parameters_text(text: str, ranges: Mapping[str, ParameterRange]) -> str:
    swept_names = {
        name
        for name, parameter_range in ranges.items()
        if _normalized_range_value(parameter_range.minimum) != _normalized_range_value(parameter_range.maximum)
    }
    if not swept_names:
        return text

    pattern = re.compile(r"\s*<Parameter>(?:(?!</Parameter>).)*<Name>(?P<name>.*?)</Name>(?:(?!</Parameter>).)*</Parameter>", re.DOTALL)

    def replace(match: re.Match[str]) -> str:
        name = match.group("name").strip()
        return match.group(0) if name in swept_names else ""

    return pattern.sub(replace, text)


def _normalized_range_value(value: str) -> str:
    return str(value).strip().lower()


def _replace_tag_text(text: str, tag: str, value: str, count: int = 0) -> str:
    pattern = re.compile(
        r"(<" + re.escape(tag) + r"(?:\s+[^>]*)?>)(.*?)(</" + re.escape(tag) + r">)",
        re.DOTALL,
    )
    return pattern.sub(r"\g<1>" + str(value) + r"\g<3>", text, count=count)


def _patch_parameter_range(root: ET.Element, name: str, parameter_range: ParameterRange) -> None:
    parameter = _find_parameter(root, name)
    if parameter is None:
        return
    _set_child_text(parameter, "Increment", parameter_range.increment)
    _set_child_text(parameter, "Min", parameter_range.minimum)
    _set_child_text(parameter, "Max", parameter_range.maximum)
    if parameter_range.value is not None:
        _set_child_text(parameter, "ValueSerializable", parameter_range.value)


def _find_parameter(root: ET.Element, name: str) -> ET.Element | None:
    for parameter in root.findall("./OptimizationParameters/ArrayOfParameter/Parameter"):
        name_node = parameter.find("Name")
        if name_node is not None and (name_node.text or "").strip() == name:
            return parameter
    return None


def _patch_strategy_value(root: ET.Element, name: str, value: str) -> None:
    strategy_node = root.find("Strategy")
    if strategy_node is None or len(strategy_node) == 0:
        return
    strategy = next(iter(strategy_node))
    child = strategy.find(name)
    if child is None:
        child = ET.SubElement(strategy, name)
    child.text = value


def _set_child_text(parent: ET.Element, child_name: str, value: str) -> None:
    child = parent.find(child_name)
    if child is None:
        child = ET.SubElement(parent, child_name)
    child.text = value


def _decimal_str(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _strategy_value(value: str) -> str:
    if value == "True":
        return "true"
    if value == "False":
        return "false"
    return value


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate Pantheon broad discovery optimization templates from a seed XML.")
    parser.add_argument("--seed-template", required=True, help="Seed Strategy Analyzer XML template.")
    parser.add_argument("--output-dir", required=True, help="Folder where generated templates should be written.")
    parser.add_argument(
        "--start-hours",
        default="0,4,8,12,16,20",
        help="Comma-separated start hours. Default: 0,4,8,12,16,20.",
    )
    parser.add_argument(
        "--modes",
        default="breakout,regression",
        help="Comma-separated modes. Default: breakout,regression.",
    )
    parser.add_argument(
        "--optimizer-type",
        default="",
        help="Optional fully qualified NinjaTrader optimizer type to write into generated templates.",
    )
    parser.add_argument(
        "--optimization-fitness",
        default="",
        help="Optional fully qualified NinjaTrader optimization fitness type to write into generated templates.",
    )
    args = parser.parse_args(argv)

    start_hours = tuple(int(part.strip()) for part in args.start_hours.split(",") if part.strip())
    modes = tuple(part.strip().lower() for part in args.modes.split(",") if part.strip())
    generated = generate_broad_discovery_templates(
        args.seed_template,
        args.output_dir,
        start_hours=start_hours,
        modes=modes,
        optimizer_type=args.optimizer_type or None,
        optimization_fitness=args.optimization_fitness or None,
    )
    print(f"Generated {len(generated)} broad discovery templates in {Path(args.output_dir)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
