from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable
from xml.etree import ElementTree as ET


@dataclass(frozen=True)
class OptimizationParameter:
    name: str
    minimum: str
    maximum: str
    increment: str
    value: str
    type_name: str

    @property
    def is_swept(self) -> bool:
        return self.minimum != self.maximum

    @property
    def estimated_steps(self) -> int:
        if not self.is_swept:
            return 1
        if "Boolean" in self.type_name:
            return 2
        try:
            minimum = Decimal(self.minimum)
            maximum = Decimal(self.maximum)
            increment = Decimal(self.increment)
        except InvalidOperation:
            return 1
        if increment <= 0:
            return 1
        return int((maximum - minimum) // increment) + 1


@dataclass(frozen=True)
class StrategyOptimizationTemplate:
    path: Path
    strategy_type: str
    optimizer_type: str
    optimization_fitness: str
    parameters: tuple[OptimizationParameter, ...]
    strategy_values: dict[str, str]
    instrument_or_instrument_list: str = ""

    @property
    def swept_parameters(self) -> tuple[OptimizationParameter, ...]:
        return tuple(parameter for parameter in self.parameters if parameter.is_swept)

    @property
    def estimated_combinations(self) -> int:
        combinations = 1
        for parameter in self.swept_parameters:
            combinations *= parameter.estimated_steps
        return combinations

    @property
    def mode(self) -> str:
        return "regression" if _as_bool(self.strategy_values.get("Reverse")) else "breakout"

    @property
    def start_hour(self) -> int | None:
        return _as_int(self.strategy_values.get("StartTimeH"))

    @property
    def duration_hours(self) -> int | None:
        return _as_int(self.strategy_values.get("DurationTimeH"))


def parse_strategy_optimization_template(path: str | Path) -> StrategyOptimizationTemplate:
    template_path = Path(path)
    root = ET.parse(template_path).getroot()
    strategy_node = root.find("Strategy")
    strategy_values = _read_strategy_values(strategy_node)

    return StrategyOptimizationTemplate(
        path=template_path,
        strategy_type=_text(root.find("StrategyType")),
        optimizer_type=_text(root.find("OptimizerType")),
        optimization_fitness=_text(root.find("OptimizationFitness")),
        parameters=tuple(_read_optimization_parameters(root)),
        strategy_values=strategy_values,
        instrument_or_instrument_list=strategy_values.get("InstrumentOrInstrumentList", ""),
    )


def parse_strategy_optimization_templates(paths: Iterable[str | Path]) -> tuple[StrategyOptimizationTemplate, ...]:
    return tuple(parse_strategy_optimization_template(path) for path in paths)


def _read_optimization_parameters(root: ET.Element) -> list[OptimizationParameter]:
    parameters: list[OptimizationParameter] = []
    for node in root.findall("./OptimizationParameters/ArrayOfParameter/Parameter"):
        parameters.append(
            OptimizationParameter(
                name=_text(node.find("Name")),
                minimum=_text(node.find("Min")),
                maximum=_text(node.find("Max")),
                increment=_text(node.find("Increment")),
                value=_text(node.find("ValueSerializable")),
                type_name=_text(node.find("ParameterTypeSerializable")),
            )
        )
    return parameters


def _read_strategy_values(strategy_node: ET.Element | None) -> dict[str, str]:
    if strategy_node is None or len(strategy_node) == 0:
        return {}
    strategy = next(iter(strategy_node))
    values: dict[str, str] = {}
    for child in strategy:
        if len(child) == 0:
            values[child.tag] = _text(child)
    return values


def _text(node: ET.Element | None) -> str:
    if node is None or node.text is None:
        return ""
    return node.text.strip()


def _as_bool(value: str | None) -> bool:
    return str(value or "").strip().lower() == "true"


def _as_int(value: str | None) -> int | None:
    try:
        return int(str(value or "").strip())
    except ValueError:
        return None
