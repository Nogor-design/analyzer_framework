from __future__ import annotations

"""Spec → NinjaScript source synthesis for the strategy loop.

The repair loop and the smoke loop both need a deterministic way to render an
initial `.cs` from a `StrategySpec`. The full design (see
`docs/designs/autonomous_ninjatrader_strategy_loop.md`) calls for delegating
unknown families to the external Strategy Factory; this module
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
            "register one with authoring.register_family or use "
            "ta_foundation.nt_strategy_loop.strategy_factory_bridge"
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


def _orb_failure_reclaim_renderer(spec: StrategySpec) -> str:
    """Faithful NinjaScript realization of the `orb_failure_reclaim` family.

    Built to validate discovery candidate `c_1acc69ea578ff672_001` (NQ 1m) in
    NinjaTrader — the realization-path gap Runbook B step 2 surfaced (see
    `docs/runbooks/manual_pipeline_proof.md`).

    Fidelity caveat: the discovery engine groups bars in `America/Denver` local
    time. This strategy gates the session window by the *bar timestamp's*
    wall-clock minute-of-day, so the NinjaTrader data series must be loaded in
    the same timezone the discovery used. Body-midpoint entry is a limit order
    at `(Open + Close) / 2` of the reclaim bar, cancelled after
    `FillTimeoutBars` unfilled bars — mirroring the outcome simulator's
    `_fill_limit_order` window.
    """
    name = spec.strategy_name
    p = spec.parameters
    params = {
        "OrbMinutes": int(p.get("OrbMinutes", 5)),
        "SessionOpenHour": int(p.get("SessionOpenHour", 7)),
        "SessionOpenMinute": int(p.get("SessionOpenMinute", 30)),
        "SessionCloseHour": int(p.get("SessionCloseHour", 10)),
        "MinRangeTicks": int(p.get("MinRangeTicks", 8)),
        "MinSweepTicks": float(p.get("MinSweepTicks", 4.0)),
        "CloseBackTicks": float(p.get("CloseBackTicks", 0.0)),
        "MaxReclaimBars": int(p.get("MaxReclaimBars", 1)),
        "FillTimeoutBars": int(p.get("FillTimeoutBars", 5)),
        "MaxBarsInTrade": int(p.get("MaxBarsInTrade", 90)),
        "TradeDirection": int(p.get("TradeDirection", p.get("Direction", 0))),
        "TargetTicks": int(p.get("TargetTicks", 150)),
        "StopTicks": int(p.get("StopTicks", 20)),
    }
    return f"""using System;
using System.ComponentModel;
using System.ComponentModel.DataAnnotations;
using NinjaTrader.Cbi;
using NinjaTrader.Data;
using NinjaTrader.NinjaScript;

