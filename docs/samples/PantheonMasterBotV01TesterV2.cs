#region Using declarations
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.ComponentModel.DataAnnotations;
using System.Linq;
using System.Text;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Input;
using System.Windows.Media;
using System.Xml.Serialization;
using NinjaTrader.Cbi;
using NinjaTrader.Gui;
using NinjaTrader.Gui.Chart;
using NinjaTrader.Gui.SuperDom;
using NinjaTrader.Gui.Tools;
using NinjaTrader.Data;
using NinjaTrader.NinjaScript;
using NinjaTrader.Core.FloatingPoint;
using NinjaTrader.NinjaScript.Indicators;
using NinjaTrader.NinjaScript.DrawingTools;

using SharpDX;
using SharpDX.Direct2D1;
using SharpDX.DirectWrite;
#endregion

namespace NinjaTrader.NinjaScript.Strategies
{
	[Gui.CategoryOrder("Background", 1)]
	[Gui.CategoryOrder("Fast Averages", 2)]
	[Gui.CategoryOrder("Slow Averages", 3)]
	[Gui.CategoryOrder("Trend Averages", 4)]
	[Gui.CategoryOrder("Direction", 5)]
	[Gui.CategoryOrder("Time", 6)]
	[Gui.CategoryOrder("Display", 7)]
	[Gui.CategoryOrder("Kill", 8)]
	[Gui.CategoryOrder("Cross Profit", 9)]
	[Gui.CategoryOrder("Risk", 10)]
	[Gui.CategoryOrder("Test", 11)]
	[Gui.CategoryOrder("Day of week", 12)]
	public class PantheonMasterBotV01TesterV2 : Strategy
	{
		private bool allowTrading;

		private double fastLine = 0;
		private double slowLine = 0;
		private double trendLine = 0;

		private int trades;

		#region RiskLogic
		private int priorTradesCount;
		private double priorTradesCumProfit;
		#endregion

		#region Moving Averages
		private SMA fastSma;
		private SMA slowSma;
		private SMA trendSma;
		#endregion

		#region image
		private SharpDX.Direct2D1.Bitmap myBitmap;
		private SharpDX.IO.NativeFileStream fileStream;
		private SharpDX.WIC.BitmapDecoder bitmapDecoder;
		private SharpDX.WIC.FormatConverter converter;
		private SharpDX.WIC.BitmapFrameDecode frame;

		private SharpDX.Direct2D1.Bitmap myBitmap2;
		private SharpDX.IO.NativeFileStream fileStream2;
		private SharpDX.WIC.BitmapDecoder bitmapDecoder2;
		private SharpDX.WIC.FormatConverter converter2;
		private SharpDX.WIC.BitmapFrameDecode frame2;

		private bool NeedToUpdate;
		#endregion

