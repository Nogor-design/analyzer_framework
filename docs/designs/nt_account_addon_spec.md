# NT Account-Manager AddOn — data-pipe spec (Phase 2d)

*Drafted by Gemini (NT8 help-guide research) 2026-06-08 from handoff intent; curated by Claude.
Feeds [`account_risk_engine.md`](account_risk_engine.md) §2d. The AddOn is a **thin read-only
data pipe** — all risk logic stays in the Python DD engine (`analysis/risk/`).*

> **Sourcing note.** Citations are official `ninjatrader.com` NT8 help-guide pages (better than
> the prop-catalog's grounding redirects), and the API names below are accurate NT8 NinjaScript.
> Still verify event interleaving and the realized-P&L reset behavior **in NT** before trusting
> the live feed (see §Gotchas) — these are the bits that silently corrupt account state.

## What the AddOn must push to the DD engine

The engine (`dd_engine.DdEngine`) needs exactly three things per connected account, mapped here
to the NT API:

| DD-engine input | NT source | Notes |
|---|---|---|
| `on_value(account_value)` — intraday equity **incl. unrealized** (drives the intraday trail + violation) | `account.Get(AccountItem.NetLiquidation, Currency.UsDollar)` | NetLiquidation = value if all positions closed at market = balance + unrealized. This is the right feed for APEX intraday trailing. |
| `on_session_close(closing_balance)` — realized close (drives EOD trail) | snapshot `AccountItem.CashValue` (or realized balance) at the session boundary | EOD firms only; APEX intraday plan uses `NetLiquidation` live. |
| `add_realized(pnl)` / `realized_today` (drives the daily-loss limit) | **computed**: `Get(RealizedProfitLoss) − session_start_baseline` (see Gotcha 1) | NOT a direct field — must be a baselined diff. |

Account identity / onboarding (entered once by the client): firm + profile version + account_size
+ account_type + drawdown_type. The AddOn supplies live `NetLiquidation` / positions; Python owns
threshold/lock/violation/budget.

## NT8 API surface (read-only)

**Enumerate / get account** — `NinjaTrader.Cbi.Account`, static `Account.All` (`AccountCollection`).
Always `lock (Account.All)` when enumerating (accounts connect/disconnect on other threads):
```csharp
Account acct;
lock (Account.All) { acct = Account.All.FirstOrDefault(a => a.Name == accountName); }
```

**Read values** — `account.Get(AccountItem item, Currency currency)`:
- `AccountItem.CashValue` — cash balance
- `AccountItem.NetLiquidation` — total value incl. unrealized ← **primary risk feed**
- `AccountItem.RealizedProfitLoss` — realized since last *broker* reset (net of commissions)
- `AccountItem.UnrealizedProfitLoss` — open P&L
- `AccountItem.GrossRealizedProfitLoss` — realized before commissions

**Positions** — `account.Positions` (`PositionCollection` of `Position`): `MarketPosition`
(Flat/Long/Short), `Quantity` (int), `AveragePrice` (double),
`Position.GetUnrealizedProfitLoss(PerformanceUnit.Currency, price)`.

**Live updates** (raised on NT core background threads — marshal UI via
`Dispatcher.InvokeAsync(...)`):
- `account.AccountItemUpdate` → `AccountItemEventArgs{ AccountItem, Value, Currency, Account }`
- `account.PositionUpdate` → `PositionEventArgs{ Position, MarketPosition, Quantity, AveragePrice, Operation }`
- `account.ExecutionUpdate` → `ExecutionEventArgs{ Execution{ Price, Quantity, Time, OrderId } }`
- `account.OrderUpdate` → `OrderEventArgs{ OrderState, Order, Quantity, Filled, AverageFillPrice }`

**Connection state** — `account.Connection.Status` → `ConnectionStatus`
(Connected/Connecting/Disconnected/Disconnecting/ConnectionLost). Global monitor via
`Connection.ConnectionStatusUpdate`.

## Lifecycle (no leaks)

- Subscribe: `AddOnBase` at `State == State.DataLoaded`; `NTWindow` in `OnWindowCreated()`.
- **Unsubscribe** every handler (`account.PositionUpdate -= ...`) at `State.Terminated` /
  `OnWindowDestroyed()` — orphaned handlers leak and fire on dead UI.
- Keep handlers lightweight; only marshal small property writes to the Dispatcher.

## Gotchas (verify in NT before live — these break account state silently)

1. **Realized P&L does NOT reset at the trading-session boundary.** `AccountItem.RealizedProfitLoss`
   resets at the *broker* reset (~5:00 PM ET futures) or platform restart — not at our session open.
   To get a custom "daily realized": snapshot the value at session start and subtract. **Persist the
   baseline to disk** — an AddOn/NT restart mid-session otherwise loses it and the daily-loss limit
   silently mis-reads. This directly drives `dd_engine.add_realized`/`realized_today`.
2. **Event interleaving** between `AccountItemUpdate` (cash) and `ExecutionUpdate` (fill) for the same
   event is not guaranteed ordered — snapshot P&L on a settled state, not mid-fill, to avoid races.
3. **Sim reconnects** may replay queued `ExecutionUpdate`s — verify before trusting sim feeds for parity.
4. **Threading:** all account events are background-thread; never touch UI directly.

## Build note

This is the **2d** sub-phase and needs NT to build/test — defer until the Python engine is replay-
validated against a real account (the Phase-2 gate). When built, it lives at
`bin/Custom/AddOns/TaAccountManager.cs` and pushes `NetLiquidation` + positions to the Python engine
(via the existing bridge file/IPC pattern); it computes nothing.

*Raw Gemini research: `C:\Users\Owner\Downloads\nt_addon_account_api_gemini.md`.*
