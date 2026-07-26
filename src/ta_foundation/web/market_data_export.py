from __future__ import annotations

"""
Programmatic market-data gathering (bars + ticks) via the NinjaTrader bridge.

This drives ``TaFoundationDataExportStrategy`` (a no-orders Strategy that dumps
every closed bar — and optionally every tick — for an explicit From/To window)
through the *same* optimizer batch AddOn bridge the /optimizer web UI uses, so
the operator never hand-downloads historical data from NinjaTrader.

The capability/automation plan this implements is documented in
``docs/runbooks/market_data_gathering.md`` ("Automation plan").

Reuse map (nothing here is rebuilt):

- Seed generation: ``nt_strategy_loop.seed_template.generate_seed_template_from_source``
  reads the strategy's ``.cs`` and emits a valid Strategy Analyzer template.
- Fixed (1-combo) backtest template: ``optimization.grid_workflow
  .generate_fixed_backtest_template`` strips the optimizer sections, forces
  ``Category=Backtest``, and pins each strategy value + From/To. We post-patch
  ``OutputDirectory`` and the instrument with the backslash-safe
  ``optimizer_template_writer._replace_tag_text`` because a Windows path value
  cannot go through ``grid_workflow``'s regex-replacement substitution.
- Dispatch: the ``RunBatch`` IPC command payload + ``ensure_bridge_available``
  single-writer guard + ``closeTempTabs:true`` mirror ``optimizer_runner
  .start_run``.
- Status poll-to-terminal: mirrors ``scripts/drive_final_backtest_to_complete``.

The template-generation (:func:`build_export_template`) and command-payload
construction (:func:`build_command_payload`) are pure and unit-testable without
NinjaTrader. :func:`gather_market_data` ties them together and performs the live
dispatch + poll + verify.
"""