		protected override void OnStateChange()
		{
			if (State == State.SetDefaults)
			{
				Description									= @"Buy on cross up, Sell on cross down. Has a time window. has max stop";
				Name										= "PantheonMasterBotV01TesterV2";
				Calculate									= Calculate.OnBarClose;
				EntriesPerDirection							= 1;
				EntryHandling								= EntryHandling.AllEntries;
				IsExitOnSessionCloseStrategy				= true;
				ExitOnSessionCloseSeconds					= 30;
				IsFillLimitOnTouch							= false;
				MaximumBarsLookBack							= MaximumBarsLookBack.TwoHundredFiftySix;
				OrderFillResolution							= OrderFillResolution.Standard;
				Slippage									= 0;
				StartBehavior								= StartBehavior.WaitUntilFlat;
				TimeInForce									= TimeInForce.Gtc;
				TraceOrders									= false;
				RealtimeErrorHandling						= RealtimeErrorHandling.StopCancelClose;
				StopTargetHandling							= StopTargetHandling.PerEntryExecution;
				BarsRequiredToTrade							= 20;
				IsInstantiatedOnEachOptimizationIteration	= false;

				Contracts									= 1;
				MaxStop										= 200;
				UseMaxStop									= true;
				MaxTPRatio									= .5;
				UseMaxTP									= true;

				Long										= true;
				Short										= true;

				averageFast									= 50;
				averageSlow									= 200;
				averageTrend								= 300;

				Reverse										= false;

				UseTrend									= true;
				UseTrendReverse								= false;

				allowTrading								= true;

				UseTimeFilter								= true;
				StartTimeH									= 0;
				StartTimeM									= 0;
				DurationTimeH								= 1;
				DurationTimeM								= 30;

				#region Kill
				UseKill										= false;
				KillProfitStop								= 800;
				KillLossStop								= 400;
				#endregion

				#region Display
				Show_Current_PNL							= true;
				Show_Stats_Box								= true;
				#endregion

				#region RiskLogic
				ProfitStop									= 10000;
				LossStop									= 10000;
				MaxTrades									= 500;
				priorTradesCount							= 0;
				priorTradesCumProfit						= 0;
				trades										= 0;
				#endregion

				#region image
				FileName									= "panthionIcon4.png";
				FileName2									= "Oden2.png";
				IOpacity									= (float).3;
				BotName										= "PantheonMasterBotV01TesterV2";
				#endregion
			}
			else if (State == State.Configure)
			{
			}
			else if (State == State.DataLoaded)
			{
				int maxTP = (int)Math.Floor(MaxStop * MaxTPRatio);

				if (UseMaxStop)
					SetStopLoss("", CalculationMode.Ticks, MaxStop, false);

				if (UseMaxTP)
					SetProfitTarget("", CalculationMode.Ticks, maxTP);

				// Instantiate MAs once for better performance than repeated SMA(...) calls in OnBarUpdate
				fastSma = SMA(averageFast);
				slowSma = SMA(averageSlow);
				trendSma = SMA(averageTrend);

				// Chart display
				fastSma.Plots[0].Brush = Brushes.Red;
				slowSma.Plots[0].Brush = Brushes.Green;
				trendSma.Plots[0].Brush = Brushes.White;

				AddChartIndicator(fastSma);
				AddChartIndicator(slowSma);

				if (UseTrend)
					AddChartIndicator(trendSma);

				SimpleFont mf = new SimpleFont("Consolas", 20) { Size = 20, Bold = true };
				Draw.TextFixed(this, "Version", BotName, TextPosition.BottomRight, Brushes.Gold, mf, Brushes.Transparent, Brushes.Transparent, 0);
			}
			else if (State == State.Realtime)
			{
				ExitShort();
				ExitLong();

				#region RiskLogic
				priorTradesCount = SystemPerformance.AllTrades.Count;
				priorTradesCumProfit = SystemPerformance.AllTrades.TradesPerformance.Currency.CumProfit;
				#endregion
			}
			else if (State == State.Historical)
			{
				SetZOrder(-1);
			}
		}

