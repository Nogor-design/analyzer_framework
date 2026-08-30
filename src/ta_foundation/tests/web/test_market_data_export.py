"""Unit tests for ``market_data_export`` — template + command construction.

All tests use ``tmp_path`` for command/status/output so the real
``C:\\temp`` bridge and real ``D:\\MarketData`` are never touched. The
autouse ``isolate_nt_bridge`` fixture additionally redirects the module
default bridge files, but every test here passes explicit temp paths.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from ta_foundation.web import market_data_export as mde
from ta_foundation.web.market_data_export import (
    GatherResult,
    MarketDataExportError,
    build_command_payload,
    build_export_template,
    export_filenames,
    gather_market_data,
)


def _tag(text: str, tag: str) -> str | None:
    m = re.search(r"<" + re.escape(tag) + r"(?:\s+[^>]*)?>(.*?)</" + re.escape(tag) + r">", text, re.DOTALL)
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# Filenames
# ---------------------------------------------------------------------------

def test_export_filenames_match_strategy_convention():
    bars, ticks = export_filenames("NQ 06-26", "Export", export_ticks=True)
    assert bars == "NQ 06-26.Export.txt"
    assert ticks == "NQ 06-26 Tick.Export.txt"


def test_export_filenames_no_ticks():
    bars, ticks = export_filenames("ES 06-26", "Export", export_ticks=False)
    assert bars == "ES 06-26.Export.txt"
    assert ticks is None


# ---------------------------------------------------------------------------
# Template generation
# ---------------------------------------------------------------------------

def test_build_export_template_pins_all_params(tmp_path: Path):
    target = tmp_path / "gen" / "export.xml"
    out_dir = tmp_path / "marketdata"
    path = build_export_template(
        instrument="NQ 06-26",
        from_date="2026-05-01",
        to_date="2026-06-01",
        output_dir=out_dir,
        suffix="Export",
        export_ticks=True,
        output_path=target,
    )
    assert path == target
    text = path.read_text(encoding="utf-8")

    # From / To pinned to the requested window.
    assert _tag(text, "From") == "2026-05-01T00:00:00"
    assert _tag(text, "To") == "2026-06-01T00:00:00"

    # Strategy params pinned.
    assert _tag(text, "FilenameSuffix") == "Export"
    assert _tag(text, "ExportTicks") == "true"
    assert _tag(text, "OverwriteIfExists") == "true"
    assert _tag(text, "OutputDirectory") == str(out_dir)

    # Instrument.
    assert _tag(text, "InstrumentOrInstrumentList") == "NQ 06-26"

    # Fill resolution pinned to Standard: the export places no orders, so NT must
    # NOT load a secondary tick series for High-resolution fills (that errors with
    # "Insufficient data available for secondary series" on tick-less windows).
    assert _tag(text, "OrderFillResolution") == "Standard"

    # Fixed BACKTEST, not an optimization — no swept params.
    assert _tag(text, "Category") == "Backtest"
    assert "<OptimizationParameters>" not in text
    assert "<OptimizerType>" not in text

    # The intermediate seed file is cleaned up.
    assert not (target.with_name(target.stem + "__seed.xml")).exists()


def test_build_export_template_export_ticks_false(tmp_path: Path):
    target = tmp_path / "export.xml"
    path = build_export_template(
        instrument="ES 06-26",
        from_date="2026-05-01",
        to_date="2026-05-10",
        output_dir=r"D:\MarketData",
        suffix="Export",
        export_ticks=False,
        output_path=target,
    )
    text = path.read_text(encoding="utf-8")
    assert _tag(text, "ExportTicks") == "false"
    # Backslash path survived intact (the shared replacer is not backslash-safe;
    # we patch OutputDirectory separately to handle Windows paths).
    assert _tag(text, "OutputDirectory") == r"D:\MarketData"


def test_build_export_template_accepts_full_datetime(tmp_path: Path):
    target = tmp_path / "export.xml"
    build_export_template(
        instrument="NQ 06-26",
        from_date="2026-05-01T00:00:00",
        to_date="2026-06-01T12:30:00",
        output_dir=tmp_path,
        suffix="Export",
        export_ticks=True,
        output_path=target,
    )
    text = target.read_text(encoding="utf-8")
    assert _tag(text, "From") == "2026-05-01T00:00:00"
    assert _tag(text, "To") == "2026-06-01T00:00:00"


def test_build_export_template_rejects_bad_date(tmp_path: Path):
    with pytest.raises(MarketDataExportError):
        build_export_template(
            instrument="NQ 06-26",
            from_date="not-a-date",
            to_date="2026-06-01",
            output_dir=tmp_path,
            suffix="Export",
            export_ticks=True,
            output_path=tmp_path / "x.xml",
        )


def test_build_export_template_rejects_blank_instrument(tmp_path: Path):
    with pytest.raises(MarketDataExportError):
        build_export_template(
            instrument="   ",
            from_date="2026-05-01",
            to_date="2026-06-01",
            output_dir=tmp_path,
            suffix="Export",
            export_ticks=True,
            output_path=tmp_path / "x.xml",
        )


# ---------------------------------------------------------------------------
# Command payload
# ---------------------------------------------------------------------------

def test_build_command_payload_shape(tmp_path: Path):
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    src.mkdir()
    dest.mkdir()
    payload = build_command_payload(
        run_id="mdexport_test",
        source_folder=src,
        dest_folder=dest,
        instrument="NQ 06-26",
        timeout_seconds=3600,
    )
    assert payload["action"] == "RunBatch"
    assert payload["runId"] == "mdexport_test"
    assert payload["closeTempTabs"] is True
    assert payload["instrument"] == "NQ 06-26"
    assert payload["timeoutSeconds"] == 3600
    assert Path(payload["sourceFolder"]) == src.resolve()
    assert Path(payload["destFolder"]) == dest.resolve()


def test_build_command_payload_omits_optional(tmp_path: Path):
    payload = build_command_payload(
        run_id="r1",
        source_folder=tmp_path,
        dest_folder=tmp_path,
        instrument="",
    )
    assert "instrument" not in payload
    assert "timeoutSeconds" not in payload
    assert payload["closeTempTabs"] is True


# ---------------------------------------------------------------------------
# gather_market_data — dry run (no dispatch) and the dispatched command file
# ---------------------------------------------------------------------------

def test_gather_dry_run_builds_template_no_command(tmp_path: Path):
    cmd = tmp_path / "nt8_command.json"
    status = tmp_path / "nt8_status.json"
    result = gather_market_data(
        instrument="NQ 06-26",
        from_date="2026-05-01",
        to_date="2026-06-01",
        export_ticks=True,
        output_dir=tmp_path / "md",
        command_file=cmd,
        status_file=status,
        work_dir=tmp_path / "work",
        dispatch=False,
        logger=lambda *_: None,
    )
    assert isinstance(result, GatherResult)
    assert result.state == "prepared"
    assert Path(result.template_path).exists()
    # No command written on a dry run.
    assert not cmd.exists()
    # Expected output paths computed correctly.
    assert result.bars_path.endswith("NQ 06-26.Export.txt")
    assert result.tick_path.endswith("NQ 06-26 Tick.Export.txt")


def test_gather_dispatch_writes_correct_command(tmp_path: Path, monkeypatch):
    cmd = tmp_path / "nt8_command.json"
    status = tmp_path / "nt8_status.json"

    # Stop the poll from blocking: make it return immediately as finished.
    monkeypatch.setattr(mde, "_poll_to_terminal", lambda *a, **k: ("finished", 1, 1, None))

    result = gather_market_data(
        instrument="NQ 06-26",
        from_date="2026-05-01",
        to_date="2026-06-01",
        export_ticks=True,
        output_dir=tmp_path / "md",
        command_file=cmd,
        status_file=status,
        work_dir=tmp_path / "work",
        timeout_seconds=10,
        logger=lambda *_: None,
    )

    # Command file written with the right payload.
    assert cmd.exists()
    payload = json.loads(cmd.read_text(encoding="utf-8"))
    assert payload["action"] == "RunBatch"
    assert payload["closeTempTabs"] is True
    assert payload["instrument"] == "NQ 06-26"
    assert payload["runId"] == result.run_id
    # sourceFolder is where the template was generated.
    assert Path(result.template_path).parent.resolve() == Path(payload["sourceFolder"]).resolve()

    # Outputs absent (no real NT), so the result reports the shortfall.
    assert result.state == "finished"
    assert result.error is not None  # files did not appear
    bars = [f for f in result.files if f.kind == "bars"][0]
    assert bars.exists is False


def test_gather_respects_busy_bridge(tmp_path: Path):
    cmd = tmp_path / "nt8_command.json"
    status = tmp_path / "nt8_status.json"

    # Simulate a live run already owning the bridge.
    cmd.write_text(json.dumps({"action": "RunBatch", "runId": "other_run"}), encoding="utf-8")

    from ta_foundation.web.optimizer_runner import BridgeBusyError

    with pytest.raises(BridgeBusyError):
        gather_market_data(
            instrument="NQ 06-26",
            from_date="2026-05-01",
            to_date="2026-06-01",
            output_dir=tmp_path / "md",
            command_file=cmd,
            status_file=status,
            work_dir=tmp_path / "work",
            logger=lambda *_: None,
        )


# --- the failure that reported success (2026-08-02) -------------------------
#
# NinjaTrader refused a gather with `state="finished"`, `completed=0/1` and the
# reason only in `lastError`. The old code read the state, saw a pre-existing
# non-empty export, and reported "refreshed OK" over data nine days stale.


def _stub_existing_export(tmp_path: Path) -> Path:
    """A market-data folder whose export already exists and is non-empty."""
    md = tmp_path / "md"
    md.mkdir(parents=True, exist_ok=True)
    bars = md / "NQ 06-26.Export.txt"
    bars.write_text("20260601 090000;1;2;0;1;5\n", encoding="utf-8")
    return md


def _gather(tmp_path: Path, md: Path, **overrides):
    """Drive a gather with the real code path but no real waiting.

    The cold-start handshake sleeps between re-dispatches; tests inject a no-op
    sleeper so the retry logic is still exercised without the wall clock.
    """
    kwargs = dict(
        instrument="NQ 06-26",
        from_date="2026-05-01",
        to_date="2026-06-01",
        export_ticks=False,
        output_dir=md,
        command_file=tmp_path / "nt8_command.json",
        status_file=tmp_path / "nt8_status.json",
        work_dir=tmp_path / "work",
        timeout_seconds=10,
        logger=lambda *_: None,
        sleeper=lambda _seconds: None,
        process_check=lambda: True,
    )
    kwargs.update(overrides)
    return gather_market_data(**kwargs)


def test_nt_run_error_is_surfaced_not_swallowed(tmp_path: Path, monkeypatch):
    md = _stub_existing_export(tmp_path)
    monkeypatch.setattr(
        mde,
        "_poll_to_terminal",
        lambda *a, **k: ("finished", 0, 1, "RunError: Strategy Analyzer Run command was not executable after 12 attempts."),
    )
    result = _gather(tmp_path, md)
    assert result.error is not None, "a refused run must never report success"
    assert "RunError" in result.error


def test_incomplete_run_is_a_failure(tmp_path: Path, monkeypatch):
    md = _stub_existing_export(tmp_path)
    monkeypatch.setattr(mde, "_poll_to_terminal", lambda *a, **k: ("finished", 0, 1, None))
    result = _gather(tmp_path, md)
    assert result.error is not None
    assert "0/1" in result.error


def test_untouched_output_is_a_failure(tmp_path: Path, monkeypatch):
    """The core bug: NT wrote nothing, the stale file was still there, and
    `exists and size > 0` passed."""
    md = _stub_existing_export(tmp_path)
    monkeypatch.setattr(mde, "_poll_to_terminal", lambda *a, **k: ("finished", 1, 1, None))
    result = _gather(tmp_path, md)
    assert result.error is not None
    assert "wrote nothing" in result.error
    bars = [f for f in result.files if f.kind == "bars"][0]
    assert bars.exists is True and bars.size_bytes > 0, "the trap: it looks fine"
    assert bars.changed is False


def test_a_real_write_is_reported_as_success(tmp_path: Path, monkeypatch):
    md = _stub_existing_export(tmp_path)
    bars = md / "NQ 06-26.Export.txt"

    def _write_then_finish(*a, **k):
        bars.write_text("20260601 090000;1;2;0;1;5\n20260602 090000;2;3;1;2;6\n", encoding="utf-8")
        return ("finished", 1, 1, None)

    monkeypatch.setattr(mde, "_poll_to_terminal", _write_then_finish)
    result = _gather(tmp_path, md)
    assert result.error is None
    assert [f for f in result.files if f.kind == "bars"][0].changed is True


# ---------------------------------------------------------------------------
# Cold-start readiness handshake
# ---------------------------------------------------------------------------

_REFUSAL = "RunError: Strategy Analyzer Run command was not executable after 12 attempts."


def _scripted_poll(monkeypatch, outcomes):
    """Make _poll_to_terminal return each outcome in turn, recording run_ids."""
    seen: list[str] = []

    def fake(_status_path, *, run_id, **_kwargs):
        seen.append(run_id)
        return outcomes[min(len(seen) - 1, len(outcomes) - 1)]

    monkeypatch.setattr(mde, "_poll_to_terminal", fake)
    return seen


def test_readiness_refusal_is_recognised():
    assert mde.is_readiness_refusal(_REFUSAL)
    assert mde.is_readiness_refusal("run command was not executable")


def test_other_errors_are_not_readiness_refusals():
    assert not mde.is_readiness_refusal(None)
    assert not mde.is_readiness_refusal("")
    assert not mde.is_readiness_refusal("SetupError: instrument not found")
    assert not mde.is_readiness_refusal("export produced no output")


def test_cold_analyzer_is_retried_until_it_runs(tmp_path: Path, monkeypatch):
    """The whole point: a cold refusal must not end an unattended backfill."""
    md = _stub_existing_export(tmp_path)
    _scripted_poll(monkeypatch, [
        ("finished", 0, 1, _REFUSAL),
        ("finished", 1, 1, None),
    ])
    # The stub export must actually change for the run to count as a success.
    monkeypatch.setattr(
        mde, "_verify_outputs",
        lambda *a, **k: [mde.ExportFileResult(
            kind="bars", path=str(md / "NQ 06-26.Export.txt"), exists=True,
            line_count=1, size_bytes=42, changed=True,
        )],
    )
    result = _gather(tmp_path, md)
    assert result.error is None, result.error
    assert result.dispatch_attempts == 2


def test_retry_uses_a_fresh_run_id(tmp_path: Path, monkeypatch):
    """The AddOn de-duplicates by runId; a reused id is silently dropped."""
    md = _stub_existing_export(tmp_path)
    seen = _scripted_poll(monkeypatch, [("finished", 0, 1, _REFUSAL)])
    result = _gather(tmp_path, md)
    assert len(seen) == len(set(seen)), f"duplicate run ids would be ignored: {seen}"
    assert seen == result.dispatched_run_ids


def test_retries_leave_exactly_one_template(tmp_path: Path, monkeypatch):
    """sourceFolder must hold one XML, or the batch reruns every attempt."""
    md = _stub_existing_export(tmp_path)
    _scripted_poll(monkeypatch, [("finished", 0, 1, _REFUSAL)])
    _gather(tmp_path, md)
    templates = list((tmp_path / "work" / "generated_templates").glob("*.xml"))
    assert len(templates) == 1, templates


def test_a_real_failure_is_not_retried(tmp_path: Path, monkeypatch):
    """Retrying a broken template would just waste the operator's time."""
    md = _stub_existing_export(tmp_path)
    _scripted_poll(monkeypatch, [("finished", 0, 1, "SetupError: instrument not found")])
    result = _gather(tmp_path, md)
    assert result.dispatch_attempts == 1
    assert "SetupError" in result.error


