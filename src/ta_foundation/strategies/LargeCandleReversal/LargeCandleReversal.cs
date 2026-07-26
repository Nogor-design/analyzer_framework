// LargeCandleReversal.cs
// =============================================================================
// Large Candle Reversal — Blueprint-Parameterized Strategy
// =============================================================================
// Consumes a strategy blueprint emitted by
//   ta_foundation.analysis.large_candle_excursion.strategy_blueprint_exporter
// Every input corresponds 1:1 to a field in the blueprint JSON.  Swap
// candidates by loading a different template XML.
//
// Onset supported: first_large_after_failed_continuation (top-3 candidates).
// Other onset enum slots are defined so future blueprints plug in without
// structural change, but their detection logic is not yet implemented and
// now emits a LOUD warning at strategy start (was: silent debug print).
//
// Post-entry hold evaluation (bar+2) supports six rules from the exporter:
//   - midpoint_reclaim_yes
//   - rebreak_no
//   - explosive_start  (fav_2bar_pct >= X AND adv_2bar_pct <= Y)
//   - orderly_start    (fav_2bar_pct >= X AND adv_2bar_pct <= Y AND midpoint reclaimed)
//   - fav2bar_only     (fav_2bar_pct >= X)
//   - adv2bar_only     (adv_2bar_pct <= Y)
// Under HoldRuleMode.AnyOf the strategy holds if any rule passes; under
// PrimaryOnly it evaluates only the primary hold rule.
//
// =============================================================================
// PATCH NOTES — 2026-04-24 (rev 4 — protective stop in OnExecutionUpdate)
// =============================================================================
// FIX #11  Eliminated the SetStopLoss/ExitLong*StopMarket coexistence window.
//
//          Per NT support guidance ("To track order objects and use
//          OnOrderUpdate()/OnExecutionUpdate() you must use Exit methods,
//          such as ExitLongStopMarket() or ExitLongLimit(), instead of Set
//          methods"), mixing SetStopLoss with explicit Exit* orders on the
//          same position causes a brief window where both a "Stop loss" order
//          (from Set) and our LcrStop* order are working simultaneously.
//
//          Fixed: SetStopLoss removed entirely from TryEnter.  TryEnter now
//          only stores the planned stop offset in `plannedStopTicks`.  The
//          new OnExecutionUpdate handler fires when the entry actually fills,
//          reads execution.Order.AverageFillPrice (the REAL fill, which for
//          MarketOnNextOpen entries is the next bar's open, not Close[0]),
//          and submits the protective stop via ExitLong/ShortStopMarket using
//          the same SIGNAL_STOP_LONG/SHORT name the trail logic uses.
//
//          This also tightens stop accuracy: the previous code derived the
//          stop price from Close[0] of the signal bar, but the actual fill
//          happens at the next bar's open and can be several ticks away.
//
//          Side-effect: InitTrailStop now only moves the stop if the ATR
//          trail level is tighter than the protective level (since the
//          protective stop is already working and re-submitting at a wider
//          price would be a bug).
// =============================================================================
// PATCH NOTES — 2026-04-24 (rev 3 — Advanced Order Handling compliance)
// =============================================================================
// FIX #10  Trailing stop now follows NT's Advanced Order Handling guide:
//
//          (a) isLiveUntilCancelled=true on every explicit Exit* submission so
//              the order persists across bars.  Default behaviour cancels at
//              end of bar if not re-submitted, which would have left the
//              position un-trailed for the duration of any bar where
//              UpdateTrailStop chose not to tighten.
//
//          (b) IOrder assignment moved from OnBarUpdate into OnOrderUpdate.
//              The docs warn: "This is more reliable than assigning Order
//              objects in OnBarUpdate, as the assignment is not guaranteed to
//              be complete if it is referenced immediately after submitting."
//              Stop and target IOrder refs are now matched by signal name
//              constants (SIGNAL_STOP_LONG, SIGNAL_TARGET_LONG, etc.) and
//              assigned/nulled in the new OnOrderUpdate handler.
//
//          (c) Historical -> live transition handled via GetRealtimeOrder().
//              Without this, the first ChangeOrder/CancelOrder after the
//              strategy transitions to State.Realtime would have disabled the
//              strategy with "attempted to modify a historical order that has
//              transitioned to a live order."
//
//          (d) UpdateTrailStop now prefers ChangeOrder() when a valid working
//              IOrder reference exists (the documented modify pattern), and
//              falls back to re-submitting with the same signal name (which
//              NT treats as a replace) when the reference hasn't propagated
//              yet — e.g., on the very next bar after the initial submission.
//
//          (e) Terminal states (Cancelled/Filled/Rejected) null the reference
//              so we never call ChangeOrder against a dead order.
// =============================================================================
// PATCH NOTES — 2026-04-24 (rev 2 — stop/order management rewrite)
// =============================================================================
// FIX #7  Stop placement order — SetStopLoss() was being called AFTER
//         EnterLong/EnterShort.  NinjaTrader's managed approach requires Set
//         methods to be called BEFORE the associated entry order so the stop
//         can be attached to the entry execution.  Fixed: SetStopLoss is now
//         called immediately before EnterLong/EnterShort in TryEnter().
//
//         Additionally, the stop was submitted with CalculationMode.Price.
//         Price-mode Set stops cannot be reliably changed after the entry fills.
//         Changed to CalculationMode.Ticks (stopTicks already computed) so the
//         initial protective stop is valid regardless of fill slippage.
//
// FIX #8  Trail stop — InitTrailStop() and UpdateTrailStop() were calling
//         SetStopLoss() to move the stop after entry.  Per NinjaTrader docs,
//         SetStopLoss() is a "Set method" whose price is locked in when the
//         entry fills; subsequent calls to change it in OnBarUpdate() are
//         unreliable and can be silently ignored or cause order conflicts.
//
//         Fixed: both methods now use ExitLongStopMarket() / ExitShortStopMarket()
//         with explicit signal names ("LcrStop" fromEntrySignal "LcrLong/Short").
//         Re-submitting the same signal name on each bar replaces the working
//         stop order with the new price, which is the correct managed-approach
//         pattern for dynamic stop movement.
//
// FIX #9  Dynamic profit target — ManagePosition() called SetProfitTarget()
//         after the hold-decision bar to switch between scalp and runner targets.
//         SetProfitTarget() has the same "locked after fill" limitation as
//         SetStopLoss(); calling it mid-trade to change the target price does
//         not reliably update the working limit order.
//
//         Fixed: all target placements in ManagePosition() now use
//         ExitLongLimit() / ExitShortLimit() with fromEntrySignal so NT can
//         pair them correctly.  IOrder references (activeStopOrder,
//         activeTargetOrder) are stored and cleared in ResetTradeState().
// =============================================================================
// FIX #1  Session labels — ClassifySession now emits "asia" for the overnight
//         Asia window (~18:00–03:00 ET). The old free-text AllowedSessionsCsv
//         field has been REPLACED with ten individual bool properties
//         (AllowSessionAsia, AllowSessionNyOpen, ...) so a template cannot
//         misspell a session label (e.g., "ny_pre" instead of "ny_pre_open")
//         and silently take zero trades. A warning is still emitted if
//         SessionMode=Allowlist is chosen with no boxes ticked.
//
// FIX #3  Post-entry lookback off-by-one — EvaluateMidpointReclaim,
//         EvaluateRebreakNo, EvaluateExplosiveStart used `i <= lookback`,
//         which included the signal bar itself in the backward iteration.
//         Midpoint reclaim was therefore trivially true for every trade.
//         Changed to `i < lookback` so only post-entry bars are considered.
//
// FIX #4a Default FailedContinuationLookbackSignals lowered 3 -> 1 to match
//         the research definition ("the previous signal had weak early
//         behavior"), which is singular.
//
// FIX #4b UpdateRebreakFlagsFor now runs on every bar (not only on qualifying
//         signal bars) and is bounded by FailedSignalRebreakWindowBars so
//         stale signals don't accumulate rebreak flags indefinitely.
//
// FIX #5  Added OrderlyStart, Fav2BarOnly, Adv2BarOnly to LcrPrimaryHoldRule
//         and implemented their evaluators. Also added a hard startup error
//         when an unimplemented onset is selected so the problem is visible.
//
// FIX #6  IsSignalCandle now enforces optional MaxRangeTicks / MaxBodyTicks
//         upper bounds so templates can pin the research candle bucket
//         (e.g., 50-75 ticks). Zero means "no upper bound" (back-compat).
// =============================================================================

