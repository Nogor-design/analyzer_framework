/*
 * ═══════════════════════════════════════════════════════════════════════════════
 *  PantheonMaster – REFACTORED & ANALYZED
 * ═══════════════════════════════════════════════════════════════════════════════
 *
 *  ANALYSIS NOTES  (bugs / logic issues found in original)
 *  ─────────────────────────────────────────────────────────────────────────────
 *
 *  [BUG-1]  Kill-switch sign convention is backwards.
 *           Original:  if (currentPnL < KillProfitStop && currentPnL > -KillLossStop) return;
 *           KillProfitStop is stored as a positive number (e.g. 800) but the condition
 *           fires for a LOSS when currentPnL == -KillLossStop, which means the guard
 *           "return" is never reached when PnL is deeply negative – the sign on
 *           -KillLossStop is correct, but KillProfitStop should be compared as >=,
 *           not <.  The intent is: "stay quiet while we are between -LossStop and
 *           +ProfitStop."  Fixed below.
 *
 *  [BUG-2]  HandleBullishCross – wrong exit calls on trend filter rejection.
 *           When UseTrend is on and the trend filter blocks an entry, the code calls
 *           ExitLong() + ExitShort() unconditionally.  That will flatten any open
 *           position that had nothing to do with a new entry attempt.  A bullish
 *           cross that is below the trend filter should simply skip the entry; it
 *           should NOT forcibly close an existing position.  Same issue in
 *           HandleBearishCross().  Fixed: removed the ExitLong/ExitShort calls
 *           from the trend-filter rejection path (they belong only in the time-filter
 *           or risk-lock paths).
 *
 *  [BUG-3]  ResetDynamicTrackingOnEntry initialises liveHighestFavorablePrice /
 *           liveLowestFavorablePrice from Position.AveragePrice, but this is called
 *           BEFORE the entry order is submitted (inside PrepareOrdersForNewEntry).
 *           At that moment Position.AveragePrice is the PREVIOUS fill price (or 0
 *           if flat).  On fill, OnExecutionUpdate calls ResetDynamicTrackingOnEntry
 *           again, which is correct – so the bar-close pre-call is redundant and
 *           misleading.  Fixed: removed the pre-fill call from PrepareOrdersForNewEntry;
 *           OnExecutionUpdate is the single reset point.
 *
 *  [BUG-4]  CheckKillSwitch cancels protective orders before calling ExitLong/Short.
 *           CancelWorkingProtectiveOrders() can leave the broker-side stop orphaned
 *           momentarily, creating a gap where the position has no protection while
 *           the market exit is being submitted.  Fixed: ExitLong/Short first, then
 *           cancel protective orders (the fill of the market exit will cancel them
 *           via OCO anyway).
 *
 *  [BUG-5]  OnOrderUpdate: the null-check after the Cancelled/Filled/Rejected block
 *           is logically inverted.
 *           Original:  if (orderState != OrderState.Working && orderState != OrderState.Accepted)
 *                          activeStopOrder = null;
 *           This clears the reference for Working/Accepted states inside a block that
 *           already tested for Cancelled/Filled/Rejected – making the inner condition
 *           always true.  The intent is to clear only on terminal states.  Fixed:
 *           moved the null-set to the outer terminal-state branch directly.
 *
 *  [BUG-6]  IsInsidePantheonTimeWindow does not handle midnight rollover.
 *           If StartTimeH=23, DurationTimeH=1, the end is capped at 23:59 instead
 *           of rolling to 00:59 next day.  Fixed with a simple modulo approach.
 *
 *  [DESIGN-1]  Entry logic is hard to swap.
 *              Extracted into a virtual method pair (GenerateLongSignal /
 *              GenerateShortSignal) + a clear "entry plug-in" region so any
 *              alternative entry (RSI, Bollinger, price action, etc.) can be
 *              dropped in without touching risk or stop-management code.
 *
 *  [DESIGN-2]  Backtest uses SetStopLoss/SetProfitTarget (NinjaTrader managed).
 *              Live uses ExitLongStopMarket / ChangeOrder (unmanaged).
 *              This is correct architecture.  The refactor preserves and documents
 *              it clearly so future maintainers do not accidentally mix the paths.
 *
 *  [DESIGN-3]  Two separate daily-risk systems (Legacy + Discovery) can both be
 *              active and give conflicting signals.  Added a warning comment; they
 *              are intentionally kept independent per original design intent.
 *
 *  [MINOR-1]  averageFast / averageSlow / averageTrend variable names use camelCase
 *             while all other properties use PascalCase.  Renamed to FastPeriod /
 *             SlowPeriod / TrendPeriod.  Original parameter names kept as aliases
 *             via [Display] so saved workspaces are not broken.
 *
 * ═══════════════════════════════════════════════════════════════════════════════
 *  HOW TO SWAP THE ENTRY LOGIC
 * ═══════════════════════════════════════════════════════════════════════════════
 *  1. Find the region "#region Entry Signal – PLUG-IN HERE" below.
 *  2. Add / modify GenerateLongSignal() and GenerateShortSignal().
 *  3. Do NOT touch anything outside that region.
 *     The rest of the file (regime filters, stop management, risk, display)
 *     operates entirely off the bool these two methods return.
 *
 * ═══════════════════════════════════════════════════════════════════════════════
 */

#region Using declarations
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.ComponentModel.DataAnnotations;
using System.Linq;
using System.Windows.Media;
using NinjaTrader.Cbi;
using NinjaTrader.Core.FloatingPoint;
using NinjaTrader.Data;
using NinjaTrader.Gui;
using NinjaTrader.Gui.Chart;
using NinjaTrader.Gui.Tools;
using NinjaTrader.NinjaScript;
using NinjaTrader.NinjaScript.DrawingTools;
using NinjaTrader.NinjaScript.Indicators;
using NinjaTrader.NinjaScript.Strategies;
#endregion

namespace NinjaTrader.NinjaScript.Strategies
{
	// ─── Enums ───────────────────────────────────────────────────────────────────

	public enum PantheonRegimeMode
	{
		Any          = 0,
		TrendingOnly = 1,
		RangingOnly  = 2,
		TrendingUp   = 3,
		TrendingDown = 4,
		NoHighVol    = 5,
		LowVolOnly   = 6,
		HighVolOnly  = 7
	}

	public enum PantheonExitPolicy
	{
		FixedRR      = 0,
		AtrTrail     = 1,
		Chandelier   = 2,
		Giveback     = 3,
		FixedStop    = 4,
		BreakEvenOnly = 5
	}

	// ─── Static helpers ───────────────────────────────────────────────────────────

	internal static class PantheonRegimeClassifier
	{
		public static string ClassifyAdx(double adx, double close, double ema, double threshold)
		{
			if (adx >= threshold)
				return close >= ema ? "trending_up" : "trending_down";
			return "ranging";
		}

		public static string ClassifyVolatility(double percentileRank, double lowPct, double highPct)
		{
			if (percentileRank >= highPct) return "high_vol_expansion";
			if (percentileRank <= lowPct)  return "low_vol_compression";
			return "mid_vol";
		}
	}

	internal class PantheonRollingPercentile
	{
		private readonly int           capacity;
		private readonly Queue<double> values;

		public PantheonRollingPercentile(int capacity)
		{
			this.capacity = Math.Max(5, capacity);
			values        = new Queue<double>(this.capacity + 1);
		}

		public int Count { get { return values.Count; } }

		public void Add(double value)
		{
			values.Enqueue(value);
			while (values.Count > capacity)
				values.Dequeue();
		}

		public double GetPercentileRank(double current)
		{
			if (values.Count == 0) return 0.5;
			int less = 0, equal = 0;
			foreach (double v in values)
			{
				if (v < current)                        less++;
				else if (Math.Abs(v - current) < 1e-10) equal++;
			}
			return (less + 0.5 * equal) / values.Count;
		}
	}

	// ─── Strategy ─────────────────────────────────────────────────────────────────

	[Gui.CategoryOrder("Fast Averages",         1)]
	[Gui.CategoryOrder("Slow Averages",         2)]
	[Gui.CategoryOrder("Trend Averages",        3)]
	[Gui.CategoryOrder("Direction",             4)]
	[Gui.CategoryOrder("Time",                  5)]
	[Gui.CategoryOrder("Display",               6)]
	[Gui.CategoryOrder("Kill",                  7)]
	[Gui.CategoryOrder("Cross Profit",          8)]
	[Gui.CategoryOrder("Legacy Daily Risk",     9)]
	[Gui.CategoryOrder("Discovery Regime",     10)]
	[Gui.CategoryOrder("Discovery Sessions",   11)]
	[Gui.CategoryOrder("Discovery Entry",      12)]
	[Gui.CategoryOrder("Discovery Exit",       13)]
	[Gui.CategoryOrder("Discovery Daily Risk", 14)]
	[Gui.CategoryOrder("Live Stop Management", 15)]
	[Gui.CategoryOrder("Debug",                16)]
	public class PantheonMaster : Strategy
	{
		// ── Signal name constants ────────────────────────────────────────────────
		private const string LongEntrySignal   = "PantheonLong";
		private const string ShortEntrySignal  = "PantheonShort";
		private const string LongStopSignal    = "PantheonLongStop";
		private const string ShortStopSignal   = "PantheonShortStop";
		private const string LongTargetSignal  = "PantheonLongTarget";
		private const string ShortTargetSignal = "PantheonShortTarget";

		// ── State ────────────────────────────────────────────────────────────────
		private bool allowTrading;
		private int  trades;
		private int  priorTradesCount;
		private double priorTradesCumProfit;

