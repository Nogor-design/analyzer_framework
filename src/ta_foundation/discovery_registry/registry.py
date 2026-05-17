"""JSON-backed registries for probe history and graveyard verdicts.

Two registries live alongside discovery output:

- `_probe_registry.json` — append-only ledger of every probe run. Used to
  compute the project-cumulative hypothesis counter for cross-probe
  Bonferroni correction.
- `_graveyard_registry.json` — every probe or hardening that produced a
  rejection-only result (broad PF<1.0 across all rows, or
  hardening_passed=False). Used by the pre-run refusal check.

Both files are simple JSON with a "version" field and a "records" list.
Append-only at the record level; idempotent on the hash key.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .hashing import ProbeIdentity, jaccard_overlap, probe_hash

PROBE_REGISTRY_FILENAME = "_probe_registry.json"
GRAVEYARD_REGISTRY_FILENAME = "_graveyard_registry.json"
REGISTRY_VERSION = 1

# Near-match thresholds for the graveyard refusal check.
DEFAULT_PARAM_OVERLAP_THRESHOLD = 0.80
DEFAULT_OUTCOME_OVERLAP_THRESHOLD = 0.80


@dataclass
class ProbeRecord:
    hash: str
    yaml_path: str
    sidecar_path: Optional[str]
    run_date: str            # ISO8601 UTC
    instrument: str
    stage: Optional[str]
    families: List[str]      # e.g. ["level_discovery::large_candle_origin_retest"]
    n_combinations_run: int

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class GraveyardRecord:
    hash: str
    yaml_path: str
    sidecar_path: Optional[str]
    verdict_date: str        # ISO8601 UTC
    reason: str              # short human-readable verdict ("broad PF<1.0", "t-test failed", "slippage stress failed", etc.)
    families: List[str]
    instrument: str
    stress_failure_cell: Optional[Dict[str, Any]] = None
    override_history: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RegistryHit:
    """Result of a graveyard near-match check."""
    matched_record: GraveyardRecord
    match_kind: str          # "exact_hash" | "near_match"
    param_overlap: float     # 0.0..1.0
    outcome_overlap: float   # 0.0..1.0
    proposed_hash: str

    def explain(self) -> str:
        if self.match_kind == "exact_hash":
            return (
                f"Exact probe-identity hash match against graveyard entry "
                f"{self.matched_record.yaml_path} "
                f"(verdict {self.matched_record.verdict_date}, reason: "
                f"{self.matched_record.reason})."
            )
        return (
            f"Near-match against graveyard entry {self.matched_record.yaml_path}: "
            f"param overlap={self.param_overlap:.2f}, outcome overlap={self.outcome_overlap:.2f} "
            f"(verdict {self.matched_record.verdict_date}, reason: "
            f"{self.matched_record.reason})."
        )


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _families_from_identity(identity: ProbeIdentity) -> List[str]:
    return [f"{s.block}::{s.signal}" for s in identity.signals]


def _load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"version": REGISTRY_VERSION, "records": []}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or "records" not in data:
            return {"version": REGISTRY_VERSION, "records": []}
        return data
    except (json.JSONDecodeError, OSError):
        return {"version": REGISTRY_VERSION, "records": []}


def _save_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)
    tmp.replace(path)


class ProbeRegistry:
    """Append-only ledger of probe runs."""

    def __init__(self, output_dir: Path):
        self.output_dir = Path(output_dir)
        self.path = self.output_dir / PROBE_REGISTRY_FILENAME
        self._data = _load_json(self.path)

    def records(self) -> List[ProbeRecord]:
        return [ProbeRecord(**r) for r in self._data.get("records", []) if isinstance(r, dict)]

    def cumulative_hypotheses(
        self,
        family_filter: Optional[List[str]] = None,
    ) -> int:
        """Sum of n_combinations_run, optionally filtered to a family list.

        family_filter matches any family token in a probe record's `families`
        list. None means count every probe.
        """
        total = 0
        for r in self.records():
            if family_filter is not None:
                if not any(f in family_filter for f in r.families):
                    continue
            total += max(int(r.n_combinations_run), 0)
        return total

    def append(self, record: ProbeRecord) -> bool:
        """Append; idempotent on (hash, yaml_path) pair.

        Returns True if a new row was added, False if it already existed.
        """
        existing = self._data.setdefault("records", [])
        for r in existing:
            if isinstance(r, dict) and r.get("hash") == record.hash and r.get("yaml_path") == record.yaml_path:
                return False
        existing.append(record.to_dict())
        self._data["version"] = REGISTRY_VERSION
        _save_json(self.path, self._data)
        return True


class GraveyardRegistry:
    """Append-only ledger of graveyarded probes / hardenings."""

    def __init__(self, output_dir: Path):
        self.output_dir = Path(output_dir)
        self.path = self.output_dir / GRAVEYARD_REGISTRY_FILENAME
        self._data = _load_json(self.path)

    def records(self) -> List[GraveyardRecord]:
        out: List[GraveyardRecord] = []
        for r in self._data.get("records", []):
            if not isinstance(r, dict):
                continue
            try:
                out.append(GraveyardRecord(**r))
            except TypeError:
                # Tolerate older records missing newer fields.
                base = {k: r.get(k) for k in (
                    "hash", "yaml_path", "sidecar_path", "verdict_date",
                    "reason", "families", "instrument",
                )}
                base["stress_failure_cell"] = r.get("stress_failure_cell")
                base["override_history"] = r.get("override_history", [])
                out.append(GraveyardRecord(**base))
        return out

    def find_by_hash(self, h: str) -> Optional[GraveyardRecord]:
        for r in self.records():
            if r.hash == h:
                return r
        return None

    def append(self, record: GraveyardRecord) -> bool:
        existing = self._data.setdefault("records", [])
        for r in existing:
            if isinstance(r, dict) and r.get("hash") == record.hash:
                return False
        existing.append(record.to_dict())
        self._data["version"] = REGISTRY_VERSION
        _save_json(self.path, self._data)
        return True

    def record_override(self, h: str, reason: str, yaml_path: str) -> None:
        """Annotate a graveyard entry with an operator override."""
        records = self._data.setdefault("records", [])
        for r in records:
            if isinstance(r, dict) and r.get("hash") == h:
                history = r.setdefault("override_history", [])
                history.append({
                    "date": _utcnow_iso(),
                    "reason": reason,
                    "yaml_path": yaml_path,
                })
                _save_json(self.path, self._data)
                return


def check_graveyard(
    identity: ProbeIdentity,
    graveyard: GraveyardRegistry,
    *,
    param_overlap_threshold: float = DEFAULT_PARAM_OVERLAP_THRESHOLD,
    outcome_overlap_threshold: float = DEFAULT_OUTCOME_OVERLAP_THRESHOLD,
    identity_resolver=None,
) -> Optional[RegistryHit]:
    """Look for an exact or near-match in the graveyard.

    Near-match definition:
        same family set (block::signal pairs) AND
        same outcome mode AND
        Jaccard(take_profit) >= outcome_overlap_threshold AND
        Jaccard(stop) >= outcome_overlap_threshold AND
        for every shared signal, mean Jaccard(param_ranges) >= param_overlap_threshold

    `identity_resolver` is an optional callable `yaml_path -> ProbeIdentity`
    used to re-derive the graveyard entry's identity for fine-grained
    Jaccard comparison. When None, only exact-hash matches are returned
    (degraded mode — the registry still works but near-match is disabled).
    """
    h = probe_hash(identity)
    direct = graveyard.find_by_hash(h)
    if direct is not None:
        return RegistryHit(
            matched_record=direct,
            match_kind="exact_hash",
            param_overlap=1.0,
            outcome_overlap=1.0,
            proposed_hash=h,
        )
    if identity_resolver is None:
        return None

    proposed_families = set(_families_from_identity(identity))
    if not proposed_families:
        return None

    for record in graveyard.records():
        if set(record.families) != proposed_families:
            continue
        try:
            other = identity_resolver(record.yaml_path)
        except Exception:
            continue
        if other is None or other.outcome_mode != identity.outcome_mode:
            continue
        tp_overlap = jaccard_overlap(identity.outcome_take_profit, other.outcome_take_profit)
        sl_overlap = jaccard_overlap(identity.outcome_stop, other.outcome_stop)
        outcome_overlap = min(tp_overlap, sl_overlap)
        if outcome_overlap < outcome_overlap_threshold:
            continue
        # Param overlap per signal.
        signal_lookup_other = {(s.block, s.signal): s for s in other.signals}
        overlaps: List[float] = []
        for s in identity.signals:
            other_sig = signal_lookup_other.get((s.block, s.signal))
            if other_sig is None:
                overlaps.append(0.0)
                continue
            shared = set(s.param_ranges) & set(other_sig.param_ranges)
            if not shared:
                overlaps.append(0.0)
                continue
            per_param = [
                jaccard_overlap(s.param_ranges[k], other_sig.param_ranges[k])
                for k in shared
            ]
            overlaps.append(sum(per_param) / len(per_param) if per_param else 0.0)
        mean_overlap = sum(overlaps) / len(overlaps) if overlaps else 0.0
        if mean_overlap >= param_overlap_threshold:
            return RegistryHit(
                matched_record=record,
                match_kind="near_match",
                param_overlap=mean_overlap,
                outcome_overlap=outcome_overlap,
                proposed_hash=h,
            )
    return None
