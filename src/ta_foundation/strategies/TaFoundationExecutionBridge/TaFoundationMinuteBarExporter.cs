#region Using declarations
using System;
using System.ComponentModel;
using System.ComponentModel.DataAnnotations;
using System.Globalization;
using System.IO;
using System.Text;
using NinjaTrader.NinjaScript;
using NinjaTrader.NinjaScript.Indicators;
#endregion

namespace NinjaTrader.NinjaScript.Indicators
{
    /// <summary>
    /// Writes closed bars (and optionally recent ticks) to .Last.txt files consumed
    /// by the ta_foundation Python pipeline.
    ///
    /// Minute bar output:  <OutputDirectory>\NQ 06-26.Last.txt
    ///   Format: yyyyMMdd HHmmss;open;high;low;close;volume
    ///
    /// Tick output (optional): <OutputDirectory>\NQ 06-26 Tick.Last.txt
    ///   Format: yyyyMMdd HHmmss fffffff;last;bid;ask;volume
    ///   Written once per minute-bar close — NOT on every tick — to avoid I/O stalls.
    /// </summary>
    public class TaFoundationMinuteBarExporter : Indicator
    {
        // BarsInProgress index for the added tick series
        private const int TickSeriesIndex = 1;

        private string resolvedBarsFilePath  = string.Empty;
        private string resolvedTickFilePath  = string.Empty;
        private DateTime lastLoggedBarUtc    = DateTime.MinValue;

        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Name        = "TaFoundationMinuteBarExporter";
                Description = "Writes closed bars and optional ticks to ta_foundation .Last.txt files.";
                Calculate   = Calculate.OnBarClose;
                IsOverlay   = false;
                DisplayInDataBox = false;

