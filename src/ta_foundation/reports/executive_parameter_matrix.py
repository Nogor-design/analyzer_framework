from __future__ import annotations

from datetime import timedelta
from typing import Any, Dict, List, Optional

import pandas as pd

from ta_foundation.core.model import AnalysisPackage
from ta_foundation.utils.kpi import normalize_kpi_key


def _settings_map(pkg: AnalysisPackage) -> dict[str, Any]:
    df = getattr(pkg, "settings", None)
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return {}
    out: dict[str, Any] = {}
    for _, row in df.iterrows():
        item = str(row.get("item", "")).strip()
        if item:
            out[item.lower()] = row.get("value", "")
    return out


def _kpi(pkg: AnalysisPackage, *keys: str) -> Any:
    summary = getattr(pkg, "summary", None)
    kpis = getattr(summary, "kpis_all", None) if summary else None
    if not isinstance(kpis, dict):
        return None
    for key in keys:
        norm = normalize_kpi_key(key)
        if norm in kpis:
            return kpis[norm]
        if key in kpis:
            return kpis[key]
        lowered = str(key).strip().lower()
        if lowered in kpis:
            return kpis[lowered]
        for existing_key, existing_value in kpis.items():
            if normalize_kpi_key(str(existing_key)) == norm:
                return existing_value
    return None


def _summary_cell(pkg: AnalysisPackage, row_label: str, col_label: str) -> Any:
    summary = getattr(pkg, "summary", None)
    if summary is None:
        return None
    row_key = normalize_kpi_key(row_label)
    col_key = normalize_kpi_key(col_label)
    candidates = [
        getattr(summary, "performance_table", None),
        getattr(summary, "performance", None),
        (getattr(summary, "tables", None) or {}).get("performance") if hasattr(summary, "tables") else None,
    ]
    for table in candidates:
        if isinstance(table, dict):
            row = table.get(row_key)
            if isinstance(row, dict) and col_key in row:
                return row[col_key]
    if col_key == normalize_kpi_key("all trades"):
        kpis_all = getattr(summary, "kpis_all", None)
        if isinstance(kpis_all, dict):
            return kpis_all.get(row_key)
    return None


def _to_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        if isinstance(value, str):
            cleaned = value.replace("$", "").replace(",", "").strip().rstrip("%")
            return float(cleaned)
        return float(value)
    except Exception:
        return None


def _win_rate_pct(value: Any) -> Optional[float]:
    numeric = _to_float(value)
    if numeric is None:
        return None
    if isinstance(value, str) and "%" in value:
        return numeric
    if numeric >= 1.0:
        return numeric
    if 0.05 <= numeric < 1.0:
        return numeric * 100.0
    if 0.0 < numeric < 0.05:
        scaled = numeric * 10000.0
        return scaled if scaled <= 100.0 else numeric * 100.0
    return None


def _date_range(pkg: AnalysisPackage) -> str:
    summary = getattr(pkg, "summary", None)
    start = getattr(summary, "start_dt", None) if summary else None
    end = getattr(summary, "end_dt", None) if summary else None

    def _fmt(dt: Any) -> Optional[str]:
        try:
            return dt.strftime("%m/%d/%Y")
        except Exception:
            return None

    left = _fmt(start)
    right = _fmt(end)
    if left and right:
        return f"{left} - {right}"

    daily = getattr(pkg, "daily", None)
    if isinstance(daily, pd.DataFrame) and not daily.empty:
        for col in ("date", "period"):
            if col in daily.columns:
                try:
                    dmin = pd.to_datetime(daily[col]).min()
                    dmax = pd.to_datetime(daily[col]).max()
                    return f"{dmin:%m/%d/%Y} - {dmax:%m/%d/%Y}"
                except Exception:
                    pass
    return "-"


