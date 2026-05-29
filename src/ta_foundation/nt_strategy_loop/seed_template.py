from __future__ import annotations

"""Generate provisional NinjaTrader Strategy Analyzer seed templates.

User-saved Strategy Analyzer templates remain the best source of truth because
NinjaTrader XML can carry platform-specific serialization details. This module
creates a bootstrap template for generated strategies so the autonomous loop can
move from compile-clean source to a first optimizer plan without hand authoring
XML.
"""

import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Sequence
from xml.sax.saxutils import escape


@dataclass(frozen=True)
class StrategyParameter:
    name: str
    type_name: str
    default: str
    minimum: str
    maximum: str
    increment: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class GeneratedSeedTemplate:
    strategy_name: str
    path: str
    parameter_count: int
    swept_parameter_count: int
    warning: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SeedTemplateError(RuntimeError):
    pass


def generate_seed_template_from_source(
    source_path: str | Path,
    output_path: str | Path,
    *,
    strategy_name: str | None = None,
    instrument: str = "NQ 06-26",
    optimizer_type: str = "NinjaTrader.NinjaScript.Optimizers.DefaultOptimizer",
    optimization_fitness: str = "NinjaTrader.NinjaScript.OptimizationFitnesses.MaxNetProfit",
    keep_best_results: int = 500,
    from_date: str = "2026-04-14T00:00:00",
    to_date: str = "2026-05-14T00:00:00",
) -> GeneratedSeedTemplate:
    source = Path(source_path)
    if not source.is_file():
        raise SeedTemplateError(f"Strategy source not found: {source}")
    text = source.read_text(encoding="utf-8-sig", errors="replace")
    name = strategy_name or _detect_strategy_name(text) or source.stem
    parameters = extract_strategy_parameters(text)
    if not parameters:
        raise SeedTemplateError(f"No NinjaScriptProperty parameters found in {source}")

    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        render_seed_template(
            strategy_name=name,
            parameters=parameters,
            instrument=instrument,
            optimizer_type=optimizer_type,
            optimization_fitness=optimization_fitness,
            keep_best_results=keep_best_results,
            from_date=from_date,
            to_date=to_date,
        ),
        encoding="utf-8",
    )
    return GeneratedSeedTemplate(
        strategy_name=name,
        path=str(destination.resolve()),
        parameter_count=len(parameters),
        swept_parameter_count=sum(1 for parameter in parameters if parameter.minimum != parameter.maximum),
        warning=(
            "Generated bootstrap seed. Prefer replacing it with a NinjaTrader-saved "
            "Strategy Analyzer template before serious optimization."
        ),
    )


def extract_strategy_parameters(source_text: str) -> list[StrategyParameter]:
    defaults = _state_setdefaults(source_text)
    declarations = re.finditer(
        r"\[NinjaScriptProperty\](?P<attrs>.*?)(?:public)\s+"
        r"(?P<type>[A-Za-z_][A-Za-z0-9_<>.?]*)\s+"
        r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\{\s*get;\s*set;\s*\}",
        source_text,
        re.DOTALL,
    )
    parameters: list[StrategyParameter] = []
    for match in declarations:
        type_name = match.group("type").strip()
        name = match.group("name").strip()
        default = defaults.get(name) or _default_for_type(type_name)
        range_values = _range_values(match.group("attrs"))
        minimum, maximum = _bounded_sweep(default, type_name, range_values)
        increment = _increment_for_type(type_name, minimum, maximum)
        parameters.append(
            StrategyParameter(
                name=name,
                type_name=_dotnet_type(type_name),
                default=_serialize(default, type_name),
                minimum=_serialize(minimum, type_name),
                maximum=_serialize(maximum, type_name),
                increment=increment,
            )
        )
    return parameters


