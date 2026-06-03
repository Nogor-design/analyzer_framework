from __future__ import annotations

"""Category-bundle review for a finished session's final templates.

The weekly coverage package dedups *within* each time/slowMA lane. This module
takes the whole final bundle (validated + best-effort fallback templates, plus
any later refinements that landed in the same selection files) and clusters them
into behavioural *categories* — templates that trade nearly the same pattern,
banded by direction, stop, target, slow-MA, time bucket, and the session risk
knobs (ProfitStop / LossStop / MaxTrades).

Within a category one representative is recommended to keep (best score); the
operator ticks which to keep or drop in the UI, then builds a pruned bundle.

Pure file IO: reads the selection CSVs the weekly package already wrote and
copies template XML files. No NinjaTrader, no analysis recompute.
"""

import csv
import shutil
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ta_foundation.web.optimizer_session import OptimizerSession


PACKAGE_DIRNAME = "weekly_coverage_package"
PRUNED_DIRNAME = "pruned_final_bundle"
PRUNED_TEMPLATES_DIRNAME = "templates"
PRUNED_ZIP_FILENAME = "pruned_final_bundle.zip"

VALIDATED_CSV = "operationally_diverse_validated_selection.csv"
FALLBACK_CSV = "best_effort_fallback_selection.csv"

# Numeric knobs snap to these bands before forming a category key. Two templates
# that differ only within a band trade the same pattern for review purposes.
DEFAULT_BANDS = {
    "max_stop": 50.0,
    "max_tp_ratio": 0.5,
    "profit_stop": 500.0,
    "loss_stop": 500.0,
    "max_trades": 2.0,
}


class CategoryBundleError(Exception):
    pass


@dataclass(frozen=True)
class CategoryBundleConfig:
    bands: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_BANDS))
    package_dirname: str = PACKAGE_DIRNAME

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None = None) -> "CategoryBundleConfig":
        payload = payload or {}
        bands = dict(DEFAULT_BANDS)
        for key, value in (payload.get("bands") or {}).items():
            if key in bands:
                try:
                    bands[key] = float(value)
                except (TypeError, ValueError):
                    pass
        return cls(bands=bands, package_dirname=str(payload.get("package_dirname") or PACKAGE_DIRNAME))


@dataclass
class BundleMember:
    run_id: str
    template_name: str
    source: str               # "validated" | "fallback"
    bucket: str
    side: str
    slow_ma: str
    direction_shape: str
    max_stop: str
    max_tp_ratio: str
    profit_stop: str
    loss_stop: str
    max_trades: str
    net_profit: float | None
    profit_factor: float | None
    trades: int | None
    percent_days_traded: float | None
    score: float | None
    template_path: str
    recommended_keep: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BundleCategory:
    category_id: str
    label: str
    member_count: int
    members: list[BundleMember]

    def to_dict(self) -> dict[str, Any]:
        return {
            "category_id": self.category_id,
            "label": self.label,
            "member_count": self.member_count,
            "members": [m.to_dict() for m in self.members],
        }