                OutputDirectory  = @"D:\MarketData";
                LookbackBars     = 5000;
                ExportTicks      = false;
                TickLookback     = 50000;
            }
            else if (State == State.Configure)
            {
                EnsureDirectory(OutputDirectory);

                if (ExportTicks)
                {
                    // Add a 1-tick series on the same instrument/contract.
                    // BarsInProgress == TickSeriesIndex inside OnBarUpdate.
                    AddDataSeries(BarsPeriodType.Tick, 1);
                }
            }
            else if (State == State.DataLoaded)
            {
                string baseName = Instrument.FullName; // e.g. "NQ 06-26"
                resolvedBarsFilePath = Path.Combine(OutputDirectory ?? string.Empty,
                    baseName + ".Last.txt");
                resolvedTickFilePath = Path.Combine(OutputDirectory ?? string.Empty,
                    baseName + " Tick.Last.txt");
            }
        }

        protected override void OnBarUpdate()
        {
            // Only react to the primary (minute) series closing.
            // The tick series fires here too; we ignore those events —
            // tick data is harvested from BarsArray[TickSeriesIndex] at minute-bar close.
            if (BarsInProgress != 0)
                return;

            if (Historical)
                return;

            if (CurrentBar < 0)
                return;

            WriteMinuteBarsSnapshot();

            if (ExportTicks)
                WriteTickSnapshot();
        }

        // ── Minute bars ──────────────────────────────────────────────────────

        private void WriteMinuteBarsSnapshot()
        {
            if (string.IsNullOrWhiteSpace(resolvedBarsFilePath))
                return;

            int lookback  = Math.Max(1, LookbackBars);
            int startBar  = Math.Max(0, CurrentBar - lookback + 1);
            var builder   = new StringBuilder();

            for (int barIndex = startBar; barIndex <= CurrentBar; barIndex++)
            {
                int barsAgo     = CurrentBar - barIndex;
                DateTime barUtc = NormalizeToUtc(Time[barsAgo]);
                builder.AppendFormat(
                    CultureInfo.InvariantCulture,
                    "{0:yyyyMMdd HHmmss};{1:0.########};{2:0.########};{3:0.########};{4:0.########};{5}",
                    barUtc,
                    Open[barsAgo],
                    High[barsAgo],
                    Low[barsAgo],
                    Close[barsAgo],
                    Volume[barsAgo]);
                builder.AppendLine();
            }

            WriteTextAtomically(resolvedBarsFilePath, builder.ToString());

            DateTime newestUtc = NormalizeToUtc(Time[0]);
            if (newestUtc != lastLoggedBarUtc)
            {
                lastLoggedBarUtc = newestUtc;
                Print(string.Format(
                    CultureInfo.InvariantCulture,
                    "{0:o}|EXPORT_BARS|file={1}|bar_utc={2:o}|bars={3}",
                    DateTime.UtcNow,
                    resolvedBarsFilePath,
                    newestUtc,
                    CurrentBar + 1));
            }
        }

        // ── Tick data ────────────────────────────────────────────────────────

        private void WriteTickSnapshot()
        {
            if (string.IsNullOrWhiteSpace(resolvedTickFilePath))
                return;

            // Guard: tick series may not have loaded yet early in the session
            if (BarsArray.Length <= TickSeriesIndex)
                return;

            var tickBars  = BarsArray[TickSeriesIndex];
            int totalTicks = tickBars.Count;
            if (totalTicks == 0)
                return;

            int lookback  = Math.Max(1, TickLookback);
            int startIdx  = Math.Max(0, totalTicks - lookback);
            var builder   = new StringBuilder();

            for (int i = startIdx; i < totalTicks; i++)
            {
                int barsAgo      = totalTicks - 1 - i;
                DateTime tickUtc = NormalizeToUtc(tickBars.GetTime(barsAgo));

                // NinjaTrader stores sub-second precision in the DateTime ticks field.
                // The Python parser expects 7 fractional-second digits (100ns resolution).
                long subSecond100ns = tickUtc.Ticks % TimeSpan.TicksPerSecond; // 100-nanosecond units
                string frac7       = subSecond100ns.ToString("D7");

                builder.AppendFormat(
                    CultureInfo.InvariantCulture,
                    "{0:yyyyMMdd HHmmss} {1};{2:0.########};{3:0.########};{4:0.########};{5}",
                    tickUtc,
                    frac7,
                    tickBars.GetClose(barsAgo),   // last traded price
                    tickBars.GetClose(barsAgo),   // bid — NT tick bars don't carry separate bid/ask;
                    tickBars.GetClose(barsAgo),   // ask — use last price as placeholder
                    (long)tickBars.GetVolume(barsAgo));
                builder.AppendLine();
            }

            WriteTextAtomically(resolvedTickFilePath, builder.ToString());

            Print(string.Format(
                CultureInfo.InvariantCulture,
                "{0:o}|EXPORT_TICKS|file={1}|ticks={2}",
                DateTime.UtcNow,
                resolvedTickFilePath,
                totalTicks - startIdx));
        }

        // ── Helpers ──────────────────────────────────────────────────────────

        private static DateTime NormalizeToUtc(DateTime value)
        {
            if (value.Kind == DateTimeKind.Utc)
                return value;
            if (value.Kind == DateTimeKind.Local)
                return value.ToUniversalTime();
            return TimeZoneInfo.ConvertTimeToUtc(value, TimeZoneInfo.Local);
        }

        private static void EnsureDirectory(string path)
        {
            if (!string.IsNullOrWhiteSpace(path) && !Directory.Exists(path))
                Directory.CreateDirectory(path);
        }

        private static void WriteTextAtomically(string finalPath, string contents)
        {
            string tmpPath = finalPath + ".tmp";
            File.WriteAllText(tmpPath, contents ?? string.Empty, Encoding.UTF8);
            if (File.Exists(finalPath))
                File.Delete(finalPath);
            File.Move(tmpPath, finalPath);
        }

        // ── Parameters ───────────────────────────────────────────────────────

        [NinjaScriptProperty]
        [Display(Name = "OutputDirectory", Order = 1, GroupName = "Parameters")]
        public string OutputDirectory { get; set; }

        [Range(100, 20000)]
        [NinjaScriptProperty]
        [Display(Name = "LookbackBars", Order = 2, GroupName = "Parameters")]
        public int LookbackBars { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "ExportTicks", Order = 3, GroupName = "Parameters",
            Description = "Also write a Tick.Last.txt file. Adds a 1-tick data series — restart NinjaTrader if you change this.")]
        public bool ExportTicks { get; set; }

        [Range(1000, 500000)]
        [NinjaScriptProperty]
        [Display(Name = "TickLookback", Order = 4, GroupName = "Parameters",
            Description = "Number of recent ticks to include in the Tick.Last.txt snapshot. Default 50000 ≈ one NQ session.")]
        public int TickLookback { get; set; }
    }
}
