from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, List, Optional

import pandas as pd

from ta_foundation.core.model import AnalysisPackage
from ta_foundation.reports.momentum_board import build_strategy_momentum_board


@dataclass(frozen=True)
class SessionFamily:
    slug: str
    label: str
    single_token: str
    multi_token: str
    start_minute: int
    end_minute: int


SESSION_FAMILIES: tuple[SessionFamily, ...] = (
    SessionFamily("asia", "Asia", "Dawn", "Dawning", 17 * 60, 24 * 60),
    SessionFamily("london_early", "London Early", "Rise", "Rising", 0, 3 * 60),
    SessionFamily("london_late", "London Late", "Prime", "Priming", 3 * 60, 6 * 60),
    SessionFamily("pre_market", "Pre-Market", "Coil", "Coiling", 6 * 60, 7 * 60 + 30),
    SessionFamily("overlap", "Overlap", "War", "Warring", 7 * 60 + 30, 8 * 60 + 30),
    SessionFamily("ny_open", "NY Open", "Rage", "Raging", 8 * 60 + 30, 10 * 60),
    SessionFamily("midday", "Midday", "Drift", "Drifting", 10 * 60, 13 * 60),
    SessionFamily("power_hour", "Power Hour", "Close", "Closing", 13 * 60, 17 * 60),
)


def _first_token_from_run_id(run_id: str) -> str:
    base = str(run_id).split("-", 1)[0]
    match = re.match(r"[A-Z][a-z]*", base)
    token = match.group(0) if match else base
    return token.strip().lower()


def _settings_map(pkg: AnalysisPackage) -> Dict[str, Any]:
    df = getattr(pkg, "settings", None)
    if not isinstance(df, pd.DataFrame) or df.empty:
        return {}

    item_col = next((c for c in df.columns if str(c).strip().lower() == "item"), None)
    value_col = next((c for c in df.columns if str(c).strip().lower() == "value"), None)
    if not item_col or not value_col:
        return {}

    out: Dict[str, Any] = {}
    for _, row in df.iterrows():
        key = str(row.get(item_col, "")).strip().lower()
        if key:
            out[key] = row.get(value_col)
    return out


def _norm_key(value: str) -> str:
    return "".join(ch for ch in str(value).strip().lower() if ch.isalnum())


def _settings_lookup(settings: Dict[str, Any], *candidates: str) -> Any:
    if not settings:
        return None

    direct = {str(k).strip().lower(): v for k, v in settings.items()}
    normalized = {_norm_key(k): v for k, v in settings.items()}

    for candidate in candidates:
        key = str(candidate).strip().lower()
        if key in direct:
            return direct[key]
        nkey = _norm_key(candidate)
        if nkey in normalized:
            return normalized[nkey]
    return None


def _first_token_from_settings(pkg: AnalysisPackage) -> str:
    settings = _settings_map(pkg)
    for key in ("bot_name", "bot name", "botname"):
        value = _settings_lookup(settings, key)
        if value is None:
            continue
        text = str(value).strip()
        if not text:
            continue
        first = text.split()[0].strip().lower()
        if first:
            return first
    return ""


def _start_minute_from_settings(pkg: AnalysisPackage) -> Optional[int]:
    settings = _settings_map(pkg)

    def _coerce_int(key: str) -> Optional[int]:
        raw = _settings_lookup(
            settings,
            key,
            key.replace("_", " "),
            key.replace("_", ""),
        )
        if raw is None or str(raw).strip() == "":
            return None
        try:
            return int(float(str(raw).strip()))
        except Exception:
            return None

    hour = _coerce_int("start_time_(hh)")
    minute = _coerce_int("start_time_(mm)")
    if hour is None or minute is None:
        return None
    return hour * 60 + minute