		protected override void OnBarUpdate()
		{
			if (BarsInProgress != 0)
				return;

			if (CurrentBars[0] < 1)
				return;

			if (CurrentBar < BarsRequiredToTrade)
				return;

			int requiredBars = Math.Max(averageFast, averageSlow);
			if (UseTrend)
				requiredBars = Math.Max(requiredBars, averageTrend);

			if (CurrentBar < requiredBars)
				return;

			BarBrush = null;

			fastLine = fastSma[0];
			slowLine = slowSma[0];
			trendLine = UseTrend ? trendSma[0] : 0.0;

			#region RiskLogic
			if (Bars.IsFirstBarOfSession)
			{
				priorTradesCount = SystemPerformance.AllTrades.Count;
				priorTradesCumProfit = SystemPerformance.AllTrades.TradesPerformance.Currency.CumProfit;
				allowTrading = true;
				trades = 0;
			}

			if (SystemPerformance.AllTrades.TradesPerformance.Currency.CumProfit - priorTradesCumProfit >= ProfitStop
				|| SystemPerformance.AllTrades.TradesPerformance.Currency.CumProfit - priorTradesCumProfit <= -LossStop
				|| SystemPerformance.AllTrades.Count - priorTradesCount >= MaxTrades)
			{
				ExitShort();
				ExitLong();

				Print("Stop trade Profit: " + (SystemPerformance.AllTrades.TradesPerformance.Currency.CumProfit - priorTradesCumProfit).ToString());
				Print("Stop Num Trades: " + (SystemPerformance.AllTrades.Count - priorTradesCount).ToString());

				allowTrading = false;
				return;
			}
			#endregion

			if (UseTimeFilter)
			{
				int curTime = ToTime(Time[0]);
				int endTimeHours = (StartTimeH + DurationTimeH) <= 23 ? (StartTimeH + DurationTimeH) : 23;
				int endTimeMinutes = 0;

				if ((StartTimeM + DurationTimeM) <= 59)
				{
					endTimeMinutes = StartTimeM + DurationTimeM;
				}
				else
				{
					endTimeMinutes = StartTimeM + DurationTimeM - 60;
					endTimeHours = endTimeHours + 1;

					if (endTimeHours == 24)
					{
						endTimeMinutes = 59;
						endTimeHours = 23;
					}
				}

				int ST = ToTime(StartTimeH, StartTimeM, 0);
				int ET = ToTime(endTimeHours, endTimeMinutes, 0);

				if (curTime < ST || curTime > ET)
				{
					if (Position.MarketPosition != MarketPosition.Flat)
					{
						if (CrossAbove(fastSma, slowSma, 1) || CrossBelow(fastSma, slowSma, 1))
						{
							ExitShort();
							ExitLong();
						}
					}
					return;
				}
			}

			if (!allowTrading)
			{
				ExitShort();
				ExitLong();
				return;
			}

			if (CrossAbove(fastSma, slowSma, 1) || CrossBelow(fastSma, slowSma, 1))
			{
				if (MaxTrades <= trades)
				{
					ExitShort();
					ExitLong();
					Print("Current Max Trades Hit Stop Trading: ");
					allowTrading = false;
					return;
				}
			}

			// Bullish crossover set
			if (CrossAbove(fastSma, slowSma, 1) && Long)
			{
				if (UseTrend)
				{
					// Normal trend mode: longs only above trend
					if (!UseTrendReverse && Close[0] < trendLine)
					{
						ExitShort();
						ExitLong();
						return;
					}

					// Reverse trend mode: longs only below trend
					if (UseTrendReverse && Close[0] > trendLine)
					{
						ExitShort();
						ExitLong();
						return;
					}
				}

				if (Reverse)
				{
					EnterShort(Convert.ToInt32(Contracts), "");
					Draw.VerticalLine(this, "Short Trade " + CurrentBar, 0, Brushes.Crimson, DashStyleHelper.Solid, 2);
					BarBrush = Brushes.Yellow;
				}
				else
				{
					EnterLong(Convert.ToInt32(Contracts), "");
					Draw.VerticalLine(this, "Long Trade " + CurrentBar, 0, Brushes.Lime, DashStyleHelper.Solid, 2);
					BarBrush = Brushes.Yellow;
				}

				trades++;
			}

			// Bearish crossover set
			if (CrossBelow(fastSma, slowSma, 1) && Short)
			{
				if (UseTrend)
				{
					// Normal trend mode: shorts only below trend
					if (!UseTrendReverse && Close[0] > trendLine)
					{
						ExitShort();
						ExitLong();
						return;
					}

					// Reverse trend mode: shorts only above trend
					if (UseTrendReverse && Close[0] < trendLine)
					{
						ExitShort();
						ExitLong();
						return;
					}
				}

				if (Reverse)
				{
					EnterLong(Convert.ToInt32(Contracts), "");
					Draw.VerticalLine(this, "Long Trade " + CurrentBar, 0, Brushes.Lime, DashStyleHelper.Solid, 2);
					BarBrush = Brushes.Yellow;
				}
				else
				{
					EnterShort(Convert.ToInt32(Contracts), "");
					Draw.VerticalLine(this, "Short Trade " + CurrentBar, 0, Brushes.Crimson, DashStyleHelper.Solid, 2);
					BarBrush = Brushes.Yellow;
				}

				trades++;
			}
		}

		#region Results Stats Box
		private double NP = 0;
		private double GP = 0;
		private double GL = 0;
		private double W = 0;
		private double L = 0;
		private double PF = 0;
		private double WR = 0;
		private int NT = 0;

		private int myTradeCount = 0;

