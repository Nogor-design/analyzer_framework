from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from ta_foundation.core.daily_outcomes import derive_daily_outcomes_for_package
from ta_foundation.core.model import AnalysisPackage
from ta_foundation.reports.momentum_board import build_strategy_momentum_board
from ta_foundation.reports.session_momentum_board import (
    SESSION_FAMILIES,
    active_window_for_pkg,
    build_stackability_summary,
    classify_strategy_session,
)

TZ_DENVER = ZoneInfo("America/Denver")

_BOT_LINE_RE = re.compile(
    r"^(?P<run_id>[^|]+?)\s*\|\s*"
    r"(?P<window>[^|]+?)\s*\|\s*"
    r"Trigger Odds\s+(?P<trigger>\d+(?:\.\d+)?)%\s*\|\s*"
    r"Success Odds\s+(?P<success>\d+(?:\.\d+)?)%\s*\|\s*"
    r"R/R\s+(?P<rr>.+?)\s*$",
    re.IGNORECASE,
)
_TIME_RANGE_RE = re.compile(
    r"^\s*(?P<start>\d{1,2}:\d{2}\s*[AP]M)\s*[–-]\s*(?P<end>\d{1,2}:\d{2}\s*[AP]M)(?:\s+(?P<tz>[A-Za-z]+))?\s*$",
    re.IGNORECASE,
)


def _load_board_text(path_str: str) -> tuple[Path, str]:
    path = Path(path_str).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    path = path.resolve()
    return path, path.read_text(encoding="utf-8")


def _parse_percent(line: str, label: str) -> Optional[float]:
    match = re.search(rf"{re.escape(label)}\s+(\d+(?:\.\d+)?)\s*percent", line, flags=re.IGNORECASE)
    return float(match.group(1)) if match else None


def _parse_account_posture(line: str) -> Optional[Dict[str, int]]:
    match = re.search(r"account posture at\s+(\d+)\s+out of\s+(\d+)\s+accounts", line, flags=re.IGNORECASE)
    if not match:
        return None
    return {"active": int(match.group(1)), "capacity": int(match.group(2))}


def _clock_to_minutes(text: str) -> Optional[int]:
    try:
        dt = datetime.strptime(text.strip().upper(), "%I:%M %p")
    except Exception:
        return None
    return dt.hour * 60 + dt.minute


def _window_from_board_label(label: str) -> Dict[str, Any]:
    match = _TIME_RANGE_RE.match(str(label or "").strip())
    if not match:
        return {
            "raw_label": str(label or "-").strip() or "-",
            "label": str(label or "-").strip() or "-",
            "duration_label": "-",
            "start_minute": None,
            "end_minute": None,
            "duration_minutes": None,
        }

    start_minute = _clock_to_minutes(match.group("start"))
    end_minute = _clock_to_minutes(match.group("end"))
    tz_code = (match.group("tz") or "").strip().upper()
    raw_label = str(label).strip()

    if start_minute is None or end_minute is None:
        duration = None
    else:
        duration = end_minute - start_minute
        if duration <= 0:
            duration += 24 * 60

    duration_label = "-"
    if duration is not None:
        duration_label = f"{duration / 60.0:.1f}h" if duration % 60 else f"{duration // 60}h"

    suffix = f" {tz_code}" if tz_code else ""
    return {
        "raw_label": raw_label,
        "label": f"{match.group('start').upper()}-{match.group('end').upper()}{suffix}",
        "duration_label": duration_label,
        "start_minute": start_minute,
        "end_minute": None if duration is None else start_minute + duration,
        "duration_minutes": duration,
        "timezone_code": tz_code,
    }


def _session_from_run_id(run_id: str) -> Dict[str, str]:
    base = str(run_id).split("-", 1)[0]
    token_match = re.match(r"[A-Z][a-z]*", base)
    token = token_match.group(0).lower() if token_match else base.lower()
    for family in SESSION_FAMILIES:
        if token in {family.single_token.lower(), family.multi_token.lower()}:
            return {"slug": family.slug, "label": family.label, "source": "name_token"}
    return {"slug": "unclassified", "label": "Unclassified", "source": "unknown"}