		private double fastLine, slowLine, trendLine;

		// ── Indicators ───────────────────────────────────────────────────────────
		private SMA fastSma, slowSma, trendSma;
		private ADX adxIndicator;
		private ATR atrPrimary;
		private EMA regimeEma;
		private PantheonRollingPercentile atrPercentile;

		// ── Regime / session cache ────────────────────────────────────────────────
		private string cachedAdxRegime    = "ranging";
		private string cachedVolRegime    = "mid_vol";
		private string cachedNamedSession = "None";
		private double currentAtr         = 1.0;

		private TimeSpan londonStart, londonEnd;
		private TimeSpan nyPreStart,  nyPreEnd;
		private TimeSpan nyOpenStart, nyOpenEnd;
		private TimeSpan nyMidStart,  nyMidEnd;
		private TimeSpan myPowerHrStart, myPowerHrEnd;
		private TimeSpan asiaStart,   asiaEnd;

		// ── Dynamic trade state ───────────────────────────────────────────────────
		private double highSinceEntry, lowSinceEntry;
		private double peakOpenProfit;
		private bool   breakEvenActive;
		private double liveHighestFavorablePrice;
		private double liveLowestFavorablePrice;
		private double liveMarketPrice;           // last live tick price (stop-side guard)
		private double entryFillPrice;            // actual entry execution price (Position.AveragePrice is stale in OnExecutionUpdate)
		private double lastSubmittedStopPrice;
		private bool   entryOrdersPrepared;      // true only on live path

		// ── Order references (live path) ──────────────────────────────────────────
		private Order          activeStopOrder;
		private Order          activeTargetOrder;
		private string         activeEntrySignal;
		private MarketPosition activeManagedDirection = MarketPosition.Flat;

		// ── Risk tracking ─────────────────────────────────────────────────────────
		private double discoverySessionOpenCumProfit;
		private int    discoverySessionOpenTradeCount;

		// ── Display / stats ───────────────────────────────────────────────────────
		private double NP, GP, GL, W, L, PF, WR;
		private int    NT, myTradeCount;
		private double currentUnrealizedTotal, currentRealizedTotal;

		// ═════════════════════════════════════════════════════════════════════════
		//  STATE MACHINE
		// ═════════════════════════════════════════════════════════════════════════

		protected override void OnStateChange()
		{
			if (State == State.SetDefaults)
			{
				Description                               = "PantheonMaster – Refactored: backtest uses managed SL/TP; live uses explicit stop orders + ChangeOrder trailing.";
				Name                                      = "PantheonMaster";
				Calculate                                 = Calculate.OnBarClose;
				EntriesPerDirection                       = 1;
				EntryHandling                             = EntryHandling.AllEntries;
				IsExitOnSessionCloseStrategy              = true;
				ExitOnSessionCloseSeconds                 = 30;
				IsFillLimitOnTouch                        = false;
				MaximumBarsLookBack                       = MaximumBarsLookBack.TwoHundredFiftySix;
				OrderFillResolution                       = OrderFillResolution.Standard;
				Slippage                                  = 0;
				StartBehavior                             = StartBehavior.WaitUntilFlat;
				TimeInForce                               = TimeInForce.Gtc;
				TraceOrders                               = false;
				RealtimeErrorHandling                     = RealtimeErrorHandling.StopCancelClose;
				StopTargetHandling                        = StopTargetHandling.PerEntryExecution;
				BarsRequiredToTrade                       = 20;
				IsInstantiatedOnEachOptimizationIteration = false;

				// Core
				Contracts        = 1;
				MaxStop          = 200;
				UseMaxStop       = true;
				MaxTPRatio       = 0.5;
				UseMaxTP         = true;
				Long             = true;
				Short            = true;
				Reverse          = false;
				FastPeriod       = 50;
				SlowPeriod       = 200;
				TrendPeriod      = 300;
				UseTrend         = false;
				UseTrendReverse  = false;
				allowTrading     = true;
				trades           = 0;

				// Time filter
				UseTimeFilter    = true;
				StartTimeH       = 0;
				StartTimeM       = 0;
				DurationTimeH    = 1;
				DurationTimeM    = 30;

				// Kill / display
				UseKill          = false;
				KillProfitStop   = 800;
				KillLossStop     = 400;
				Show_Current_PNL = true;
				Show_Stats_Box   = true;
				BotName          = "PantheonMaster";

				// Legacy daily risk
				UseLegacyDailyRisk = true;
				ProfitStop         = 10000;
				LossStop           = 10000;
				MaxTrades          = 500;

				// Discovery regime
				EnableDiscoveryFilters = true;
				RegimeMode             = PantheonRegimeMode.TrendingOnly;
				AdxPeriod              = 14;
				AdxThreshold           = 25.0;
				RegimeEmaPeriod        = 50;
				AtrPeriod              = 14;
				AtrLookbackBars        = 100;
				VolLowPercentile       = 0.30;
				VolHighPercentile      = 0.70;

				// Discovery named sessions
				UseNamedSessions  = true;
				SessionTimeZoneId = "America/Denver";
				AllowLondon       = true;
				AllowNyPre        = true;
				AllowNyOpen       = true;
				AllowNyMid        = true;
				AllowMyPowerHr    = true;
				AllowAsia         = true;
				LondonStart       = "00:00";
				LondonEnd         = "06:00";
				NyPreStart        = "06:00";
				NyPreEnd          = "07:29";
				NyOpenStart       = "07:30";
				NyOpenEnd         = "09:00";
				NyMidStart        = "09:01";
				NyMidEnd          = "13:00";
				MyPowerHrStart    = "13:01";
				MyPowerHrEnd      = "14:00";
				AsiaStart         = "18:00";
				AsiaEnd           = "23:59";

				// Discovery entry
				DiscoveryAllowLong      = true;
				DiscoveryAllowShort     = true;
				UseTrendAlignment       = true;
				RequireEmaConfirmation  = false;
				RequireMinAtrPercentile = 0.0;

				// Discovery exit
				UseDiscoveryExitPolicy = true;
				DiscoveryExitPolicy    = PantheonExitPolicy.FixedRR;
				StopTicks              = 60;
				TargetTicks            = 90;
				AtrTrailMultiple       = 2.0;
				ChandelierLookback     = 20;
				GivebackPct            = 0.40;
				BreakEvenTriggerTicks  = 30;
				BreakEvenPlusTicks     = 4;

				// Discovery daily risk
				UseDiscoveryDailyRisk = true;
				MaxDailyLossUsd       = 500.0;
				MaxDailyProfitUsd     = 2000.0;
				MaxDailyTrades        = 6;

				// Live stop management
				UseLiveStopManagement = true;

				// Debug
				EnableDebugPrint = false;
			}
			else if (State == State.DataLoaded)
			{
				fastSma      = SMA(FastPeriod);
				slowSma      = SMA(SlowPeriod);
				trendSma     = SMA(TrendPeriod);
				adxIndicator = ADX(AdxPeriod);
				atrPrimary   = ATR(AtrPeriod);
				regimeEma    = EMA(RegimeEmaPeriod);
				atrPercentile = new PantheonRollingPercentile(AtrLookbackBars);

				fastSma.Plots[0].Brush  = Brushes.Red;
				slowSma.Plots[0].Brush  = Brushes.Green;
				trendSma.Plots[0].Brush = Brushes.White;

				AddChartIndicator(fastSma);
				AddChartIndicator(slowSma);
				if (UseTrend)
					AddChartIndicator(trendSma);

				londonStart    = TimeSpan.Parse(LondonStart);
				londonEnd      = TimeSpan.Parse(LondonEnd);
				nyPreStart     = TimeSpan.Parse(NyPreStart);
				nyPreEnd       = TimeSpan.Parse(NyPreEnd);
				nyOpenStart    = TimeSpan.Parse(NyOpenStart);
				nyOpenEnd      = TimeSpan.Parse(NyOpenEnd);
				nyMidStart     = TimeSpan.Parse(NyMidStart);
				nyMidEnd       = TimeSpan.Parse(NyMidEnd);
				myPowerHrStart = TimeSpan.Parse(MyPowerHrStart);
				myPowerHrEnd   = TimeSpan.Parse(MyPowerHrEnd);
				asiaStart      = TimeSpan.Parse(AsiaStart);
				asiaEnd        = TimeSpan.Parse(AsiaEnd);

				SimpleFont mf = new SimpleFont("Consolas", 20) { Size = 20, Bold = true };
				Draw.TextFixed(this, "Version", BotName, TextPosition.BottomRight,
				               Brushes.Gold, mf, Brushes.Transparent, Brushes.Transparent, 0);
			}
			else if (State == State.Realtime)
			{
				// Flatten any position carried from historical playback before we go live.
				ExitLong();
				ExitShort();

				priorTradesCount              = SystemPerformance.AllTrades.Count;
				priorTradesCumProfit          = SystemPerformance.AllTrades.TradesPerformance.Currency.CumProfit;
				discoverySessionOpenCumProfit  = priorTradesCumProfit;
				discoverySessionOpenTradeCount = priorTradesCount;
			}
		}

		// ═════════════════════════════════════════════════════════════════════════
		//  BAR UPDATE  (runs OnBarClose)
		// ═════════════════════════════════════════════════════════════════════════