		protected override void OnPositionUpdate(Position position, double averagePrice, int quantity, MarketPosition marketPosition)
		{
			if (State != State.Historical && Position.MarketPosition == MarketPosition.Flat && SystemPerformance.RealTimeTrades.Count > 0 && myTradeCount != SystemPerformance.RealTimeTrades.Count)
			{
				if (DrawObjects["PnLReport"] != null)
					RemoveDrawObject("PnLReport");

				myTradeCount = SystemPerformance.RealTimeTrades.Count - 1;
				Trade lastTrade = SystemPerformance.RealTimeTrades[SystemPerformance.RealTimeTrades.Count - 1];

				if (lastTrade.ProfitCurrency > 0)
				{
					W = W + 1;
					GP = GP + (lastTrade.ProfitCurrency * Contracts);
				}
				else
				{
					L = L + 1;
					GL = GL + (lastTrade.ProfitCurrency * Contracts);
				}

				NP = SystemPerformance.AllTrades.TradesPerformance.Currency.CumProfit - priorTradesCumProfit;
				PF = Math.Round(GP / -GL, 2);
				NT = NT + 1;
				WR = Math.Round((100 * W / NT), 2);

				string title = string.Format("{0,-10}| ", "NP $") +
							   string.Format("{0,-10}| ", "PF  ") +
							   string.Format("{0,-10}| ", "NT #") +
							   string.Format("{0,-10} ", "WR %");

				string values = string.Format("{0,-10}| ", NP) +
								string.Format("{0,-10}| ", PF) +
								string.Format("{0,-10}| ", NT) +
								string.Format("{0,-10} ", WR);

				System.Windows.Media.Brush statsBrush = Brushes.White;
				SimpleFont myFont2 = new SimpleFont("Consolas", 20) { Size = 20, Bold = true };

				if (Show_Stats_Box)
				{
					TextFixed TF = Draw.TextFixed(this, "Stats", title + "\n" + values, TextPosition.TopLeft, ChartControl.Properties.ChartText, myFont2, statsBrush, statsBrush, 16);
					TF.OutlineStroke.Width = 1.0f;
				}

				if (SystemPerformance.RealTimeTrades.Count >= MaxTrades)
					allowTrading = false;
			}
		}
		#endregion

		#region Current RealTime PNL
		private bool RunOneTime = true;
		private double InitialBalance = 0;
		private double InitialPnL = 0;
		private double CurrentUnrelizedTotal = 0;
		private double CurrentRelizedTotal = 0;

		protected override void OnMarketData(MarketDataEventArgs marketDataUpdate)
		{
			if (State != State.Historical)
			{
				Account a = Account.All.First(t => t.Name == Account.Name.ToString());

				if (RunOneTime)
				{
					InitialPnL = 0;
					RunOneTime = false;
				}

				CurrentUnrelizedTotal = 0;
				CurrentRelizedTotal = 0;

				if (Position.MarketPosition != MarketPosition.Flat)
					CurrentUnrelizedTotal = a.GetAccountItem(AccountItem.UnrealizedProfitLoss, Currency.UsDollar).Value;

				CurrentRelizedTotal = NP;

				double CurrentPnL = CurrentUnrelizedTotal + CurrentRelizedTotal;
				double ReachedPnL = CurrentPnL - InitialPnL;

				string title2 = string.Format("{0,-15}", "Current PnL $");
				string values2 = string.Format("{0,-15}", Math.Round(CurrentUnrelizedTotal, 2));

				System.Windows.Media.Brush statsBrush = Brushes.White;
				if (Math.Round(CurrentUnrelizedTotal, 2) >= 0)
					statsBrush = Brushes.Lime;
				if (Math.Round(CurrentUnrelizedTotal, 2) < 0)
					statsBrush = Brushes.Red;

				SimpleFont myFont2 = new SimpleFont("Consolas", 20) { Size = 20, Bold = false };

				if (Show_Current_PNL)
				{
					TextFixed TF = Draw.TextFixed(this, "PnL", title2 + "\n" + values2, TextPosition.TopRight, ChartControl.Properties.ChartText, myFont2, statsBrush, statsBrush, 8);
					TF.OutlineStroke.Width = 1.0f;
				}

				if (((CurrentPnL >= KillProfitStop) || (CurrentPnL <= -KillLossStop)) && allowTrading && UseKill)
				{
					allowTrading = false;

					string pnlReport = "";
					if (CurrentPnL >= KillProfitStop)
						pnlReport = "Profit Limit Reached: " + CurrentPnL + "\n(Trading is Not Allowed until next day)";
					if (CurrentPnL <= -KillLossStop)
						pnlReport = "Loss Limit Reached: " + CurrentPnL + "\n(Trading is Not Allowed until next day)";

					if (Position.MarketPosition == MarketPosition.Long)
						ExitLong();
					else if (Position.MarketPosition == MarketPosition.Short)
						ExitShort();

					if (Show_Current_PNL)
						Draw.TextFixed(this, "PnLReport", "\n\n" + pnlReport, TextPosition.TopRight, ChartControl.Properties.ChartText, myFont2, Brushes.Transparent, Brushes.Transparent, 0);
				}
			}
		}
		#endregion