def test_exhausted_handshake_reports_actionably(tmp_path: Path, monkeypatch):
    md = _stub_existing_export(tmp_path)
    _scripted_poll(monkeypatch, [("finished", 0, 1, _REFUSAL)])
    result = _gather(tmp_path, md, readiness_backoff_seconds=(1, 2))
    assert result.dispatch_attempts == 3
    assert "still not ready" in result.error
    assert "no data was pulled" in result.error


def test_backoff_waits_are_applied_in_order(tmp_path: Path, monkeypatch):
    md = _stub_existing_export(tmp_path)
    _scripted_poll(monkeypatch, [("finished", 0, 1, _REFUSAL)])
    waits: list[int] = []
    _gather(
        tmp_path, md,
        readiness_backoff_seconds=(5, 10, 20),
        sleeper=waits.append,
    )
    assert waits == [5, 10, 20]


def test_success_first_time_never_waits(tmp_path: Path, monkeypatch):
    md = _stub_existing_export(tmp_path)
    _scripted_poll(monkeypatch, [("finished", 1, 1, None)])
    monkeypatch.setattr(
        mde, "_verify_outputs",
        lambda *a, **k: [mde.ExportFileResult(
            kind="bars", path=str(md / "NQ 06-26.Export.txt"), exists=True,
            line_count=1, size_bytes=42, changed=True,
        )],
    )
    waits: list[int] = []
    result = _gather(tmp_path, md, sleeper=waits.append)
    assert result.dispatch_attempts == 1
    assert waits == []


