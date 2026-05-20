from __future__ import annotations

"""Spec → NinjaScript source synthesis for the strategy loop.

The repair loop and the smoke loop both need a deterministic way to render an
initial `.cs` from a `StrategySpec`. The full design (see
`docs/designs/autonomous_ninjatrader_strategy_loop.md`) calls for delegating
unknown families to NinjatraderDocScrapper's strategy factory; this module
owns the families that live in-tree and raises a clear error for the rest so
the orchestrator can hand off to that factory deliberately.
"""

from dataclasses import dataclass, field
from typing import Any, Callable


class AuthoringError(RuntimeError):
    pass


@dataclass(frozen=True)
class StrategySpec:
    strategy_name: str
    family: str
    intent: str
    parameters: dict[str, Any] = field(default_factory=dict)
    risk_note: str = ""

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "StrategySpec":
        return cls(
            strategy_name=str(payload["strategy_name"]),
            family=str(payload["family"]),
            intent=str(payload.get("intent") or ""),
            parameters=dict(payload.get("parameters") or {}),
            risk_note=str(payload.get("risk_note") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "strategy_name": self.strategy_name,
            "family": self.family,
            "intent": self.intent,
            "parameters": dict(self.parameters),
            "risk_note": self.risk_note,
        }


SourceRenderer = Callable[[StrategySpec], str]


_RENDERERS: dict[str, SourceRenderer] = {}


def register_family(family: str, renderer: SourceRenderer) -> None:
    _RENDERERS[family] = renderer


def render_source(spec: StrategySpec) -> str:
    renderer = _RENDERERS.get(spec.family)
    if renderer is None:
        raise AuthoringError(
            f"no in-tree renderer for family {spec.family!r}; "
            f"register one with authoring.register_family or hand off to NinjatraderDocScrapper"
        )
    return renderer(spec)


def render_source_request(spec: StrategySpec) -> str:
    intent = spec.intent or "Generate a managed-order NinjaTrader 8 strategy for the autonomous loop."
    return (
        f"# Source Request: {spec.strategy_name}\n\n"
        f"Family: `{spec.family}`\n\n"
        f"{intent}\n"
    )


def _sma_cross_renderer(spec: StrategySpec) -> str:
    name = spec.strategy_name
    params = {
        "FastPeriod": int(spec.parameters.get("FastPeriod", 9)),
        "SlowPeriod": int(spec.parameters.get("SlowPeriod", 21)),
        "ProfitTargetTicks": int(spec.parameters.get("ProfitTargetTicks", 24)),
        "StopLossTicks": int(spec.parameters.get("StopLossTicks", 16)),
        "Reverse": bool(spec.parameters.get("Reverse", False)),
    }
    reverse_literal = "true" if params["Reverse"] else "false"
    return f"""using System;
using System.ComponentModel;
using System.ComponentModel.DataAnnotations;
using NinjaTrader.Cbi;
using NinjaTrader.Data;
using NinjaTrader.NinjaScript;
using NinjaTrader.NinjaScript.Indicators;

namespace NinjaTrader.NinjaScript.Strategies
{{
    public class {name} : Strategy
    {{
        private SMA fast;
        private SMA slow;

        protected override void OnStateChange()
        {{
            if (State == State.SetDefaults)
            {{
                Name = "{name}";
                Calculate = Calculate.OnBarClose;
                EntriesPerDirection = 1;
                EntryHandling = EntryHandling.AllEntries;
                IsExitOnSessionCloseStrategy = true;
                ExitOnSessionCloseSeconds = 30;
                FastPeriod = {params["FastPeriod"]};
                SlowPeriod = {params["SlowPeriod"]};
                ProfitTargetTicks = {params["ProfitTargetTicks"]};
                StopLossTicks = {params["StopLossTicks"]};
                Reverse = {reverse_literal};
            }}
            else if (State == State.Configure)
            {{
                SetProfitTarget(CalculationMode.Ticks, ProfitTargetTicks);
                SetStopLoss(CalculationMode.Ticks, StopLossTicks);
            }}
            else if (State == State.DataLoaded)
            {{
                fast = SMA(FastPeriod);
                slow = SMA(SlowPeriod);
            }}
        }}

        protected override void OnBarUpdate()
        {{
            if (CurrentBar < Math.Max(FastPeriod, SlowPeriod) + 1)
                return;

            bool crossUp = CrossAbove(fast, slow, 1);
            bool crossDown = CrossBelow(fast, slow, 1);
            if (Reverse)
            {{
                bool originalUp = crossUp;
                crossUp = crossDown;
                crossDown = originalUp;
            }}

            if (Position.MarketPosition == MarketPosition.Flat && crossUp)
                EnterLong("SmokeLong");
            else if (Position.MarketPosition == MarketPosition.Flat && crossDown)
                EnterShort("SmokeShort");
        }}

        [NinjaScriptProperty]
        [Range(2, 50)]
        [Display(Name = "FastPeriod", GroupName = "Parameters", Order = 1)]
        public int FastPeriod {{ get; set; }}

        [NinjaScriptProperty]
        [Range(3, 100)]
        [Display(Name = "SlowPeriod", GroupName = "Parameters", Order = 2)]
        public int SlowPeriod {{ get; set; }}

        [NinjaScriptProperty]
        [Range(4, 200)]
        [Display(Name = "ProfitTargetTicks", GroupName = "Parameters", Order = 3)]
        public int ProfitTargetTicks {{ get; set; }}

        [NinjaScriptProperty]
        [Range(4, 100)]
        [Display(Name = "StopLossTicks", GroupName = "Parameters", Order = 4)]
        public int StopLossTicks {{ get; set; }}

        [NinjaScriptProperty]
        [Display(Name = "Reverse", GroupName = "Parameters", Order = 5)]
        public bool Reverse {{ get; set; }}
    }}
}}
"""


register_family("sma_cross_smoke", _sma_cross_renderer)
register_family("sma_cross", _sma_cross_renderer)
