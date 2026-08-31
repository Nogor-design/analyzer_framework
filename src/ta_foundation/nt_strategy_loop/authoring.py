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


def _overnight_range_fade_renderer(spec: StrategySpec) -> str:
    """NinjaScript realization of the `overnight_range_fade` family.

    Fades the first regular-hours break of the Globex overnight range. Built
    from the study in `D:\\strategy-analysis\\large-candle`
    (`docs/OVERNIGHT_BREAK_RESULTS.md`), which measured +0.237 R per trade at a
    session-clustered t of 2.82 across NQ/ES/YM/RTY -- a candidate, not a
    validated edge, which is exactly why it needs a NinjaTrader run.

    Two details differ from `orb_failure_reclaim` and both matter:

    * The range is the OVERNIGHT one, 16:01 to 07:30 Denver, so it wraps
      midnight. A calendar-day reset would split it in half; the session is
      reset on ``Bars.IsFirstBarOfSession`` and time is indexed as minutes
      since the session open, which is wrap-safe and timezone-parameterised.
    * The bracket is sized per trade from the ATR of the bar BEFORE the signal,
      so it cannot use a fixed ``SetStopLoss`` in ``State.Configure``. The study
      found the edge is flat below one ATR of stop and plateaus from two
      upward, so a fixed tick stop would not be a faithful realization.
    """
    name = spec.strategy_name
    p = spec.parameters
    params = {
        "SessionOpenHour": int(p.get("SessionOpenHour", 16)),
        "SessionOpenMinute": int(p.get("SessionOpenMinute", 0)),
        "RthOpenHour": int(p.get("RthOpenHour", 7)),
        "RthOpenMinute": int(p.get("RthOpenMinute", 30)),
        "EntryWindowMinutes": int(p.get("EntryWindowMinutes", 60)),
        "FlatByHour": int(p.get("FlatByHour", 14)),
        "FlatByMinute": int(p.get("FlatByMinute", 55)),
        "AtrPeriod": int(p.get("AtrPeriod", 14)),
        "StopAtrMult": float(p.get("StopAtrMult", 2.0)),
        "TargetAtrMult": float(p.get("TargetAtrMult", 4.0)),
        "TrailBars": int(p.get("TrailBars", 0)),
        "MinOvernightBars": int(p.get("MinOvernightBars", 300)),
        "MinRangeTicks": int(p.get("MinRangeTicks", 20)),
        "Reverse": bool(p.get("Reverse", False)),
        "Contracts": int(p.get("Contracts", 1)),
    }
    reverse = "true" if params["Reverse"] else "false"
    return f"""using System;
using System.ComponentModel;
using System.ComponentModel.DataAnnotations;
using NinjaTrader.Cbi;
using NinjaTrader.Data;
using NinjaTrader.NinjaScript;
using NinjaTrader.NinjaScript.Indicators;

namespace NinjaTrader.NinjaScript.Strategies
{{
    // Overnight-range fade. Each Globex session accumulates the high and low
    // from the session open through the cash open, then fades the FIRST break
    // of each side that occurs inside the entry window. Stop and target are
    // multiples of the ATR of the bar before the signal, because the measured
    // edge is flat below one ATR of stop and plateaus from two upward.
    public class {name} : Strategy
    {{
        private double onHigh;
        private double onLow;
        private int onBars;
        private bool onReady;
        private bool rangeLatched;
        private bool firedUp;
        private bool firedDown;
        private double trailPrice;
        private ATR atr;

        protected override void OnStateChange()
        {{
            if (State == State.SetDefaults)
            {{
                Name = "{name}";
                Description = "Fade the first regular-hours break of the overnight range.";
                Calculate = Calculate.OnBarClose;
                // A market entry with a bracket can reach the stop and the
                // target inside one minute. Tick fills decide that ordering
                // rather than leaving it to a bar-level convention, which the
                // source study showed was not merely conservative but
                // anti-correlated with the tape.
                OrderFillResolution = OrderFillResolution.High;
                OrderFillResolutionType = BarsPeriodType.Tick;
                OrderFillResolutionValue = 1;
                EntriesPerDirection = 1;
                EntryHandling = EntryHandling.AllEntries;
                IsExitOnSessionCloseStrategy = true;
                ExitOnSessionCloseSeconds = 30;
                BarsRequiredToTrade = 20;
                DefaultQuantity = 1;
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
                MinOvernightBars = {params["MinOvernightBars"]};
                MinRangeTicks = {params["MinRangeTicks"]};
                Reverse = {reverse};
                Contracts = {params["Contracts"]};
            }}
            else if (State == State.DataLoaded)
            {{
                atr = ATR(Math.Max(2, AtrPeriod));
            }}
        }}

        // Minutes elapsed since the session open, wrapping midnight. The
        // overnight window spans 16:01 to 07:30, so a calendar-day reset or a
        // raw minute-of-day test would cut it in two.
        private int MinutesSinceSessionOpen(int minuteOfDay)
        {{
            int open = SessionOpenHour * 60 + SessionOpenMinute;
            int delta = minuteOfDay - open;
            if (delta < 0)
                delta += 1440;
            return delta;
        }}

        private int OffsetFromSessionOpen(int hour, int minute)
        {{
            return MinutesSinceSessionOpen(hour * 60 + minute);
        }}

        protected override void OnBarUpdate()
        {{
            if (CurrentBar < BarsRequiredToTrade || CurrentBar < AtrPeriod + 1)
                return;

            if (Bars.IsFirstBarOfSession)
            {{
                onHigh = double.MinValue;
                onLow = double.MaxValue;
                onBars = 0;
                onReady = false;
                rangeLatched = false;
                firedUp = false;
                firedDown = false;
            }}

            int elapsed = MinutesSinceSessionOpen(Time[0].Hour * 60 + Time[0].Minute);
            int rthOffset = OffsetFromSessionOpen(RthOpenHour, RthOpenMinute);
            int flatOffset = OffsetFromSessionOpen(FlatByHour, FlatByMinute);

            // Accumulate the overnight range, then latch it at the cash open.
            if (!rangeLatched)
            {{
                if (elapsed <= rthOffset)
                {{
                    onHigh = Math.Max(onHigh, High[0]);
                    onLow = Math.Min(onLow, Low[0]);
                    onBars++;
                    return;
                }}
                rangeLatched = true;
                onReady = onBars >= MinOvernightBars
                    && (onHigh - onLow) >= MinRangeTicks * TickSize;
            }}

            // Flat before the cash close regardless of what else is true.
            if (Position.MarketPosition != MarketPosition.Flat && elapsed >= flatOffset)
            {{
                // Bare form: the flatten must match whichever entry is open,
                // and a named fromEntrySignal that does not match is silently
                // ignored by NinjaTrader.
                if (Position.MarketPosition == MarketPosition.Long)
                    ExitLong();
                else
                    ExitShort();
                return;
            }}

            if (TrailBars > 0 && Position.MarketPosition != MarketPosition.Flat)
            {{
                UpdateStructureTrail();
                return;
            }}

            if (!onReady || Position.MarketPosition != MarketPosition.Flat)
                return;
            if (elapsed <= rthOffset || elapsed > rthOffset + EntryWindowMinutes)
                return;

            bool upFresh = High[0] > onHigh && !firedUp;
            bool downFresh = Low[0] < onLow && !firedDown;
            if (!upFresh && !downFresh)
                return;

            // A bar that takes out both sides says nothing about which side
            // won; consume both levels rather than guessing.
            if (upFresh && downFresh)
            {{
                firedUp = true;
                firedDown = true;
                return;
            }}

            double atrTicks = atr[1] / TickSize;
            if (atrTicks <= 0 || double.IsNaN(atrTicks))
                return;
            int stopTicks = Math.Max(1, (int)Math.Round(StopAtrMult * atrTicks));
            int targetTicks = Math.Max(1, (int)Math.Round(TargetAtrMult * atrTicks));

            SetStopLoss("", CalculationMode.Ticks, stopTicks, false);
            if (TrailBars <= 0)
                SetProfitTarget("", CalculationMode.Ticks, targetTicks);

            // Fade: a break of the overnight high is sold. Reverse trades the
            // continuation instead, which is the control arm of the study.
            bool goShort = upFresh ? !Reverse : Reverse;
            if (goShort)
            {{
                EnterShort(Contracts, "OnFadeShort");
                trailPrice = double.MaxValue;
            }}
            else
            {{
                EnterLong(Contracts, "OnFadeLong");
                trailPrice = double.MinValue;
            }}
            if (upFresh)
                firedUp = true;
            else
                firedDown = true;
        }}

        // Ratchet a stop to the extreme of the last TrailBars bars, never
        // loosening it. The source study found this roughly doubles expectancy
        // over a symmetric bracket once the entry actually has an edge.
        private void UpdateStructureTrail()
        {{
            int look = Math.Min(TrailBars, CurrentBar);
            if (look < 1)
                return;

            if (Position.MarketPosition == MarketPosition.Long)
            {{
                double level = Low[0];
                for (int i = 1; i < look; i++)
                    level = Math.Min(level, Low[i]);
                if (trailPrice == double.MinValue || level > trailPrice)
                {{
                    trailPrice = level;
                    SetStopLoss("", CalculationMode.Price, trailPrice, false);
                }}
            }}
            else
            {{
                double level = High[0];
                for (int i = 1; i < look; i++)
                    level = Math.Max(level, High[i]);
                if (trailPrice == double.MaxValue || level < trailPrice)
                {{
                    trailPrice = level;
                    SetStopLoss("", CalculationMode.Price, trailPrice, false);
                }}
            }}
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
        [Range(15, 390)]
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

        [NinjaScriptProperty]
        [Range(0.5, 8.0)]
        [Display(Name = "TargetAtrMult", GroupName = "Bracket", Order = 10)]
        public double TargetAtrMult {{ get; set; }}

        [NinjaScriptProperty]
        [Range(0, 30)]
        [Display(Name = "TrailBars", GroupName = "Bracket", Order = 11)]
        public int TrailBars {{ get; set; }}

        [NinjaScriptProperty]
        [Range(0, 900)]
        [Display(Name = "MinOvernightBars", GroupName = "Signal", Order = 12)]
        public int MinOvernightBars {{ get; set; }}

        [NinjaScriptProperty]
        [Range(0, 400)]
        [Display(Name = "MinRangeTicks", GroupName = "Signal", Order = 13)]
        public int MinRangeTicks {{ get; set; }}

        [NinjaScriptProperty]
        [Display(Name = "Reverse", GroupName = "Signal", Order = 14)]
        public bool Reverse {{ get; set; }}

        [NinjaScriptProperty]
        [Range(1, 10)]
        [Display(Name = "Contracts", GroupName = "Bracket", Order = 15)]
        public int Contracts {{ get; set; }}
    }}
}}
"""


register_family("overnight_range_fade", _overnight_range_fade_renderer)
