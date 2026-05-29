#region Using declarations
using System;
using System.ComponentModel;
using System.ComponentModel.DataAnnotations;
using System.Globalization;
using System.IO;
using System.Text;
using NinjaTrader.Cbi;
using NinjaTrader.Data;
using NinjaTrader.NinjaScript;
#endregion

namespace NinjaTrader.NinjaScript.Strategies
{
    /// <summary>
    /// Data-export strategy: writes every closed bar (and optional tick) to a
    /// <instrument>.<Suffix>.txt file consumed by the ta_foundation Python
    /// pipeline. Unlike the streaming TaFoundationMinuteBarExporter Indicator
    /// — which only writes real-time bars from whatever chart it happens to
    /// be attached to — this strategy is designed to be triggered via
    /// NinjaTrader's Strategy Analyzer with an explicit From/To date range,
    /// so any historical window NinjaTrader has data for can be dumped
    /// programmatically in a single backtest run.
    ///
    /// Workflow:
    ///   1. Drop this .cs into bin/Custom/Strategies/ (or use
    ///      ta_foundation install_strategy_source).
    ///   2. NinjaTrader auto-compiles; observe_compile (Python) waits for
    ///      the Strategies-namespace type to appear -- same IPC that drives
    ///      the existing nt_strategy_loop pipeline.
    ///   3. In Strategy Analyzer set the From/To window and click Backtest
    ///      -- or trigger the same via the existing optimizer-bridge with a
    ///      1-combo seed template.
    ///   4. The strategy writes every closed bar to the output file using a
    ///      StreamWriter (multi-year safe -- O(1) memory). No orders are
    ///      placed; this strategy never takes a position.
    ///
    /// Output filename convention: <instrument>.<FilenameSuffix>.txt. Default
    /// suffix is "Export" -- distinct from the live .Last.txt (indicator
    /// streaming) and the .Full.txt (indicator BackfillOnce) so the three
    /// data-feed paths never collide. The Python parser accepts all three.
    /// </summary>
    public class TaFoundationDataExportStrategy : Strategy
    {
        private const int TickSeriesIndex = 1;

        private string resolvedBarsFilePath = string.Empty;
        private string resolvedTickFilePath = string.Empty;
        private StreamWriter barsWriter;
        private StreamWriter tickWriter;
        private int totalBarsWritten   = 0;
        private long totalTicksWritten = 0;

        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Name        = "TaFoundationDataExportStrategy";
                Description = "Dump every closed bar (and optional tick) to file. " +
                              "Run via Strategy Analyzer with the desired From/To window. " +
                              "Places no orders.";
                Calculate                     = Calculate.OnBarClose;
                EntriesPerDirection           = 1;
                EntryHandling                 = EntryHandling.AllEntries;
                IsExitOnSessionCloseStrategy  = false;
                DefaultQuantity               = 1;
                BarsRequiredToTrade           = 0;

