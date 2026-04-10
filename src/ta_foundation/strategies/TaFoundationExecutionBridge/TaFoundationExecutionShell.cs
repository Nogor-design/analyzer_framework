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

    public enum ShellMode
    {
        Idle = 0,
        EntryPending = 1,
        InPosition = 2,
        ExitPending = 3,
        Disabled = 4,
        Recovery = 5
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
        [DataMember(Name = "position_id")] public string PositionId { get; set; }
        [DataMember(Name = "expected_position_state")] public string ExpectedPositionState { get; set; }
        [DataMember(Name = "signal_expiry_seconds")] public int? SignalExpirySeconds { get; set; }
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

    [DataContract]
    public class PersistentShellState
    {
        [DataMember(Name = "active_template")] public string ActiveTemplate { get; set; }
        [DataMember(Name = "last_instruction_id")] public string LastInstructionId { get; set; }
        [DataMember(Name = "signal_intake_enabled")] public bool SignalIntakeEnabled { get; set; }
        [DataMember(Name = "heartbeat_faulted")] public bool HeartbeatFaulted { get; set; }
        [DataMember(Name = "daily_lockout")] public bool DailyLockout { get; set; }
        [DataMember(Name = "current_trading_day")] public string CurrentTradingDay { get; set; }
        [DataMember(Name = "last_bridge_message_utc")] public string LastBridgeMessageUtc { get; set; }
        [DataMember(Name = "shell_mode")] public string ShellMode { get; set; }
        [DataMember(Name = "position_id")] public string PositionId { get; set; }
        [DataMember(Name = "pending_stop_price")] public double PendingStopPrice { get; set; }
        [DataMember(Name = "pending_target_ticks")] public int PendingTargetTicks { get; set; }
        [DataMember(Name = "processed_ids")] public List<string> ProcessedIds { get; set; }
    }

    public class TaFoundationExecutionShell : Strategy
    {
        private const string EntryLongSignal = "TF_ENTER_LONG";
        private const string EntryShortSignal = "TF_ENTER_SHORT";
        private const string ExitSignal = "TF_EXIT_ALL";
        private const string PartialSignal = "TF_PARTIAL";
        private const string LongStopSignal = "TF_STOP_LONG";
        private const string ShortStopSignal = "TF_STOP_SHORT";

        private readonly Queue<BridgeInstruction> pendingInstructions = new Queue<BridgeInstruction>();
        private readonly Dictionary<string, StrategyTemplate> templates = new Dictionary<string, StrategyTemplate>(StringComparer.OrdinalIgnoreCase);
        private readonly HashSet<string> seenMessageIds = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        private readonly Queue<string> processedMessageOrder = new Queue<string>();

        private DateTime lastBridgeMessageUtc = DateTime.MinValue;
        private DateTime lastPollUtc = DateTime.MinValue;
        private DateTime currentTradingDay = Core.Globals.MinDate;

        private string activeTemplate = string.Empty;
        private string lastInstructionId = string.Empty;
        private string activePositionId = string.Empty;

        private bool signalIntakeEnabled = true;
        private bool heartbeatFaulted = false;
        private bool dailyLockout = false;

        private double dailyRealizedPnL = 0.0;
        private double pendingStopPrice = 0.0;
        private int pendingTargetTicks = 0;

        private ShellMode shellMode = ShellMode.Idle;

        private Order entryOrder;
        private Order exitOrder;
        private Order stopOrder;
        private Order targetOrder;

        private int entryBarNumber = -1;
        private int lastKnownPositionQuantity = 0;

        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Name = "TaFoundationExecutionShell";
                Description = "Production-safe NT8 execution shell receiving normalized Python instructions.";
                Calculate = Calculate.OnEachTick;
                EntriesPerDirection = 1;
                EntryHandling = EntryHandling.UniqueEntries;
                IsExitOnSessionCloseStrategy = true;
                ExitOnSessionCloseSeconds = 30;
                IsInstantiatedOnEachOptimizationIteration = false;
                BarsRequiredToTrade = 20;
                TraceOrders = false;

                InboxDirectory = @"C:\ta_foundation\bridge\inbox";
                ArchiveDirectory = @"C:\ta_foundation\bridge\archive";
                RejectDirectory = @"C:\ta_foundation\bridge\rejected";
                LogFilePath = @"C:\ta_foundation\bridge\logs\execution_shell.log";
                TemplateDirectory = @"C:\ta_foundation\bridge\templates";
                StateFilePath = @"C:\ta_foundation\bridge\state\shell_state.json";
                ProcessedIdsFilePath = @"C:\ta_foundation\bridge\state\processed_ids.log";
                PollIntervalSeconds = 1;
                StaleSignalSeconds = 8;
                HeartbeatTimeoutSeconds = 20;
                FlattenOnHeartbeatLoss = false;
                FlatOnDisable = true;
                OneTradeAtATime = true;
                DryRunMode = true;
                RecoverOpenPositionOnStartup = true;
                FlattenIfRecoveryFails = true;
                PersistProcessedIds = true;
                ProcessedIdsRetainCount = 5000;
                MaxPositionSize = 3;
                MaxStopTicksCap = 100;
                MaxDailyLoss = 500;
                RequireInstrumentMatch = true;
            }
            else if (State == State.Configure)
            {
                EnsureDirectory(InboxDirectory);
                EnsureDirectory(ArchiveDirectory);
                EnsureDirectory(RejectDirectory);
                EnsureDirectory(Path.GetDirectoryName(LogFilePath));
                EnsureDirectory(Path.GetDirectoryName(StateFilePath));
                EnsureDirectory(Path.GetDirectoryName(ProcessedIdsFilePath));
                LoadTemplates();
                LoadPersistentState();
                LoadProcessedIds();
            }
            else if (State == State.DataLoaded)
            {
                TryRecoverPositionState();
            }
            else if (State == State.Terminated)
            {
                try
                {
                    SavePersistentState();
                }
                catch
                {
                }

                if (FlatOnDisable)
                    FlattenAndDisable("STRATEGY_TERMINATED");
            }
        }

        protected override void OnBarUpdate()
        {
            if (BarsInProgress != 0)
                return;

            if (CurrentBar < BarsRequiredToTrade)
                return;

            RotateTradingDayIfNeeded();
            PollBridgeFiles();
            CheckHeartbeatFault();
            DrainInstructionQueue();
            EnforceTimeBasedManagement();
            PersistStatePeriodically();
        }

        protected override void OnOrderUpdate(Order order, double limitPrice, double stopPrice, int quantity,
            int filled, double averageFillPrice, OrderState orderState, DateTime time,
            ErrorCode error, string comment)
        {
            if (order == null)
                return;

            TrackOrderReference(order);

            if (orderState == OrderState.Rejected)
            {
                AppendLog("ORDER_REJECT", string.Format(CultureInfo.InvariantCulture,
                    "name={0} id={1} error={2} comment={3}", order.Name, order.OrderId, error, comment));

                if (order == entryOrder)
                {
                    shellMode = ShellMode.Idle;
                    entryOrder = null;
                }
            }
            else if (orderState == OrderState.Cancelled)
            {
                AppendLog("ORDER_CANCEL", string.Format(CultureInfo.InvariantCulture,
                    "name={0} id={1}", order.Name, order.OrderId));
            }
            else if (orderState == OrderState.Working)
            {
                AppendLog("ORDER_WORKING", string.Format(CultureInfo.InvariantCulture,
                    "name={0} id={1} qty={2} stop={3} limit={4}", order.Name, order.OrderId, quantity, stopPrice, limitPrice));
            }
        }

        protected override void OnExecutionUpdate(Execution execution, string executionId, double price, int quantity,
            MarketPosition marketPosition, string orderId, DateTime time)
        {
            if (execution == null || execution.Order == null)
                return;

            Order order = execution.Order;
            TrackOrderReference(order);

            AppendLog("FILL", string.Format(CultureInfo.InvariantCulture,
                "order={0} signal={1} side={2} qty={3} price={4} state={5}",
                orderId,
                order.Name,
                marketPosition,
                quantity,
                price,
                order.OrderState));

            if (order.Name == EntryLongSignal || order.Name == EntryShortSignal)
            {
                shellMode = Position.MarketPosition == MarketPosition.Flat ? ShellMode.EntryPending : ShellMode.InPosition;
                entryBarNumber = CurrentBar;
                EnsureProtectiveOrdersAfterEntry(price);
            }
            else if (order.Name == ExitSignal || order.Name == PartialSignal || order.Name == LongStopSignal || order.Name == ShortStopSignal)
            {
                if (Position.MarketPosition == MarketPosition.Flat)
                {
                    ResetRuntimeTradeState();
                    shellMode = signalIntakeEnabled ? ShellMode.Idle : ShellMode.Disabled;
                }
                else
                {
                    shellMode = ShellMode.InPosition;
                }
            }

            lastKnownPositionQuantity = Position.Quantity;
            SavePersistentState();
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

            foreach (string file in Directory.GetFiles(InboxDirectory, "*.json").OrderBy(p => p, StringComparer.OrdinalIgnoreCase))
                TryReadInstructionFile(file);
        }

        private void TryReadInstructionFile(string path)
        {
            string payload = string.Empty;
            BridgeInstruction instruction = null;

            try
            {
                payload = File.ReadAllText(path, Encoding.UTF8);
                instruction = DeserializeJson<BridgeInstruction>(payload);
                string rejection = ValidateInstruction(instruction);

                if (!string.IsNullOrEmpty(rejection))
                {
                    AppendLog("REJECT", string.Format(CultureInfo.InvariantCulture,
                        "id={0} reason={1}", instruction == null ? "<null>" : instruction.MessageId, rejection));
                    MoveFile(path, RejectDirectory);
                    return;
                }

                pendingInstructions.Enqueue(instruction);
                RecordProcessedMessageId(instruction.MessageId);
                lastBridgeMessageUtc = DateTime.UtcNow;
                heartbeatFaulted = false;
                lastInstructionId = instruction.MessageId;

                AppendLog("ACCEPT", string.Format(CultureInfo.InvariantCulture,
                    "id={0} action={1} template={2}", instruction.MessageId, instruction.Action, instruction.TemplateName));
                MoveFile(path, ArchiveDirectory);
                SavePersistentState();
            }
            catch (Exception ex)
            {
                AppendLog("ERROR", string.Format(CultureInfo.InvariantCulture,
                    "file={0} message={1} payload={2}", path, ex.Message, payload));
                MoveFile(path, RejectDirectory);
            }
        }

        private void DrainInstructionQueue()
        {
            int safetyCounter = 0;
            while (pendingInstructions.Count > 0 && safetyCounter < 100)
            {
                safetyCounter++;
                BridgeInstruction instruction = pendingInstructions.Peek();

                if (!CanExecuteInstructionNow(instruction))
                    break;

                pendingInstructions.Dequeue();
                ExecuteInstruction(instruction);
            }
        }

        private bool CanExecuteInstructionNow(BridgeInstruction instruction)
        {
            BridgeAction action = ParseAction(instruction.Action);

            if (action == BridgeAction.HEARTBEAT || action == BridgeAction.FLATTEN_AND_DISABLE)
                return true;

            if (shellMode == ShellMode.EntryPending)
            {
                return action == BridgeAction.EXIT_ALL ||
                       action == BridgeAction.SCRATCH ||
                       action == BridgeAction.CANCEL_WORKING ||
                       action == BridgeAction.FLATTEN_AND_DISABLE;
            }

            if ((action == BridgeAction.TAKE_PARTIAL || action == BridgeAction.MOVE_STOP || action == BridgeAction.HOLD_FOR_RUNNER || action == BridgeAction.DOWNGRADE_TO_SCALP)
                && Position.MarketPosition == MarketPosition.Flat)
                return false;

            return true;
        }

        private void ExecuteInstruction(BridgeInstruction instruction)
        {
            BridgeAction action = ParseAction(instruction.Action);

            switch (action)
            {
                case BridgeAction.HEARTBEAT:
                    AppendLog("HEARTBEAT", string.Format(CultureInfo.InvariantCulture, "id={0}", instruction.MessageId));
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
                    AppendLog("RUNNER", string.Format(CultureInfo.InvariantCulture,
                        "id={0} mode={1}", instruction.MessageId, instruction.RunnerMode));
                    return;

                case BridgeAction.DOWNGRADE_TO_SCALP:
                    activeTemplate = "scalp_reversal_template";
                    AppendLog("DOWNGRADE", string.Format(CultureInfo.InvariantCulture,
                        "id={0} new_template={1}", instruction.MessageId, activeTemplate));
                    SavePersistentState();
                    return;

                case BridgeAction.CANCEL_WORKING:
                    CancelTrackedWorkingOrders();
                    AppendLog("CANCEL", string.Format(CultureInfo.InvariantCulture, "id={0}", instruction.MessageId));
                    return;

                case BridgeAction.FLATTEN_AND_DISABLE:
                    FlattenAndDisable("BRIDGE_COMMAND");
                    return;

                default:
                    AppendLog("REJECT", string.Format(CultureInfo.InvariantCulture,
                        "id={0} reason=unknown action", instruction.MessageId));
                    return;
            }
        }

        private void HandleEntry(BridgeInstruction instruction, MarketPosition desiredSide)
        {
            if (!signalIntakeEnabled || dailyLockout || shellMode == ShellMode.Disabled)
            {
                AppendLog("REJECT", string.Format(CultureInfo.InvariantCulture,
                    "id={0} reason=intake disabled or daily lockout", instruction.MessageId));
                return;
            }

            if (Position.MarketPosition != MarketPosition.Flat)
            {
                AppendLog("REJECT", string.Format(CultureInfo.InvariantCulture,
                    "id={0} reason=non_flat_position current={1}", instruction.MessageId, Position.MarketPosition));
                return;
            }

            if (shellMode == ShellMode.EntryPending || shellMode == ShellMode.ExitPending)
            {
                AppendLog("REJECT", string.Format(CultureInfo.InvariantCulture,
                    "id={0} reason=shell busy mode={1}", instruction.MessageId, shellMode));
                return;
            }

            StrategyTemplate template = ResolveTemplate(instruction.TemplateName);
            if (template != null && !template.AllowEntry)
            {
                AppendLog("REJECT", string.Format(CultureInfo.InvariantCulture,
                    "id={0} reason=template disallows entry", instruction.MessageId));
                return;
            }

            int quantity = Math.Max(1, Math.Min(MaxPositionSize, instruction.Quantity <= 0 ? 1 : instruction.Quantity));
            int stopTicks = NormalizeStopTicks(instruction, template);
            int targetTicks = NormalizeTargetTicks(instruction, stopTicks);

            activeTemplate = string.IsNullOrWhiteSpace(instruction.TemplateName) ? activeTemplate : instruction.TemplateName;
            activePositionId = string.IsNullOrWhiteSpace(instruction.PositionId) ? instruction.MessageId : instruction.PositionId;
            pendingTargetTicks = targetTicks;
            pendingStopPrice = 0.0;
            shellMode = ShellMode.EntryPending;

            if (DryRunMode)
            {
                AppendLog("DRYRUN_ENTRY", string.Format(CultureInfo.InvariantCulture,
                    "id={0} side={1} qty={2} stop_ticks={3} target_ticks={4} template={5}",
                    instruction.MessageId,
                    desiredSide,
                    quantity,
                    stopTicks,
                    targetTicks,
                    activeTemplate));
                shellMode = ShellMode.InPosition;
                entryBarNumber = CurrentBar;
                pendingStopPrice = desiredSide == MarketPosition.Long
                    ? RoundToTickSize(Close[0] - (stopTicks * TickSize))
                    : RoundToTickSize(Close[0] + (stopTicks * TickSize));
                SavePersistentState();
                return;
            }

            SetInitialTarget(desiredSide, targetTicks);

            if (desiredSide == MarketPosition.Long)
            {
                pendingStopPrice = RoundToTickSize(Close[0] - (stopTicks * TickSize));
                EnterLong(quantity, EntryLongSignal);
            }
            else
            {
                pendingStopPrice = RoundToTickSize(Close[0] + (stopTicks * TickSize));
                EnterShort(quantity, EntryShortSignal);
            }

            AppendLog("ORDER", string.Format(CultureInfo.InvariantCulture,
                "id={0} side={1} qty={2} stop_ticks={3} target_ticks={4} template={5} pending_stop={6}",
                instruction.MessageId,
                desiredSide,
                quantity,
                stopTicks,
                targetTicks,
                activeTemplate,
                pendingStopPrice));
            SavePersistentState();
        }

        private void HandlePartial(BridgeInstruction instruction)
        {
            if (Position.MarketPosition == MarketPosition.Flat)
            {
                AppendLog("REJECT", string.Format(CultureInfo.InvariantCulture,
                    "id={0} reason=no position for partial", instruction.MessageId));
                return;
            }

            int partialQty = instruction.Quantity > 0 ? instruction.Quantity : 1;
            partialQty = Math.Min(partialQty, Math.Abs(Position.Quantity));

            if (DryRunMode)
            {
                AppendLog("DRYRUN_PARTIAL", string.Format(CultureInfo.InvariantCulture,
                    "id={0} qty={1}", instruction.MessageId, partialQty));
                return;
            }

            shellMode = ShellMode.ExitPending;
            if (Position.MarketPosition == MarketPosition.Long)
                ExitLong(partialQty, PartialSignal, EntryLongSignal);
            else if (Position.MarketPosition == MarketPosition.Short)
                ExitShort(partialQty, PartialSignal, EntryShortSignal);

            AppendLog("PARTIAL", string.Format(CultureInfo.InvariantCulture,
                "id={0} qty={1}", instruction.MessageId, partialQty));
        }

        private void HandleMoveStop(BridgeInstruction instruction)
        {
            if (Position.MarketPosition == MarketPosition.Flat)
            {
                AppendLog("REJECT", string.Format(CultureInfo.InvariantCulture,
                    "id={0} reason=no position for move stop", instruction.MessageId));
                return;
            }

            if (!instruction.StopPrice.HasValue || instruction.StopPrice.Value <= 0)
            {
                AppendLog("REJECT", string.Format(CultureInfo.InvariantCulture,
                    "id={0} reason=invalid stop price", instruction.MessageId));
                return;
            }

            double newStop = RoundToTickSize(instruction.StopPrice.Value);
            pendingStopPrice = newStop;

            if (DryRunMode)
            {
                AppendLog("DRYRUN_MOVE_STOP", string.Format(CultureInfo.InvariantCulture,
                    "id={0} stop_price={1}", instruction.MessageId, newStop));
                SavePersistentState();
                return;
            }

            if (Position.MarketPosition == MarketPosition.Long)
                ExitLongStopMarket(0, true, Math.Abs(Position.Quantity), newStop, LongStopSignal, EntryLongSignal);
            else if (Position.MarketPosition == MarketPosition.Short)
                ExitShortStopMarket(0, true, Math.Abs(Position.Quantity), newStop, ShortStopSignal, EntryShortSignal);

            AppendLog("MOVE_STOP", string.Format(CultureInfo.InvariantCulture,
                "id={0} stop_price={1}", instruction.MessageId, newStop));
            SavePersistentState();
        }

        private void ExitAll(string reason)
        {
            if (DryRunMode)
            {
                AppendLog("DRYRUN_EXIT", reason);
                ResetRuntimeTradeState();
                shellMode = signalIntakeEnabled ? ShellMode.Idle : ShellMode.Disabled;
                SavePersistentState();
                return;
            }

            shellMode = ShellMode.ExitPending;
            if (Position.MarketPosition == MarketPosition.Long)
                ExitLong(ExitSignal, EntryLongSignal);
            else if (Position.MarketPosition == MarketPosition.Short)
                ExitShort(ExitSignal, EntryShortSignal);

            CancelTrackedWorkingOrders();
            AppendLog("EXIT", reason);
            SavePersistentState();
        }

        private void FlattenAndDisable(string reason)
        {
            signalIntakeEnabled = false;
            shellMode = ShellMode.Disabled;
            ExitAll("FLATTEN_AND_DISABLE:" + reason);
            AppendLog("DISABLE", reason);
            SavePersistentState();
        }

        private void CheckHeartbeatFault()
        {
            if (lastBridgeMessageUtc == DateTime.MinValue || heartbeatFaulted)
                return;

            if ((DateTime.UtcNow - lastBridgeMessageUtc).TotalSeconds > HeartbeatTimeoutSeconds)
            {
                heartbeatFaulted = true;
                AppendLog("HEARTBEAT_LOST", string.Format(CultureInfo.InvariantCulture,
                    "timeout_seconds={0}", HeartbeatTimeoutSeconds));

                if (FlattenOnHeartbeatLoss)
                    FlattenAndDisable("HEARTBEAT_TIMEOUT");
                else
                {
                    signalIntakeEnabled = false;
                    SavePersistentState();
                }
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

            if (!TryParseInstructionTime(instruction.Timestamp, out DateTime tsUtc))
                return "invalid timestamp";

            int expirySeconds = instruction.SignalExpirySeconds.HasValue && instruction.SignalExpirySeconds.Value > 0
                ? instruction.SignalExpirySeconds.Value
                : StaleSignalSeconds;

            if ((DateTime.UtcNow - tsUtc).TotalSeconds > expirySeconds)
                return "stale signal";

            if (RequireInstrumentMatch && !string.IsNullOrWhiteSpace(instruction.Instrument))
            {
                string raw = instruction.Instrument.Trim();
                if (!string.Equals(raw, Instrument.FullName, StringComparison.OrdinalIgnoreCase) &&
                    !string.Equals(raw, Instrument.MasterInstrument.Name, StringComparison.OrdinalIgnoreCase))
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
                AppendLog("DAY_RESET", string.Format(CultureInfo.InvariantCulture,
                    "date={0:yyyy-MM-dd}", currentTradingDay));
                SavePersistentState();
            }

            double realized = SystemPerformance.AllTrades.TradesPerformance.Currency.CumProfit;
            dailyRealizedPnL = realized;
            if (realized <= -Math.Abs(MaxDailyLoss))
            {
                if (!dailyLockout)
                {
                    dailyLockout = true;
                    AppendLog("LOCKOUT", string.Format(CultureInfo.InvariantCulture,
                        "realized={0} threshold={1}", realized, -Math.Abs(MaxDailyLoss)));
                    SavePersistentState();
                }
            }
        }

        private void LoadTemplates()
        {
            templates.Clear();
            if (!Directory.Exists(TemplateDirectory))
            {
                AppendLog("WARN", string.Format(CultureInfo.InvariantCulture,
                    "template directory not found: {0}", TemplateDirectory));
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
                    AppendLog("TEMPLATE", string.Format(CultureInfo.InvariantCulture,
                        "loaded={0}", template.TemplateName));
                }
                catch (Exception ex)
                {
                    AppendLog("WARN", string.Format(CultureInfo.InvariantCulture,
                        "template load failed file={0} err={1}", path, ex.Message));
                }
            }
        }

        private StrategyTemplate ResolveTemplate(string templateName)
        {
            if (string.IsNullOrWhiteSpace(templateName))
                return null;

            StrategyTemplate template;
            return templates.TryGetValue(templateName, out template) ? template : null;
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
                return (T)serializer.ReadObject(stream);
        }

        private static string SerializeJson<T>(T value)
        {
            DataContractJsonSerializer serializer = new DataContractJsonSerializer(typeof(T));
            using (MemoryStream stream = new MemoryStream())
            {
                serializer.WriteObject(stream, value);
                return Encoding.UTF8.GetString(stream.ToArray());
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
                    string.Format(CultureInfo.InvariantCulture,
                        "{0:yyyyMMdd_HHmmssfff}_{1}", DateTime.UtcNow, fileName));

                if (File.Exists(destinationPath))
                    File.Delete(destinationPath);

                File.Move(sourcePath, destinationPath);
            }
            catch (Exception ex)
            {
                AppendLog("WARN", string.Format(CultureInfo.InvariantCulture,
                    "move failed src={0} dst={1} err={2}", sourcePath, destinationDirectory, ex.Message));
            }
        }

        private void AppendLog(string eventType, string message)
        {
            string line = string.Format(CultureInfo.InvariantCulture,
                "{0:o}|{1}|instr={2}|template={3}|mode={4}|pos={5}|qty={6}|msg={7}",
                DateTime.UtcNow,
                eventType,
                lastInstructionId,
                activeTemplate,
                shellMode,
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

        private int NormalizeStopTicks(BridgeInstruction instruction, StrategyTemplate template)
        {
            int stopTicks = instruction.StopTicks > 0 ? instruction.StopTicks : 12;
            if (template != null && template.HardStopTicksCap > 0)
                stopTicks = Math.Min(stopTicks, template.HardStopTicksCap);
            stopTicks = Math.Min(stopTicks, MaxStopTicksCap);
            return Math.Max(1, stopTicks);
        }

        private int NormalizeTargetTicks(BridgeInstruction instruction, int stopTicks)
        {
            int targetTicks = instruction.TargetTicks.HasValue && instruction.TargetTicks.Value > 0
                ? instruction.TargetTicks.Value
                : Math.Max(4, stopTicks);
            return Math.Max(1, targetTicks);
        }

        private void SetInitialTarget(MarketPosition desiredSide, int targetTicks)
        {
            if (desiredSide == MarketPosition.Long)
                SetProfitTarget(EntryLongSignal, CalculationMode.Ticks, targetTicks);
            else
                SetProfitTarget(EntryShortSignal, CalculationMode.Ticks, targetTicks);
        }

        private void EnsureProtectiveOrdersAfterEntry(double fillPrice)
        {
            if (DryRunMode || Position.MarketPosition == MarketPosition.Flat)
                return;

            if (pendingStopPrice <= 0)
            {
                int fallbackTicks = Math.Min(12, MaxStopTicksCap);
                pendingStopPrice = Position.MarketPosition == MarketPosition.Long
                    ? RoundToTickSize(fillPrice - fallbackTicks * TickSize)
                    : RoundToTickSize(fillPrice + fallbackTicks * TickSize);
                AppendLog("WARN", string.Format(CultureInfo.InvariantCulture,
                    "fallback protective stop applied at {0}", pendingStopPrice));
            }

            if (Position.MarketPosition == MarketPosition.Long)
                ExitLongStopMarket(0, true, Math.Abs(Position.Quantity), pendingStopPrice, LongStopSignal, EntryLongSignal);
            else if (Position.MarketPosition == MarketPosition.Short)
                ExitShortStopMarket(0, true, Math.Abs(Position.Quantity), pendingStopPrice, ShortStopSignal, EntryShortSignal);

            AppendLog("STOP_INIT", string.Format(CultureInfo.InvariantCulture,
                "pos={0} stop_price={1}", Position.MarketPosition, pendingStopPrice));
        }

        private void CancelTrackedWorkingOrders()
        {
            TryCancel(stopOrder);
            TryCancel(targetOrder);
            TryCancel(entryOrder);
            TryCancel(exitOrder);
        }

        private void TryCancel(Order order)
        {
            if (order == null)
                return;
            if (order.OrderState == OrderState.Working || order.OrderState == OrderState.Accepted || order.OrderState == OrderState.PartFilled)
                CancelOrder(order);
        }

        private void TrackOrderReference(Order order)
        {
            if (order == null)
                return;

            if (order.Name == EntryLongSignal || order.Name == EntryShortSignal)
                entryOrder = order;
            else if (order.Name == ExitSignal || order.Name == PartialSignal)
                exitOrder = order;
            else if (order.Name == LongStopSignal || order.Name == ShortStopSignal)
                stopOrder = order;
            else if (order.OrderType == OrderType.Limit)
                targetOrder = order;
        }

        private void ResetRuntimeTradeState()
        {
            entryOrder = null;
            exitOrder = null;
            stopOrder = null;
            targetOrder = null;
            pendingStopPrice = 0.0;
            pendingTargetTicks = 0;
            activePositionId = string.Empty;
            entryBarNumber = -1;
            lastKnownPositionQuantity = 0;
        }

        private void EnforceTimeBasedManagement()
        {
            if (Position.MarketPosition == MarketPosition.Flat)
                return;
            if (entryBarNumber < 0)
                return;

            StrategyTemplate template = ResolveTemplate(activeTemplate);
            int maxHoldBars = template != null && template.MaxHoldBars > 0 ? template.MaxHoldBars : 0;
            if (maxHoldBars <= 0)
                return;

            if ((CurrentBar - entryBarNumber) >= maxHoldBars)
            {
                AppendLog("MAX_HOLD_EXIT", string.Format(CultureInfo.InvariantCulture,
                    "bars_held={0} max_hold={1}", CurrentBar - entryBarNumber, maxHoldBars));
                ExitAll("MAX_HOLD_BARS");
            }
        }

        private void TryRecoverPositionState()
        {
            if (!RecoverOpenPositionOnStartup)
                return;

            if (Position.MarketPosition == MarketPosition.Flat)
            {
                shellMode = signalIntakeEnabled ? ShellMode.Idle : ShellMode.Disabled;
                return;
            }

            shellMode = ShellMode.Recovery;
            AppendLog("RECOVERY", string.Format(CultureInfo.InvariantCulture,
                "detected non-flat position side={0} qty={1}", Position.MarketPosition, Position.Quantity));

            if (pendingStopPrice > 0 && !DryRunMode)
            {
                EnsureProtectiveOrdersAfterEntry(Position.AveragePrice);
                shellMode = ShellMode.InPosition;
                AppendLog("RECOVERY", "protective stop restored from persisted state");
                SavePersistentState();
                return;
            }

            if (FlattenIfRecoveryFails)
            {
                AppendLog("RECOVERY_FAIL", "persisted stop unavailable, flattening position");
                ExitAll("RECOVERY_FAIL_FLATTEN");
                shellMode = ShellMode.ExitPending;
            }
            else
            {
                AppendLog("RECOVERY_WARN", "position detected without persisted stop; manual inspection required");
            }
        }

        private void LoadPersistentState()
        {
            try
            {
                if (string.IsNullOrWhiteSpace(StateFilePath) || !File.Exists(StateFilePath))
                    return;

                PersistentShellState state = DeserializeJson<PersistentShellState>(File.ReadAllText(StateFilePath, Encoding.UTF8));
                if (state == null)
                    return;

                activeTemplate = state.ActiveTemplate ?? string.Empty;
                lastInstructionId = state.LastInstructionId ?? string.Empty;
                signalIntakeEnabled = state.SignalIntakeEnabled;
                heartbeatFaulted = state.HeartbeatFaulted;
                dailyLockout = state.DailyLockout;
                activePositionId = state.PositionId ?? string.Empty;
                pendingStopPrice = state.PendingStopPrice;
                pendingTargetTicks = state.PendingTargetTicks;

                if (!string.IsNullOrWhiteSpace(state.CurrentTradingDay))
                {
                    DateTime parsedDay;
                    if (DateTime.TryParse(state.CurrentTradingDay, CultureInfo.InvariantCulture, DateTimeStyles.None, out parsedDay))
                        currentTradingDay = parsedDay.Date;
                }

                if (!string.IsNullOrWhiteSpace(state.LastBridgeMessageUtc))
                {
                    DateTime parsedUtc;
                    if (DateTime.TryParse(state.LastBridgeMessageUtc, CultureInfo.InvariantCulture, DateTimeStyles.AdjustToUniversal, out parsedUtc))
                        lastBridgeMessageUtc = parsedUtc.ToUniversalTime();
                }

                ShellMode parsedMode;
                if (Enum.TryParse(state.ShellMode ?? string.Empty, true, out parsedMode))
                    shellMode = parsedMode;
            }
            catch (Exception ex)
            {
                AppendLog("WARN", string.Format(CultureInfo.InvariantCulture,
                    "failed to load persistent state err={0}", ex.Message));
            }
        }

        private void SavePersistentState()
        {
            try
            {
                if (string.IsNullOrWhiteSpace(StateFilePath))
                    return;

                PersistentShellState state = new PersistentShellState
                {
                    ActiveTemplate = activeTemplate,
                    LastInstructionId = lastInstructionId,
                    SignalIntakeEnabled = signalIntakeEnabled,
                    HeartbeatFaulted = heartbeatFaulted,
                    DailyLockout = dailyLockout,
                    CurrentTradingDay = currentTradingDay == Core.Globals.MinDate ? string.Empty : currentTradingDay.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture),
                    LastBridgeMessageUtc = lastBridgeMessageUtc == DateTime.MinValue ? string.Empty : lastBridgeMessageUtc.ToString("o", CultureInfo.InvariantCulture),
                    ShellMode = shellMode.ToString(),
                    PositionId = activePositionId,
                    PendingStopPrice = pendingStopPrice,
                    PendingTargetTicks = pendingTargetTicks,
                    ProcessedIds = seenMessageIds.TakeLastSafe(ProcessedIdsRetainCount).ToList()
                };

                string tmpPath = StateFilePath + ".tmp";
                File.WriteAllText(tmpPath, SerializeJson(state), Encoding.UTF8);
                if (File.Exists(StateFilePath))
                    File.Delete(StateFilePath);
                File.Move(tmpPath, StateFilePath);
            }
            catch (Exception ex)
            {
                AppendLog("WARN", string.Format(CultureInfo.InvariantCulture,
                    "failed to save persistent state err={0}", ex.Message));
            }
        }

        private void PersistStatePeriodically()
        {
            if (CurrentBar % 5 == 0)
                SavePersistentState();
        }

        private void LoadProcessedIds()
        {
            try
            {
                if (!PersistProcessedIds || string.IsNullOrWhiteSpace(ProcessedIdsFilePath) || !File.Exists(ProcessedIdsFilePath))
                    return;

                foreach (string line in File.ReadAllLines(ProcessedIdsFilePath, Encoding.UTF8))
                {
                    string id = line == null ? string.Empty : line.Trim();
                    if (string.IsNullOrWhiteSpace(id))
                        continue;
                    if (seenMessageIds.Add(id))
                        processedMessageOrder.Enqueue(id);
                }

                TrimProcessedIdsRetention();
            }
            catch (Exception ex)
            {
                AppendLog("WARN", string.Format(CultureInfo.InvariantCulture,
                    "failed to load processed ids err={0}", ex.Message));
            }
        }

        private void RecordProcessedMessageId(string id)
        {
            if (string.IsNullOrWhiteSpace(id))
                return;
            if (!seenMessageIds.Add(id))
                return;

            processedMessageOrder.Enqueue(id);
            TrimProcessedIdsRetention();

            if (!PersistProcessedIds || string.IsNullOrWhiteSpace(ProcessedIdsFilePath))
                return;

            try
            {
                EnsureDirectory(Path.GetDirectoryName(ProcessedIdsFilePath));
                File.AppendAllText(ProcessedIdsFilePath, id + Environment.NewLine, Encoding.UTF8);
            }
            catch (Exception ex)
            {
                AppendLog("WARN", string.Format(CultureInfo.InvariantCulture,
                    "failed to append processed id err={0}", ex.Message));
            }
        }

        private void TrimProcessedIdsRetention()
        {
            while (processedMessageOrder.Count > Math.Max(100, ProcessedIdsRetainCount))
            {
                string oldest = processedMessageOrder.Dequeue();
                seenMessageIds.Remove(oldest);
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
        [Display(Name = "StateFilePath", GroupName = "Bridge", Order = 6)]
        public string StateFilePath { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "ProcessedIdsFilePath", GroupName = "Bridge", Order = 7)]
        public string ProcessedIdsFilePath { get; set; }

        [NinjaScriptProperty]
        [Range(1, 30)]
        [Display(Name = "PollIntervalSeconds", GroupName = "Bridge", Order = 8)]
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
        [Display(Name = "RecoverOpenPositionOnStartup", GroupName = "Execution", Order = 2)]
        public bool RecoverOpenPositionOnStartup { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "FlattenIfRecoveryFails", GroupName = "Execution", Order = 3)]
        public bool FlattenIfRecoveryFails { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "PersistProcessedIds", GroupName = "Execution", Order = 4)]
        public bool PersistProcessedIds { get; set; }

        [NinjaScriptProperty]
        [Range(100, 50000)]
        [Display(Name = "ProcessedIdsRetainCount", GroupName = "Execution", Order = 5)]
        public int ProcessedIdsRetainCount { get; set; }

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

    internal static class EnumerableExtensions
    {
        public static IEnumerable<T> TakeLastSafe<T>(this IEnumerable<T> source, int count)
        {
            if (source == null)
                return Enumerable.Empty<T>();
            if (count <= 0)
                return Enumerable.Empty<T>();

            Queue<T> queue = new Queue<T>();
            foreach (T item in source)
            {
                queue.Enqueue(item);
                if (queue.Count > count)
                    queue.Dequeue();
            }

            return queue.ToArray();
        }
    }
}
