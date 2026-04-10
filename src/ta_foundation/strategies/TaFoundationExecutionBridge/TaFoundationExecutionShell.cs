#region Using declarations
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.ComponentModel.DataAnnotations;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Runtime.Serialization;
using System.Runtime.Serialization.Json;
using System.Text;
using NinjaTrader.Cbi;
using NinjaTrader.Data;
using NinjaTrader.NinjaScript;
using NinjaTrader.NinjaScript.Strategies;
#endregion

namespace NinjaTrader.NinjaScript.Strategies
{
    public enum BridgeAction
    {
        NONE = 0,
        HEARTBEAT = 1,
        ENTER_LONG = 2,
        ENTER_SHORT = 3,
        EXIT_ALL = 4,
        SCRATCH = 5,
        TAKE_PARTIAL = 6,
        MOVE_STOP = 7,
        HOLD_FOR_RUNNER = 8,
        DOWNGRADE_TO_SCALP = 9,
        CANCEL_WORKING = 10,
        FLATTEN_AND_DISABLE = 11
    }

    [DataContract]
    public class BridgeInstruction
    {
        [DataMember(Name = "message_id")] public string MessageId { get; set; }
        [DataMember(Name = "timestamp")] public string Timestamp { get; set; }
        [DataMember(Name = "instrument")] public string Instrument { get; set; }
        [DataMember(Name = "timeframe")] public string Timeframe { get; set; }
        [DataMember(Name = "action")] public string Action { get; set; }
        [DataMember(Name = "side")] public string Side { get; set; }
        [DataMember(Name = "template_name")] public string TemplateName { get; set; }
        [DataMember(Name = "confidence")] public double Confidence { get; set; }
        [DataMember(Name = "entry_mode")] public string EntryMode { get; set; }
        [DataMember(Name = "quantity")] public int Quantity { get; set; }
        [DataMember(Name = "stop_mode")] public string StopMode { get; set; }
        [DataMember(Name = "stop_ticks")] public int StopTicks { get; set; }
        [DataMember(Name = "stop_price")] public double? StopPrice { get; set; }
        [DataMember(Name = "target_mode")] public string TargetMode { get; set; }
        [DataMember(Name = "target_ticks")] public int? TargetTicks { get; set; }
        [DataMember(Name = "partial_target_ticks")] public int? PartialTargetTicks { get; set; }
        [DataMember(Name = "runner_mode")] public string RunnerMode { get; set; }
        [DataMember(Name = "max_hold_bars")] public int? MaxHoldBars { get; set; }
        [DataMember(Name = "thesis_id")] public string ThesisId { get; set; }
        [DataMember(Name = "notes")] public string Notes { get; set; }
    }

    [DataContract]
    public class StrategyTemplate
    {
        [DataMember(Name = "template_name")] public string TemplateName { get; set; }
        [DataMember(Name = "allow_entry")] public bool AllowEntry { get; set; }
        [DataMember(Name = "stop_mode")] public string StopMode { get; set; }
        [DataMember(Name = "hard_stop_ticks_cap")] public int HardStopTicksCap { get; set; }
        [DataMember(Name = "initial_target_mode")] public string InitialTargetMode { get; set; }
        [DataMember(Name = "max_hold_bars")] public int MaxHoldBars { get; set; }
        [DataMember(Name = "max_adds")] public int MaxAdds { get; set; }
        [DataMember(Name = "allow_scale_in")] public bool AllowScaleIn { get; set; }
        [DataMember(Name = "flatten_on_session_end")] public bool FlattenOnSessionEnd { get; set; }
    }

    public class TaFoundationExecutionShell : Strategy
    {
        private const string EntryLongSignal = "TF_ENTER_LONG";
        private const string EntryShortSignal = "TF_ENTER_SHORT";
        private const string LongStopSignal = "TF_STOP_LONG";
        private const string ShortStopSignal = "TF_STOP_SHORT";

