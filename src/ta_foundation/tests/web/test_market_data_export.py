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
    monkeypatch.setattr(mde, "_poll_to_terminal", lambda *a, **k: "finished")

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
