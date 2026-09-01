#region Using declarations
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.ComponentModel.DataAnnotations;
using System.Globalization;
using System.IO;
using System.Security.Cryptography;
using System.Text;
using NinjaTrader.Data;
using NinjaTrader.NinjaScript;
#endregion

namespace NinjaTrader.NinjaScript.Strategies
{
    /// <summary>
    /// No-orders reference for the frozen overnight-range fade parity gate.
    /// It observes a primary 2-minute series and an added 1-minute series,
    /// emits independent virtual events for both break sides, and never calls
    /// an entry, exit, order, account, or position API.
    /// </summary>
    public class OvernightFadeParityReference : Strategy
    {
        private const int AtrPeriod = 14;
        private const int SessionOpenMinute = 16 * 60;
        private const int RthOpenMinute = 7 * 60 + 30;
        private const int EntryWindowMinutes = 60;
        private const int FlatBarMinute = 14 * 60 + 55;
        private const int MinimumOvernightOneMinuteBars = 600;
        private const int MaximumOutcomeOneMinuteRows = 240;
        private const double LegAtrMultiple = 2.0;

        private sealed class PendingEvent
        {
            public string EventId;
            public DateTime SessionDate;
            public DateTime SignalLabel;
            public DateTime DecisionTime;
            public string BreakSide;
            public int FadeDirection;
            public double AtrPrice;
            public double OnHigh;
            public double OnLow;
            public int OvernightBars;
        }

        private sealed class VirtualEvent
        {
            public PendingEvent Source;
            public DateTime EntryTime;
            public double EntryPrice;
            public double TargetPrice;
            public double StopPrice;
            public double LegTicksExact;
            public int RowsScanned;
        }

        private readonly List<PendingEvent> pendingEvents = new List<PendingEvent>();
        private readonly List<VirtualEvent> activeEvents = new List<VirtualEvent>();

        private StreamWriter signalWriter;
        private StreamWriter outcomeWriter;
        private StreamWriter twoMinuteWriter;
        private string signalPath = string.Empty;
        private string outcomePath = string.Empty;
        private string twoMinutePath = string.Empty;
        private DateTime segmentStart;
        private DateTime segmentEnd;
        private DateTime currentSession = DateTime.MinValue;
        private DateTime lastOneMinuteTime = DateTime.MinValue;
        private double onHigh = double.MinValue;
        private double onLow = double.MaxValue;
        private int onBars;
        private bool rangeLatched;
        private bool onReady;
        private bool firedUp;
        private bool firedDown;
        private bool hasAtr;
        private double atrWilder;
        private int signalRows;
        private int outcomeRows;
        private int twoMinuteRows;

        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Name = "OvernightFadeParityReference";
                Description = "No-orders virtual ledger for overnight-range fade parity.";
                Calculate = Calculate.OnBarClose;
                BarsRequiredToTrade = 0;
                IsExitOnSessionCloseStrategy = false;
                IsInstantiatedOnEachOptimizationIteration = true;

