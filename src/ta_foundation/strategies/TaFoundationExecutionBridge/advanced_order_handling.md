# Advanced Order Handling

> ⚠️ **Important:** Advanced order handling is intended for **experienced programmers**.

Advanced order handling allows you to:
- Submit orders
- Modify orders
- Cancel orders

…at your discretion using **event-driven logic inside a strategy**.

Each order method in the **Managed Approach** includes overloads designed specifically for advanced handling.

---

## 📌 Key Concepts

### Live Orders

Orders can remain active until:
- You explicitly call `CancelOrder()`, or  
- The order’s **Time in Force (TIF)** expires  

This gives you full control over order lifecycle instead of relying on bar-close behavior.

---

### Live-Until-Cancelled Orders

Certain overloads (e.g., `EnterLongLimit()`) allow you to submit orders that remain active indefinitely.

🔑 **Requirement:**  
You must store a reference to the returned `Order` object to manage it later.

---

## 🧠 Order Object Behavior

All order methods return an `Order` object with these properties:

- **Dynamic State:** Always reflects the current order status  
- **OrderId is NOT stable:** It may change during the order lifecycle  
- **Equality Check:** Compare orders using object equality (`==`)

---

## 💻 Example: Basic Order Tracking

```csharp
private Order entryOrder = null;

protected override void OnBarUpdate()
{
    if (entryOrder == null && Close[0] > Open[0])
        EnterLong("myEntryOrder");
}

protected override void OnOrderUpdate(Order order, double limitPrice, double stopPrice,
    int quantity, int filled, double averageFillPrice, OrderState orderState,
    DateTime time, ErrorCode error, string nativeError)
{
    if (order.Name == "myEntryOrder" && orderState != OrderState.Filled)
        entryOrder = order;

    if (entryOrder != null && entryOrder == order)
    {
        if (order.OrderState == OrderState.Cancelled && order.Filled == 0)
            entryOrder = null;

        if (order.OrderState == OrderState.Filled)
            entryOrder = null;
    }
}
```

---

## 🔄 Transition: Historical → Live Orders

When switching to real-time:
- Historical orders are **resubmitted**
- Order IDs and OCO IDs are **updated**

⚠️ You MUST update your order references to the live versions.

---

## 💻 Example: Transition Handling

```csharp
private Order entryOrder = null;

protected override void OnBarUpdate()
{
    if (entryOrder == null && Close[0] > Open[0])
        entryOrder = EnterLongLimit("myEntryOrder", Low[0]);
}

protected override void OnOrderUpdate(Order order, double limitPrice, double stopPrice,
    int quantity, int filled, double averageFillPrice, OrderState orderState,
    DateTime time, ErrorCode error, string nativeError)
{
    if (entryOrder != null && entryOrder.IsBacktestOrder && State == State.Realtime)
        entryOrder = GetRealtimeOrder(entryOrder);

    if (entryOrder != null && entryOrder == order)
    {
        if (order.OrderState == OrderState.Cancelled && order.Filled == 0)
            entryOrder = null;

        if (order.OrderState == OrderState.Filled)
            entryOrder = null;
    }
}
```

---

## 📊 Multi-Instrument Strategies

You can submit orders to **different instruments** using `BarsInProgress`.

### Example Setup:
- Primary series: `MSFT` → index `0`
- Secondary series: `AAPL` → index `1`

---

### 🧩 Order Method Overload

```csharp
EnterLongLimit(int barsInProgressIndex, bool isLiveUntilCancelled, int quantity, double limitPrice, string signalName)
```

---

## 💻 Example: Cross-Instrument Order

```csharp
private Order entryOrder = null;

protected override void OnStateChange()
{
    if (State == State.Configure)
    {
        AddDataSeries("AAPL", BarsPeriodType.Minute, 1);
    }
}

protected override void OnBarUpdate()
{
    if (BarsInProgress == 0)
    {
        if (entryOrder == null)
            EnterLongLimit(1, true, 1, Lows[1][0], "AAPL Order");
    }
}

protected override void OnOrderUpdate(Order order, double limitPrice, double stopPrice,
    int quantity, int filled, double averageFillPrice, OrderState orderState,
    DateTime time, ErrorCode error, string nativeError)
{
    if (order.Name == "AAPL Order" && orderState != OrderState.Filled)
        entryOrder = order;
}
```

---

## ✅ Best Practices

- Always assign `Order` objects inside `OnOrderUpdate()`
- Never rely on `OrderId` as a unique identifier
- Null out references after completion
- Handle historical → live transitions explicitly
- Store order references for all active orders

---

## 🧭 Summary

Advanced order handling provides:
- Precise execution control  
- Full lifecycle management  
- Multi-instrument flexibility  

…but requires disciplined state tracking and event-driven design.
