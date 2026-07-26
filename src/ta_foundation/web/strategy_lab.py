from __future__ import annotations

"""Web helpers for the autonomous NinjaTrader Strategy Lab.

This module is intentionally read-heavy. The autonomous loop already writes a
durable artifact tree under ``.ta_artifacts/nt_strategy_lab/sessions``; the web
page should summarize those artifacts and dispatch existing CLI entry points
rather than inventing a second orchestration model.
"""

import json
import sys
from pathlib import Path
from typing import Any

from ta_foundation.nt_strategy_loop.session import DEFAULT_LAB_ROOT


TERMINAL_DECISIONS = {"candidate", "archive", "incomplete", "halted"}
BUILT_IN_SPEC_FAMILIES = ("sma_cross", "sma_cross_smoke")


def list_strategy_lab_sessions(root: str | Path = DEFAULT_LAB_ROOT) -> list[dict[str, Any]]:
    base = Path(root)
    if not base.exists():
        return []
    summaries = [
        build_strategy_lab_summary(path)
        for path in sorted(base.glob("loop_*"), key=lambda p: _mtime(p), reverse=True)
        if path.is_dir()
    ]
    return summaries


def get_strategy_lab_session(session_id: str, root: str | Path = DEFAULT_LAB_ROOT) -> dict[str, Any] | None:
    if not session_id or "/" in session_id or "\\" in session_id:
        return None
    path = Path(root) / session_id
    if not path.is_dir():
        return None
    return build_strategy_lab_summary(path)


def build_strategy_lab_summary(session_dir: str | Path) -> dict[str, Any]:
    path = Path(session_dir)
    manifest = _read_json(path / "manifest.json") or _read_json(path / "session.json") or {}
    spec = _read_json(path / "strategy_spec.json") or {}
    attempts = _attempt_summaries(path)
    artifacts = manifest.get("artifacts") if isinstance(manifest.get("artifacts"), dict) else {}
    optimizer_analysis = _read_json(path / "optimizer" / "optimizer_analysis.json") or {}
    decision = str(manifest.get("decision") or "").strip()
    strategy_name = str(
        manifest.get("strategy_name")
        or spec.get("strategy_name")
        or path.name.rsplit("_", 1)[-1]
        or ""
    )
    latest_attempt = attempts[-1] if attempts else None
    compile_state = (latest_attempt or {}).get("compile_state") or ""
    compile_clean = _has_compile_clean_source(path)
    optimizer_session_id = str(artifacts.get("optimizer_session_id") or "").strip()
    optimizer_state = _optimizer_state(optimizer_analysis, artifacts)
    stage = _infer_stage(
        decision=decision,
        attempts=attempts,
        compile_clean=compile_clean,
        optimizer_session_id=optimizer_session_id,
        optimizer_analysis=optimizer_analysis,
    )
    created_at = str(manifest.get("created_at") or "")
    updated_at = _latest_iso(path)
    return {
        "session_id": path.name,
        "session_dir": str(path.resolve()),
        "strategy_name": strategy_name,
        "family": str(spec.get("family") or ""),
        "intent": str(spec.get("intent") or ""),
        "compile_mode": str(manifest.get("compile_mode") or ""),
        "decision": decision or ("halted" if stage == "authoring_failed" else ("running" if stage not in {"empty", "done"} else "")),
        "stage": stage,
        "created_at": created_at,
        "updated_at": updated_at,
        "attempt_count": len(attempts),
        "compile_state": compile_state,
        "compile_clean": compile_clean,
        "latest_error": (latest_attempt or {}).get("last_error") or "",
        "latest_error_count": (latest_attempt or {}).get("error_count"),
        "attempts": attempts,
        "optimizer_session_id": optimizer_session_id,
        "optimizer_session_dir": str(artifacts.get("optimizer_session_dir") or ""),
        "optimizer_state": optimizer_state,
        "optimizer_csv": str(artifacts.get("optimizer_csv") or ""),
        "seed_template": str(artifacts.get("seed_template") or ""),
        "optimizer_analysis": _optimizer_summary(optimizer_analysis),
        "summary_markdown": _read_text(path / "decisions" / "STRATEGY_LOOP_SUMMARY.md"),
        "next_action_markdown": _read_text(path / "decisions" / "NEXT_ACTION.md"),
        "artifacts": _artifact_links(path, artifacts),
        "gates": _gate_summaries(
            decision=decision,
            compile_clean=compile_clean,
            optimizer_session_id=optimizer_session_id,
            optimizer_analysis=optimizer_analysis,
            latest_attempt=latest_attempt,
        ),
    }


