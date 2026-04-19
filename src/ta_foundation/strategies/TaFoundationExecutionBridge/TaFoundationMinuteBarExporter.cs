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
    public class TaFoundationMinuteBarExporter : Indicator
    {
        private string resolvedOutputFilePath = string.Empty;
        private DateTime lastLoggedBarUtc = DateTime.MinValue;

        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Name = "TaFoundationMinuteBarExporter";
                Description = "Writes the current instrument's closed bars to an NT-style .Last.txt file for the Python strategy loop.";
                Calculate = Calculate.OnBarClose;
                IsOverlay = false;
                DisplayInDataBox = false;

                OutputDirectory = @"D:\MarketData";
                LookbackBars = 5000;
            }
            else if (State == State.Configure)
            {
                EnsureDirectory(OutputDirectory);
            }
            else if (State == State.DataLoaded)
            {
                resolvedOutputFilePath = Path.Combine(OutputDirectory ?? string.Empty, Instrument.FullName + ".Last.txt");
            }
        }

        protected override void OnBarUpdate()
        {
            if (BarsInProgress != 0)
                return;

            if (Historical)
                return;

            if (CurrentBar < 0)
                return;

            WriteRecentBarsSnapshot();
        }

        private void WriteRecentBarsSnapshot()
        {
            if (string.IsNullOrWhiteSpace(resolvedOutputFilePath))
                return;

            int lookback = Math.Max(1, LookbackBars);
            int startBar = Math.Max(0, CurrentBar - lookback + 1);
            StringBuilder builder = new StringBuilder();

            for (int barIndex = startBar; barIndex <= CurrentBar; barIndex++)
            {
                int barsAgo = CurrentBar - barIndex;
                DateTime barTimeUtc = NormalizeToUtc(Time[barsAgo]);
                builder.AppendFormat(
                    CultureInfo.InvariantCulture,
                    "{0:yyyyMMdd HHmmss};{1:0.########};{2:0.########};{3:0.########};{4:0.########};{5}",
                    barTimeUtc,
                    Open[barsAgo],
                    High[barsAgo],
                    Low[barsAgo],
                    Close[barsAgo],
                    Volume[barsAgo]);
                builder.AppendLine();
            }

            WriteTextAtomically(resolvedOutputFilePath, builder.ToString());

            DateTime newestBarUtc = NormalizeToUtc(Time[0]);
            if (newestBarUtc != lastLoggedBarUtc)
            {
                lastLoggedBarUtc = newestBarUtc;
                Print(string.Format(
                    CultureInfo.InvariantCulture,
                    "{0:o}|EXPORT_LAST|file={1}|bar_utc={2:o}|bars={3}",
                    DateTime.UtcNow,
                    resolvedOutputFilePath,
                    newestBarUtc,
                    CurrentBar + 1));
            }
        }

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
            if (string.IsNullOrWhiteSpace(path))
                return;

            if (!Directory.Exists(path))
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

        [NinjaScriptProperty]
        [Display(Name = "OutputDirectory", Order = 1, GroupName = "Parameters")]
        public string OutputDirectory
        { get; set; }

        [Range(100, 20000)]
        [NinjaScriptProperty]
        [Display(Name = "LookbackBars", Order = 2, GroupName = "Parameters")]
        public int LookbackBars
        { get; set; }
    }
}