                OutputDirectory   = @"D:\MarketData";
                FilenameSuffix    = "Export";
                ExportTicks       = false;
                OverwriteIfExists = true;
            }
            else if (State == State.Configure)
            {
                if (!string.IsNullOrWhiteSpace(OutputDirectory) && !Directory.Exists(OutputDirectory))
                    Directory.CreateDirectory(OutputDirectory);

                if (ExportTicks)
                    AddDataSeries(BarsPeriodType.Tick, 1);
            }
            else if (State == State.DataLoaded)
            {
                string baseName = Instrument.FullName; // e.g. "NQ 06-26"
                string suffix   = string.IsNullOrWhiteSpace(FilenameSuffix) ? "Export" : FilenameSuffix.Trim();

                resolvedBarsFilePath = Path.Combine(OutputDirectory ?? string.Empty,
                    baseName + "." + suffix + ".txt");
                resolvedTickFilePath = Path.Combine(OutputDirectory ?? string.Empty,
                    baseName + " Tick." + suffix + ".txt");

                if (OverwriteIfExists)
                {
                    if (File.Exists(resolvedBarsFilePath)) File.Delete(resolvedBarsFilePath);
                    if (ExportTicks && File.Exists(resolvedTickFilePath)) File.Delete(resolvedTickFilePath);
                }

                barsWriter = new StreamWriter(resolvedBarsFilePath, append: !OverwriteIfExists, encoding: Encoding.UTF8);
                if (ExportTicks)
                    tickWriter = new StreamWriter(resolvedTickFilePath, append: !OverwriteIfExists, encoding: Encoding.UTF8);

                Print(string.Format(CultureInfo.InvariantCulture,
                    "{0:o}|EXPORT_OPEN|bars_file={1}|tick_file={2}|overwrite={3}",
                    DateTime.UtcNow,
                    resolvedBarsFilePath,
                    ExportTicks ? resolvedTickFilePath : "(disabled)",
                    OverwriteIfExists));
            }
            else if (State == State.Terminated)
            {
                if (barsWriter != null) { try { barsWriter.Flush(); barsWriter.Dispose(); } catch { } barsWriter = null; }
                if (tickWriter != null) { try { tickWriter.Flush(); tickWriter.Dispose(); } catch { } tickWriter = null; }

                Print(string.Format(CultureInfo.InvariantCulture,
                    "{0:o}|EXPORT_DONE|bars_written={1}|ticks_written={2}",
                    DateTime.UtcNow, totalBarsWritten, totalTicksWritten));
            }
        }

        protected override void OnBarUpdate()
        {
            if (BarsInProgress == 0)
            {
                if (barsWriter == null || CurrentBar < 0)
                    return;

                DateTime barUtc = NormalizeToUtc(Time[0]);
                barsWriter.WriteLine(string.Format(
                    CultureInfo.InvariantCulture,
                    "{0:yyyyMMdd HHmmss};{1:0.########};{2:0.########};{3:0.########};{4:0.########};{5}",
                    barUtc,
                    Open[0],
                    High[0],
                    Low[0],
                    Close[0],
                    Volume[0]));
                totalBarsWritten++;

                // Periodic flush for crash-safety on multi-year dumps.
                if ((totalBarsWritten & 0x3FF) == 0)
                    barsWriter.Flush();
            }
            else if (BarsInProgress == TickSeriesIndex && tickWriter != null)
            {
                DateTime tickUtc = NormalizeToUtc(Time[0]);
                long subSecond100ns = tickUtc.Ticks % TimeSpan.TicksPerSecond;
                string frac7 = subSecond100ns.ToString("D7");
                double last = Close[0]; // NinjaTrader tick bars don't carry separate bid/ask

                tickWriter.WriteLine(string.Format(
                    CultureInfo.InvariantCulture,
                    "{0:yyyyMMdd HHmmss} {1};{2:0.########};{3:0.########};{4:0.########};{5}",
                    tickUtc,
                    frac7,
                    last,
                    last,
                    last,
                    (long)Volume[0]));
                totalTicksWritten++;

                if ((totalTicksWritten & 0xFFFF) == 0)
                    tickWriter.Flush();
            }
        }

        private static DateTime NormalizeToUtc(DateTime value)
        {
            if (value.Kind == DateTimeKind.Utc)   return value;
            if (value.Kind == DateTimeKind.Local) return value.ToUniversalTime();
            return TimeZoneInfo.ConvertTimeToUtc(value, TimeZoneInfo.Local);
        }

        // ── Parameters ───────────────────────────────────────────────────────

        [NinjaScriptProperty]
        [Display(Name = "OutputDirectory", Order = 1, GroupName = "Parameters")]
        public string OutputDirectory { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "FilenameSuffix", Order = 2, GroupName = "Parameters",
            Description = "Output filename: <instrument>.<suffix>.txt. Default 'Export' " +
                          "keeps the dump distinct from streaming .Last.txt and " +
                          "indicator-backfill .Full.txt.")]
        public string FilenameSuffix { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "ExportTicks", Order = 3, GroupName = "Parameters",
            Description = "Also export tick data to <instrument> Tick.<suffix>.txt. " +
                          "Multi-year tick exports can be GB-scale -- size accordingly.")]
        public bool ExportTicks { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "OverwriteIfExists", Order = 4, GroupName = "Parameters",
            Description = "Delete existing file before writing. False = append.")]
        public bool OverwriteIfExists { get; set; }
    }
}
