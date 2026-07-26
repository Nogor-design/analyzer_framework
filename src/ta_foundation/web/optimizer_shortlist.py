from __future__ import annotations

"""Per-session shortlist of candidate rows the operator wants to keep.

A small, server-side "shopping cart" that lets the operator pick
interesting rows from the Leaderboard or Candidate Results panels and
then act on them from the Decision Dashboard (or any other page that
includes the shared sidebar).

Persistence shape (``<session>/shortlist.json``):

  {
    "schema_version": 1,
    "updated_at": "2026-05-30T18:45:00+00:00",
    "items": [
      {"stage_id": "stage_2",
       "candidate_id": "stage_2__T_X__row01234",
       "source": "leaderboard",
       "added_at": "2026-05-30T18:42:11+00:00"},
      ...
    ]
  }

Items are pointers, not snapshots. KPIs, params, and links are
re-resolved on every read so the sidebar stays accurate after
re-ingest. A pointer whose row can no longer be found in the
session's parsed_results / evaluated_candidates is returned with
``status == "stale"`` so the UI can flag it.

Two row classes are tracked:
  * ``stage_id == "final_backtest"`` — finalist rows from
    ``evaluated_candidates.json``. These already exist as fixed-
    backtest templates and drive the existing build-reports /
    refine-from-decision flows.
  * Any other ``stage_id`` — in-sample optimizer rows from
    ``parsed_results/<stage_id>/scored_rows.json``. Acting on these
    needs a separate promotion step that isn't shipped yet; the
    sidebar shows them as "not actionable yet" so the operator
    knows the shortlist captured them but they need a follow-up
    PR's promotion path.
"""

import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from ta_foundation.web.optimizer_session import OptimizerSession


SHORTLIST_FILENAME = "shortlist.json"
SCHEMA_VERSION = 2
FINAL_BACKTEST_STAGE_ID = "final_backtest"

# Source tags we accept from the UI. Free-form text is OK too but
# limiting the canonical set keeps telemetry sane.
KNOWN_SOURCES: tuple[str, ...] = (
    "leaderboard",
    "candidate_results",
    "decision_dashboard",
    "lineage",
    "api",
)

ITEM_STATUS_OK = "ok"
ITEM_STATUS_STALE = "stale"          # pointer no longer resolves
ITEM_STATUS_PENDING = "pending"      # row exists but not yet actionable (stage row)
ITEM_STATUS_PROMOTED = "promoted"    # stage row has a promoted NT template; awaiting NT run / parse


class ShortlistError(Exception):
    pass


@dataclass(frozen=True)
class ShortlistItem:
    """A stored pointer to a row the operator wants to keep.

    ``promoted_run_id`` / ``promoted_at`` are stamped when a pending stage
    row has been turned into a NinjaTrader fixed-backtest template via the
    promotion path (see :mod:`ta_foundation.web.optimizer_promotion`). They
    survive across reloads so the UI knows the row is no longer "needs
    action" and re-clicking the Promote button is a no-op.
    """
    stage_id: str
    candidate_id: str
    source: str           # KNOWN_SOURCES value; arbitrary string accepted
    added_at: str         # ISO-8601 UTC
    promoted_run_id: str | None = None
    promoted_at: str | None = None

    def key(self) -> tuple[str, str]:
        return (self.stage_id, self.candidate_id)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "stage_id": self.stage_id,
            "candidate_id": self.candidate_id,
            "source": self.source,
            "added_at": self.added_at,
        }
        if self.promoted_run_id is not None:
            payload["promoted_run_id"] = self.promoted_run_id
        if self.promoted_at is not None:
            payload["promoted_at"] = self.promoted_at
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ShortlistItem":
        sid = str(data.get("stage_id") or "").strip()
        cid = str(data.get("candidate_id") or "").strip()
        if not sid or not cid:
            raise ShortlistError(
                "shortlist item requires non-empty stage_id and candidate_id"
            )
        promoted_run_id = data.get("promoted_run_id")
        promoted_at = data.get("promoted_at")
        return cls(
            stage_id=sid,
            candidate_id=cid,
            source=str(data.get("source") or "api"),
            added_at=str(data.get("added_at") or _now_iso()),
            promoted_run_id=str(promoted_run_id) if promoted_run_id else None,
            promoted_at=str(promoted_at) if promoted_at else None,
        )