		#region image
		private void UpdateImage(string fileName)
		{
			if (myBitmap != null) myBitmap.Dispose();
			if (fileStream != null) fileStream.Dispose();
			if (bitmapDecoder != null) bitmapDecoder.Dispose();
			if (converter != null) converter.Dispose();
			if (frame != null) frame.Dispose();

			if (myBitmap2 != null) myBitmap2.Dispose();
			if (fileStream2 != null) fileStream2.Dispose();
			if (bitmapDecoder2 != null) bitmapDecoder2.Dispose();
			if (converter2 != null) converter2.Dispose();
			if (frame2 != null) frame2.Dispose();

			if (RenderTarget == null)
				return;

			fileStream = new SharpDX.IO.NativeFileStream(System.IO.Path.Combine(NinjaTrader.Core.Globals.UserDataDir + "templates/MyResources", FileName), SharpDX.IO.NativeFileMode.Open, SharpDX.IO.NativeFileAccess.Read);
			bitmapDecoder = new SharpDX.WIC.BitmapDecoder(Core.Globals.WicImagingFactory, fileStream, SharpDX.WIC.DecodeOptions.CacheOnDemand);
			frame = bitmapDecoder.GetFrame(0);
			converter = new SharpDX.WIC.FormatConverter(Core.Globals.WicImagingFactory);
			converter.Initialize(frame, SharpDX.WIC.PixelFormat.Format32bppPRGBA);
			myBitmap = SharpDX.Direct2D1.Bitmap.FromWicBitmap(RenderTarget, converter);

			fileStream2 = new SharpDX.IO.NativeFileStream(System.IO.Path.Combine(NinjaTrader.Core.Globals.UserDataDir + "templates/MyResources", FileName2), SharpDX.IO.NativeFileMode.Open, SharpDX.IO.NativeFileAccess.Read);
			bitmapDecoder2 = new SharpDX.WIC.BitmapDecoder(Core.Globals.WicImagingFactory, fileStream2, SharpDX.WIC.DecodeOptions.CacheOnDemand);
			frame2 = bitmapDecoder2.GetFrame(0);
			converter2 = new SharpDX.WIC.FormatConverter(Core.Globals.WicImagingFactory);
			converter2.Initialize(frame2, SharpDX.WIC.PixelFormat.Format32bppPRGBA);
			myBitmap2 = SharpDX.Direct2D1.Bitmap.FromWicBitmap(RenderTarget, converter2);
		}

		public override void OnRenderTargetChanged()
		{
			base.OnRenderTargetChanged();
			UpdateImage(FileName);
		}

		protected override void OnRender(ChartControl chartControl, ChartScale chartScale)
		{
			base.OnRender(chartControl, chartScale);

			if (RenderTarget == null || Bars == null || Bars.Instrument == null || myBitmap == null || myBitmap2 == null)
				return;

			if (NeedToUpdate)
			{
				UpdateImage(FileName);
				NeedToUpdate = false;
			}

			RenderTarget.DrawBitmap(myBitmap, new SharpDX.RectangleF(20, 20, 100, 100), 1.0f, BitmapInterpolationMode.Linear);
			RenderTarget.DrawBitmap(myBitmap2, new SharpDX.RectangleF((float)(ChartPanel.W - 696) / 2, (float)(ChartPanel.H - 564) / 2, 696, 564), IOpacity, BitmapInterpolationMode.Linear);
		}
		#endregion

		#region Properties
		[NinjaScriptProperty]
		[Display(Name = "Use_Time_Filter ", Order = 9, GroupName = "Time")]
		public bool UseTimeFilter { get; set; }

		[NinjaScriptProperty]
		[Range(0, 23)]
		[Display(Name = "Start_Time_(HH) ", Order = 100, GroupName = "Time")]
		public int StartTimeH { get; set; }

		[NinjaScriptProperty]
		[Range(0, 59)]
		[Display(Name = "Start_Time_(mm) ", Order = 110, GroupName = "Time")]
		public int StartTimeM { get; set; }

		[NinjaScriptProperty]
		[Range(0, 23)]
		[Display(Name = "Duration_Time_(HH) ", Order = 120, GroupName = "Time")]
		public int DurationTimeH { get; set; }

		[NinjaScriptProperty]
		[Range(0, 59)]
		[Display(Name = "Duration_Time_(mm) ", Order = 130, GroupName = "Time")]
		public int DurationTimeM { get; set; }

		[NinjaScriptProperty]
		[Range(2, int.MaxValue)]
		[Display(ResourceType = typeof(Custom.Resource), Name = "averageFast ", GroupName = "Fast Averages", Order = 1)]
		public int averageFast { get; set; }