# The ~70 base-class fields NinjaTrader 8 serializes for every strategy inside
# a saved Strategy Analyzer template, transcribed verbatim from a real
# NinjaTrader-saved optimizer template (the proven web-optimizer seed format).
# `{name}`, `{instrument}`, `{from_date}`, `{to_date}` are the only per-run
# substitutions. Crucially this carries `<Category>Optimize</Category>` as a
# strategy property — without it NinjaTrader's Strategy Analyzer rejects the
# run with "unknown category 'NinjaScript'".
_STRATEGY_BOILERPLATE = """\
      <IsVisible>true</IsVisible>
      <calculate2>OnBarClose</calculate2>
      <AreLinesConfigurable>true</AreLinesConfigurable>
      <ArePlotsConfigurable>true</ArePlotsConfigurable>
      <BarsPeriodSerializable>
        <BarsPeriodTypeSerialize>4</BarsPeriodTypeSerialize>
        <BaseBarsPeriodType>Minute</BaseBarsPeriodType>
        <BaseBarsPeriodValue>1</BaseBarsPeriodValue>
        <VolumetricDeltaType>BidAsk</VolumetricDeltaType>
        <MarketDataType>Last</MarketDataType>
        <PointAndFigurePriceType>Close</PointAndFigurePriceType>
        <ReversalType>Tick</ReversalType>
        <Value>1</Value>
        <Value2>1</Value2>
      </BarsPeriodSerializable>
      <BarsToLoad>0</BarsToLoad>
      <Calculate>OnBarClose</Calculate>
      <Displacement>0</Displacement>
      <DisplayInDataBox>true</DisplayInDataBox>
      <From>{from_date}</From>
      <IsAutoScale>true</IsAutoScale>
      <Lines />
      <MaximumBarsLookBack>TwoHundredFiftySix</MaximumBarsLookBack>
      <Name>{name}</Name>
      <Panel>-1</Panel>
      <Plots />
      <ScaleJustification>Right</ScaleJustification>
      <ShowTransparentPlotsInDataBox>false</ShowTransparentPlotsInDataBox>
      <To>{to_date}</To>
      <IsDataSeriesRequired>true</IsDataSeriesRequired>
      <IsOverlay>true</IsOverlay>
      <SelectedValueSeries>0</SelectedValueSeries>
      <Gtd>1800-01-01T00:00:00</Gtd>
      <Template />
      <TimeInForce>Gtc</TimeInForce>
      <BarsPeriodParameter>
        <Increment>1</Increment>
        <Max xsi:type="xsd:int">0</Max>
        <Min xsi:type="xsd:int">0</Min>
        <Name />
        <ParameterTypeSerializable>System.Int32, mscorlib, Version=4.0.0.0, Culture=neutral, PublicKeyToken=b77a5c561934e089</ParameterTypeSerializable>
        <ValueSerializable>0</ValueSerializable>
      </BarsPeriodParameter>
      <BarsRequiredToTrade>20</BarsRequiredToTrade>
      <Category>Optimize</Category>
      <ConnectionLossHandling>Recalculate</ConnectionLossHandling>
      <DaysToLoad>5</DaysToLoad>
      <DefaultQuantity>1</DefaultQuantity>
      <DisconnectDelaySeconds>10</DisconnectDelaySeconds>
      <EntriesPerDirection>1</EntriesPerDirection>
      <EntryHandling>AllEntries</EntryHandling>
      <ExitOnSessionCloseSeconds>30</ExitOnSessionCloseSeconds>
      <IncludeCommission>false</IncludeCommission>
      <InstrumentOrInstrumentList>{instrument}</InstrumentOrInstrumentList>
      <IsAggregated>false</IsAggregated>
      <IsExitOnSessionCloseStrategy>true</IsExitOnSessionCloseStrategy>
      <IsFillLimitOnTouch>false</IsFillLimitOnTouch>
      <IsOptimizeDataSeries>false</IsOptimizeDataSeries>
      <IsStableSession>true</IsStableSession>
      <IsTickReplay>false</IsTickReplay>
      <IsTradingHoursBreakLineVisible>true</IsTradingHoursBreakLineVisible>
      <IsWaitUntilFlat>false</IsWaitUntilFlat>
      <NumberRestartAttempts>4</NumberRestartAttempts>
      <OptimizationPeriod>10</OptimizationPeriod>
      <OrderFillResolution>High</OrderFillResolution>
      <OrderFillResolutionType>Tick</OrderFillResolutionType>
      <OrderFillResolutionValue>1</OrderFillResolutionValue>
      <RestartsWithinMinutes>5</RestartsWithinMinutes>
      <SetOrderQuantity>Strategy</SetOrderQuantity>
      <Slippage>0</Slippage>
      <StartBehavior>WaitUntilFlat</StartBehavior>
      <StopTargetHandling>PerEntryExecution</StopTargetHandling>
      <SupportsOptimizationGraph>true</SupportsOptimizationGraph>
      <TestPeriod>28</TestPeriod>
      <TradingHoursSerializable />
      <DrawOnPricePanel>false</DrawOnPricePanel>
      <ZOrder>-2147483648</ZOrder>"""

