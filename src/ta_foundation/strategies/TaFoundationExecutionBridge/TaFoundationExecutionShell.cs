#region Using declarations
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.ComponentModel.DataAnnotations;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using NinjaTrader.Cbi;
using NinjaTrader.Data;
using NinjaTrader.NinjaScript;
using NinjaTrader.NinjaScript.Strategies;
using System.Web.Script.Serialization;
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

   
    public class BridgeInstruction
    {
		public string MessageId { get; set; }
		public string Timestamp { get; set; }
		public string Instrument { get; set; }
		public string Timeframe { get; set; }
		public string Action { get; set; }
		public string Side { get; set; }
		public string TemplateName { get; set; }
		public double Confidence { get; set; }
		public string EntryMode { get; set; }
		public int Quantity { get; set; }
		public string StopMode { get; set; }
		public int StopTicks { get; set; }
		public double? StopPrice { get; set; }
		public string TargetMode { get; set; }
		public int? TargetTicks { get; set; }
		public int? PartialTargetTicks { get; set; }
		public string RunnerMode { get; set; }
		public int? MaxHoldBars { get; set; }
		public string ThesisId { get; set; }
		public string Notes { get; set; }
		public string PositionId { get; set; }
		public string ExpectedPositionState { get; set; }
		public int? SignalExpirySeconds { get; set; }
    }

   
    public class StrategyTemplate
    {
		public string TemplateName { get; set; }
		public bool AllowEntry { get; set; }
		public string StopMode { get; set; }
		public int HardStopTicksCap { get; set; }
		public string InitialTargetMode { get; set; }
		public int MaxHoldBars { get; set; }
		public int MaxAdds { get; set; }
		public bool AllowScaleIn { get; set; }
		public bool FlattenOnSessionEnd { get; set; }
    }

   
    public class PersistentShellState
    {
		public string ActiveTemplate { get; set; }
		public string LastInstructionId { get; set; }
		public bool SignalIntakeEnabled { get; set; }
		public bool HeartbeatFaulted { get; set; }
		public bool DailyLockout { get; set; }
		public string CurrentTradingDay { get; set; }
		public string LastBridgeMessageUtc { get; set; }
		public string ShellMode { get; set; }
		public string PositionId { get; set; }
		public double PendingStopPrice { get; set; }
		public int PendingTargetTicks { get; set; }
		public List<string> ProcessedIds { get; set; }
		public string LastHealthSnapshotUtc { get; set; }
    }

    public class TaFoundationExecutionShell : Strategy
    {
        private const int StartupRecoveryGraceSeconds = 10;
        private const string EntryLongSignal = "TF_ENTER_LONG";
        private const string EntryShortSignal = "TF_ENTER_SHORT";
        private const string ExitSignal = "TF_EXIT_ALL";
        private const string PartialSignal = "TF_PARTIAL";
        private const string LongStopSignal   = "TF_STOP_LONG";
        private const string ShortStopSignal  = "TF_STOP_SHORT";
        private const string LongTargetSignal  = "TF_TARGET_LONG";
        private const string ShortTargetSignal = "TF_TARGET_SHORT";

        private readonly Queue<BridgeInstruction> pendingInstructions = new Queue<BridgeInstruction>();
        private readonly Dictionary<string, StrategyTemplate> templates = new Dictionary<string, StrategyTemplate>(StringComparer.OrdinalIgnoreCase);
        private readonly HashSet<string> seenMessageIds = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        private readonly Queue<string> processedMessageOrder = new Queue<string>();

        private DateTime lastBridgeMessageUtc = DateTime.MinValue;
        private DateTime lastPollUtc = DateTime.MinValue;
        private DateTime currentTradingDay = Core.Globals.MinDate;
        private DateTime lastHealthSnapshotUtc = DateTime.MinValue;
        private DateTime startupRecoveryDeadlineUtc = DateTime.MinValue;

        private string activeTemplate = string.Empty;
        private string lastInstructionId = string.Empty;
        private string activePositionId = string.Empty;

        private bool signalIntakeEnabled = true;
        private bool heartbeatFaulted = false;
        private bool dailyLockout = false;
        private bool resumeIntakeWhenFlatAfterHeartbeatFault = false;
        private bool pendingStartupRecovery = false;

        private double dailyRealizedPnL = 0.0;
        private double pendingStopPrice = 0.0;
        private int pendingTargetTicks = 0;
        private int pendingStopTicks = 0;       // ticks stored at entry time; used in PlaceProtectiveOrders()
        private bool pendingStopNeeded = false;

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
                OutboxDirectory = @"C:\ta_foundation\bridge\outbox";
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
                RecoverOpenPositionOnStartup = false;
                FlattenIfRecoveryFails = true;
                PersistProcessedIds = true;
                ProcessedIdsRetainCount = 5000;
                MaxPositionSize = 3;
                MaxStopTicksCap = 100;
                MaxDailyLoss = 500;
                RequireInstrumentMatch = true;
                EnableOutboxEvents = true;
                RecoveryFallbackStopTicks = 16;
                HealthSnapshotIntervalSeconds = 30;
            }
            else if (State == State.Configure)
            {
                EnsureDirectory(InboxDirectory);
                EnsureDirectory(ArchiveDirectory);
                EnsureDirectory(RejectDirectory);
                EnsureDirectory(OutboxDirectory);
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

                // ── Auto-reset intake after transient fault on clean flat restart ─────
                // LoadPersistentState() restores signalIntakeEnabled from the state file.
                // If the previous session ended with a heartbeat fault while flat, the
                // saved state will have signalIntakeEnabled=false.  On a clean restart
                // with no open position this is safe to clear automatically.
                // A deliberate FlattenAndDisable (kill-switch) also saves intake=false,
                // so we only auto-reset when heartbeatFaulted=true (transient condition),
                // not when both are false (intentional disable).
                bool recoverableOpenState = HasRecoverableOpenState();
                bool positionFlat = Position != null
                    ? Position.MarketPosition == MarketPosition.Flat
                    : !recoverableOpenState;
                if (!signalIntakeEnabled && heartbeatFaulted && positionFlat)
                {
                    AppendLog("STARTUP_AUTO_RESET",
                        "intake re-enabled after heartbeat fault on clean flat restart; previous session ended with transient hb fault");
                    signalIntakeEnabled = true;
                    heartbeatFaulted = false;
                    shellMode = ShellMode.Idle;
                    lastBridgeMessageUtc = DateTime.MinValue;
                }

                // ── Startup diagnostics ───────────────────────────────────────────────
                // Written BEFORE SHELL_READY so log sequence is: diagnostics → ready.
                AppendLog("STARTUP_STATE", string.Format(CultureInfo.InvariantCulture,
                    "mode={0} intake={1} hb_faulted={2} daily_lockout={3} ",
                    "pos_side={4} pos_qty={5} template={6} pending_stop={7}",
                    shellMode, signalIntakeEnabled, heartbeatFaulted, dailyLockout,
                    Position != null ? Position.MarketPosition.ToString() : "Unavailable",
                    Position != null ? Position.Quantity : 0,
                    string.IsNullOrWhiteSpace(activeTemplate) ? "<none>" : activeTemplate,
                    pendingStopPrice));

                // Probe inbox for any waiting files — log count so we can see if
                // stale messages are already queued at startup.
                int inboxDepth = 0;
                try
                {
                    if (Directory.Exists(InboxDirectory))
                        inboxDepth = Directory.GetFiles(InboxDirectory, "*.json").Length;
                }
                catch { }
                AppendLog("STARTUP_INBOX", string.Format(CultureInfo.InvariantCulture,
                    "inbox_depth={0} inbox_dir={1}", inboxDepth, InboxDirectory));

                LogReconciliationSnapshot("STARTUP");
                WriteOutboxEvent("SHELL_READY", string.Format(CultureInfo.InvariantCulture,
                    "mode={0};intake={1};hb_faulted={2};daily_lockout={3};"
                    + "recovered_mode={4};inbox_depth={5}",
                    shellMode, signalIntakeEnabled, heartbeatFaulted, dailyLockout,
                    Position != null ? Position.MarketPosition.ToString() : "Unknown",
                    inboxDepth));
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
			
			    try
			    {
			        if (FlatOnDisable && Position != null && Position.MarketPosition != MarketPosition.Flat)
			            FlattenAndDisable("STRATEGY_TERMINATED");
			    }
			    catch
			    {
			    }
			}
        }

        protected override void OnBarUpdate()
        {
            if (BarsInProgress != 0)
                return;

            ContinueStartupRecoveryIfNeeded();

            if (CurrentBar < BarsRequiredToTrade)
                return;

            RotateTradingDayIfNeeded();
            SubmitPendingStopIfReady();
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

                WriteOutboxEvent("ORDER_REJECTED", string.Format(CultureInfo.InvariantCulture,
                    "signal={0};order_id={1};error={2};comment={3}", order.Name, order.OrderId, error, comment));
            }
            else if (orderState == OrderState.Cancelled)
            {
                AppendLog("ORDER_CANCEL", string.Format(CultureInfo.InvariantCulture,
                    "name={0} id={1}", order.Name, order.OrderId));
                WriteOutboxEvent("ORDER_CANCELLED", string.Format(CultureInfo.InvariantCulture,
                    "signal={0};order_id={1}", order.Name, order.OrderId));
            }
            else if (orderState == OrderState.PartFilled)
            {
                AppendLog("ORDER_PARTIAL", string.Format(CultureInfo.InvariantCulture,
                    "name={0} id={1} filled={2}/{3} avg={4}", order.Name, order.OrderId, filled, quantity, averageFillPrice));
            }
            else if (orderState == OrderState.Working)
            {
                AppendLog("ORDER_WORKING", string.Format(CultureInfo.InvariantCulture,
                    "name={0} id={1} qty={2} stop={3} limit={4}", order.Name, order.OrderId, quantity, stopPrice, limitPrice));

                bool isStop = order.Name == LongStopSignal || order.Name == ShortStopSignal
                          || order.OrderType == OrderType.StopMarket;
                if (isStop)
                {
                    WriteOutboxEvent("STOP_WORKING", string.Format(CultureInfo.InvariantCulture,
                        "order_id={0};signal={1};stop_price={2};qty={3};pos_side={4}",
                        order.OrderId, order.Name, stopPrice, quantity,
                        Position != null ? Position.MarketPosition.ToString() : "Unknown"));
                }
            }

            LogReconciliationSnapshot("ORDER_UPDATE");
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
            WriteOutboxEvent("FILLED", string.Format(CultureInfo.InvariantCulture,
                "order_id={0};signal={1};side={2};qty={3};price={4};remaining_qty={5};pos_side={6}",
                orderId, order.Name, marketPosition, quantity, price,
                Position.Quantity, Position.MarketPosition));

            if (order.Name == EntryLongSignal || order.Name == EntryShortSignal)
            {
                shellMode = ShellMode.InPosition;
                entryBarNumber = CurrentBar;
                // Place stop + target immediately using the actual fill price.
                // isLiveUntilCancelled=true keeps them alive across bars and lets
                // us move the stop by calling ExitLong/ShortStopMarket again.
                if (!DryRunMode)
                    PlaceProtectiveOrders(order.Name, price, quantity);
            }
            else if (order.Name == ExitSignal || order.Name == PartialSignal
                     || order.Name == LongStopSignal  || order.Name == ShortStopSignal
                     || order.Name == LongTargetSignal || order.Name == ShortTargetSignal)
            {
                if (Position.MarketPosition == MarketPosition.Flat)
                {
                    // Cancel the other bracket leg before clearing references.
                    // LUC exit orders remain live in NT8 until explicitly cancelled.
                    // Leaving the target alive after the stop fills (or vice versa)
                    // causes the next ENTER_LONG with the same fromEntrySignal to be
                    // silently dropped by NT8's bracket-conflict detection.
                    if (order.Name == LongStopSignal || order.Name == ShortStopSignal)
                        TryCancel(targetOrder);
                    else if (order.Name == LongTargetSignal || order.Name == ShortTargetSignal)
                        TryCancel(stopOrder);
                    ResetRuntimeTradeState();
                    if (!TryAutoResumeIntakeAfterFlatTradeCompletion())
                        shellMode = signalIntakeEnabled ? ShellMode.Idle : ShellMode.Disabled;
                }
                else
                {
                    shellMode = ShellMode.InPosition;
                }
            }

            lastKnownPositionQuantity = Position.Quantity;
            SavePersistentState();
            LogReconciliationSnapshot("EXECUTION");
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
                instruction = DeserializeInstruction(payload);
                string rejection = ValidateInstruction(instruction);

                if (!string.IsNullOrEmpty(rejection))
                {
                    string rejectId = instruction != null ? instruction.MessageId : "<null>";
                    AppendLog("REJECT", string.Format(CultureInfo.InvariantCulture,
                        "id={0} reason={1}", rejectId, rejection));
                    // Write outbox event so harness can detect early-validation rejects
                    // (e.g. stale signal, instrument mismatch, stop-ticks cap exceeded).
                    if (instruction != null)
                    {
                        lastInstructionId = instruction.MessageId;
                        WriteOutboxEvent("REJECTED", string.Format(CultureInfo.InvariantCulture,
                            "id={0};action={1};reason={2}", rejectId, instruction.Action ?? "?", rejection));
                    }
                    string ignored;
                    TryMoveFile(path, RejectDirectory, out ignored);
                    return;
                }

                string archivedPath;
                if (!TryMoveFile(path, ArchiveDirectory, out archivedPath))
                {
                    AppendLog("WARN", string.Format(CultureInfo.InvariantCulture,
                        "id={0} reason=archive move failed, leaving unprocessed", instruction.MessageId));
                    return;
                }

                pendingInstructions.Enqueue(instruction);
                RecordProcessedMessageId(instruction.MessageId);
                lastBridgeMessageUtc = DateTime.UtcNow;
                heartbeatFaulted = false;
                lastInstructionId = instruction.MessageId;

                AppendLog("ACCEPT", string.Format(CultureInfo.InvariantCulture,
                    "id={0} action={1} template={2} archived={3}", instruction.MessageId, instruction.Action, instruction.TemplateName, archivedPath));
                WriteOutboxEvent("ACCEPTED", string.Format(CultureInfo.InvariantCulture,
                    "id={0};action={1};template={2}", instruction.MessageId, instruction.Action, instruction.TemplateName));
                SavePersistentState();
            }
            catch (Exception ex)
            {
                AppendLog("ERROR", string.Format(CultureInfo.InvariantCulture,
                    "file={0} message={1} payload={2}", path, ex.Message, payload));
                string ignored;
                TryMoveFile(path, RejectDirectory, out ignored);
            }
        }

        private void DrainInstructionQueue()
        {
            int safetyCounter = 0;
            while (pendingInstructions.Count > 0 && safetyCounter < 100)
            {
                safetyCounter++;
                BridgeInstruction instruction = pendingInstructions.Peek();

                pendingInstructions.Dequeue();
                string gateReason;
                if (!CanExecuteInstructionNow(instruction, out gateReason))
                {
                    AppendLog("REJECT_GATE", string.Format(CultureInfo.InvariantCulture,
                        "id={0} action={1} reason={2}", instruction.MessageId, instruction.Action, gateReason));
                    WriteOutboxEvent("REJECTED", string.Format(CultureInfo.InvariantCulture,
                        "id={0};action={1};reason={2}", instruction.MessageId, instruction.Action, gateReason));
                    continue;
                }

                ExecuteInstruction(instruction);
            }
        }

        private bool CanExecuteInstructionNow(BridgeInstruction instruction, out string reason)
        {
            reason = string.Empty;
            BridgeAction action = ParseAction(instruction.Action);

            if (action == BridgeAction.HEARTBEAT || action == BridgeAction.FLATTEN_AND_DISABLE)
                return true;

            if (shellMode == ShellMode.EntryPending)
            {
                bool allowed = action == BridgeAction.EXIT_ALL ||
                               action == BridgeAction.SCRATCH ||
                               action == BridgeAction.CANCEL_WORKING ||
                               action == BridgeAction.FLATTEN_AND_DISABLE;
                if (!allowed)
                    reason = "entry pending";
                return allowed;
            }

            if ((action == BridgeAction.TAKE_PARTIAL || action == BridgeAction.MOVE_STOP || action == BridgeAction.HOLD_FOR_RUNNER || action == BridgeAction.DOWNGRADE_TO_SCALP)
                && Position.MarketPosition == MarketPosition.Flat)
            {
                reason = "no open position";
                return false;
            }

            bool shellThinksInTrade =
			    shellMode == ShellMode.EntryPending ||
			    shellMode == ShellMode.InPosition ||
			    shellMode == ShellMode.ExitPending;
				
			bool actualPositionOpen = false;
			try
			{
			    actualPositionOpen = Position != null && Position.MarketPosition != MarketPosition.Flat;
			}
			catch
			{
			    actualPositionOpen = false;
			}
			
			if (action == BridgeAction.ENTER_LONG || action == BridgeAction.ENTER_SHORT)
			{
			    if (DryRunMode)
			    {
			        if (shellThinksInTrade)
			        {
			            reason = "already in position";
			            return false;
			        }
			    }
			    else
			    {
			        if (actualPositionOpen || shellThinksInTrade)
			        {
			            reason = "already in position";
			            return false;
			        }
			    }
			}

            return true;
        }

        private void ExecuteInstruction(BridgeInstruction instruction)
        {
            BridgeAction action = ParseAction(instruction.Action);

            switch (action)
            {
                case BridgeAction.HEARTBEAT:
                    AppendLog("HEARTBEAT", string.Format(CultureInfo.InvariantCulture, "id={0}", instruction.MessageId));
                    WriteOutboxEvent("HEARTBEAT", string.Format(CultureInfo.InvariantCulture, "id={0}", instruction.MessageId));
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
                    WriteOutboxEvent("REJECTED", string.Format(CultureInfo.InvariantCulture,
                        "id={0};action={1};reason=unknown action", instruction.MessageId, instruction.Action));
                    return;
            }
        }

        private void HandleEntry(BridgeInstruction instruction, MarketPosition desiredSide)
        {
            if (!signalIntakeEnabled || dailyLockout || shellMode == ShellMode.Disabled)
            {
                string rejectReason = !signalIntakeEnabled ? "intake_disabled"
                    : dailyLockout ? "daily_lockout"
                    : "shell_disabled";
                AppendLog("REJECT", string.Format(CultureInfo.InvariantCulture,
                    "id={0} reason={1}", instruction.MessageId, rejectReason));
                // Emit outbox event so the test harness can distinguish
                // "message not seen" from "message seen but intake was off".
                WriteOutboxEvent("REJECTED", string.Format(CultureInfo.InvariantCulture,
                    "id={0};action={1};reason={2}", instruction.MessageId, instruction.Action, rejectReason));
                return;
            }

            if (!string.IsNullOrWhiteSpace(instruction.ExpectedPositionState) &&
                !string.Equals(instruction.ExpectedPositionState.Trim(), "FLAT", StringComparison.OrdinalIgnoreCase))
            {
                AppendLog("REJECT", string.Format(CultureInfo.InvariantCulture,
                    "id={0} reason=expected_position_state mismatch expected={1} actual=FLAT", instruction.MessageId, instruction.ExpectedPositionState));
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
            // Store stop ticks so PlaceProtectiveOrders() can compute the stop price
            // from the actual fill price received in OnExecutionUpdate.
            pendingStopTicks = stopTicks;
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
                    ? RoundPriceToTick(Close[0] - (stopTicks * TickSize))
                    : RoundPriceToTick(Close[0] + (stopTicks * TickSize));
                SavePersistentState();
                WriteOutboxEvent("ACCEPTED", string.Format(CultureInfo.InvariantCulture,
                    "id={0};action={1};mode=DRYRUN", instruction.MessageId, instruction.Action));
                return;
            }

            // Stop and target are placed in OnExecutionUpdate -> PlaceProtectiveOrders()
            // once the entry fill is confirmed, using the actual fill price.
            // Do NOT call SetStopLoss/SetProfitTarget here — mixing Set-methods with
            // the Exit-method protective orders placed later triggers NT8's internal
            // order-handling rules and causes one category to be silently ignored.
            if (desiredSide == MarketPosition.Long)
            {
                EnterLong(quantity, EntryLongSignal);
            }
            else
            {
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
            WriteOutboxEvent("ENTRY_SUBMITTED", string.Format(CultureInfo.InvariantCulture,
                "id={0};side={1};qty={2};stop={3};target_ticks={4}", instruction.MessageId, desiredSide, quantity, pendingStopPrice, targetTicks));
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
            WriteOutboxEvent("PARTIAL_SUBMITTED", string.Format(CultureInfo.InvariantCulture,
                "id={0};qty={1}", instruction.MessageId, partialQty));
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

            double newStop = RoundPriceToTick(instruction.StopPrice.Value);
            if (!IsValidStopForCurrentPosition(newStop))
            {
                AppendLog("REJECT", string.Format(CultureInfo.InvariantCulture,
                    "id={0} reason=unsafe stop for current side stop={1} avg={2}", instruction.MessageId, newStop, Position.AveragePrice));
                WriteOutboxEvent("REJECTED", string.Format(CultureInfo.InvariantCulture,
                    "id={0};action=MOVE_STOP;reason=unsafe stop level", instruction.MessageId));
                return;
            }

            pendingStopPrice = newStop;

            if (DryRunMode)
            {
                AppendLog("DRYRUN_MOVE_STOP", string.Format(CultureInfo.InvariantCulture,
                    "id={0} stop_price={1}", instruction.MessageId, newStop));
                SavePersistentState();
                return;
            }

            // To move a live stop that was submitted via ExitLong/ShortStopMarket,
            // simply call the same method again with the new price.  NT8 detects
            // the existing working order for that signal name and updates its price.
            // Using SetStopLoss() here instead would be silently ignored because an
            // Exit-method order is already active on this position (internal handling rule).
            int qty = Math.Abs(Position.Quantity);
            if (Position.MarketPosition == MarketPosition.Long)
                stopOrder = ExitLongStopMarket(0, true, qty, newStop, LongStopSignal, EntryLongSignal);
            else if (Position.MarketPosition == MarketPosition.Short)
                stopOrder = ExitShortStopMarket(0, true, qty, newStop, ShortStopSignal, EntryShortSignal);

            AppendLog("MOVE_STOP", string.Format(CultureInfo.InvariantCulture,
                "id={0} stop_price={1}", instruction.MessageId, newStop));
            WriteOutboxEvent("STOP_MOVED", string.Format(CultureInfo.InvariantCulture,
                "id={0};stop_price={1}", instruction.MessageId, newStop));
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
            WriteOutboxEvent("EXIT_SUBMITTED", string.Format(CultureInfo.InvariantCulture, "reason={0}", reason));
            SavePersistentState();
        }

        private void FlattenAndDisable(string reason)
        {
            signalIntakeEnabled = false;
            shellMode = ShellMode.Disabled;
            ExitAll("FLATTEN_AND_DISABLE:" + reason);
            AppendLog("DISABLE", reason);
            WriteOutboxEvent("FLATTENED", string.Format(CultureInfo.InvariantCulture, "reason={0}", reason));
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
                    resumeIntakeWhenFlatAfterHeartbeatFault =
                        Position != null && Position.MarketPosition != MarketPosition.Flat;
                    if (resumeIntakeWhenFlatAfterHeartbeatFault)
                    {
                        AppendLog("HEARTBEAT_HOLD", "intake disabled until current position is flat");
                    }
                    SavePersistentState();
                }

                WriteOutboxEvent("HEARTBEAT_LOST", string.Format(CultureInfo.InvariantCulture,
                    "timeout_seconds={0};flatten={1}", HeartbeatTimeoutSeconds, FlattenOnHeartbeatLoss));
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
            if ((tsUtc - DateTime.UtcNow).TotalSeconds > 30)
                return "future timestamp";

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
            if ((action == BridgeAction.ENTER_LONG || action == BridgeAction.ENTER_SHORT) && string.IsNullOrWhiteSpace(instruction.TemplateName))
                return "missing template_name for entry";

            if ((action == BridgeAction.ENTER_LONG || action == BridgeAction.ENTER_SHORT) && instruction.Quantity > MaxPositionSize)
                return "quantity above max position size";
            // stop_ticks exceeding MaxStopTicksCap is not a hard rejection.
            // NormalizeStopTicks() already clamps to the cap at execution time.
            // We only reject values that are obviously erroneous (> 10× the cap),
            // e.g. accidental 5-digit entries from a misconfigured signal.
            if (instruction.StopTicks > MaxStopTicksCap * 10)
                return "stop ticks unreasonably large";
            if ((action == BridgeAction.MOVE_STOP) && (!instruction.StopPrice.HasValue || instruction.StopPrice.Value <= 0))
                return "invalid stop price";
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
		    JavaScriptSerializer serializer = new JavaScriptSerializer();
		    return serializer.Deserialize<T>(json);
		}
		
		private static string SerializeJson<T>(T value)
		{
		    JavaScriptSerializer serializer = new JavaScriptSerializer();
		    return serializer.Serialize(value);
		}

        private static void EnsureDirectory(string path)
        {
            if (string.IsNullOrWhiteSpace(path))
                return;
            if (!Directory.Exists(path))
                Directory.CreateDirectory(path);
        }

		private double RoundPriceToTick(double price)
		{
		    if (TickSize <= 0)
		        return price;
		
		    return Math.Round(price / TickSize, MidpointRounding.AwayFromZero) * TickSize;
		}
		
        private bool TryMoveFile(string sourcePath, string destinationDirectory, out string destinationPath)
        {
            destinationPath = string.Empty;
            try
            {
                EnsureDirectory(destinationDirectory);
                string fileName = Path.GetFileName(sourcePath);
                destinationPath = Path.Combine(destinationDirectory,
                    string.Format(CultureInfo.InvariantCulture,
                        "{0:yyyyMMdd_HHmmssfff}_{1}", DateTime.UtcNow, fileName));

                if (File.Exists(destinationPath))
                    File.Delete(destinationPath);

                File.Move(sourcePath, destinationPath);
                return true;
            }
            catch (Exception ex)
            {
                AppendLog("WARN", string.Format(CultureInfo.InvariantCulture,
                    "move failed src={0} dst={1} err={2}", sourcePath, destinationDirectory, ex.Message));
                return false;
            }
        }

       private void AppendLog(string eventType, string message)
		{
		    string posSide = "<unavailable>";
		    int posQty = 0;
		
		    try
		    {
		        if (Position != null)
		        {
		            posSide = Position.MarketPosition.ToString();
		            posQty = Position.Quantity;
		        }
		    }
		    catch
		    {
		    }
		
		    string line = string.Format(CultureInfo.InvariantCulture,
		        "{0:o}|{1}|instr={2}|template={3}|mode={4}|pos={5}|qty={6}|msg={7}",
		        DateTime.UtcNow,
		        eventType,
		        lastInstructionId,
		        activeTemplate,
		        shellMode,
		        posSide,
		        posQty,
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

        // NOTE: SetProfitTarget() is intentionally NOT used here.
        // Mixing Set-methods (SetProfitTarget/SetStopLoss) with Exit-methods
        // (ExitLongStopMarket) on the same position triggers NT8's internal
        // order-handling rules and causes one category of orders to be silently
        // ignored.  All protective orders on this shell use Exit-methods with
        // isLiveUntilCancelled=true so they can be submitted once and updated
        // (moved) by resubmitting with a new price.
        //
        // InitialTarget is now submitted from OnExecutionUpdate once the entry
        // fills (see PlaceProtectiveOrders).  This stub is kept as documentation.
        private void SetInitialTarget(MarketPosition desiredSide, int targetTicks)
        {
            // Intentionally empty — targets are placed in PlaceProtectiveOrders().
        }

        private void SubmitPendingStopIfReady()
        {
            // Called at the top of every OnBarUpdate as a safety-net fallback only.
            // The primary stop+target placement happens immediately in OnExecutionUpdate
            // via PlaceProtectiveOrders() the moment the entry fill is confirmed.
            // This path fires only when pendingStopNeeded is still true, which means
            // the OnExecutionUpdate placement was skipped (e.g. recovery restart).
            //
            // CRITICAL: ExitLong/ShortStopMarket with isLiveUntilCancelled=true is used
            // instead of SetStopLoss().  SetStopLoss() recalculates and resubmits on
            // every bar update and conflicts with the Exit-method target order, causing
            // NT8 to silently discard Exit orders per its internal order handling rules.
            // With isLiveUntilCancelled=true the stop persists across bars and is moved
            // by calling the same Exit method again with a new price.
            if (!pendingStopNeeded || DryRunMode)
                return;
            if (shellMode != ShellMode.InPosition)
                return;
            if (pendingStopPrice <= 0)
                return;
            if (Position == null || Position.MarketPosition == MarketPosition.Flat)
                return;

            int qty = Math.Abs(Position.Quantity);
            if (Position.MarketPosition == MarketPosition.Long)
                stopOrder = ExitLongStopMarket(0, true, qty, pendingStopPrice, LongStopSignal, EntryLongSignal);
            else if (Position.MarketPosition == MarketPosition.Short)
                stopOrder = ExitShortStopMarket(0, true, qty, pendingStopPrice, ShortStopSignal, EntryShortSignal);
            else
                return;

            pendingStopNeeded = false;

            AppendLog("STOP_INIT", string.Format(CultureInfo.InvariantCulture,
                "pos={0} stop_price={1} qty={2} source=OnBarUpdate_fallback", Position.MarketPosition, pendingStopPrice, qty));
            WriteOutboxEvent("STOP_ATTACHED", string.Format(CultureInfo.InvariantCulture,
                "side={0};stop_price={1};qty={2};source=fallback",
                Position.MarketPosition, pendingStopPrice, qty));
        }

        /// <summary>
        /// Submits protective stop and profit-target orders immediately after an entry fills.
        /// Called from OnExecutionUpdate with the actual fill price.
        ///
        /// KEY DESIGN RULE — use Exit methods only, never SetStopLoss/SetProfitTarget:
        ///   • SetStopLoss/SetProfitTarget ("Set methods") recalculate on every bar
        ///     and conflict with Exit-method orders already active, causing NT8 to
        ///     silently discard them per its internal order-handling rules.
        ///   • ExitLong/ShortStopMarket + ExitLong/ShortLimit with isLiveUntilCancelled=true
        ///     persist across bars without resubmission.
        ///   • To move the stop, call ExitLong/ShortStopMarket again with a new price;
        ///     NT8 updates the working order in-place.
        /// </summary>
        private void PlaceProtectiveOrders(string entrySignalName, double fillPrice, int fillQty)
        {
            if (DryRunMode)
                return;

            bool isLong = entrySignalName == EntryLongSignal;

            // Compute stop price from the stored tick distance (set in HandleEntry).
            int stopTicks = pendingStopTicks > 0 ? pendingStopTicks : Math.Min(12, MaxStopTicksCap);
            double stopPrice = isLong
                ? RoundPriceToTick(fillPrice - stopTicks * TickSize)
                : RoundPriceToTick(fillPrice + stopTicks * TickSize);

            // Cap stop distance.
            stopPrice = isLong
                ? Math.Max(stopPrice, RoundPriceToTick(fillPrice - MaxStopTicksCap * TickSize))
                : Math.Min(stopPrice, RoundPriceToTick(fillPrice + MaxStopTicksCap * TickSize));

            pendingStopPrice = stopPrice;

            // Compute target price from stored target ticks.
            int targetTicks = pendingTargetTicks > 0 ? pendingTargetTicks : stopTicks;
            double targetPrice = isLong
                ? RoundPriceToTick(fillPrice + targetTicks * TickSize)
                : RoundPriceToTick(fillPrice - targetTicks * TickSize);

            string stopSignal   = isLong ? LongStopSignal   : ShortStopSignal;
            string targetSignal = isLong ? LongTargetSignal : ShortTargetSignal;
            string fromEntry    = isLong ? EntryLongSignal  : EntryShortSignal;

            if (isLong)
            {
                stopOrder   = ExitLongStopMarket(0, true, fillQty, stopPrice,   stopSignal,   fromEntry);
                targetOrder = ExitLongLimit     (0, true, fillQty, targetPrice, targetSignal, fromEntry);
            }
            else
            {
                stopOrder   = ExitShortStopMarket(0, true, fillQty, stopPrice,   stopSignal,   fromEntry);
                targetOrder = ExitShortLimit     (0, true, fillQty, targetPrice, targetSignal, fromEntry);
            }

            pendingStopNeeded = false;

            AppendLog("STOP_INIT", string.Format(CultureInfo.InvariantCulture,
                "side={0} fill={1} stop={2} target={3} qty={4}",
                isLong ? "Long" : "Short", fillPrice, stopPrice, targetPrice, fillQty));
            WriteOutboxEvent("STOP_ATTACHED", string.Format(CultureInfo.InvariantCulture,
                "side={0};fill_price={1};stop_price={2};target_price={3};qty={4}",
                isLong ? "Long" : "Short", fillPrice, stopPrice, targetPrice, fillQty));
            SavePersistentState();
        }

        private void EnsureProtectiveOrdersAfterEntry(double fillPrice)
        {
            // Used during recovery restarts to reattach a stop when the OnExecutionUpdate
            // fill callback was missed.  Same Exit-method pattern as PlaceProtectiveOrders.
            if (DryRunMode || Position == null || Position.MarketPosition == MarketPosition.Flat)
                return;

            if (pendingStopPrice <= 0)
            {
                int fallbackTicks = Math.Min(12, MaxStopTicksCap);
                pendingStopPrice = Position.MarketPosition == MarketPosition.Long
                    ? RoundPriceToTick(fillPrice - fallbackTicks * TickSize)
                    : RoundPriceToTick(fillPrice + fallbackTicks * TickSize);
                AppendLog("WARN", string.Format(CultureInfo.InvariantCulture,
                    "fallback protective stop applied at {0}", pendingStopPrice));
            }

            int qty = Math.Abs(Position.Quantity);
            if (Position.MarketPosition == MarketPosition.Long)
                stopOrder = ExitLongStopMarket(0, true, qty, pendingStopPrice, LongStopSignal, EntryLongSignal);
            else if (Position.MarketPosition == MarketPosition.Short)
                stopOrder = ExitShortStopMarket(0, true, qty, pendingStopPrice, ShortStopSignal, EntryShortSignal);

            pendingStopNeeded = false;

            AppendLog("STOP_INIT", string.Format(CultureInfo.InvariantCulture,
                "pos={0} stop_price={1} qty={2}", Position.MarketPosition, pendingStopPrice, qty));
            WriteOutboxEvent("STOP_ATTACHED", string.Format(CultureInfo.InvariantCulture,
                "side={0};stop_price={1};qty={2};source=recovery",
                Position.MarketPosition, pendingStopPrice, qty));
        }

        private bool IsValidStopForCurrentPosition(double stopPrice)
        {
            double minGap = TickSize;
            if (Position.MarketPosition == MarketPosition.Long)
                return stopPrice <= RoundPriceToTick(Close[0] - minGap);
            if (Position.MarketPosition == MarketPosition.Short)
                return stopPrice >= RoundPriceToTick(Close[0] + minGap);
            return false;
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
            else if (order.Name == LongStopSignal || order.Name == ShortStopSignal
                     || order.OrderType == OrderType.StopMarket)
                stopOrder = order;
            else if (order.Name == LongTargetSignal || order.Name == ShortTargetSignal
                     || order.OrderType == OrderType.Limit)
                targetOrder = order;
        }

        private void ResetRuntimeTradeState()
        {
            entryOrder = null;
            exitOrder = null;
            stopOrder = null;
            targetOrder = null;
            pendingStopPrice = 0.0;
            pendingStopNeeded = false;
            pendingTargetTicks = 0;
            pendingStopTicks = 0;
            activePositionId = string.Empty;
            entryBarNumber = -1;
            lastKnownPositionQuantity = 0;
        }

        private bool HasRecoverableOpenState()
        {
            if (!RecoverOpenPositionOnStartup)
                return false;

            return !string.IsNullOrWhiteSpace(activePositionId)
                || pendingStopPrice > 0
                || pendingTargetTicks > 0
                || shellMode == ShellMode.InPosition
                || shellMode == ShellMode.ExitPending
                || shellMode == ShellMode.Recovery;
        }

        private void ContinueStartupRecoveryIfNeeded()
        {
            if (!pendingStartupRecovery)
                return;

            TryRecoverPositionState();
        }

        private bool IsStartupRecoveryGraceActive()
        {
            return startupRecoveryDeadlineUtc != DateTime.MinValue
                && DateTime.UtcNow < startupRecoveryDeadlineUtc;
        }

        private void BeginStartupRecoveryWindow()
        {
            if (startupRecoveryDeadlineUtc == DateTime.MinValue)
                startupRecoveryDeadlineUtc = DateTime.UtcNow.AddSeconds(StartupRecoveryGraceSeconds);
        }

        private bool TryAutoResumeIntakeAfterFlatTradeCompletion()
        {
            if (!resumeIntakeWhenFlatAfterHeartbeatFault)
                return false;
            if (dailyLockout)
                return false;
            if (Position != null && Position.MarketPosition != MarketPosition.Flat)
                return false;

            signalIntakeEnabled = true;
            heartbeatFaulted = false;
            resumeIntakeWhenFlatAfterHeartbeatFault = false;
            lastBridgeMessageUtc = DateTime.UtcNow;
            shellMode = ShellMode.Idle;

            AppendLog("FLAT_AUTO_RESET",
                "intake re-enabled after trade completed flat following heartbeat fault");
            SavePersistentState();
            return true;
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

		private BridgeInstruction DeserializeInstruction(string json)
		{
		    if (string.IsNullOrWhiteSpace(json))
		        return null;
		
		    JavaScriptSerializer serializer = new JavaScriptSerializer();
		    Dictionary<string, object> raw = serializer.Deserialize<Dictionary<string, object>>(json);
		
		    if (raw == null)
		        return null;
		
		    BridgeInstruction instruction = new BridgeInstruction();
		
		    instruction.MessageId = GetString(raw, "message_id");
		    instruction.Timestamp = GetString(raw, "timestamp");
		    instruction.Instrument = GetString(raw, "instrument");
		    instruction.Timeframe = GetString(raw, "timeframe");
		    instruction.Action = GetString(raw, "action");
		    instruction.Side = GetString(raw, "side");
		    instruction.TemplateName = GetString(raw, "template_name");
		    instruction.Confidence = GetDouble(raw, "confidence", 0.0);
		    instruction.EntryMode = GetString(raw, "entry_mode");
		    instruction.Quantity = GetInt(raw, "quantity", 0);
		    instruction.StopMode = GetString(raw, "stop_mode");
		    instruction.StopTicks = GetInt(raw, "stop_ticks", 0);
		    instruction.StopPrice = GetNullableDouble(raw, "stop_price");
		    instruction.TargetMode = GetString(raw, "target_mode");
		    instruction.TargetTicks = GetNullableInt(raw, "target_ticks");
		    instruction.PartialTargetTicks = GetNullableInt(raw, "partial_target_ticks");
		    instruction.RunnerMode = GetString(raw, "runner_mode");
		    instruction.MaxHoldBars = GetNullableInt(raw, "max_hold_bars");
		    instruction.ThesisId = GetString(raw, "thesis_id");
		    instruction.Notes = GetString(raw, "notes");
		    instruction.PositionId = GetString(raw, "position_id");
		    instruction.ExpectedPositionState = GetString(raw, "expected_position_state");
		    instruction.SignalExpirySeconds = GetNullableInt(raw, "signal_expiry_seconds");
		
		    return instruction;
		}
		
		private string GetString(Dictionary<string, object> raw, string key)
		{
		    object value;
		    if (!raw.TryGetValue(key, out value) || value == null)
		        return null;
		    return Convert.ToString(value, CultureInfo.InvariantCulture);
		}
		
		private int GetInt(Dictionary<string, object> raw, string key, int defaultValue)
		{
		    object value;
		    if (!raw.TryGetValue(key, out value) || value == null)
		        return defaultValue;
		
		    try
		    {
		        return Convert.ToInt32(value, CultureInfo.InvariantCulture);
		    }
		    catch
		    {
		        return defaultValue;
		    }
		}
		
		private int? GetNullableInt(Dictionary<string, object> raw, string key)
		{
		    object value;
		    if (!raw.TryGetValue(key, out value) || value == null)
		        return null;
		
		    try
		    {
		        return Convert.ToInt32(value, CultureInfo.InvariantCulture);
		    }
		    catch
		    {
		        return null;
		    }
		}
		
		private double GetDouble(Dictionary<string, object> raw, string key, double defaultValue)
		{
		    object value;
		    if (!raw.TryGetValue(key, out value) || value == null)
		        return defaultValue;
		
		    try
		    {
		        return Convert.ToDouble(value, CultureInfo.InvariantCulture);
		    }
		    catch
		    {
		        return defaultValue;
		    }
		}
		
		private double? GetNullableDouble(Dictionary<string, object> raw, string key)
		{
		    object value;
		    if (!raw.TryGetValue(key, out value) || value == null)
		        return null;
		
		    try
		    {
		        return Convert.ToDouble(value, CultureInfo.InvariantCulture);
		    }
		    catch
		    {
		        return null;
		    }
		}

		private void TryRecoverPositionState()
		{
		    if (!RecoverOpenPositionOnStartup)
		    {
		        pendingStartupRecovery = false;
		        startupRecoveryDeadlineUtc = DateTime.MinValue;
		        if (Position == null || Position.MarketPosition == MarketPosition.Flat)
		        {
		            ResetRuntimeTradeState();
		            shellMode = signalIntakeEnabled ? ShellMode.Idle : ShellMode.Disabled;
		        }
		        return;
		    }
		
		    if (Position == null)
		    {
		        if (HasRecoverableOpenState())
		        {
		            bool startingRecoveryWait = startupRecoveryDeadlineUtc == DateTime.MinValue;
		            BeginStartupRecoveryWindow();
		            pendingStartupRecovery = true;
		            shellMode = ShellMode.Recovery;
		            if (IsStartupRecoveryGraceActive())
		            {
		                if (startingRecoveryWait)
		                {
		                    AppendLog("RECOVERY_WAIT", string.Format(CultureInfo.InvariantCulture,
		                        "Position was null during startup; deferring recovery for up to {0} seconds.",
		                        StartupRecoveryGraceSeconds));
		                    LogReconciliationSnapshot("RECOVERY_POSITION_PENDING");
		                }
		                return;
		            }

		            AppendLog("RECOVERY_FAIL", "Position remained unavailable beyond startup recovery grace window; manual inspection required.");
		            signalIntakeEnabled = false;
		            SavePersistentState();
		            LogReconciliationSnapshot("RECOVERY_TIMEOUT");
		            return;
		        }
		
		        pendingStartupRecovery = false;
		        startupRecoveryDeadlineUtc = DateTime.MinValue;
		        shellMode = signalIntakeEnabled ? ShellMode.Idle : ShellMode.Disabled;
		        AppendLog("RECOVERY_WARN", "Position was null during DataLoaded; skipping recovery.");
		        LogReconciliationSnapshot("RECOVERY_POSITION_NULL");
		        return;
		    }
		
		    if (Position.MarketPosition == MarketPosition.Flat)
		    {
		        if (HasRecoverableOpenState())
		        {
		            bool startingRecoveryWait = startupRecoveryDeadlineUtc == DateTime.MinValue;
		            BeginStartupRecoveryWindow();
		            pendingStartupRecovery = true;
		            shellMode = ShellMode.Recovery;
		            if (IsStartupRecoveryGraceActive())
		            {
		                if (startingRecoveryWait)
		                    LogReconciliationSnapshot("RECOVERY_FLAT_WAIT");
		                return;
		            }

		            AppendLog("RECOVERY_FAIL", "Position remained flat beyond startup recovery grace window; manual inspection required.");
		            signalIntakeEnabled = false;
		            SavePersistentState();
		            LogReconciliationSnapshot("RECOVERY_TIMEOUT");
		            return;
		        }

		        pendingStartupRecovery = false;
		        startupRecoveryDeadlineUtc = DateTime.MinValue;
		        ResetRuntimeTradeState();
		        shellMode = signalIntakeEnabled ? ShellMode.Idle : ShellMode.Disabled;
		        SavePersistentState();
		        LogReconciliationSnapshot("RECOVERY_FLAT");
		        return;
		    }
		
		    pendingStartupRecovery = false;
		    startupRecoveryDeadlineUtc = DateTime.MinValue;
		    shellMode = ShellMode.Recovery;
		    AppendLog("RECOVERY", string.Format(CultureInfo.InvariantCulture,
		        "detected non-flat position side={0} qty={1}", Position.MarketPosition, Position.Quantity));
		
		    if (pendingStopPrice > 0 && !DryRunMode)
		    {
		        EnsureProtectiveOrdersAfterEntry(Position.AveragePrice);
		        shellMode = ShellMode.InPosition;
		        AppendLog("RECOVERY", "protective stop restored from persisted state");
		        SavePersistentState();
		        LogReconciliationSnapshot("RECOVERY_RESTORED");
		        return;
		    }
		
		    if (!DryRunMode && RecoveryFallbackStopTicks > 0)
		    {
		        pendingStopPrice = Position.MarketPosition == MarketPosition.Long
		            ? RoundPriceToTick(Position.AveragePrice - (RecoveryFallbackStopTicks * TickSize))
		            : RoundPriceToTick(Position.AveragePrice + (RecoveryFallbackStopTicks * TickSize));
		        AppendLog("RECOVERY_WARN", string.Format(CultureInfo.InvariantCulture,
		            "persisted stop missing; applying fallback stop ticks={0} stop={1}", RecoveryFallbackStopTicks, pendingStopPrice));
		        EnsureProtectiveOrdersAfterEntry(Position.AveragePrice);
		        shellMode = ShellMode.InPosition;
		        SavePersistentState();
		        LogReconciliationSnapshot("RECOVERY_FALLBACK_STOP");
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
                    ProcessedIds = processedMessageOrder.TakeLastSafe(ProcessedIdsRetainCount).ToList(),
                    LastHealthSnapshotUtc = lastHealthSnapshotUtc == DateTime.MinValue ? string.Empty : lastHealthSnapshotUtc.ToString("o", CultureInfo.InvariantCulture)
                };

                WriteTextAtomically(StateFilePath, SerializeJson(state));
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
            if ((DateTime.UtcNow - lastHealthSnapshotUtc).TotalSeconds >= Math.Max(5, HealthSnapshotIntervalSeconds))
                LogReconciliationSnapshot("PERIODIC");
        }

        private void LoadProcessedIds()
        {
            try
            {
                if (!PersistProcessedIds || string.IsNullOrWhiteSpace(ProcessedIdsFilePath) || !File.Exists(ProcessedIdsFilePath))
                    return;

                bool restoredFromState = false;
                foreach (string line in File.ReadAllLines(ProcessedIdsFilePath, Encoding.UTF8))
                {
                    string id = line == null ? string.Empty : line.Trim();
                    if (string.IsNullOrWhiteSpace(id))
                        continue;
                    if (seenMessageIds.Add(id))
                        processedMessageOrder.Enqueue(id);
                }

                if (processedMessageOrder.Count == 0 && File.Exists(StateFilePath))
                {
                    PersistentShellState persisted = DeserializeJson<PersistentShellState>(File.ReadAllText(StateFilePath, Encoding.UTF8));
                    if (persisted != null && persisted.ProcessedIds != null)
                    {
                        foreach (string id in persisted.ProcessedIds)
                        {
                            if (string.IsNullOrWhiteSpace(id))
                                continue;
                            if (seenMessageIds.Add(id.Trim()))
                                processedMessageOrder.Enqueue(id.Trim());
                        }
                        restoredFromState = processedMessageOrder.Count > 0;
                    }
                }

                TrimProcessedIdsRetention();
                AppendLog("STATE", string.Format(CultureInfo.InvariantCulture,
                    "processed_id_count={0} restored_from_state={1}", processedMessageOrder.Count, restoredFromState));
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
            bool trimmed = TrimProcessedIdsRetention();

            if (!PersistProcessedIds || string.IsNullOrWhiteSpace(ProcessedIdsFilePath))
                return;

            try
            {
                EnsureDirectory(Path.GetDirectoryName(ProcessedIdsFilePath));
                if (trimmed)
                    WriteProcessedIdsSnapshot();
                else
                    File.AppendAllText(ProcessedIdsFilePath, id + Environment.NewLine, Encoding.UTF8);
            }
            catch (Exception ex)
            {
                AppendLog("WARN", string.Format(CultureInfo.InvariantCulture,
                    "failed to append processed id err={0}", ex.Message));
            }
        }

        private bool TrimProcessedIdsRetention()
        {
            bool trimmed = false;
            while (processedMessageOrder.Count > Math.Max(100, ProcessedIdsRetainCount))
            {
                string oldest = processedMessageOrder.Dequeue();
                seenMessageIds.Remove(oldest);
                trimmed = true;
            }
            return trimmed;
        }

        private void WriteProcessedIdsSnapshot()
        {
            if (!PersistProcessedIds || string.IsNullOrWhiteSpace(ProcessedIdsFilePath))
                return;

            EnsureDirectory(Path.GetDirectoryName(ProcessedIdsFilePath));
            StringBuilder builder = new StringBuilder();
            foreach (string id in processedMessageOrder)
                builder.AppendLine(id);

            WriteTextAtomically(ProcessedIdsFilePath, builder.ToString());
        }

        private void WriteTextAtomically(string finalPath, string contents)
        {
            string tmpPath = finalPath + ".tmp";
            File.WriteAllText(tmpPath, contents ?? string.Empty, Encoding.UTF8);
            if (File.Exists(finalPath))
                File.Delete(finalPath);
            File.Move(tmpPath, finalPath);
        }

        private void LogReconciliationSnapshot(string reason)
		{
		    lastHealthSnapshotUtc = DateTime.UtcNow;
		
		    string posSide = "<unavailable>";
		    int posQty = 0;
		    double avgPrice = 0.0;
		
		    try
		    {
		        if (Position != null)
		        {
		            posSide = Position.MarketPosition.ToString();
		            posQty = Position.Quantity;
		            avgPrice = Position.AveragePrice;
		        }
		    }
		    catch
		    {
		    }
		
		    AppendLog("HEALTH", string.Format(CultureInfo.InvariantCulture,
		        "reason={0} shell_mode={1} intake={2} heartbeat_faulted={3} daily_lockout={4} active_position_id={5} pos_side={6} pos_qty={7} avg={8} entry_order={9} stop_order={10} target_order={11} exit_order={12} pending_stop={13} pending_target_ticks={14} queue_depth={15} last_msg_utc={16:o}",
		        reason,
		        shellMode,
		        signalIntakeEnabled,
		        heartbeatFaulted,
		        dailyLockout,
		        activePositionId,
		        posSide,
		        posQty,
		        avgPrice,
		        entryOrder == null ? "<null>" : entryOrder.OrderId,
		        stopOrder == null ? "<null>" : stopOrder.OrderId,
		        targetOrder == null ? "<null>" : targetOrder.OrderId,
		        exitOrder == null ? "<null>" : exitOrder.OrderId,
		        pendingStopPrice,
		        pendingTargetTicks,
		        pendingInstructions.Count,
		        lastBridgeMessageUtc));
		}

        private void WriteOutboxEvent(string status, string detail)
		{
		    if (!EnableOutboxEvents || string.IsNullOrWhiteSpace(OutboxDirectory))
		        return;
		
		    try
		    {
		        EnsureDirectory(OutboxDirectory);
		
		        string id = string.IsNullOrWhiteSpace(lastInstructionId)
		            ? Guid.NewGuid().ToString("N")
		            : lastInstructionId;
		
		        string safeStatus = string.IsNullOrWhiteSpace(status)
		            ? "UNKNOWN"
		            : status.Trim().ToUpperInvariant();
		
		        string posSide = "<unavailable>";
		        int posQty = 0;
		
		        try
		        {
		            if (Position != null)
		            {
		                posSide = Position.MarketPosition.ToString();
		                posQty = Position.Quantity;
		            }
		        }
		        catch
		        {
		        }
		
		        string fileName = string.Format(CultureInfo.InvariantCulture,
		            "{0:yyyyMMdd_HHmmssfff}_{1}_{2}.evt.json",
		            DateTime.UtcNow,
		            safeStatus,
		            id.Replace(":", "_").Replace("/", "_"));
		
		        string payload = string.Format(CultureInfo.InvariantCulture,
		            "{{\"timestamp_utc\":\"{0:o}\",\"status\":\"{1}\",\"instruction_id\":\"{2}\",\"shell_mode\":\"{3}\",\"position\":\"{4}\",\"quantity\":{5},\"detail\":\"{6}\"}}",
		            DateTime.UtcNow,
		            safeStatus,
		            id,
		            shellMode,
		            posSide,
		            posQty,
		            (detail ?? string.Empty).Replace("\\", "\\\\").Replace("\"", "\\\""));
		
		        WriteTextAtomically(Path.Combine(OutboxDirectory, fileName), payload);
		    }
		    catch (Exception ex)
		    {
		        AppendLog("WARN", string.Format(CultureInfo.InvariantCulture,
		            "outbox write failed status={0} err={1}", status, ex.Message));
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
        [Display(Name = "OutboxDirectory", GroupName = "Bridge", Order = 4)]
        public string OutboxDirectory { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "TemplateDirectory", GroupName = "Bridge", Order = 5)]
        public string TemplateDirectory { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "LogFilePath", GroupName = "Bridge", Order = 6)]
        public string LogFilePath { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "StateFilePath", GroupName = "Bridge", Order = 7)]
        public string StateFilePath { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "ProcessedIdsFilePath", GroupName = "Bridge", Order = 8)]
        public string ProcessedIdsFilePath { get; set; }

        [NinjaScriptProperty]
        [Range(1, 30)]
        [Display(Name = "PollIntervalSeconds", GroupName = "Bridge", Order = 9)]
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

        [NinjaScriptProperty]
        [Display(Name = "EnableOutboxEvents", GroupName = "Execution", Order = 6)]
        public bool EnableOutboxEvents { get; set; }

        [NinjaScriptProperty]
        [Range(0, 200)]
        [Display(Name = "RecoveryFallbackStopTicks", GroupName = "Execution", Order = 7)]
        public int RecoveryFallbackStopTicks { get; set; }

        [NinjaScriptProperty]
        [Range(5, 600)]
        [Display(Name = "HealthSnapshotIntervalSeconds", GroupName = "Execution", Order = 8)]
        public int HealthSnapshotIntervalSeconds { get; set; }
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