def test_absent_ninjatrader_fails_fast_without_dispatching(tmp_path: Path, monkeypatch):
    """Otherwise an unattended refresh polls an absent AddOn for six hours."""
    md = _stub_existing_export(tmp_path)
    monkeypatch.setattr(mde, "_poll_to_terminal", lambda *a, **k: pytest.fail(
        "must not poll when NinjaTrader is absent"))
    cmd = tmp_path / "nt8_command.json"
    with pytest.raises(mde.MarketDataExportError) as exc:
        _gather(tmp_path, md, process_check=lambda: False)
    assert "not running" in str(exc.value)
    assert "ensure-nt-ready" in str(exc.value)
    assert not cmd.exists(), "no command may be written when NT is absent"


def test_unknown_process_state_still_proceeds(tmp_path: Path, monkeypatch):
    """An unanswerable check must never be read as 'NinjaTrader is absent'."""
    md = _stub_existing_export(tmp_path)
    _scripted_poll(monkeypatch, [("finished", 0, 1, "SetupError: nope")])
    result = _gather(tmp_path, md, process_check=lambda: None)
    assert result.dispatch_attempts == 1
    assert "SetupError" in result.error


def test_process_check_can_be_disabled(tmp_path: Path, monkeypatch):
    md = _stub_existing_export(tmp_path)
    _scripted_poll(monkeypatch, [("finished", 0, 1, "SetupError: nope")])
    result = _gather(tmp_path, md, process_check=None)
    assert "SetupError" in result.error


def test_live_process_check_answers_without_raising():
    """The real check must return a bool or None, never blow up a refresh."""
    assert mde.ninjatrader_is_running() in (True, False, None)
