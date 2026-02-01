from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

import pandas as pd

from pathlib import Path
from ta_foundation.reports.html.embed import file_to_base64_data_uri

from ta_foundation.core.model import AnalysisPackage


# Instrument inference mapping from run_id / bot name tokens (case-insensitive)
TOKEN_TO_SYMBOL = [
    ("gassious", "NG"),
    ("gass", "NG"),
    ("oily", "CL"),
    ("oil", "CL"),
    ("slate", "RTY"),
    ("granit", "YM"),
    ("iron", "ES"),
    ("brass", "MNQ"),
    ("gold", "GC"),
    ("bronze", "NQ"),
]

# Tick values in USD
TICK_VALUE_USD = {
    "NQ": 5.0,
    "MNQ": 0.5,
    "ES": 12.5,
    "RTY": 5.0,
    "CL": 10.0,
    "NG": 10.0,
    "GC": 10.0,
    "YM": 5.0,  # not provided explicitly; add if you want it different
}

def _to_bool(x: Any) -> Optional[bool]:
    if x is None:
        return None
    if isinstance(x, bool):
        return x
    s = str(x).strip().lower()
    if s in {"true", "t", "1", "yes", "y"}:
        return True
    if s in {"false", "f", "0", "no", "n"}:
        return False
    return None

def load_default_images(folder: Path) -> dict[str, str]:
    """
    Load default.png and default_Background.png if present.
    Returns a dict with optional keys:
      - default_image_uri
      - default_background_uri
    """
    out: dict[str, str] = {}

    candidates = {
        "default_image_uri": "default",
        "default_background_uri": "default_Background",
    }

    for key, stem in candidates.items():
        for ext in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
            p = folder / f"{stem}{ext}"
            if p.exists():
                try:
                    out[key] = file_to_base64_data_uri(p)
                except Exception:
                    pass
                break

    return out

def attach_background_image(pkg: AnalysisPackage, folder: Path) -> None:
    """
    Optional background image:
      <run_id>_Background.png|jpg|webp|gif
    """
    for ext in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
        p = folder / f"{pkg.run_id}_Background{ext}"
        if p.exists():
            try:
                uri = file_to_base64_data_uri(p)
                pkg.metadata.setdefault("derived", {})["background_image_uri"] = uri
                pkg.metadata["derived"]["background_image_path"] = str(p)
            except Exception as e:
                pkg.warnings.append({
                    "code": "BACKGROUND_IMAGE_FAILED",
                    "message": f"Failed to embed background image {p.name}: {e}",
                })
            return


def attach_detail_chart_images(pkg: AnalysisPackage, folder: Path) -> None:
    """
    Optional per-run chart images that can be shown under the executive profile card:
      <run_id>_Analysis.(png|jpg|jpeg|webp|gif)
      <run_id>_Summery.(png|...)
      <run_id>_Summary.(png|...)   # fallback spelling

    Stored in pkg.metadata["derived"] as:
      - analysis_image_uri
      - summery_image_uri
    """
    exts = (".png", ".jpg", ".jpeg", ".webp", ".gif")
    derived = pkg.metadata.setdefault("derived", {})

    # Analysis image
    if "analysis_image_uri" not in derived:
        for ext in exts:
            p = folder / f"{pkg.run_id}_Analysis{ext}"
            if p.exists() and p.is_file():
                try:
                    derived["analysis_image_uri"] = file_to_base64_data_uri(p)
                    derived["analysis_image_path"] = str(p)
                    derived["analysis_image_source"] = "run"
                except Exception as e:
                    pkg.warnings.append({
                        "code": "ANALYSIS_IMAGE_FAILED",
                        "message": f"Failed to embed analysis image {p.name}: {e}",
                    })
                break

    # Summery/Summary image (support both spellings)
    if "summery_image_uri" not in derived:
        candidates = [f"{pkg.run_id}_Summery", f"{pkg.run_id}_Summary"]
        for base in candidates:
            for ext in exts:
                p = folder / f"{base}{ext}"
                if p.exists() and p.is_file():
                    try:
                        derived["summery_image_uri"] = file_to_base64_data_uri(p)
                        derived["summery_image_path"] = str(p)
                        derived["summery_image_source"] = "run"
                    except Exception as e:
                        pkg.warnings.append({
                            "code": "SUMMARY_IMAGE_FAILED",
                            "message": f"Failed to embed summary image {p.name}: {e}",
                        })
                    break
            if "summery_image_uri" in derived:
                break