def build_full_loop_command(payload: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    validation = validate_full_loop_payload(payload)
    if not validation["ok"]:
        return [], validation

    command = [
        sys.executable,
        "-m",
        "ta_foundation.web.strategy_lab_job",
        "full-loop",
        "--spec",
        validation["spec_path"],
        "--compile-mode",
        validation["compile_mode"],
        "--max-repair-attempts",
        str(validation["max_repair_attempts"]),
        "--instrument",
        validation["instrument"],
        "--market-suffix",
        validation["market_suffix"],
        "--keep-best-results",
        str(validation["keep_best_results"]),
        "--max-combinations-per-chunk",
        str(validation["max_combinations_per_chunk"]),
        "--max-drawdown",
        str(validation["max_drawdown"]),
        "--min-trades",
        str(validation["min_trades"]),
        "--min-profit-factor",
        str(validation["min_profit_factor"]),
        "--optimizer-timeout-seconds",
        str(validation["optimizer_timeout_seconds"]),
    ]
    if validation["lab_root"]:
        command.extend(["--lab-root", validation["lab_root"]])
    if validation["overwrite"]:
        command.append("--overwrite")
    if validation["repair_llm"]:
        command.extend([
            "--repair-llm",
            "--repair-llm-model",
            validation["repair_llm_model"],
            "--repair-llm-url",
            validation["repair_llm_url"],
        ])
    return command, validation


def build_repair_loop_command(payload: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    validation = validate_full_loop_payload(payload)
    if not validation["ok"]:
        return [], validation
    command = [
        sys.executable,
        "-m",
        "ta_foundation.web.strategy_lab_job",
        "repair-loop",
        "--spec",
        validation["spec_path"],
        "--compile-mode",
        validation["compile_mode"],
        "--max-repair-attempts",
        str(validation["max_repair_attempts"]),
    ]
    if validation["lab_root"]:
        command.extend(["--lab-root", validation["lab_root"]])
    if validation["overwrite"]:
        command.append("--overwrite")
    if validation["repair_llm"]:
        command.extend([
            "--repair-llm",
            "--repair-llm-model",
            validation["repair_llm_model"],
            "--repair-llm-url",
            validation["repair_llm_url"],
        ])
    return command, validation


def build_ensure_nt_ready_command(payload: dict[str, Any]) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "ta_foundation.nt_strategy_loop.cli",
        "ensure-nt-ready",
    ]
    if bool(payload.get("restart")):
        command.append("--restart")
    startup_wait = _int(payload.get("startup_wait_seconds"), 150, minimum=0)
    command.extend(["--startup-wait-seconds", str(startup_wait)])
    return command


def summarize_strategy_spec(path_value: str) -> dict[str, Any]:
    """Return user-facing identity details for a Strategy Lab spec file."""

    errors: list[str] = []
    warnings: list[str] = []
    spec_path = str(path_value or "").strip()
    data: dict[str, Any] = {}
    resolved_path = ""

    if not spec_path:
        errors.append("Choose a strategy_spec.json file first.")
    else:
        path = Path(spec_path).expanduser()
        resolved_path = str(path.resolve()) if path.exists() else str(path)
        if not path.is_file():
            errors.append(f"strategy_spec.json was not found: {spec_path}")
        else:
            try:
                loaded = json.loads(path.read_text(encoding="utf-8-sig"))
                if isinstance(loaded, dict):
                    data = loaded
                else:
                    errors.append("strategy_spec.json must contain a JSON object.")
            except Exception as exc:  # noqa: BLE001 - preview should explain parse failures
                errors.append(f"strategy_spec.json is not valid JSON: {exc}")

    strategy_name = str(data.get("strategy_name") or "").strip()
    family = str(data.get("family") or "").strip()
    parameters = data.get("parameters") if isinstance(data.get("parameters"), dict) else {}
    if data and not strategy_name:
        errors.append("strategy_spec.json must include strategy_name.")
    if data and not family:
        errors.append("strategy_spec.json must include family.")
    elif family and family not in BUILT_IN_SPEC_FAMILIES:
        warnings.append(
            f"{family!r} is not one of the built-in families shown here. "
            "It can still work if this process registers a custom renderer."
        )

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "path": resolved_path or spec_path,
        "strategy_name": strategy_name,
        "family": family,
        "intent": str(data.get("intent") or "").strip(),
        "risk_note": str(data.get("risk_note") or "").strip(),
        "parameter_keys": sorted(str(k) for k in parameters.keys()),
        "built_in_families": list(BUILT_IN_SPEC_FAMILIES),
    }