_XML_NS = 'xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'


def render_seed_template(
    *,
    strategy_name: str,
    parameters: Sequence[StrategyParameter],
    instrument: str,
    optimizer_type: str,
    optimization_fitness: str,
    keep_best_results: int,
    from_date: str,
    to_date: str,
) -> str:
    """Render a NinjaTrader 8 Strategy Analyzer optimizer template.

    Emits the `<StrategyTemplate>` format NinjaTrader actually accepts — the
    same shape a user-saved optimizer template has — not the legacy synthetic
    `<NinjaTrader>` shape, which NT rejected with "unknown category
    'NinjaScript'" because it lacked the strategy's `<Category>` property.
    """
    name = escape(strategy_name)
    strategy_values = "\n".join(
        f"      <{escape(parameter.name)}>{escape(_strategy_value(parameter.default))}</{escape(parameter.name)}>"
        for parameter in parameters
    )
    # NinjaTrader's Strategy Analyzer optimizer can only enumerate numeric and
    # boolean parameters. Emitting <Parameter> blocks for System.String,
    # System.DateTime, Brush, enum, or other reference types crashes the
    # optimizer at WaitForIterationsCompleted with a NullReferenceException
    # the moment it tries to call .GetType() on the enumerated value. The
    # strategy still receives the default for those properties via the
    # <Strategy> block above; they just don't get an OptimizationParameter.
    optimization_parameters = "\n".join(
        _parameter_xml(parameter)
        for parameter in parameters
        if _is_optimizable_type(parameter.type_name)
    )
    boilerplate = _STRATEGY_BOILERPLATE.format(
        name=name,
        instrument=escape(instrument),
        from_date=escape(from_date),
        to_date=escape(to_date),
    )
    return f"""<?xml version="1.0" encoding="utf-8"?>
<StrategyTemplate>
  <StrategyType>NinjaTrader.NinjaScript.Strategies.{name}</StrategyType>
  <OptimizerType>{escape(optimizer_type)}</OptimizerType>
  <OptimizerParameters>
    <ArrayOfParameterWrapper {_XML_NS}>
      <ParameterWrapper>
        <DisplayName>IsStrategyGenerator</DisplayName>
        <Name>IsStrategyGenerator</Name>
        <Value xsi:type="xsd:boolean">false</Value>
      </ParameterWrapper>
      <ParameterWrapper>
        <DisplayName>Keep best # results</DisplayName>
        <Name>KeepBestResults</Name>
        <Value xsi:type="xsd:int">{int(keep_best_results)}</Value>
      </ParameterWrapper>
      <ParameterWrapper>
        <DisplayName>LogTypeName</DisplayName>
        <Name>LogTypeName</Name>
        <Value xsi:type="xsd:string">Optimizer</Value>
      </ParameterWrapper>
      <ParameterWrapper>
        <DisplayName>Visible</DisplayName>
        <Name>IsVisible</Name>
        <Value xsi:type="xsd:boolean">true</Value>
      </ParameterWrapper>
      <ParameterWrapper>
        <DisplayName>Name</DisplayName>
        <Name>Name</Name>
        <Value xsi:type="xsd:string">Default</Value>
      </ParameterWrapper>
    </ArrayOfParameterWrapper>
  </OptimizerParameters>
  <OptimizationFitness>{escape(optimization_fitness)}</OptimizationFitness>
  <OptimizationParameters>
    <ArrayOfParameter {_XML_NS}>
{optimization_parameters}
    </ArrayOfParameter>
  </OptimizationParameters>
  <Strategy>
    <{name} {_XML_NS}>
{boilerplate}
{strategy_values}
    </{name}>
  </Strategy>
</StrategyTemplate>
"""


_OPTIMIZABLE_DOTNET_TYPES = frozenset({
    "System.Boolean",
    "System.Int16",
    "System.Int32",
    "System.Int64",
    "System.Double",
    "System.Decimal",
})


