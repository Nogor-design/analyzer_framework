from __future__ import annotations

import sys
from pathlib import Path
from typing import Any


def build_prediction_command_args(body: dict[str, Any]) -> list[str]:
    """Build CLI args for existing prediction entry points without reimplementing them."""
    job_type = _clean_str(body.get("prediction_job_type") or body.get("job_type") or "run_prediction")

    if job_type == "run_prediction":
        config_path = _required_existing_path(body, "config_path")
        args = [sys.executable, "-m", "ta_foundation.prediction.run_prediction", "--config", config_path]
        _append_optional(args, body, "asof", "--asof")
        if bool(body.get("dry_run")):
            args.append("--dry-run")
        if bool(body.get("no_catchup")):
            args.append("--no-catchup")
        return args

    if job_type == "run_multi_agent":
        config_path = _required_existing_path(body, "config_path")
        args = [sys.executable, "-m", "ta_foundation.prediction.run_multi_agent", "--config", config_path]
        _append_optional(args, body, "asof", "--asof")
        _append_optional(args, body, "score_date", "--score-date")
        _append_optional(args, body, "backtest", "--backtest")
        if bool(body.get("leaderboard_only")):
            args.append("--leaderboard-only")
        return args

    if job_type == "backtest_horizon":
        minute_bars_file = _required_existing_path(body, "minute_bars_file")
        store_dir = _required_str(body, "store_dir")
        args = [
            sys.executable,
            "-m",
            "ta_foundation.prediction.backtest_horizon_predictions",
            "--minute-bars-file",
            minute_bars_file,
            "--store-dir",
            store_dir,
        ]
        _append_optional(args, body, "instrument", "--instrument")
        _append_optional(args, body, "contract", "--contract")
        _append_optional(args, body, "timeframes", "--timeframes")
        _append_optional(args, body, "horizons", "--horizons")
        _append_optional(args, body, "asof_warmup", "--asof-warmup")
        _append_optional(args, body, "asof_stride", "--asof-stride")
        _append_optional(args, body, "asof_start", "--asof-start")
        _append_optional(args, body, "asof_end", "--asof-end")
        _append_optional(args, body, "config_path", "--config", must_exist=True)
        if bool(body.get("print_report")):
            args.append("--print-report")
        return args

    raise ValueError(
        "Unknown prediction_job_type: "
        f"{job_type!r}. Expected run_prediction, run_multi_agent, or backtest_horizon."
    )


def validate_prediction_job_request(body: dict[str, Any]) -> dict[str, Any]:
    try:
        build_prediction_command_args(body)
    except (FileNotFoundError, ValueError) as exc:
        return {"ok": False, "errors": [str(exc)], "warnings": []}
    return {"ok": True, "errors": [], "warnings": []}


def _append_optional(
    args: list[str],
    body: dict[str, Any],
    field: str,
    flag: str,
    *,
    must_exist: bool = False,
) -> None:
    value = _clean_str(body.get(field))
    if not value:
        return
    if must_exist and not Path(value).exists():
        raise FileNotFoundError(f"{field} does not exist: {value}")
    args.extend([flag, value])


def _required_existing_path(body: dict[str, Any], field: str) -> str:
    value = _required_str(body, field)
    if not Path(value).exists():
        raise FileNotFoundError(f"{field} does not exist: {value}")
    return value


def _required_str(body: dict[str, Any], field: str) -> str:
    value = _clean_str(body.get(field))
    if not value:
        raise ValueError(f"{field} is required.")
    return value


def _clean_str(value: Any) -> str:
    return str(value or "").strip()