def parse_deployment_board_text(text: str) -> Dict[str, Any]:
    lines = [line.rstrip() for line in str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")]

    intro_lines: List[str] = []
    current_regime_line = ""
    deployment_law: List[str] = []
    summary_lines: List[str] = []
    sections: Dict[str, List[Dict[str, Any]]] = {"primary": [], "secondary": [], "reserve": []}
    current_section: Optional[str] = None
    signer = ""

    section_map = {
        "primary bots": "primary",
        "secondary bots": "secondary",
        "reserve": "reserve",
    }

    i = 0
    while i < len(lines):
        raw_line = lines[i]
        line = raw_line.strip()
        lower = line.lower()

        if not line:
            i += 1
            continue

        if lower in section_map:
            current_section = section_map[lower]
            i += 1
            continue
        if lower == "deployment law":
            current_section = "deployment_law"
            i += 1
            continue
        if lower == "summary":
            current_section = "summary"
            i += 1
            continue

        bot_match = _BOT_LINE_RE.match(line) if current_section in {"primary", "secondary", "reserve"} else None
        if bot_match:
            entry = {
                "tier": current_section,
                "run_id": bot_match.group("run_id").strip(),
                "board_window": _window_from_board_label(bot_match.group("window").strip()),
                "trigger_odds": float(bot_match.group("trigger")),
                "success_odds": float(bot_match.group("success")),
                "rr_text": bot_match.group("rr").strip(),
                "reason": "",
            }
            if i + 1 < len(lines) and lines[i + 1].strip().lower().startswith("reason:"):
                entry["reason"] = lines[i + 1].strip()[len("reason:") :].strip()
                i += 1
            sections[current_section].append(entry)
            i += 1
            continue

        if current_section == "deployment_law":
            deployment_law.append(line[1:].strip() if line.startswith("-") else line)
        elif current_section == "summary":
            summary_lines.append(line)
        else:
            if lower.startswith("current regime is "):
                current_regime_line = line
            else:
                intro_lines.append(line)
        i += 1

    if summary_lines:
        possible_signer = summary_lines[-1].strip()
        if possible_signer and len(possible_signer.split()) <= 2 and ":" not in possible_signer and "|" not in possible_signer:
            signer = possible_signer
            summary_lines = summary_lines[:-1]

    rows: List[Dict[str, Any]] = []
    board_rank = 1
    tier_order = {"primary": 0, "secondary": 1, "reserve": 2}
    for tier in ("primary", "secondary", "reserve"):
        for tier_rank, entry in enumerate(sections[tier], start=1):
            row = dict(entry)
            row["tier_rank"] = tier_rank
            row["tier_order"] = tier_order[tier]
            row["board_rank"] = board_rank
            board_rank += 1
            rows.append(row)

    account_posture = _parse_account_posture(current_regime_line)
    breakout_pct = _parse_percent(current_regime_line, "Breakout")
    regression_pct = _parse_percent(current_regime_line, "Regression")

    return {
        "intro_lines": intro_lines,
        "current_regime_line": current_regime_line,
        "breakout_pct": breakout_pct,
        "regression_pct": regression_pct,
        "account_posture": account_posture,
        "sections": sections,
        "rows": rows,
        "deployment_law": deployment_law,
        "summary_text": " ".join(summary_lines).strip(),
        "summary_lines": summary_lines,
        "signer": signer,
        "raw_text": "\n".join(lines).strip(),
    }


def build_deployment_board_insight(
    packages: Dict[str, AnalysisPackage],
    *,
    board_text_path: str,
    as_of: Optional[date] = None,
    strip_days: int = 5,
) -> Dict[str, Any]:
    resolved_as_of = as_of or datetime.now(TZ_DENVER).date()
    board_path, raw_text = _load_board_text(board_text_path)
    parsed = parse_deployment_board_text(raw_text)

    momentum = build_strategy_momentum_board(
        packages,
        as_of=resolved_as_of,
        strip_days=strip_days,
        top_n=0,
    )
    momentum_by_id = {str(row["run_id"]): row for row in momentum["rows"]}

    rows: List[Dict[str, Any]] = []
    for entry in parsed["rows"]:
        row = dict(entry)
        matched = momentum_by_id.get(str(row["run_id"]))
        row["matched"] = matched is not None
        if matched is not None:
            pkg = matched["pkg"]
            session_info = classify_strategy_session(row["run_id"], pkg)
            active_window = active_window_for_pkg(pkg)
            outcomes = derive_daily_outcomes_for_package(pkg).get("by_date", {}) or {}
            today_payload = outcomes.get(resolved_as_of.isoformat()) or {}
            day_profit = today_payload.get("net_profit")
            day_trades = int(today_payload.get("trades") or 0)
            day_status = str(today_payload.get("status") or ("NO_TRADE" if day_trades <= 0 else "")).upper()
            if day_trades > 0 and not day_status:
                profit_value = float(day_profit or 0.0)
                if profit_value > 0:
                    day_status = "WIN"
                elif profit_value < 0:
                    day_status = "LOSS"
                else:
                    day_status = "FLAT"
            row["pkg"] = pkg
            row["recent5"] = matched["recent5"]
            row["recent10"] = matched["recent10"]
            row["recent20"] = matched["recent20"]
            row["prev5"] = matched["prev5"]
            row["delta5"] = matched["delta5"]
            row["score"] = matched["score"]
            row["status"] = matched["status"]
            row["session_label"] = session_info["label"]
            row["session_source"] = session_info["source"]
            row["active_window"] = active_window
            derived = (getattr(pkg, "metadata", None) or {}).get("derived", {}) or {}
            assets = getattr(pkg, "assets", None) or {}
            row["run_image_uri"] = derived.get("run_image_uri") or assets.get("run_image_uri")
            row["background_image_uri"] = derived.get("background_image_uri") or assets.get("background_image_uri")
            row["today_profit"] = float(day_profit) if day_profit is not None else None
            row["today_trades"] = day_trades
            row["today_status"] = day_status
        else:
            fallback_session = _session_from_run_id(row["run_id"])
            row["pkg"] = None
            row["recent5"] = None
            row["recent10"] = None
            row["recent20"] = None
            row["prev5"] = None
            row["delta5"] = None
            row["score"] = None
            row["status"] = "Unmatched"
            row["session_label"] = fallback_session["label"]
            row["session_source"] = fallback_session["source"]
            row["active_window"] = {
                "label": "-",
                "duration_label": "-",
                "start_minute": None,
                "duration_minutes": None,
                "end_minute": None,
            }
            row["run_image_uri"] = None
            row["background_image_uri"] = None
            row["today_profit"] = None
            row["today_trades"] = 0
            row["today_status"] = "UNMATCHED"
        rows.append(row)

    recommendation_pool = [row for row in rows if row.get("board_window")]
    for row in rows:
        earlier = [other for other in recommendation_pool if int(other["board_rank"]) < int(row["board_rank"])]
        row["stackability"] = build_stackability_summary(
            {"run_id": row["run_id"], "active_window": row.get("board_window") or {}},
            [{"run_id": e["run_id"], "overall_rank": e["board_rank"], "session_label": e["session_label"], "active_window": e.get("board_window") or {}} for e in earlier],
        )

    top_pick = rows[0] if rows else None
    strongest_support = max(
        [row for row in rows if row.get("recent10") is not None],
        key=lambda row: (row["recent10"].pnl, row["recent5"].pnl, row.get("score") or 0.0),
        default=None,
    )
    cleanest_stack = min(
        [row for row in rows if (row.get("stackability") or {}).get("status") == "safe"],
        key=lambda row: (row["tier_order"], row["tier_rank"]),
        default=None,
    )
    earliest_window = min(
        [row for row in rows if (row.get("board_window") or {}).get("start_minute") is not None],
        key=lambda row: int((row["board_window"] or {}).get("start_minute") or 0),
        default=None,
    )

    return {
        "board_path": board_path,
        "as_of": resolved_as_of,
        "strip_days": momentum["strip_days"],
        "rows": rows,
        "parsed": parsed,
        "summary": {
            "top_pick": top_pick,
            "strongest_support": strongest_support,
            "cleanest_stack": cleanest_stack,
            "earliest_window": earliest_window,
        },
    }
