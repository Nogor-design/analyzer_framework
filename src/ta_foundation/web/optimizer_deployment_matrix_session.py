from __future__ import annotations

"""Build a 252-cell deployment manifest from a completed optimizer session.

Joins the session's final-backtest templates (their pinned params) with the
final metrics, classifies each into its cell, and names it with the canonical
``template_naming`` package (computed fresh, so it always reflects the current
naming rules). Empty cells are filled best-effort. This is the per-session
coverage view the daily-prediction tool ultimately consumes.
"""

import json
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from ta_foundation.web.optimizer_deployment_matrix import load_naming_rules
from ta_foundation.web.optimizer_deployment_matrix_manifest import (
    apply_best_effort_fallback,
    build_deployment_manifest,
)

_BOOL_TAGS = {"Reverse", "Long", "Short"}
_FLOAT_TAGS = {"averageFast", "averageSlow", "MaxStop", "MaxTPRatio", "MaxTrades", "ProfitStop", "LossStop"}
_INT_TAGS = {"StartTimeH", "StartTimeM", "DurationTimeH", "DurationTimeM"}
_FACT_TAGS = _BOOL_TAGS | _FLOAT_TAGS | _INT_TAGS


def _find(root: ET.Element, tag: str) -> ET.Element | None:
    for el in root.iter():
        if el.tag.rsplit("}", 1)[-1] == tag:
            return el
    return None


def _facts_from_template_xml(path: str | Path) -> dict[str, Any]:
    """Read the pinned strategy params from a final template XML into a row dict
    (native tag names; the manifest classifiers accept these directly)."""
    root = ET.fromstring(Path(path).read_text(encoding="utf-8"))
    out: dict[str, Any] = {}
    for tag in _FACT_TAGS:
        el = _find(root, tag)
        if el is None or el.text is None:
            continue
        text = el.text.strip()
        if tag in _BOOL_TAGS:
            out[tag] = text.lower() == "true"
        elif tag in _FLOAT_TAGS:
            out[tag] = float(text)
        else:
            out[tag] = int(text)
    return out


def _canonical_name(path: str | Path, market_suffix: str) -> str | None:
    """The current canonical name for a template, computed fresh from its XML so
    it reflects the live naming rules.

    Routed through ``analyze_template_any``, which prefers the external
    ``template_naming`` package and falls back to the in-repo decoder. Importing
    ``template_naming.core`` directly meant that on any checkout without that
    optional package every template silently kept its stored ``semantic_name``
    -- often a bare filename -- instead of a real deployment name.
    """
    try:
        from ta_foundation.web.optimizer_template_naming_fallback import (
            analyze_template_any,
        )

        name = analyze_template_any(path).compact_name
    except Exception:
        return None
    if not name:
        return None
    return f"{name}-{market_suffix}" if market_suffix else name


def session_final_rows(session) -> list[dict[str, Any]]:
    """Assemble manifest input rows from a session's final-backtest outputs."""
    base = Path(session.directory) / "deployment_package" / "final_backtest_handoff"
    idx_path = base / "renamed_backtest_templates" / "renamed_template_index.json"
    eval_path = base / "final_backtest_review" / "evaluated_candidates.json"

    market = ""
    try:
        market = str(getattr(session.load_document(), "market_suffix", "") or "")
    except Exception:
        market = ""

    metrics_by_id: dict[str, dict[str, Any]] = {}
    if eval_path.exists():
        ev = json.loads(eval_path.read_text(encoding="utf-8"))
        rows = ev.get("rows") if isinstance(ev, dict) else ev
        for r in rows or []:
            run_id = r.get("run_id")
            if run_id:
                metrics_by_id[run_id] = r

    out: list[dict[str, Any]] = []
    if not idx_path.exists():
        return out
    index = json.loads(idx_path.read_text(encoding="utf-8"))
    market = market or str(index.get("market") or "")
    for run_id, meta in (index.get("templates") or {}).items():
        path = meta.get("renamed_path") or meta.get("source_path")
        if not path or not Path(path).exists():
            continue
        row = _facts_from_template_xml(path)
        metrics = metrics_by_id.get(run_id, {})
        row["run_id"] = run_id
        row["template_name"] = _canonical_name(path, market) or meta.get("semantic_name")
        row["template_path"] = path
        row["profit_factor"] = metrics.get("profit_factor")
        row["total_net_profit"] = metrics.get("total_net_profit")
        row["trades"] = metrics.get("trades")
        out.append(row)
    return out


def build_session_deployment_manifest(
    session, rules: dict | None = None, *, with_features: bool = False
) -> dict[str, Any]:
    """Full per-session manifest: classify final templates into the 252 grid, keep
    the best per cell, and fill empty cells best-effort.

    ``with_features`` is **opt-in** and expensive: it loads the tick store and runs
    the exit-policy simulator per covered cell (and pulls in matplotlib via the
    report's policy set). Leave it off for the fast coverage heatmap; turn it on
    only for the predictor-facing manifest export.
    """
    rules = rules or load_naming_rules()
    rows = session_final_rows(session)
    manifest = build_deployment_manifest(rows, rules)
    manifest = apply_best_effort_fallback(manifest, rules)

    if not with_features:
        return manifest

    try:
        from ta_foundation.web.optimizer_template_quality_features import (
            load_market_for_session,
            template_quality_features,
        )

        market = load_market_for_session(session)
        for cell in manifest.get("cells", []):
            if cell.get("status") != "covered":
                continue
            try:
                features = template_quality_features(session, cell, market=market)
            except Exception:
                features = {}
            if features:
                cell["features"] = features
    except Exception:
        pass
    return manifest
