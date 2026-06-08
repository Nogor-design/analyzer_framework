"""Phase-0 outcome ledger — an accountable record of issued daily lineups.

Append-only JSONL. Every lineup we recommend is journaled *as issued*; the
realised per-template P&L for that day is journaled later. Joining the two gives
an auditable track record of what the selector actually did — Eric's
"accountable prediction" — and the real-recommendation substrate the Phase-1
validation grades against baselines (``grade.py``).

This is deliberately NOT the ``research_ledger`` SQLite layer (that tracks
research *hypotheses/runs/candidates* for the agentic program). This is a small,
inspectable, forward-only journal of *production lineup recommendations*: one
JSON object per line, ``kind`` in {``"recommendation"``, ``"actuals"``}, keyed by
``ledger_id``. Last write wins on re-journal. No mutation, no DB.

See ``docs/designs/daily_lineup_selector.md`` and
``docs/designs/strategy_business_roadmap.md`` Phase 0.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

DEFAULT_LEDGER_PATH = Path(".ta_artifacts/selection/lineup_ledger.jsonl")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class LineupPick:
    """One template chosen for one slice, with the selector's rationale fields."""

    slice_key: str
    template_id: str
    score: Optional[float] = None
    rationale: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LineupRecommendation:
    """A daily lineup *as issued* — what we recommended for ``for_day``.

    ``for_day`` is the trading day the lineup is *for* (ISO date). ``ledger_id``
    is the stable join key (default ``<selector_version>:<for_day>`` so re-running
    the same selector for the same day overwrites rather than duplicates).
    """

    for_day: date
    selector_version: str
    picks: list[LineupPick]
    source_session: Optional[str] = None
    regime: Optional[str] = None
    notes: dict[str, Any] = field(default_factory=dict)
    ledger_id: str = ""
    created_at: str = field(default_factory=_now_iso)

    def __post_init__(self) -> None:
        if not self.ledger_id:
            object.__setattr__(
                self, "ledger_id", f"{self.selector_version}:{self.for_day.isoformat()}"
            )

    def slices(self) -> dict[str, list[str]]:
        """slice_key -> [template_id, ...] for the issued picks."""
        out: dict[str, list[str]] = {}
        for p in self.picks:
            out.setdefault(p.slice_key, []).append(p.template_id)
        return out

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["kind"] = "recommendation"
        d["for_day"] = self.for_day.isoformat()
        return d

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "LineupRecommendation":
        return LineupRecommendation(
            for_day=date.fromisoformat(d["for_day"]),
            selector_version=d["selector_version"],
            picks=[LineupPick(**p) for p in d.get("picks", [])],
            source_session=d.get("source_session"),
            regime=d.get("regime"),
            notes=d.get("notes", {}) or {},
            ledger_id=d.get("ledger_id", ""),
            created_at=d.get("created_at", ""),
        )


@dataclass(frozen=True)
class DayActuals:
    """Realised per-template P&L for the day a recommendation was issued for.

    ``realized_by_template`` is the single source of truth (template_id -> $).
    The lineup's realised total is *derived* by joining against the
    recommendation's slices (see ``grade.py``) — never stored, to avoid drift.
    """

    ledger_id: str
    realized_by_template: dict[str, float]
    recorded_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["kind"] = "actuals"
        return d

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "DayActuals":
        return DayActuals(
            ledger_id=d["ledger_id"],
            realized_by_template={k: float(v) for k, v in (d.get("realized_by_template") or {}).items()},
            recorded_at=d.get("recorded_at", ""),
        )


class OutcomeLedger:
    """Append-only JSONL journal of lineup recommendations + realised actuals."""

    def __init__(self, path: Path | str = DEFAULT_LEDGER_PATH) -> None:
        self.path = Path(path)

    # ---- writing -------------------------------------------------------
    def _append(self, obj: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(obj, sort_keys=True) + "\n")

    def record_recommendation(self, rec: LineupRecommendation, *, overwrite: bool = False) -> bool:
        """Journal a recommendation. Idempotent on ``ledger_id``: if one already
        exists and ``overwrite`` is False, do nothing and return False (so a
        daily cron re-run is safe). ``overwrite=True`` appends a new version
        (last-wins on read)."""
        if not overwrite and rec.ledger_id in self.recommendations():
            return False
        self._append(rec.to_dict())
        return True

    def record_actuals(self, actuals: DayActuals) -> None:
        """Journal realised P&L for a recommendation (last-wins on re-record)."""
        self._append(actuals.to_dict())

    # ---- reading -------------------------------------------------------
    def _rows(self) -> Iterator[dict[str, Any]]:
        if not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    yield json.loads(line)

    def recommendations(self) -> dict[str, LineupRecommendation]:
        """ledger_id -> latest recommendation (last write wins)."""
        out: dict[str, LineupRecommendation] = {}
        for row in self._rows():
            if row.get("kind") == "recommendation":
                rec = LineupRecommendation.from_dict(row)
                out[rec.ledger_id] = rec
        return out

    def actuals(self) -> dict[str, DayActuals]:
        """ledger_id -> latest actuals (last write wins)."""
        out: dict[str, DayActuals] = {}
        for row in self._rows():
            if row.get("kind") == "actuals":
                a = DayActuals.from_dict(row)
                out[a.ledger_id] = a
        return out

    def joined(self) -> list[tuple[LineupRecommendation, Optional[DayActuals]]]:
        """(recommendation, actuals|None) for every recommendation, by for_day."""
        recs = self.recommendations()
        acts = self.actuals()
        return [
            (recs[k], acts.get(k))
            for k in sorted(recs, key=lambda k: recs[k].for_day)
        ]