namespace NinjaTrader.NinjaScript.Strategies
{{
    // ORB failure-reclaim. Each session builds an opening range over the first
    // OrbMinutes after the session open; after a sweep beyond the range that
    // closes back inside within MaxReclaimBars, enter on a limit order at the
    // reclaim bar's body midpoint. One signal per side per day.
    public class {name} : Strategy
    {{
        private DateTime currentDay = DateTime.MinValue;
        private double orbHigh;
        private double orbLow;
        private int orbBarsSeen;
        private bool orbReady;
        private bool orbDone;
        private bool firedLong;
        private bool firedShort;
        private int longSweepBar;
        private int shortSweepBar;
        private int entrySubmitBar;
        private Order entryOrder;

        protected override void OnStateChange()
        {{
            if (State == State.SetDefaults)
            {{
                Name = "{name}";
                Description = "ORB failure-reclaim realization for discovery validation.";
                Calculate = Calculate.OnBarClose;
                // Resolve intrabar fills with tick data: a body-midpoint limit
                // can fill and then hit the stop inside the same 1-minute bar.
                // Standard (minute) resolution mis-fills that collision badly.
                OrderFillResolution = OrderFillResolution.High;
                OrderFillResolutionType = BarsPeriodType.Tick;
                OrderFillResolutionValue = 1;
                EntriesPerDirection = 1;
                EntryHandling = EntryHandling.AllEntries;
                IsExitOnSessionCloseStrategy = true;
                ExitOnSessionCloseSeconds = 30;
                BarsRequiredToTrade = 20;
                DefaultQuantity = 1;
                OrbMinutes = {params["OrbMinutes"]};
                SessionOpenHour = {params["SessionOpenHour"]};
                SessionOpenMinute = {params["SessionOpenMinute"]};
                SessionCloseHour = {params["SessionCloseHour"]};
                MinRangeTicks = {params["MinRangeTicks"]};
                MinSweepTicks = {params["MinSweepTicks"]};
                CloseBackTicks = {params["CloseBackTicks"]};
                MaxReclaimBars = {params["MaxReclaimBars"]};
                FillTimeoutBars = {params["FillTimeoutBars"]};
                MaxBarsInTrade = {params["MaxBarsInTrade"]};
                TradeDirection = {params["TradeDirection"]};
                TargetTicks = {params["TargetTicks"]};
                StopTicks = {params["StopTicks"]};
            }}
            else if (State == State.Configure)
            {{
                SetProfitTarget(CalculationMode.Ticks, TargetTicks);
                SetStopLoss(CalculationMode.Ticks, StopTicks);
            }}
        }}

        protected override void OnBarUpdate()
        {{
            if (CurrentBar < BarsRequiredToTrade)
                return;

            if (Time[0].Date != currentDay)
            {{
                currentDay = Time[0].Date;
                orbHigh = double.MinValue;
                orbLow = double.MaxValue;
                orbBarsSeen = 0;
                orbReady = false;
                orbDone = false;
                firedLong = false;
                firedShort = false;
                longSweepBar = -1;
                shortSweepBar = -1;
                entryOrder = null;
            }}

            int tod = Time[0].Hour * 60 + Time[0].Minute;
            int openTod = SessionOpenHour * 60 + SessionOpenMinute;

            // Build the opening range; never trade a bar still inside it.
            if (!orbDone)
            {{
                if (tod >= openTod && tod < openTod + OrbMinutes)
                {{
                    orbHigh = Math.Max(orbHigh, High[0]);
                    orbLow = Math.Min(orbLow, Low[0]);
                    orbBarsSeen++;
                }}
                else if (tod >= openTod + OrbMinutes && orbBarsSeen > 0)
                {{
                    orbDone = true;
                    orbReady = (orbHigh - orbLow) >= MinRangeTicks * TickSize;
                }}
                return;
            }}

            // Cancel a stale, still-working entry limit order.
            if (entryOrder != null && entryOrder.OrderState == OrderState.Working
                && CurrentBar - entrySubmitBar > FillTimeoutBars)
            {{
                CancelOrder(entryOrder);
                entryOrder = null;
            }}

            // Time-based exit.
            if (Position.MarketPosition != MarketPosition.Flat
                && BarsSinceEntryExecution() >= MaxBarsInTrade)
            {{
                if (Position.MarketPosition == MarketPosition.Long)
                    ExitLong("OrbTimeExit", "OrbLong");
                else
                    ExitShort("OrbTimeExit", "OrbShort");
                return;
            }}

            if (!orbReady)
                return;
            if (Time[0].Hour >= SessionCloseHour)
                return;
            if (Position.MarketPosition != MarketPosition.Flat)
                return;
            if (entryOrder != null && entryOrder.OrderState == OrderState.Working)
                return;

            double sweepDist = MinSweepTicks * TickSize;
            double closeBack = CloseBackTicks * TickSize;
            double midPrice = (Open[0] + Close[0]) / 2.0;

            // Short: price sweeps above orbHigh, then closes back inside.
            if (TradeDirection <= 0 && !firedShort)
            {{
                if (shortSweepBar >= 0 && CurrentBar - shortSweepBar > MaxReclaimBars)
                    shortSweepBar = -1;
                if (High[0] >= orbHigh + sweepDist && shortSweepBar < 0)
                    shortSweepBar = CurrentBar;
                if (shortSweepBar >= 0 && Close[0] <= orbHigh - closeBack)
                {{
                    EnterShortLimit(0, true, 1, midPrice, "OrbShort");
                    entrySubmitBar = CurrentBar;
                    firedShort = true;
                    return;
                }}
            }}

            // Long: price sweeps below orbLow, then closes back inside.
            if (TradeDirection >= 0 && !firedLong)
            {{
                if (longSweepBar >= 0 && CurrentBar - longSweepBar > MaxReclaimBars)
                    longSweepBar = -1;
                if (Low[0] <= orbLow - sweepDist && longSweepBar < 0)
                    longSweepBar = CurrentBar;
                if (longSweepBar >= 0 && Close[0] >= orbLow + closeBack)
                {{
                    EnterLongLimit(0, true, 1, midPrice, "OrbLong");
                    entrySubmitBar = CurrentBar;
                    firedLong = true;
                }}
            }}
        }}

        protected override void OnOrderUpdate(Order order, double limitPrice, double stopPrice,
            int quantity, int filled, double averageFillPrice, OrderState orderState,
            DateTime time, ErrorCode error, string comment)
        {{
            if (order.Name == "OrbLong" || order.Name == "OrbShort")
                entryOrder = order;
        }}

        [NinjaScriptProperty]
        [Display(Name = "OrbMinutes", GroupName = "Parameters", Order = 1)]
        public int OrbMinutes {{ get; set; }}

        [NinjaScriptProperty]
        [Display(Name = "SessionOpenHour", GroupName = "Parameters", Order = 2)]
        public int SessionOpenHour {{ get; set; }}

        [NinjaScriptProperty]
        [Display(Name = "SessionOpenMinute", GroupName = "Parameters", Order = 3)]
        public int SessionOpenMinute {{ get; set; }}

        [NinjaScriptProperty]
        [Display(Name = "SessionCloseHour", GroupName = "Parameters", Order = 4)]
        public int SessionCloseHour {{ get; set; }}

        [NinjaScriptProperty]
        [Display(Name = "MinRangeTicks", GroupName = "Parameters", Order = 5)]
        public int MinRangeTicks {{ get; set; }}

        [NinjaScriptProperty]
        [Display(Name = "MinSweepTicks", GroupName = "Parameters", Order = 6)]
        public double MinSweepTicks {{ get; set; }}

        [NinjaScriptProperty]
        [Display(Name = "CloseBackTicks", GroupName = "Parameters", Order = 7)]
        public double CloseBackTicks {{ get; set; }}

        [NinjaScriptProperty]
        [Display(Name = "MaxReclaimBars", GroupName = "Parameters", Order = 8)]
        public int MaxReclaimBars {{ get; set; }}

        [NinjaScriptProperty]
        [Display(Name = "FillTimeoutBars", GroupName = "Parameters", Order = 9)]
        public int FillTimeoutBars {{ get; set; }}

        [NinjaScriptProperty]
        [Display(Name = "MaxBarsInTrade", GroupName = "Parameters", Order = 10)]
        public int MaxBarsInTrade {{ get; set; }}

        [NinjaScriptProperty]
        [Display(Name = "TradeDirection", GroupName = "Parameters", Order = 11)]
        public int TradeDirection {{ get; set; }}

        [NinjaScriptProperty]
        [Range(20, 400)]
        [Display(Name = "TargetTicks", GroupName = "Parameters", Order = 12)]
        public int TargetTicks {{ get; set; }}

        [NinjaScriptProperty]
        [Range(4, 100)]
        [Display(Name = "StopTicks", GroupName = "Parameters", Order = 13)]
        public int StopTicks {{ get; set; }}
    }}
}}
"""


register_family("orb_failure_reclaim", _orb_failure_reclaim_renderer)


def _cash_open_first_bar_follow_through_renderer(spec: StrategySpec) -> str:
    """Render the fixed, theory-first cash-open continuation hypothesis.

    The signal is evaluated when the configured cash-open minute bar closes.
    A managed market entry submitted at that close fills on the next bar open,
    matching the Python structural-hypothesis reference. Stop and target
    distances scale with the signal bar's body.
    """

    name = spec.strategy_name
    p = spec.parameters
    params = {
        "CashOpenHour": int(p.get("CashOpenHour", 7)),
        "CashOpenMinute": int(p.get("CashOpenMinute", 30)),
        "MinBodyTicks": int(p.get("MinBodyTicks", 3)),
        "TargetBodyMultiple": float(p.get("TargetBodyMultiple", 2.0)),
        "StopBodyMultiple": float(p.get("StopBodyMultiple", 1.0)),
        "MaxBarsInTrade": int(p.get("MaxBarsInTrade", 60)),
    }
    return f"""using System;
using System.ComponentModel;
using System.ComponentModel.DataAnnotations;
using NinjaTrader.Cbi;
using NinjaTrader.Data;
using NinjaTrader.NinjaScript;