@dataclass
class CategoryBundleView:
    session_id: str
    total_templates: int
    category_count: int
    duplicate_count: int          # members beyond the first in each category
    recommended_keep_count: int
    categories: list[BundleCategory]
    status: str = "ok"

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "total_templates": self.total_templates,
            "category_count": self.category_count,
            "duplicate_count": self.duplicate_count,
            "recommended_keep_count": self.recommended_keep_count,
            "status": self.status,
            "categories": [c.to_dict() for c in self.categories],
        }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_category_bundle(
    session: OptimizerSession,
    config: CategoryBundleConfig | None = None,
) -> CategoryBundleView:
    cfg = config or CategoryBundleConfig()
    data_dir = session.directory / "deployment_package" / cfg.package_dirname / "data"
    if not data_dir.exists():
        return CategoryBundleView(
            session_id=session.id, total_templates=0, category_count=0,
            duplicate_count=0, recommended_keep_count=0, categories=[],
            status="no_weekly_package",
        )

    members = _load_members(data_dir / VALIDATED_CSV, "validated")
    members += _load_members(data_dir / FALLBACK_CSV, "fallback")
    # De-dupe by run_id in case a run appears in both files.
    seen: set[str] = set()
    unique: list[BundleMember] = []
    for member in members:
        if member.run_id in seen:
            continue
        seen.add(member.run_id)
        unique.append(member)

    if not unique:
        return CategoryBundleView(
            session_id=session.id, total_templates=0, category_count=0,
            duplicate_count=0, recommended_keep_count=0, categories=[],
            status="no_templates",
        )

    groups: dict[tuple[str, ...], list[BundleMember]] = {}
    for member in unique:
        groups.setdefault(_category_key(member, cfg), []).append(member)

    categories: list[BundleCategory] = []
    duplicate_count = 0
    recommended = 0
    for key, group in groups.items():
        group.sort(key=_member_rank, reverse=True)
        group[0].recommended_keep = True
        recommended += 1
        duplicate_count += len(group) - 1
        categories.append(BundleCategory(
            category_id="__".join(key),
            label=_category_label(group[0]),
            member_count=len(group),
            members=group,
        ))

    # Clusters with duplicates first (those are where pruning matters), then by P&L.
    categories.sort(key=lambda c: (c.member_count, _safe_float(c.members[0].net_profit, 0.0)), reverse=True)

    return CategoryBundleView(
        session_id=session.id,
        total_templates=len(unique),
        category_count=len(categories),
        duplicate_count=duplicate_count,
        recommended_keep_count=recommended,
        categories=categories,
    )


def build_pruned_bundle(
    session: OptimizerSession,
    keep_run_ids: list[str],
    config: CategoryBundleConfig | None = None,
) -> dict[str, Any]:
    cfg = config or CategoryBundleConfig()
    view = compute_category_bundle(session, cfg)
    keep = {str(r).strip() for r in keep_run_ids if str(r).strip()}
    if not keep:
        raise CategoryBundleError("No templates selected to keep.")

    by_run = {m.run_id: m for c in view.categories for m in c.members}
    root = session.directory / "deployment_package" / cfg.package_dirname / PRUNED_DIRNAME
    templates_dir = root / PRUNED_TEMPLATES_DIRNAME
    if root.exists():
        shutil.rmtree(root)
    templates_dir.mkdir(parents=True, exist_ok=True)

    manifest: list[dict[str, Any]] = []
    copied = 0
    missing: list[str] = []
    for run_id in sorted(keep):
        member = by_run.get(run_id)
        if member is None:
            continue
        src = _resolve_template_path(session, member, cfg)
        if src is None:
            missing.append(run_id)
        else:
            shutil.copy2(src, templates_dir / src.name)
            copied += 1
        manifest.append({
            "run_id": member.run_id,
            "template_name": member.template_name,
            "source": member.source,
            "bucket": member.bucket,
            "side": member.side,
            "slowMA": member.slow_ma,
            "direction_shape": member.direction_shape,
            "max_stop": member.max_stop,
            "max_tp_ratio": member.max_tp_ratio,
            "profit_stop": member.profit_stop,
            "loss_stop": member.loss_stop,
            "max_trades": member.max_trades,
            "net_profit": member.net_profit,
            "profit_factor": member.profit_factor,
            "trades": member.trades,
            "template_copied": src is not None,
        })

    _write_csv(root / "pruned_bundle_manifest.csv", manifest)
    zip_path = root.parent / PRUNED_ZIP_FILENAME
    _write_zip(templates_dir, zip_path)

    return {
        "session_id": session.id,
        "kept": copied,
        "requested": len(keep),
        "dropped": view.total_templates - len(keep),
        "missing_templates": missing,
        "bundle_dir": str(root.resolve()),
        "zip_path": str(zip_path.resolve()),
        "zip_url": f"/optimizer/sessions/{session.id}/category-bundle.zip",
        "manifest_path": str((root / "pruned_bundle_manifest.csv").resolve()),
    }