@dataclass(frozen=True)
class Shortlist:
    """The raw on-disk list — pointers only, no hydrated KPIs."""
    updated_at: str
    items: tuple[ShortlistItem, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "updated_at": self.updated_at,
            "items": [i.to_dict() for i in self.items],
        }


@dataclass(frozen=True)
class ResolvedItem:
    """A shortlist pointer plus the current state of the row it
    references. ``status`` tells the UI how to handle it:

    * ``ok``       — finalist row resolved against evaluated_candidates
    * ``pending``  — stage row resolved against scored_rows (UI shows
                     "not actionable yet")
    * ``promoted`` — stage row already stamped as a NinjaTrader template
                     via the promotion path; awaiting NT run / parse
    * ``stale``    — pointer didn't resolve; row was deleted / renamed
    """
    stage_id: str
    candidate_id: str
    source: str
    added_at: str
    status: str
    is_finalist: bool
    template_id: str | None = None
    optimizer_target: str | None = None
    kpis: dict[str, Any] = field(default_factory=dict)
    links: dict[str, str] = field(default_factory=dict)
    promoted_run_id: str | None = None
    promoted_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage_id": self.stage_id,
            "candidate_id": self.candidate_id,
            "source": self.source,
            "added_at": self.added_at,
            "status": self.status,
            "is_finalist": self.is_finalist,
            "template_id": self.template_id,
            "optimizer_target": self.optimizer_target,
            "kpis": dict(self.kpis),
            "links": dict(self.links),
            "promoted_run_id": self.promoted_run_id,
            "promoted_at": self.promoted_at,
        }


@dataclass(frozen=True)
class ResolvedShortlist:
    session_id: str
    updated_at: str
    items: tuple[ResolvedItem, ...] = ()

    @property
    def count(self) -> int:
        return len(self.items)

    @property
    def finalist_candidate_ids(self) -> list[str]:
        return [i.candidate_id for i in self.items if i.is_finalist and i.status != ITEM_STATUS_STALE]

    @property
    def pending_count(self) -> int:
        return sum(1 for i in self.items if i.status == ITEM_STATUS_PENDING)

    @property
    def stale_count(self) -> int:
        return sum(1 for i in self.items if i.status == ITEM_STATUS_STALE)

    @property
    def promoted_count(self) -> int:
        return sum(1 for i in self.items if i.status == ITEM_STATUS_PROMOTED)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "updated_at": self.updated_at,
            "count": self.count,
            "pending_count": self.pending_count,
            "stale_count": self.stale_count,
            "promoted_count": self.promoted_count,
            "finalist_candidate_ids": self.finalist_candidate_ids,
            "items": [i.to_dict() for i in self.items],
        }


# ---------------------------------------------------------------------------
# Public API — load / mutate
# ---------------------------------------------------------------------------

def load_shortlist(session: OptimizerSession) -> Shortlist:
    """Read the raw pointer list. Missing file → empty shortlist."""
    path = _path(session)
    data = _read_json(path)
    if not isinstance(data, dict):
        return Shortlist(updated_at=_now_iso(), items=())
    raw_items = data.get("items") or []
    items: list[ShortlistItem] = []
    seen: set[tuple[str, str]] = set()
    for entry in raw_items:
        if not isinstance(entry, dict):
            continue
        try:
            item = ShortlistItem.from_dict(entry)
        except ShortlistError:
            continue
        if item.key() in seen:
            continue
        seen.add(item.key())
        items.append(item)
    return Shortlist(updated_at=str(data.get("updated_at") or _now_iso()), items=tuple(items))


def add_items(
    session: OptimizerSession,
    items: Iterable[dict[str, Any]],
    *,
    source: str | None = None,
) -> Shortlist:
    """Append items to the shortlist (idempotent on (stage_id, candidate_id)).

    Items already present are kept untouched (original ``added_at`` and
    ``source`` survive); new items get ``source`` as supplied, or the
    per-item ``source`` field if the caller set one.
    """
    new_items = list(items or [])
    if not new_items:
        return load_shortlist(session)

    current = load_shortlist(session)
    existing: dict[tuple[str, str], ShortlistItem] = {
        i.key(): i for i in current.items
    }
    ordered_keys = [i.key() for i in current.items]

    for raw in new_items:
        if not isinstance(raw, dict):
            continue
        payload = dict(raw)
        payload.setdefault("source", source or "api")
        try:
            item = ShortlistItem.from_dict(payload)
        except ShortlistError:
            continue
        if item.key() in existing:
            continue
        existing[item.key()] = item
        ordered_keys.append(item.key())

    next_shortlist = Shortlist(
        updated_at=_now_iso(),
        items=tuple(existing[k] for k in ordered_keys),
    )
    _write(session, next_shortlist)
    return next_shortlist