def _active_window(sm: dict[str, Any]) -> str:
    def _to_int(key: str) -> Optional[int]:
        value = sm.get(key)
        try:
            return int(float(str(value).strip())) if value not in (None, "") else None
        except Exception:
            return None

    sh = _to_int("start_time_(hh)")
    smm = _to_int("start_time_(mm)")
    dh = _to_int("duration_time_(hh)")
    dmm = _to_int("duration_time_(mm)")
    if None in (sh, smm, dh, dmm):
        return "-"

    def _fmt(delta: timedelta) -> str:
        total = int(delta.total_seconds() // 60)
        hh = (total // 60) % 24
        mm = total % 60
        return f"{hh:02d}:{mm:02d}"

    start = timedelta(hours=sh, minutes=smm)
    end = start + timedelta(hours=dh, minutes=dmm)
    return f"{_fmt(start)} - {_fmt(end)} Colorado"


def _direction(sm: dict[str, Any]) -> str:
    def _to_bool(key: str) -> Optional[bool]:
        raw = str(sm.get(key, "")).strip().lower()
        if raw in {"true", "1", "yes", "y"}:
            return True
        if raw in {"false", "0", "no", "n"}:
            return False
        return None

    long_enabled = _to_bool("long")
    short_enabled = _to_bool("short")
    if long_enabled and short_enabled:
        return "Long and Short"
    if long_enabled:
        return "Long Only"
    if short_enabled:
        return "Short Only"
    return "-"


def _daily_extremes(pkg: AnalysisPackage) -> tuple[Optional[float], Optional[float]]:
    daily = getattr(pkg, "daily", None)
    if not isinstance(daily, pd.DataFrame) or daily.empty:
        return None, None
    value_col = next(
        (
            col
            for col in daily.columns
            if str(col).strip().lower() in {"net_profit", "net profit", "pnl", "profit"}
        ),
        None,
    )
    if value_col is None:
        return None, None
    try:
        series = pd.to_numeric(daily[value_col], errors="coerce").dropna()
        if series.empty:
            return None, None
        return float(series.max()), float(series.min())
    except Exception:
        return None, None


def _rating_mae_mfe(value: Optional[float]) -> str:
    if value is None:
        return "N/A"
    if value < 0.4:
        return "Excellent"
    if value < 0.7:
        return "Good"
    if value <= 1.2:
        return "Fair"
    return "Poor"


def _rating_mfe_etd(value: Optional[float], etd: Optional[float]) -> str:
    if etd == 0.0:
        return "Excellent"
    if value is None:
        return "N/A"
    if value > 3.0:
        return "Excellent"
    if value >= 1.5:
        return "Good"
    if value >= 1.0:
        return "Fair"
    return "Poor"


def _side_kpi(kpis: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = kpis.get(normalize_kpi_key(key))
        if value is not None:
            return value
        value = kpis.get(key)
        if value is not None:
            return value
        value = kpis.get(str(key).strip().lower())
        if value is not None:
            return value
        norm = normalize_kpi_key(key)
        for existing_key, existing_value in kpis.items():
            if normalize_kpi_key(str(existing_key)) == norm:
                return existing_value
    return None


def _instrument_and_tick(pkg: AnalysisPackage, sm: dict[str, Any]) -> tuple[str, Optional[float]]:
    derived = (getattr(pkg, "metadata", None) or {}).get("derived", {}) or {}
    instrument = derived.get("instrument") or sm.get("instrument") or "-"
    tick_value = _to_float(derived.get("tick_value_usd"))
    return str(instrument), tick_value


def _chart_label(sm: dict[str, Any]) -> str:
    value = str(sm.get("value", "")).strip()
    chart_type = str(sm.get("type", "")).strip()
    if value and chart_type:
        return f"{value} {chart_type}"
    return value or chart_type or "-"


def _max_take_profit(max_stop: Any, max_tp_ratio: Any) -> Optional[float]:
    stop = _to_float(max_stop)
    ratio = _to_float(max_tp_ratio)
    if stop is None or ratio is None:
        return None
    return stop * ratio


def build_executive_parameter_matrix(
    packages: Dict[str, AnalysisPackage],
    *,
    sort_by: str = "run_id",
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for run_id, pkg in packages.items():
        sm = _settings_map(pkg)
        derived = (getattr(pkg, "metadata", None) or {}).get("derived", {}) or {}
        instrument, tick_value = _instrument_and_tick(pkg, sm)

        raw_win_rate = _summary_cell(pkg, "Percent profitable", "All trades")
        if raw_win_rate is None:
            raw_win_rate = _kpi(pkg, "percent profitable")
        win_rate_pct = _win_rate_pct(raw_win_rate)

        avg_mae = _to_float(_kpi(pkg, "avg. mae", "average mae"))
        avg_mfe = _to_float(_kpi(pkg, "avg. mfe", "average mfe"))
        avg_etd = _to_float(_kpi(pkg, "avg. etd", "average etd"))

        mae_mfe_ratio = None
        mfe_etd_ratio = None
        if avg_mae is not None and avg_mfe not in (None, 0):
            mae_mfe_ratio = avg_mae / avg_mfe
        if avg_mfe is not None and avg_etd not in (None, 0):
            mfe_etd_ratio = avg_mfe / avg_etd

        best_day, worst_day = _daily_extremes(pkg)
        kpis_long = getattr(getattr(pkg, "summary", None), "kpis_long", None) or {}
        kpis_short = getattr(getattr(pkg, "summary", None), "kpis_short", None) or {}

        max_stop = _to_float(sm.get("maxstop"))
        tp_ratio = _to_float(sm.get("maxtpratio"))

        rows.append(
            {
                "run_id": run_id,
                "period": _date_range(pkg),
                "instrument": instrument,
                "tick_value": tick_value,
                "direction": _direction(sm),
                "contracts": sm.get("contracts", "-"),
                "active_window": _active_window(sm),
                "chart_value": sm.get("value", "-"),
                "chart_type": sm.get("type", "-"),
                "chart_label": _chart_label(sm),
                "label": sm.get("label", "-"),
                "fast_ma": sm.get("averagefast", "-"),
                "slow_ma": sm.get("averageslow", "-"),
                "trend_ma": sm.get("averagetrend", "-"),
                "max_trades": sm.get("maxtrades", "-"),
                "max_stop": max_stop,
                "tp_ratio": tp_ratio,
                "max_take_profit": _max_take_profit(max_stop, tp_ratio),
                "total_net_profit": _to_float(_kpi(pkg, "total net profit", "net profit")),
                "max_drawdown": _to_float(_kpi(pkg, "max drawdown", "maximum drawdown")),
                "win_rate_pct": win_rate_pct,
                "profit_factor": _to_float(_kpi(pkg, "profit factor")),
                "total_trades": _to_float(_kpi(pkg, "total number of trades", "total trades")),
                "avg_win": _to_float(_kpi(pkg, "avg. winning trade", "average winning trade")),
                "avg_loss": _to_float(_kpi(pkg, "avg. losing trade", "average losing trade")),
                "avg_mae": avg_mae,
                "avg_mfe": avg_mfe,
                "avg_etd": avg_etd,
                "mae_mfe_ratio": mae_mfe_ratio,
                "mae_mfe_rating": _rating_mae_mfe(mae_mfe_ratio),
                "mfe_etd_ratio": mfe_etd_ratio,
                "mfe_etd_rating": _rating_mfe_etd(mfe_etd_ratio, avg_etd),
                "best_day": best_day,
                "worst_day": worst_day,
                "max_potential_profit": _to_float(derived.get("max_potential_profit_usd")),
                "max_potential_loss": _to_float(derived.get("max_potential_loss_usd")),
                "long_profit": _to_float(_side_kpi(kpis_long, "total net profit", "net profit")),
                "long_win_rate_pct": _win_rate_pct(_side_kpi(kpis_long, "percent profitable")),
                "short_profit": _to_float(_side_kpi(kpis_short, "total net profit", "net profit")),
                "short_win_rate_pct": _win_rate_pct(_side_kpi(kpis_short, "percent profitable")),
            }
        )

    if sort_by == "total_net_profit":
        rows.sort(
            key=lambda row: (
                row.get("total_net_profit") is None,
                -(row.get("total_net_profit") or 0.0),
                str(row.get("run_id") or ""),
            )
        )
    else:
        rows.sort(key=lambda row: str(row.get("run_id") or "").lower())
    return rows