def pruned_bundle_zip_path(session: OptimizerSession, config: CategoryBundleConfig | None = None) -> Path:
    cfg = config or CategoryBundleConfig()
    return session.directory / "deployment_package" / cfg.package_dirname / PRUNED_ZIP_FILENAME


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _load_members(path: Path, source: str) -> list[BundleMember]:
    if not path.exists():
        return []
    out: list[BundleMember] = []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            out.append(BundleMember(
                run_id=str(row.get("run_id") or ""),
                template_name=str(row.get("template_name") or ""),
                source=source,
                bucket=str(row.get("bucket") or ""),
                side=str(row.get("side") or ""),
                slow_ma=str(row.get("slowMA") or ""),
                direction_shape=str(row.get("direction_shape") or ""),
                max_stop=str(row.get("max_stop") or ""),
                max_tp_ratio=str(row.get("max_tp_ratio") or ""),
                profit_stop=str(row.get("profit_stop") or ""),
                loss_stop=str(row.get("loss_stop") or ""),
                max_trades=str(row.get("max_trades") or ""),
                net_profit=_safe_float(row.get("net_profit"), None),
                profit_factor=_safe_float(row.get("profit_factor"), None),
                trades=_safe_int(row.get("trades"), None),
                percent_days_traded=_safe_float(row.get("percent_days_traded"), None),
                score=_safe_float(row.get("score"), None),
                template_path=str(row.get("template_path") or ""),
            ))
    return out


def _category_key(member: BundleMember, cfg: CategoryBundleConfig) -> tuple[str, ...]:
    return (
        member.bucket,
        member.side,
        member.slow_ma,
        member.direction_shape,
        _band(member.max_stop, cfg.bands["max_stop"]),
        _band(member.max_tp_ratio, cfg.bands["max_tp_ratio"]),
        _band(member.profit_stop, cfg.bands["profit_stop"]),
        _band(member.loss_stop, cfg.bands["loss_stop"]),
        _band(member.max_trades, cfg.bands["max_trades"]),
    )


def _category_label(member: BundleMember) -> str:
    return (
        f"{member.bucket} {member.side} slow={member.slow_ma} "
        f"{member.direction_shape} stop≈{member.max_stop} tp≈{member.max_tp_ratio} "
        f"PS≈{member.profit_stop} LS≈{member.loss_stop} MT≈{member.max_trades}"
    )


def _member_rank(member: BundleMember) -> tuple[float, float, float]:
    return (
        _safe_float(member.score, -1e9),
        _safe_float(member.net_profit, -1e12),
        _safe_float(member.profit_factor, -1e9),
    )


def _resolve_template_path(
    session: OptimizerSession,
    member: BundleMember,
    cfg: CategoryBundleConfig,
) -> Path | None:
    if member.template_path:
        candidate = Path(member.template_path)
        if candidate.exists():
            return candidate
    if not member.template_name:
        return None
    pkg = session.directory / "deployment_package" / cfg.package_dirname
    for sub in (
        "operationally_diverse_validated_named_templates",
        "best_effort_fallback_named_templates",
    ):
        candidate = pkg / sub / member.template_name
        if candidate.exists():
            return candidate
    return None


def _band(value: Any, band: float) -> str:
    raw = _safe_float(value, None) if value not in (None, "") else None
    if raw is None or band is None or band <= 0:
        return str(value).strip()
    return str(int(round(raw / band)))


def _safe_float(value: Any, default: Any) -> Any:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: Any) -> Any:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return default


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames or ["run_id"])
        writer.writeheader()
        writer.writerows(rows)


def _write_zip(folder: Path, zip_path: Path) -> None:
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(folder.parent))