namespace NinjaTrader.NinjaScript.Strategies
{{
    // One theory-first signal per session: the direction of a meaningful
    // cash-open bar continues as overnight positioning is incorporated.
    public class {name} : Strategy
    {{
        private DateTime signalDate = DateTime.MinValue;

        protected override void OnStateChange()
        {{
            if (State == State.SetDefaults)
            {{
                Name = "{name}";
                Description = "Cash-open first-bar follow-through validation.";
                Calculate = Calculate.OnBarClose;
                OrderFillResolution = OrderFillResolution.High;
                OrderFillResolutionType = BarsPeriodType.Tick;
                OrderFillResolutionValue = 1;
                EntriesPerDirection = 1;
                EntryHandling = EntryHandling.AllEntries;
                IsExitOnSessionCloseStrategy = true;
                ExitOnSessionCloseSeconds = 30;
                BarsRequiredToTrade = 2;
                DefaultQuantity = 1;
                CashOpenHour = {params["CashOpenHour"]};
                CashOpenMinute = {params["CashOpenMinute"]};
                MinBodyTicks = {params["MinBodyTicks"]};
                TargetBodyMultiple = {params["TargetBodyMultiple"]};
                StopBodyMultiple = {params["StopBodyMultiple"]};
                MaxBarsInTrade = {params["MaxBarsInTrade"]};
            }}
        }}

        protected override void OnBarUpdate()
        {{
            if (CurrentBar < BarsRequiredToTrade)
                return;

            if (Position.MarketPosition != MarketPosition.Flat)
            {{
                // BarsSinceEntryExecution is zero on the entry bar. Signaling
                // after MaxBarsInTrade observed bars exits at the following
                // bar open, the closest managed-order analogue to the Python
                // reference's final-bar close timeout.
                if (BarsSinceEntryExecution() >= MaxBarsInTrade - 1)
                {{
                    if (Position.MarketPosition == MarketPosition.Long)
                        ExitLong("TimeExit", "FirstBarLong");
                    else
                        ExitShort("TimeExit", "FirstBarShort");
                }}
                return;
            }}

            // NinjaTrader labels time-based bars by their ending timestamp.
            // The 07:30-07:31 cash-open bar is therefore Time[0] == 07:31.
            int signalBarCloseMinute =
                (CashOpenHour * 60 + CashOpenMinute + 1) % (24 * 60);
            int currentBarCloseMinute = Time[0].Hour * 60 + Time[0].Minute;
            if (currentBarCloseMinute != signalBarCloseMinute)
                return;
            DateTime cashOpenDate = signalBarCloseMinute == 0
                ? Time[0].Date.AddDays(-1)
                : Time[0].Date;
            if (signalDate == cashOpenDate)
                return;
            signalDate = cashOpenDate;

            double body = Close[0] - Open[0];
            double bodyTicks = Math.Abs(body) / TickSize;
            if (bodyTicks < MinBodyTicks)
                return;

            int targetTicks = Math.Max(
                1, (int)Math.Round(bodyTicks * TargetBodyMultiple)
            );
            int stopTicks = Math.Max(
                1, (int)Math.Round(bodyTicks * StopBodyMultiple)
            );
            SetProfitTarget(CalculationMode.Ticks, targetTicks);
            SetStopLoss(CalculationMode.Ticks, stopTicks);

            // OnBarClose market orders fill at the next bar open in Strategy
            // Analyzer, which is the pre-registered entry convention.
            if (body > 0)
                EnterLong("FirstBarLong");
            else
                EnterShort("FirstBarShort");
        }}

        [NinjaScriptProperty]
        [Range(0, 23)]
        [Display(Name = "CashOpenHour", GroupName = "Parameters", Order = 1)]
        public int CashOpenHour {{ get; set; }}

        [NinjaScriptProperty]
        [Range(0, 59)]
        [Display(Name = "CashOpenMinute", GroupName = "Parameters", Order = 2)]
        public int CashOpenMinute {{ get; set; }}

        [NinjaScriptProperty]
        [Range(1, 100)]
        [Display(Name = "MinBodyTicks", GroupName = "Parameters", Order = 3)]
        public int MinBodyTicks {{ get; set; }}

        [NinjaScriptProperty]
        [Range(0.1, 10.0)]
        [Display(Name = "TargetBodyMultiple", GroupName = "Parameters", Order = 4)]
        public double TargetBodyMultiple {{ get; set; }}

        [NinjaScriptProperty]
        [Range(0.1, 10.0)]
        [Display(Name = "StopBodyMultiple", GroupName = "Parameters", Order = 5)]
        public double StopBodyMultiple {{ get; set; }}

        [NinjaScriptProperty]
        [Range(1, 600)]
        [Display(Name = "MaxBarsInTrade", GroupName = "Parameters", Order = 6)]
        public int MaxBarsInTrade {{ get; set; }}
    }}
}}
"""


register_family(
    "cash_open_first_bar_follow_through",
    _cash_open_first_bar_follow_through_renderer,
)


_OVERNIGHT_RANGE_FADE_DEFAULTS: dict[str, Any] = {
    "SessionOpenHour": 16,
    "SessionOpenMinute": 0,
    "RthOpenHour": 7,
    "RthOpenMinute": 30,
    "EntryWindowMinutes": 60,
    "FlatByHour": 14,
    "FlatByMinute": 55,
    "AtrPeriod": 14,
    "StopAtrMult": 2.0,
    "TargetAtrMult": 4.0,
    "TrailBars": 0,
    "TrailArmAtrMult": 2.0,
    "LockAtrMult": 0.0,
    "MaxHoldMinutes": 240,
    "MinOvernightBars": 600,
    "MinRangeTicks": 0,
    "BreakSide": 0,
    "Reverse": False,
    "Contracts": 1,
    "ExpectedTradingHoursName": "CME US Index Futures ETH",
    "ExpectedTimeZoneId": "Mountain Standard Time",
}

_OVERNIGHT_RANGE_FADE_TYPES: dict[str, type] = {
    key: type(value) for key, value in _OVERNIGHT_RANGE_FADE_DEFAULTS.items()
}


def _overnight_range_fade_params(raw: dict[str, Any]) -> dict[str, Any]:
    params: dict[str, Any] = {}
    for key, default in _OVERNIGHT_RANGE_FADE_DEFAULTS.items():
        kind = _OVERNIGHT_RANGE_FADE_TYPES[key]
        value = raw.get(key, default)
        params[key] = str(value) if kind is str else kind(value)
    return params


def _validate_overnight_range_fade(p: dict[str, Any]) -> None:
    """Refuse a spec that cannot describe the researched experiment.

    The Strategy Analyzer will happily run an inverted clock or an entry window
    that reaches past the flatten time; it just measures something else. Every
    rule here is also enforced at runtime, because an optimizer can pick a
    combination the renderer never saw.
    """
    problems: list[str] = []
    for key in ("SessionOpenHour", "RthOpenHour", "FlatByHour"):
        if not 0 <= p[key] <= 23:
            problems.append(f"{key}={p[key]} is not an hour")
    for key in ("SessionOpenMinute", "RthOpenMinute", "FlatByMinute"):
        if not 0 <= p[key] <= 59:
            problems.append(f"{key}={p[key]} is not a minute")
    session_open = p["SessionOpenHour"] * 60 + p["SessionOpenMinute"]
    rth_open = p["RthOpenHour"] * 60 + p["RthOpenMinute"]
    flat_by = p["FlatByHour"] * 60 + p["FlatByMinute"]
    if not rth_open < flat_by < session_open:
        problems.append(
            "the clock must run RthOpen < FlatBy < SessionOpen; the overnight "
            "range wraps midnight and the trade must be flat before the next "
            "session opens"
        )
    if p["EntryWindowMinutes"] < 1 or rth_open + p["EntryWindowMinutes"] >= flat_by:
        problems.append("EntryWindowMinutes must be positive and end before FlatBy")
    overnight_minutes = 1440 - session_open + rth_open
    if not 0 <= p["MinOvernightBars"] <= overnight_minutes:
        problems.append(
            f"MinOvernightBars={p['MinOvernightBars']} counts one-minute bars and "
            f"cannot exceed the {overnight_minutes}-minute overnight window"
        )
    if p["AtrPeriod"] < 2:
        problems.append("AtrPeriod must be at least 2")
    if p["StopAtrMult"] <= 0:
        problems.append("StopAtrMult must be positive")
    if p["TrailBars"] < 0:
        problems.append("TrailBars cannot be negative")
    if p["TrailBars"] == 0 and p["TargetAtrMult"] <= 0:
        problems.append("a fixed bracket needs a positive TargetAtrMult")
    if p["TrailArmAtrMult"] < 0:
        problems.append("TrailArmAtrMult cannot be negative")
    if p["LockAtrMult"] < 0:
        problems.append("LockAtrMult cannot be negative")
    if p["LockAtrMult"] > 0:
        # lca.exits "lock": stop S, moved to +lock once +trigger is reached.
        # The trigger is TrailArmAtrMult, shared with the structure trail; the
        # two are different policies and cannot both be on.
        if p["TrailBars"] > 0:
            problems.append("LockAtrMult and TrailBars are different exit policies; set one of them")
        if p["TrailArmAtrMult"] <= 0:
            problems.append("a lock needs a positive TrailArmAtrMult to arm it")
        elif p["LockAtrMult"] >= p["TrailArmAtrMult"]:
            problems.append("LockAtrMult must be below TrailArmAtrMult, or the locked stop sits beyond the excursion that armed it")
    if p["MaxHoldMinutes"] < 1:
        problems.append("MaxHoldMinutes must be at least 1")
    if p["MinRangeTicks"] < 0:
        problems.append("MinRangeTicks cannot be negative")
    if p["BreakSide"] not in (0, 1, 2):
        problems.append("BreakSide must be 0 (both), 1 (up breaks) or 2 (down breaks)")
    if p["Contracts"] < 1:
        problems.append("Contracts must be at least 1")
    for key in ("ExpectedTradingHoursName", "ExpectedTimeZoneId"):
        if any(ch in p[key] for ch in '"\\\r\n'):
            problems.append(f"{key} cannot contain quotes, backslashes or newlines")
    if problems:
        raise AuthoringError(
            "overnight_range_fade spec is internally inconsistent: " + "; ".join(problems)
        )


def _overnight_range_fade_renderer(spec: StrategySpec) -> str:
    """NinjaScript realization of the `overnight_range_fade` family.

    Fades the first regular-hours break of the Globex overnight range. Built
    from the study in `D:\\strategy-analysis\\large-candle`
    (`docs/OVERNIGHT_BREAK_RESULTS.md`), which measured +0.237 R per trade at a
    session-clustered t of 2.82 across NQ/ES/YM/RTY -- a candidate, not a
    validated edge, which is exactly why it needs a NinjaTrader run.

    This is the managed-order (Layer 2) harness of
    `D:\\nt-strategy-forge\\docs\\OVERNIGHT_FADE_CANDIDATE_AUDIT_2026-08-30.md`.
    Its event definition deliberately follows the order-free
    `OvernightFadeParityReference` rather than inventing its own:

    * Session identity and the overnight range come from an added one-minute
      series on the clock, not from ``Bars.IsFirstBarOfSession``. That removes
      the dependence on whichever trading-hours template happens to be loaded,
      and there is no warm-up ``return`` that can skip a session reset.
    * Each side's first break is consumed whether or not a position is open.
      An occupied or side-filtered break is written to the output log, never
      silently re-armed for a later bar to trade as though it were first.
    * The bracket is the source's continuous ``mult x ATR`` leg moved outward
      onto the tick lattice with ``ceil`` and a frozen 1e-10 tolerance, so an
      order is never tighter than the research threshold.
    * The ATR is the source's exclusive Wilder recursion seeded with the first
      true range, read as of the bar before the signal.
    * The path ends at ``MaxHoldMinutes`` one-minute rows or the FlatBy bar.
    * The structure trail counts one-minute bars and arms only after
      ``TrailArmAtrMult`` ATR of favourable excursion, never loosening the
      initial stop.
    * ``LockAtrMult`` > 0 is lca.exits "lock": once the trade has been
      ``TrailArmAtrMult`` ATR ahead the stop moves, once, to ``LockAtrMult``
      ATR of profit (rounded down to the tick lattice, never tighter than
      the research) and the target stays. It is the Odysseus mandate of
      the Large Candle roster and is exclusive with ``TrailBars``.
    """
    name = spec.strategy_name
    params = _overnight_range_fade_params(spec.parameters)
    _validate_overnight_range_fade(params)
    reverse = "true" if params["Reverse"] else "false"
    return f"""using System;