def _is_optimizable_type(dotnet_type: str) -> bool:
    """Whether NinjaTrader's optimizer can enumerate values of this type.

    Matched against the empirical behavior of NinjaTrader's own
    ``Save as Template`` action: int (16/32/64), double, decimal, and bool
    appear in ``<OptimizationParameters>`` blocks; **System.Single (float),
    System.String, System.DateTime, enums, and Brush types do not**. Emitting
    a ``<Parameter>`` block for one of those crashes NinjaTrader's Strategy
    Analyzer at ``Optimizer.WaitForIterationsCompleted`` with a
    ``NullReferenceException`` the moment the optimizer calls ``.GetType()``
    on the deserialized parameter value (which is null because NT's XML
    deserializer rejects the type/xsi:type pairing). The strategy itself
    still receives the property value via the ``<Strategy>`` block — only
    the optimizer entry goes away.
    """
    return str(dotnet_type or "").strip() in _OPTIMIZABLE_DOTNET_TYPES


def _parameter_xml(parameter: StrategyParameter) -> str:
    """Render one `<OptimizationParameters>` `<Parameter>` block in NT's order."""
    xsd = _xsd_type(parameter.type_name)
    full_type = (
        f"{parameter.type_name}, mscorlib, Version=4.0.0.0, "
        "Culture=neutral, PublicKeyToken=b77a5c561934e089"
    )
    return f"""      <Parameter>
        <EnumValuesSerializable />
        <Increment>{escape(parameter.increment)}</Increment>
        <Max xsi:type="{xsd}">{escape(parameter.maximum)}</Max>
        <Min xsi:type="{xsd}">{escape(parameter.minimum)}</Min>
        <Name>{escape(parameter.name)}</Name>
        <ParameterTypeSerializable>{escape(full_type)}</ParameterTypeSerializable>
        <ValueSerializable>{escape(_value_serializable(parameter))}</ValueSerializable>
      </Parameter>"""


def _xsd_type(dotnet_type: str) -> str:
    """Map a .NET type name to the xsi:type used on <Min>/<Max>."""
    return {
        "System.Int32": "xsd:int",
        "System.Int64": "xsd:long",
        "System.Int16": "xsd:short",
        "System.Double": "xsd:double",
        "System.Single": "xsd:float",
        "System.Decimal": "xsd:decimal",
        "System.Boolean": "xsd:boolean",
        "System.String": "xsd:string",
    }.get(dotnet_type, "xsd:string")


def _value_serializable(parameter: StrategyParameter) -> str:
    """`<ValueSerializable>` text. NT writes capitalised True/False for bools
    here (unlike the lowercase form used inside the <Strategy> element)."""
    if parameter.type_name == "System.Boolean":
        return "True" if parameter.default.strip().lower() in {"true", "1", "yes"} else "False"
    return parameter.default


def _detect_strategy_name(source_text: str) -> str:
    match = re.search(r"\bclass\s+([A-Za-z_][A-Za-z0-9_]*)\s*:\s*Strategy\b", source_text)
    return match.group(1) if match else ""


def _state_setdefaults(source_text: str) -> dict[str, str]:
    match = re.search(
        r"if\s*\(\s*State\s*==\s*State\.SetDefaults\s*\)\s*\{(?P<body>.*?)\n\s*\}",
        source_text,
        re.DOTALL,
    )
    if not match:
        return {}
    defaults: dict[str, str] = {}
    for assignment in re.finditer(
        r"\b(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?P<value>[^;]+);",
        match.group("body"),
    ):
        defaults[assignment.group("name")] = _normalize_csharp_default(assignment.group("value"))
    return defaults


_CSHARP_PRIMITIVE_CAST_RE = re.compile(
    r"^\(\s*(float|double|decimal|int|long|short|byte|sbyte|uint|ulong|ushort)\s*\)\s*",
    re.IGNORECASE,
)
_CSHARP_NUMERIC_SUFFIX_RE = re.compile(r"[fFdDmMlLuU]+$")