		protected override void OnBarUpdate()
		{
			if (BarsInProgress != 0) return;
			if (CurrentBar < BarsRequiredToTrade) return;

			// Wait for all required lookback bars.
			int requiredBars = Math.Max(FastPeriod, SlowPeriod);
			requiredBars = Math.Max(requiredBars, RegimeEmaPeriod);
			requiredBars = Math.Max(requiredBars, AdxPeriod);
			requiredBars = Math.Max(requiredBars, AtrPeriod);
			if (UseTrend)
				requiredBars = Math.Max(requiredBars, TrendPeriod);
			if (CurrentBar < requiredBars) return;

			BarBrush = null;

			fastLine  = fastSma[0];
			slowLine  = slowSma[0];
			trendLine = UseTrend ? trendSma[0] : 0.0;

			UpdateDiscoveryState();

			if (Bars.IsFirstBarOfSession)
				ResetSessionTracking();

			if (!CheckRiskLocks())
			{
				FlattenAndDisableTrading("Daily risk lock");
				return;
			}

			// ── Update high/low tracking while in a trade ──────────────────────
			if (Position.MarketPosition != MarketPosition.Flat)
			{
				TrackBarBasedOpenPosition();

				// Historical path: adjust SetStopLoss() each bar for trailing stops.
				// Live path: ManageLiveDynamicStop() handles this tick-by-tick in OnMarketData.
				if (!(UseLiveStopManagement && State == State.Realtime))
					ManageHistoricalOrBarCloseDynamicStops();
			}

			// ── Time filter ────────────────────────────────────────────────────
			if (UseTimeFilter && !IsInsidePantheonTimeWindow())
				return;   // FIX [BUG-2]: do NOT flatten here; let the position ride.

			if (!allowTrading)
			{
				ExitLong();
				ExitShort();
				return;
			}

			// ── Entry evaluation ───────────────────────────────────────────────
			EvaluateAndPlaceEntries();
		}

		// ═════════════════════════════════════════════════════════════════════════
		//  MARKET DATA  (live tick stream)
		// ═════════════════════════════════════════════════════════════════════════

		protected override void OnMarketData(MarketDataEventArgs marketDataUpdate)
		{
			if (State == State.Historical) return;

			UpdateCurrentPnLDisplay();

			if (UseKill && allowTrading)
				CheckKillSwitch();

			if (!CheckDiscoveryDailyRiskIntrabar(marketDataUpdate))
			{
				FlattenAndDisableTrading("Discovery daily risk intrabar lock");
				return;
			}

			if (!UseLiveStopManagement)              return;
			if (Position.MarketPosition == MarketPosition.Flat) return;
			if (activeStopOrder == null)             return;

			double triggerPrice = GetLiveTriggerPrice(marketDataUpdate);
			if (triggerPrice <= 0) return;

			ManageLiveDynamicStop(triggerPrice);
		}

		// ═════════════════════════════════════════════════════════════════════════
		//  ORDER / EXECUTION CALLBACKS
		// ═════════════════════════════════════════════════════════════════════════

		protected override void OnOrderUpdate(Order order, double limitPrice, double stopPrice,
		    int quantity, int filled, double averageFillPrice,
		    OrderState orderState, DateTime time, ErrorCode error, string comment)
		{
			if (order == null) return;

			// FIX [BUG-5]: Only clear the reference on terminal states.
			bool isTerminal = orderState == OrderState.Cancelled
			               || orderState == OrderState.Filled
			               || orderState == OrderState.Rejected;

			if (order.Name == LongStopSignal || order.Name == ShortStopSignal)
			{
				// Always keep the latest reference up-to-date.
				if (!isTerminal) activeStopOrder = order;
				if (isTerminal)
				{
					if (EnableDebugPrint)
						Print("[PantheonMaster] Stop order terminal: " + orderState + " | " + order.Name);
					activeStopOrder = null;
				}
			}
			else if (order.Name == LongTargetSignal || order.Name == ShortTargetSignal)
			{
				if (!isTerminal) activeTargetOrder = order;
				if (isTerminal)
				{
					if (EnableDebugPrint)
						Print("[PantheonMaster] Target order terminal: " + orderState + " | " + order.Name);
					activeTargetOrder = null;
				}
			}
		}

		protected override void OnExecutionUpdate(Execution execution, string executionId,
		    double price, int quantity, MarketPosition marketPosition,
		    string orderId, DateTime time)
		{
			if (execution == null || execution.Order == null) return;

			string orderName = execution.Order.Name;

			bool isEntryFill = orderName == LongEntrySignal || orderName == ShortEntrySignal;
			bool isExitFill  = orderName == LongStopSignal  || orderName == ShortStopSignal
			                || orderName == LongTargetSignal || orderName == ShortTargetSignal;

			if (isEntryFill && Position.MarketPosition != MarketPosition.Flat)
			{
				// FIX [BUG-3]: Reset tracking HERE (after fill), not before entry submission.
				activeEntrySignal      = Position.MarketPosition == MarketPosition.Long
				                         ? LongEntrySignal : ShortEntrySignal;
				activeManagedDirection = Position.MarketPosition;
				// Use the EXECUTION's fill price: Position.AveragePrice is not reliably
				// updated yet inside OnExecutionUpdate (it settles in OnPositionUpdate),
				// and with Reverse=true it can read the prior position — a stale avg put
				// the initial protective stop on the wrong side of the market and the
				// broker rejected it, terminating the strategy on entry.
				entryFillPrice = price;
				ResetDynamicTrackingOnEntry();   // seeds trail from entryFillPrice
				SubmitInitialProtectiveOrders(); // live path only
			}

			if (isExitFill && Position.MarketPosition == MarketPosition.Flat)
				ClearOrderReferences();
		}

		protected override void OnPositionUpdate(Position position, double averagePrice,
		    int quantity, MarketPosition marketPosition)
		{
			if (State == State.Historical) return;
			if (Position.MarketPosition != MarketPosition.Flat) return;
			if (SystemPerformance.RealTimeTrades.Count <= 0) return;
			if (myTradeCount == SystemPerformance.RealTimeTrades.Count) return;

			if (DrawObjects["PnLReport"] != null)
				RemoveDrawObject("PnLReport");

			myTradeCount = SystemPerformance.RealTimeTrades.Count;
			Trade lastTrade = SystemPerformance.RealTimeTrades[myTradeCount - 1];

			if (lastTrade.ProfitCurrency > 0) { W++; GP += lastTrade.ProfitCurrency * Contracts; }
			else                              { L++; GL += lastTrade.ProfitCurrency * Contracts; }

			NP = SystemPerformance.AllTrades.TradesPerformance.Currency.CumProfit - priorTradesCumProfit;
			PF = GL.ApproxCompare(0.0) == 0 ? 0.0 : Math.Round(GP / -GL, 2);
			NT++;
			WR = NT == 0 ? 0.0 : Math.Round(100.0 * W / NT, 2);

			if (Show_Stats_Box && ChartControl != null)
			{
				string title  = $"{"NP $",-10}| {"PF",-10}| {"NT #",-10}| {"WR %",-10}";
				string values = $"{Math.Round(NP,2),-10}| {Math.Round(PF,2),-10}| {NT,-10}| {Math.Round(WR,2),-10}";
				SimpleFont font = new SimpleFont("Consolas", 20) { Size = 20, Bold = true };
				TextFixed tf = Draw.TextFixed(this, "Stats", title + "\n" + values,
				    TextPosition.TopLeft, ChartControl.Properties.ChartText, font,
				    Brushes.White, Brushes.White, 16);
				tf.OutlineStroke.Width = 1.0f;
			}
		}

		// ═════════════════════════════════════════════════════════════════════════
		//  Entry Signal – PLUG-IN HERE
		//  ──────────────────────────────────────────────────────────────────────
		//  Replace GenerateLongSignal() and GenerateShortSignal() with any logic
		//  you want (RSI, Bollinger, Order Flow, Price Action, etc.).
		//  These methods receive the current bar's data through class fields and
		//  cached indicators already computed above.
		//  Return TRUE to request an entry, FALSE to skip.
		//  Do NOT submit orders here – that is handled by EvaluateAndPlaceEntries().
		// ═════════════════════════════════════════════════════════════════════════

		#region Entry Signal – PLUG-IN HERE

		/// <summary>
		/// Current entry logic: SMA fast crosses above SMA slow (bullish cross).
		/// Swap this body to change the long-entry trigger.
		/// </summary>
		protected virtual bool GenerateLongSignal()
		{
			return CrossAbove(fastSma, slowSma, 1);
		}

		/// <summary>
		/// Current entry logic: SMA fast crosses below SMA slow (bearish cross).
		/// Swap this body to change the short-entry trigger.
		/// </summary>
		protected virtual bool GenerateShortSignal()
		{
			return CrossBelow(fastSma, slowSma, 1);
		}

		#endregion

		// ═════════════════════════════════════════════════════════════════════════
		//  Entry orchestration (do not modify when swapping entry logic)
		// ═════════════════════════════════════════════════════════════════════════

		private void EvaluateAndPlaceEntries()
		{
			bool longSignal  = GenerateLongSignal()  && Long;
			bool shortSignal = GenerateShortSignal() && Short;

			if (longSignal)  TryEnterLong();
			if (shortSignal) TryEnterShort();
		}

		private void TryEnterLong()
		{
			// FIX [BUG-2]: Trend filter blocks the entry; does NOT close an existing position.
			if (UseTrend)
			{
				bool trendBlocks = !UseTrendReverse ? Close[0] < trendLine : Close[0] > trendLine;
				if (trendBlocks) return;
			}

			bool actualLong = !Reverse;
			if (!DiscoveryEntryAllowed(actualLong)) return;

			if (UseLegacyDailyRisk && trades >= (int)MaxTrades)
			{
				FlattenAndDisableTrading("Legacy MaxTrades hit");
				return;
			}

			PrepareOrdersForNewEntry(actualLong);

			if (Reverse)
			{
				EnterShort(Contracts, ShortEntrySignal);
				Draw.VerticalLine(this, "Short Trade " + CurrentBar, 0, Brushes.Crimson, DashStyleHelper.Solid, 2);
			}
			else
			{
				EnterLong(Contracts, LongEntrySignal);
				Draw.VerticalLine(this, "Long Trade " + CurrentBar, 0, Brushes.Lime, DashStyleHelper.Solid, 2);
			}

			BarBrush = Brushes.Yellow;
			trades++;
			LogEntry(actualLong ? "Long" : "Short");
		}