        private readonly Queue<BridgeInstruction> pendingInstructions = new Queue<BridgeInstruction>();
        private readonly Dictionary<string, StrategyTemplate> templates = new Dictionary<string, StrategyTemplate>(StringComparer.OrdinalIgnoreCase);
        private readonly HashSet<string> seenMessageIds = new HashSet<string>(StringComparer.OrdinalIgnoreCase);

        private DateTime lastBridgeMessageUtc = DateTime.MinValue;
        private DateTime lastPollUtc = DateTime.MinValue;
        private DateTime currentTradingDay = Core.Globals.MinDate;
        private string activeTemplate = "";
        private string lastInstructionId = "";
        private bool signalIntakeEnabled = true;
        private bool heartbeatFaulted = false;
        private bool dailyLockout = false;
        private double dailyRealizedPnL = 0.0;
        private int pendingLongStopTicks = 0;
        private int pendingShortStopTicks = 0;

        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Name = "TaFoundationExecutionShell";
                Description = "Reusable NT8 execution shell receiving normalized Python instructions.";
                Calculate = Calculate.OnBarClose;
                EntriesPerDirection = 1;
                EntryHandling = EntryHandling.AllEntries;
                IsExitOnSessionCloseStrategy = true;
                ExitOnSessionCloseSeconds = 30;
                IsInstantiatedOnEachOptimizationIteration = false;
                BarsRequiredToTrade = 20;

