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
import subprocess
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
    make_run_id,
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

# A cold Strategy Analyzer refuses automated runs until its Run command becomes
# executable. The AddOn already polls for runnability, but its budget (~7s) was
# sized for a mid-batch tab-activation race, not for a freshly started
# NinjaTrader -- which the 2026-08-30 audit showed can stay un-runnable for far
# longer and previously needed a manual RUN BATCH BACKTEST click to get going.
#
# The refusal is benign and idempotent: the AddOn dispatched nothing, so
# re-issuing the same batch is safe. Retrying *only* this specific error keeps a
# genuine failure (bad template, missing data, wrong instrument) loud.
READINESS_REFUSAL_MARKER = "run command was not executable"

#: How long to wait for the AddOn's first heartbeat before concluding nothing is
#: listening. Generous enough for a busy NinjaTrader to get around to the command
#: file, short enough that an unattended job fails in minutes, not hours.
ACK_TIMEOUT_SECONDS = 120

#: Waits before each re-dispatch after a readiness refusal. Cumulative ~2.5 min,
#: matching the documented 1-2 minute NinjaTrader cold-start window.
READINESS_RETRY_BACKOFF_SECONDS: tuple[int, ...] = (20, 45, 90)


def ninjatrader_is_running() -> bool | None:
    """Whether a NinjaTrader process exists.

    Returns ``None`` when the question cannot be answered (non-Windows, or the
    process query failed). Callers must treat ``None`` as "proceed", never as
    "absent": guessing absent would abort a perfectly good refresh.
    """

    if os.name != "nt":
        return None
    try:
        completed = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq NinjaTrader.exe", "/NH"],
            capture_output=True, text=True, timeout=20, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return "NinjaTrader.exe" in completed.stdout


