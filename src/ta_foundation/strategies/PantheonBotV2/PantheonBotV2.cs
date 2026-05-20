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
    #region Enums
    public enum TrendRegime
    {
        Down = 0,
        Flat = 1,
        Up   = 2
    }

    public enum VwapRegime
    {
        Below = 0,
        Near  = 1,
        Above = 2
    }

    public enum VolatilityRegime
    {
        Low  = 0,
        Mid  = 1,
        High = 2
    }

    public enum TrendSlopeMode
    {
        RawPoints     = 0,
        AtrNormalized = 1
    }

    public enum TrendRegimeFilter
    {
        Down = 0,
        Flat = 1,
        Up   = 2,
        Any  = 3
    }

    public enum VwapRegimeFilter
    {
        Below = 0,
        Near  = 1,
        Above = 2,
        Any   = 3
    }

    public enum VolatilityRegimeFilter
    {
        Low  = 0,
        Mid  = 1,
        High = 2,
        Any  = 3
    }
    #endregion

    public class PantheonBotV2 : Strategy
    {
        #region Helper classes
        public static class RegimeClassifier
        {
            public static TrendRegime ClassifyTrend(
                double emaNow,
                double emaLookback,
                double atrNow,
                int slopeLookbackBars,
                double flatThreshold,
                TrendSlopeMode slopeMode)
            {
                if (slopeLookbackBars <= 0)
                    return TrendRegime.Flat;

                double slope;

                if (slopeMode == TrendSlopeMode.AtrNormalized)
                {
                    if (atrNow <= 0)
                        return TrendRegime.Flat;

                    slope = (emaNow - emaLookback) / (slopeLookbackBars * atrNow);
                }
                else
                {
                    slope = (emaNow - emaLookback) / slopeLookbackBars;
                }

                if (slope > flatThreshold)
                    return TrendRegime.Up;

                if (slope < -flatThreshold)
                    return TrendRegime.Down;

                return TrendRegime.Flat;
            }

            public static VwapRegime ClassifyVwap(double price, double vwap, double atr, double nearThresholdAtr)
            {
                if (atr <= 0)
                    return VwapRegime.Near;

                double distAtr = (price - vwap) / atr;

                if (distAtr > nearThresholdAtr)
                    return VwapRegime.Above;

                if (distAtr < -nearThresholdAtr)
                    return VwapRegime.Below;

                return VwapRegime.Near;
            }

            public static VolatilityRegime ClassifyVolatilityFromPercentile(double percentileRank, double lowThreshold, double highThreshold)
            {
                if (percentileRank <= lowThreshold)
                    return VolatilityRegime.Low;

                if (percentileRank >= highThreshold)
                    return VolatilityRegime.High;

                return VolatilityRegime.Mid;
            }
        }

        public class RollingPercentileRank
        {
            private readonly int capacity;
            private readonly Queue<double> queue;

            public RollingPercentileRank(int capacity)
            {
                this.capacity = Math.Max(5, capacity);
                this.queue = new Queue<double>(this.capacity + 1);
            }

            public int Count
            {
                get { return queue.Count; }
            }

            public void Add(double value)
            {
                queue.Enqueue(value);
                while (queue.Count > capacity)
                    queue.Dequeue();
            }

            public double GetPercentileRank(double currentValue)
            {
                if (queue.Count == 0)
                    return 0.5;

                int less = 0;
                int equal = 0;

                foreach (double x in queue)
                {
                    if (x < currentValue)
                        less++;
                    else if (Math.Abs(x - currentValue) < 1e-10)
                        equal++;
                }

                return (less + 0.5 * equal) / queue.Count;
            }
        }
        #endregion

        #region Fields
        private const int PrimaryBip = 0;

        // BIP indexes match the exact AddDataSeries order in State.Configure
        private const int BIP_5M  = 1;
        private const int BIP_15M = 2;
        private const int BIP_30M = 3;
        private const int BIP_60M = 4;

        private int selectedTrendBip = BIP_15M;

        // Primary-series indicators
        private ATR atrPrimary;
        private ATR atrVolatility;
        private EMA fastEntryEma;

        // Higher-timeframe indicators
        private EMA emaTrendHtf;
        private ATR atrTrendHtf;

        // Rolling ATR context
        private RollingPercentileRank atrPercentileRank;

        // Cached regime states
        private TrendRegime currentTrendRegime = TrendRegime.Flat;
        private VwapRegime currentVwapRegime = VwapRegime.Near;
        private VolatilityRegime currentVolatilityRegime = VolatilityRegime.Mid;

        // Cached diagnostics
        private double currentTrendSlopeValue = 0.0;
        private double currentVwapDistAtr = 0.0;
        private double currentAtrPercentile = 0.5;

        // Manual session VWAP accumulator
        private double sessionCumPv = 0.0;
        private double sessionCumVolume = 0.0;
        private double currentSessionVwap = 0.0;

        private bool allowTrading;
        private double trendLine = 0.0;

        private Order stopOrder = null;
        private double highSinceEntry = 0.0;
        private double lowSinceEntry = 0.0;
        private bool lockInActivated = false;

        private int trades;

        private int priorTradesCount;
        private double priorTradesCumProfit;
        #endregion

        #region State
        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Description = @"Buy on cross up, Sell on cross down. Analyzer-safe version with regime filters.";
                Name = "PantheonBotV2";
                Calculate = Calculate.OnBarClose;
                EntriesPerDirection = 1;
                EntryHandling = EntryHandling.AllEntries;
                IsExitOnSessionCloseStrategy = true;
                ExitOnSessionCloseSeconds = 30;
                IsFillLimitOnTouch = false;
                MaximumBarsLookBack = MaximumBarsLookBack.TwoHundredFiftySix;
                OrderFillResolution = OrderFillResolution.Standard;
                Slippage = 0;
                StartBehavior = StartBehavior.WaitUntilFlat;
                TimeInForce = TimeInForce.Gtc;
                TraceOrders = false;
                RealtimeErrorHandling = RealtimeErrorHandling.StopCancelClose;
                StopTargetHandling = StopTargetHandling.PerEntryExecution;
                BarsRequiredToTrade = 20;
                IsInstantiatedOnEachOptimizationIteration = false;

                Contracts = 1;
                MaxStop = 200;
                UseMaxStop = true;
                MaxTPRatio = 0.5;
                UseMaxTP = true;

                Long = true;
                Short = true;
                Reverse = false;

                averageFast = 50;
                averageSlow = 200;
                averageTrend = 300;

                AddPlot(Brushes.Red, "FAST");
                AddPlot(Brushes.Green, "SLOW");
                AddPlot(Brushes.White, "TREND");

                UseTrend = true;
                UseTrendReverse = false;

                allowTrading = true;

                UseTimeFilter = true;
                StartTimeH = 0;
                StartTimeM = 0;
                DurationTimeH = 1;
                DurationTimeM = 30;

                UseDynamicStop = true;

                UseLockIn = true;
                LockInTriggerTicks = 30;
                LockInPlusTicks = 6;

                UseGiveback = true;
                GivebackStartTicks = 100;
                GivebackTicks = 30;

                UseTrail = false;
                TrailStartTicks = 30;
                TrailDistanceTicks = 12;

                // Regime defaults
                TrendHigherTimeFrameMinutes = 15;
                TrendEmaPeriod = 34;
                TrendSlopeLookbackBars = 3;
                TrendAtrPeriod = 14;
                TrendFlatThreshold = 0.03;
                TrendSlopeModeInt = 1;

                VwapAtrPeriod = 14;
                VwapNearThresholdAtr = 0.20;
                UseCloseForVwapPrice = true;

                VolatilityAtrPeriod = 14;
                VolatilityLookbackWindow = 100;
                VolatilityLowPercentile = 0.33;
                VolatilityHighPercentile = 0.67;

                RequiredTrendRegimeFilter = TrendRegimeFilter.Up;
                RequiredVwapRegimeFilter = VwapRegimeFilter.Near;
                RequiredVolatilityRegimeFilter = VolatilityRegimeFilter.Any;
                BlockedVolatilityRegimeFilter = VolatilityRegimeFilter.High;

                EnableDebugPrint = false;
                EnableTradeMarkers = false;

                UseKill = false;
                KillProfitStop = 800;
                KillLossStop = 400;

                ProfitStop = 10000;
                LossStop = 10000;
                MaxTrades = 500;

                priorTradesCount = 0;
                priorTradesCumProfit = 0;
                trades = 0;
            }
            else if (State == State.Configure)
            {
                // All four HTF series added unconditionally in fixed order.
                // BIP_5M=1, BIP_15M=2, BIP_30M=3, BIP_60M=4
                AddDataSeries(BarsPeriodType.Minute, 5);
                AddDataSeries(BarsPeriodType.Minute, 15);
                AddDataSeries(BarsPeriodType.Minute, 30);
                AddDataSeries(BarsPeriodType.Minute, 60);
            }
            else if (State == State.DataLoaded)
            {
                selectedTrendBip = ResolveTrendBip(TrendHigherTimeFrameMinutes);

                int maxTP = (int)Math.Floor(MaxStop * MaxTPRatio);

                if (UseMaxStop && !UseDynamicStop)
                    SetStopLoss("", CalculationMode.Ticks, MaxStop, false);

                if (UseMaxTP)
                    SetProfitTarget("", CalculationMode.Ticks, maxTP);

                atrPrimary = ATR(BarsArray[PrimaryBip], VwapAtrPeriod);
                atrVolatility = ATR(BarsArray[PrimaryBip], VolatilityAtrPeriod);
                fastEntryEma = EMA(BarsArray[PrimaryBip], 20);

                emaTrendHtf = EMA(BarsArray[selectedTrendBip], TrendEmaPeriod);
                atrTrendHtf = ATR(BarsArray[selectedTrendBip], TrendAtrPeriod);

                atrPercentileRank = new RollingPercentileRank(VolatilityLookbackWindow);
            }
            else if (State == State.Realtime)
            {
                ExitShort();
                ExitLong();

                priorTradesCount = SystemPerformance.AllTrades.Count;
                priorTradesCumProfit = SystemPerformance.AllTrades.TradesPerformance.Currency.CumProfit;
            }
        }
        #endregion

        #region Core
        protected override void OnBarUpdate()
        {
            if (BarsInProgress == selectedTrendBip)
            {
                if (CurrentBars[selectedTrendBip] < Math.Max(TrendEmaPeriod, TrendSlopeLookbackBars) + 2)
                    return;

                UpdateTrendRegimeFromHigherTimeFrame();
                return;
            }

            if (BarsInProgress != PrimaryBip)
                return;

            if (CurrentBars[PrimaryBip] < Math.Max(Math.Max(VwapAtrPeriod, VolatilityAtrPeriod), 20))
                return;

            if (CurrentBars[selectedTrendBip] < Math.Max(TrendEmaPeriod, TrendSlopeLookbackBars) + 2)
                return;

            if (CurrentBar < BarsRequiredToTrade)
                return;

            int maxMA = Math.Max(averageFast, averageSlow);
            if (CurrentBar < Math.Max(maxMA, averageTrend))
                return;

            FAST[0] = SMA(averageFast)[0];
            SLOW[0] = SMA(averageSlow)[0];
            TREND[0] = SMA(averageTrend)[0];
            trendLine = TREND[0];

            UpdateSessionVwap();
            UpdateVwapRegimeFromPrimary();
            UpdateVolatilityRegimeFromPrimary();

            if (EnableDebugPrint)
            {
                // Backward-compatible debug line: original format is unchanged
                // through `pct=...%`; new pipe-delimited block appended at end
                // for ta_foundation bar_regime parity tests to localize which
                // intermediate (EMA, ATR, VWAP) diverges.
                Print(string.Format(
                    "{0:yyyy-MM-dd HH:mm:ss} | Trend={1} slope={2:F4} | VWAP={3} distATR={4:F3} | Vol={5} pct={6:P1}"
                    + " | ema_htf={7:F4} atr_htf={8:F4} atr_pri={9:F4} vwap={10:F4} atr_vol={11:F4}",
                    Time[0],
                    currentTrendRegime,
                    currentTrendSlopeValue,
                    currentVwapRegime,
                    currentVwapDistAtr,
                    currentVolatilityRegime,
                    currentAtrPercentile,
                    emaTrendHtf[0],
                    atrTrendHtf[0],
                    atrPrimary[0],
                    currentSessionVwap,
                    atrVolatility[0]));
            }

            bool allowTradeThisBar = allowTrading && PassesRegimeFilters();

            if (Bars.IsFirstBarOfSession)
            {
                priorTradesCount = SystemPerformance.AllTrades.Count;
                priorTradesCumProfit = SystemPerformance.AllTrades.TradesPerformance.Currency.CumProfit;
                allowTrading = true;
                allowTradeThisBar = PassesRegimeFilters();
                trades = 0;
            }

            if (SystemPerformance.AllTrades.TradesPerformance.Currency.CumProfit - priorTradesCumProfit >= ProfitStop
                || SystemPerformance.AllTrades.TradesPerformance.Currency.CumProfit - priorTradesCumProfit <= -LossStop
                || SystemPerformance.AllTrades.Count - priorTradesCount >= MaxTrades)
            {
                ExitShort();
                ExitLong();
                allowTrading = false;
                return;
            }

            if (UseTimeFilter)
            {
                int curTime = ToTime(Time[0]);
                int endTimeHours = (StartTimeH + DurationTimeH) <= 23 ? (StartTimeH + DurationTimeH) : 23;
                int endTimeMinutes;

                if ((StartTimeM + DurationTimeM) <= 59)
                {
                    endTimeMinutes = StartTimeM + DurationTimeM;
                }
                else
                {
                    endTimeMinutes = StartTimeM + DurationTimeM - 60;
                    endTimeHours++;

                    if (endTimeHours >= 24)
                    {
                        endTimeHours = 23;
                        endTimeMinutes = 59;
                    }
                }

                int st = ToTime(StartTimeH, StartTimeM, 0);
                int et = ToTime(endTimeHours, endTimeMinutes, 0);

                if (curTime < st || curTime > et)
                {
                    if (Position.MarketPosition != MarketPosition.Flat)
                    {
                        if (CrossAbove(FAST, SLOW, 1) || CrossBelow(FAST, SLOW, 1))
                        {
                            ExitShort();
                            ExitLong();
                        }
                    }
                    return;
                }
            }

            if (!allowTradeThisBar)
            {
                ExitShort();
                ExitLong();
                return;
            }

            if (UseDynamicStop && Position.MarketPosition != MarketPosition.Flat)
            {
                ManageDynamicStop();
            }
            else if (Position.MarketPosition == MarketPosition.Flat)
            {
                stopOrder = null;
                lockInActivated = false;
                highSinceEntry = 0.0;
                lowSinceEntry = 0.0;
            }

            if ((CrossAbove(FAST, SLOW, 1)) || (CrossBelow(FAST, SLOW, 1)))
            {
                if (MaxTrades <= trades)
                {
                    ExitShort();
                    ExitLong();
                    allowTrading = false;
                    return;
                }
            }

            if (CrossAbove(FAST, SLOW, 1) && Long)
            {
                if (UseTrend)
                {
                    if (Close[0] < trendLine && !UseTrendReverse)
                    {
                        ExitShort();
                        ExitLong();
                        return;
                    }

                    if (Close[0] > trendLine && UseTrendReverse)
                    {
                        ExitShort();
                        ExitLong();
                        return;
                    }
                }

                if (allowTradeThisBar)
                {
                    if (Reverse)
                    {
                        EnterShort(Convert.ToInt32(Contracts), "RevShort");
                        if (EnableTradeMarkers && ChartControl != null)
                            Draw.VerticalLine(this, "ShortTrade" + CurrentBar, 0, Brushes.Crimson);
                    }
                    else
                    {
                        EnterLong(Convert.ToInt32(Contracts), "Long");
                        if (EnableTradeMarkers && ChartControl != null)
                            Draw.VerticalLine(this, "LongTrade" + CurrentBar, 0, Brushes.Lime);
                    }
                    trades++;
                }
            }

            if (CrossBelow(FAST, SLOW, 1) && Short)
            {
                if (UseTrend)
                {
                    if (Close[0] > trendLine && !UseTrendReverse)
                    {
                        ExitShort();
                        ExitLong();
                        return;
                    }

                    if (Close[0] < trendLine && UseTrendReverse)
                    {
                        ExitShort();
                        ExitLong();
                        return;
                    }
                }

                if (allowTradeThisBar)
                {
                    if (Reverse)
                    {
                        EnterLong(Convert.ToInt32(Contracts), "RevLong");
                        if (EnableTradeMarkers && ChartControl != null)
                            Draw.VerticalLine(this, "LongTrade" + CurrentBar, 0, Brushes.Lime);
                    }
                    else
                    {
                        EnterShort(Convert.ToInt32(Contracts), "Short");
                        if (EnableTradeMarkers && ChartControl != null)
                            Draw.VerticalLine(this, "ShortTrade" + CurrentBar, 0, Brushes.Crimson);
                    }
                    trades++;
                }
            }
        }
        #endregion

        #region Regimes
        private int ResolveTrendBip(int minutes)
        {
            switch (minutes)
            {
                case 5:  return BIP_5M;
                case 15: return BIP_15M;
                case 30: return BIP_30M;
                case 60: return BIP_60M;
                default: return BIP_15M;
            }
        }

        private void UpdateTrendRegimeFromHigherTimeFrame()
        {
            int lb = TrendSlopeLookbackBars;
            double emaNow = emaTrendHtf[0];
            double emaThen = emaTrendHtf[lb];
            double atrNow = atrTrendHtf[0];

            TrendSlopeMode mode = (TrendSlopeMode)TrendSlopeModeInt;

            if (mode == TrendSlopeMode.AtrNormalized)
                currentTrendSlopeValue = atrNow > 0 ? (emaNow - emaThen) / (lb * atrNow) : 0.0;
            else
                currentTrendSlopeValue = (emaNow - emaThen) / lb;

            currentTrendRegime = RegimeClassifier.ClassifyTrend(
                emaNow,
                emaThen,
                atrNow,
                lb,
                TrendFlatThreshold,
                mode
            );
        }

        private void UpdateSessionVwap()
        {
            if (Bars.IsFirstBarOfSession)
            {
                sessionCumPv = 0.0;
                sessionCumVolume = 0.0;
                currentSessionVwap = 0.0;
            }

            double typicalPrice = (High[0] + Low[0] + Close[0]) / 3.0;
            double barVolume = Volume[0];

            sessionCumPv += typicalPrice * barVolume;
            sessionCumVolume += barVolume;

            currentSessionVwap = sessionCumVolume > 0 ? sessionCumPv / sessionCumVolume : Close[0];
        }

        private void UpdateVwapRegimeFromPrimary()
        {
            double atr = atrPrimary[0];
            double price = UseCloseForVwapPrice
                ? Close[0]
                : (High[0] + Low[0] + Close[0]) / 3.0;

            currentVwapDistAtr = atr > 0
                ? (price - currentSessionVwap) / atr
                : 0.0;

            currentVwapRegime = RegimeClassifier.ClassifyVwap(
                price,
                currentSessionVwap,
                atr,
                VwapNearThresholdAtr
            );
        }

        private void UpdateVolatilityRegimeFromPrimary()
        {
            double atrNow = atrVolatility[0];

            atrPercentileRank.Add(atrNow);
            currentAtrPercentile = atrPercentileRank.GetPercentileRank(atrNow);

            currentVolatilityRegime = RegimeClassifier.ClassifyVolatilityFromPercentile(
                currentAtrPercentile,
                VolatilityLowPercentile,
                VolatilityHighPercentile
            );
        }

        private bool PassesRegimeFilters()
        {
            // Trend
            if (RequiredTrendRegimeFilter != TrendRegimeFilter.Any)
            {
                TrendRegime required =
                    RequiredTrendRegimeFilter == TrendRegimeFilter.Up   ? TrendRegime.Up   :
                    RequiredTrendRegimeFilter == TrendRegimeFilter.Down ? TrendRegime.Down :
                                                                          TrendRegime.Flat;
                if (currentTrendRegime != required)
                    return false;
            }

            // VWAP
            if (RequiredVwapRegimeFilter != VwapRegimeFilter.Any)
            {
                VwapRegime required =
                    RequiredVwapRegimeFilter == VwapRegimeFilter.Above ? VwapRegime.Above :
                    RequiredVwapRegimeFilter == VwapRegimeFilter.Below ? VwapRegime.Below :
                                                                         VwapRegime.Near;
                if (currentVwapRegime != required)
                    return false;
            }

            // Volatility required
            if (RequiredVolatilityRegimeFilter != VolatilityRegimeFilter.Any)
            {
                VolatilityRegime required =
                    RequiredVolatilityRegimeFilter == VolatilityRegimeFilter.High ? VolatilityRegime.High :
                    RequiredVolatilityRegimeFilter == VolatilityRegimeFilter.Low  ? VolatilityRegime.Low  :
                                                                                    VolatilityRegime.Mid;
                if (currentVolatilityRegime != required)
                    return false;
            }

            // Volatility blocked
            if (BlockedVolatilityRegimeFilter != VolatilityRegimeFilter.Any)
            {
                VolatilityRegime blocked =
                    BlockedVolatilityRegimeFilter == VolatilityRegimeFilter.High ? VolatilityRegime.High :
                    BlockedVolatilityRegimeFilter == VolatilityRegimeFilter.Low  ? VolatilityRegime.Low  :
                                                                                   VolatilityRegime.Mid;
                if (currentVolatilityRegime == blocked)
                    return false;
            }

            return true;
        }
        #endregion

        #region Orders / Stops
        protected override void OnOrderUpdate(
            Order order,
            double limitPrice,
            double stopPrice,
            int quantity,
            int filled,
            double averageFillPrice,
            OrderState orderState,
            DateTime time,
            ErrorCode error,
            string nativeError)
        {
            if (order == null)
                return;

            if (order.Name == "DynamicStop")
            {
                if (orderState == OrderState.Accepted || orderState == OrderState.Working)
                    stopOrder = order;

                if (orderState == OrderState.Filled
                 || orderState == OrderState.Cancelled
                 || orderState == OrderState.Rejected)
                    stopOrder = null;
            }

            if (error != ErrorCode.NoError)
                Print(string.Format("*** ORDER ERROR on {0}: {1} Native='{2}'", order.Name, error, nativeError));
        }

        private void ManageDynamicStop()
        {
            double entry = Position.AveragePrice;

            if (Position.MarketPosition == MarketPosition.Long)
            {
                if (highSinceEntry == 0) highSinceEntry = entry;
                highSinceEntry = Math.Max(highSinceEntry, High[0]);
            }
            else if (Position.MarketPosition == MarketPosition.Short)
            {
                if (lowSinceEntry == 0) lowSinceEntry = entry;
                lowSinceEntry = Math.Min(lowSinceEntry, Low[0]);
            }

            if (stopOrder == null)
            {
                double initialStopPrice =
                    Position.MarketPosition == MarketPosition.Long
                        ? entry - MaxStop * TickSize
                        : entry + MaxStop * TickSize;

                if (State == State.Realtime)
                {
                    if (Position.MarketPosition == MarketPosition.Long)
                        stopOrder = ExitLongStopMarket(0, true, Position.Quantity, initialStopPrice, "DynamicStop", "");
                    else
                        stopOrder = ExitShortStopMarket(0, true, Position.Quantity, initialStopPrice, "DynamicStop", "");
                }
                else
                {
                    SetStopLoss("", CalculationMode.Price, initialStopPrice, false);
                }

                return;
            }

            if (stopOrder.OrderState != OrderState.Working && stopOrder.OrderState != OrderState.Accepted)
                return;

            double ticksInProfit =
                Position.MarketPosition == MarketPosition.Long
                    ? (Close[0] - entry) / TickSize
                    : (entry - Close[0]) / TickSize;

            double currentStop = stopOrder.StopPrice;
            double candidateStop = currentStop;

            if (UseLockIn && !lockInActivated && ticksInProfit >= LockInTriggerTicks)
            {
                double lockPrice =
                    Position.MarketPosition == MarketPosition.Long
                        ? entry + LockInPlusTicks * TickSize
                        : entry - LockInPlusTicks * TickSize;

                candidateStop = TightenStop(candidateStop, lockPrice);
                lockInActivated = true;
            }

            if (UseGiveback && ticksInProfit >= GivebackStartTicks)
            {
                double givebackStop =
                    Position.MarketPosition == MarketPosition.Long
                        ? highSinceEntry - GivebackTicks * TickSize
                        : lowSinceEntry + GivebackTicks * TickSize;

                candidateStop = TightenStop(candidateStop, givebackStop);
            }

            if (UseTrail && ticksInProfit >= TrailStartTicks)
            {
                double rawTrail =
                    Position.MarketPosition == MarketPosition.Long
                        ? Close[0] - TrailDistanceTicks * TickSize
                        : Close[0] + TrailDistanceTicks * TickSize;

                candidateStop = TightenStop(candidateStop, rawTrail);
            }

            if (Position.MarketPosition == MarketPosition.Long)
                candidateStop = Math.Min(candidateStop, Close[0] - TickSize);
            else
                candidateStop = Math.Max(candidateStop, Close[0] + TickSize);

            if (Math.Abs(candidateStop - currentStop) >= TickSize / 2.0)
            {
                if (State == State.Realtime && stopOrder != null)
                    ChangeOrder(stopOrder, stopOrder.Quantity, stopOrder.LimitPrice, candidateStop);
                else
                    SetStopLoss("", CalculationMode.Price, candidateStop, false);
            }
        }

        private double TightenStop(double current, double proposed)
        {
            if (Position.MarketPosition == MarketPosition.Long)
                return Math.Max(current, proposed);

            return Math.Min(current, proposed);
        }
        #endregion

        #region Public accessors
        [Browsable(false)]
        public TrendRegime CurrentTrendRegime
        {
            get { return currentTrendRegime; }
        }

        [Browsable(false)]
        public VwapRegime CurrentVwapRegime
        {
            get { return currentVwapRegime; }
        }

        [Browsable(false)]
        public VolatilityRegime CurrentVolatilityRegime
        {
            get { return currentVolatilityRegime; }
        }

        [Browsable(false)]
        public double CurrentTrendSlopeValue
        {
            get { return currentTrendSlopeValue; }
        }

        [Browsable(false)]
        public double CurrentVwapDistanceAtr
        {
            get { return currentVwapDistAtr; }
        }

        [Browsable(false)]
        public double CurrentAtrPercentile
        {
            get { return currentAtrPercentile; }
        }

        [Browsable(false)]
        public double CurrentSessionVwap
        {
            get { return currentSessionVwap; }
        }
        #endregion

        #region Properties
        [NinjaScriptProperty]
        [Display(Name = "Trend HTF Minutes", GroupName = "Regime - Trend", Order = 0)]
        public int TrendHigherTimeFrameMinutes { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Trend EMA Period", GroupName = "Regime - Trend", Order = 1)]
        public int TrendEmaPeriod { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Trend Slope Lookback Bars", GroupName = "Regime - Trend", Order = 2)]
        public int TrendSlopeLookbackBars { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Trend ATR Period", GroupName = "Regime - Trend", Order = 3)]
        public int TrendAtrPeriod { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Trend Flat Threshold", GroupName = "Regime - Trend", Order = 4)]
        public double TrendFlatThreshold { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Trend Slope Mode (0=RawPoints,1=AtrNormalized)", GroupName = "Regime - Trend", Order = 5)]
        public int TrendSlopeModeInt { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "VWAP ATR Period", GroupName = "Regime - VWAP", Order = 10)]
        public int VwapAtrPeriod { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "VWAP Near Threshold ATR", GroupName = "Regime - VWAP", Order = 11)]
        public double VwapNearThresholdAtr { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Use Close for VWAP Regime", GroupName = "Regime - VWAP", Order = 12)]
        public bool UseCloseForVwapPrice { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Volatility ATR Period", GroupName = "Regime - Volatility", Order = 20)]
        public int VolatilityAtrPeriod { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Volatility Lookback Window", GroupName = "Regime - Volatility", Order = 21)]
        public int VolatilityLookbackWindow { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Volatility Low Percentile", GroupName = "Regime - Volatility", Order = 22)]
        public double VolatilityLowPercentile { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Volatility High Percentile", GroupName = "Regime - Volatility", Order = 23)]
        public double VolatilityHighPercentile { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Required Trend Regime", GroupName = "Filters", Order = 30)]
        public TrendRegimeFilter RequiredTrendRegimeFilter { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Required VWAP Regime", GroupName = "Filters", Order = 31)]
        public VwapRegimeFilter RequiredVwapRegimeFilter { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Required Volatility Regime", GroupName = "Filters", Order = 32)]
        public VolatilityRegimeFilter RequiredVolatilityRegimeFilter { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Blocked Volatility Regime", GroupName = "Filters", Order = 33)]
        public VolatilityRegimeFilter BlockedVolatilityRegimeFilter { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Enable Debug Print", GroupName = "Diagnostics", Order = 40)]
        public bool EnableDebugPrint { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Enable Trade Markers", GroupName = "Diagnostics", Order = 41)]
        public bool EnableTradeMarkers { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Use_DynamicStop", Order = 1, GroupName = "Stops")]
        public bool UseDynamicStop { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Use_LockIn", Order = 10, GroupName = "Stops")]
        public bool UseLockIn { get; set; }

        [NinjaScriptProperty]
        [Range(1, int.MaxValue)]
        [Display(Name = "LockIn_TriggerTicks", Order = 11, GroupName = "Stops")]
        public int LockInTriggerTicks { get; set; }

        [NinjaScriptProperty]
        [Range(0, int.MaxValue)]
        [Display(Name = "LockIn_PlusTicks", Order = 12, GroupName = "Stops")]
        public int LockInPlusTicks { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Use_Giveback", Order = 20, GroupName = "Stops")]
        public bool UseGiveback { get; set; }

        [NinjaScriptProperty]
        [Range(1, int.MaxValue)]
        [Display(Name = "Giveback_StartTicks", Order = 21, GroupName = "Stops")]
        public int GivebackStartTicks { get; set; }

        [NinjaScriptProperty]
        [Range(1, int.MaxValue)]
        [Display(Name = "Giveback_Ticks", Order = 22, GroupName = "Stops")]
        public int GivebackTicks { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Use_Trail", Order = 30, GroupName = "Stops")]
        public bool UseTrail { get; set; }

        [NinjaScriptProperty]
        [Range(1, int.MaxValue)]
        [Display(Name = "Trail_StartTicks", Order = 31, GroupName = "Stops")]
        public int TrailStartTicks { get; set; }

        [NinjaScriptProperty]
        [Range(1, int.MaxValue)]
        [Display(Name = "Trail_DistanceTicks", Order = 32, GroupName = "Stops")]
        public int TrailDistanceTicks { get; set; }

        [Browsable(false)]
        [XmlIgnore]
        public Series<double> FAST
        {
            get { return Values[0]; }
        }

        [Browsable(false)]
        [XmlIgnore]
        public Series<double> SLOW
        {
            get { return Values[1]; }
        }

        [Browsable(false)]
        [XmlIgnore]
        public Series<double> TREND
        {
            get { return Values[2]; }
        }

        [NinjaScriptProperty]
        [Display(Name = "Use_Time_Filter", Order = 9, GroupName = "Time")]
        public bool UseTimeFilter { get; set; }

        [NinjaScriptProperty]
        [Range(0, 23)]
        [Display(Name = "Start_Time_(HH)", Order = 100, GroupName = "Time")]
        public int StartTimeH { get; set; }

        [NinjaScriptProperty]
        [Range(0, 59)]
        [Display(Name = "Start_Time_(mm)", Order = 110, GroupName = "Time")]
        public int StartTimeM { get; set; }

        [NinjaScriptProperty]
        [Range(0, 23)]
        [Display(Name = "Duration_Time_(HH)", Order = 120, GroupName = "Time")]
        public int DurationTimeH { get; set; }

        [NinjaScriptProperty]
        [Range(0, 59)]
        [Display(Name = "Duration_Time_(mm)", Order = 130, GroupName = "Time")]
        public int DurationTimeM { get; set; }

        [NinjaScriptProperty]
        [Range(2, int.MaxValue)]
        [Display(Name = "averageFast", GroupName = "Fast Averages", Order = 1)]
        public int averageFast { get; set; }

        [NinjaScriptProperty]
        [Range(2, int.MaxValue)]
        [Display(Name = "averageSlow", GroupName = "Slow Averages", Order = 4)]
        public int averageSlow { get; set; }

        [NinjaScriptProperty]
        [Range(2, int.MaxValue)]
        [Display(Name = "averageTrend", GroupName = "Trend Averages", Order = 7)]
        public int averageTrend { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "UseTrend", Order = 10, GroupName = "Trend Averages")]
        public bool UseTrend { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "UseTrendReverse", Description = "UseTrend must be true to use.", Order = 11, GroupName = "Trend Averages")]
        public bool UseTrendReverse { get; set; }

        [NinjaScriptProperty]
        [Range(0, double.MaxValue)]
        [Display(Name = "ProfitStop", Order = 11, GroupName = "Risk")]
        public double ProfitStop { get; set; }

        [NinjaScriptProperty]
        [Range(0, double.MaxValue)]
        [Display(Name = "LossStop", Order = 12, GroupName = "Risk")]
        public double LossStop { get; set; }

        [NinjaScriptProperty]
        [Range(0, int.MaxValue)]
        [Display(Name = "MaxTrades", Order = 13, GroupName = "Risk")]
        public double MaxTrades { get; set; }

        [NinjaScriptProperty]
        [Range(1, int.MaxValue)]
        [Display(Name = "Contracts", Order = 5, GroupName = "Risk")]
        public int Contracts { get; set; }

        [NinjaScriptProperty]
        [Range(1, int.MaxValue)]
        [Display(Name = "MaxStop", Order = 6, GroupName = "Cross Profit")]
        public int MaxStop { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Use_MaxStop", Order = 7, GroupName = "Cross Profit")]
        public bool UseMaxStop { get; set; }

        [NinjaScriptProperty]
        [Range(0.1, double.MaxValue)]
        [Display(Name = "MaxTPRatio", Order = 80, GroupName = "Cross Profit")]
        public double MaxTPRatio { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Use_MaxTP", Order = 81, GroupName = "Cross Profit")]
        public bool UseMaxTP { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Long", Order = 70, GroupName = "Direction")]
        public bool Long { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Short", Order = 80, GroupName = "Direction")]
        public bool Short { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Reverse", Order = 20, GroupName = "Test")]
        public bool Reverse { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Use_Kill", Order = 20, GroupName = "Kill")]
        public bool UseKill { get; set; }

        [NinjaScriptProperty]
        [Range(0, double.MaxValue)]
        [Display(Name = "Kill_Profit_Stop", Order = 11, GroupName = "Kill")]
        public double KillProfitStop { get; set; }

        [NinjaScriptProperty]
        [Range(0, double.MaxValue)]
        [Display(Name = "Kill_Loss_Stop", Order = 12, GroupName = "Kill")]
        public double KillLossStop { get; set; }
        #endregion
    }
}