using System.ComponentModel;
using System.ComponentModel.DataAnnotations;
using System.Globalization;
using NinjaTrader.Cbi;
using NinjaTrader.Data;
using NinjaTrader.NinjaScript;

namespace NinjaTrader.NinjaScript.Strategies
{{
    // Overnight-range fade, managed-order research harness. Historical only.
    //
    // Two series, two jobs. The added 1-minute series owns the clock: session
    // identity, the 16:01 -> 07:30 overnight range, the holding horizon, the
    // flatten time and the structure trail. The primary series (2-minute for
    // the study) owns the decision: a bar whose high takes out the overnight
    // high inside the entry window is sold, a bar whose low takes out the low
    // is bought, and the bracket is sized from the ATR of the bar before it.
    public class {name} : Strategy
    {{
        // An ATR leg is a continuous price; orders live on the tick lattice.
        // Rounding outward never tightens the researched leg. The tolerance is
        // the one frozen in fixed_2r2r_lattice_sensitivity_v1.
        private const double LatticeTolerance = 1e-10;

        private bool configured;

        private DateTime currentSession = DateTime.MinValue;
        private double onHigh = double.MinValue;
        private double onLow = double.MaxValue;
        private int onBars;
        private bool rangeLatched;
        private bool onReady;
        private bool firedUp;
        private bool firedDown;

        private bool hasAtr;
        private double atrWilder;

        private double pendingAtrTicks;
        private int pendingStopTicks;
        private int entryBar = -1;
        private int tradeDirection;
        private double tradeAtrTicks;
        private double fillPrice;
        private double bestTicks;
        private bool trailArmed;
        private double trailPrice;
        private bool exitSubmitted;

        private int signalsSubmitted;
        private int signalsSuppressed;
        private int signalsFiltered;

        protected override void OnStateChange()
        {{
            if (State == State.SetDefaults)
            {{
                Name = "{name}";
                Description = "Fade the first regular-hours break of the overnight range.";
                Calculate = Calculate.OnBarClose;
                // The source resolves every bracket on one-minute bars and
                // reaches for ticks only where one minute touched both legs.
                // NinjaTrader allows High fill resolution only for a
                // single-series strategy, and this one adds a one-minute
                // series; its prescription for multi-series is to submit the
                // orders on the finer series. So: Standard resolution, and
                // every order (entries below, exits in ManagePosition) goes
                // to BarsInProgress 1, where fills simulate on one-minute bars.
                OrderFillResolution = OrderFillResolution.Standard;
                EntriesPerDirection = 1;
                EntryHandling = EntryHandling.AllEntries;
                IsExitOnSessionCloseStrategy = true;
                ExitOnSessionCloseSeconds = 30;
                // No bar-count guard: it would return before session state is
                // maintained. Readiness is the latched range and a seeded ATR.
                BarsRequiredToTrade = 0;
                DefaultQuantity = 1;
                IsInstantiatedOnEachOptimizationIteration = true;
                SessionOpenHour = {params["SessionOpenHour"]};
                SessionOpenMinute = {params["SessionOpenMinute"]};
                RthOpenHour = {params["RthOpenHour"]};
                RthOpenMinute = {params["RthOpenMinute"]};
                EntryWindowMinutes = {params["EntryWindowMinutes"]};
                FlatByHour = {params["FlatByHour"]};
                FlatByMinute = {params["FlatByMinute"]};
                AtrPeriod = {params["AtrPeriod"]};
                StopAtrMult = {params["StopAtrMult"]};
                TargetAtrMult = {params["TargetAtrMult"]};
                TrailBars = {params["TrailBars"]};
                TrailArmAtrMult = {params["TrailArmAtrMult"]};
                LockAtrMult = {params["LockAtrMult"]};
                MaxHoldMinutes = {params["MaxHoldMinutes"]};
                MinOvernightBars = {params["MinOvernightBars"]};
                MinRangeTicks = {params["MinRangeTicks"]};
                BreakSide = {params["BreakSide"]};
                Reverse = {reverse};
                Contracts = {params["Contracts"]};
                ExpectedTradingHoursName = "{params["ExpectedTradingHoursName"]}";
                ExpectedTimeZoneId = "{params["ExpectedTimeZoneId"]}";
            }}
            else if (State == State.Configure)
            {{
                AddDataSeries(BarsPeriodType.Minute, 1);
            }}
            else if (State == State.DataLoaded)
            {{
                ValidateConfiguration();
            }}
            else if (State == State.Realtime)
            {{
                throw new InvalidOperationException(
                    "{name} is a historical research harness and refuses real-time execution.");
            }}
            else if (State == State.Terminated && configured)
            {{
                Print(string.Format(CultureInfo.InvariantCulture,
                    "OVERNIGHT_FADE_DONE|submitted={{0}}|suppressed_occupied={{1}}|side_filtered={{2}}",
                    signalsSubmitted, signalsSuppressed, signalsFiltered));
            }}
        }}

        protected override void OnBarUpdate()
        {{
            if (BarsInProgress == 1)
            {{
                if (CurrentBars[1] >= 0)
                    OnMinuteBar();
                return;
            }}
            if (BarsInProgress == 0 && CurrentBars[0] >= 0)
                OnDecisionBar();
        }}

        // ---- clock: the 1-minute series ------------------------------------

        private void OnMinuteBar()
        {{
            DateTime stamp = Times[1][0];
            DateTime session = SessionDateOf(stamp);
            if (session != currentSession)
                ResetSession(session);

            int minute = stamp.Hour * 60 + stamp.Minute;
            if (!rangeLatched)
            {{
                if (IsOvernightStamp(minute))
                {{
                    onHigh = Math.Max(onHigh, Highs[1][0]);
                    onLow = Math.Min(onLow, Lows[1][0]);
                    onBars++;
                }}
                else
                {{
                    rangeLatched = true;
                    onReady = onBars >= MinOvernightBars
                        && onHigh > onLow
                        && (onHigh - onLow) >= MinRangeTicks * TickSize;
                }}
            }}

            ManagePosition(minute);
        }}

        private void ManagePosition(int minute)
        {{
            if (Position.MarketPosition == MarketPosition.Flat)
            {{
                entryBar = -1;
                exitSubmitted = false;
                return;
            }}
            if (entryBar < 0)
            {{
                // The first 1-minute bar the position exists on is the fill
                // bar: row 0 of the source's forward path.
                entryBar = CurrentBars[1];
                fillPrice = Position.AveragePrice;
                tradeDirection = Position.MarketPosition == MarketPosition.Long ? 1 : -1;
                tradeAtrTicks = pendingAtrTicks;
                bestTicks = 0.0;
                trailArmed = TrailArmAtrMult <= 0.0;
                trailPrice = fillPrice - tradeDirection * pendingStopTicks * TickSize;
            }}
            if (exitSubmitted)
                return;

            // The source path ends at its MaxHoldMinutes-th row or the FlatBy
            // bar and books that row's close. A managed exit submitted on the
            // close fills at the next open: a one-bar lattice residual, not a
            // different estimand.
            int rowsHeld = CurrentBars[1] - entryBar + 1;
            bool horizon = rowsHeld >= MaxHoldMinutes;
            bool flatTime = MinutesSinceSessionOpen(minute)
                >= MinutesSinceSessionOpen(FlatByHour * 60 + FlatByMinute);
            if (horizon || flatTime)
            {{
                string reason = horizon ? "OnFadeHorizon" : "OnFadeFlatBy";
                if (tradeDirection > 0)
                    ExitLong(1, Position.Quantity, reason, "OnFadeLong");
                else
                    ExitShort(1, Position.Quantity, reason, "OnFadeShort");
                exitSubmitted = true;
                return;
            }}

            if (TrailBars > 0)
                AdvanceStructureTrail(rowsHeld);
            else if (LockAtrMult > 0.0)
                AdvanceLock();
        }}

        // Ratchet the stop to the extreme of the last TrailBars 1-minute bars
        // once the trade has been TrailArmAtrMult ATR ahead. Mirrors
        // lca.exits.simulate: the bar's own stop/target test comes first (the
        // fill engine), then the excursion, then the stop for the next bar.
        private void AdvanceStructureTrail(int rowsHeld)
        {{
            double favourable = tradeDirection > 0
                ? (Highs[1][0] - fillPrice) / TickSize
                : (fillPrice - Lows[1][0]) / TickSize;
            bestTicks = Math.Max(bestTicks, favourable);
            if (!trailArmed && bestTicks >= TrailArmAtrMult * tradeAtrTicks)
                trailArmed = true;
            if (!trailArmed)
                return;

            int look = Math.Min(TrailBars, rowsHeld);
            if (tradeDirection > 0)
            {{
                double level = Lows[1][0];
                for (int i = 1; i < look; i++)
                    level = Math.Min(level, Lows[1][i]);
                if (level > trailPrice)
                {{
                    trailPrice = level;
                    SetStopLoss("", CalculationMode.Price, trailPrice, false);
                }}
            }}
            else
            {{
                double level = Highs[1][0];
                for (int i = 1; i < look; i++)
                    level = Math.Max(level, Highs[1][i]);
                if (level < trailPrice)
                {{
                    trailPrice = level;
                    SetStopLoss("", CalculationMode.Price, trailPrice, false);
                }}
            }}
        }}

        // lca.exits "lock": once the trade has been TrailArmAtrMult ATR ahead,
        // move the stop to LockAtrMult ATR of PROFIT, once, and leave the
        // target where it is. The level is rounded DOWN to the tick lattice so
        // the NinjaTrader stop is never tighter than the research stop; the
        // bar's own stop/target test ran first in the fill engine, as in
        // AdvanceStructureTrail.
        private void AdvanceLock()
        {{
            double favourable = tradeDirection > 0
                ? (Highs[1][0] - fillPrice) / TickSize
                : (fillPrice - Lows[1][0]) / TickSize;
            bestTicks = Math.Max(bestTicks, favourable);
            if (!trailArmed && bestTicks >= TrailArmAtrMult * tradeAtrTicks)
                trailArmed = true;
            if (!trailArmed)
                return;

            int lockTicks = (int)Math.Floor(LockAtrMult * tradeAtrTicks + LatticeTolerance);
            double lockPrice = fillPrice + tradeDirection * lockTicks * TickSize;
            bool tighter = tradeDirection > 0 ? lockPrice > trailPrice : lockPrice < trailPrice;
            if (tighter)
            {{
                trailPrice = lockPrice;
                SetStopLoss("", CalculationMode.Price, trailPrice, false);
                Print(string.Format(CultureInfo.InvariantCulture,
                    "OVERNIGHT_FADE_LOCK|time={{0:yyyy-MM-dd HH:mm}}|direction={{1}}|fill={{2}}|lock_price={{3}}|lock_ticks={{4}}|best_ticks={{5:R}}",
                    Times[1][0], tradeDirection, fillPrice, lockPrice, lockTicks, bestTicks));
            }}
        }}

        // ---- decision: the primary series ----------------------------------

        private void OnDecisionBar()
        {{
            double priorAtr = hasAtr ? atrWilder : double.NaN;
            double trueRange = PrimaryTrueRange();
            EvaluateBreak(Times[0][0], priorAtr);
            UpdateAtr(trueRange);
        }}

        private void EvaluateBreak(DateTime decision, double priorAtr)
        {{
            if (!onReady || currentSession == DateTime.MinValue)
                return;
            int since = MinutesSinceSessionOpen(decision.Hour * 60 + decision.Minute);
            int rthOffset = MinutesSinceSessionOpen(RthOpenHour * 60 + RthOpenMinute);
            if (since <= rthOffset || since > rthOffset + EntryWindowMinutes)
                return;

            bool upFresh = Highs[0][0] > onHigh && !firedUp;
            bool downFresh = Lows[0][0] < onLow && !firedDown;
            if (!upFresh && !downFresh)
                return;

            // A bar that takes out both sides says nothing about which side
            // won; consume both levels rather than guessing.
            if (upFresh && downFresh)
            {{
                firedUp = true;
                firedDown = true;
                AuditSignal(decision, "both", "both_sides_consumed", double.NaN, 0, 0);
                return;
            }}

            // The first break of a side is consumed HERE, before any position
            // or filter test, so a later break can never trade as a first one.
            if (upFresh)
                firedUp = true;
            else
                firedDown = true;
            string side = upFresh ? "up" : "down";

            if (double.IsNaN(priorAtr) || priorAtr <= 0.0)
            {{
                AuditSignal(decision, side, "atr_unavailable", double.NaN, 0, 0);
                return;
            }}
            double atrTicks = priorAtr / TickSize;
            int stopTicks = LatticeTicks(StopAtrMult * atrTicks);
            int targetTicks = LatticeTicks(TargetAtrMult * atrTicks);

            if ((BreakSide == 1 && !upFresh) || (BreakSide == 2 && upFresh))
            {{
                signalsFiltered++;
                AuditSignal(decision, side, "side_filtered", atrTicks, stopTicks, targetTicks);
                return;
            }}
            if (Position.MarketPosition != MarketPosition.Flat)
            {{
                signalsSuppressed++;
                AuditSignal(decision, side, "suppressed_occupied", atrTicks, stopTicks, targetTicks);
                return;
            }}

            SetStopLoss("", CalculationMode.Ticks, stopTicks, false);
            if (TrailBars <= 0)
                SetProfitTarget("", CalculationMode.Ticks, targetTicks);
            pendingAtrTicks = atrTicks;
            pendingStopTicks = stopTicks;

            // Fade: a break of the overnight high is sold. Reverse trades the
            // continuation instead, which is the control arm of the study.
            bool goShort = upFresh ? !Reverse : Reverse;
            // Submitted on the one-minute series (index 1): the fill is the
            // next one-minute open, which is also the next two-minute open,
            // and the managed stop and target attached to it are then
            // simulated against one-minute bars rather than the primary.
            if (goShort)
                EnterShort(1, Contracts, "OnFadeShort");
            else
                EnterLong(1, Contracts, "OnFadeLong");
            signalsSubmitted++;
            AuditSignal(decision, side, "submitted", atrTicks, stopTicks, targetTicks);
        }}

        // ---- source arithmetic ---------------------------------------------

        private double PrimaryTrueRange()
        {{
            if (!hasAtr)
                return Highs[0][0] - Lows[0][0];
            double previousClose = Closes[0][1];
            return Math.Max(
                Highs[0][0] - Lows[0][0],
                Math.Max(Math.Abs(Highs[0][0] - previousClose),
                    Math.Abs(Lows[0][0] - previousClose)));
        }}

        private void UpdateAtr(double trueRange)
        {{
            if (!hasAtr)
            {{
                atrWilder = trueRange;
                hasAtr = true;
                return;
            }}
            atrWilder = ((AtrPeriod - 1.0) * atrWilder + trueRange) / AtrPeriod;
        }}

        private static int LatticeTicks(double exactTicks)
        {{
            return Math.Max(1, (int)Math.Ceiling(exactTicks - LatticeTolerance));
        }}

        // Bars carry close stamps: the 1-minute bar stamped 16:01 covers 16:00
        // and opens the next session, so the test is on the coverage START.
        private DateTime SessionDateOf(DateTime closeStamp)
        {{
            DateTime coverageStart = closeStamp.AddMinutes(-1);
            int startMinute = coverageStart.Hour * 60 + coverageStart.Minute;
            return startMinute >= SessionOpenHour * 60 + SessionOpenMinute
                ? coverageStart.Date.AddDays(1)
                : coverageStart.Date;
        }}

        // The overnight window is two clock pieces either side of midnight.
        private bool IsOvernightStamp(int minuteOfDay)
        {{
            return minuteOfDay > SessionOpenHour * 60 + SessionOpenMinute
                || minuteOfDay <= RthOpenHour * 60 + RthOpenMinute;
        }}

        // Minutes elapsed since the session open, wrapping midnight.
        private int MinutesSinceSessionOpen(int minuteOfDay)
        {{
            int delta = minuteOfDay - (SessionOpenHour * 60 + SessionOpenMinute);
            if (delta < 0)
                delta += 1440;
            return delta;
        }}

        private void ResetSession(DateTime session)
        {{
            currentSession = session;
            onHigh = double.MinValue;
            onLow = double.MaxValue;
            onBars = 0;
            rangeLatched = false;
            onReady = false;
            firedUp = false;
            firedDown = false;
        }}

        private void AuditSignal(DateTime decision, string side, string status,
            double atrTicks, int stopTicks, int targetTicks)
        {{
            Print(string.Format(CultureInfo.InvariantCulture,
                "OVERNIGHT_FADE_SIGNAL|instrument={{0}}|session={{1:yyyy-MM-dd}}|decision={{2:yyyy-MM-ddTHH:mm}}|side={{3}}|status={{4}}|atr_ticks={{5:R}}|stop_ticks={{6}}|target_ticks={{7}}",
                Instrument.FullName, currentSession, decision, side, status,
                atrTicks, stopTicks, targetTicks));
        }}

        private void ValidateConfiguration()
        {{
            if (BarsPeriod.BarsPeriodType != BarsPeriodType.Minute)
                throw new InvalidOperationException(
                    "{name} requires a minute-based primary series; the study decides on 2-minute bars.");
            int sessionOpen = SessionOpenHour * 60 + SessionOpenMinute;
            int rthOpen = RthOpenHour * 60 + RthOpenMinute;
            int flatBy = FlatByHour * 60 + FlatByMinute;
            if (!(rthOpen < flatBy && flatBy < sessionOpen))
                throw new InvalidOperationException(
                    "The clock must run RthOpen < FlatBy < SessionOpen.");
            if (rthOpen + EntryWindowMinutes >= flatBy)
                throw new InvalidOperationException(
                    "EntryWindowMinutes must end before FlatBy.");
            if (MinOvernightBars > 1440 - sessionOpen + rthOpen)
                throw new InvalidOperationException(
                    "MinOvernightBars exceeds the overnight window.");
            if (TrailBars <= 0 && TargetAtrMult <= 0.0)
                throw new InvalidOperationException(
                    "A fixed bracket needs a positive TargetAtrMult.");
            if (LockAtrMult > 0.0 && TrailBars > 0)
                throw new InvalidOperationException(
                    "LockAtrMult and TrailBars are different exit policies; set one of them.");
            if (LockAtrMult > 0.0 && (TrailArmAtrMult <= 0.0 || LockAtrMult >= TrailArmAtrMult))
                throw new InvalidOperationException(
                    "LockAtrMult must be positive and below TrailArmAtrMult, which arms it.");
            // Every clock parameter above is read in the application time
            // zone, and session boundaries depend on the trading-hours
            // template. An empty expectation disables its check.
            if (!string.IsNullOrWhiteSpace(ExpectedTimeZoneId)
                && !string.Equals(Core.Globals.GeneralOptions.TimeZoneInfo.Id,
                    ExpectedTimeZoneId.Trim(), StringComparison.OrdinalIgnoreCase))
            {{
                throw new InvalidOperationException(string.Format(CultureInfo.InvariantCulture,
                    "Application time zone mismatch: expected={{0}}, actual={{1}}",
                    ExpectedTimeZoneId, Core.Globals.GeneralOptions.TimeZoneInfo.Id));
            }}
            if (!string.IsNullOrWhiteSpace(ExpectedTradingHoursName)
                && (Bars.TradingHours == null
                    || !string.Equals(Bars.TradingHours.Name, ExpectedTradingHoursName.Trim(),
                        StringComparison.OrdinalIgnoreCase)))
            {{
                throw new InvalidOperationException(string.Format(CultureInfo.InvariantCulture,
                    "Trading-hours template mismatch: expected={{0}}, actual={{1}}",
                    ExpectedTradingHoursName,
                    Bars.TradingHours == null ? "<null>" : Bars.TradingHours.Name));
            }}
            configured = true;
            Print(string.Format(CultureInfo.InvariantCulture,
                "OVERNIGHT_FADE_BOUND|instrument={{0}}|primary={{1}}|trading_hours={{2}}|time_zone={{3}}|break_side={{4}}|stop_atr={{5:R}}|target_atr={{6:R}}|trail_bars={{7}}|trail_arm_atr={{8:R}}|lock_atr={{9:R}}",
                Instrument.FullName, BarsPeriod,
                Bars.TradingHours == null ? "<null>" : Bars.TradingHours.Name,
                Core.Globals.GeneralOptions.TimeZoneInfo.Id,
                BreakSide, StopAtrMult, TargetAtrMult, TrailBars, TrailArmAtrMult, LockAtrMult));
        }}

        [NinjaScriptProperty]
        [Range(0, 23)]
        [Display(Name = "SessionOpenHour", GroupName = "Session", Order = 1)]
        public int SessionOpenHour {{ get; set; }}

        [NinjaScriptProperty]
        [Range(0, 59)]
        [Display(Name = "SessionOpenMinute", GroupName = "Session", Order = 2)]
        public int SessionOpenMinute {{ get; set; }}

        [NinjaScriptProperty]
        [Range(0, 23)]
        [Display(Name = "RthOpenHour", GroupName = "Session", Order = 3)]
        public int RthOpenHour {{ get; set; }}

        [NinjaScriptProperty]
        [Range(0, 59)]
        [Display(Name = "RthOpenMinute", GroupName = "Session", Order = 4)]
        public int RthOpenMinute {{ get; set; }}

        [NinjaScriptProperty]
        [Range(1, 390)]
        [Display(Name = "EntryWindowMinutes", GroupName = "Signal", Order = 5)]
        public int EntryWindowMinutes {{ get; set; }}

        [NinjaScriptProperty]
        [Range(0, 23)]
        [Display(Name = "FlatByHour", GroupName = "Session", Order = 6)]
        public int FlatByHour {{ get; set; }}

        [NinjaScriptProperty]
        [Range(0, 59)]
        [Display(Name = "FlatByMinute", GroupName = "Session", Order = 7)]
        public int FlatByMinute {{ get; set; }}

        [NinjaScriptProperty]
        [Range(2, 50)]
        [Display(Name = "AtrPeriod", GroupName = "Bracket", Order = 8)]
        public int AtrPeriod {{ get; set; }}

        [NinjaScriptProperty]
        [Range(0.5, 5.0)]
        [Display(Name = "StopAtrMult", GroupName = "Bracket", Order = 9)]
        public double StopAtrMult {{ get; set; }}

        // 0 is legal when TrailBars > 0 (the trail replaces the target); the
        // renderer and ValidateConfiguration refuse 0 with no trail. NinjaTrader
        // checks [Range] when a template loads, so the attribute must admit it.
        [NinjaScriptProperty]
        [Range(0.0, 8.0)]
        [Display(Name = "TargetAtrMult", GroupName = "Bracket", Order = 10)]
        public double TargetAtrMult {{ get; set; }}

        [NinjaScriptProperty]
        [Range(0, 30)]
        [Display(Name = "TrailBars", GroupName = "Bracket", Order = 11)]
        public int TrailBars {{ get; set; }}

        [NinjaScriptProperty]
        [Range(0.0, 10.0)]
        [Display(Name = "TrailArmAtrMult", GroupName = "Bracket", Order = 12)]
        public double TrailArmAtrMult {{ get; set; }}

        [NinjaScriptProperty]
        [Range(0.0, 10.0)]
        [Display(Name = "LockAtrMult", GroupName = "Bracket", Order = 13)]
        public double LockAtrMult {{ get; set; }}

        [NinjaScriptProperty]
        [Range(1, 1440)]
        [Display(Name = "MaxHoldMinutes", GroupName = "Bracket", Order = 14)]
        public int MaxHoldMinutes {{ get; set; }}

        [NinjaScriptProperty]
        [Range(0, 1440)]
        [Display(Name = "MinOvernightBars", GroupName = "Signal", Order = 14)]
        public int MinOvernightBars {{ get; set; }}

        [NinjaScriptProperty]
        [Range(0, 400)]
        [Display(Name = "MinRangeTicks", GroupName = "Signal", Order = 15)]
        public int MinRangeTicks {{ get; set; }}

        [NinjaScriptProperty]
        [Range(0, 2)]
        [Display(Name = "BreakSide", Description = "0 both, 1 up breaks only, 2 down breaks only", GroupName = "Signal", Order = 16)]
        public int BreakSide {{ get; set; }}

        [NinjaScriptProperty]
        [Display(Name = "Reverse", GroupName = "Signal", Order = 17)]
        public bool Reverse {{ get; set; }}

        [NinjaScriptProperty]
        [Range(1, 10)]
        [Display(Name = "Contracts", GroupName = "Bracket", Order = 18)]
        public int Contracts {{ get; set; }}

        [NinjaScriptProperty]
        [Display(Name = "ExpectedTradingHoursName", GroupName = "Runtime", Order = 19)]
        public string ExpectedTradingHoursName {{ get; set; }}

        [NinjaScriptProperty]
        [Display(Name = "ExpectedTimeZoneId", GroupName = "Runtime", Order = 20)]
        public string ExpectedTimeZoneId {{ get; set; }}
    }}
}}
"""


register_family("overnight_range_fade", _overnight_range_fade_renderer)