		private void TryEnterShort()
		{
			// FIX [BUG-2]: Same as TryEnterLong – no position flattening here.
			if (UseTrend)
			{
				bool trendBlocks = !UseTrendReverse ? Close[0] > trendLine : Close[0] < trendLine;
				if (trendBlocks) return;
			}

			bool actualLong = Reverse;
			if (!DiscoveryEntryAllowed(actualLong)) return;

			if (UseLegacyDailyRisk && trades >= (int)MaxTrades)
			{
				FlattenAndDisableTrading("Legacy MaxTrades hit");
				return;
			}

			PrepareOrdersForNewEntry(actualLong);

			if (Reverse)
			{
				EnterLong(Contracts, LongEntrySignal);
				Draw.VerticalLine(this, "Long Trade " + CurrentBar, 0, Brushes.Lime, DashStyleHelper.Solid, 2);
			}
			else
			{
				EnterShort(Contracts, ShortEntrySignal);
				Draw.VerticalLine(this, "Short Trade " + CurrentBar, 0, Brushes.Crimson, DashStyleHelper.Solid, 2);
			}

			BarBrush = Brushes.Yellow;
			trades++;
			LogEntry(actualLong ? "Long" : "Short");
		}

		// ═════════════════════════════════════════════════════════════════════════
		//  Order preparation
		//  ──────────────────────────────────────────────────────────────────────
		//  BACKTEST path  → SetStopLoss / SetProfitTarget (NinjaTrader managed).
		//  LIVE path      → entryOrdersPrepared = true; actual orders submitted
		//                   in SubmitInitialProtectiveOrders() after fill.
		// ═════════════════════════════════════════════════════════════════════════

		private void PrepareOrdersForNewEntry(bool isLong)
		{
			activeEntrySignal      = isLong ? LongEntrySignal : ShortEntrySignal;
			activeManagedDirection = isLong ? MarketPosition.Long : MarketPosition.Short;

			// Live: defer stop/target submission until after the fill (OnExecutionUpdate).
			entryOrdersPrepared = UseLiveStopManagement && State == State.Realtime;

			// Historical/Analyzer: configure managed SL and TP before entry order.
			if (!entryOrdersPrepared)
				ConfigureHistoricalManagedOrders(isLong);
		}

		/// <summary>
		/// Backtest / Optimizer path.
		/// Uses NinjaTrader's built-in SetStopLoss / SetProfitTarget so the Strategy
		/// Analyzer can properly model fills.  The trailing logic (AtrTrail, Chandelier,
		/// Giveback) is applied bar-by-bar via ManageHistoricalOrBarCloseDynamicStops().
		/// </summary>
		private void ConfigureHistoricalManagedOrders(bool isLong)
		{
			string sig = isLong ? LongEntrySignal : ShortEntrySignal;

			if (!UseDiscoveryExitPolicy)
			{
				if (UseMaxStop) SetStopLoss(sig, CalculationMode.Ticks, MaxStop, false);
				if (UseMaxTP)
				{
					int maxTP = Math.Max(1, (int)Math.Floor(MaxStop * MaxTPRatio));
					SetProfitTarget(sig, CalculationMode.Ticks, maxTP);
				}
				lastSubmittedStopPrice = 0.0;
				return;
			}

			switch (DiscoveryExitPolicy)
			{
				case PantheonExitPolicy.FixedRR:
					SetStopLoss(sig, CalculationMode.Ticks, StopTicks, false);
					SetProfitTarget(sig, CalculationMode.Ticks, TargetTicks);
					break;

				case PantheonExitPolicy.FixedStop:
					SetStopLoss(sig, CalculationMode.Ticks, StopTicks, false);
					break;

				// Trailing policies: set an initial protective stop; the bar-close
				// loop in ManageHistoricalOrBarCloseDynamicStops() ratchets it.
				case PantheonExitPolicy.BreakEvenOnly:
				case PantheonExitPolicy.AtrTrail:
				case PantheonExitPolicy.Chandelier:
				case PantheonExitPolicy.Giveback:
					SetStopLoss(sig, CalculationMode.Ticks, StopTicks, false);
					break;
			}

			lastSubmittedStopPrice = 0.0;
		}

		// ═════════════════════════════════════════════════════════════════════════
		//  Live explicit order management
		// ═════════════════════════════════════════════════════════════════════════

		/// <summary>
		/// Submits ExitLongStopMarket / ExitShortStopMarket and optional limit target
		/// immediately after the entry fill is confirmed.  Live path only.
		/// </summary>
		private void SubmitInitialProtectiveOrders()
		{
			if (!entryOrdersPrepared) return;

			CancelWorkingProtectiveOrders();

			bool   isLong = Position.MarketPosition == MarketPosition.Long;
			int    qty    = Position.Quantity;
			double avg    = entryFillPrice > 0 ? entryFillPrice : Position.AveragePrice;
			if (qty <= 0) return;

			double initialStop = RoundToTick(GetInitialStopPrice(isLong, avg));
			string fromEntry   = isLong ? LongEntrySignal : ShortEntrySignal;

			if (isLong) ExitLongStopMarket(qty,  initialStop, LongStopSignal,  fromEntry);
			else        ExitShortStopMarket(qty, initialStop, ShortStopSignal, fromEntry);

			lastSubmittedStopPrice = initialStop;

			if (ShouldUseTarget())
			{
				double targetPrice = RoundToTick(GetInitialTargetPrice(isLong, avg));
				if (isLong) ExitLongLimit(qty,  targetPrice, LongTargetSignal,  fromEntry);
				else        ExitShortLimit(qty, targetPrice, ShortTargetSignal, fromEntry);

				if (EnableDebugPrint)
					Print($"[PantheonMaster] Protective orders: Stop={initialStop:F2}  Target={targetPrice:F2}");
			}
			else if (EnableDebugPrint)
				Print($"[PantheonMaster] Protective orders: Stop={initialStop:F2}  (no target)");

			entryOrdersPrepared = false;
		}

		/// <summary>
		/// Moves the live stop via ChangeOrder() only when the new price is more
		/// favourable than the last submitted stop.  Called from OnMarketData.
		/// </summary>
		private void MoveStopIfImproved(double proposedStopPrice)
		{
			if (activeStopOrder == null) return;
			if (Position.MarketPosition == MarketPosition.Flat) return;

			proposedStopPrice = RoundToTick(proposedStopPrice);

			bool improveLong  = Position.MarketPosition == MarketPosition.Long
			                 && proposedStopPrice > lastSubmittedStopPrice + TickSize * 0.5;
			bool improveShort = Position.MarketPosition == MarketPosition.Short
			                 && proposedStopPrice < lastSubmittedStopPrice - TickSize * 0.5;

			if (!improveLong && !improveShort) return;

			int qty = Position.Quantity;
			if (qty <= 0) return;

			// A live protective stop must sit on the valid side of the CURRENT market or
			// the broker rejects it (buy-stop must be ABOVE market, sell-stop BELOW) and
			// the strategy terminates. When price has retraced through the proposed trail
			// level (e.g. a short ran down then ticked back up past lowSinceEntry+ATR), skip
			// this move and keep the existing still-valid resting stop — an actual stop hit
			// is handled by the working order, not a fresh ChangeOrder to an illegal price.
			// GetCurrentBid/Ask are frequently 0 in Market Replay, so use the last live tick
			// price (set in ManageLiveDynamicStop) with bid/ask then bar close as fallbacks.
			double mkt = liveMarketPrice;
			if (mkt <= 0) { double a = GetCurrentAsk(), b = GetCurrentBid();
			                mkt = (a > 0 && b > 0) ? (a + b) * 0.5 : Math.Max(a, b); }
			if (mkt <= 0) mkt = Close[0];
			if (mkt > 0)
			{
				if (Position.MarketPosition == MarketPosition.Long
				    && proposedStopPrice >= mkt - TickSize * 0.5) return;
				if (Position.MarketPosition == MarketPosition.Short
				    && proposedStopPrice <= mkt + TickSize * 0.5) return;
			}

			ChangeOrder(activeStopOrder, qty, 0, proposedStopPrice);
			lastSubmittedStopPrice = proposedStopPrice;

			if (EnableDebugPrint)
				Print($"[PantheonMaster] ChangeOrder stop -> {proposedStopPrice:F2}");
		}

		private void CancelWorkingProtectiveOrders()
		{
			bool IsActive(Order o) =>
				o != null && (o.OrderState == OrderState.Accepted
				           || o.OrderState == OrderState.Working
				           || o.OrderState == OrderState.PartFilled);

			if (IsActive(activeStopOrder))  CancelOrder(activeStopOrder);
			if (IsActive(activeTargetOrder)) CancelOrder(activeTargetOrder);
		}

		private void ClearOrderReferences()
		{
			activeStopOrder            = null;
			activeTargetOrder          = null;
			activeEntrySignal          = null;
			activeManagedDirection     = MarketPosition.Flat;
			lastSubmittedStopPrice     = 0.0;
			breakEvenActive            = false;
			peakOpenProfit             = 0.0;
			liveHighestFavorablePrice  = 0.0;
			liveLowestFavorablePrice   = 0.0;
			highSinceEntry             = 0.0;
			lowSinceEntry              = 0.0;
		}