def _duration_minute_from_settings(pkg: AnalysisPackage) -> Optional[int]:
    settings = _settings_map(pkg)

    def _coerce_int(key: str) -> Optional[int]:
        raw = _settings_lookup(
            settings,
            key,
            key.replace("_", " "),
            key.replace("_", ""),
        )
        if raw is None or str(raw).strip() == "":
            return None
        try:
            return int(float(str(raw).strip()))
        except Exception:
            return None

    hour = _coerce_int("duration_time_(hh)")
    minute = _coerce_int("duration_time_(mm)")
    if hour is None or minute is None:
        return None
    return hour * 60 + minute


def _fmt_clock(total_minutes: int) -> str:
    total = int(total_minutes) % (24 * 60)
    return f"{total // 60:02d}:{total % 60:02d}"


def active_window_for_pkg(pkg: AnalysisPackage) -> Dict[str, Any]:
    start_minute = _start_minute_from_settings(pkg)
    duration_minutes = _duration_minute_from_settings(pkg)
    if start_minute is None or duration_minutes is None:
        return {
            "start_minute": start_minute,
            "duration_minutes": duration_minutes,
            "end_minute": None,
            "label": "-",
            "duration_label": "-",
        }

    end_minute = start_minute + duration_minutes
    duration_hours = duration_minutes / 60.0
    duration_label = f"{duration_hours:.1f}h" if duration_minutes % 60 else f"{duration_minutes // 60}h"
    return {
        "start_minute": start_minute,
        "duration_minutes": duration_minutes,
        "end_minute": end_minute,
        "label": f"{_fmt_clock(start_minute)}-{_fmt_clock(end_minute)} MT",
        "duration_label": duration_label,
    }


def _window_segments(window: Dict[str, Any]) -> List[tuple[int, int]]:
    start_minute = window.get("start_minute")
    duration_minutes = window.get("duration_minutes")
    if start_minute is None or duration_minutes is None:
        return []

    start = int(start_minute) % (24 * 60)
    duration = int(duration_minutes)
    if duration <= 0:
        return []
    if duration >= 24 * 60:
        return [(0, 24 * 60)]

    end = start + duration
    if end <= 24 * 60:
        return [(start, end)]
    return [(start, 24 * 60), (0, end % (24 * 60))]