def infer_instrument_from_run_id(run_id: str) -> Tuple[str, float, str]:
    """
    Infer futures symbol and tick value from run_id using your mapping rules.
    Default = NQ.

    Returns: (symbol, tick_value_usd, source)
    """
    rid = (run_id or "").lower()

    for token, sym in TOKEN_TO_SYMBOL:
        if token in rid:
            return sym, float(TICK_VALUE_USD.get(sym, 0.0)), f"token:{token}"

    # Default
    return "NQ", float(TICK_VALUE_USD["NQ"]), "default"


def _settings_map(pkg: AnalysisPackage) -> Dict[str, Any]:
    """
    Build mapping from settings item -> value. Settings DF columns: section, item, value
    """
    df = getattr(pkg, "settings", None)
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return {}
    out: Dict[str, Any] = {}
    for _, r in df.iterrows():
        item = str(r.get("item", "")).strip()
        if not item:
            continue
        out[item.lower().strip()] = r.get("value", "")
    return out


def _to_float(x: Any) -> Optional[float]:
    if x is None or x == "":
        return None
    try:
        # tolerate "$1,500" etc.
        s = str(x).strip().replace("$", "").replace(",", "")
        # tolerate parentheses for negatives
        if s.startswith("(") and s.endswith(")"):
            s = "-" + s[1:-1]
        # tolerate trailing %
        if s.endswith("%"):
            s = s[:-1]
        return float(s)
    except Exception:
        return None


def compute_and_attach_derived_metrics(packages: Dict[str, AnalysisPackage]) -> None:
    """
    Compute derived metrics for every run and store in pkg.metadata["derived"].
    Safe: never raises on missing values; stores None where unavailable.
    """
    for run_id, pkg in (packages or {}).items():
        sm = _settings_map(pkg)

        symbol, tick_value, source = infer_instrument_from_run_id(run_id)

        profit_stop = _to_float(sm.get("profitstop"))  # dollars
        loss_stop = _to_float(sm.get("lossstop"))      # dollars
        max_stop_ticks = _to_float(sm.get("maxstop"))  # ticks
        max_tp_ratio = _to_float(sm.get("maxtpratio")) # ratio
        max_trades = _to_float(sm.get("maxtrades"))
        kill_flag = _to_bool(sm.get("use_kill"))
        kill_profit = _to_float(sm.get("kill_profit_stop"))
        kill_loss = _to_float(sm.get("kill_loss_stop"))
        #
        # Use_Kill, False,
        # Kill_Profit_Stop, 800,
        # Kill_Loss_Stop, 400,

        stop_loss_dollars = None
        max_tp_ticks = None
        max_tp_dollars = None
        if max_stop_ticks is not None:
            stop_loss_dollars = max_stop_ticks * tick_value
            if max_tp_ratio is not None:
                max_tp_ticks = max_stop_ticks * max_tp_ratio
                max_tp_dollars = max_tp_ticks * tick_value

        # Per your definition:
        # Max potential loss = LossStop - $1 + (MaxStop*TickValue)
        max_potential_loss = None
        if kill_flag:
            max_potential_loss = kill_loss

        # if loss_stop is not None and stop_loss_dollars is not None:
        #     max_potential_loss = loss_stop - 1.0 + stop_loss_dollars

        # "Similar calculation" for win. Implemented symmetrically:
        # Max potential profit = ProfitStop + $1 + (MaxStop*MaxTPRatio*TickValue)
        max_potential_profit = None
        if kill_flag:
            max_potential_profit = kill_profit
        #
        # kill_flag = _to_bool(sm.get("Use_Kill"))
        # kill_profit = _to_float(sm.get("Kill_Profit_Stop"))
        # kill_loss = _to_float(sm.get("Kill_Loss_Stop"))

        # if profit_stop is not None and max_tp_dollars is not None:
        #     max_potential_profit = profit_stop - 1.0 + max_tp_dollars

        # print(f"usekill: {sm.get("usekill")}")
        # print(f"use_kill: {sm.get("use_kill")}")
        derived = {
            "instrument": symbol,
            "tick_value_usd": tick_value,
            "instrument_source": source,

            "profit_stop_usd": profit_stop,
            "loss_stop_usd": loss_stop,
            "max_stop_ticks": max_stop_ticks,
            "max_tp_ratio": max_tp_ratio,

            "stop_loss_usd_per_trade": stop_loss_dollars,
            "max_tp_ticks_per_trade": max_tp_ticks,
            "max_tp_usd_per_trade": max_tp_dollars,

            "max_potential_profit_usd": max_potential_profit,
            "max_potential_loss_usd": max_potential_loss,
        }

        if pkg.metadata is None:
            pkg.metadata = {}
        pkg.metadata["derived"] = derived