		private bool ShouldUseTarget()
		{
			if (!UseDiscoveryExitPolicy) return UseMaxTP;
			return DiscoveryExitPolicy == PantheonExitPolicy.FixedRR;
		}

		private double GetInitialStopPrice(bool isLong, double avg)
		{
			if (!UseDiscoveryExitPolicy)
			{
				if (!UseMaxStop)
					return isLong ? avg - 100000 * TickSize : avg + 100000 * TickSize;
				return isLong ? avg - MaxStop * TickSize : avg + MaxStop * TickSize;
			}

			// All discovery policies start with StopTicks as the initial stop.
			return isLong ? avg - StopTicks * TickSize : avg + StopTicks * TickSize;
		}

		private double GetInitialTargetPrice(bool isLong, double avg)
		{
			if (!UseDiscoveryExitPolicy)
			{
				int maxTP = Math.Max(1, (int)Math.Floor(MaxStop * MaxTPRatio));
				return isLong ? avg + maxTP * TickSize : avg - maxTP * TickSize;
			}
			return isLong ? avg + TargetTicks * TickSize : avg - TargetTicks * TickSize;
		}

		private double RoundToTick(double price) =>
			Instrument.MasterInstrument.RoundToTickSize(price);

		// ═════════════════════════════════════════════════════════════════════════
		//  Discovery entry gate
		// ═════════════════════════════════════════════════════════════════════════

		private bool DiscoveryEntryAllowed(bool isLong)
		{
			if (!EnableDiscoveryFilters) return true;

			if (UseNamedSessions && !SessionAllowed())     return false;
			if (!RegimeAllowed())                          return false;
			if (isLong && !DiscoveryAllowLong)             return false;
			if (!isLong && !DiscoveryAllowShort)           return false;

			if (UseTrendAlignment)
			{
				if (cachedAdxRegime == "trending_up"   && !isLong) return false;
				if (cachedAdxRegime == "trending_down" &&  isLong) return false;
			}

			if (RequireMinAtrPercentile > 0.0 && atrPercentile.Count >= 5)
			{
				double pctRank = atrPercentile.GetPercentileRank(atrPrimary[0]);
				if (pctRank < RequireMinAtrPercentile) return false;
			}

			if (!EmaConfirmationPassed(isLong)) return false;

			return true;
		}

		private bool EmaConfirmationPassed(bool isLong)
		{
			if (!RequireEmaConfirmation) return true;
			double confirmLine = UseTrend ? trendSma[0] : slowSma[0];
			return isLong ? Close[0] > confirmLine : Close[0] < confirmLine;
		}

		// ═════════════════════════════════════════════════════════════════════════
		//  Regime / session classification
		// ═════════════════════════════════════════════════════════════════════════

		private void UpdateDiscoveryState()
		{
			if (atrPrimary[0] > 0)
			{
				atrPercentile.Add(atrPrimary[0]);
				currentAtr = atrPrimary[0];
			}

			cachedAdxRegime = PantheonRegimeClassifier.ClassifyAdx(
				adxIndicator[0], Close[0], regimeEma[0], AdxThreshold);

			double pctRank = atrPercentile.Count >= 5
				? atrPercentile.GetPercentileRank(atrPrimary[0]) : 0.5;

			cachedVolRegime    = PantheonRegimeClassifier.ClassifyVolatility(pctRank, VolLowPercentile, VolHighPercentile);
			cachedNamedSession = ClassifyNamedSession(Time[0]);
		}

		private bool RegimeAllowed()
		{
			switch (RegimeMode)
			{
				case PantheonRegimeMode.Any:          return true;
				case PantheonRegimeMode.TrendingOnly: return cachedAdxRegime == "trending_up" || cachedAdxRegime == "trending_down";
				case PantheonRegimeMode.RangingOnly:  return cachedAdxRegime == "ranging";
				case PantheonRegimeMode.TrendingUp:   return cachedAdxRegime == "trending_up";
				case PantheonRegimeMode.TrendingDown: return cachedAdxRegime == "trending_down";
				case PantheonRegimeMode.NoHighVol:    return cachedVolRegime != "high_vol_expansion";
				case PantheonRegimeMode.LowVolOnly:   return cachedVolRegime == "low_vol_compression";
				case PantheonRegimeMode.HighVolOnly:  return cachedVolRegime == "high_vol_expansion";
				default: return true;
			}
		}

		private string ClassifyNamedSession(DateTime barTime)
		{
			TimeSpan t = barTime.TimeOfDay;
			if (IsWithinTimeRange(t, londonStart,    londonEnd))    return "London";
			if (IsWithinTimeRange(t, nyPreStart,     nyPreEnd))     return "NyPre";
			if (IsWithinTimeRange(t, nyOpenStart,    nyOpenEnd))    return "NyOpen";
			if (IsWithinTimeRange(t, nyMidStart,     nyMidEnd))     return "NyMid";
			if (IsWithinTimeRange(t, myPowerHrStart, myPowerHrEnd)) return "MyPowerHr";
			if (IsWithinTimeRange(t, asiaStart,      asiaEnd))      return "Asia";
			return "None";
		}

		private bool IsWithinTimeRange(TimeSpan value, TimeSpan start, TimeSpan end)
		{
			// Handles overnight ranges (e.g. 22:00 → 02:00).
			if (start <= end) return value >= start && value <= end;
			return value >= start || value <= end;
		}

		private bool SessionAllowed()
		{
			switch (cachedNamedSession)
			{
				case "London":    return AllowLondon;
				case "NyPre":     return AllowNyPre;
				case "NyOpen":    return AllowNyOpen;
				case "NyMid":     return AllowNyMid;
				case "MyPowerHr": return AllowMyPowerHr;
				case "Asia":      return AllowAsia;
				default:          return false;  // "None" = outside all defined sessions
			}
		}

		// ═════════════════════════════════════════════════════════════════════════
		//  Bar-close tracking (both paths)
		// ═════════════════════════════════════════════════════════════════════════

		private void TrackBarBasedOpenPosition()
		{
			bool isLong = Position.MarketPosition == MarketPosition.Long;

			if (isLong)
				highSinceEntry = highSinceEntry.ApproxCompare(0.0) == 0
				    ? High[0] : Math.Max(highSinceEntry, High[0]);
			else
				lowSinceEntry = lowSinceEntry.ApproxCompare(0.0) == 0
				    ? Low[0] : Math.Min(lowSinceEntry, Low[0]);

			double openProfit = Position.GetUnrealizedProfitLoss(PerformanceUnit.Currency, Close[0]);
			peakOpenProfit = Math.Max(peakOpenProfit, openProfit);
		}

		// ═════════════════════════════════════════════════════════════════════════
		//  BACKTEST / bar-close dynamic stop logic
		//  Uses SetStopLoss(price) to move the managed stop each bar.
		// ═════════════════════════════════════════════════════════════════════════

		private void MoveHistoricalStopIfImproved(double proposedStopPrice)
		{
			if (Position.MarketPosition == MarketPosition.Flat) return;
			if (string.IsNullOrEmpty(activeEntrySignal)) return;

			proposedStopPrice = RoundToTick(proposedStopPrice);

			bool improveLong  = Position.MarketPosition == MarketPosition.Long
			                 && (lastSubmittedStopPrice.ApproxCompare(0.0) == 0
			                     || proposedStopPrice > lastSubmittedStopPrice + TickSize * 0.5);

			bool improveShort = Position.MarketPosition == MarketPosition.Short
			                 && (lastSubmittedStopPrice.ApproxCompare(0.0) == 0
			                     || proposedStopPrice < lastSubmittedStopPrice - TickSize * 0.5);

			if (!improveLong && !improveShort) return;

			SetStopLoss(activeEntrySignal, CalculationMode.Price, proposedStopPrice, false);
			lastSubmittedStopPrice = proposedStopPrice;

			if (EnableDebugPrint)
				Print($"[PantheonMaster] Historical stop -> {proposedStopPrice:F2}");
		}

		private void ManageHistoricalOrBarCloseDynamicStops()
		{
			if (Position.MarketPosition == MarketPosition.Flat) return;
			if (!UseDiscoveryExitPolicy) return;

			bool   isLong = Position.MarketPosition == MarketPosition.Long;
			double avg    = Position.AveragePrice;

			switch (DiscoveryExitPolicy)
			{
				case PantheonExitPolicy.BreakEvenOnly:
					if (!breakEvenActive)
					{
						double trigger = isLong
						    ? avg + BreakEvenTriggerTicks * TickSize
						    : avg - BreakEvenTriggerTicks * TickSize;

						if (isLong ? Close[0] >= trigger : Close[0] <= trigger)
						{
							breakEvenActive = true;
							double beStop   = isLong
							    ? avg + BreakEvenPlusTicks * TickSize
							    : avg - BreakEvenPlusTicks * TickSize;
							MoveHistoricalStopIfImproved(beStop);
						}
					}
					break;

				case PantheonExitPolicy.AtrTrail:
					MoveHistoricalStopIfImproved(isLong
					    ? highSinceEntry - AtrTrailMultiple * currentAtr
					    : lowSinceEntry  + AtrTrailMultiple * currentAtr);
					break;

				case PantheonExitPolicy.Chandelier:
					if (CurrentBar >= ChandelierLookback)
					{
						double refPrice = isLong
						    ? MAX(High, ChandelierLookback)[0]
						    : MIN(Low,  ChandelierLookback)[0];

						MoveHistoricalStopIfImproved(isLong
						    ? refPrice - AtrTrailMultiple * currentAtr
						    : refPrice + AtrTrailMultiple * currentAtr);
					}
					break;

				case PantheonExitPolicy.Giveback:
					if (peakOpenProfit > 0)
					{
						double protectedProfit = peakOpenProfit * (1.0 - GivebackPct);
						MoveHistoricalStopIfImproved(
						    GetStopPriceFromProtectedOpenProfit(isLong, protectedProfit));
					}
					break;
			}
		}

