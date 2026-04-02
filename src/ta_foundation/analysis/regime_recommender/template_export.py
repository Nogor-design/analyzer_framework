from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Any, Dict
import xml.etree.ElementTree as ET

import pandas as pd


DEFAULT_SESSION_WINDOWS: Dict[str, Dict[str, str]] = {
    "london": {"start": "01:00", "duration": "03:00"},
    "ny_early": {"start": "07:30", "duration": "02:30"},
    "ny_midday": {"start": "10:00", "duration": "02:00"},
    "power_hour": {"start": "13:00", "duration": "01:00"},
    "asia": {"start": "18:00", "duration": "04:00"},
}


def _parse_hhmm(s: str) -> tuple[int, int]:
    hh, mm = (s or "00:00").split(":", 1)
    return int(hh), int(mm)


def _find_strategy_node(root: ET.Element) -> ET.Element:
    strategy_container = root.find("./Strategy")
    if strategy_container is None:
        raise ValueError("invalid_template:missing Strategy node")
    for child in strategy_container:
        return child
    raise ValueError("invalid_template:missing strategy payload node")


def _set_text(strategy_node: ET.Element, key: str, value: Any) -> None:
    node = strategy_node.find(f"./{key}")
    if node is None:
        return
    if isinstance(value, bool):
        node.text = "true" if value else "false"
    else:
        node.text = str(value)


def generate_session_templates(
    strategy_id: str,
    regime_id: str,
    recommended_params: Dict[str, Any],
    base_template_path: str,
    output_dir: str,
    session_windows: Dict[str, Dict[str, str]] | None = None,
) -> Dict[str, Any]:
    session_windows = session_windows or DEFAULT_SESSION_WINDOWS

    template_path = Path(base_template_path)
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    manifest: Dict[str, Any] = {
        "strategy_id": strategy_id,
        "regime_id": regime_id,
        "generated_at": pd.Timestamp.now(tz="America/Denver").isoformat(),
        "templates": [],
    }

    for session_key, win in session_windows.items():
        start_h, start_m = _parse_hhmm(win.get("start", "00:00"))
        dur_h, dur_m = _parse_hhmm(win.get("duration", "01:00"))

        tree = ET.parse(template_path)
        root = tree.getroot()
        strategy_node = _find_strategy_node(root)

        merged_params = dict(recommended_params)
        merged_params.update(
            {
                "UseTimeFilter": True,
                "StartTimeH": start_h,
                "StartTimeM": start_m,
                "DurationTimeH": dur_h,
                "DurationTimeM": dur_m,
            }
        )

        for key, value in merged_params.items():
            _set_text(strategy_node, key, value)

        out_name = f"{strategy_id}__{regime_id}__{session_key}.xml"
        out_path = out_dir / out_name
        tree.write(out_path, encoding="utf-8", xml_declaration=True)

        digest = sha256(out_path.read_bytes()).hexdigest()[:16]
        manifest["templates"].append(
            {
                "session": session_key,
                "path": str(out_path),
                "start_time": f"{start_h:02d}:{start_m:02d}",
                "duration": f"{dur_h:02d}:{dur_m:02d}",
                "source_template": str(template_path),
                "params_hash": digest,
            }
        )

    return manifest