import json
import os
import tempfile
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ta_foundation.nt_strategy_loop.seed_template import (
    generate_seed_template_from_source,
)
from ta_foundation.optimization.grid_workflow import generate_fixed_backtest_template
from ta_foundation.web.optimizer_runner import (
    DEFAULT_COMMAND_FILE,
    DEFAULT_STATUS_FILE,
    BridgeBusyError,
    ensure_bridge_available,
)
from ta_foundation.web.optimizer_template_writer import (
    _replace_or_insert_strategy_tag,
    _replace_tag_text,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STRATEGY_ID = "TaFoundationDataExportStrategy"
DEFAULT_OUTPUT_DIR = Path(r"D:\MarketData")
DEFAULT_SUFFIX = "Export"

# The canonical .cs lives under the execution-bridge folder, not a per-strategy
# templates dir, so we generate the seed straight from source.
_REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STRATEGY_SOURCE = (
    _REPO_ROOT
    / "strategies"
    / "TaFoundationExecutionBridge"
    / f"{STRATEGY_ID}.cs"
)

# Status poll cadence / ceilings (mirror drive_final_backtest_to_complete).
POLL_INTERVAL_SECONDS = 20
DEFAULT_TIMEOUT_SECONDS = 6 * 60 * 60
_TERMINAL_STATES = frozenset({
    "finished", "completed", "complete", "success", "done",
    "failed", "error", "cancelled", "canceled", "timed_out", "timedout",
})


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class MarketDataExportError(Exception):
    pass


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

@dataclass
class ExportFileResult:
    """Post-run verification of a single output file."""
    kind: str          # "bars" | "ticks"
    path: str
    exists: bool
    line_count: int
    size_bytes: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class GatherResult:
    instrument: str
    from_date: str
    to_date: str
    export_ticks: bool
    suffix: str
    output_dir: str
    state: str
    run_id: str
    template_path: str
    command_file: str
    status_file: str
    bars_path: str
    tick_path: str | None
    files: list[ExportFileResult] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["files"] = [f.to_dict() for f in self.files]
        return data


# ---------------------------------------------------------------------------
# Pure helpers (unit-testable without NinjaTrader)
# ---------------------------------------------------------------------------

def export_filenames(instrument: str, suffix: str, *, export_ticks: bool) -> tuple[str, str | None]:
    """Return ``(bars_filename, tick_filename_or_None)`` matching the C#
    strategy's ``<instrument>.<suffix>.txt`` / ``<instrument> Tick.<suffix>.txt``
    convention exactly."""
    inst = str(instrument).strip()
    suf = (str(suffix).strip() or DEFAULT_SUFFIX)
    bars = f"{inst}.{suf}.txt"
    ticks = f"{inst} Tick.{suf}.txt" if export_ticks else None
    return bars, ticks


def build_export_template(
    *,
    instrument: str,
    from_date: str,
    to_date: str,
    output_dir: str | Path,
    suffix: str,
    export_ticks: bool,
    output_path: str | Path,
    overwrite_if_exists: bool = True,
    strategy_source: str | Path | None = None,
) -> Path:
    """Generate a single, 1-combo FIXED-backtest template for the data-export
    strategy with ``OutputDirectory`` / ``FilenameSuffix`` / ``ExportTicks`` /
    ``OverwriteIfExists`` / ``From`` / ``To`` / instrument all pinned. No swept
    parameters — this is a plain backtest, not an optimization.

    Returns the path to the written template. Performs no NinjaTrader IO.
    """
    source = Path(strategy_source) if strategy_source else DEFAULT_STRATEGY_SOURCE
    if not source.is_file():
        raise MarketDataExportError(f"Strategy source not found: {source}")

    inst = str(instrument).strip()
    if not inst:
        raise MarketDataExportError("instrument must be a non-empty NT contract, e.g. 'NQ 06-26'")
    from_d = _normalize_date(from_date, "from_date")
    to_d = _normalize_date(to_date, "to_date")
    suf = (str(suffix).strip() or DEFAULT_SUFFIX)
    out_dir = str(output_dir)

    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)

    # Step 1: seed XML straight from the strategy .cs (no NT UI download).
    seed_path = target.with_name(target.stem + "__seed.xml")
    generate_seed_template_from_source(
        source_path=source,
        output_path=seed_path,
        strategy_name=STRATEGY_ID,
        instrument=inst,
        from_date=f"{from_d}T00:00:00",
        to_date=f"{to_d}T00:00:00",
    )

    # Step 2: stamp the 1-combo fixed backtest, pinning the value params we can
    # send through grid_workflow (NO backslash values — OutputDirectory is
    # patched separately below because the shared replacer is not
    # backslash-safe in the replacement string).
    generate_fixed_backtest_template(
        seed_path,
        target,
        {
            "FilenameSuffix": suf,
            "ExportTicks": bool(export_ticks),
            "OverwriteIfExists": bool(overwrite_if_exists),
        },
        from_date=from_d,
        to_date=to_d,
        strict_params=True,
    )

    # Step 3: backslash-safe patches for the path + instrument.
    text = target.read_text(encoding="utf-8")
    text = _replace_tag_text(text, "OutputDirectory", out_dir, count=1)
    text = _replace_or_insert_strategy_tag(text, "InstrumentOrInstrumentList", inst)
    # The seed inherits NT's default OrderFillResolution=High (Tick), which forces
    # NT to load a SECONDARY TICK SERIES for fill simulation -> "Insufficient data
    # available for secondary series" on any window NT lacks ticks for. The export
    # strategy places NO orders, so tick-resolution fills are never needed; pin
    # Standard so NT loads only the primary bar series being exported.
    text = _replace_or_insert_strategy_tag(text, "OrderFillResolution", "Standard")
    target.write_text(text, encoding="utf-8")

    # Clean up the intermediate seed; the fixed template is self-contained.
    try:
        seed_path.unlink()
    except OSError:
        pass

    return target


def build_command_payload(
    *,
    run_id: str,
    source_folder: str | Path,
    dest_folder: str | Path,
    instrument: str,
    timeout_seconds: int | None = None,
) -> dict[str, Any]:
    """Construct the ``RunBatch`` IPC payload the NT batch AddOn consumes.

    Mirrors ``optimizer_runner.start_run``'s payload shape: ``closeTempTabs``
    is always true so per-template Strategy Analyzer tabs are closed after the
    run (leaking tabs crashes NT on long sessions)."""
    payload: dict[str, Any] = {
        "action": "RunBatch",
        "runId": run_id,
        "sourceFolder": str(Path(source_folder).resolve()),
        "destFolder": str(Path(dest_folder).resolve()),
        "closeTempTabs": True,
    }
    inst = str(instrument).strip()
    if inst:
        payload["instrument"] = inst
    if timeout_seconds:
        payload["timeoutSeconds"] = int(timeout_seconds)
    return payload