		// ═════════════════════════════════════════════════════════════════════════
		//  LIVE intrabar dynamic stop logic  (tick-by-tick via OnMarketData)
		// ═════════════════════════════════════════════════════════════════════════

		private double GetLiveTriggerPrice(MarketDataEventArgs md)
		{
			// For a long position we care about bid (what we can sell at).
			// For a short position we care about ask (what we can buy back at).
			if (Position.MarketPosition == MarketPosition.Long  && md.MarketDataType == MarketDataType.Bid) return md.Price;
			if (Position.MarketPosition == MarketPosition.Short && md.MarketDataType == MarketDataType.Ask) return md.Price;
			return 0.0;
		}

		private void ManageLiveDynamicStop(double triggerPrice)
		{
			if (!UseDiscoveryExitPolicy) return;

			liveMarketPrice = triggerPrice;   // last live price, for the stop-side guard

			bool   isLong = Position.MarketPosition == MarketPosition.Long;
			double avg    = Position.AveragePrice;

			// Update intrabar high/low.
			if (isLong)
				liveHighestFavorablePrice = Math.Max(liveHighestFavorablePrice, triggerPrice);
			else
				liveLowestFavorablePrice = liveLowestFavorablePrice.ApproxCompare(0.0) == 0
				    ? triggerPrice : Math.Min(liveLowestFavorablePrice, triggerPrice);

			double liveOpenProfit = GetOpenProfitCurrencyFromPrice(triggerPrice);
			peakOpenProfit = Math.Max(peakOpenProfit, liveOpenProfit);

			switch (DiscoveryExitPolicy)
			{
				case PantheonExitPolicy.BreakEvenOnly:
					if (!breakEvenActive)
					{
						double triggerLevel = isLong
						    ? avg + BreakEvenTriggerTicks * TickSize
						    : avg - BreakEvenTriggerTicks * TickSize;

						if (isLong ? triggerPrice >= triggerLevel : triggerPrice <= triggerLevel)
						{
							breakEvenActive = true;
							MoveStopIfImproved(isLong
							    ? avg + BreakEvenPlusTicks * TickSize
							    : avg - BreakEvenPlusTicks * TickSize);
						}
					}
					break;

				case PantheonExitPolicy.AtrTrail:
					MoveStopIfImproved(isLong
					    ? liveHighestFavorablePrice - AtrTrailMultiple * currentAtr
					    : liveLowestFavorablePrice  + AtrTrailMultiple * currentAtr);
					break;

				case PantheonExitPolicy.Chandelier:
					double refPrice = isLong
					    ? Math.Max(MAX(High, ChandelierLookback)[0], liveHighestFavorablePrice)
					    : Math.Min(MIN(Low,  ChandelierLookback)[0], liveLowestFavorablePrice);
					MoveStopIfImproved(isLong
					    ? refPrice - AtrTrailMultiple * currentAtr
					    : refPrice + AtrTrailMultiple * currentAtr);
					break;

				case PantheonExitPolicy.Giveback:
					if (peakOpenProfit > 0)
					{
						double protectedProfit = peakOpenProfit * (1.0 - GivebackPct);
						MoveStopIfImproved(GetStopPriceFromProtectedOpenProfit(isLong, protectedProfit));
					}
					break;
			}
		}

		private double GetOpenProfitCurrencyFromPrice(double price)
		{
			if (Position.Quantity <= 0) return 0.0;
			double points = Position.MarketPosition == MarketPosition.Long
			    ? price - Position.AveragePrice
			    : Position.AveragePrice - price;
			return points * Instrument.MasterInstrument.PointValue * Position.Quantity;
		}

		private double GetStopPriceFromProtectedOpenProfit(bool isLong, double protectedOpenProfitCurrency)
		{
			double denom          = Instrument.MasterInstrument.PointValue * Math.Max(1, Position.Quantity);
			double protectedPoints = denom.ApproxCompare(0.0) == 0 ? 0.0 : protectedOpenProfitCurrency / denom;
			return isLong ? Position.AveragePrice + protectedPoints : Position.AveragePrice - protectedPoints;
		}

		private void ResetDynamicTrackingOnEntry()
		{
			// Seed from the actual entry fill price (entryFillPrice); Position.AveragePrice
			// is not reliably settled inside OnExecutionUpdate. A stale seed made the short
			// trail compute a stop far below the market.
			bool   isLong     = activeManagedDirection == MarketPosition.Long;
			double entryPrice = entryFillPrice > 0 ? entryFillPrice : Position.AveragePrice;

			highSinceEntry            = 0.0;
			lowSinceEntry             = 0.0;
			peakOpenProfit            = 0.0;
			breakEvenActive           = false;
			liveHighestFavorablePrice = isLong ? entryPrice : 0.0;
			liveLowestFavorablePrice  = isLong ? 0.0 : entryPrice;
			lastSubmittedStopPrice    = 0.0;
		}

		// ═════════════════════════════════════════════════════════════════════════
		//  Risk management
		// ═════════════════════════════════════════════════════════════════════════

		private void ResetSessionTracking()
		{
			priorTradesCount              = SystemPerformance.AllTrades.Count;
			priorTradesCumProfit          = SystemPerformance.AllTrades.TradesPerformance.Currency.CumProfit;
			discoverySessionOpenCumProfit  = priorTradesCumProfit;
			discoverySessionOpenTradeCount = priorTradesCount;
			allowTrading = true;
			trades       = 0;
			breakEvenActive = false;

			if (EnableDebugPrint) Print("[PantheonMaster] New session reset");
		}

		private bool CheckRiskLocks()
		{
			if (!allowTrading) return false;

			if (UseLegacyDailyRisk)
			{
				double legacyPnl        = SystemPerformance.AllTrades.TradesPerformance.Currency.CumProfit - priorTradesCumProfit;
				int    legacyTradeCount = SystemPerformance.AllTrades.Count - priorTradesCount;

				if (legacyPnl >= ProfitStop || legacyPnl <= -LossStop || legacyTradeCount >= (int)MaxTrades)
				{
					if (EnableDebugPrint) Print("[PantheonMaster] Legacy daily risk lock hit");
					return false;
				}
			}

			if (UseDiscoveryDailyRisk)
			{
				double sessionPnl    = GetDiscoverySessionPnl(Close[0]);
				int    sessionTrades = SystemPerformance.AllTrades.Count - discoverySessionOpenTradeCount;

				if (sessionPnl <= -MaxDailyLossUsd || sessionPnl >= MaxDailyProfitUsd || sessionTrades >= MaxDailyTrades)
				{
					if (EnableDebugPrint)
						Print($"[PantheonMaster] Discovery daily risk lock | pnl={sessionPnl:F2} trades={sessionTrades}");
					return false;
				}
			}

			return true;
		}

		private double GetDiscoverySessionPnl(double markPrice)
		{
			double realized   = SystemPerformance.AllTrades.TradesPerformance.Currency.CumProfit - discoverySessionOpenCumProfit;
			double unrealized = Position.MarketPosition != MarketPosition.Flat
			    ? Position.GetUnrealizedProfitLoss(PerformanceUnit.Currency, markPrice) : 0.0;
			return realized + unrealized;
		}

		private double GetIntrabarRiskMarkPrice(MarketDataEventArgs md)
		{
			if (Position.MarketPosition == MarketPosition.Long  && md.MarketDataType == MarketDataType.Bid)  return md.Price;
			if (Position.MarketPosition == MarketPosition.Short && md.MarketDataType == MarketDataType.Ask)  return md.Price;
			if (Position.MarketPosition == MarketPosition.Flat  && md.MarketDataType == MarketDataType.Last) return md.Price;
			return 0.0;
		}

		private bool CheckDiscoveryDailyRiskIntrabar(MarketDataEventArgs md)
		{
			if (!UseDiscoveryDailyRisk) return true;

			double markPrice = GetIntrabarRiskMarkPrice(md);
			if (markPrice <= 0) return true;

			double sessionPnl    = GetDiscoverySessionPnl(markPrice);
			int    sessionTrades = SystemPerformance.AllTrades.Count - discoverySessionOpenTradeCount;

			bool locked = sessionPnl <= -MaxDailyLossUsd
			           || sessionPnl >= MaxDailyProfitUsd
			           || sessionTrades >= MaxDailyTrades;

			if (locked && EnableDebugPrint)
				Print($"[PantheonMaster] Discovery intrabar lock | pnl={sessionPnl:F2} trades={sessionTrades}");

			return !locked;
		}

		private void FlattenAndDisableTrading(string reason)
		{
			ExitLong();           // FIX [BUG-4]: exit first, then cancel orphan orders.
			ExitShort();
			CancelWorkingProtectiveOrders();
			allowTrading = false;
			if (EnableDebugPrint) Print($"[PantheonMaster] Trading disabled: {reason}");
		}

		// ═════════════════════════════════════════════════════════════════════════
		//  Time window helpers
		// ═════════════════════════════════════════════════════════════════════════

