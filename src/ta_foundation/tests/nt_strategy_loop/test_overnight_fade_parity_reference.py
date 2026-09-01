from __future__ import annotations

from pathlib import Path


SOURCE = (
    Path(__file__).resolve().parents[2]
    / "strategies"
    / "OvernightFadeParityReference"
    / "OvernightFadeParityReference.cs"
)


def _source() -> str:
    return SOURCE.read_text(encoding="utf-8")


def test_reference_is_structurally_no_orders() -> None:
    source = _source()
    forbidden_calls = (
        "EnterLong(",
        "EnterShort(",
        "ExitLong(",
        "ExitShort(",
        "SetStopLoss(",
        "SetProfitTarget(",
        "SubmitOrderUnmanaged(",
        "Account.",
    )
    for call in forbidden_calls:
        assert call not in source
    assert "IsExitOnSessionCloseStrategy = false;" in source


def test_reference_freezes_source_measurement_units() -> None:
    source = _source()
    assert "private const int MinimumOvernightOneMinuteBars = 600;" in source
    assert "private const int MaximumOutcomeOneMinuteRows = 240;" in source
    assert "private const double LegAtrMultiple = 2.0;" in source
    assert "AddDataSeries(BarsPeriodType.Minute, 1);" in source
    assert "BarsPeriod.Value != 2" in source
    assert "((AtrPeriod - 1.0) * atrWilder + trueRange) / AtrPeriod" in source
    assert "WriteTwoMinuteBar(decisionTime, trueRange, priorAtr);" in source
    assert 'prefix + ".bars2m.csv"' in source
    assert (
        '"decision_dt,signal_dt_label,session_date,open,high,low,close,volume,'
        'true_range_price,prior_atr_price,prior_atr_ticks"'
    ) in source


def test_reference_preserves_next_one_minute_open_and_independent_events() -> None:
    source = _source()
    assert "private readonly List<PendingEvent> pendingEvents" in source
    assert "private readonly List<VirtualEvent> activeEvents" in source
    assert "oneMinuteBarTime <= pending.DecisionTime" in source
    assert "double entry = Opens[1][0];" in source
    assert 'WriteOutcome(item, barTime, double.NaN, "tied_bar_requires_ticks")' in source


def test_reference_binds_source_manifest_and_emits_joinable_ledgers() -> None:
    source = _source()
    assert "ExpectedSourceManifestSha256" in source
    assert "ExpectedSegmentBindingSha256" in source
    assert "BuildExpectedSegmentBinding(actualHash)" in source
    assert "Instrument.FullName, expectedInstrument" in source
    assert "Core.Globals.GeneralOptions.TimeZoneInfo.Id" in source
    assert "Bars.TradingHours.Name" in source
    assert "StableEventId(currentSession, breakSide, signalLabel)" in source
    assert (
        '"event_id,instrument,contract,session_date,break_side,signal_dt_label,'
        'decision_dt,on_high,on_low,overnight_raw_1m_bars,source_atr_price,'
        'source_atr_ticks"'
    ) in source


def test_reference_fails_closed_outside_historical_analyzer_runs() -> None:
    source = _source()
    assert "else if (State == State.Realtime)" in source
    assert "historical-only and refuses real-time execution" in source
    assert (
        '"event_id,entry_actual_dt,exit_bar_dt,entry_price,leg_ticks_exact,'
        'target_price_exact,stop_price_exact,exit_reason,exit_price,gross_ticks,'
        'bars_scanned"'
    ) in source