def windows_overlap(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    a_segments = _window_segments(a)
    b_segments = _window_segments(b)
    if not a_segments or not b_segments:
        return False

    for a_start, a_end in a_segments:
        for b_start, b_end in b_segments:
            if max(a_start, b_start) < min(a_end, b_end):
                return True
    return False


def build_stackability_summary(
    row: Dict[str, Any],
    comparators: List[Dict[str, Any]],
) -> Dict[str, Any]:
    window = row.get("active_window") or {}
    if window.get("start_minute") is None or window.get("duration_minutes") is None:
        return {
            "status": "unknown",
            "label": "Window missing",
            "detail": "No settings time found",
            "conflicts": [],
            "checked": 0,
        }

    checked = 0
    conflicts: List[Dict[str, Any]] = []
    for other in comparators:
        if other.get("run_id") == row.get("run_id"):
            continue
        other_window = other.get("active_window") or {}
        if other_window.get("start_minute") is None or other_window.get("duration_minutes") is None:
            continue
        checked += 1
        if windows_overlap(window, other_window):
            conflicts.append(
                {
                    "run_id": other.get("run_id"),
                    "rank": other.get("overall_rank") or other.get("rank"),
                    "session_label": other.get("session_label", ""),
                    "window_label": other_window.get("label", "-"),
                }
            )

    if conflicts:
        first = conflicts[0]
        label = (
            f"Overlaps #{first['rank']} {first['session_label']}"
            if len(conflicts) == 1
            else f"Overlaps {len(conflicts)} top bots"
        )
        detail = ", ".join(
            f"#{c['rank']} {c['window_label']}" for c in conflicts[:2]
        )
        if len(conflicts) > 2:
            detail += f" +{len(conflicts) - 2} more"
        return {
            "status": "conflict",
            "label": label,
            "detail": detail,
            "conflicts": conflicts,
            "checked": checked,
        }

    if checked > 0:
        return {
            "status": "safe",
            "label": f"Safe with top {checked}",
            "detail": "No overlap detected",
            "conflicts": [],
            "checked": checked,
        }

    return {
        "status": "unknown",
        "label": "No peers checked",
        "detail": "Comparator windows unavailable",
        "conflicts": [],
        "checked": 0,
    }


def classify_strategy_session(run_id: str, pkg: AnalysisPackage) -> Dict[str, Any]:
    token_candidates = [_first_token_from_run_id(run_id), _first_token_from_settings(pkg)]
    for token in token_candidates:
        for family in SESSION_FAMILIES:
            if token in {family.single_token.lower(), family.multi_token.lower()}:
                return {
                    "slug": family.slug,
                    "label": family.label,
                    "source": "name_token",
                    "matched_token": token,
                    "family": family,
                }

    start_minute = _start_minute_from_settings(pkg)
    if start_minute is not None:
        for family in SESSION_FAMILIES:
            if family.start_minute <= start_minute < family.end_minute:
                return {
                    "slug": family.slug,
                    "label": family.label,
                    "source": "settings_start_time",
                    "matched_token": None,
                    "family": family,
                }

    return {
        "slug": "unclassified",
        "label": "Unclassified",
        "source": "unknown",
        "matched_token": None,
        "family": None,
    }


def build_strategy_session_momentum_board(
    packages: Dict[str, AnalysisPackage],
    *,
    as_of: Optional[date] = None,
    strip_days: int = 5,
    overall_top_n: int = 5,
    top_n_per_session: int = 5,
    overlap_compare_top_n: int = 3,
) -> Dict[str, Any]:
    board = build_strategy_momentum_board(
        packages,
        as_of=as_of,
        strip_days=strip_days,
        top_n=0,
    )

    rows = board["rows"]
    grouped: Dict[str, Dict[str, Any]] = {
        family.slug: {"family": family, "rows": []}
        for family in SESSION_FAMILIES
    }
    grouped["unclassified"] = {"family": None, "rows": []}

    for row in rows:
        session_info = classify_strategy_session(row["run_id"], row["pkg"])
        active_window = active_window_for_pkg(row["pkg"])
        row["session_slug"] = session_info["slug"]
        row["session_label"] = session_info["label"]
        row["session_source"] = session_info["source"]
        row["session_token"] = session_info["matched_token"]
        row["active_window"] = active_window
        grouped.setdefault(session_info["slug"], {"family": session_info.get("family"), "rows": []})
        grouped[session_info["slug"]]["rows"].append(row)

    group_sections: List[Dict[str, Any]] = []
    for family in SESSION_FAMILIES:
        rows_for_group = grouped[family.slug]["rows"]
        for idx, row in enumerate(rows_for_group[:top_n_per_session], start=1):
            row[f"group_rank_{family.slug}"] = idx
        group_sections.append(
            {
                "slug": family.slug,
                "label": family.label,
                "single_token": family.single_token,
                "multi_token": family.multi_token,
                "rows": rows_for_group[:top_n_per_session],
                "count": len(rows_for_group),
            }
        )

    if grouped["unclassified"]["rows"]:
        unclassified_rows = grouped["unclassified"]["rows"]
        for idx, row in enumerate(unclassified_rows[:top_n_per_session], start=1):
            row["group_rank_unclassified"] = idx
        group_sections.append(
            {
                "slug": "unclassified",
                "label": "Unclassified",
                "single_token": "",
                "multi_token": "",
                "rows": unclassified_rows[:top_n_per_session],
                "count": len(unclassified_rows),
            }
        )

    overall_rows = rows[:overall_top_n] if overall_top_n > 0 else list(rows)
    for idx, row in enumerate(overall_rows, start=1):
        row["overall_rank"] = idx

    comparison_pool = rows[:overlap_compare_top_n] if overlap_compare_top_n > 0 else []
    for row in rows:
        row["stackability"] = build_stackability_summary(row, comparison_pool)

    return {
        "as_of": board["as_of"],
        "strip_days": board["strip_days"],
        "rows": rows,
        "overall_rows": overall_rows,
        "groups": group_sections,
        "summary": board["summary"],
        "overlap_compare_top_n": overlap_compare_top_n,
    }