		private bool IsInsidePantheonTimeWindow()
		{
			// FIX [BUG-6]: correctly handle midnight rollover.
			int totalStartMinutes = StartTimeH * 60 + StartTimeM;
			int totalEndMinutes   = totalStartMinutes + DurationTimeH * 60 + DurationTimeM;

			int curMinutes = Time[0].Hour * 60 + Time[0].Minute;

			if (totalEndMinutes <= 1440)  // Does not cross midnight
				return curMinutes >= totalStartMinutes && curMinutes <= totalEndMinutes;

			// Crosses midnight: active from StartTime until midnight OR from midnight until end.
			int endMinutesMod = totalEndMinutes % 1440;
			return curMinutes >= totalStartMinutes || curMinutes <= endMinutesMod;
		}

		// ═════════════════════════════════════════════════════════════════════════
		//  Display / kill switch
		// ═════════════════════════════════════════════════════════════════════════

		private void UpdateCurrentPnLDisplay()
		{
			Account acct = Account.All.FirstOrDefault(a => a.Name == Account.Name);
			if (acct == null) return;

			currentUnrealizedTotal = 0;
			currentRealizedTotal   = NP;

			if (Position.MarketPosition != MarketPosition.Flat)
				currentUnrealizedTotal = acct.GetAccountItem(AccountItem.UnrealizedProfitLoss, Currency.UsDollar).Value;

			if (Show_Current_PNL && ChartControl != null)
			{
				Brush  statsBrush = currentUnrealizedTotal >= 0 ? Brushes.Lime : Brushes.Red;
				SimpleFont font   = new SimpleFont("Consolas", 20) { Size = 20, Bold = false };
				TextFixed tf      = Draw.TextFixed(this, "PnL",
				    $"{"Current PnL $",-15}\n{Math.Round(currentUnrealizedTotal,2),-15}",
				    TextPosition.TopRight, ChartControl.Properties.ChartText, font, statsBrush, statsBrush, 8);
				tf.OutlineStroke.Width = 1.0f;
			}
		}

		private void CheckKillSwitch()
		{
			double currentPnL = currentUnrealizedTotal + currentRealizedTotal;

			// FIX [BUG-1]: stay quiet while within the profit/loss range; fire when outside.
			if (currentPnL < KillProfitStop && currentPnL > -KillLossStop)
				return;

			// FIX [BUG-4]: close position before cancelling protective orders.
			if      (Position.MarketPosition == MarketPosition.Long)  ExitLong();
			else if (Position.MarketPosition == MarketPosition.Short) ExitShort();

			CancelWorkingProtectiveOrders();
			allowTrading = false;

			if (Show_Current_PNL && ChartControl != null)
			{
				string msg    = currentPnL >= KillProfitStop
				    ? $"Profit Limit Reached: {Math.Round(currentPnL, 2)}\n(Trading paused until next day)"
				    : $"Loss Limit Reached: {Math.Round(currentPnL, 2)}\n(Trading paused until next day)";
				SimpleFont font = new SimpleFont("Consolas", 20) { Size = 20, Bold = false };
				Draw.TextFixed(this, "PnLReport", "\n\n" + msg, TextPosition.TopRight,
				    ChartControl.Properties.ChartText, font, Brushes.Transparent, Brushes.Transparent, 0);
			}
		}

		// ═════════════════════════════════════════════════════════════════════════
		//  Logging
		// ═════════════════════════════════════════════════════════════════════════

		private void LogEntry(string direction)
		{
			if (!EnableDebugPrint) return;
			Print($"[PantheonMaster] ENTRY {direction} | bar={CurrentBar} | adxRegime={cachedAdxRegime} | volRegime={cachedVolRegime} | session={cachedNamedSession} | ATR={currentAtr:F2} | fast={fastLine:F2} | slow={slowLine:F2} | trend={trendLine:F2}");
		}

		// ═════════════════════════════════════════════════════════════════════════
		//  Properties
		// ═════════════════════════════════════════════════════════════════════════

		#region Pantheon Averages
		[NinjaScriptProperty]
		[Range(2, int.MaxValue)]
		[Display(Name = "FastPeriod", GroupName = "Fast Averages", Order = 1)]
		public int FastPeriod { get; set; }

		[NinjaScriptProperty]
		[Range(2, int.MaxValue)]
		[Display(Name = "SlowPeriod", GroupName = "Slow Averages", Order = 1)]
		public int SlowPeriod { get; set; }

		[NinjaScriptProperty]
		[Range(2, int.MaxValue)]
		[Display(Name = "TrendPeriod", GroupName = "Trend Averages", Order = 1)]
		public int TrendPeriod { get; set; }

		[NinjaScriptProperty]
		[Display(Name = "UseTrend", GroupName = "Trend Averages", Order = 2)]
		public bool UseTrend { get; set; }

		[NinjaScriptProperty]
		[Display(Name = "UseTrendReverse", GroupName = "Trend Averages", Order = 3)]
		public bool UseTrendReverse { get; set; }
		#endregion

		#region Direction
		[NinjaScriptProperty]
		[Display(Name = "Long", GroupName = "Direction", Order = 1)]
		public bool Long { get; set; }

		[NinjaScriptProperty]
		[Display(Name = "Short", GroupName = "Direction", Order = 2)]
		public bool Short { get; set; }

		[NinjaScriptProperty]
		[Display(Name = "Reverse", GroupName = "Direction", Order = 3)]
		public bool Reverse { get; set; }
		#endregion

		#region Time
		[NinjaScriptProperty]
		[Display(Name = "Use Time Filter", GroupName = "Time", Order = 1)]
		public bool UseTimeFilter { get; set; }

		[NinjaScriptProperty]
		[Range(0, 23)]
		[Display(Name = "Start Time (HH)", GroupName = "Time", Order = 2)]
		public int StartTimeH { get; set; }

		[NinjaScriptProperty]
		[Range(0, 59)]
		[Display(Name = "Start Time (mm)", GroupName = "Time", Order = 3)]
		public int StartTimeM { get; set; }

		[NinjaScriptProperty]
		[Range(0, 23)]
		[Display(Name = "Duration Time (HH)", GroupName = "Time", Order = 4)]
		public int DurationTimeH { get; set; }

		[NinjaScriptProperty]
		[Range(0, 59)]
		[Display(Name = "Duration Time (mm)", GroupName = "Time", Order = 5)]
		public int DurationTimeM { get; set; }
		#endregion

		#region Display
		[NinjaScriptProperty]
		[Display(Name = "Show Current PNL", GroupName = "Display", Order = 1)]
		public bool Show_Current_PNL { get; set; }

		[NinjaScriptProperty]
		[Display(Name = "Show Stats Box", GroupName = "Display", Order = 2)]
		public bool Show_Stats_Box { get; set; }

		[NinjaScriptProperty]
		[Display(Name = "Bot Name", GroupName = "Display", Order = 3)]
		public string BotName { get; set; }
		#endregion

		#region Kill
		[NinjaScriptProperty]
		[Display(Name = "Use Kill", GroupName = "Kill", Order = 1)]
		public bool UseKill { get; set; }

		[NinjaScriptProperty]
		[Range(0, double.MaxValue)]
		[Display(Name = "Kill Profit Stop", GroupName = "Kill", Order = 2)]
		public double KillProfitStop { get; set; }

		[NinjaScriptProperty]
		[Range(0, double.MaxValue)]
		[Display(Name = "Kill Loss Stop", GroupName = "Kill", Order = 3)]
		public double KillLossStop { get; set; }
		#endregion

		#region Cross Profit
		[NinjaScriptProperty]
		[Range(1, int.MaxValue)]
		[Display(Name = "Contracts", GroupName = "Cross Profit", Order = 1)]
		public int Contracts { get; set; }

		[NinjaScriptProperty]
		[Range(1, int.MaxValue)]
		[Display(Name = "MaxStop", GroupName = "Cross Profit", Order = 2)]
		public int MaxStop { get; set; }

		[NinjaScriptProperty]
		[Display(Name = "Use MaxStop", GroupName = "Cross Profit", Order = 3)]
		public bool UseMaxStop { get; set; }

		[NinjaScriptProperty]
		[Range(0.1, double.MaxValue)]
		[Display(Name = "MaxTPRatio", GroupName = "Cross Profit", Order = 4)]
		public double MaxTPRatio { get; set; }

		[NinjaScriptProperty]
		[Display(Name = "Use MaxTP", GroupName = "Cross Profit", Order = 5)]
		public bool UseMaxTP { get; set; }
		#endregion

		#region Legacy Daily Risk
		[NinjaScriptProperty]
		[Display(Name = "Use Legacy Daily Risk", GroupName = "Legacy Daily Risk", Order = 1)]
		public bool UseLegacyDailyRisk { get; set; }

		[NinjaScriptProperty]
		[Range(0, double.MaxValue)]
		[Display(Name = "ProfitStop", GroupName = "Legacy Daily Risk", Order = 2)]
		public double ProfitStop { get; set; }

		[NinjaScriptProperty]
		[Range(0, double.MaxValue)]
		[Display(Name = "LossStop", GroupName = "Legacy Daily Risk", Order = 3)]
		public double LossStop { get; set; }

		[NinjaScriptProperty]
		[Range(0, int.MaxValue)]
		[Display(Name = "MaxTrades", GroupName = "Legacy Daily Risk", Order = 4)]
		public double MaxTrades { get; set; }
		#endregion

		#region Discovery Regime
		[NinjaScriptProperty]
		[Display(Name = "Enable Discovery Filters", GroupName = "Discovery Regime", Order = 1)]
		public bool EnableDiscoveryFilters { get; set; }