def validate_full_loop_payload(payload: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    spec_path = str(payload.get("spec_path") or "").strip()
    if not spec_path:
        errors.append("spec_path is required")
    elif not Path(spec_path).expanduser().is_file():
        errors.append(f"spec_path does not exist: {spec_path}")
    else:
        try:
            data = json.loads(Path(spec_path).expanduser().read_text(encoding="utf-8-sig"))
            if not str(data.get("strategy_name") or "").strip():
                errors.append("strategy_spec.json must include strategy_name")
            if not str(data.get("family") or "").strip():
                errors.append("strategy_spec.json must include family")
        except Exception as exc:  # noqa: BLE001 - validation should surface parse failure
            errors.append(f"spec_path is not valid JSON: {exc}")

    compile_mode = str(payload.get("compile_mode") or "live").strip()
    if compile_mode not in {"live", "fixture"}:
        errors.append("compile_mode must be live or fixture")

    validation = {
        "ok": not errors,
        "errors": errors,
        "spec_path": spec_path,
        "lab_root": str(payload.get("lab_root") or "").strip(),
        "compile_mode": compile_mode,
        "max_repair_attempts": _int(payload.get("max_repair_attempts"), 5, minimum=1),
        "overwrite": bool(payload.get("overwrite")),
        "repair_llm": bool(payload.get("repair_llm")),
        "repair_llm_model": str(payload.get("repair_llm_model") or "qwen3-coder:30b").strip(),
        "repair_llm_url": str(payload.get("repair_llm_url") or "http://localhost:11434").strip(),
        "instrument": str(payload.get("instrument") or "NQ 06-26").strip(),
        "market_suffix": str(payload.get("market_suffix") or "NQ").strip(),
        "keep_best_results": _int(payload.get("keep_best_results"), 500, minimum=1),
        "max_combinations_per_chunk": _int(payload.get("max_combinations_per_chunk"), 5000, minimum=1),
        "max_drawdown": _float(payload.get("max_drawdown"), 2500.0, minimum=0.0),
        "min_trades": _int(payload.get("min_trades"), 10, minimum=0),
        "min_profit_factor": _float(payload.get("min_profit_factor"), 1.5, minimum=0.0),
        "optimizer_timeout_seconds": _int(payload.get("optimizer_timeout_seconds"), 3600, minimum=1),
    }
    return validation


def _attempt_summaries(session_dir: Path) -> list[dict[str, Any]]:
    attempts_dir = session_dir / "attempts"
    if not attempts_dir.exists():
        return []
    rows: list[dict[str, Any]] = []
    for attempt_dir in sorted(attempts_dir.glob("attempt_*")):
        if not attempt_dir.is_dir():
            continue
        status = _read_json(attempt_dir / "compile_status.json") or {}
        source_files = sorted(attempt_dir.glob("*.cs"))
        rows.append({
            "attempt": attempt_dir.name.replace("attempt_", ""),
            "attempt_dir": str(attempt_dir.resolve()),
            "source_file": str(source_files[0].resolve()) if source_files else "",
            "compile_state": str(status.get("state") or ""),
            "compiled": status.get("compiled"),
            "error_count": _error_count(status),
            "last_error": str(status.get("lastError") or status.get("last_error") or ""),
            "compile_block_reason": str(status.get("compileBlockReason") or status.get("compile_block_reason") or ""),
            "heartbeat_utc": str(status.get("heartbeatUtc") or status.get("heartbeat_utc") or ""),
            "errors_csv": str(status.get("errorsCsv") or status.get("errors_csv") or ""),
            "errors_text": str(status.get("errorsText") or status.get("errors_text") or ""),
            "has_repair_prompt": (attempt_dir / "repair_prompt.md").is_file(),
            "has_repair_summary": (attempt_dir / "repair_summary.md").is_file(),
        })
    return rows


def _optimizer_summary(data: dict[str, Any]) -> dict[str, Any]:
    best = data.get("best_row") if isinstance(data.get("best_row"), dict) else {}
    return {
        "row_count": int(data.get("row_count") or 0),
        "passing_rows": int(data.get("passing_rows") or 0),
        "reject_reasons": list(data.get("reject_reasons") or []),
        "warnings": list(data.get("warnings") or []),
        "best_row": best,
    }


def _optimizer_state(data: dict[str, Any], artifacts: dict[str, Any]) -> str:
    if data:
        return "analyzed"
    if artifacts.get("optimizer_session_id"):
        return "created"
    return "not_started"


def _infer_stage(
    *,
    decision: str,
    attempts: list[dict[str, Any]],
    compile_clean: bool,
    optimizer_session_id: str,
    optimizer_analysis: dict[str, Any],
) -> str:
    if decision in TERMINAL_DECISIONS:
        return "done"
    if attempts and not any(a.get("source_file") or a.get("compile_state") for a in attempts):
        return "authoring_failed"
    if optimizer_analysis:
        return "analyzing"
    if optimizer_session_id:
        return "optimizer_running"
    if compile_clean:
        return "compile_clean"
    if attempts:
        latest = attempts[-1]
        if latest.get("compile_state") in {"failed", "timed_out", "worker_error"}:
            return "repairing"
        return "observing_compile"
    return "empty"


def _gate_summaries(
    *,
    decision: str,
    compile_clean: bool,
    optimizer_session_id: str,
    optimizer_analysis: dict[str, Any],
    latest_attempt: dict[str, Any] | None,
) -> list[dict[str, str]]:
    block_reason = (latest_attempt or {}).get("compile_block_reason") or ""
    return [
        _gate("Compile clean", "ready" if compile_clean else "blocked", "NinjaTrader accepted the strategy source."),
        _gate(
            "Peer compile block",
            "blocked" if block_reason == "peer_strategy_errors" else "ready",
            (latest_attempt or {}).get("last_error") or "No peer compile block reported.",
        ),
        _gate(
            "Optimizer session",
            "ready" if optimizer_session_id else ("not_applicable" if not compile_clean else "blocked"),
            optimizer_session_id or "Created after compile-clean.",
        ),
        _gate(
            "Optimizer analysis",
            "ready" if optimizer_analysis else ("not_applicable" if not optimizer_session_id else "blocked"),
            "Guardrail analysis is available." if optimizer_analysis else "Waiting for optimizer results.",
        ),
        _gate(
            "Human deployment review",
            "needs_review" if decision == "candidate" else "not_applicable",
            "Candidate requires operator review before paper/live use.",
        ),
    ]


def _gate(name: str, state: str, message: str) -> dict[str, str]:
    return {"name": name, "state": state, "message": message}


def _artifact_links(session_dir: Path, artifacts: dict[str, Any]) -> list[dict[str, str]]:
    candidates = [
        ("Manifest", session_dir / "manifest.json"),
        ("Strategy spec", session_dir / "strategy_spec.json"),
        ("Source request", session_dir / "source_request.md"),
        ("Loop summary", session_dir / "decisions" / "STRATEGY_LOOP_SUMMARY.md"),
        ("Next action", session_dir / "decisions" / "NEXT_ACTION.md"),
        ("Optimizer analysis", session_dir / "optimizer" / "optimizer_analysis.json"),
    ]
    if artifacts.get("seed_template"):
        candidates.append(("Seed template", Path(str(artifacts["seed_template"]))))
    return [
        {"label": label, "path": str(path.resolve())}
        for label, path in candidates
        if path.exists()
    ]


def _has_compile_clean_source(session_dir: Path) -> bool:
    return any((session_dir / "compile_clean").glob("*.cs"))


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _read_text(path: Path, *, limit: int = 12000) -> str:
    try:
        if not path.is_file():
            return ""
        text = path.read_text(encoding="utf-8", errors="replace")
        return text[:limit]
    except Exception:
        return ""


def _error_count(status: dict[str, Any]) -> int | None:
    for key in ("error_count", "errorCount"):
        if key in status:
            try:
                return int(status[key])
            except (TypeError, ValueError):
                return None
    errors = status.get("errors")
    if isinstance(errors, list):
        return len(errors)
    return None


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _latest_iso(path: Path) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(_mtime(path), tz=timezone.utc).isoformat(timespec="seconds")


def _int(value: Any, default: int, *, minimum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, parsed)


def _float(value: Any, default: float, *, minimum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, parsed)