                OutputDirectory = @"D:\nt-strategy-forge\.tmp\overnight-parity-l1";
                FileTag = "fixed_2r2r";
                OverwriteIfExists = false;
                SourceManifestPath = @"D:\strategy-analysis\large-candle\artifacts\parity\fixed_2r2r_source_v1\manifest.json";
                ExpectedSourceManifestSha256 = string.Empty;
                SegmentBindingPath = string.Empty;
                ExpectedSegmentBindingSha256 = string.Empty;
                SourceInstrument = string.Empty;
                SourceContract = string.Empty;
                SegmentStartDate = string.Empty;
                SegmentEndDate = string.Empty;
                ExpectedApplicationTimeZoneId = "Mountain Standard Time";
                ExpectedTradingHoursName = "CME US Index Futures ETH";
                ExpectedTradingHoursTimeZoneId = "Central Standard Time";
            }
            else if (State == State.Configure)
            {
                AddDataSeries(BarsPeriodType.Minute, 1);
            }
            else if (State == State.DataLoaded)
            {
                ValidateAndOpenOutputs();
            }
            else if (State == State.Realtime)
            {
                throw new InvalidOperationException(
                    "OvernightFadeParityReference is historical-only and refuses real-time execution.");
            }
            else if (State == State.Terminated)
            {
                FinalizeTruncatedEvents();
                CloseWriter(ref signalWriter);
                CloseWriter(ref outcomeWriter);
                CloseWriter(ref twoMinuteWriter);
                if (!string.IsNullOrWhiteSpace(signalPath))
                {
                    Print(string.Format(CultureInfo.InvariantCulture,
                        "OVERNIGHT_FADE_PARITY_DONE|signals={0}|outcomes={1}|two_minute_rows={2}|signal_file={3}|outcome_file={4}|two_minute_file={5}",
                        signalRows, outcomeRows, twoMinuteRows,
                        signalPath, outcomePath, twoMinutePath));
                }
            }
        }

        protected override void OnBarUpdate()
        {
            if (signalWriter == null || outcomeWriter == null || CurrentBars.Length < 2)
                return;
            if (BarsInProgress == 1)
            {
                ProcessOneMinuteBar();
                return;
            }
            if (BarsInProgress == 0)
                ProcessTwoMinuteBar();
        }

        private void ProcessOneMinuteBar()
        {
            if (CurrentBars[1] < 0)
                return;
            DateTime barTime = Times[1][0];
            if (!InsideSegment(barTime))
                return;
            lastOneMinuteTime = barTime;

            DateTime session = SourceSessionDate(barTime);
            if (session != currentSession)
                ResetSession(session);

            int minute = barTime.Hour * 60 + barTime.Minute;
            if (!rangeLatched)
            {
                bool overnight = minute > SessionOpenMinute || minute <= RthOpenMinute;
                if (overnight)
                {
                    onHigh = Math.Max(onHigh, Highs[1][0]);
                    onLow = Math.Min(onLow, Lows[1][0]);
                    onBars++;
                }
                else
                {
                    rangeLatched = true;
                    onReady = onBars >= MinimumOvernightOneMinuteBars && onHigh > onLow;
                }
            }

            OpenPendingEvents(barTime);
            ResolveVirtualEvents(barTime, minute);
        }

        private void ProcessTwoMinuteBar()
        {
            if (CurrentBars[0] < 0)
                return;
            DateTime decisionTime = Times[0][0];
            if (!InsideSegment(decisionTime))
                return;

            double priorAtr = hasAtr ? atrWilder : double.NaN;
            double trueRange = SourceTrueRange();
            WriteTwoMinuteBar(decisionTime, trueRange, priorAtr);
            EvaluateBreak(decisionTime, priorAtr);
            UpdateSourceAtr(trueRange);
        }

        private void EvaluateBreak(DateTime decisionTime, double priorAtr)
        {
            if (!onReady || currentSession == DateTime.MinValue)
                return;
            int minute = decisionTime.Hour * 60 + decisionTime.Minute;
            if (minute <= RthOpenMinute || minute > RthOpenMinute + EntryWindowMinutes)
                return;

            bool upFresh = Highs[0][0] > onHigh && !firedUp;
            bool downFresh = Lows[0][0] < onLow && !firedDown;
            if (!upFresh && !downFresh)
                return;
            if (upFresh && downFresh)
            {
                firedUp = true;
                firedDown = true;
                return;
            }

            string breakSide = upFresh ? "up" : "down";
            if (upFresh)
                firedUp = true;
            else
                firedDown = true;

            // The source consumes the first side even when ATR is unavailable;
            // it later filters the NaN event. Preserve that ordering here.
            if (double.IsNaN(priorAtr) || priorAtr <= 0.0)
                return;

            DateTime signalLabel = decisionTime.AddMinutes(-1);
            int fadeDirection = upFresh ? -1 : 1;
            string eventId = StableEventId(currentSession, breakSide, signalLabel);
            PendingEvent pending = new PendingEvent
            {
                EventId = eventId,
                SessionDate = currentSession,
                SignalLabel = signalLabel,
                DecisionTime = decisionTime,
                BreakSide = breakSide,
                FadeDirection = fadeDirection,
                AtrPrice = priorAtr,
                OnHigh = onHigh,
                OnLow = onLow,
                OvernightBars = onBars,
            };
            pendingEvents.Add(pending);
            WriteSignal(pending);
        }

        private void OpenPendingEvents(DateTime oneMinuteBarTime)
        {
            for (int i = pendingEvents.Count - 1; i >= 0; i--)
            {
                PendingEvent pending = pendingEvents[i];
                if (oneMinuteBarTime <= pending.DecisionTime)
                    continue;
                double entry = Opens[1][0];
                double legPrice = LegAtrMultiple * pending.AtrPrice;
                activeEvents.Add(new VirtualEvent
                {
                    Source = pending,
                    EntryTime = oneMinuteBarTime,
                    EntryPrice = entry,
                    TargetPrice = entry + pending.FadeDirection * legPrice,
                    StopPrice = entry - pending.FadeDirection * legPrice,
                    LegTicksExact = legPrice / TickSize,
                    RowsScanned = 0,
                });
                pendingEvents.RemoveAt(i);
            }
        }

        private void ResolveVirtualEvents(DateTime barTime, int minute)
        {
            for (int i = activeEvents.Count - 1; i >= 0; i--)
            {
                VirtualEvent item = activeEvents[i];
                item.RowsScanned++;
                bool targetHit = item.Source.FadeDirection > 0
                    ? Highs[1][0] >= item.TargetPrice
                    : Lows[1][0] <= item.TargetPrice;
                bool stopHit = item.Source.FadeDirection > 0
                    ? Lows[1][0] <= item.StopPrice
                    : Highs[1][0] >= item.StopPrice;

                if (targetHit && stopHit)
                {
                    WriteOutcome(item, barTime, double.NaN, "tied_bar_requires_ticks");
                    activeEvents.RemoveAt(i);
                }
                else if (targetHit)
                {
                    WriteOutcome(item, barTime, item.TargetPrice, "target");
                    activeEvents.RemoveAt(i);
                }
                else if (stopHit)
                {
                    WriteOutcome(item, barTime, item.StopPrice, "stop");
                    activeEvents.RemoveAt(i);
                }
                else if (item.RowsScanned >= MaximumOutcomeOneMinuteRows
                    || minute == FlatBarMinute)
                {
                    WriteOutcome(item, barTime, Closes[1][0], "timeout");
                    activeEvents.RemoveAt(i);
                }
            }
        }

        private double SourceTrueRange()
        {
            if (!hasAtr)
                return Highs[0][0] - Lows[0][0];
            double previousClose = Closes[0][1];
            return Math.Max(
                Highs[0][0] - Lows[0][0],
                Math.Max(Math.Abs(Highs[0][0] - previousClose),
                    Math.Abs(Lows[0][0] - previousClose)));
        }

        private void UpdateSourceAtr(double trueRange)
        {
            if (!hasAtr)
            {
                atrWilder = trueRange;
                hasAtr = true;
                return;
            }
            atrWilder = ((AtrPeriod - 1.0) * atrWilder + trueRange) / AtrPeriod;
        }

        private void ResetSession(DateTime session)
        {
            currentSession = session;
            onHigh = double.MinValue;
            onLow = double.MaxValue;
            onBars = 0;
            rangeLatched = false;
            onReady = false;
            firedUp = false;
            firedDown = false;
        }

        private bool InsideSegment(DateTime barTime)
        {
            return barTime >= segmentStart && barTime < segmentEnd.AddDays(1);
        }

        private static DateTime SourceSessionDate(DateTime closeStamp)
        {
            DateTime coverageStart = closeStamp.AddMinutes(-1);
            return coverageStart.Hour >= 16
                ? coverageStart.Date.AddDays(1)
                : coverageStart.Date;
        }

        private void WriteSignal(PendingEvent item)
        {
            signalWriter.WriteLine(string.Format(CultureInfo.InvariantCulture,
                "{0},{1},{2},{3},{4},{5},{6},{7:R},{8:R},{9},{10:R},{11:R}",
                item.EventId,
                SourceInstrument,
                SourceContract,
                item.SessionDate.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture),
                item.BreakSide,
                FormatLocal(item.SignalLabel),
                FormatLocal(item.DecisionTime),
                item.OnHigh,
                item.OnLow,
                item.OvernightBars,
                item.AtrPrice,
                item.AtrPrice / TickSize));
            signalRows++;
            signalWriter.Flush();
        }

        private void WriteOutcome(
            VirtualEvent item,
            DateTime exitBarTime,
            double exitPrice,
            string reason)
        {
            double grossTicks = double.NaN;
            if (!double.IsNaN(exitPrice))
            {
                grossTicks = (exitPrice - item.EntryPrice)
                    * item.Source.FadeDirection / TickSize;
            }
            outcomeWriter.WriteLine(string.Format(CultureInfo.InvariantCulture,
                "{0},{1},{2},{3:R},{4:R},{5:R},{6:R},{7},{8},{9:R},{10}",
                item.Source.EventId,
                FormatLocal(item.EntryTime),
                FormatLocal(exitBarTime),
                item.EntryPrice,
                item.LegTicksExact,
                item.TargetPrice,
                item.StopPrice,
                reason,
                double.IsNaN(exitPrice) ? string.Empty : exitPrice.ToString("R", CultureInfo.InvariantCulture),
                grossTicks,
                item.RowsScanned));
            outcomeRows++;
            outcomeWriter.Flush();
        }

        private void WriteTwoMinuteBar(
            DateTime decisionTime,
            double trueRange,
            double priorAtr)
        {
            DateTime session = SourceSessionDate(decisionTime);
            twoMinuteWriter.WriteLine(string.Format(CultureInfo.InvariantCulture,
                "{0},{1},{2},{3:R},{4:R},{5:R},{6:R},{7},{8:R},{9},{10}",
                FormatLocal(decisionTime),
                FormatLocal(decisionTime.AddMinutes(-1)),
                session.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture),
                Opens[0][0],
                Highs[0][0],
                Lows[0][0],
                Closes[0][0],
                Volumes[0][0],
                trueRange,
                double.IsNaN(priorAtr)
                    ? string.Empty : priorAtr.ToString("R", CultureInfo.InvariantCulture),
                double.IsNaN(priorAtr)
                    ? string.Empty : (priorAtr / TickSize).ToString("R", CultureInfo.InvariantCulture)));
            twoMinuteRows++;
            if ((twoMinuteRows & 0x3FF) == 0)
                twoMinuteWriter.Flush();
        }

        private void FinalizeTruncatedEvents()
        {
            if (outcomeWriter == null || lastOneMinuteTime == DateTime.MinValue)
                return;
            for (int i = activeEvents.Count - 1; i >= 0; i--)
            {
                WriteOutcome(activeEvents[i], lastOneMinuteTime,
                    double.NaN, "truncated_at_termination");
            }
            activeEvents.Clear();
        }

        private void ValidateAndOpenOutputs()
        {
            if (BarsPeriod.BarsPeriodType != BarsPeriodType.Minute || BarsPeriod.Value != 2)
                throw new InvalidOperationException(
                    "Overnight fade parity requires a primary 2-minute series.");
            if (!string.Equals(Core.Globals.GeneralOptions.TimeZoneInfo.Id,
                ExpectedApplicationTimeZoneId,
                StringComparison.OrdinalIgnoreCase))
            {
                throw new InvalidOperationException(string.Format(
                    CultureInfo.InvariantCulture,
                    "NinjaTrader application time zone mismatch: expected={0}, actual={1}",
                    ExpectedApplicationTimeZoneId,
                    Core.Globals.GeneralOptions.TimeZoneInfo.Id));
            }
            if (Bars.TradingHours == null
                || !string.Equals(Bars.TradingHours.Name, ExpectedTradingHoursName,
                    StringComparison.OrdinalIgnoreCase))
            {
                throw new InvalidOperationException(string.Format(
                    CultureInfo.InvariantCulture,
                    "Primary trading-hours template mismatch: expected={0}, actual={1}",
                    ExpectedTradingHoursName,
                    Bars.TradingHours == null ? "<null>" : Bars.TradingHours.Name));
            }
            if (Bars.TradingHours.TimeZoneInfo == null
                || !string.Equals(Bars.TradingHours.TimeZoneInfo.Id,
                    ExpectedTradingHoursTimeZoneId, StringComparison.OrdinalIgnoreCase))
            {
                throw new InvalidOperationException(string.Format(
                    CultureInfo.InvariantCulture,
                    "Trading-hours time zone mismatch: expected={0}, actual={1}",
                    ExpectedTradingHoursTimeZoneId,
                    Bars.TradingHours.TimeZoneInfo == null
                        ? "<null>" : Bars.TradingHours.TimeZoneInfo.Id));
            }
            if (string.IsNullOrWhiteSpace(SourceInstrument)
                || string.IsNullOrWhiteSpace(SourceContract))
                throw new InvalidOperationException("SourceInstrument and SourceContract are required.");
            string expectedInstrument = SourceInstrument.Trim() + " " + SourceContract.Trim();
            if (Instrument == null
                || !string.Equals(Instrument.FullName, expectedInstrument,
                    StringComparison.OrdinalIgnoreCase))
            {
                throw new InvalidOperationException(string.Format(
                    CultureInfo.InvariantCulture,
                    "Runtime instrument mismatch: expected={0}, actual={1}",
                    expectedInstrument,
                    Instrument == null ? "<null>" : Instrument.FullName));
            }
            if (!DateTime.TryParseExact(SegmentStartDate, "yyyy-MM-dd",
                CultureInfo.InvariantCulture, DateTimeStyles.None, out segmentStart)
                || !DateTime.TryParseExact(SegmentEndDate, "yyyy-MM-dd",
                    CultureInfo.InvariantCulture, DateTimeStyles.None, out segmentEnd)
                || segmentEnd < segmentStart)
            {
                throw new InvalidOperationException(
                    "SegmentStartDate and SegmentEndDate must be valid yyyy-MM-dd values.");
            }
            string actualHash = ComputeSha256(SourceManifestPath);
            if (string.IsNullOrWhiteSpace(ExpectedSourceManifestSha256)
                || !string.Equals(actualHash, ExpectedSourceManifestSha256.Trim(),
                    StringComparison.OrdinalIgnoreCase))
            {
                throw new InvalidOperationException(string.Format(
                    CultureInfo.InvariantCulture,
                    "Overnight fade source manifest hash mismatch: expected={0}, actual={1}",
                    ExpectedSourceManifestSha256, actualHash));
            }
            string expectedBinding = BuildExpectedSegmentBinding(actualHash);
            string actualBinding = ReadRequiredText(SegmentBindingPath).Trim();
            if (!string.Equals(actualBinding, expectedBinding, StringComparison.Ordinal))
            {
                throw new InvalidOperationException(string.Format(
                    CultureInfo.InvariantCulture,
                    "Segment binding content mismatch: expected={0}, actual={1}",
                    expectedBinding, actualBinding));
            }
            string actualBindingHash = ComputeSha256(SegmentBindingPath);
            if (string.IsNullOrWhiteSpace(ExpectedSegmentBindingSha256)
                || !string.Equals(actualBindingHash, ExpectedSegmentBindingSha256.Trim(),
                    StringComparison.OrdinalIgnoreCase))
            {
                throw new InvalidOperationException(string.Format(
                    CultureInfo.InvariantCulture,
                    "Segment binding hash mismatch: expected={0}, actual={1}",
                    ExpectedSegmentBindingSha256, actualBindingHash));
            }

            Directory.CreateDirectory(OutputDirectory);
            string prefix = SanitizeFilePart(SourceInstrument + "_" + SourceContract
                + "_" + FileTag);
            signalPath = Path.Combine(OutputDirectory, prefix + ".signals.csv");
            outcomePath = Path.Combine(OutputDirectory, prefix + ".outcomes.csv");
            twoMinutePath = Path.Combine(OutputDirectory, prefix + ".bars2m.csv");
            if (!OverwriteIfExists && (File.Exists(signalPath)
                || File.Exists(outcomePath) || File.Exists(twoMinutePath)))
                throw new IOException("Parity output already exists and overwrite is disabled.");

            signalWriter = new StreamWriter(signalPath, false, new UTF8Encoding(false));
            outcomeWriter = new StreamWriter(outcomePath, false, new UTF8Encoding(false));
            twoMinuteWriter = new StreamWriter(twoMinutePath, false, new UTF8Encoding(false));
            signalWriter.WriteLine(
                "event_id,instrument,contract,session_date,break_side,signal_dt_label,decision_dt,on_high,on_low,overnight_raw_1m_bars,source_atr_price,source_atr_ticks");
            outcomeWriter.WriteLine(
                "event_id,entry_actual_dt,exit_bar_dt,entry_price,leg_ticks_exact,target_price_exact,stop_price_exact,exit_reason,exit_price,gross_ticks,bars_scanned");
            twoMinuteWriter.WriteLine(
                "decision_dt,signal_dt_label,session_date,open,high,low,close,volume,true_range_price,prior_atr_price,prior_atr_ticks");
            Print("OVERNIGHT_FADE_PARITY_SOURCE_BOUND|sha256=" + actualHash
                + "|binding_sha256=" + actualBindingHash
                + "|instrument=" + Instrument.FullName
                + "|trading_hours=" + Bars.TradingHours.Name
                + "|file=" + SourceManifestPath);
        }

        private string BuildExpectedSegmentBinding(string sourceManifestSha256)
        {
            return "instrument=" + SourceInstrument.Trim()
                + "|contract=" + SourceContract.Trim()
                + "|start=" + segmentStart.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture)
                + "|end=" + segmentEnd.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture)
                + "|source_manifest_sha256=" + sourceManifestSha256.ToLowerInvariant();
        }

        private string StableEventId(DateTime session, string breakSide, DateTime signalLabel)
        {
            string key = SourceInstrument + "|" + SourceContract + "|"
                + session.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture) + "|"
                + breakSide + "|" + FormatLocal(signalLabel);
            using (SHA256 sha = SHA256.Create())
            {
                byte[] digest = sha.ComputeHash(Encoding.UTF8.GetBytes(key));
                StringBuilder builder = new StringBuilder(48);
                for (int i = 0; i < 12; i++)
                    builder.Append(digest[i].ToString("x2", CultureInfo.InvariantCulture));
                return builder.ToString();
            }
        }

        private static string FormatLocal(DateTime value)
        {
            TimeSpan offset = Core.Globals.GeneralOptions.TimeZoneInfo.GetUtcOffset(value);
            return new DateTimeOffset(DateTime.SpecifyKind(value, DateTimeKind.Unspecified), offset)
                .ToString("yyyy-MM-dd'T'HH:mm:sszzz", CultureInfo.InvariantCulture);
        }

        private static string ReadRequiredText(string path)
        {
            if (string.IsNullOrWhiteSpace(path) || !File.Exists(path))
                throw new FileNotFoundException("Segment binding file was not found.", path);
            return File.ReadAllText(path, Encoding.UTF8);
        }

        private static string ComputeSha256(string path)
        {
            if (string.IsNullOrWhiteSpace(path) || !File.Exists(path))
                throw new FileNotFoundException("Source manifest was not found.", path);
            using (SHA256 sha = SHA256.Create())
            using (FileStream stream = File.OpenRead(path))
            {
                byte[] digest = sha.ComputeHash(stream);
                StringBuilder builder = new StringBuilder(digest.Length * 2);
                foreach (byte value in digest)
                    builder.Append(value.ToString("x2", CultureInfo.InvariantCulture));
                return builder.ToString();
            }
        }

        private static string SanitizeFilePart(string value)
        {
            string text = string.IsNullOrWhiteSpace(value) ? "default" : value.Trim();
            foreach (char invalid in Path.GetInvalidFileNameChars())
                text = text.Replace(invalid, '_');
            return text.Replace(' ', '_');
        }

        private static void CloseWriter(ref StreamWriter writer)
        {
            if (writer == null)
                return;
            try
            {
                writer.Flush();
                writer.Dispose();
            }
            catch
            {
                // Preserve any original NinjaTrader lifecycle exception.
            }
            writer = null;
        }

        [NinjaScriptProperty]
        [Display(Name = "OutputDirectory", Order = 1, GroupName = "Parity")]
        public string OutputDirectory { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "FileTag", Order = 2, GroupName = "Parity")]
        public string FileTag { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "OverwriteIfExists", Order = 3, GroupName = "Parity")]
        public bool OverwriteIfExists { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "SourceManifestPath", Order = 4, GroupName = "Parity")]
        public string SourceManifestPath { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "ExpectedSourceManifestSha256", Order = 5, GroupName = "Parity")]
        public string ExpectedSourceManifestSha256 { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "SegmentBindingPath", Order = 6, GroupName = "Parity")]
        public string SegmentBindingPath { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "ExpectedSegmentBindingSha256", Order = 7, GroupName = "Parity")]
        public string ExpectedSegmentBindingSha256 { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "SourceInstrument", Order = 8, GroupName = "Segment")]
        public string SourceInstrument { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "SourceContract", Order = 9, GroupName = "Segment")]
        public string SourceContract { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "SegmentStartDate", Order = 10, GroupName = "Segment")]
        public string SegmentStartDate { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "SegmentEndDate", Order = 11, GroupName = "Segment")]
        public string SegmentEndDate { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "ExpectedApplicationTimeZoneId", Order = 12, GroupName = "Runtime")]
        public string ExpectedApplicationTimeZoneId { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "ExpectedTradingHoursName", Order = 13, GroupName = "Runtime")]
        public string ExpectedTradingHoursName { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "ExpectedTradingHoursTimeZoneId", Order = 14, GroupName = "Runtime")]
        public string ExpectedTradingHoursTimeZoneId { get; set; }
    }
}