def is_readiness_refusal(error: str | None) -> bool:
    """True when NinjaTrader refused the run because it was not ready yet.

    Distinguishes "ask again in a moment" from "this batch is broken". Only the
    former may be retried automatically.
    """

    return bool(error) and READINESS_REFUSAL_MARKER in error.lower()


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
    #: Size before the run. A re-dump of an existing contract overwrites a file
    #: that was already there, so "the file exists and is non-empty" says
    #: nothing about whether NinjaTrader actually wrote anything this time.
    size_before: int = 0
    #: Whether this run changed the file at all. The only honest success signal
    #: when the output path is a pre-existing export.
    changed: bool = False

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
    #: How many RunBatch dispatches this gather needed. >1 means NinjaTrader
    #: refused an earlier attempt as not-yet-ready and the handshake waited.
    dispatch_attempts: int = 1
    #: run_ids actually sent, in order. The AddOn de-duplicates by runId, so a
    #: retry that reused the first id would be silently ignored.
    dispatched_run_ids: list[str] = field(default_factory=list)

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
    readiness_backoff_seconds: tuple[int, ...] | None = None,
    sleeper=time.sleep,
    process_check=ninjatrader_is_running,
    ack_timeout_seconds: int = ACK_TIMEOUT_SECONDS,
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

    run_id = make_run_id("mdexport", moment)
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
    _enforce_single_template(source_folder, template_path, logger)

    if not dispatch:
        result.state = "prepared"
        return result

    # 2. Fail fast if NinjaTrader is not even running. Without this an
    #    unattended refresh writes the command file and then polls an absent
    #    AddOn for the full timeout -- six hours per contract by default, which
    #    turns an overnight backfill into an overnight hang. A definite "no
    #    process" is worth far more than a slow timeout; an unknown answer is
    #    never treated as absent.
    if process_check is not None and process_check() is False:
        result.state = "blocked"
        result.error = (
            "NinjaTrader is not running, so the export bridge has no listener. "
            "Start it first (see the nt-ensure-ready playbook: "
            "`python -m ta_foundation.nt_strategy_loop.cli ensure-nt-ready`). "
            "No command was dispatched and no data was pulled."
        )
        raise MarketDataExportError(result.error)

    # 3. Guard + write the command.
    try:
        ensure_bridge_available(cmd_path, status_path, now=moment)
    except BridgeBusyError as exc:
        result.state = "blocked"
        result.error = str(exc)
        raise

    # Record pre-run size *and* mtime so we can confirm NT actually rewrote the
    # files. Size alone is not enough: re-dumping an identical window produces
    # an identical byte count, and the output path is usually a pre-existing
    # export rather than a fresh file.
    pre_state = {
        "bars": _file_state(bars_path),
        "ticks": _file_state(tick_path) if tick_path else (0, 0),
    }

    # Clear stale heartbeat so the first live one for this run isn't erased.
    try:
        if status_path.exists():
            status_path.unlink()
    except OSError:
        pass

    # 4. Dispatch and poll, re-issuing only while NinjaTrader says it is not
    #    ready yet. This is the cold-start handshake: without it, an unattended
    #    backfill dies on the first refusal and needs a human to click RUN BATCH
    #    BACKTEST once to warm the Analyzer.
    backoff = (
        READINESS_RETRY_BACKOFF_SECONDS
        if readiness_backoff_seconds is None
        else tuple(readiness_backoff_seconds)
    )

    final_state = "unknown"
    completed = total = 0
    last_error: str | None = None

    for attempt in range(len(backoff) + 1):
        # The AddOn de-duplicates by runId, so every attempt takes a fresh one
        # from the shared generator (collision-proof within this process). The
        # template file is *not* regenerated: sourceFolder must hold exactly one
        # XML, which _enforce_single_template guarantees above.
        attempt_run_id = run_id if attempt == 0 else make_run_id("mdexport", moment)
        result.dispatched_run_ids.append(attempt_run_id)
        result.dispatch_attempts = attempt + 1

        if attempt:
            # Re-check the shared bridge: something else may have claimed it
            # while we were waiting out the cold start. Our own refused attempt
            # still owns the command file, so identify as that previous run --
            # otherwise we would treat ourselves as a foreign owner and abort.
            try:
                ensure_bridge_available(
                    cmd_path,
                    status_path,
                    now=datetime.now(timezone.utc),
                    requesting_run_id=result.dispatched_run_ids,
                )
            except BridgeBusyError as exc:
                result.state = "blocked"
                result.error = str(exc)
                raise
            try:
                if status_path.exists():
                    status_path.unlink()
            except OSError:
                pass

        payload = build_command_payload(
            run_id=attempt_run_id,
            source_folder=source_folder,
            dest_folder=dest_folder,
            instrument=instrument,
            timeout_seconds=timeout_seconds,
        )
        _write_command_file(cmd_path, payload)
        result.state = "dispatched"
        result.run_id = attempt_run_id
        logger(f"[market_data_export] dispatched RunBatch {attempt_run_id} -> {cmd_path}")

        final_state, completed, total, last_error = _poll_to_terminal(
            status_path,
            run_id=attempt_run_id,
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
            logger=logger,
            ack_timeout_seconds=ack_timeout_seconds,
        )

        if not is_readiness_refusal(last_error):
            break
        if attempt >= len(backoff):
            logger(
                "[market_data_export] NinjaTrader still not ready after "
                f"{attempt + 1} attempt(s); giving up"
            )
            break

        wait = backoff[attempt]
        logger(
            f"[market_data_export] NinjaTrader not ready yet (attempt {attempt + 1}); "
            f"waiting {wait}s before re-dispatching"
        )
        sleeper(wait)

    result.state = final_state

    # NinjaTrader signals a refused run as state="finished", completed=0, with
    # the reason only in lastError (e.g. "RunError: Strategy Analyzer Run
    # command was not executable"). Surfacing it is what stops a failed gather
    # from being reported as a success over an untouched file.
    if final_state == "no_ack" and result.error is None:
        result.error = (
            "NinjaTrader never acknowledged the export command within "
            f"{ack_timeout_seconds}s. The process is running but its batch AddOn "
            "is not servicing the command bridge -- it can be busy running a "
            "strategy, or the AddOn may need re-authorizing. Nothing was "
            "exported. Check the Control Center, then retry; do not assume the "
            "data was refreshed."
        )

    if last_error and result.error is None:
        if is_readiness_refusal(last_error):
            # Exhausted the handshake. Say so plainly, so the operator knows the
            # backfill did not silently skip -- NinjaTrader never started.
            result.error = (
                f"{last_error} NinjaTrader was still not ready after "
                f"{result.dispatch_attempts} dispatch attempt(s). Confirm it is "
                "logged in and the Control Center is up (see the nt-ensure-ready "
                "playbook); no data was pulled."
            )
        else:
            result.error = last_error

    # 5. Verify outputs.
    result.files = _verify_outputs(bars_path, tick_path, pre_state)
    present = all(f.exists and f.size_bytes > 0 for f in result.files)
    wrote = any(f.changed for f in result.files)
    incomplete = bool(total) and completed < total

    if result.error is None and (final_state == "timeout" or not present):
        missing = [f.path for f in result.files if not (f.exists and f.size_bytes > 0)]
        result.error = (
            f"export did not produce expected output (state={final_state}); "
            f"missing/empty: {missing}"
        )
    if result.error is None and incomplete:
        result.error = (
            f"export reported {completed}/{total} template(s) complete "
            f"(state={final_state})"
        )
    if result.error is None and not wrote:
        # Every file already existed and none of them moved. Nothing was pulled.
        result.error = (
            f"export left every output byte-identical (state={final_state}); "
            "NinjaTrader wrote nothing"
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
    ack_timeout_seconds: int = ACK_TIMEOUT_SECONDS,
) -> tuple[str, int, int, str | None]:
    """Poll the AddOn heartbeat file until the run reaches a terminal state.

    Mirrors ``scripts/drive_final_backtest_to_complete.wait_for_nt``: only
    heartbeats whose ``runId`` matches ours count; ``completed >= total`` with a
    non-in-flight state is treated as finished.

    Returns ``(state, completed, total, last_error)``. The last three are not
    decoration: NinjaTrader reports a failed run as ``state="finished"`` with
    ``completed=0`` and the reason in ``lastError`` -- so a caller that reads
    only the state cannot tell a successful export from a refused one.

    A dispatch that is never acknowledged returns ``"no_ack"`` rather than
    burning the whole timeout. NinjaTrader can be alive and responsive while its
    AddOn is not servicing the command file at all -- observed 2026-08-31 with
    NinjaTrader busy running a strategy, and evidenced before that by the
    ``nt8_command.stale-*`` recovery files. Waiting six hours for a listener
    that never answers is the difference between an unattended job that reports
    a problem and one that silently occupies the bridge all night.
    """
    started = time.time()
    deadline = started + timeout_seconds
    acknowledged = False
    last_completed = -1
    completed = total = 0
    last_error: str | None = None
    while time.time() < deadline:
        payload = _read_status(status_path)
        if payload is None or str(payload.get("runId") or "") != run_id:
            if not acknowledged and time.time() - started >= ack_timeout_seconds:
                return "no_ack", completed, total, last_error
            time.sleep(poll_interval_seconds)
            continue
        acknowledged = True
        state = str(payload.get("state") or "").strip().lower()
        completed = int(payload.get("completed") or 0)
        total = int(payload.get("total") or 0)
        last_error = str(payload.get("lastError") or "").strip() or None
        if completed != last_completed:
            logger(
                f"[market_data_export] {run_id}: {state} {completed}/{total} "
                f"cur={payload.get('currentTemplate')}"
            )
            last_completed = completed
        if state in _TERMINAL_STATES:
            return state, completed, total, last_error
        if total and completed >= total and state not in {"running", "starting"}:
            return "finished", completed, total, last_error
        time.sleep(poll_interval_seconds)
    return "timeout", completed, total, last_error