#region Using declarations
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.ComponentModel.DataAnnotations;
using System.Xml.Serialization;
using NinjaTrader.Cbi;
using NinjaTrader.Data;
using NinjaTrader.NinjaScript;
using NinjaTrader.NinjaScript.Indicators;
using NinjaTrader.NinjaScript.Strategies;
using NinjaTrader.NinjaScript.DrawingTools;
using System.Windows.Media;
#endregion

namespace NinjaTrader.NinjaScript.Strategies
{
    // =========================================================================
    // Enums — blueprint field values map onto these.
    // =========================================================================

    public enum LcrOnsetCondition
    {
        FirstLargeAfterFailedContinuation = 0,
        FirstLargeAfterDirectionalRun     = 1,   // reserved
        FirstLargeAtKeyLevelVwapStretched = 2,   // reserved
        FirstLargeAfterSessionRangeBreak  = 3,   // reserved
        FirstLargeAfterCompression        = 4,   // reserved
    }

    public enum LcrDirectionPolicy
    {
        CounterToFailedContinuation = 0,
        CounterToDirectionalRun     = 1,
        CounterToLevelRejection     = 2,
        ContinuationOfRangeBreak    = 3,
        ContinuationOfCompression   = 4,
        SignalDirection             = 5,
    }

    public enum LcrCandleBasis
    {
        Range = 0,
        Body  = 1,
    }

    public enum LcrTriggerEvent
    {
        OnBarClose = 0,
    }

    public enum LcrFillType
    {
        MarketOnNextOpen = 0,
    }

    public enum LcrStopStyle
    {
        SignalCandleExtremeWithCap = 0,
        AtrMultiple                = 1,
    }

    public enum LcrHoldRuleMode
    {
        AnyOf       = 0,
        PrimaryOnly = 1,
    }

    public enum LcrPrimaryHoldRule
    {
        MidpointReclaimYes = 0,
        RebreakNo          = 1,
        ExplosiveStart     = 2,
        None               = 3,
        OrderlyStart       = 4,   // FIX #5
        Fav2BarOnly        = 5,   // FIX #5
        Adv2BarOnly        = 6,   // FIX #5
    }

    public enum LcrOnFailAction
    {
        ExitAtMarket       = 0,
        ExitAtScalpTarget  = 1,
    }

    public enum LcrOnPassAction
    {
        TrailForRunner     = 0,
        ExitAtScalpTarget  = 1,
    }

    public enum LcrTrailStyle
    {
        Atr       = 0,
        Chandelier = 1,
        None       = 2,
    }

    public enum LcrSessionMode
    {
        AllSession = 0,
        Allowlist  = 1,
    }

    // =========================================================================
    // Rolling helpers
    // =========================================================================

    internal class LcrRollingAverage
    {
        private readonly int cap;
        private readonly Queue<double> q;
        private double sum;

        public LcrRollingAverage(int capacity)
        {
            cap = Math.Max(1, capacity);
            q   = new Queue<double>(cap + 1);
            sum = 0.0;
        }

        public int Count => q.Count;

        public void Add(double v)
        {
            q.Enqueue(v);
            sum += v;
            if (q.Count > cap)
                sum -= q.Dequeue();
        }

        public double Value => q.Count == 0 ? 0.0 : sum / q.Count;
    }

    internal class LcrRollingSignal
    {
        public int BarIndex;
        public DateTime Time;
        public int Direction;       // +1 bull, -1 bear
        public double SizeTicks;
        public double High;
        public double Low;
        public bool RebrokeWithinN; // set when a later bar breaks back through signal extreme
    }

    // =========================================================================
    // Strategy
    // =========================================================================

    [Gui.CategoryOrder("A: Blueprint",               1)]
    [Gui.CategoryOrder("B: Onset Detection",         2)]
    [Gui.CategoryOrder("C: Context Gates",           3)]
    [Gui.CategoryOrder("D: Quiet Filter",            4)]
    [Gui.CategoryOrder("E: Session Filter",          5)]
    [Gui.CategoryOrder("F: Entry",                   6)]
    [Gui.CategoryOrder("G: Stop",                    7)]
    [Gui.CategoryOrder("H: Post-Entry Management",   8)]
    [Gui.CategoryOrder("I: Exit / Targets",          9)]
    [Gui.CategoryOrder("J: Risk",                   10)]
    [Gui.CategoryOrder("K: Debug",                  11)]
    public class LargeCandleReversal : Strategy
    {
        // =====================================================================
        // Private fields
        // =====================================================================

        private ATR atrIndicator;

        private LcrRollingAverage volumeAvg;
        private LcrRollingAverage rangeAvg;

        // Prior large-candle signal buffer (for quiet filter + failed-continuation detection).
        private List<LcrRollingSignal> priorSignals;
        private const int PRIOR_SIGNAL_BUFFER = 50;

        // Direction streak (consecutive same-direction large candles).
        private int directionStreak = 0;
        private int lastSignalDir   = 0;

        // Current trade state.
        private bool   inPosition      = false;
        private bool   isLongTrade     = false;
        private int    entryBar        = -1;
        private double entryPrice      = 0.0;
        private double signalHigh      = 0.0;
        private double signalLow       = 0.0;
        private double signalSizeTicks = 0.0;
        private double signalMidpoint  = 0.0;
        private double stopPrice       = 0.0;
        private double scalpTargetPrice    = 0.0;
        private double expansionTargetPrice = 0.0;
        private double runnerTargetPrice   = 0.0;
        private double trailStopPrice   = 0.0;
        private double plannedStopTicks = 0.0;   // Set in TryEnter, consumed in OnExecutionUpdate.
        private bool   holdDecisionMade = false;
        private bool   holdDecisionPass = false;
        private DateTime entryTime      = DateTime.MinValue;

        // IOrder references for managed exit orders (stop + target).
        // Per NinjaTrader's Advanced Order Handling guide, these references are
        // assigned in OnOrderUpdate (NOT in OnBarUpdate), because the assignment
        // returned by ExitLong*/ExitShort* "is not guaranteed to be complete if
        // it is referenced immediately after submitting".  We submit with
        // isLiveUntilCancelled=true so the order persists across bars; trail
        // movement re-submits with the SAME signal name to update price.
        //
        // Signal names used (kept in constants so OnOrderUpdate can match):
        private const string SIGNAL_STOP_LONG    = "LcrStopLong";
        private const string SIGNAL_STOP_SHORT   = "LcrStopShort";
        private const string SIGNAL_TARGET_LONG  = "LcrTargetLong";
        private const string SIGNAL_TARGET_SHORT = "LcrTargetShort";
        private const string SIGNAL_ENTRY_LONG   = "LcrLong";
        private const string SIGNAL_ENTRY_SHORT  = "LcrShort";

        private Order activeStopOrder   = null;
        private Order activeTargetOrder = null;

        // Session allow-list cache.
        private HashSet<string> allowedSessionSet = new HashSet<string>();

        // =====================================================================
        // Inputs — grouped to mirror the blueprint sections.
        // =====================================================================

        #region A: Blueprint