def _normalize_csharp_default(raw: str) -> str:
    """Convert a C# right-hand-side default expression to a plain XML-safe
    string. Returns ``""`` when the value can't be normalized, so callers
    can fall back to a type default.

    Handles the patterns NinjaTrader source files commonly use that NT's
    Strategy Analyzer XML loader rejects when emitted verbatim:

    * Primitive casts: ``(float).3`` → ``0.3``, ``(int)5`` → ``5``
    * Leading-dot floats: ``.3`` → ``0.3``, ``-.5`` → ``-0.5``
    * Numeric suffixes: ``0.3f`` / ``100L`` / ``1.5m`` → digits only
    * Digit separators: ``1_000`` → ``1000``
    * Wrapping parens: ``(0.5)`` → ``0.5``
    * Trailing line comments: ``0.5 // note`` → ``0.5``
    * Quoted strings: returns the contents (drops the quotes)
    * Booleans: lowercased ``true`` / ``false``
    * Anything unparseable (``null``, ``int.MaxValue``, ``Brushes.Yellow``,
      ``Calculate.OnBarClose``, arithmetic expressions): returns ``""``
    """
    s = (raw or "").strip()
    if not s:
        return ""
    comment_idx = s.find("//")
    if comment_idx >= 0:
        s = s[:comment_idx].rstrip()
    if not s:
        return ""
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        return s[1:-1]
    lowered = s.lower()
    if lowered == "true":
        return "true"
    if lowered == "false":
        return "false"
    s = _CSHARP_PRIMITIVE_CAST_RE.sub("", s).strip()
    while s.startswith("(") and s.endswith(")"):
        inner = s[1:-1].strip()
        if not inner:
            break
        s = inner
    cleaned = s.replace("_", "")
    cleaned = _CSHARP_NUMERIC_SUFFIX_RE.sub("", cleaned)
    if cleaned.startswith("."):
        cleaned = "0" + cleaned
    elif cleaned.startswith(("-.", "+.")):
        cleaned = cleaned[0] + "0" + cleaned[1:]
    try:
        if any(ch in cleaned for ch in (".", "e", "E")):
            float(cleaned)
        else:
            int(cleaned)
        return cleaned
    except ValueError:
        return ""


def _range_values(attrs: str) -> tuple[str, str] | None:
    match = re.search(r"\[Range\s*\(\s*([^,\)]+)\s*,\s*([^\)]+)\)\s*\]", attrs)
    if not match:
        return None
    return match.group(1).strip(), match.group(2).strip()


def _bounded_sweep(default: str, type_name: str, range_values: tuple[str, str] | None) -> tuple[str, str]:
    # Pin every parameter to its default. The recipe stage planner is the
    # sole source of sweep ranges; auto-generated ±1 sweeps (or full bool
    # ranges) on 30+ parameters multiply into hundreds of millions of
    # combinations once NT receives the template.
    return default, default


def _default_for_type(type_name: str) -> str:
    lowered = type_name.lower()
    if lowered in {"bool", "boolean", "system.boolean"}:
        return "false"
    if lowered in {"double", "float", "decimal", "system.double", "system.single", "system.decimal"}:
        return "0.0"
    if lowered in {"int", "long", "short", "system.int32", "system.int64", "system.int16"}:
        return "0"
    return ""


def _increment_for_type(type_name: str, minimum: str, maximum: str) -> str:
    lowered = type_name.lower()
    if lowered in {"bool", "boolean", "system.boolean"}:
        return "1"
    if lowered in {"double", "float", "decimal", "system.double", "system.single", "system.decimal"}:
        return "1"
    return "1" if minimum != maximum else "1"


def _dotnet_type(type_name: str) -> str:
    lowered = type_name.lower()
    mapping = {
        "bool": "System.Boolean",
        "boolean": "System.Boolean",
        "int": "System.Int32",
        "long": "System.Int64",
        "short": "System.Int16",
        "double": "System.Double",
        "float": "System.Single",
        "decimal": "System.Decimal",
        "string": "System.String",
    }
    return mapping.get(lowered, type_name)


def _serialize(value: str, type_name: str) -> str:
    lowered = type_name.lower()
    text = str(value).strip()
    if lowered in {"bool", "boolean", "system.boolean"}:
        return "true" if text.lower() in {"true", "1", "yes"} else "false"
    return text


def _strategy_value(value: str) -> str:
    if value in {"True", "False"}:
        return value.lower()
    return value