def _read_status(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _file_state(path: Path | None) -> tuple[int, int]:
    """``(size_bytes, mtime_ns)``, or ``(0, 0)`` when the file is absent."""
    if path is None:
        return (0, 0)
    try:
        stat = path.stat()
    except OSError:
        return (0, 0)
    return (stat.st_size, stat.st_mtime_ns)


def _verify_outputs(
    bars_path: Path,
    tick_path: Path | None,
    pre_state: dict[str, tuple[int, int]],
) -> list[ExportFileResult]:
    files = [_verify_one("bars", bars_path, pre_state)]
    if tick_path is not None:
        files.append(_verify_one("ticks", tick_path, pre_state))
    return files


def _verify_one(
    kind: str, path: Path, pre_state: dict[str, tuple[int, int]]
) -> ExportFileResult:
    exists = path.exists()
    size, mtime = _file_state(path)
    lines = _count_lines(path) if exists else 0
    before_size, before_mtime = pre_state.get(kind, (0, 0))
    return ExportFileResult(
        kind=kind,
        path=str(path),
        exists=exists,
        line_count=lines,
        size_bytes=size,
        size_before=before_size,
        # Either dimension moving proves NT wrote. mtime catches a re-dump of an
        # identical window; size catches a filesystem with coarse timestamps.
        changed=exists and size > 0 and (size != before_size or mtime != before_mtime),
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


def _enforce_single_template(source_folder: Path, template_path: Path, logger) -> None:
    """Guarantee the AddOn's sourceFolder holds exactly our one template.

    The batch AddOn runs *every* XML in sourceFolder. For a single-contract
    export that makes a leftover template silently in-scope: a stale window or
    the wrong instrument gets re-run and its bars land in the output the caller
    is about to accept as this run's result. That is the one failure here that
    can put wrong data into the market-data store, so it is enforced rather than
    assumed.

    This function owns ``<work_dir>/generated_templates``, so clearing stale
    XML from it is safe; anything non-XML is left alone and reported.
    """

    for stale in sorted(source_folder.glob("*.xml")):
        if stale.resolve() == template_path.resolve():
            continue
        logger(f"[market_data_export] removing stale template: {stale.name}")
        try:
            stale.unlink()
        except OSError as exc:
            raise MarketDataExportError(
                f"cannot remove stale template {stale}: {exc}. The batch would "
                "run it alongside this export and mix its bars into the result."
            ) from exc

    remaining = sorted(source_folder.glob("*.xml"))
    if remaining != [template_path]:
        raise MarketDataExportError(
            f"expected exactly one template in {source_folder}, found "
            f"{[p.name for p in remaining]}. Refusing to dispatch: the batch "
            "runs every template in the folder."
        )


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