        [NinjaScriptProperty]
        [Display(Name = "Blueprint ID",  GroupName = "A: Blueprint", Order = 1)]
        public string BlueprintId { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Onset Condition", GroupName = "A: Blueprint", Order = 2)]
        public LcrOnsetCondition OnsetCondition { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Direction Policy", GroupName = "A: Blueprint", Order = 3)]
        public LcrDirectionPolicy DirectionPolicy { get; set; }

        #endregion

        #region B: Onset Detection

        [NinjaScriptProperty]
        [Range(2, 200)]
        [Display(Name = "Candle Lookback Bars", GroupName = "B: Onset Detection", Order = 1)]
        public int CandleLookbackBars { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Candle Basis", GroupName = "B: Onset Detection", Order = 2)]
        public LcrCandleBasis CandleBasis { get; set; }

        [NinjaScriptProperty]
        [Range(1.0, 10.0)]
        [Display(Name = "Threshold Multiplier", GroupName = "B: Onset Detection", Order = 3)]
        public double ThresholdMultiplier { get; set; }

        [NinjaScriptProperty]
        [Range(0, 100)]
        [Display(Name = "Min Body Ticks", GroupName = "B: Onset Detection", Order = 4)]
        public int MinBodyTicks { get; set; }

        [NinjaScriptProperty]
        [Range(0, 200)]
        [Display(Name = "Min Range Ticks", GroupName = "B: Onset Detection", Order = 5)]
        public int MinRangeTicks { get; set; }

        // FIX #6 — optional maximums so templates can pin the research bucket.
        // Zero = no upper bound (back-compat with existing templates).
        [NinjaScriptProperty]
        [Range(0, 1000)]
        [Display(Name = "Max Body Ticks (0 = disabled)", GroupName = "B: Onset Detection", Order = 6)]
        public int MaxBodyTicks { get; set; }

        [NinjaScriptProperty]
        [Range(0, 1000)]
        [Display(Name = "Max Range Ticks (0 = disabled)", GroupName = "B: Onset Detection", Order = 7)]
        public int MaxRangeTicks { get; set; }

        [NinjaScriptProperty]
        [Range(1, 10)]
        [Display(Name = "Failed Continuation Lookback Signals", GroupName = "B: Onset Detection", Order = 8)]
        public int FailedContinuationLookbackSignals { get; set; }

        // FIX #4b — bound rebreak-detection to a bar window after each prior signal.
        [NinjaScriptProperty]
        [Range(1, 200)]
        [Display(Name = "Failed Signal Rebreak Window Bars", GroupName = "B: Onset Detection", Order = 9)]
        public int FailedSignalRebreakWindowBars { get; set; }

        [NinjaScriptProperty]
        [Range(5, 200)]
        [Display(Name = "ATR Period", GroupName = "B: Onset Detection", Order = 10)]
        public int AtrPeriod { get; set; }

        #endregion

        #region C: Context Gates

        [NinjaScriptProperty]
        [Range(0.0, 10.0)]
        [Display(Name = "VWAP Stretch ATR Min", GroupName = "C: Context Gates", Order = 1)]
        public double VwapStretchAtrMin { get; set; }

        [NinjaScriptProperty]
        [Range(1.0, 20.0)]
        [Display(Name = "Volume Multiple Min", GroupName = "C: Context Gates", Order = 2)]
        public double VolumeMultipleMin { get; set; }

        [NinjaScriptProperty]
        [Range(0, 20)]
        [Display(Name = "Direction Streak Min", GroupName = "C: Context Gates", Order = 3)]
        public int DirectionStreakMin { get; set; }

        [NinjaScriptProperty]
        [Range(1.0, 10.0)]
        [Display(Name = "Range Expansion Multiple Min", GroupName = "C: Context Gates", Order = 4)]
        public double RangeExpansionMultipleMin { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Require Context Gates (else report only)", GroupName = "C: Context Gates", Order = 5)]
        public bool RequireContextGates { get; set; }

        #endregion

        #region D: Quiet Filter

        [NinjaScriptProperty]
        [Range(0, 600)]
        [Display(Name = "Quiet Lookback Minutes", GroupName = "D: Quiet Filter", Order = 1)]
        public int QuietLookbackMinutes { get; set; }

        [NinjaScriptProperty]
        [Range(0, 20)]
        [Display(Name = "Quiet Max Prior Signals", GroupName = "D: Quiet Filter", Order = 2)]
        public int QuietMaxPriorSignals { get; set; }

        #endregion

        #region E: Session Filter

        [NinjaScriptProperty]
        [Display(Name = "Session Mode", GroupName = "E: Session Filter", Order = 1)]
        public LcrSessionMode SessionMode { get; set; }

        // Individual session checkboxes — replaces the old AllowedSessionsCsv
        // free-text field so templates cannot drift via misspelling (e.g., the
        // old "ny_pre" / "ny_pre_open" bug). The display names mirror the
        // canonical labels emitted by ClassifySession.

        [NinjaScriptProperty]
        [Display(Name = "  Allow asia (20:00–03:00 ET)",        GroupName = "E: Session Filter", Order = 2)]
        public bool AllowSessionAsia { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "  Allow early_london (03:00–05:00 ET)", GroupName = "E: Session Filter", Order = 3)]
        public bool AllowSessionEarlyLondon { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "  Allow mid_london (05:00–07:00 ET)",   GroupName = "E: Session Filter", Order = 4)]
        public bool AllowSessionMidLondon { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "  Allow london_ny_overlap (07:00–09:00 ET)", GroupName = "E: Session Filter", Order = 5)]
        public bool AllowSessionLondonNyOverlap { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "  Allow ny_pre_open (09:00–09:30 ET)",  GroupName = "E: Session Filter", Order = 6)]
        public bool AllowSessionNyPreOpen { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "  Allow ny_open (09:30–11:00 ET)",      GroupName = "E: Session Filter", Order = 7)]
        public bool AllowSessionNyOpen { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "  Allow mid_day (11:00–14:00 ET)",      GroupName = "E: Session Filter", Order = 8)]
        public bool AllowSessionMidDay { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "  Allow power_hour (14:00–16:00 ET)",   GroupName = "E: Session Filter", Order = 9)]
        public bool AllowSessionPowerHour { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "  Allow after_hours (16:00–18:00 ET)",  GroupName = "E: Session Filter", Order = 10)]
        public bool AllowSessionAfterHours { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "  Allow overnight (18:00–20:00 ET)",    GroupName = "E: Session Filter", Order = 11)]
        public bool AllowSessionOvernight { get; set; }

        #endregion

        #region F: Entry

        [NinjaScriptProperty]
        [Display(Name = "Trigger Event", GroupName = "F: Entry", Order = 1)]
        public LcrTriggerEvent TriggerEvent { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Fill Type", GroupName = "F: Entry", Order = 2)]
        public LcrFillType FillType { get; set; }

        [NinjaScriptProperty]
        [Range(1, 50)]
        [Display(Name = "Max Entries Per Session", GroupName = "F: Entry", Order = 3)]
        public int MaxEntriesPerSession { get; set; }

        [NinjaScriptProperty]
        [Range(1, 100)]
        [Display(Name = "Contracts", GroupName = "F: Entry", Order = 4)]
        public int Contracts { get; set; }

        #endregion

        #region G: Stop

        [NinjaScriptProperty]
        [Display(Name = "Stop Style", GroupName = "G: Stop", Order = 1)]
        public LcrStopStyle StopStyle { get; set; }

        [NinjaScriptProperty]
        [Range(0, 100)]
        [Display(Name = "Stop Base Offset Ticks", GroupName = "G: Stop", Order = 2)]
        public int StopBaseOffsetTicks { get; set; }

        [NinjaScriptProperty]
        [Range(10, 400)]
        [Display(Name = "Max Stop Ticks", GroupName = "G: Stop", Order = 3)]
        public int MaxStopTicks { get; set; }

        [NinjaScriptProperty]
        [Range(0.5, 5.0)]
        [Display(Name = "ATR Fallback Multiple", GroupName = "G: Stop", Order = 4)]
        public double AtrFallbackMultiple { get; set; }

        [NinjaScriptProperty]
        [Range(5, 400)]
        [Display(Name = "Recommended Stop Ticks (from blueprint)", GroupName = "G: Stop", Order = 5)]
        public int RecommendedStopTicks { get; set; }

        #endregion

        #region H: Post-Entry Management

        [NinjaScriptProperty]
        [Range(1, 10)]
        [Display(Name = "Evaluation Bar", GroupName = "H: Post-Entry Management", Order = 1)]
        public int EvaluationBar { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Hold Rule Mode", GroupName = "H: Post-Entry Management", Order = 2)]
        public LcrHoldRuleMode HoldRuleMode { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Primary Hold Rule", GroupName = "H: Post-Entry Management", Order = 3)]
        public LcrPrimaryHoldRule PrimaryHoldRule { get; set; }

        [NinjaScriptProperty]
        [Range(1, 10)]
        [Display(Name = "Midpoint Reclaim Bars", GroupName = "H: Post-Entry Management", Order = 4)]
        public int MidpointReclaimBars { get; set; }

        [NinjaScriptProperty]
        [Range(1, 10)]
        [Display(Name = "Rebreak Check Bars", GroupName = "H: Post-Entry Management", Order = 5)]
        public int RebreakCheckBars { get; set; }

        [NinjaScriptProperty]
        [Range(0.0, 100.0)]
        [Display(Name = "Explosive Fav2Bar Pct Min", GroupName = "H: Post-Entry Management", Order = 6)]
        public double ExplosiveFav2BarPctMin { get; set; }

        [NinjaScriptProperty]
        [Range(0.0, 100.0)]
        [Display(Name = "Explosive Adv2Bar Pct Max", GroupName = "H: Post-Entry Management", Order = 7)]
        public double ExplosiveAdv2BarPctMax { get; set; }

        // FIX #5 — Orderly start threshold inputs.
        [NinjaScriptProperty]
        [Range(0.0, 100.0)]
        [Display(Name = "Orderly Fav2Bar Pct Min", GroupName = "H: Post-Entry Management", Order = 8)]
        public double OrderlyFav2BarPctMin { get; set; }

        [NinjaScriptProperty]
        [Range(0.0, 100.0)]
        [Display(Name = "Orderly Adv2Bar Pct Max", GroupName = "H: Post-Entry Management", Order = 9)]
        public double OrderlyAdv2BarPctMax { get; set; }

        // FIX #5 — Atomic fav/adv 2-bar thresholds (used by Fav2BarOnly / Adv2BarOnly).
        [NinjaScriptProperty]
        [Range(0.0, 100.0)]
        [Display(Name = "Fav2BarOnly Pct Min", GroupName = "H: Post-Entry Management", Order = 10)]
        public double Fav2BarOnlyPctMin { get; set; }

        [NinjaScriptProperty]
        [Range(0.0, 100.0)]
        [Display(Name = "Adv2BarOnly Pct Max", GroupName = "H: Post-Entry Management", Order = 11)]
        public double Adv2BarOnlyPctMax { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "On-Fail Action", GroupName = "H: Post-Entry Management", Order = 12)]
        public LcrOnFailAction OnFailAction { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "On-Pass Action", GroupName = "H: Post-Entry Management", Order = 13)]
        public LcrOnPassAction OnPassAction { get; set; }

        #endregion

        #region I: Exit / Targets

        [NinjaScriptProperty]
        [Range(1, 300)]
        [Display(Name = "Scalp Target % of Signal Candle", GroupName = "I: Exit / Targets", Order = 1)]
        public int ScalpTargetPct { get; set; }

        [NinjaScriptProperty]
        [Range(1, 300)]
        [Display(Name = "Expansion Target % of Signal Candle", GroupName = "I: Exit / Targets", Order = 2)]
        public int ExpansionTargetPct { get; set; }

        [NinjaScriptProperty]
        [Range(1, 500)]
        [Display(Name = "Runner Target % of Signal Candle", GroupName = "I: Exit / Targets", Order = 3)]
        public int RunnerTargetPct { get; set; }

        [NinjaScriptProperty]
        [Range(1, 600)]
        [Display(Name = "Time Stop Minutes", GroupName = "I: Exit / Targets", Order = 4)]
        public int TimeStopMinutes { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Trail Style", GroupName = "I: Exit / Targets", Order = 5)]
        public LcrTrailStyle TrailStyle { get; set; }

        [NinjaScriptProperty]
        [Range(0.25, 5.0)]
        [Display(Name = "ATR Trail Multiple", GroupName = "I: Exit / Targets", Order = 6)]
        public double AtrTrailMultiple { get; set; }

        #endregion

        #region J: Risk

        [NinjaScriptProperty]
        [Range(1, 20)]
        [Display(Name = "Base Unit", GroupName = "J: Risk", Order = 1)]
        public int BaseUnit { get; set; }

        [NinjaScriptProperty]
        [Range(0, 10)]
        [Display(Name = "Max Add-Ons", GroupName = "J: Risk", Order = 2)]
        public int MaxAddOns { get; set; }

        [NinjaScriptProperty]
        [Range(1, 20)]
        [Display(Name = "Max Total Units", GroupName = "J: Risk", Order = 3)]
        public int MaxTotalUnits { get; set; }

        #endregion

        #region K: Debug

        [NinjaScriptProperty]
        [Display(Name = "Enable Debug Print", GroupName = "K: Debug", Order = 1)]
        public bool EnableDebugPrint { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Draw Markers On Chart", GroupName = "K: Debug", Order = 2)]
        public bool DrawMarkers { get; set; }

        #endregion

        // =====================================================================
        // State machine
        // =====================================================================

        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Description = "Large Candle Reversal — parameterised from a ta_foundation strategy blueprint.";
                Name        = "LargeCandleReversal";

                Calculate                                 = Calculate.OnBarClose;
                EntriesPerDirection                       = 1;
                EntryHandling                             = EntryHandling.AllEntries;
                IsExitOnSessionCloseStrategy              = true;
                ExitOnSessionCloseSeconds                 = 30;
                MaximumBarsLookBack                       = MaximumBarsLookBack.TwoHundredFiftySix;
                BarsRequiredToTrade                       = 60;
                IsInstantiatedOnEachOptimizationIteration = false;
                StartBehavior                             = StartBehavior.WaitUntilFlat;
                StopTargetHandling                        = StopTargetHandling.PerEntryExecution;
                TraceOrders                               = false;

                // Blueprint defaults — overridden by template XML.
                BlueprintId                       = "lcr_default";
                OnsetCondition                    = LcrOnsetCondition.FirstLargeAfterFailedContinuation;
                DirectionPolicy                   = LcrDirectionPolicy.CounterToFailedContinuation;

                CandleLookbackBars                = 20;
                CandleBasis                       = LcrCandleBasis.Range;
                ThresholdMultiplier               = 1.5;
                MinBodyTicks                      = 2;
                MinRangeTicks                     = 4;
                MaxBodyTicks                      = 0;    // FIX #6: 0 = disabled
                MaxRangeTicks                     = 0;    // FIX #6: 0 = disabled
                FailedContinuationLookbackSignals = 1;    // FIX #4a: was 3
                FailedSignalRebreakWindowBars     = 10;   // FIX #4b
                AtrPeriod                         = 14;

                VwapStretchAtrMin                 = 0.75;
                VolumeMultipleMin                 = 2.5;
                DirectionStreakMin                = 3;
                RangeExpansionMultipleMin         = 1.5;
                RequireContextGates               = false;

                QuietLookbackMinutes              = 60;
                QuietMaxPriorSignals              = 1;

                SessionMode                       = LcrSessionMode.AllSession;
                AllowSessionAsia                  = false;
                AllowSessionEarlyLondon           = false;
                AllowSessionMidLondon             = false;
                AllowSessionLondonNyOverlap       = false;
                AllowSessionNyPreOpen             = false;
                AllowSessionNyOpen                = false;
                AllowSessionMidDay                = false;
                AllowSessionPowerHour             = false;
                AllowSessionAfterHours            = false;
                AllowSessionOvernight             = false;

                TriggerEvent                      = LcrTriggerEvent.OnBarClose;
                FillType                          = LcrFillType.MarketOnNextOpen;
                MaxEntriesPerSession              = 1;
                Contracts                         = 1;

                StopStyle                         = LcrStopStyle.SignalCandleExtremeWithCap;
                StopBaseOffsetTicks               = 4;
                MaxStopTicks                      = 100;
                AtrFallbackMultiple               = 1.2;
                RecommendedStopTicks              = 40;

                EvaluationBar                     = 2;
                HoldRuleMode                      = LcrHoldRuleMode.AnyOf;
                PrimaryHoldRule                   = LcrPrimaryHoldRule.MidpointReclaimYes;
                MidpointReclaimBars               = 2;
                RebreakCheckBars                  = 2;
                ExplosiveFav2BarPctMin            = 45.0;
                ExplosiveAdv2BarPctMax            = 20.0;
                OrderlyFav2BarPctMin              = 25.0;   // FIX #5
                OrderlyAdv2BarPctMax              = 35.0;   // FIX #5
                Fav2BarOnlyPctMin                 = 35.0;   // FIX #5
                Adv2BarOnlyPctMax                 = 20.0;   // FIX #5
                OnFailAction                      = LcrOnFailAction.ExitAtMarket;
                OnPassAction                      = LcrOnPassAction.TrailForRunner;

                ScalpTargetPct                    = 30;
                ExpansionTargetPct                = 62;
                RunnerTargetPct                   = 125;
                TimeStopMinutes                   = 30;
                TrailStyle                        = LcrTrailStyle.Atr;
                AtrTrailMultiple                  = 1.5;

                BaseUnit                          = 1;
                MaxAddOns                         = 1;
                MaxTotalUnits                     = 2;

                EnableDebugPrint                  = false;
                DrawMarkers                       = true;
            }
            else if (State == State.DataLoaded)
            {
                atrIndicator = ATR(AtrPeriod);
                volumeAvg    = new LcrRollingAverage(CandleLookbackBars);
                rangeAvg     = new LcrRollingAverage(CandleLookbackBars);
                priorSignals = new List<LcrRollingSignal>(PRIOR_SIGNAL_BUFFER);
                directionStreak = 0;
                lastSignalDir   = 0;

                allowedSessionSet.Clear();
                if (AllowSessionAsia)            allowedSessionSet.Add("asia");
                if (AllowSessionEarlyLondon)     allowedSessionSet.Add("early_london");
                if (AllowSessionMidLondon)       allowedSessionSet.Add("mid_london");
                if (AllowSessionLondonNyOverlap) allowedSessionSet.Add("london_ny_overlap");
                if (AllowSessionNyPreOpen)       allowedSessionSet.Add("ny_pre_open");
                if (AllowSessionNyOpen)          allowedSessionSet.Add("ny_open");
                if (AllowSessionMidDay)          allowedSessionSet.Add("mid_day");
                if (AllowSessionPowerHour)       allowedSessionSet.Add("power_hour");
                if (AllowSessionAfterHours)      allowedSessionSet.Add("after_hours");
                if (AllowSessionOvernight)       allowedSessionSet.Add("overnight");

                // With checkboxes, misspelling is impossible — but we still
                // validate that Allowlist mode actually has at least one box
                // checked, otherwise the strategy silently takes no trades.
                if (SessionMode == LcrSessionMode.Allowlist && allowedSessionSet.Count == 0)
                {
                    Print($"[LCR][WARN] SessionMode=Allowlist but NO session checkboxes are ticked. " +
                          $"Strategy will take NO trades. Either tick at least one session or set " +
                          $"SessionMode=AllSession.");
                }

                // FIX #5 — make unimplemented onsets noisy so they don't silently
                // disable the strategy.
                if (OnsetCondition != LcrOnsetCondition.FirstLargeAfterFailedContinuation)
                {
                    Print($"[LCR][WARN] Onset '{OnsetCondition}' is declared but NOT IMPLEMENTED. " +
                          $"EvaluateOnset will reject every signal. No trades will be taken. " +
                          $"Only FirstLargeAfterFailedContinuation is currently supported.");
                }
            }
            else if (State == State.Realtime)
            {
                ExitLong();
                ExitShort();
                ResetTradeState();
            }
        }

        // =====================================================================
        // OnBarUpdate
        // =====================================================================

        protected override void OnBarUpdate()
        {
            if (CurrentBar < BarsRequiredToTrade)
                return;

            // Feed rolling averages and tag signal candles AFTER update so the
            // context of a bar's "was this a large candle" uses prior bars only
            // (no look-ahead — CandleLookbackBars excludes the current bar).
            UpdateRollingAverages();

            // FIX #4b — update rebreak flags on EVERY bar, not only signal bars.
            // A prior signal can get rebroken by any subsequent bar within the
            // configured bar window; waiting for a later qualifying signal was
            // far too strict and made the failed-continuation onset rare.
            UpdateRebreakFlags();

            // Determine if this bar is a qualifying large candle (signal).
            bool   isSignal  = IsSignalCandle(out int signalDir, out double sizeTicks);
            if (isSignal)
            {
                UpdateDirectionStreak(signalDir);
            }

            // Manage existing position (stop/trail/targets/hold decision).
            if (inPosition)
                ManagePosition();

            // If the current bar is a signal candle and we're flat, evaluate entry.
            if (isSignal && !inPosition)
            {
                if (EvaluateOnset(signalDir) && EvaluateSessionAndQuiet() && EvaluateContextGates())
                {
                    TryEnter(signalDir, sizeTicks);
                }
            }

            // Record the signal AFTER entry logic so the current signal is in the
            // buffer for the next bar's onset check.
            if (isSignal)
                AppendSignalToBuffer(signalDir, sizeTicks);
        }

        // =====================================================================
        // Rolling averages (use prior bars only — look-ahead safe).
        // =====================================================================

        private void UpdateRollingAverages()
        {
            if (CurrentBar >= 1)
            {
                volumeAvg.Add(Volume[1]);
                rangeAvg.Add(High[1] - Low[1]);
            }
        }

        // =====================================================================
        // Large-candle detection
        // =====================================================================

        private bool IsSignalCandle(out int signalDir, out double sizeTicks)
        {
            signalDir = 0;
            sizeTicks = 0.0;

            double body       = Math.Abs(Close[0] - Open[0]);
            double totalRange = High[0] - Low[0];

            double metric = CandleBasis == LcrCandleBasis.Body ? body : totalRange;
            double refAvg = rangeAvg.Count > 0 ? rangeAvg.Value : 0.0;
            if (refAvg <= 0.0)
                return false;

            double ratio = metric / refAvg;
            if (ratio < ThresholdMultiplier)
                return false;

            double rangeTicks = totalRange / TickSize;
            double bodyTicks  = body / TickSize;
            if (rangeTicks < MinRangeTicks || bodyTicks < MinBodyTicks)
                return false;

            // FIX #6 — optional upper bound so templates can pin the research bucket.
            if (MaxRangeTicks > 0 && rangeTicks > MaxRangeTicks) return false;
            if (MaxBodyTicks  > 0 && bodyTicks  > MaxBodyTicks)  return false;

            signalDir = Close[0] >= Open[0] ? +1 : -1;
            sizeTicks = rangeTicks;
            return true;
        }

        // =====================================================================
        // Onset evaluation
        // =====================================================================

        private bool EvaluateOnset(int currentSignalDir)
        {
            switch (OnsetCondition)
            {
                case LcrOnsetCondition.FirstLargeAfterFailedContinuation:
                    return HasFailedContinuation(currentSignalDir);
                default:
                    // Already warned loudly at State.DataLoaded — just reject here.
                    return false;
            }
        }

        /// <summary>
        /// Returns true when at least FailedContinuationLookbackSignals prior
        /// opposite-direction large candles broke back through their own extreme
        /// (rebroke).  The current signal is the reversal candle — its direction
        /// is OPPOSITE to the failed-continuation direction.
        /// </summary>
        private bool HasFailedContinuation(int currentSignalDir)
        {
            int priorFailedDir = -currentSignalDir;
            int failedCount    = 0;
            for (int i = priorSignals.Count - 1; i >= 0; i--)
            {
                var s = priorSignals[i];
                if (s.Direction != priorFailedDir)
                    continue;
                if (s.RebrokeWithinN)
                    failedCount++;
                if (failedCount >= FailedContinuationLookbackSignals)
                    return true;
            }
            return false;
        }

        /// <summary>
        /// FIX #4b — now runs on every bar (not only signal bars) and is bounded
        /// by FailedSignalRebreakWindowBars so a stale signal from 500 bars ago
        /// can't still be flipped by a later bar.
        ///
        /// A prior bullish signal is "rebroken" when a later bar's LOW trades
        /// below the signal's low.  A prior bearish signal is "rebroken" when a
        /// later bar's HIGH trades above the signal's high.  The direction of
        /// the breaking bar is irrelevant — only the price level matters.
        /// </summary>
        private void UpdateRebreakFlags()
        {
            for (int i = priorSignals.Count - 1; i >= 0; i--)
            {
                var s = priorSignals[i];
                if (s.RebrokeWithinN)
                    continue;

                int barsSinceSignal = CurrentBar - s.BarIndex;
                if (barsSinceSignal <= 0)
                    continue;                               // same bar, skip
                if (barsSinceSignal > FailedSignalRebreakWindowBars)
                    continue;                               // outside window

                bool broke = s.Direction > 0
                    ? Low[0]  < s.Low
                    : High[0] > s.High;
                if (broke)
                    s.RebrokeWithinN = true;
            }
        }

        private void AppendSignalToBuffer(int signalDir, double sizeTicks)
        {
            priorSignals.Add(new LcrRollingSignal
            {
                BarIndex = CurrentBar,
                Time     = Time[0],
                Direction = signalDir,
                SizeTicks = sizeTicks,
                High      = High[0],
                Low       = Low[0],
                RebrokeWithinN = false,
            });
            if (priorSignals.Count > PRIOR_SIGNAL_BUFFER)
                priorSignals.RemoveAt(0);
        }

        private void UpdateDirectionStreak(int signalDir)
        {
            if (signalDir == lastSignalDir)
                directionStreak++;
            else
                directionStreak = 1;
            lastSignalDir = signalDir;
        }

        // =====================================================================
        // Context gates + session/quiet filters
        // =====================================================================

        private bool EvaluateContextGates()
        {
            if (!RequireContextGates)
                return true;

            // Range expansion: current-bar range / rolling avg of prior ranges.
            double refRange = rangeAvg.Count > 0 ? rangeAvg.Value : 0.0;
            if (refRange > 0.0)
            {
                double expansion = (High[0] - Low[0]) / refRange;
                if (expansion < RangeExpansionMultipleMin)
                    return false;
            }

            // Volume multiple.
            double refVol = volumeAvg.Count > 0 ? volumeAvg.Value : 0.0;
            if (refVol > 0.0 && (double)Volume[0] < refVol * VolumeMultipleMin)
                return false;

            // Direction streak (of prior same-direction signals).
            if (directionStreak < DirectionStreakMin)
                return false;

            // VWAP stretch — require a 1-minute VWAP series.  NinjaTrader's
            // built-in VWAP session indicator can be plugged in here.
            try
            {
                double vwap = VWAP()[0];
                double atr  = atrIndicator[0];
                if (atr > 0.0)
                {
                    double stretch = Math.Abs(Close[0] - vwap) / atr;
                    if (stretch < VwapStretchAtrMin)
                        return false;
                }
            }
            catch
            {
                // VWAP() not available on this data series — skip this gate.
            }

            return true;
        }

        private bool EvaluateSessionAndQuiet()
        {
            // Session allow-list.
            if (SessionMode == LcrSessionMode.Allowlist && allowedSessionSet.Count > 0)
            {
                var label = ClassifySession(Time[0]).ToLowerInvariant();
                if (!allowedSessionSet.Contains(label))
                    return false;
            }

            // Quiet filter — count prior signals within QuietLookbackMinutes.
            if (QuietLookbackMinutes > 0 && QuietMaxPriorSignals >= 0)
            {
                var cutoffTime = Time[0].AddMinutes(-QuietLookbackMinutes);
                int count = 0;
                for (int i = priorSignals.Count - 1; i >= 0; i--)
                {
                    if (priorSignals[i].Time < cutoffTime)
                        break;
                    count++;
                    if (count > QuietMaxPriorSignals)
                        return false;
                }
            }
            return true;
        }

        /// <summary>
        /// Simple session classifier — returns the bucket label for a given
        /// bar-close timestamp. These labels are matched against the
        /// AllowSession* checkboxes in Section E. Times are bar close in the
        /// chart's time zone; the caller is responsible for chart time ==
        /// America/New_York.
        ///
        /// FIX #1 — "asia" is now emitted for the Tokyo/Sydney overnight window
        /// (18:00–03:00 ET). Previously this label was absent entirely, and the
        /// exporter wrote AllowedSessionsCsv="asia" into templates which then
        /// silently rejected every bar.
        /// </summary>
        private string ClassifySession(DateTime t)
        {
            int h = t.Hour, m = t.Minute;
            int mm = h * 60 + m;
            // NY eastern-time mapping (the discovery report uses America/New_York).
            if (mm >= 0    && mm < 3 * 60)   return "asia";              // 00:00–03:00 ET
            if (mm < 5 * 60)                 return "early_london";       // 03:00–05:00
            if (mm < 7 * 60)                 return "mid_london";         // 05:00–07:00
            if (mm < 9 * 60)                 return "london_ny_overlap";  // 07:00–09:00
            if (mm < 9 * 60 + 30)            return "ny_pre_open";        // 09:00–09:30
            if (mm < 11 * 60)                return "ny_open";            // 09:30–11:00
            if (mm < 14 * 60)                return "mid_day";            // 11:00–14:00
            if (mm < 16 * 60)                return "power_hour";         // 14:00–16:00
            if (mm < 18 * 60)                return "after_hours";        // 16:00–18:00
            if (mm < 20 * 60)                return "overnight";          // 18:00–20:00 (tail of US after-hours)
            return "asia";                                                 // 20:00–24:00 ET
        }

        // =====================================================================
        // Entry
        // =====================================================================

        private void TryEnter(int signalDir, double sizeTicks)
        {
            int tradeDir = ResolveTradeDirection(signalDir);
            if (tradeDir == 0)
                return;

            ResetTradeState();
            inPosition      = true;
            isLongTrade     = tradeDir > 0;
            entryBar        = CurrentBar;
            entryPrice      = Close[0];
            entryTime       = Time[0];
            signalHigh      = High[0];
            signalLow       = Low[0];
            signalSizeTicks = sizeTicks;
            signalMidpoint  = (signalHigh + signalLow) / 2.0;

            // Stop: signal candle extreme + offset, capped at MaxStopTicks,
            // else atr fallback.
            double stopTicks;
            if (StopStyle == LcrStopStyle.SignalCandleExtremeWithCap)
            {
                double extremeTicks = isLongTrade
                    ? (entryPrice - signalLow) / TickSize + StopBaseOffsetTicks
                    : (signalHigh - entryPrice) / TickSize + StopBaseOffsetTicks;
                stopTicks = Math.Min(extremeTicks, MaxStopTicks);
            }
            else
            {
                double atrTicks = atrIndicator[0] / TickSize * AtrFallbackMultiple;
                stopTicks = Math.Min(atrTicks, MaxStopTicks);
            }
            if (stopTicks < 10)
                stopTicks = Math.Max(10, RecommendedStopTicks);
            stopPrice = isLongTrade
                ? entryPrice - stopTicks * TickSize
                : entryPrice + stopTicks * TickSize;

            // Targets derived from signal candle size.
            double sizePrice   = signalSizeTicks * TickSize;
            double scalpOffset = sizePrice * (ScalpTargetPct / 100.0);
            double expOffset   = sizePrice * (ExpansionTargetPct / 100.0);
            double runOffset   = sizePrice * (RunnerTargetPct / 100.0);
            scalpTargetPrice    = isLongTrade ? entryPrice + scalpOffset : entryPrice - scalpOffset;
            expansionTargetPrice = isLongTrade ? entryPrice + expOffset   : entryPrice - expOffset;
            runnerTargetPrice   = isLongTrade ? entryPrice + runOffset    : entryPrice - runOffset;

            // Store the planned stop offset (in ticks) so OnExecutionUpdate can
            // submit the protective stop based on the actual fill price, not the
            // close-of-signal-bar price.  Per NinjaTrader support guidance, when
            // tracking explicit Exit orders via OnOrderUpdate the recommended
            // pattern is to submit BOTH the protective stop and the eventual
            // target with explicit Exit* methods — mixing SetStopLoss with
            // ExitLong*/ExitShort* on the same position causes a window where
            // both a "Stop loss" order (from Set) and our LcrStop* order are
            // working simultaneously.  See OnExecutionUpdate below.
            plannedStopTicks = stopTicks;

            if (isLongTrade)
                EnterLong(Contracts, SIGNAL_ENTRY_LONG);
            else
                EnterShort(Contracts, SIGNAL_ENTRY_SHORT);

            if (DrawMarkers)
            {
                Draw.ArrowUp(this, $"e{CurrentBar}", false, 0,
                    isLongTrade ? Low[0] - 2 * TickSize : High[0] + 2 * TickSize,
                    isLongTrade ? Brushes.LimeGreen : Brushes.Red);
            }

            if (EnableDebugPrint)
                Print($"[LCR] ENTRY bar={CurrentBar} dir={(isLongTrade ? "L" : "S")} " +
                      $"size={signalSizeTicks:F1}t stop={stopTicks:F1}t scalp={ScalpTargetPct}% " +
                      $"runner={RunnerTargetPct}%");
        }

        /// <summary>
        /// Translate signal direction + DirectionPolicy into the actual trade direction.
        ///
        /// NOTE: The Counter* policies currently return signalDir (trade WITH the
        /// signal candle, which itself is treated as the reversal of a prior
        /// failed move). This matches the internal "continuation after failed
        /// reversal" story but may conflict with the research Strategy Cards'
        /// use of the word "reverse" (which typically means fade). The Python
        /// exporter should be audited to confirm the intended semantics before
        /// changing anything here.
        /// </summary>
        private int ResolveTradeDirection(int signalDir)
        {
            switch (DirectionPolicy)
            {
                case LcrDirectionPolicy.SignalDirection:
                    return signalDir;
                case LcrDirectionPolicy.CounterToFailedContinuation:
                case LcrDirectionPolicy.CounterToDirectionalRun:
                case LcrDirectionPolicy.CounterToLevelRejection:
                    return signalDir;
                case LcrDirectionPolicy.ContinuationOfRangeBreak:
                case LcrDirectionPolicy.ContinuationOfCompression:
                    return signalDir;
                default:
                    return signalDir;
            }
        }

        // =====================================================================
        // Position management
        // =====================================================================

        private void ManagePosition()
        {
            int barsSinceEntry = CurrentBar - entryBar;

            // Time stop.
            double minutesSinceEntry = (Time[0] - entryTime).TotalMinutes;
            if (TimeStopMinutes > 0 && minutesSinceEntry >= TimeStopMinutes)
            {
                ExitMarket("TimeStop");
                return;
            }

            // Hold evaluation at EvaluationBar.
            if (!holdDecisionMade && barsSinceEntry >= EvaluationBar)
            {
                holdDecisionPass = EvaluateHoldRules();
                holdDecisionMade = true;

                if (DrawMarkers)
                {
                    var col = holdDecisionPass ? Brushes.DarkGreen : Brushes.OrangeRed;
                    Draw.Diamond(this, $"h{CurrentBar}", false, 0,
                        isLongTrade ? High[0] + 4 * TickSize : Low[0] - 4 * TickSize, col);
                }

                if (EnableDebugPrint)
                    Print($"[LCR] HOLD eval bar+{barsSinceEntry} pass={holdDecisionPass}");

                if (!holdDecisionPass)
                {
                    // Fail path — switch to scalp target.
                    // Per the Advanced Order Handling guide we DO NOT assign the
                    // returned IOrder here (assignment in OnBarUpdate is unreliable);
                    // OnOrderUpdate matches by signal name and stores the reference.
                    // isLiveUntilCancelled=true (2nd arg) keeps the order working
                    // across bars instead of auto-cancelling at end of bar.
                    if (OnFailAction == LcrOnFailAction.ExitAtScalpTarget)
                    {
                        SubmitTargetLimit(scalpTargetPrice);
                    }
                    else
                    {
                        ExitMarket("FailExit");
                    }
                    return;
                }

                // Pass path — set the appropriate target; init trail if runner.
                if (OnPassAction == LcrOnPassAction.ExitAtScalpTarget)
                {
                    SubmitTargetLimit(scalpTargetPrice);
                }
                else
                {
                    SubmitTargetLimit(runnerTargetPrice);
                    InitTrailStop();
                }
            }

            // Trail management each bar after decision.
            if (holdDecisionMade && holdDecisionPass && TrailStyle != LcrTrailStyle.None)
                UpdateTrailStop();
        }

        private bool EvaluateHoldRules()
        {
            bool midpointOk  = EvaluateMidpointReclaim();
            bool rebreakOk   = EvaluateRebreakNo();
            bool explosiveOk = EvaluateExplosiveStart();
            bool orderlyOk   = EvaluateOrderlyStart();     // FIX #5
            bool favOnlyOk   = EvaluateFav2BarOnly();      // FIX #5
            bool advOnlyOk   = EvaluateAdv2BarOnly();      // FIX #5

            if (HoldRuleMode == LcrHoldRuleMode.PrimaryOnly)
            {
                switch (PrimaryHoldRule)
                {
                    case LcrPrimaryHoldRule.MidpointReclaimYes: return midpointOk;
                    case LcrPrimaryHoldRule.RebreakNo:          return rebreakOk;
                    case LcrPrimaryHoldRule.ExplosiveStart:     return explosiveOk;
                    case LcrPrimaryHoldRule.OrderlyStart:       return orderlyOk;
                    case LcrPrimaryHoldRule.Fav2BarOnly:        return favOnlyOk;
                    case LcrPrimaryHoldRule.Adv2BarOnly:        return advOnlyOk;
                    default:                                    return true;
                }
            }
            // AnyOf mode.
            return midpointOk || rebreakOk || explosiveOk || orderlyOk || favOnlyOk || advOnlyOk;
        }

        // FIX #3 — all early-path evaluators now use `i < lookback` so they
        // iterate ONLY over post-entry bars. The old `i <= lookback` included
        // the signal candle itself, which by construction contained its own
        // midpoint and made midpoint reclaim trivially true.

        private bool EvaluateMidpointReclaim()
        {
            int lookback = Math.Min(MidpointReclaimBars, CurrentBar - entryBar);
            for (int i = 0; i < lookback; i++)
            {
                double hi = High[i];
                double lo = Low[i];
                bool reclaimed = isLongTrade ? hi >= signalMidpoint : lo <= signalMidpoint;
                if (reclaimed)
                    return true;
            }
            return false;
        }

        private bool EvaluateRebreakNo()
        {
            int lookback = Math.Min(RebreakCheckBars, CurrentBar - entryBar);
            for (int i = 0; i < lookback; i++)
            {
                bool broke = isLongTrade ? Low[i] < signalLow : High[i] > signalHigh;
                if (broke)
                    return false;
            }
            return true;
        }

        private void GetFav2BarPct(out double favPct, out double advPct)
        {
            favPct = 0.0;
            advPct = 0.0;
            if (signalSizeTicks <= 0) return;

            double fav = 0.0, adv = 0.0;
            int lookback = Math.Min(2, CurrentBar - entryBar);
            for (int i = 0; i < lookback; i++)
            {
                double hi = High[i], lo = Low[i];
                if (isLongTrade)
                {
                    fav = Math.Max(fav, (hi - entryPrice) / TickSize);
                    adv = Math.Max(adv, (entryPrice - lo) / TickSize);
                }
                else
                {
                    fav = Math.Max(fav, (entryPrice - lo) / TickSize);
                    adv = Math.Max(adv, (hi - entryPrice) / TickSize);
                }
            }
            favPct = 100.0 * fav / signalSizeTicks;
            advPct = 100.0 * adv / signalSizeTicks;
        }

        private bool EvaluateExplosiveStart()
        {
            if (signalSizeTicks <= 0) return false;
            GetFav2BarPct(out double favPct, out double advPct);
            return favPct >= ExplosiveFav2BarPctMin && advPct <= ExplosiveAdv2BarPctMax;
        }

        // FIX #5 — orderly_start: fav >= X AND adv <= Y AND midpoint reclaimed.
        private bool EvaluateOrderlyStart()
        {
            if (signalSizeTicks <= 0) return false;
            GetFav2BarPct(out double favPct, out double advPct);
            if (favPct < OrderlyFav2BarPctMin) return false;
            if (advPct > OrderlyAdv2BarPctMax) return false;
            return EvaluateMidpointReclaim();
        }

        // FIX #5 — fav2bar-only: just the favorable-excursion threshold.
        private bool EvaluateFav2BarOnly()
        {
            if (signalSizeTicks <= 0) return false;
            GetFav2BarPct(out double favPct, out _);
            return favPct >= Fav2BarOnlyPctMin;
        }

        // FIX #5 — adv2bar-only: just the adverse-excursion cap.
        private bool EvaluateAdv2BarOnly()
        {
            if (signalSizeTicks <= 0) return false;
            GetFav2BarPct(out _, out double advPct);
            return advPct <= Adv2BarOnlyPctMax;
        }

        // =====================================================================
        // Trail / target order helpers
        //
        // Per NinjaTrader's Advanced Order Handling guide, when using explicit
        // Exit* methods we:
        //   1. Submit with isLiveUntilCancelled=true so the order persists
        //      across bars (default behaviour cancels at end of bar).
        //   2. DO NOT rely on assigning the IOrder returned in OnBarUpdate
        //      ("not guaranteed to be complete if it is referenced immediately
        //      after submitting").  We assign in OnOrderUpdate by signal name.
        //   3. Update price by either re-submitting with the same signal name
        //      (NT replaces the working order) or via ChangeOrder() once we
        //      hold a valid IOrder reference.  We use the re-submit pattern
        //      because it is simpler and works whether or not the OnOrderUpdate
        //      assignment has propagated yet.
        // =====================================================================

        private void SubmitTargetLimit(double price)
        {
            if (isLongTrade)
                ExitLongLimit(0, true, Contracts, price, SIGNAL_TARGET_LONG, SIGNAL_ENTRY_LONG);
            else
                ExitShortLimit(0, true, Contracts, price, SIGNAL_TARGET_SHORT, SIGNAL_ENTRY_SHORT);
        }

        private void SubmitTrailStop(double price)
        {
            if (isLongTrade)
                ExitLongStopMarket(0, true, Contracts, price, SIGNAL_STOP_LONG, SIGNAL_ENTRY_LONG);
            else
                ExitShortStopMarket(0, true, Contracts, price, SIGNAL_STOP_SHORT, SIGNAL_ENTRY_SHORT);
        }

        private void InitTrailStop()
        {
            if (TrailStyle != LcrTrailStyle.Atr) return;

            double offset = atrIndicator[0] * AtrTrailMultiple;
            double candidate = isLongTrade ? Close[0] - offset : Close[0] + offset;

            // The protective stop (placed in OnExecutionUpdate) is already on
            // the books with signal name SIGNAL_STOP_LONG/SHORT.  Re-submitting
            // with the same signal name modifies it.  Only move it if the ATR
            // trail level is TIGHTER than the protective level — otherwise we
            // would be widening the stop, which violates the "only tighten"
            // contract a runner trail is supposed to honor.
            bool tighter = isLongTrade ? candidate > trailStopPrice : candidate < trailStopPrice;
            if (!tighter) return;

            trailStopPrice = candidate;
            SubmitTrailStop(trailStopPrice);
        }

        private void UpdateTrailStop()
        {
            if (TrailStyle != LcrTrailStyle.Atr) return;

            double offset = atrIndicator[0] * AtrTrailMultiple;
            double candidate = isLongTrade ? Close[0] - offset : Close[0] + offset;

            // Only tighten — never loosen.
            bool tighter = isLongTrade ? candidate > trailStopPrice : candidate < trailStopPrice;
            if (!tighter) return;

            trailStopPrice = candidate;

            // Prefer ChangeOrder when we have a valid live reference; this is
            // the documented way to modify a working order in the managed
            // approach.  Fall back to re-submitting (which NT treats as a
            // replace because the signal name is the same) if the reference
            // hasn't been assigned yet by OnOrderUpdate.
            if (activeStopOrder != null
                && activeStopOrder.OrderState == OrderState.Working)
            {
                // ChangeOrder(order, quantity, limitPrice, stopPrice).
                // For stop-market orders limitPrice is 0.
                ChangeOrder(activeStopOrder, activeStopOrder.Quantity, 0, trailStopPrice);
            }
            else
            {
                SubmitTrailStop(trailStopPrice);
            }
        }

        // =====================================================================
        // Exit helpers + state reset
        // =====================================================================

        private void ExitMarket(string tag)
        {
            if (isLongTrade) ExitLong(tag);
            else             ExitShort(tag);
        }

        private void ResetTradeState()
        {
            inPosition         = false;
            isLongTrade        = false;
            entryBar           = -1;
            entryPrice         = 0.0;
            signalHigh         = 0.0;
            signalLow          = 0.0;
            signalSizeTicks    = 0.0;
            signalMidpoint     = 0.0;
            stopPrice          = 0.0;
            scalpTargetPrice   = 0.0;
            expansionTargetPrice = 0.0;
            runnerTargetPrice  = 0.0;
            trailStopPrice     = 0.0;
            plannedStopTicks   = 0.0;
            holdDecisionMade   = false;
            holdDecisionPass   = false;
            entryTime          = DateTime.MinValue;
            activeStopOrder    = null;
            activeTargetOrder  = null;
        }

        protected override void OnPositionUpdate(Position position, double averagePrice,
            int quantity, MarketPosition marketPosition)
        {
            if (marketPosition == MarketPosition.Flat)
                ResetTradeState();
        }

        // =====================================================================
        // OnOrderUpdate — assigns/clears IOrder references for our exit orders.
        //
        // Per NinjaTrader's Advanced Order Handling guide:
        //   * IOrder assignment from a submission method is more reliable when
        //     done in OnOrderUpdate than in OnBarUpdate.
        //   * When transitioning from historical to live, calls to
        //     ChangeOrder/CancelOrder against a stale historical reference will
        //     DISABLE the strategy. GetRealtimeOrder() updates the reference.
        //   * Null the reference when the order reaches a terminal state
        //     (Cancelled with no fills, Filled, Rejected) so we don't try to
        //     modify a dead order.
        // =====================================================================
        protected override void OnOrderUpdate(Order order, double limitPrice, double stopPrice,
            int quantity, int filled, double averageFillPrice, OrderState orderState,
            DateTime time, ErrorCode error, string comment)
        {
            if (order == null) return;

            // Historical -> live transition: replace any backtest-generated
            // references with their realtime equivalents BEFORE we ever try to
            // modify them.  This is the documented pattern from the Advanced
            // Order Handling guide.  Without this, the first ChangeOrder after
            // going live will disable the strategy.
            if (State == State.Realtime)
            {
                if (activeStopOrder != null && activeStopOrder.IsBacktestOrder)
                    activeStopOrder = GetRealtimeOrder(activeStopOrder);
                if (activeTargetOrder != null && activeTargetOrder.IsBacktestOrder)
                    activeTargetOrder = GetRealtimeOrder(activeTargetOrder);
            }

            // Match incoming order to our tracked references by signal name.
            // We assign on every non-terminal update so the latest reference
            // reflects any internal NT replacement (e.g., re-submission).
            string name = order.Name ?? string.Empty;
            bool isStop   = name == SIGNAL_STOP_LONG   || name == SIGNAL_STOP_SHORT;
            bool isTarget = name == SIGNAL_TARGET_LONG || name == SIGNAL_TARGET_SHORT;

            if (isStop)
            {
                // Assign while the order is still alive.
                if (orderState == OrderState.Accepted
                    || orderState == OrderState.Working
                    || orderState == OrderState.ChangePending
                    || orderState == OrderState.Submitted)
                {
                    activeStopOrder = order;
                }
                // Null out on terminal states so we don't ChangeOrder a dead order.
                if (orderState == OrderState.Cancelled
                    || orderState == OrderState.Filled
                    || orderState == OrderState.Rejected)
                {
                    if (activeStopOrder != null && activeStopOrder == order)
                        activeStopOrder = null;
                }
            }
            else if (isTarget)
            {
                if (orderState == OrderState.Accepted
                    || orderState == OrderState.Working
                    || orderState == OrderState.ChangeSubmitted
                    || orderState == OrderState.Submitted)
                {
                    activeTargetOrder = order;
                }
                if (orderState == OrderState.Cancelled
                    || orderState == OrderState.Filled
                    || orderState == OrderState.Rejected)
                {
                    if (activeTargetOrder != null && activeTargetOrder == order)
                        activeTargetOrder = null;
                }
            }

            if (EnableDebugPrint && (isStop || isTarget) && error != ErrorCode.NoError)
                Print($"[LCR][ORDER-ERR] name={name} state={orderState} err={error} comment={comment}");
        }

        // =====================================================================
        // OnExecutionUpdate — submits the initial protective stop based on the
        // ACTUAL entry fill price.
        //
        // Per NT support guidance, when using explicit Exit* methods to manage
        // stops/targets you should NOT also use SetStopLoss — the two compete
        // and create overlapping working orders.  The idiomatic pattern is:
        //   * Place entry in OnBarUpdate (no Set methods).
        //   * In OnExecutionUpdate, when the entry fills, submit the protective
        //     stop (and optionally a target) via ExitLong*/ExitShort* using
        //     execution.Order.AverageFillPrice as the basis.
        //   * Move/replace the stop later via the same Exit* method (re-submit
        //     with the same signal name) or ChangeOrder().
        // This eliminates the "Stop loss" + "LcrStop*" coexistence window.
        // =====================================================================
        protected override void OnExecutionUpdate(Execution execution, string executionId,
            double price, int quantity, MarketPosition marketPosition, string orderId,
            DateTime time)
        {
            if (execution == null || execution.Order == null) return;

            string name = execution.Order.Name ?? string.Empty;
            bool isEntry = name == SIGNAL_ENTRY_LONG || name == SIGNAL_ENTRY_SHORT;
            if (!isEntry) return;
            if (execution.Order.OrderState != OrderState.Filled
                && execution.Order.OrderState != OrderState.PartFilled) return;
            if (plannedStopTicks <= 0) return;

            double fillPrice = execution.Order.AverageFillPrice;
            // Recompute the actual stop price from the real fill (not the close
            // of the signal bar that was used as a planning estimate).  This
            // also fixes the subtle issue where MarketOnNextOpen entry fills at
            // the next bar's open, not Close[0].
            double actualStopPrice = isLongTrade
                ? fillPrice - plannedStopTicks * TickSize
                : fillPrice + plannedStopTicks * TickSize;
            stopPrice = actualStopPrice;

            // Submit protective stop.  isLiveUntilCancelled=true keeps it alive
            // until we either move it (re-submit / ChangeOrder) or the position
            // closes (NT auto-cancels).
            SubmitTrailStop(actualStopPrice);
            // Initialize trail tracking price so UpdateTrailStop's "only tighten"
            // comparison works on the very first runner bar.
            trailStopPrice = actualStopPrice;

            if (EnableDebugPrint)
                Print($"[LCR] STOP-PLACED fill={fillPrice:F2} stop={actualStopPrice:F2} " +
                      $"ticks={plannedStopTicks:F1}");
        }
    }
}