def remove_item(
    session: OptimizerSession, *, stage_id: str, candidate_id: str
) -> Shortlist:
    sid = (stage_id or "").strip()
    cid = (candidate_id or "").strip()
    if not sid or not cid:
        raise ShortlistError("stage_id and candidate_id are required")
    current = load_shortlist(session)
    filtered = tuple(i for i in current.items if i.key() != (sid, cid))
    next_shortlist = Shortlist(updated_at=_now_iso(), items=filtered)
    _write(session, next_shortlist)
    return next_shortlist


def clear(session: OptimizerSession) -> Shortlist:
    next_shortlist = Shortlist(updated_at=_now_iso(), items=())
    _write(session, next_shortlist)
    return next_shortlist


def mark_promoted(
    session: OptimizerSession,
    *,
    stage_id: str,
    candidate_id: str,
    promoted_run_id: str,
    promoted_at: str | None = None,
) -> Shortlist:
    """Stamp ``promoted_run_id`` / ``promoted_at`` on a single shortlist item.

    Used by the promotion path after a P_NNN template lands on disk so the
    shortlist sidebar can flip the item from "pending" to "promoted" and
    re-clicking the Promote button skips it.
    """
    sid = (stage_id or "").strip()
    cid = (candidate_id or "").strip()
    run_id = (promoted_run_id or "").strip()
    if not sid or not cid:
        raise ShortlistError("stage_id and candidate_id are required")
    if not run_id:
        raise ShortlistError("promoted_run_id is required")
    when = (promoted_at or "").strip() or _now_iso()
    current = load_shortlist(session)
    updated: list[ShortlistItem] = []
    for entry in current.items:
        if entry.key() == (sid, cid):
            updated.append(
                ShortlistItem(
                    stage_id=entry.stage_id,
                    candidate_id=entry.candidate_id,
                    source=entry.source,
                    added_at=entry.added_at,
                    promoted_run_id=run_id,
                    promoted_at=when,
                )
            )
        else:
            updated.append(entry)
    next_shortlist = Shortlist(updated_at=_now_iso(), items=tuple(updated))
    _write(session, next_shortlist)
    return next_shortlist


# ---------------------------------------------------------------------------
# Resolution — hydrate pointers with current row state
# ---------------------------------------------------------------------------

def resolve_shortlist(session: OptimizerSession) -> ResolvedShortlist:
    raw = load_shortlist(session)
    if not raw.items:
        return ResolvedShortlist(session_id=session.id, updated_at=raw.updated_at, items=())

    # Bulk-load finalist + stage rows once, then resolve each pointer.
    finalists = _evaluated_by_run_id(session)
    stage_cache: dict[str, dict[str, dict[str, Any]]] = {}

    resolved: list[ResolvedItem] = []
    for item in raw.items:
        if item.stage_id == FINAL_BACKTEST_STAGE_ID:
            resolved.append(_resolve_finalist(session, item, finalists))
        else:
            resolved.append(_resolve_stage_row(session, item, stage_cache))

    return ResolvedShortlist(
        session_id=session.id,
        updated_at=raw.updated_at,
        items=tuple(resolved),
    )


def _resolve_finalist(
    session: OptimizerSession,
    item: ShortlistItem,
    finalists: dict[str, dict[str, Any]],
) -> ResolvedItem:
    row = finalists.get(item.candidate_id)
    if row is None:
        return ResolvedItem(
            stage_id=item.stage_id,
            candidate_id=item.candidate_id,
            source=item.source,
            added_at=item.added_at,
            status=ITEM_STATUS_STALE,
            is_finalist=True,
        )
    kpis = {
        "profit_factor": _safe_float(row.get("profit_factor")),
        "total_net_profit": _safe_float(row.get("total_net_profit")),
        "max_drawdown": _safe_float(row.get("max_drawdown")),
        "trades": _safe_int(row.get("trades")),
    }
    return ResolvedItem(
        stage_id=item.stage_id,
        candidate_id=item.candidate_id,
        source=item.source,
        added_at=item.added_at,
        status=ITEM_STATUS_OK,
        is_finalist=True,
        template_id=_str_or_none(row.get("template_id")),
        optimizer_target=_str_or_none(
            row.get("optimization_target") or row.get("optimizer_target")
        ),
        kpis=kpis,
        links={
            "report_url": f"/optimizer/sessions/{session.id}/candidates/{item.candidate_id}/report",
            "lineage_url": f"/optimizer/sessions/{session.id}/lineage/{item.candidate_id}",
        },
    )


