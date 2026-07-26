from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


def export_strategy_blueprints_json(
    packages: Dict[str, Any],
    output_path: Path,
    title: str = "Large Candle Excursion Strategy Blueprints",
) -> bool:
    """Write a sidecar JSON file containing every strategy blueprint emitted
    by the findings pipeline.  Returns True if any blueprints were written.
    """
    payload = _collect_blueprints(packages)
    if not payload or not payload.get("blueprints"):
        return False
    doc = {
        "schema": "ta_foundation.strategy_blueprint.v1",
        "title": title,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        **payload,
    }
    output_path.write_text(json.dumps(doc, indent=2, default=_default), encoding="utf-8")
    return True


def _default(v: Any) -> Any:
    try:
        import numpy as np  # type: ignore
        if isinstance(v, (np.integer,)):
            return int(v)
        if isinstance(v, (np.floating,)):
            return float(v)
        if isinstance(v, (np.bool_,)):
            return bool(v)
    except Exception:
        pass
    if isinstance(v, datetime):
        return v.isoformat()
    return str(v)


def _collect_blueprints(packages: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    all_blueprints: List[Dict[str, Any]] = []
    diagnostics: Dict[str, Any] = {}
    for pkg in (packages or {}).values():
        derived = (getattr(pkg, "metadata", {}) or {}).get("derived", {}) or {}
        findings = derived.get("large_candle_excursion_findings") or {}
        exporter = findings.get("strategy_blueprint_exporter") or {}
        bps = exporter.get("blueprints") or []
        if bps:
            all_blueprints.extend(bps)
            diagnostics.setdefault("per_package", []).append({
                "n_blueprints": len(bps),
                "diagnostics": exporter.get("diagnostics") or {},
            })
    if not all_blueprints:
        return None
    return {
        "blueprints": all_blueprints,
        "diagnostics": diagnostics,
    }
