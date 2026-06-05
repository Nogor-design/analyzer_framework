// StrategyDiscoveryFilter.cs
// =============================================================================
// Strategy Discovery Condition Backtest Harness
// =============================================================================
// This strategy tests the exact market conditions discovered by the
// ta_foundation Strategy Discovery engine.  Every filter from the report
// is exposed as an optimizable parameter so you can sweep them in the
// NinjaTrader optimizer and confirm which ones actually improve results.
//
// HOW TO USE
// ----------
// 1. Run ta_foundation with your export and read the Strategy Discovery report.
// 2. Note the top entry rules, filter rules, and exit policy from the report.
// 3. Load this strategy and set the matching parameters.
// 4. Backtest.  Then use the NinjaTrader optimizer on the parameter ranges
//    that the report flagged as robust (not fragile).
//
// SECTION MAP (matches report sections)
// --------------------------------------
//  [A] Regime Filter      → MarketRegimeMode, AdxPeriod, AdxThreshold,
//                           VolatilityMode, AtrPeriod, AtrLookback,
//                           VolLowPct, VolHighPct
//  [B] Session Filter     → AllowRTH, AllowONH, AllowETH,
//                           RthStartH, RthStartM, RthEndH, RthEndM
//  [C] Direction Filter   → AllowLong, AllowShort
//  [D] Entry Signal       → EntryEmaPeriod (crossover), RequireEntryConfirmation,
//                           ConfirmAtrMultiple
//  [E] Exit Policy        → ExitPolicy, StopTicks, TargetTicks,
//                           AtrTrailMultiple, ChandelierLookback,
//                           GivebackPct, BreakEvenTriggerTicks, BreakEvenPlusTicks
//  [F] Daily Risk Limits  → MaxDailyLossUsd, MaxDailyProfitUsd, MaxDailyTrades
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
    // Enums
    // =========================================================================

    /// <summary>
    /// Which market regime(s) are allowed to take entries.
    /// Maps directly to the regime labels in the Strategy Discovery report.
    /// </summary>
    public enum SdfMarketRegimeMode
    {
        Any             = 0,   // No regime filter — trade all conditions
        TrendingOnly    = 1,   // trending_up or trending_down
        RangingOnly     = 2,   // ranging_wide or ranging_tight
        TrendingUp      = 3,   // trending_up only  (bullish trend)
        TrendingDown    = 4,   // trending_down only (bearish trend)
        NoHighVol       = 5,   // exclude high_vol_expansion sessions
        LowVolOnly      = 6,   // low_vol_compression only (tight ATR)
        HighVolOnly     = 7,   // high_vol_expansion only
    }

    /// <summary>
    /// Exit policies matching the Exit Policy Sweep section of the report.
    /// </summary>
    public enum SdfExitPolicy
    {
        FixedRR         = 0,   // Fixed stop + fixed target (ticks)
        AtrTrail        = 1,   // ATR-multiple trailing stop
        Chandelier      = 2,   // Highest-high / lowest-low trailing stop
        Giveback        = 3,   // Exit when GivebackPct% of open profit is given back
        FixedStop       = 4,   // Fixed stop only; let session close handle target
        BreakEvenOnly   = 5,   // Move stop to breakeven after trigger, no trail
    }

    /// <summary>
    /// Entry trigger family. Maps to the ta_foundation discovery signal families.
    /// EmaCross is the legacy baseline; the candle values mirror
    /// analysis/entry_strategies/candle/patterns.py PATTERN_REGISTRY keys exactly.
    /// NbarBreakout mirrors the breakout family (close beyond prior N-bar channel).
    ///
    /// IMPORTANT: this enum is PINNED, not swept. NinjaTrader's optimizer cannot
    /// enumerate enum parameters (it NullReferences in Strategy Analyzer). The
    /// recipe seed fixes EntrySignal to one value; it must never appear in
    /// &lt;OptimizationParameters&gt;.
    /// </summary>
    public enum SdfEntrySignal
    {
        EmaCross          = 0,   // legacy: CrossAbove(fastEma, slowEma)
        LargeBody         = 1,   // detect_large_body
        PinBarBullish     = 2,   // detect_pin_bar_bullish  (long only)
        PinBarBearish     = 3,   // detect_pin_bar_bearish  (short only)
        InsideBar         = 4,   // detect_inside_bar
        OutsideBar        = 5,   // detect_outside_bar
        EngulfingBullish  = 6,   // detect_engulfing_bullish (long only)
        EngulfingBearish  = 7,   // detect_engulfing_bearish (short only)
        Doji              = 8,   // detect_doji (both directions)
        CleanBreakoutBar  = 9,   // detect_clean_breakout_bar
        NbarBreakout      = 10,  // breakout family: close beyond prior N-bar high/low
    }

    /// <summary>
    /// Entry timing mode. Mirrors analysis/entry_strategies/candle/signals.py.
    ///   NextOpen     — market order on signal-bar close → fills next bar open.
    ///   BreakExtreme — buy-stop above signal high / sell-stop below signal low.
    ///   BodyMidpoint — limit at (open+close)/2 of the signal bar (retrace entry).
    /// PHASE 2: BreakExtreme/BodyMidpoint fill behaviour must be parity-checked
    /// against the Python outcome simulator before their results are trusted.
    /// </summary>
    public enum SdfTimingMode
    {
        NextOpen     = 0,
        BreakExtreme = 1,
        BodyMidpoint = 2,
    }

    // =========================================================================
    // Rolling helpers (same pattern as PantheonBotV2)
    // =========================================================================

    internal static class SdfRegimeClassifier
    {
        /// <summary>
        /// ADX-based regime: trending vs ranging.
        /// trending_up   = ADX >= threshold AND price > ema
        /// trending_down = ADX >= threshold AND price < ema
        /// ranging       = ADX < threshold
        /// </summary>
        public static string ClassifyAdx(double adx, double close, double ema, double threshold)
        {
            if (adx >= threshold)
                return close >= ema ? "trending_up" : "trending_down";

            return "ranging";
        }

        /// <summary>
        /// ATR percentile regime.
        /// high_vol_expansion    = percentileRank >= highPct
        /// low_vol_compression   = percentileRank <= lowPct
        /// mid_vol               = between
        /// </summary>
        public static string ClassifyVolatility(double percentileRank, double lowPct, double highPct)
        {
            if (percentileRank >= highPct)
                return "high_vol_expansion";
            if (percentileRank <= lowPct)
                return "low_vol_compression";
            return "mid_vol";
        }
    }

    internal class SdfRollingPercentile
    {
        private readonly int cap;
        private readonly Queue<double> q;

        public SdfRollingPercentile(int capacity)
        {
            cap = Math.Max(5, capacity);
            q   = new Queue<double>(cap + 1);
        }

        public int Count => q.Count;

        public void Add(double v)
        {
            q.Enqueue(v);
            while (q.Count > cap)
                q.Dequeue();
        }

        public double GetPercentileRank(double current)
        {
            if (q.Count == 0)
                return 0.5;

            int less = 0, equal = 0;
            foreach (double x in q)
            {
                if (x < current)         less++;
                else if (Math.Abs(x - current) < 1e-10) equal++;
            }
            return (less + 0.5 * equal) / q.Count;
        }
    }

    // =========================================================================
    // Candle feature engine — bit-for-bit parity with
    // analysis/entry_strategies/candle/features.py + patterns.py
    // =========================================================================
    //
    // PARITY CONTRACT (do not "improve" any of this without re-running the
    // parity harness — every constant here matches the Python source):
    //
    //  * body        = |close - open|
    //  * upper_wick  = high - max(close, open)        (clipped at 0)
    //  * lower_wick  = min(close, open) - low         (clipped at 0)
    //  * total_range = high - low                     (clipped at 0)
    //  * is_bullish  = close >= open                  (doji counts as bullish)
    //  * size_ticks  = round(total_range / tick_size, 2)
    //  * body_to_range      = clip(body/range, 0..1); NaN when range == 0
    //  * upper/lower_wick_to_body = wick/body; NaN when body < 1 tick
    //  * body_vs_roll_N / size_vs_roll_N: divide by the rolling mean of the
    //    PRIOR N bars (pandas .rolling(N, min_periods=1).mean().shift(1)) —
    //    the current bar is EXCLUDED from its own average.
    //  * ATR: simple rolling mean of True Range over `atrPeriod`
    //    (min_periods=1), INCLUDING the current bar's TR. This matches
    //    features.py `_compute_atr` which uses pandas .rolling().mean() —
    //    a SIMPLE mean, NOT Wilder's smoothing. DO NOT substitute
    //    NinjaTrader's ATR() indicator here; it is Wilder-smoothed and will
    //    silently diverge from the discovery report.
    //  * rolling extreme (clean breakout / N-bar breakout): max/min of the
    //    PRIOR N highs/lows (high.shift(1).rolling(N).max()).
    //
    // NaN handling mirrors the pandas `.fillna()` calls at each comparison
    // site in patterns.py:
    //  * wick-to-body comparisons use .fillna(0)  → NaN treated as 0.0
    //  * body_to_range comparisons use .fillna(1) → NaN treated as 1.0
    //  * vs-roll / vs-atr comparisons use .fillna(0) → NaN treated as 0.0
    // C# comparisons against double.NaN are always false, so call sites use the
    // Nz0()/Nz1() helpers to reproduce the fillna semantics exactly.

    internal class SdfCandleFeatureEngine
    {
        private readonly double tickSize;
        private readonly double minBodyPrice;   // 1 tick (features.py min_body_ticks=1)
        private readonly int    rollLookback;   // body_vs_roll / size_vs_roll window
        private readonly int    atrPeriod;
        private readonly int    extremeLookback;

        private readonly Queue<double> bodyBuf  = new Queue<double>();
        private readonly Queue<double> rangeBuf = new Queue<double>();
        private readonly Queue<double> trBuf    = new Queue<double>();
        private readonly Queue<double> highBuf  = new Queue<double>();
        private readonly Queue<double> lowBuf   = new Queue<double>();

        private bool   hasPrevInternal = false;
        private double pOpen, pHigh, pLow, pClose, pBody;

        public int Count { get; private set; }

        // Current-bar raw values (valid after Update)
        public double CurOpen, CurHigh, CurLow, CurClose;

        // Current-bar features (valid after Update)
        public bool   HasPrev;
        public double Body, UpperWick, LowerWick, TotalRange;
        public bool   IsBullish;
        public double SizeTicks;
        public double BodyToRange;       // NaN when range == 0
        public double UpperWickToBody;   // NaN when body < 1 tick
        public double LowerWickToBody;
        public double SizeVsRoll;        // NaN when no prior bar
        public double BodyVsRoll;
        public double SizeVsAtr;
        public double RollHigh, RollLow; // prior-N extreme; NaN when no prior bar
        public double PrevOpen, PrevHigh, PrevLow, PrevClose, PrevBody;

        public SdfCandleFeatureEngine(double tickSize, int rollLookback, int atrPeriod, int extremeLookback)
        {
            this.tickSize        = tickSize > 0 ? tickSize : 0.25;
            this.minBodyPrice    = 1.0 * this.tickSize;
            this.rollLookback    = Math.Max(1, rollLookback);
            this.atrPeriod       = Math.Max(1, atrPeriod);
            this.extremeLookback = Math.Max(1, extremeLookback);
        }

        private static double MeanQ(Queue<double> q)
        {
            if (q.Count == 0) return double.NaN;
            double s = 0.0;
            foreach (double v in q) s += v;
            return s / q.Count;
        }

        private static double MaxQ(Queue<double> q)
        {
            if (q.Count == 0) return double.NaN;
            double m = double.NegativeInfinity;
            foreach (double v in q) if (v > m) m = v;
            return m;
        }

        private static double MinQ(Queue<double> q)
        {
            if (q.Count == 0) return double.NaN;
            double m = double.PositiveInfinity;
            foreach (double v in q) if (v < m) m = v;
            return m;
        }

        public void Update(double o, double h, double l, double c)
        {
            CurOpen = o; CurHigh = h; CurLow = l; CurClose = c;

            // ── Anatomy ────────────────────────────────────────────────────
            double body  = Math.Abs(c - o);
            double maxOC = Math.Max(c, o);
            double minOC = Math.Min(c, o);
            double upperWick = Math.Max(0.0, h - maxOC);
            double lowerWick = Math.Max(0.0, minOC - l);
            double range     = Math.Max(0.0, h - l);

            Body       = body;
            UpperWick  = upperWick;
            LowerWick  = lowerWick;
            TotalRange = range;
            IsBullish  = c >= o;
            SizeTicks  = Math.Round(range / tickSize, 2);

            BodyToRange = range > 0.0
                ? Math.Min(1.0, Math.Max(0.0, body / range))
                : double.NaN;

            if (body >= minBodyPrice)
            {
                UpperWickToBody = upperWick / body;
                LowerWickToBody = lowerWick / body;
            }
            else
            {
                UpperWickToBody = double.NaN;
                LowerWickToBody = double.NaN;
            }

            // ── ATR (simple mean of TR, current bar INCLUDED) ───────────────
            // First bar: prev_close is undefined; pandas skips the NaN terms so
            // TR collapses to (high - low). Seeding prevClose = c reproduces
            // that because |h-c| and |l-c| are both <= h-l.
            double prevCloseForTr = hasPrevInternal ? pClose : c;
            double tr = Math.Max(h - l,
                          Math.Max(Math.Abs(h - prevCloseForTr),
                                   Math.Abs(l - prevCloseForTr)));
            trBuf.Enqueue(tr);
            while (trBuf.Count > atrPeriod) trBuf.Dequeue();
            double atr = MeanQ(trBuf);
            SizeVsAtr = (atr > 0.0) ? range / atr : double.NaN;

            // ── Rolling-average comparison (PRIOR bars only — shift(1)) ─────
            // Buffers currently hold only prior bars (we push the current bar
            // at the end of Update), so their mean already excludes bar i.
            double avgBody  = MeanQ(bodyBuf);
            double avgRange = MeanQ(rangeBuf);
            BodyVsRoll = (avgBody  > 0.0) ? body  / avgBody  : double.NaN;
            SizeVsRoll = (avgRange > 0.0) ? range / avgRange : double.NaN;

            // ── Rolling extreme (PRIOR N highs/lows) ────────────────────────
            RollHigh = MaxQ(highBuf);
            RollLow  = MinQ(lowBuf);

            // ── Expose prior bar for 2-bar patterns ─────────────────────────
            HasPrev   = hasPrevInternal;
            PrevOpen  = pOpen;  PrevHigh = pHigh; PrevLow = pLow;
            PrevClose = pClose; PrevBody = pBody;

            // ── Push current bar into rolling state (AFTER feature compute) ──
            bodyBuf.Enqueue(body);   while (bodyBuf.Count  > rollLookback)    bodyBuf.Dequeue();
            rangeBuf.Enqueue(range); while (rangeBuf.Count > rollLookback)    rangeBuf.Dequeue();
            highBuf.Enqueue(h);      while (highBuf.Count  > extremeLookback) highBuf.Dequeue();
            lowBuf.Enqueue(l);       while (lowBuf.Count   > extremeLookback) lowBuf.Dequeue();

            pOpen = o; pHigh = h; pLow = l; pClose = c; pBody = body;
            hasPrevInternal = true;
            Count++;
        }
    }

    // =========================================================================
    // Strategy
    // =========================================================================

    [Gui.CategoryOrder("A: Regime Filter",   1)]
    [Gui.CategoryOrder("B: Session Filter",  2)]
    [Gui.CategoryOrder("C: Direction",       3)]
    [Gui.CategoryOrder("D: Entry Signal",    4)]
    [Gui.CategoryOrder("D2: Entry Pattern",  5)]
    [Gui.CategoryOrder("E: Exit Policy",     6)]
    [Gui.CategoryOrder("F: Daily Risk",      7)]
    [Gui.CategoryOrder("G: Debug",           8)]
    public class StrategyDiscoveryFilter : Strategy
    {
        // =====================================================================
        // Private fields
        // =====================================================================

        // Indicators
        private ADX  adxIndicator;
        private ATR  atrPrimary;
        private EMA  entryEma;
        private EMA  regimeEma;

        // Rolling ATR percentile
        private SdfRollingPercentile atrPercentile;

        // Cached regime
        private string cachedAdxRegime  = "ranging";
        private string cachedVolRegime  = "mid_vol";
        private string cachedSession    = "RTH";

        // Cached ATR
        private double currentAtr = 1.0;

        // Chandelier tracking
        private double highSinceEntry = 0.0;
        private double lowSinceEntry  = double.MaxValue;

        // Giveback / break-even tracking
        private double peakOpenProfit  = 0.0;
        private bool   breakEvenActive = false;

        // Dynamic stop order reference
        private Order dynamicStopOrder = null;

        // Daily risk limits
        private bool   allowTrading         = true;
        private int    dailyTradeCount      = 0;
        private double sessionOpenCumProfit = 0.0;
        private int    sessionOpenTradeCount = 0;

        // Bar-level cache
        private bool   conditionsMet = false;

        // Candle feature engine (parity with candle/features.py + patterns.py)
        private SdfCandleFeatureEngine candleEngine;

        // Pending stop/limit entry tracking (BreakExtreme / BodyMidpoint timing)
        private Order pendingEntryOrder = null;
        private int   pendingEntryBar   = -1;

        // =====================================================================
        // State machine
        // =====================================================================

        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Description = "Strategy Discovery condition backtest harness. " +
                              "Maps every filter from the ta_foundation report to an optimizable parameter.";
                Name        = "StrategyDiscoveryFilter";

                Calculate                               = Calculate.OnBarClose;
                EntriesPerDirection                     = 1;
                EntryHandling                           = EntryHandling.AllEntries;
                IsExitOnSessionCloseStrategy            = true;
                ExitOnSessionCloseSeconds               = 30;
                MaximumBarsLookBack                     = MaximumBarsLookBack.TwoHundredFiftySix;
                BarsRequiredToTrade                     = 50;
                IsInstantiatedOnEachOptimizationIteration = false;
                StartBehavior                           = StartBehavior.WaitUntilFlat;
                StopTargetHandling                      = StopTargetHandling.PerEntryExecution;
                TraceOrders                             = false;

                // ── A: Regime Filter ─────────────────────────────────────────
                RegimeMode              = SdfMarketRegimeMode.TrendingOnly;
                AdxPeriod               = 14;
                AdxThreshold            = 25.0;
                RegimeEmaPeriod         = 50;
                AtrPeriod               = 14;
                AtrLookbackBars         = 100;
                VolLowPercentile        = 0.30;
                VolHighPercentile       = 0.70;

                // ── B: Session Filter ─────────────────────────────────────────
                AllowRTH                = true;
                AllowONH                = false;
                AllowETH                = false;
                // RTH = 8:30 AM – 3:00 PM CT (Central Time) for NQ/ES futures
                RthStartH               = 8;
                RthStartM               = 30;
                RthEndH                 = 15;
                RthEndM                 = 0;
                // ETH = sessions outside RTH but not overnight gap
                EthStartH               = 7;
                EthStartM               = 0;
                EthEndH                 = 8;
                EthEndM                 = 29;

                // ── C: Direction Filter ───────────────────────────────────────
                AllowLong               = true;
                AllowShort              = true;
                // Trend-aligned: only take longs when regime is trending_up etc.
                UseTrendAlignment       = true;

                // ── D: Entry Signal ───────────────────────────────────────────
                EntryEmaPeriod          = 20;
                SlowEmaPeriod           = 50;
                // Require close to be on the correct side of the slow EMA
                RequireEmaConfirmation  = true;
                // Extra: require ATR > this multiple of its own average (avoid dead markets)
                RequireMinAtrMultiple   = 0.0;   // 0 = disabled; try 0.8

                // ── D2: Entry Pattern (parity with candle discovery) ──────────
                EntrySignal             = SdfEntrySignal.EmaCross;
                TimingMode              = SdfTimingMode.NextOpen;
                BufferTicks             = 1.0;   // break_extreme stop buffer
                FillTimeoutBars         = 3;     // bars to wait for a stop/limit fill
                // Rolling/comparison windows (candle/features.py defaults)
                BodyRollLookback        = 20;    // body_vs_roll_20 / size_vs_roll_20
                ExtremeLookback         = 20;    // clean breakout + N-bar breakout
                // Pattern thresholds (patterns.py per-detector defaults)
                BodyMultiplier          = 1.5;   // large_body body_multiplier
                WickToBodyMax           = 0.5;   // large_body wick_to_body_max
                PinWickToBodyMin        = 1.5;   // pin bar wick_to_body_min
                PinOppWickToBodyMax     = 0.5;   // pin bar opposite-wick max
                PinBodyToRangeMax       = 0.35;  // pin bar body_to_range_max
                EngulfRatio             = 1.0;   // engulfing engulf_ratio
                DojiBodyToRangeMax      = 0.15;  // doji body_to_range_max
                CleanAtrMult            = 1.5;   // clean breakout size_vs_atr >=
                CleanBodyToRangeMin     = 0.60;  // clean breakout body_to_range >=
                EntryMinSizeTicks       = 4;     // min_size_ticks
                EntryMaxSizeTicks       = 200;   // max_size_ticks

                // ── E: Exit Policy ────────────────────────────────────────────
                ExitPolicy              = SdfExitPolicy.FixedRR;
                StopTicks               = 60;
                TargetTicks             = 90;    // default 1.5:1
                AtrTrailMultiple        = 2.0;
                ChandelierLookback      = 20;
                GivebackPct             = 0.40;  // exit when 40% of peak profit given back
                BreakEvenTriggerTicks   = 30;
                BreakEvenPlusTicks      = 4;

                // ── F: Daily Risk ─────────────────────────────────────────────
                MaxDailyLossUsd         = 500.0;
                MaxDailyProfitUsd       = 2000.0;
                MaxDailyTrades          = 6;

                // ── G: Debug ──────────────────────────────────────────────────
                EnableDebugPrint        = false;
                Contracts               = 1;
            }
            else if (State == State.DataLoaded)
            {
                adxIndicator  = ADX(AdxPeriod);
                atrPrimary    = ATR(AtrPeriod);
                entryEma      = EMA(EntryEmaPeriod);
                regimeEma     = EMA(RegimeEmaPeriod);
                atrPercentile = new SdfRollingPercentile(AtrLookbackBars);

                // Candle feature engine. ATR period 14 matches features.py
                // DEFAULT_CONFIG["atr_period"] (the discovery feature ATR, which
                // is independent of the regime-filter ATR above).
                candleEngine  = new SdfCandleFeatureEngine(
                    TickSize, BodyRollLookback, 14, ExtremeLookback);
            }
            else if (State == State.Realtime)
            {
                // Flatten any open position from historical replay on realtime start
                ExitLong();
                ExitShort();

                sessionOpenCumProfit  = SystemPerformance.AllTrades.TradesPerformance.Currency.CumProfit;
                sessionOpenTradeCount = SystemPerformance.AllTrades.Count;
            }
        }

        // =====================================================================
        // OnBarUpdate — main logic
        // =====================================================================

        protected override void OnBarUpdate()
        {
            // ── Feed the candle feature engine on EVERY bar ───────────────────
            // Parity: features.py computes over the full bar series, so the
            // rolling windows must see the warmup bars too. Must run BEFORE the
            // BarsRequiredToTrade gate or the first ~lookback signals will not
            // match the discovery report.
            candleEngine.Update(Open[0], High[0], Low[0], Close[0]);

            if (CurrentBar < BarsRequiredToTrade)
                return;

            // ── Cancel a stale pending stop/limit entry (timing modes) ────────
            if (pendingEntryOrder != null)
            {
                bool working = pendingEntryOrder.OrderState == OrderState.Working
                            || pendingEntryOrder.OrderState == OrderState.Accepted
                            || pendingEntryOrder.OrderState == OrderState.Submitted;
                if (Position.MarketPosition != MarketPosition.Flat || !working)
                {
                    pendingEntryOrder = null;
                }
                else if (CurrentBar - pendingEntryBar >= FillTimeoutBars)
                {
                    CancelOrder(pendingEntryOrder);
                    pendingEntryOrder = null;
                }
            }

            // ── Signal-level parity probe ─────────────────────────────────────
            // Logs every bar the RAW candle pattern fires, BEFORE any regime/
            // session/direction/position gating. This is what the Python parity
            // harness (scripts/parity_signal_export.py) emits, so the two lists
            // can be diffed bar-for-bar to prove the detector math matches.
            // Independent of execution — does not place orders.
            if (EnableDebugPrint && EntrySignal != SdfEntrySignal.EmaCross)
            {
                bool rawLong, rawShort;
                ComputeCandleSignal(out rawLong, out rawShort);
                if (rawLong || rawShort)
                {
                    int dir = rawLong && rawShort ? 0 : (rawLong ? 1 : -1);
                    Print($"[SDF-SIGNAL] {Time[0]:yyyy-MM-ddTHH:mm:ss} dir={dir} " +
                          $"sig={EntrySignal} bar={CurrentBar}");
                }
            }

            // ── Feed rolling ATR percentile ───────────────────────────────────
            if (atrPrimary[0] > 0)
                atrPercentile.Add(atrPrimary[0]);

            currentAtr = atrPrimary[0] > 0 ? atrPrimary[0] : currentAtr;

            // ── Classify current bar ──────────────────────────────────────────
            cachedAdxRegime = SdfRegimeClassifier.ClassifyAdx(
                adxIndicator[0], Close[0], regimeEma[0], AdxThreshold);

            double pctRank = atrPercentile.Count >= 5
                ? atrPercentile.GetPercentileRank(atrPrimary[0])
                : 0.5;

            cachedVolRegime = SdfRegimeClassifier.ClassifyVolatility(
                pctRank, VolLowPercentile, VolHighPercentile);

            cachedSession = ClassifySession(Time[0]);

            // ── Daily reset on new session bar ────────────────────────────────
            if (Bars.IsFirstBarOfSession)
            {
                allowTrading          = true;
                dailyTradeCount       = 0;
                sessionOpenCumProfit  = SystemPerformance.AllTrades.TradesPerformance.Currency.CumProfit;
                sessionOpenTradeCount = SystemPerformance.AllTrades.Count;
                breakEvenActive       = false;

                if (EnableDebugPrint)
                    Print($"[SDF] New session. Regime={cachedAdxRegime} Vol={cachedVolRegime}");
            }

            // ── Daily risk limits ─────────────────────────────────────────────
            if (allowTrading)
                allowTrading = CheckDailyLimits();

            if (!allowTrading)
            {
                ExitLong("DailyLimit");
                ExitShort("DailyLimit");
                return;
            }

            // ── Dynamic exit management (runs every bar when in position) ─────
            ManageDynamicExits();

            // ── Entry gate: check all filters ────────────────────────────────
            conditionsMet = CheckAllFilters();

            if (!conditionsMet)
                return;

            // ── Entry signal: dispatch on EntrySignal ─────────────────────────
            bool longSignal, shortSignal;
            ComputeEntrySignals(out longSignal, out shortSignal);

            // Trend-alignment: only take signals in the direction of regime
            if (UseTrendAlignment)
            {
                if (cachedAdxRegime == "trending_up")
                    shortSignal = false;    // in uptrend, no shorts
                else if (cachedAdxRegime == "trending_down")
                    longSignal = false;     // in downtrend, no longs
            }

            // Direction filter
            if (!AllowLong)  longSignal  = false;
            if (!AllowShort) shortSignal = false;

            // ── Place entries ─────────────────────────────────────────────────
            // One attempt at a time: must be flat AND have no working stop/limit
            // entry from a prior BreakExtreme / BodyMidpoint submission.
            if (Position.MarketPosition != MarketPosition.Flat || pendingEntryOrder != null)
                return;

            if (longSignal)
                PlaceEntry(isLong: true);
            else if (shortSignal)
                PlaceEntry(isLong: false);
        }

        // =====================================================================
        // Entry-signal dispatch (parity with candle/patterns.py)
        // =====================================================================

        // fillna(0): a NaN feature compares as 0.0 (matches pandas .fillna(0)).
        private static double Nz0(double v) => double.IsNaN(v) ? 0.0 : v;
        // fillna(1): a NaN body_to_range compares as 1.0 (matches .fillna(1)).
        private static double Nz1(double v) => double.IsNaN(v) ? 1.0 : v;

        private void ComputeEntrySignals(out bool longSignal, out bool shortSignal)
        {
            longSignal  = false;
            shortSignal = false;

            if (EntrySignal == SdfEntrySignal.EmaCross)
            {
                // Legacy baseline entry.
                longSignal  = CrossAbove(entryEma, regimeEma, 1);
                shortSignal = CrossBelow(entryEma, regimeEma, 1);
                return;
            }

            ComputeCandleSignal(out longSignal, out shortSignal);
        }

        /// <summary>
        /// Reproduces the per-pattern boolean masks from candle/patterns.py.
        /// Emits the pattern's *inherent* direction(s); direction filtering
        /// (AllowLong/Short, trend alignment) is applied by the caller, exactly
        /// as the discovery applies its direction param downstream.
        /// </summary>
        private void ComputeCandleSignal(out bool longSignal, out bool shortSignal)
        {
            longSignal  = false;
            shortSignal = false;

            var e = candleEngine;
            bool inBounds = e.SizeTicks >= EntryMinSizeTicks
                         && e.SizeTicks <= EntryMaxSizeTicks;
            // Patterns that only gate on a lower size floor (no upper cap).
            bool minSizeOk = e.SizeTicks >= EntryMinSizeTicks;

            switch (EntrySignal)
            {
                case SdfEntrySignal.LargeBody:
                {
                    bool largeBody  = Nz0(e.BodyVsRoll) >= BodyMultiplier;
                    bool smallWicks = Nz0(e.UpperWickToBody) <= WickToBodyMax
                                   && Nz0(e.LowerWickToBody) <= WickToBodyMax;
                    bool baseMask   = largeBody && smallWicks && inBounds;
                    longSignal  = baseMask &&  e.IsBullish;
                    shortSignal = baseMask && !e.IsBullish;
                    break;
                }

                case SdfEntrySignal.PinBarBullish:
                {
                    longSignal =
                        Nz0(e.LowerWickToBody) >= PinWickToBodyMin &&
                        Nz0(e.UpperWickToBody) <= PinOppWickToBodyMax &&
                        Nz1(e.BodyToRange)     <= PinBodyToRangeMax &&
                        inBounds;
                    break;
                }

                case SdfEntrySignal.PinBarBearish:
                {
                    shortSignal =
                        Nz0(e.UpperWickToBody) >= PinWickToBodyMin &&
                        Nz0(e.LowerWickToBody) <= PinOppWickToBodyMax &&
                        Nz1(e.BodyToRange)     <= PinBodyToRangeMax &&
                        inBounds;
                    break;
                }

                case SdfEntrySignal.InsideBar:
                {
                    // patterns.py uses a min_size floor of 2 ticks for inside
                    // bars, but here EntryMinSizeTicks is the single knob; the
                    // generator pins it to the discovered value.
                    bool inside = e.HasPrev
                               && e.CurHigh < e.PrevHigh
                               && e.CurLow  > e.PrevLow
                               && minSizeOk;
                    longSignal  = inside &&  e.IsBullish;
                    shortSignal = inside && !e.IsBullish;
                    break;
                }

                case SdfEntrySignal.OutsideBar:
                {
                    bool outside = e.HasPrev
                                && e.CurHigh > e.PrevHigh
                                && e.CurLow  < e.PrevLow
                                && minSizeOk;
                    longSignal  = outside &&  e.IsBullish;
                    shortSignal = outside && !e.IsBullish;
                    break;
                }

                case SdfEntrySignal.EngulfingBullish:
                {
                    double pb = e.HasPrev ? e.PrevBody : 0.0;
                    longSignal = e.IsBullish
                              && e.HasPrev
                              && e.CurOpen  <= e.PrevClose
                              && e.CurClose >= e.PrevOpen
                              && e.Body     >= pb * EngulfRatio
                              && minSizeOk;
                    break;
                }

                case SdfEntrySignal.EngulfingBearish:
                {
                    double pb = e.HasPrev ? e.PrevBody : 0.0;
                    shortSignal = !e.IsBullish
                               && e.HasPrev
                               && e.CurOpen  >= e.PrevClose
                               && e.CurClose <= e.PrevOpen
                               && e.Body     >= pb * EngulfRatio
                               && minSizeOk;
                    break;
                }

                case SdfEntrySignal.Doji:
                {
                    bool doji = Nz1(e.BodyToRange) <= DojiBodyToRangeMax && minSizeOk;
                    // Discovery emits both sides for a doji; direction filter decides.
                    longSignal  = doji;
                    shortSignal = doji;
                    break;
                }

                case SdfEntrySignal.CleanBreakoutBar:
                {
                    bool large = Nz0(e.SizeVsAtr)   >= CleanAtrMult;
                    bool clean = Nz0(e.BodyToRange) >= CleanBodyToRangeMin;
                    bool baseMask = large && clean && minSizeOk;
                    // C# comparisons vs NaN RollHigh/RollLow are false → no
                    // signal until the extreme window has a prior bar, matching
                    // pandas (high >= NaN → False).
                    longSignal  = baseMask &&  e.IsBullish && e.CurHigh >= e.RollHigh;
                    shortSignal = baseMask && !e.IsBullish && e.CurLow  <= e.RollLow;
                    break;
                }

                case SdfEntrySignal.NbarBreakout:
                {
                    // Breakout family: close beyond the prior N-bar channel.
                    longSignal  = e.HasPrev && e.CurClose > e.RollHigh;
                    shortSignal = e.HasPrev && e.CurClose < e.RollLow;
                    break;
                }
            }
        }

        // =====================================================================
        // Entry placement — timing modes mirror candle/signals.py emitters
        // =====================================================================

        private void PlaceEntry(bool isLong)
        {
            string signalName = isLong ? "Long" : "Short";
            ResetEntryTracking(isLong);

            switch (TimingMode)
            {
                case SdfTimingMode.NextOpen:
                    // Market on bar close → fills at the next bar's open.
                    if (isLong) EnterLong(Contracts, signalName);
                    else        EnterShort(Contracts, signalName);
                    ApplyFixedExits(signalName);
                    LogEntry(signalName);
                    break;

                case SdfTimingMode.BreakExtreme:
                {
                    // Stop-market just beyond the signal bar's extreme.
                    double buf = BufferTicks * TickSize;
                    double trigger = isLong
                        ? candleEngine.CurHigh + buf
                        : candleEngine.CurLow  - buf;
                    pendingEntryOrder = isLong
                        ? EnterLongStopMarket(0, true, Contracts, trigger, signalName)
                        : EnterShortStopMarket(0, true, Contracts, trigger, signalName);
                    pendingEntryBar = CurrentBar;
                    ApplyFixedExits(signalName);
                    LogEntry(signalName);
                    break;
                }

                case SdfTimingMode.BodyMidpoint:
                {
                    // Limit at the midpoint of the signal bar's body (retrace).
                    double mid = (candleEngine.CurOpen + candleEngine.CurClose) / 2.0;
                    pendingEntryOrder = isLong
                        ? EnterLongLimit(0, true, Contracts, mid, signalName)
                        : EnterShortLimit(0, true, Contracts, mid, signalName);
                    pendingEntryBar = CurrentBar;
                    ApplyFixedExits(signalName);
                    LogEntry(signalName);
                    break;
                }
            }
        }

        /// <summary>
        /// Attach fixed stop/target for FixedRR / FixedStop policies. For named
        /// entry signals NinjaTrader applies these on fill, so it is safe to set
        /// them alongside a stop/limit entry submission.
        /// </summary>
        private void ApplyFixedExits(string signalName)
        {
            if (ExitPolicy == SdfExitPolicy.FixedRR || ExitPolicy == SdfExitPolicy.FixedStop)
                SetStopLoss(signalName, CalculationMode.Ticks, StopTicks, false);

            if (ExitPolicy == SdfExitPolicy.FixedRR)
                SetProfitTarget(signalName, CalculationMode.Ticks, TargetTicks);
        }

        // =====================================================================
        // Filter evaluation — mirrors Strategy Discovery condition logic
        // =====================================================================

        private bool CheckAllFilters()
        {
            // [A] Regime filter
            if (!RegimeAllowed())
                return false;

            // [B] Session filter
            if (!SessionAllowed())
                return false;

            // [D] Min ATR filter (avoid dead / very tight markets)
            if (RequireMinAtrMultiple > 0.0 && atrPercentile.Count >= 5)
            {
                // compute average ATR from rolling window (approximate via percentile rank)
                // simple approach: require current ATR is above a minimum fraction of its own historical mean
                // we use percentile rank: percentileRank < RequireMinAtrMultiple means ATR is below typical
                double pctRank = atrPercentile.GetPercentileRank(atrPrimary[0]);
                if (pctRank < RequireMinAtrMultiple)
                    return false;
            }

            return true;
        }

        // ── Regime filter (Section A of report) ──────────────────────────────

        private bool RegimeAllowed()
        {
            switch (RegimeMode)
            {
                case SdfMarketRegimeMode.Any:
                    return true;

                case SdfMarketRegimeMode.TrendingOnly:
                    return cachedAdxRegime == "trending_up" || cachedAdxRegime == "trending_down";

                case SdfMarketRegimeMode.RangingOnly:
                    return cachedAdxRegime == "ranging";

                case SdfMarketRegimeMode.TrendingUp:
                    return cachedAdxRegime == "trending_up";

                case SdfMarketRegimeMode.TrendingDown:
                    return cachedAdxRegime == "trending_down";

                case SdfMarketRegimeMode.NoHighVol:
                    // Allow all regimes EXCEPT high volatility expansion
                    return cachedVolRegime != "high_vol_expansion";

                case SdfMarketRegimeMode.LowVolOnly:
                    return cachedVolRegime == "low_vol_compression";

                case SdfMarketRegimeMode.HighVolOnly:
                    return cachedVolRegime == "high_vol_expansion";

                default:
                    return true;
            }
        }

        // ── Session filter (Section B of report) ─────────────────────────────

        private string ClassifySession(DateTime barTime)
        {
            // Times assumed to be exchange local time (CT for CME futures).
            // NinjaTrader bar Time[] is already in the account timezone if
            // configured correctly.  Adjust offsets if you use a different TZ.
            int t        = ToTime(barTime);
            int rthStart = ToTime(RthStartH, RthStartM, 0);
            int rthEnd   = ToTime(RthEndH, RthEndM, 0);
            int ethStart = ToTime(EthStartH, EthStartM, 0);
            int ethEnd   = ToTime(EthEndH, EthEndM, 59);

            if (t >= rthStart && t <= rthEnd)
                return "RTH";

            if (t >= ethStart && t < rthStart)
                return "ETH";

            return "ONH";   // everything else is overnight
        }

        private bool SessionAllowed()
        {
            if (cachedSession == "RTH" && AllowRTH)  return true;
            if (cachedSession == "ONH" && AllowONH)  return true;
            if (cachedSession == "ETH" && AllowETH)  return true;
            return false;
        }

        // ── Require EMA confirmation (optional entry tightening) ──────────────

        private bool EmaConfirmationPassed(bool isLong)
        {
            if (!RequireEmaConfirmation)
                return true;

            // Long: price must be above the slow EMA (trend-aligned entry)
            // Short: price must be below the slow EMA
            return isLong
                ? Close[0] > regimeEma[0]
                : Close[0] < regimeEma[0];
        }

        // =====================================================================
        // Dynamic exit management — runs every bar while in a position
        // Mirrors Exit Policy Sweep from the report
        // =====================================================================

        private void ManageDynamicExits()
        {
            if (Position.MarketPosition == MarketPosition.Flat)
                return;

            bool isLong = Position.MarketPosition == MarketPosition.Long;

            // Track high/low since entry for Chandelier
            if (isLong)
                highSinceEntry = Math.Max(highSinceEntry, High[0]);
            else
                lowSinceEntry  = Math.Min(lowSinceEntry, Low[0]);

            // Track peak open profit for Giveback
            double openProfit = Position.GetUnrealizedProfitLoss(PerformanceUnit.Currency, Close[0]);
            peakOpenProfit = Math.Max(peakOpenProfit, openProfit);

            switch (ExitPolicy)
            {
                case SdfExitPolicy.AtrTrail:
                    ManageAtrTrail(isLong);
                    break;

                case SdfExitPolicy.Chandelier:
                    ManageChandelier(isLong);
                    break;

                case SdfExitPolicy.Giveback:
                    ManageGiveback(isLong, openProfit);
                    break;

                case SdfExitPolicy.BreakEvenOnly:
                    ManageBreakEven(isLong, openProfit);
                    break;

                // FixedRR and FixedStop are managed by SetStopLoss / SetProfitTarget
                // called at entry time — nothing to do here.
            }
        }

        // ATR trail: stop trails at entryPrice ± (AtrTrailMultiple × ATR)
        private void ManageAtrTrail(bool isLong)
        {
            double trailDist = AtrTrailMultiple * currentAtr;
            if (isLong)
            {
                double stopPrice = High[0] - trailDist;
                if (Close[0] <= stopPrice)
                {
                    ExitLong("AtrTrail");
                    LogExit("Long", "ATR trail hit");
                }
            }
            else
            {
                double stopPrice = Low[0] + trailDist;
                if (Close[0] >= stopPrice)
                {
                    ExitShort("AtrTrail");
                    LogExit("Short", "ATR trail hit");
                }
            }
        }

        // Chandelier: stop at highest-high (for longs) or lowest-low (for shorts)
        // over ChandelierLookback bars, minus/plus ATR multiple
        private void ManageChandelier(bool isLong)
        {
            if (CurrentBar < ChandelierLookback)
                return;

            double ref_price  = isLong
                ? MAX(High, ChandelierLookback)[0]
                : MIN(Low,  ChandelierLookback)[0];
            double stopPrice  = isLong
                ? ref_price - AtrTrailMultiple * currentAtr
                : ref_price + AtrTrailMultiple * currentAtr;

            if (isLong && Close[0] <= stopPrice)
            {
                ExitLong("Chandelier");
                LogExit("Long", $"Chandelier {stopPrice:F2}");
            }
            else if (!isLong && Close[0] >= stopPrice)
            {
                ExitShort("Chandelier");
                LogExit("Short", $"Chandelier {stopPrice:F2}");
            }
        }

        // Giveback: exit when we give back GivebackPct% of peak open profit
        private void ManageGiveback(bool isLong, double openProfit)
        {
            if (peakOpenProfit <= 0)
                return;   // never been in profit — don't exit on giveback

            double givebackThreshold = peakOpenProfit * (1.0 - GivebackPct);
            if (openProfit < givebackThreshold)
            {
                if (isLong)
                {
                    ExitLong("Giveback");
                    LogExit("Long", $"giveback peak={peakOpenProfit:F0} now={openProfit:F0}");
                }
                else
                {
                    ExitShort("Giveback");
                    LogExit("Short", $"giveback peak={peakOpenProfit:F0} now={openProfit:F0}");
                }
            }
        }

        // Break-even: after BreakEvenTriggerTicks of profit, move stop to entry + BreakEvenPlusTicks
        private void ManageBreakEven(bool isLong, double openProfit)
        {
            if (breakEvenActive)
                return;

            double triggerValueUsd = BreakEvenTriggerTicks * Instrument.MasterInstrument.TickSize
                                     * Instrument.MasterInstrument.PointValue;

            if (openProfit >= triggerValueUsd)
            {
                breakEvenActive = true;
                double stopOffset = BreakEvenPlusTicks * TickSize;

                if (isLong)
                {
                    double newStop = Position.AveragePrice + stopOffset;
                    SetStopLoss("Long", CalculationMode.Price, newStop, false);
                    LogExit("Long", $"break-even moved to {newStop:F2}");
                }
                else
                {
                    double newStop = Position.AveragePrice - stopOffset;
                    SetStopLoss("Short", CalculationMode.Price, newStop, false);
                    LogExit("Short", $"break-even moved to {newStop:F2}");
                }
            }
        }

        // =====================================================================
        // Daily risk limits (Section F)
        // =====================================================================

        private bool CheckDailyLimits()
        {
            double sessionPnl   = SystemPerformance.AllTrades.TradesPerformance.Currency.CumProfit
                                  - sessionOpenCumProfit;
            int    sessionTrades = SystemPerformance.AllTrades.Count - sessionOpenTradeCount;

            if (sessionPnl <= -MaxDailyLossUsd)
            {
                if (EnableDebugPrint)
                    Print($"[SDF] Daily loss limit hit: {sessionPnl:F0} <= -{MaxDailyLossUsd}");
                return false;
            }

            if (sessionPnl >= MaxDailyProfitUsd)
            {
                if (EnableDebugPrint)
                    Print($"[SDF] Daily profit target hit: {sessionPnl:F0} >= {MaxDailyProfitUsd}");
                return false;
            }

            if (sessionTrades >= MaxDailyTrades)
            {
                if (EnableDebugPrint)
                    Print($"[SDF] Max daily trades reached: {sessionTrades}");
                return false;
            }

            return true;
        }

        // =====================================================================
        // Helpers
        // =====================================================================

        private void ResetEntryTracking(bool isLong)
        {
            highSinceEntry  = isLong ? Close[0] : double.MinValue;
            lowSinceEntry   = isLong ? double.MaxValue : Close[0];
            peakOpenProfit  = 0.0;
            breakEvenActive = false;
        }

        private void LogEntry(string direction)
        {
            if (!EnableDebugPrint)
                return;

            Print($"[SDF] ENTRY {direction} | bar={CurrentBar} " +
                  $"regime={cachedAdxRegime} vol={cachedVolRegime} session={cachedSession} " +
                  $"ADX={adxIndicator[0]:F1} ATR={currentAtr:F2} " +
                  $"EMA(fast)={entryEma[0]:F2} EMA(slow)={regimeEma[0]:F2} " +
                  $"stop={StopTicks}t target={TargetTicks}t exit={ExitPolicy}");
        }

        private void LogExit(string direction, string reason)
        {
            if (!EnableDebugPrint)
                return;

            Print($"[SDF] EXIT {direction} | {reason} | bar={CurrentBar}");
        }

        // =====================================================================
        // Parameters
        // =====================================================================

        #region A: Regime Filter

        [NinjaScriptProperty]
        [Display(Name = "Regime Mode",
                 Description = "Which market regime(s) to trade. Matches 'regime_summary' labels in the report.",
                 GroupName = "A: Regime Filter", Order = 1)]
        public SdfMarketRegimeMode RegimeMode { get; set; }

        [NinjaScriptProperty]
        [Range(5, 50)]
        [Display(Name = "ADX Period",
                 Description = "ADX lookback. Report default: 14.",
                 GroupName = "A: Regime Filter", Order = 2)]
        public int AdxPeriod { get; set; }

        [NinjaScriptProperty]
        [Range(10.0, 50.0)]
        [Display(Name = "ADX Threshold",
                 Description = "ADX >= this value = trending. Report entry rules typically use 20–30.",
                 GroupName = "A: Regime Filter", Order = 3)]
        public double AdxThreshold { get; set; }

        [NinjaScriptProperty]
        [Range(10, 500)]
        [Display(Name = "Regime EMA Period",
                 Description = "EMA used for trending_up/down direction check.",
                 GroupName = "A: Regime Filter", Order = 4)]
        public int RegimeEmaPeriod { get; set; }

        [NinjaScriptProperty]
        [Range(5, 50)]
        [Display(Name = "ATR Period",
                 Description = "ATR period for volatility regime classification.",
                 GroupName = "A: Regime Filter", Order = 5)]
        public int AtrPeriod { get; set; }

        [NinjaScriptProperty]
        [Range(20, 500)]
        [Display(Name = "ATR Lookback Bars",
                 Description = "Rolling window for ATR percentile rank. Report default: 100.",
                 GroupName = "A: Regime Filter", Order = 6)]
        public int AtrLookbackBars { get; set; }

        [NinjaScriptProperty]
        [Range(0.05, 0.49)]
        [Display(Name = "Low Vol Percentile",
                 Description = "ATR percentile <= this = low_vol_compression. Report default: 0.30.",
                 GroupName = "A: Regime Filter", Order = 7)]
        public double VolLowPercentile { get; set; }

        [NinjaScriptProperty]
        [Range(0.51, 0.95)]
        [Display(Name = "High Vol Percentile",
                 Description = "ATR percentile >= this = high_vol_expansion. Report default: 0.70.",
                 GroupName = "A: Regime Filter", Order = 8)]
        public double VolHighPercentile { get; set; }

        #endregion

        #region B: Session Filter

        [NinjaScriptProperty]
        [Display(Name = "Allow RTH",
                 Description = "Trade Regular Trading Hours (8:30–15:00 CT). Report: most strategies work here.",
                 GroupName = "B: Session Filter", Order = 1)]
        public bool AllowRTH { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Allow ONH",
                 Description = "Trade Overnight (outside RTH and ETH). Report: usually lower edge, higher volatility.",
                 GroupName = "B: Session Filter", Order = 2)]
        public bool AllowONH { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Allow ETH",
                 Description = "Trade Extended Hours (pre-market). Check report session breakdown first.",
                 GroupName = "B: Session Filter", Order = 3)]
        public bool AllowETH { get; set; }

        [NinjaScriptProperty]
        [Range(0, 23)]
        [Display(Name = "RTH Start Hour", GroupName = "B: Session Filter", Order = 4)]
        public int RthStartH { get; set; }

        [NinjaScriptProperty]
        [Range(0, 59)]
        [Display(Name = "RTH Start Min", GroupName = "B: Session Filter", Order = 5)]
        public int RthStartM { get; set; }

        [NinjaScriptProperty]
        [Range(0, 23)]
        [Display(Name = "RTH End Hour", GroupName = "B: Session Filter", Order = 6)]
        public int RthEndH { get; set; }

        [NinjaScriptProperty]
        [Range(0, 59)]
        [Display(Name = "RTH End Min", GroupName = "B: Session Filter", Order = 7)]
        public int RthEndM { get; set; }

        [NinjaScriptProperty]
        [Range(0, 23)]
        [Display(Name = "ETH Start Hour", GroupName = "B: Session Filter", Order = 8)]
        public int EthStartH { get; set; }

        [NinjaScriptProperty]
        [Range(0, 59)]
        [Display(Name = "ETH Start Min", GroupName = "B: Session Filter", Order = 9)]
        public int EthStartM { get; set; }

        [NinjaScriptProperty]
        [Range(0, 23)]
        [Display(Name = "ETH End Hour", GroupName = "B: Session Filter", Order = 10)]
        public int EthEndH { get; set; }

        [NinjaScriptProperty]
        [Range(0, 59)]
        [Display(Name = "ETH End Min", GroupName = "B: Session Filter", Order = 11)]
        public int EthEndM { get; set; }

        #endregion

        #region C: Direction

        [NinjaScriptProperty]
        [Display(Name = "Allow Long",
                 Description = "Enable long entries. Disable if report shows Long P&L is negative.",
                 GroupName = "C: Direction", Order = 1)]
        public bool AllowLong { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Allow Short",
                 Description = "Enable short entries. Disable if report shows Short P&L is negative.",
                 GroupName = "C: Direction", Order = 2)]
        public bool AllowShort { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Use Trend Alignment",
                 Description = "In trending_up regime only take longs; in trending_down only take shorts.",
                 GroupName = "C: Direction", Order = 3)]
        public bool UseTrendAlignment { get; set; }

        #endregion

        #region D: Entry Signal

        [NinjaScriptProperty]
        [Range(3, 200)]
        [Display(Name = "Fast EMA Period",
                 Description = "Entry signal EMA. Crossover of fast over slow = entry trigger.",
                 GroupName = "D: Entry Signal", Order = 1)]
        public int EntryEmaPeriod { get; set; }

        [NinjaScriptProperty]
        [Range(10, 500)]
        [Display(Name = "Slow EMA Period (Regime)",
                 Description = "Slow EMA used for both trend direction and entry confirmation.",
                 GroupName = "D: Entry Signal", Order = 2)]
        public int SlowEmaPeriod { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Require EMA Confirmation",
                 Description = "Require close > slow EMA for longs (and < for shorts) at entry.",
                 GroupName = "D: Entry Signal", Order = 3)]
        public bool RequireEmaConfirmation { get; set; }

        [NinjaScriptProperty]
        [Range(0.0, 1.0)]
        [Display(Name = "Min ATR Percentile",
                 Description = "0 = disabled. 0.3 = only trade when ATR is in the upper 70% of history. " +
                               "Use to avoid dead / compressed markets.",
                 GroupName = "D: Entry Signal", Order = 4)]
        public double RequireMinAtrMultiple { get; set; }

        #endregion

        #region D2: Entry Pattern

        [NinjaScriptProperty]
        [Display(Name = "Entry Signal",
                 Description = "Entry trigger family. Mirrors the ta_foundation discovery " +
                               "signal families. PINNED only — the NT optimizer cannot sweep enums.",
                 GroupName = "D2: Entry Pattern", Order = 1)]
        public SdfEntrySignal EntrySignal { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Timing Mode",
                 Description = "NextOpen = market at bar close. BreakExtreme = stop beyond the " +
                               "signal bar. BodyMidpoint = limit at body midpoint. PINNED only.",
                 GroupName = "D2: Entry Pattern", Order = 2)]
        public SdfTimingMode TimingMode { get; set; }

        [NinjaScriptProperty]
        [Range(0.0, 20.0)]
        [Display(Name = "Buffer Ticks",
                 Description = "BreakExtreme: ticks beyond the signal bar extreme for the stop trigger.",
                 GroupName = "D2: Entry Pattern", Order = 3)]
        public double BufferTicks { get; set; }

        [NinjaScriptProperty]
        [Range(1, 50)]
        [Display(Name = "Fill Timeout Bars",
                 Description = "BreakExtreme/BodyMidpoint: cancel the working entry after this many bars.",
                 GroupName = "D2: Entry Pattern", Order = 4)]
        public int FillTimeoutBars { get; set; }

        [NinjaScriptProperty]
        [Range(2, 200)]
        [Display(Name = "Body Roll Lookback",
                 Description = "Window N for body_vs_roll_N / size_vs_roll_N (features.py size_lookbacks). " +
                               "Discovery default 20.",
                 GroupName = "D2: Entry Pattern", Order = 5)]
        public int BodyRollLookback { get; set; }

        [NinjaScriptProperty]
        [Range(2, 200)]
        [Display(Name = "Extreme Lookback",
                 Description = "Window N for clean-breakout extreme and N-bar breakout channel. " +
                               "Discovery default 20.",
                 GroupName = "D2: Entry Pattern", Order = 6)]
        public int ExtremeLookback { get; set; }

        [NinjaScriptProperty]
        [Range(0.1, 10.0)]
        [Display(Name = "Body Multiplier",
                 Description = "LargeBody: body_vs_roll >= this. patterns.py body_multiplier (default 1.5).",
                 GroupName = "D2: Entry Pattern", Order = 7)]
        public double BodyMultiplier { get; set; }

        [NinjaScriptProperty]
        [Range(0.05, 5.0)]
        [Display(Name = "Wick-to-Body Max",
                 Description = "LargeBody: both wicks/body <= this. patterns.py wick_to_body_max (default 0.5).",
                 GroupName = "D2: Entry Pattern", Order = 8)]
        public double WickToBodyMax { get; set; }

        [NinjaScriptProperty]
        [Range(0.5, 10.0)]
        [Display(Name = "Pin Wick-to-Body Min",
                 Description = "PinBar: dominant wick/body >= this. patterns.py wick_to_body_min (default 1.5).",
                 GroupName = "D2: Entry Pattern", Order = 9)]
        public double PinWickToBodyMin { get; set; }

        [NinjaScriptProperty]
        [Range(0.05, 5.0)]
        [Display(Name = "Pin Opp Wick-to-Body Max",
                 Description = "PinBar: opposite wick/body <= this. patterns.py *_wick_to_body_max (default 0.5).",
                 GroupName = "D2: Entry Pattern", Order = 10)]
        public double PinOppWickToBodyMax { get; set; }

        [NinjaScriptProperty]
        [Range(0.01, 1.0)]
        [Display(Name = "Pin Body-to-Range Max",
                 Description = "PinBar: body/range <= this. patterns.py body_to_range_max (default 0.35).",
                 GroupName = "D2: Entry Pattern", Order = 11)]
        public double PinBodyToRangeMax { get; set; }

        [NinjaScriptProperty]
        [Range(0.1, 10.0)]
        [Display(Name = "Engulf Ratio",
                 Description = "Engulfing: current body >= prior body × this. patterns.py engulf_ratio (default 1.0).",
                 GroupName = "D2: Entry Pattern", Order = 12)]
        public double EngulfRatio { get; set; }

        [NinjaScriptProperty]
        [Range(0.01, 1.0)]
        [Display(Name = "Doji Body-to-Range Max",
                 Description = "Doji: body/range <= this. patterns.py body_to_range_max (default 0.15).",
                 GroupName = "D2: Entry Pattern", Order = 13)]
        public double DojiBodyToRangeMax { get; set; }

        [NinjaScriptProperty]
        [Range(0.1, 10.0)]
        [Display(Name = "Clean ATR Mult",
                 Description = "CleanBreakout: size_vs_atr >= this. patterns.py atr_mult (default 1.5).",
                 GroupName = "D2: Entry Pattern", Order = 14)]
        public double CleanAtrMult { get; set; }

        [NinjaScriptProperty]
        [Range(0.1, 1.0)]
        [Display(Name = "Clean Body-to-Range Min",
                 Description = "CleanBreakout: body/range >= this. patterns.py body_to_range_min (default 0.60).",
                 GroupName = "D2: Entry Pattern", Order = 15)]
        public double CleanBodyToRangeMin { get; set; }

        [NinjaScriptProperty]
        [Range(0, 500)]
        [Display(Name = "Entry Min Size Ticks",
                 Description = "Signal bar total range >= this many ticks. patterns.py min_size_ticks.",
                 GroupName = "D2: Entry Pattern", Order = 16)]
        public int EntryMinSizeTicks { get; set; }

        [NinjaScriptProperty]
        [Range(1, 2000)]
        [Display(Name = "Entry Max Size Ticks",
                 Description = "Signal bar total range <= this many ticks (large_body / pin bars). " +
                               "patterns.py max_size_ticks (default 200).",
                 GroupName = "D2: Entry Pattern", Order = 17)]
        public int EntryMaxSizeTicks { get; set; }

        #endregion

        #region E: Exit Policy

        [NinjaScriptProperty]
        [Display(Name = "Exit Policy",
                 Description = "Matches the Exit Policy Sweep section of the report. " +
                               "FixedRR uses StopTicks/TargetTicks. " +
                               "AtrTrail uses AtrTrailMultiple. " +
                               "Chandelier uses ChandelierLookback + AtrTrailMultiple. " +
                               "Giveback uses GivebackPct. " +
                               "BreakEvenOnly uses BreakEvenTrigger/Plus ticks.",
                 GroupName = "E: Exit Policy", Order = 1)]
        public SdfExitPolicy ExitPolicy { get; set; }

        [NinjaScriptProperty]
        [Range(4, 500)]
        [Display(Name = "Stop Ticks",
                 Description = "Stop loss in ticks. Set to p75 MAE from the MAE/MFE Profile section.",
                 GroupName = "E: Exit Policy", Order = 2)]
        public int StopTicks { get; set; }

        [NinjaScriptProperty]
        [Range(4, 500)]
        [Display(Name = "Target Ticks (FixedRR)",
                 Description = "Profit target in ticks for FixedRR policy. Set to p50–p75 MFE from report.",
                 GroupName = "E: Exit Policy", Order = 3)]
        public int TargetTicks { get; set; }

        [NinjaScriptProperty]
        [Range(0.5, 6.0)]
        [Display(Name = "ATR Trail Multiple",
                 Description = "Stop trails at High - (multiple × ATR) for longs. " +
                               "Also used as Chandelier ATR offset.",
                 GroupName = "E: Exit Policy", Order = 4)]
        public double AtrTrailMultiple { get; set; }

        [NinjaScriptProperty]
        [Range(3, 100)]
        [Display(Name = "Chandelier Lookback",
                 Description = "Bars to look back for highest-high (long) / lowest-low (short).",
                 GroupName = "E: Exit Policy", Order = 5)]
        public int ChandelierLookback { get; set; }

        [NinjaScriptProperty]
        [Range(0.05, 0.95)]
        [Display(Name = "Giveback Fraction",
                 Description = "Exit when this fraction of peak open profit has been given back. " +
                               "0.40 = exit when 40% of peak profit disappears.",
                 GroupName = "E: Exit Policy", Order = 6)]
        public double GivebackPct { get; set; }

        [NinjaScriptProperty]
        [Range(4, 300)]
        [Display(Name = "Break-Even Trigger Ticks",
                 Description = "Move stop to breakeven after this many ticks of open profit.",
                 GroupName = "E: Exit Policy", Order = 7)]
        public int BreakEvenTriggerTicks { get; set; }

        [NinjaScriptProperty]
        [Range(0, 20)]
        [Display(Name = "Break-Even Plus Ticks",
                 Description = "Lock in this many extra ticks above breakeven when moving stop.",
                 GroupName = "E: Exit Policy", Order = 8)]
        public int BreakEvenPlusTicks { get; set; }

        #endregion

        #region F: Daily Risk

        [NinjaScriptProperty]
        [Range(50.0, 10000.0)]
        [Display(Name = "Max Daily Loss ($)",
                 Description = "Stop trading for the session if P&L drops below this.",
                 GroupName = "F: Daily Risk", Order = 1)]
        public double MaxDailyLossUsd { get; set; }

        [NinjaScriptProperty]
        [Range(50.0, 50000.0)]
        [Display(Name = "Max Daily Profit ($)",
                 Description = "Stop trading for the session after hitting this profit target.",
                 GroupName = "F: Daily Risk", Order = 2)]
        public double MaxDailyProfitUsd { get; set; }

        [NinjaScriptProperty]
        [Range(1, 50)]
        [Display(Name = "Max Daily Trades",
                 Description = "Maximum trades per session. " +
                               "Set to max_consecutive_losses + 2 from the Drawdown section as a safety margin.",
                 GroupName = "F: Daily Risk", Order = 3)]
        public int MaxDailyTrades { get; set; }

        #endregion

        #region G: Debug

        [NinjaScriptProperty]
        [Display(Name = "Enable Debug Print",
                 Description = "Print regime, session, and exit reasons to the Output window.",
                 GroupName = "G: Debug", Order = 1)]
        public bool EnableDebugPrint { get; set; }

        [NinjaScriptProperty]
        [Range(1, 10)]
        [Display(Name = "Contracts",
                 Description = "Number of contracts per entry.",
                 GroupName = "G: Debug", Order = 2)]
        public int Contracts { get; set; }

        #endregion
    }
}