# ---------------------------------------------------------------------------
# Live entry point
# ---------------------------------------------------------------------------

def gather_market_data(
    instrument: str = "NQ 06-26",
    from_date: str = "",
    to_date: str = "",
    *,
    export_ticks: bool = True,
    suffix: str = DEFAULT_SUFFIX,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    overwrite_if_exists: bool = True,
    command_file: str | Path | None = None,
    status_file: str | Path | None = None,
    work_dir: str | Path | None = None,
    strategy_source: str | Path | None = None,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    poll_interval_seconds: int = POLL_INTERVAL_SECONDS,
    dispatch: bool = True,
    now: datetime | None = None,
    logger=print,
) -> GatherResult:
    """Gather bars (and optional ticks) for ``instrument`` over [from_date,
    to_date] by driving ``TaFoundationDataExportStrategy`` through the NT batch
    bridge.

    Steps (all reuse existing machinery):

    1. Generate a 1-combo fixed-backtest template (:func:`build_export_template`).
    2. Guard the shared bridge (:func:`ensure_bridge_available`) and write the
       ``RunBatch`` command (:func:`build_command_payload`) to ``command_file``.
    3. Poll ``status_file`` to a terminal state.
    4. Verify the output files exist and grew; return counts + paths.

    ``command_file`` / ``status_file`` default to the real bridge files; tests
    must pass temp paths. ``dispatch=False`` builds the template + payload and
    returns without writing the command (useful for dry runs).
    """
    moment = now or datetime.now(timezone.utc)
    cmd_path = Path(command_file) if command_file else DEFAULT_COMMAND_FILE
    status_path = Path(status_file) if status_file else DEFAULT_STATUS_FILE
    out_dir = Path(output_dir)

    from_d = _normalize_date(from_date, "from_date")
    to_d = _normalize_date(to_date, "to_date")
    suf = (str(suffix).strip() or DEFAULT_SUFFIX)

    # Working area for the generated template (the AddOn's sourceFolder).
    if work_dir is not None:
        base = Path(work_dir)
    else:
        base = Path(tempfile.mkdtemp(prefix="md_export_"))
    source_folder = base / "generated_templates"
    dest_folder = base / "nt_output"
    source_folder.mkdir(parents=True, exist_ok=True)
    dest_folder.mkdir(parents=True, exist_ok=True)

    run_id = "mdexport_" + moment.strftime("%Y%m%d_%H%M%S")
    template_path = source_folder / f"{run_id}.xml"

    bars_name, tick_name = export_filenames(instrument, suf, export_ticks=export_ticks)
    bars_path = out_dir / bars_name
    tick_path = (out_dir / tick_name) if tick_name else None

    result = GatherResult(
        instrument=str(instrument).strip(),
        from_date=from_d,
        to_date=to_d,
        export_ticks=bool(export_ticks),
        suffix=suf,
        output_dir=str(out_dir),
        state="building",
        run_id=run_id,
        template_path=str(template_path),
        command_file=str(cmd_path),
        status_file=str(status_path),
        bars_path=str(bars_path),
        tick_path=str(tick_path) if tick_path else None,
    )

    # 1. Build the template.
    build_export_template(
        instrument=instrument,
        from_date=from_d,
        to_date=to_d,
        output_dir=out_dir,
        suffix=suf,
        export_ticks=export_ticks,
        output_path=template_path,
        overwrite_if_exists=overwrite_if_exists,
        strategy_source=strategy_source,
    )
    logger(f"[market_data_export] template written: {template_path}")

    if not dispatch:
        result.state = "prepared"
        return result

    # 2. Guard + write the command.
    try:
        ensure_bridge_available(cmd_path, status_path, now=moment)
    except BridgeBusyError as exc:
        result.state = "blocked"
        result.error = str(exc)
        raise

    # Record pre-run sizes so we can confirm the files actually grew.
    pre_sizes = {
        "bars": _safe_size(bars_path),
        "ticks": _safe_size(tick_path) if tick_path else 0,
    }

    # Clear stale heartbeat so the first live one for this run isn't erased.
    try:
        if status_path.exists():
            status_path.unlink()
    except OSError:
        pass

    payload = build_command_payload(
        run_id=run_id,
        source_folder=source_folder,
        dest_folder=dest_folder,
        instrument=instrument,
        timeout_seconds=timeout_seconds,
    )
    _write_command_file(cmd_path, payload)
    result.state = "dispatched"
    logger(f"[market_data_export] dispatched RunBatch {run_id} -> {cmd_path}")

    # 3. Poll to terminal.
    final_state = _poll_to_terminal(
        status_path,
        run_id=run_id,
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
        logger=logger,
    )
    result.state = final_state

    # 4. Verify outputs.
    result.files = _verify_outputs(bars_path, tick_path, pre_sizes)
    grew = all(f.exists and f.size_bytes > 0 for f in result.files)
    if final_state in {"timeout"} or not grew:
        if result.error is None:
            missing = [f.path for f in result.files if not (f.exists and f.size_bytes > 0)]
            result.error = (
                f"export did not produce expected output (state={final_state}); "
                f"missing/empty: {missing}"
            )
    return result


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _poll_to_terminal(
    status_path: Path,
    *,
    run_id: str,
    timeout_seconds: int,
    poll_interval_seconds: int,
    logger,
) -> str:
    """Poll the AddOn heartbeat file until the run reaches a terminal state.

    Mirrors ``scripts/drive_final_backtest_to_complete.wait_for_nt``: only
    heartbeats whose ``runId`` matches ours count; ``completed >= total`` with a
    non-in-flight state is treated as finished. Returns the terminal state, or
    ``"timeout"``."""
    deadline = time.time() + timeout_seconds
    last_completed = -1
    while time.time() < deadline:
        payload = _read_status(status_path)
        if payload is None or str(payload.get("runId") or "") != run_id:
            time.sleep(poll_interval_seconds)
            continue
        state = str(payload.get("state") or "").strip().lower()
        completed = int(payload.get("completed") or 0)
        total = int(payload.get("total") or 0)
        if completed != last_completed:
            logger(
                f"[market_data_export] {run_id}: {state} {completed}/{total} "
                f"cur={payload.get('currentTemplate')}"
            )
            last_completed = completed
        if state in _TERMINAL_STATES:
            return state
        if total and completed >= total and state not in {"running", "starting"}:
            return "finished"
        time.sleep(poll_interval_seconds)
    return "timeout"