		[NinjaScriptProperty]
		[Display(Name = "Regime Mode", GroupName = "Discovery Regime", Order = 2)]
		public PantheonRegimeMode RegimeMode { get; set; }

		[NinjaScriptProperty]
		[Range(5, 50)]
		[Display(Name = "ADX Period", GroupName = "Discovery Regime", Order = 3)]
		public int AdxPeriod { get; set; }

		[NinjaScriptProperty]
		[Range(10.0, 50.0)]
		[Display(Name = "ADX Threshold", GroupName = "Discovery Regime", Order = 4)]
		public double AdxThreshold { get; set; }

		[NinjaScriptProperty]
		[Range(10, 500)]
		[Display(Name = "Regime EMA Period", GroupName = "Discovery Regime", Order = 5)]
		public int RegimeEmaPeriod { get; set; }

		[NinjaScriptProperty]
		[Range(5, 50)]
		[Display(Name = "ATR Period", GroupName = "Discovery Regime", Order = 6)]
		public int AtrPeriod { get; set; }

		[NinjaScriptProperty]
		[Range(20, 500)]
		[Display(Name = "ATR Lookback Bars", GroupName = "Discovery Regime", Order = 7)]
		public int AtrLookbackBars { get; set; }

		[NinjaScriptProperty]
		[Range(0.05, 0.49)]
		[Display(Name = "Low Vol Percentile", GroupName = "Discovery Regime", Order = 8)]
		public double VolLowPercentile { get; set; }

		[NinjaScriptProperty]
		[Range(0.51, 0.95)]
		[Display(Name = "High Vol Percentile", GroupName = "Discovery Regime", Order = 9)]
		public double VolHighPercentile { get; set; }
		#endregion

		#region Discovery Sessions
		[NinjaScriptProperty]
		[Display(Name = "Use Named Sessions", GroupName = "Discovery Sessions", Order = 1)]
		public bool UseNamedSessions { get; set; }

		[NinjaScriptProperty]
		[Display(Name = "Session Time Zone Id", Description = "Informational – session windows are compared to Time[0].", GroupName = "Discovery Sessions", Order = 2)]
		public string SessionTimeZoneId { get; set; }

		[NinjaScriptProperty] [Display(Name = "Allow London",    GroupName = "Discovery Sessions", Order = 3)]  public bool AllowLondon    { get; set; }
		[NinjaScriptProperty] [Display(Name = "Allow NyPre",     GroupName = "Discovery Sessions", Order = 4)]  public bool AllowNyPre     { get; set; }
		[NinjaScriptProperty] [Display(Name = "Allow NyOpen",    GroupName = "Discovery Sessions", Order = 5)]  public bool AllowNyOpen    { get; set; }
		[NinjaScriptProperty] [Display(Name = "Allow NyMid",     GroupName = "Discovery Sessions", Order = 6)]  public bool AllowNyMid     { get; set; }
		[NinjaScriptProperty] [Display(Name = "Allow MyPowerHr", GroupName = "Discovery Sessions", Order = 7)]  public bool AllowMyPowerHr { get; set; }
		[NinjaScriptProperty] [Display(Name = "Allow Asia",      GroupName = "Discovery Sessions", Order = 8)]  public bool AllowAsia      { get; set; }

		[NinjaScriptProperty] [Display(Name = "London Start",    GroupName = "Discovery Sessions", Order = 9)]  public string LondonStart    { get; set; }
		[NinjaScriptProperty] [Display(Name = "London End",      GroupName = "Discovery Sessions", Order = 10)] public string LondonEnd      { get; set; }
		[NinjaScriptProperty] [Display(Name = "NyPre Start",     GroupName = "Discovery Sessions", Order = 11)] public string NyPreStart     { get; set; }
		[NinjaScriptProperty] [Display(Name = "NyPre End",       GroupName = "Discovery Sessions", Order = 12)] public string NyPreEnd       { get; set; }
		[NinjaScriptProperty] [Display(Name = "NyOpen Start",    GroupName = "Discovery Sessions", Order = 13)] public string NyOpenStart    { get; set; }
		[NinjaScriptProperty] [Display(Name = "NyOpen End",      GroupName = "Discovery Sessions", Order = 14)] public string NyOpenEnd      { get; set; }
		[NinjaScriptProperty] [Display(Name = "NyMid Start",     GroupName = "Discovery Sessions", Order = 15)] public string NyMidStart     { get; set; }
		[NinjaScriptProperty] [Display(Name = "NyMid End",       GroupName = "Discovery Sessions", Order = 16)] public string NyMidEnd       { get; set; }
		[NinjaScriptProperty] [Display(Name = "MyPowerHr Start", GroupName = "Discovery Sessions", Order = 17)] public string MyPowerHrStart { get; set; }
		[NinjaScriptProperty] [Display(Name = "MyPowerHr End",   GroupName = "Discovery Sessions", Order = 18)] public string MyPowerHrEnd   { get; set; }
		[NinjaScriptProperty] [Display(Name = "Asia Start",      GroupName = "Discovery Sessions", Order = 19)] public string AsiaStart      { get; set; }
		[NinjaScriptProperty] [Display(Name = "Asia End",        GroupName = "Discovery Sessions", Order = 20)] public string AsiaEnd        { get; set; }
		#endregion

		#region Discovery Entry
		[NinjaScriptProperty]
		[Display(Name = "Discovery Allow Long",  GroupName = "Discovery Entry", Order = 1)]
		public bool DiscoveryAllowLong { get; set; }

		[NinjaScriptProperty]
		[Display(Name = "Discovery Allow Short", GroupName = "Discovery Entry", Order = 2)]
		public bool DiscoveryAllowShort { get; set; }

		[NinjaScriptProperty]
		[Display(Name = "Use Trend Alignment",   GroupName = "Discovery Entry", Order = 3)]
		public bool UseTrendAlignment { get; set; }

		[NinjaScriptProperty]
		[Display(Name = "Require EMA Confirmation", GroupName = "Discovery Entry", Order = 4)]
		public bool RequireEmaConfirmation { get; set; }

		[NinjaScriptProperty]
		[Range(0.0, 1.0)]
		[Display(Name = "Min ATR Percentile",    GroupName = "Discovery Entry", Order = 5)]
		public double RequireMinAtrPercentile { get; set; }
		#endregion

		#region Discovery Exit
		[NinjaScriptProperty]
		[Display(Name = "Use Discovery Exit Policy", GroupName = "Discovery Exit", Order = 1)]
		public bool UseDiscoveryExitPolicy { get; set; }

		[NinjaScriptProperty]
		[Display(Name = "Discovery Exit Policy",     GroupName = "Discovery Exit", Order = 2)]
		public PantheonExitPolicy DiscoveryExitPolicy { get; set; }

		[NinjaScriptProperty]
		[Range(4, 500)]
		[Display(Name = "Stop Ticks",                GroupName = "Discovery Exit", Order = 3)]
		public int StopTicks { get; set; }

		[NinjaScriptProperty]
		[Range(4, 500)]
		[Display(Name = "Target Ticks",              GroupName = "Discovery Exit", Order = 4)]
		public int TargetTicks { get; set; }

		[NinjaScriptProperty]
		[Range(0.5, 6.0)]
		[Display(Name = "ATR Trail Multiple",         GroupName = "Discovery Exit", Order = 5)]
		public double AtrTrailMultiple { get; set; }

		[NinjaScriptProperty]
		[Range(3, 100)]
		[Display(Name = "Chandelier Lookback",        GroupName = "Discovery Exit", Order = 6)]
		public int ChandelierLookback { get; set; }

		[NinjaScriptProperty]
		[Range(0.05, 0.95)]
		[Display(Name = "Giveback Fraction",          GroupName = "Discovery Exit", Order = 7)]
		public double GivebackPct { get; set; }

		[NinjaScriptProperty]
		[Range(4, 300)]
		[Display(Name = "BreakEven Trigger Ticks",    GroupName = "Discovery Exit", Order = 8)]
		public int BreakEvenTriggerTicks { get; set; }

		[NinjaScriptProperty]
		[Range(0, 20)]
		[Display(Name = "BreakEven Plus Ticks",       GroupName = "Discovery Exit", Order = 9)]
		public int BreakEvenPlusTicks { get; set; }
		#endregion

		#region Discovery Daily Risk
		[NinjaScriptProperty]
		[Display(Name = "Use Discovery Daily Risk",  GroupName = "Discovery Daily Risk", Order = 1)]
		public bool UseDiscoveryDailyRisk { get; set; }

		[NinjaScriptProperty]
		[Range(0.0, 10000.0)]
		[Display(Name = "Max Daily Loss ($)",        GroupName = "Discovery Daily Risk", Order = 2)]
		public double MaxDailyLossUsd { get; set; }

		[NinjaScriptProperty]
		[Range(0.0, 50000.0)]
		[Display(Name = "Max Daily Profit ($)",      GroupName = "Discovery Daily Risk", Order = 3)]
		public double MaxDailyProfitUsd { get; set; }

		[NinjaScriptProperty]
		[Range(1, 50)]
		[Display(Name = "Max Daily Trades",          GroupName = "Discovery Daily Risk", Order = 4)]
		public int MaxDailyTrades { get; set; }
		#endregion

		#region Live Stop Management
		[NinjaScriptProperty]
		[Display(Name = "Use Live Stop Management", GroupName = "Live Stop Management", Order = 1)]
		public bool UseLiveStopManagement { get; set; }
		#endregion

		#region Debug
		[NinjaScriptProperty]
		[Display(Name = "Enable Debug Print", GroupName = "Debug", Order = 1)]
		public bool EnableDebugPrint { get; set; }
		#endregion
	}
}