                InboxDirectory = @"C:\\ta_foundation\\bridge\\inbox";
                ArchiveDirectory = @"C:\\ta_foundation\\bridge\\archive";
                RejectDirectory = @"C:\\ta_foundation\\bridge\\rejected";
                LogFilePath = @"C:\\ta_foundation\\bridge\\logs\\execution_shell.log";
                TemplateDirectory = @"C:\\ta_foundation\\bridge\\templates";
                PollIntervalSeconds = 1;
                StaleSignalSeconds = 8;
                HeartbeatTimeoutSeconds = 20;
                FlattenOnHeartbeatLoss = false;
                FlatOnDisable = true;
                OneTradeAtATime = true;
                DryRunMode = true;
                MaxPositionSize = 3;
                MaxStopTicksCap = 120;
                MaxDailyLoss = 500;
                RequireInstrumentMatch = true;
            }
            else if (State == State.Configure)
            {
                EnsureDirectory(InboxDirectory);
                EnsureDirectory(ArchiveDirectory);
                EnsureDirectory(RejectDirectory);
                EnsureDirectory(Path.GetDirectoryName(LogFilePath));
                LoadTemplates();
            }
            else if (State == State.Terminated)
            {
                if (FlatOnDisable)
                    FlattenAndDisable("STRATEGY_TERMINATED");
            }
        }

        protected override void OnBarUpdate()
        {
            if (CurrentBar < BarsRequiredToTrade)
                return;

            RotateTradingDayIfNeeded();
            PollBridgeFiles();
            CheckHeartbeatFault();
            DrainInstructionQueue();
        }

        protected override void OnExecutionUpdate(Execution execution, string executionId, double price, int quantity,
            MarketPosition marketPosition, string orderId, DateTime time)
        {
            if (execution == null || execution.Order == null)
                return;

            if (execution.Order.OrderState == OrderState.Filled)
            {
                if (execution.Order.Name == EntryLongSignal && pendingLongStopTicks > 0)
                {
                    double initialStop = RoundToTickSize(price - (pendingLongStopTicks * TickSize));
                    ExitLongStopMarket(0, true, execution.Order.Filled, initialStop, LongStopSignal, EntryLongSignal);
                    AppendLog("STOP_INIT", string.Format(CultureInfo.InvariantCulture,
                        "signal={0} stop_price={1} stop_ticks={2}", EntryLongSignal, initialStop, pendingLongStopTicks));
                    pendingLongStopTicks = 0;
                }
                else if (execution.Order.Name == EntryShortSignal && pendingShortStopTicks > 0)
                {
                    double initialStop = RoundToTickSize(price + (pendingShortStopTicks * TickSize));
                    ExitShortStopMarket(0, true, execution.Order.Filled, initialStop, ShortStopSignal, EntryShortSignal);
                    AppendLog("STOP_INIT", string.Format(CultureInfo.InvariantCulture,
                        "signal={0} stop_price={1} stop_ticks={2}", EntryShortSignal, initialStop, pendingShortStopTicks));
                    pendingShortStopTicks = 0;
                }

                AppendLog("FILL", string.Format(CultureInfo.InvariantCulture,
                    "order={0} signal={1} side={2} qty={3} price={4}",
                    orderId,
                    execution.Order.Name,
                    marketPosition,
                    quantity,
                    price));
            }
        }

        private void PollBridgeFiles()
        {
            DateTime now = DateTime.UtcNow;
            if ((now - lastPollUtc).TotalSeconds < PollIntervalSeconds)
                return;

            lastPollUtc = now;
            if (!signalIntakeEnabled)
                return;

            if (!Directory.Exists(InboxDirectory))
                return;

            foreach (string file in Directory.GetFiles(InboxDirectory, "*.json").OrderBy(p => p))
            {
                TryReadInstructionFile(file);
            }
        }

        private void TryReadInstructionFile(string path)
        {
            string payload = string.Empty;
            try
            {
                payload = File.ReadAllText(path, Encoding.UTF8);
                BridgeInstruction instruction = DeserializeJson<BridgeInstruction>(payload);
                string rejection = ValidateInstruction(instruction);

                if (!string.IsNullOrEmpty(rejection))
                {
                    AppendLog("REJECT", string.Format("id={0} reason={1}", instruction == null ? "<null>" : instruction.MessageId, rejection));
                    MoveFile(path, RejectDirectory);
                    return;
                }

                pendingInstructions.Enqueue(instruction);
                lastBridgeMessageUtc = DateTime.UtcNow;
                heartbeatFaulted = false;
                lastInstructionId = instruction.MessageId;

                AppendLog("ACCEPT", string.Format("id={0} action={1} template={2}", instruction.MessageId, instruction.Action, instruction.TemplateName));
                MoveFile(path, ArchiveDirectory);
            }
            catch (Exception ex)
            {
                AppendLog("ERROR", string.Format("file={0} message={1} payload={2}", path, ex.Message, payload));
                MoveFile(path, RejectDirectory);
            }
        }

        private void DrainInstructionQueue()
        {
            while (pendingInstructions.Count > 0)
            {
                BridgeInstruction instruction = pendingInstructions.Dequeue();
                ExecuteInstruction(instruction);
            }
        }

        private void ExecuteInstruction(BridgeInstruction instruction)
        {
            BridgeAction action = ParseAction(instruction.Action);
            switch (action)
            {
                case BridgeAction.HEARTBEAT:
                    AppendLog("HEARTBEAT", string.Format("id={0}", instruction.MessageId));
                    return;
                case BridgeAction.ENTER_LONG:
                    HandleEntry(instruction, MarketPosition.Long);
                    return;
                case BridgeAction.ENTER_SHORT:
                    HandleEntry(instruction, MarketPosition.Short);
                    return;
                case BridgeAction.EXIT_ALL:
                case BridgeAction.SCRATCH:
                    ExitAll("EXIT_ALL");
                    return;
                case BridgeAction.TAKE_PARTIAL:
                    HandlePartial(instruction);
                    return;
                case BridgeAction.MOVE_STOP:
                    HandleMoveStop(instruction);
                    return;
                case BridgeAction.HOLD_FOR_RUNNER:
                    AppendLog("RUNNER", string.Format("id={0} mode={1}", instruction.MessageId, instruction.RunnerMode));
                    return;
                case BridgeAction.DOWNGRADE_TO_SCALP:
                    activeTemplate = "scalp_reversal_template";
                    AppendLog("DOWNGRADE", string.Format("id={0} new_template={1}", instruction.MessageId, activeTemplate));
                    return;
                case BridgeAction.CANCEL_WORKING:
                    CancelAllOrders();
                    AppendLog("CANCEL", string.Format("id={0}", instruction.MessageId));
                    return;
                case BridgeAction.FLATTEN_AND_DISABLE:
                    FlattenAndDisable("BRIDGE_COMMAND");
                    return;
                default:
                    AppendLog("REJECT", string.Format("id={0} reason=unknown action", instruction.MessageId));
                    return;
            }
        }

        private void HandleEntry(BridgeInstruction instruction, MarketPosition desiredSide)
        {
            if (!signalIntakeEnabled || dailyLockout)
            {
                AppendLog("REJECT", string.Format("id={0} reason=intake disabled or daily lockout", instruction.MessageId));
                return;
            }

            if (OneTradeAtATime && Position.MarketPosition != MarketPosition.Flat)
            {
                AppendLog("REJECT", string.Format("id={0} reason=one_trade_at_a_time", instruction.MessageId));
                return;
            }

            if (Math.Abs(Position.Quantity) >= MaxPositionSize)
            {
                AppendLog("REJECT", string.Format("id={0} reason=max position reached", instruction.MessageId));
                return;
            }

            StrategyTemplate template = ResolveTemplate(instruction.TemplateName);
            if (template != null && !template.AllowEntry)
            {
                AppendLog("REJECT", string.Format("id={0} reason=template disallows entry", instruction.MessageId));
                return;
            }

            int quantity = Math.Max(1, Math.Min(MaxPositionSize, instruction.Quantity <= 0 ? 1 : instruction.Quantity));
            int stopTicks = instruction.StopTicks > 0 ? instruction.StopTicks : 12;
            if (template != null && template.HardStopTicksCap > 0)
                stopTicks = Math.Min(stopTicks, template.HardStopTicksCap);
            stopTicks = Math.Min(stopTicks, MaxStopTicksCap);

            int targetTicks = instruction.TargetTicks.HasValue ? instruction.TargetTicks.Value : Math.Max(4, stopTicks);

            if (DryRunMode)
            {
                activeTemplate = instruction.TemplateName;
                AppendLog("DRYRUN_ENTRY", string.Format(CultureInfo.InvariantCulture,
                    "id={0} side={1} qty={2} stop_ticks={3} target_ticks={4} template={5}",
                    instruction.MessageId,
                    desiredSide,
                    quantity,
                    stopTicks,
                    targetTicks,
                    instruction.TemplateName));
                return;
            }

            if (desiredSide == MarketPosition.Long)
            {
                pendingLongStopTicks = stopTicks;
                SetProfitTarget(EntryLongSignal, CalculationMode.Ticks, targetTicks);
                EnterLong(quantity, EntryLongSignal);
            }
            else
            {
                pendingShortStopTicks = stopTicks;
                SetProfitTarget(EntryShortSignal, CalculationMode.Ticks, targetTicks);
                EnterShort(quantity, EntryShortSignal);
            }

            activeTemplate = instruction.TemplateName;
            AppendLog("ORDER", string.Format(CultureInfo.InvariantCulture,
                "id={0} side={1} qty={2} stop_ticks={3} target_ticks={4} template={5}",
                instruction.MessageId,
                desiredSide,
                quantity,
                stopTicks,
                targetTicks,
                instruction.TemplateName));
        }

        private void HandlePartial(BridgeInstruction instruction)
        {
            if (Position.MarketPosition == MarketPosition.Flat)
            {
                AppendLog("REJECT", string.Format("id={0} reason=no position for partial", instruction.MessageId));
                return;
            }

            int partialQty = instruction.Quantity > 0 ? instruction.Quantity : 1;
            partialQty = Math.Min(partialQty, Math.Abs(Position.Quantity));

            if (DryRunMode)
            {
                AppendLog("DRYRUN_PARTIAL", string.Format("id={0} qty={1}", instruction.MessageId, partialQty));
                return;
            }

            if (Position.MarketPosition == MarketPosition.Long)
                ExitLong(partialQty, "TF_PARTIAL", EntryLongSignal);
            else if (Position.MarketPosition == MarketPosition.Short)
                ExitShort(partialQty, "TF_PARTIAL", EntryShortSignal);

            AppendLog("PARTIAL", string.Format("id={0} qty={1}", instruction.MessageId, partialQty));
        }

        private void HandleMoveStop(BridgeInstruction instruction)
        {
            if (!instruction.StopPrice.HasValue || instruction.StopPrice.Value <= 0)
            {
                AppendLog("REJECT", string.Format("id={0} reason=invalid stop price", instruction.MessageId));
                return;
            }

            if (DryRunMode)
            {
                AppendLog("DRYRUN_MOVE_STOP", string.Format(CultureInfo.InvariantCulture,
                    "id={0} stop_price={1}", instruction.MessageId, instruction.StopPrice.Value));
                return;
            }

            if (Position.MarketPosition == MarketPosition.Flat)
            {
                AppendLog("REJECT", string.Format("id={0} reason=no position for move stop", instruction.MessageId));
                return;
            }

            double newStop = RoundToTickSize(instruction.StopPrice.Value);
            if (Position.MarketPosition == MarketPosition.Long)
                ExitLongStopMarket(0, true, Math.Abs(Position.Quantity), newStop, LongStopSignal, EntryLongSignal);
            else if (Position.MarketPosition == MarketPosition.Short)
                ExitShortStopMarket(0, true, Math.Abs(Position.Quantity), newStop, ShortStopSignal, EntryShortSignal);

            AppendLog("MOVE_STOP", string.Format(CultureInfo.InvariantCulture,
                "id={0} stop_price={1}", instruction.MessageId, newStop));
        }

        private void ExitAll(string reason)
        {
            if (DryRunMode)
            {
                AppendLog("DRYRUN_EXIT", reason);
                return;
            }

            if (Position.MarketPosition == MarketPosition.Long)
                ExitLong("TF_EXIT_ALL", EntryLongSignal);
            else if (Position.MarketPosition == MarketPosition.Short)
                ExitShort("TF_EXIT_ALL", EntryShortSignal);

            AppendLog("EXIT", reason);
        }

        private void FlattenAndDisable(string reason)
        {
            signalIntakeEnabled = false;
            ExitAll("FLATTEN_AND_DISABLE:" + reason);
            AppendLog("DISABLE", reason);
        }

        private void CheckHeartbeatFault()
        {
            if (lastBridgeMessageUtc == DateTime.MinValue || heartbeatFaulted)
                return;

            if ((DateTime.UtcNow - lastBridgeMessageUtc).TotalSeconds > HeartbeatTimeoutSeconds)
            {
                heartbeatFaulted = true;
                AppendLog("HEARTBEAT_LOST", string.Format("timeout_seconds={0}", HeartbeatTimeoutSeconds));
                if (FlattenOnHeartbeatLoss)
                    FlattenAndDisable("HEARTBEAT_TIMEOUT");
                else
                    signalIntakeEnabled = false;
            }
        }

        private string ValidateInstruction(BridgeInstruction instruction)
        {
            if (instruction == null)
                return "payload parse failed";
            if (string.IsNullOrWhiteSpace(instruction.MessageId))
                return "missing message_id";
            if (seenMessageIds.Contains(instruction.MessageId))
                return "duplicate message_id";

            seenMessageIds.Add(instruction.MessageId);

            if (!TryParseInstructionTime(instruction.Timestamp, out DateTime tsUtc))
                return "invalid timestamp";
            if ((DateTime.UtcNow - tsUtc).TotalSeconds > StaleSignalSeconds)
                return "stale signal";

            if (RequireInstrumentMatch && !string.IsNullOrWhiteSpace(instruction.Instrument))
            {
                if (!string.Equals(instruction.Instrument.Trim(), Instrument.FullName, StringComparison.OrdinalIgnoreCase) &&
                    !string.Equals(instruction.Instrument.Trim(), Instrument.MasterInstrument.Name, StringComparison.OrdinalIgnoreCase))
                    return "instrument mismatch";
            }

            BridgeAction action = ParseAction(instruction.Action);
            if (action == BridgeAction.NONE)
                return "unsupported action";

            if ((action == BridgeAction.ENTER_LONG || action == BridgeAction.ENTER_SHORT) && instruction.Quantity > MaxPositionSize)
                return "quantity above max position size";
            if (instruction.StopTicks > MaxStopTicksCap)
                return "stop ticks above configured cap";
            if (dailyLockout && (action == BridgeAction.ENTER_LONG || action == BridgeAction.ENTER_SHORT))
                return "daily loss lockout active";

            return string.Empty;
        }

        private void RotateTradingDayIfNeeded()
        {
            DateTime sessionDate = Times[0][0].Date;
            if (currentTradingDay == Core.Globals.MinDate)
                currentTradingDay = sessionDate;

            if (sessionDate != currentTradingDay)
            {
                currentTradingDay = sessionDate;
                dailyRealizedPnL = 0;
                dailyLockout = false;
                AppendLog("DAY_RESET", string.Format("date={0:yyyy-MM-dd}", currentTradingDay));
            }

            double realized = SystemPerformance.AllTrades.TradesPerformance.Currency.CumProfit;
            if (realized < -Math.Abs(MaxDailyLoss))
            {
                dailyLockout = true;
                AppendLog("LOCKOUT", string.Format(CultureInfo.InvariantCulture,
                    "realized={0} threshold={1}", realized, -Math.Abs(MaxDailyLoss)));
            }
        }

        private void LoadTemplates()
        {
            templates.Clear();
            if (!Directory.Exists(TemplateDirectory))
            {
                AppendLog("WARN", string.Format("template directory not found: {0}", TemplateDirectory));
                return;
            }

            foreach (string path in Directory.GetFiles(TemplateDirectory, "*.json"))
            {
                try
                {
                    string json = File.ReadAllText(path, Encoding.UTF8);
                    StrategyTemplate template = DeserializeJson<StrategyTemplate>(json);
                    if (template == null || string.IsNullOrWhiteSpace(template.TemplateName))
                        continue;
                    templates[template.TemplateName] = template;
                    AppendLog("TEMPLATE", string.Format("loaded={0}", template.TemplateName));
                }
                catch (Exception ex)
                {
                    AppendLog("WARN", string.Format("template load failed file={0} err={1}", path, ex.Message));
                }
            }
        }

        private StrategyTemplate ResolveTemplate(string templateName)
        {
            if (string.IsNullOrWhiteSpace(templateName))
                return null;
            if (templates.ContainsKey(templateName))
                return templates[templateName];
            return null;
        }

        private static BridgeAction ParseAction(string action)
        {
            if (string.IsNullOrWhiteSpace(action))
                return BridgeAction.NONE;

            BridgeAction parsed;
            return Enum.TryParse(action.Trim(), true, out parsed) ? parsed : BridgeAction.NONE;
        }

        private static bool TryParseInstructionTime(string raw, out DateTime utc)
        {
            utc = DateTime.MinValue;
            if (string.IsNullOrWhiteSpace(raw))
                return false;

            DateTimeOffset dto;
            if (!DateTimeOffset.TryParse(raw, CultureInfo.InvariantCulture, DateTimeStyles.AssumeUniversal, out dto))
                return false;
            utc = dto.UtcDateTime;
            return true;
        }

        private static T DeserializeJson<T>(string json)
        {
            DataContractJsonSerializer serializer = new DataContractJsonSerializer(typeof(T));
            using (MemoryStream stream = new MemoryStream(Encoding.UTF8.GetBytes(json)))
            {
                return (T)serializer.ReadObject(stream);
            }
        }

        private static void EnsureDirectory(string path)
        {
            if (string.IsNullOrWhiteSpace(path))
                return;
            if (!Directory.Exists(path))
                Directory.CreateDirectory(path);
        }

        private void MoveFile(string sourcePath, string destinationDirectory)
        {
            try
            {
                EnsureDirectory(destinationDirectory);
                string fileName = Path.GetFileName(sourcePath);
                string destinationPath = Path.Combine(destinationDirectory,
                    string.Format("{0:yyyyMMdd_HHmmssfff}_{1}", DateTime.UtcNow, fileName));

                if (File.Exists(destinationPath))
                    File.Delete(destinationPath);

                File.Move(sourcePath, destinationPath);
            }
            catch (Exception ex)
            {
                AppendLog("WARN", string.Format("move failed src={0} dst={1} err={2}", sourcePath, destinationDirectory, ex.Message));
            }
        }

        private void AppendLog(string eventType, string message)
        {
            string line = string.Format("{0:o}|{1}|instr={2}|template={3}|pos={4}|qty={5}|msg={6}",
                DateTime.UtcNow,
                eventType,
                lastInstructionId,
                activeTemplate,
                Position.MarketPosition,
                Position.Quantity,
                message);

            Print(line);

            try
            {
                if (string.IsNullOrWhiteSpace(LogFilePath))
                    return;
                EnsureDirectory(Path.GetDirectoryName(LogFilePath));
                File.AppendAllText(LogFilePath, line + Environment.NewLine, Encoding.UTF8);
            }
            catch
            {
            }
        }

        #region Parameters
        [NinjaScriptProperty]
        [Display(Name = "InboxDirectory", GroupName = "Bridge", Order = 1)]
        public string InboxDirectory { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "ArchiveDirectory", GroupName = "Bridge", Order = 2)]
        public string ArchiveDirectory { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "RejectDirectory", GroupName = "Bridge", Order = 3)]
        public string RejectDirectory { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "TemplateDirectory", GroupName = "Bridge", Order = 4)]
        public string TemplateDirectory { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "LogFilePath", GroupName = "Bridge", Order = 5)]
        public string LogFilePath { get; set; }

        [NinjaScriptProperty]
        [Range(1, 30)]
        [Display(Name = "PollIntervalSeconds", GroupName = "Bridge", Order = 6)]
        public int PollIntervalSeconds { get; set; }

        [NinjaScriptProperty]
        [Range(1, 300)]
        [Display(Name = "StaleSignalSeconds", GroupName = "Safety", Order = 1)]
        public int StaleSignalSeconds { get; set; }

        [NinjaScriptProperty]
        [Range(5, 600)]
        [Display(Name = "HeartbeatTimeoutSeconds", GroupName = "Safety", Order = 2)]
        public int HeartbeatTimeoutSeconds { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "FlattenOnHeartbeatLoss", GroupName = "Safety", Order = 3)]
        public bool FlattenOnHeartbeatLoss { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "FlatOnDisable", GroupName = "Safety", Order = 4)]
        public bool FlatOnDisable { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "OneTradeAtATime", GroupName = "Safety", Order = 5)]
        public bool OneTradeAtATime { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "DryRunMode", GroupName = "Execution", Order = 1)]
        public bool DryRunMode { get; set; }

        [NinjaScriptProperty]
        [Range(1, 50)]
        [Display(Name = "MaxPositionSize", GroupName = "Risk", Order = 1)]
        public int MaxPositionSize { get; set; }

        [NinjaScriptProperty]
        [Range(1, 2000)]
        [Display(Name = "MaxStopTicksCap", GroupName = "Risk", Order = 2)]
        public int MaxStopTicksCap { get; set; }

        [NinjaScriptProperty]
        [Range(1, 50000)]
        [Display(Name = "MaxDailyLoss", GroupName = "Risk", Order = 3)]
        public double MaxDailyLoss { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "RequireInstrumentMatch", GroupName = "Risk", Order = 4)]
        public bool RequireInstrumentMatch { get; set; }
        #endregion
    }
}