		[NinjaScriptProperty]
		[Range(2, int.MaxValue)]
		[Display(ResourceType = typeof(Custom.Resource), Name = "averageSlow ", GroupName = "Slow Averages", Order = 4)]
		public int averageSlow { get; set; }

		[NinjaScriptProperty]
		[Range(2, int.MaxValue)]
		[Display(ResourceType = typeof(Custom.Resource), Name = "averageTrend ", GroupName = "Trend Averages", Order = 7)]
		public int averageTrend { get; set; }

		[NinjaScriptProperty]
		[Display(Name = "UseTrend ", Order = 10, GroupName = "Trend Averages")]
		public bool UseTrend { get; set; }

		[NinjaScriptProperty]
		[Display(Name = "UseTrendReverse ", Description = "UseTrend must be true to use.", Order = 11, GroupName = "Trend Averages")]
		public bool UseTrendReverse { get; set; }

		[NinjaScriptProperty]
		[Range(0, double.MaxValue)]
		[Display(Name = "ProfitStop ", Order = 11, GroupName = "Risk")]
		public double ProfitStop { get; set; }

		[NinjaScriptProperty]
		[Range(0, double.MaxValue)]
		[Display(Name = "LossStop ", Order = 12, GroupName = "Risk")]
		public double LossStop { get; set; }

		[NinjaScriptProperty]
		[Range(0, int.MaxValue)]
		[Display(Name = "MaxTrades ", Order = 13, GroupName = "Risk")]
		public double MaxTrades { get; set; }

		[NinjaScriptProperty]
		[Range(1, int.MaxValue)]
		[Display(Name = "Contracts ", Order = 5, GroupName = "Risk")]
		public int Contracts { get; set; }

		[NinjaScriptProperty]
		[Range(1, int.MaxValue)]
		[Display(Name = "MaxStop ", Order = 6, GroupName = "Cross Profit")]
		public int MaxStop { get; set; }

		[NinjaScriptProperty]
		[Display(Name = "Use_MaxStop ", Order = 7, GroupName = "Cross Profit")]
		public bool UseMaxStop { get; set; }

		[NinjaScriptProperty]
		[Range(.1, double.MaxValue)]
		[Display(Name = "MaxTPRatio ", Order = 80, GroupName = "Cross Profit")]
		public double MaxTPRatio { get; set; }

		[NinjaScriptProperty]
		[Display(Name = "Use_MaxTP ", Order = 81, GroupName = "Cross Profit")]
		public bool UseMaxTP { get; set; }

		[NinjaScriptProperty]
		[Display(Name = "Long ", Order = 70, GroupName = "Direction")]
		public bool Long { get; set; }

		[NinjaScriptProperty]
		[Display(Name = "Short ", Order = 80, GroupName = "Direction")]
		public bool Short { get; set; }

		[NinjaScriptProperty]
		[Display(Name = "Reverse ", Order = 20, GroupName = "Test")]
		public bool Reverse { get; set; }

		[NinjaScriptProperty]
		[Display(Name = "Use_Kill ", Order = 20, GroupName = "Kill")]
		public bool UseKill { get; set; }

		[NinjaScriptProperty]
		[Range(0, double.MaxValue)]
		[Display(Name = "Kill_Profit_Stop ", Order = 11, GroupName = "Kill")]
		public double KillProfitStop { get; set; }

		[NinjaScriptProperty]
		[Range(0, double.MaxValue)]
		[Display(Name = "Kill_Loss_Stop ", Order = 12, GroupName = "Kill")]
		public double KillLossStop { get; set; }

		[NinjaScriptProperty]
		[Display(Name = "Show_Current_PNL ", Order = 105, GroupName = "Display")]
		public bool Show_Current_PNL { get; set; }

		[NinjaScriptProperty]
		[Display(Name = "Show_Stats_Box ", Order = 107, GroupName = "Display")]
		public bool Show_Stats_Box { get; set; }

		[NinjaScriptProperty]
		[Range(0, 1)]
		[Display(Name = "Image_Opacity ", Order = 21, GroupName = "Background")]
		public float IOpacity { get; set; }

		[NinjaScriptProperty]
		[Display(Name = "Corner_Image ", Order = 0, GroupName = "Background")]
		public string FileName { get; set; }

		[NinjaScriptProperty]
		[Display(Name = "Background_Image ", Order = 1, GroupName = "Background")]
		public string FileName2 { get; set; }

		[NinjaScriptProperty]
		[Display(Name = "Bot_Name ", Order = 3, GroupName = "Background")]
		public string BotName { get; set; }
		#endregion
	}
}