def _read_status(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _verify_outputs(
    bars_path: Path,
    tick_path: Path | None,
    pre_sizes: dict[str, int],
) -> list[ExportFileResult]:
    files = [_verify_one("bars", bars_path)]
    if tick_path is not None:
        files.append(_verify_one("ticks", tick_path))
    return files


def _verify_one(kind: str, path: Path) -> ExportFileResult:
    exists = path.exists()
    size = _safe_size(path)
    lines = _count_lines(path) if exists else 0
    return ExportFileResult(
        kind=kind,
        path=str(path),
        exists=exists,
        line_count=lines,
        size_bytes=size,
    )


def _count_lines(path: Path) -> int:
    try:
        count = 0
        with path.open("rb") as handle:
            for _ in handle:
                count += 1
        return count
    except OSError:
        return 0


def _safe_size(path: Path | None) -> int:
    if path is None:
        return 0
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _normalize_date(value: str, label: str) -> str:
    """Accept ``YYYY-MM-DD`` or ``YYYY-MM-DDT...`` and return the ``YYYY-MM-DD``
    date part. ``grid_workflow`` re-appends ``T00:00:00`` for the NT tags."""
    text = str(value).strip()
    if not text:
        raise MarketDataExportError(f"{label} is required (YYYY-MM-DD)")
    date_part = text.split("T", 1)[0]
    try:
        datetime.strptime(date_part, "%Y-%m-%d")
    except ValueError as exc:
        raise MarketDataExportError(f"{label} must be YYYY-MM-DD, got {value!r}") from exc
    return date_part


def _write_command_file(path: Path, payload: dict[str, Any]) -> None:
    """Atomic command-file write (same pattern as optimizer_runner)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".tmp.", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass
        os.replace(tmp_name, path)
        try:
            os.utime(path, None)
        except OSError:
            pass
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