def _resolve_stage_row(
    session: OptimizerSession,
    item: ShortlistItem,
    cache: dict[str, dict[str, dict[str, Any]]],
) -> ResolvedItem:
    by_cid = cache.get(item.stage_id)
    if by_cid is None:
        by_cid = _stage_rows_by_candidate(session, item.stage_id)
        cache[item.stage_id] = by_cid
    row = by_cid.get(item.candidate_id)
    if row is None:
        return ResolvedItem(
            stage_id=item.stage_id,
            candidate_id=item.candidate_id,
            source=item.source,
            added_at=item.added_at,
            status=ITEM_STATUS_STALE,
            is_finalist=False,
        )
    kpis = {
        "profit_factor": _safe_float(row.get("profit_factor")),
        "total_net_profit": _safe_float(row.get("total_net_profit") or row.get("net_profit")),
        "max_drawdown": _safe_float(row.get("max_drawdown") or row.get("drawdown_abs")),
        "trades": _safe_int(row.get("trades") or row.get("total_trades")),
    }
    # A stage row that has already been promoted (P_NNN template stamped)
    # reports as PROMOTED so the UI can show it as "awaiting NT run /
    # parse" rather than the action-required PENDING badge. The promoted
    # template path is included in links so the operator can jump to the
    # XML on disk.
    status = ITEM_STATUS_PROMOTED if item.promoted_run_id else ITEM_STATUS_PENDING
    links: dict[str, str] = {
        "stage_results_url": (
            f"/optimizer/sessions/{session.id}/resume?"
            f"focus_stage={item.stage_id}&focus_tab=results"
        ),
    }
    if item.promoted_run_id:
        links["promoted_template_url"] = (
            f"/optimizer/sessions/{session.id}/generated/promoted/"
            f"{item.promoted_run_id}.xml"
        )
    return ResolvedItem(
        stage_id=item.stage_id,
        candidate_id=item.candidate_id,
        source=item.source,
        added_at=item.added_at,
        status=status,
        is_finalist=False,
        template_id=_str_or_none(row.get("template_id")),
        optimizer_target=_str_or_none(row.get("optimizer_target")),
        kpis=kpis,
        links=links,
        promoted_run_id=item.promoted_run_id,
        promoted_at=item.promoted_at,
    )


# ---------------------------------------------------------------------------
# On-disk helpers
# ---------------------------------------------------------------------------

def _path(session: OptimizerSession) -> Path:
    return session.directory / SHORTLIST_FILENAME


def _write(session: OptimizerSession, shortlist: Shortlist) -> None:
    path = _path(session)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".tmp.", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(shortlist.to_dict(), handle, indent=2, ensure_ascii=False)
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _read_json(path: Path) -> Any:
    try:
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _evaluated_by_run_id(session: OptimizerSession) -> dict[str, dict[str, Any]]:
    path = (
        session.directory
        / "deployment_package"
        / "final_backtest_handoff"
        / "final_backtest_review"
        / "evaluated_candidates.json"
    )
    payload = _read_json(path)
    if not isinstance(payload, dict):
        return {}
    rows = payload.get("rows") or []
    if not isinstance(rows, list):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        if isinstance(row, dict) and row.get("run_id"):
            out[str(row["run_id"])] = row
    return out


def _stage_rows_by_candidate(
    session: OptimizerSession, stage_id: str
) -> dict[str, dict[str, Any]]:
    path = session.directory / "parsed_results" / stage_id / "scored_rows.json"
    payload = _read_json(path)
    if not isinstance(payload, list):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for row in payload:
        if isinstance(row, dict) and row.get("candidate_id"):
            out[str(row["candidate_id"])] = row
    return out


# ---------------------------------------------------------------------------
# Primitives
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f:
        return None
    return f


def _safe_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None
