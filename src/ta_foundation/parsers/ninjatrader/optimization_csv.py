from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import pandas as pd

from ta_foundation.parsers.base import ParsedArtifact
from ta_foundation.utils.parsing import parse_money, parse_float, parse_int


# ---------------------------------------------------------------------------
# Scalar coercion helpers
# ---------------------------------------------------------------------------

# Only literal True/False text gets coerced to bool. "1" and "0" must remain
# integers because NT's optimization output uses them as numeric parameter
# values (e.g. Contracts=1, ProfitStop=10000). Coercing them to bool corrupts
# downstream rendering of numeric params.
_BOOL_TRUE  = frozenset({"true", "yes"})
_BOOL_FALSE = frozenset({"false", "no"})


def _is_blank(x: Any) -> bool:
    if x is None:
        return True
    if isinstance(x, float):
        import math
        if math.isnan(x):
            return True
    s = str(x).strip()
    return s == "" or s.lower() in {"nan", "none", "null"}


def _coerce_scalar(raw: str) -> Any:
    """
    Coerce a raw parameter value string to the most specific Python type:
    bool  >  int  >  float  >  str

    Preserves multi-word strings such as bot names ("Iron Aphrodite Hunter")
    and filenames ("panthionIcon4.png") as-is.
    """
    s = raw.strip()
    sl = s.lower()
    if sl in _BOOL_TRUE:
        return True
    if sl in _BOOL_FALSE:
        return False
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    return s


# ---------------------------------------------------------------------------
# Core Parameters-column parser
# ---------------------------------------------------------------------------

def parse_parameters(raw: str) -> tuple[dict[str, Any], list[str]]:
    """
    Parse a NinjaTrader optimization *Parameters* cell.

    Format
    ------
    The cell contains two sections separated by the outermost ``(``:

        {val1}/{val2}/.../{{bot_name}} ({name1} {name2} … {nameN})

    Values are slash-delimited.  The last value may contain spaces
    (e.g. a bot name such as "Iron Aphrodite Hunter").

    Parameter names are space-delimited inside the outer parentheses.
    Names may themselves contain inner parentheses (e.g. ``Start_Time_(HH)``);
    those inner parens are handled by scanning backwards from the LAST ``)``
    to find the matching outermost ``(``.

    Returns
    -------
    param_map : dict[str, Any]
        Ordered {name: coerced_value} mapping.
    warnings  : list[str]
        Human-readable parse issues (empty on clean parse).
    """
    warnings: list[str] = []

    s = raw.strip()
    if not s:
        return {}, ["Empty parameters string"]

    # Step 1: find the outermost closing ')' (last one in the string)
    close_idx = s.rfind(")")
    if close_idx == -1:
        return {}, [f"No closing ')' found in Parameters string: {s[:80]!r}"]

    # Step 2: walk backwards to find the matching '(' at depth 0.
    # This correctly skips inner parens like (HH), (mm).
    depth = 1
    open_idx = -1
    for i in range(close_idx - 1, -1, -1):
        ch = s[i]
        if ch == ")":
            depth += 1
        elif ch == "(":
            depth -= 1
            if depth == 0:
                open_idx = i
                break

    if open_idx == -1:
        return {}, [f"No matching opening '(' found in Parameters string: {s[:80]!r}"]

    values_str = s[:open_idx].rstrip()
    names_str  = s[open_idx + 1 : close_idx].strip()

    # Step 3: split
    raw_values = [v.strip() for v in values_str.split("/")]
    names      = names_str.split()  # whitespace split; names like Start_Time_(HH) are single tokens

    if len(raw_values) != len(names):
        warnings.append(
            f"Parameter count mismatch: {len(raw_values)} values vs {len(names)} names. "
            f"Pairing by min length."
        )

    # Step 4: zip with duplicate-name handling
    n = min(len(raw_values), len(names))
    param_map: dict[str, Any] = {}
    for i in range(n):
        name  = names[i]
        value = _coerce_scalar(raw_values[i])
        # Guard against duplicate parameter names
        if name in param_map:
            j = 2
            while f"{name}_{j}" in param_map:
                j += 1
            name = f"{name}_{j}"
        param_map[name] = value

    return param_map, warnings


def _detect_parameter_names(df: pd.DataFrame, col: str) -> list[str]:
    """
    Return the parameter names from the first non-blank Parameters cell.
    Used to establish the canonical column order before the full parse pass.
    """
    for raw_p in df[col]:
        if _is_blank(raw_p):
            continue
        s = str(raw_p).strip()
        close_idx = s.rfind(")")
        if close_idx == -1:
            continue
        depth = 1
        open_idx = -1
        for i in range(close_idx - 1, -1, -1):
            if s[i] == ")":
                depth += 1
            elif s[i] == "(":
                depth -= 1
                if depth == 0:
                    open_idx = i
                    break
        if open_idx != -1:
            names_str = s[open_idx + 1 : close_idx].strip()
            return names_str.split()
    return []


# ---------------------------------------------------------------------------
# Column rename map — NinjaTrader optimization export headers
# ---------------------------------------------------------------------------

_METRIC_RENAME: dict[str, str] = {
    "Performance":        "performance",
    "Total net profit":   "total_net_profit",
    "Gross profit":       "gross_profit",
    "Gross loss":         "gross_loss",
    "Profit factor":      "profit_factor",
    "Max. drawdown":      "max_drawdown",
    "Total # of trades":  "total_trades",
    "Percent profitable": "pct_profitable",
}

# (column_name_after_rename, parse_function)
_METRIC_PARSERS: list[tuple[str, Any]] = [
    ("total_net_profit", parse_money),
    ("gross_profit",     parse_money),
    ("gross_loss",       parse_money),
    ("max_drawdown",     parse_money),
    ("profit_factor",    parse_float),
    ("performance",      parse_float),
    ("total_trades",     parse_int),
    ("pct_profitable",   parse_float),
]


# ---------------------------------------------------------------------------
# Parser class
# ---------------------------------------------------------------------------

class NinjaTraderOptimizationCsvParser:
    """
    Parses NinjaTrader Strategy Analyzer Optimization export CSV files.

    **File identification**

    * Filename ends with ``_Optimization.csv``
    * Header row contains both ``Parameters`` and ``Performance``

    **What this parser produces**

    A :class:`~ta_foundation.parsers.base.ParsedArtifact` with
    ``kind="optimization"`` carrying:

    * ``df``  — normalized DataFrame (one row per tested combination) with:

      - standard metric columns (total_net_profit, profit_factor, …)
      - ``raw_parameters`` (original Parameters string, for traceability)
      - ``param_<Name>`` column for every detected parameter
      - ``parse_ok`` / ``parse_warnings`` per-row quality flags
      - ``source_file`` traceability column

    * ``summary`` — dict with batch-level metadata consumed by
      :func:`~ta_foundation.core.pipeline._attach_optimization_artifact`:

      ``batch_id``, ``parameter_names``, ``metric_columns``,
      ``instrument``, ``row_count``, ``successfully_parsed_rows``

    This artifact is **not** attached to an AnalysisPackage.  The ingest
    pipeline routes it to an
    :class:`~ta_foundation.optimization.model.OptimizationStore` instead.
    """

    kind = "optimization"

    def can_parse(self, path: Path, header: str) -> bool:
        name_ok = path.name.endswith("_Optimization.csv")
        sig_ok  = "Parameters" in header and "Performance" in header
        return name_ok and sig_ok

    def parse(self, path: Path, run_id: Optional[str]) -> ParsedArtifact:  # noqa: C901
        warnings: list[dict[str, Any]] = []

        # ---- read raw CSV (all columns as str to avoid pandas type coercion) ----
        try:
            df = pd.read_csv(path, dtype=str)
        except Exception as exc:
            return ParsedArtifact(
                kind="optimization",
                run_id=run_id,
                source_path=path,
                df=pd.DataFrame(),
                summary={
                    "batch_id": run_id,
                    "parameter_names": [],
                    "metric_columns": [],
                    "instrument": None,
                    "row_count": 0,
                    "successfully_parsed_rows": 0,
                },
                warnings=[{"code": "CSV_READ_ERROR", "message": f"Failed to read {path.name}: {exc}"}],
            )

        # Drop trailing unnamed columns (NinjaTrader trailing commas)
        df = df.loc[:, ~df.columns.str.startswith("Unnamed")]

        # Forward-fill Instrument: NT only writes it on the first row
        if "Instrument" in df.columns:
            df["Instrument"] = (
                df["Instrument"]
                .replace("", pd.NA)
                .ffill()
            )

        # Rename metric columns to canonical names
        df = df.rename(columns={k: v for k, v in _METRIC_RENAME.items() if k in df.columns})

        # Parse numeric metric columns
        for col, fn in _METRIC_PARSERS:
            if col in df.columns:
                df[col] = df[col].map(fn)

        # ---- Parameters column parsing ----
        all_param_names: list[str] = []
        param_rows:       list[dict[str, Any]] = []
        parse_ok_flags:   list[bool]            = []
        parse_warn_strs:  list[str]             = []

        if "Parameters" not in df.columns:
            warnings.append({
                "code": "MISSING_PARAMETERS_COL",
                "message": f"No 'Parameters' column found in {path.name}",
            })
        else:
            all_param_names = _detect_parameter_names(df, "Parameters")

            for raw_p in df["Parameters"]:
                if _is_blank(raw_p):
                    param_rows.append({})
                    parse_ok_flags.append(False)
                    parse_warn_strs.append("blank parameters cell")
                    continue

                param_map, row_warns = parse_parameters(str(raw_p))
                param_rows.append(param_map)
                parse_ok_flags.append(len(row_warns) == 0)
                parse_warn_strs.append("; ".join(row_warns))

        # Rename Parameters → raw_parameters for traceability
        if "Parameters" in df.columns:
            df = df.rename(columns={"Parameters": "raw_parameters"})

        # Attach per-row quality flags
        df["parse_ok"]       = parse_ok_flags if parse_ok_flags else True
        df["parse_warnings"] = parse_warn_strs if parse_warn_strs else ""

        # Build param_* columns and join
        if param_rows:
            param_df = pd.DataFrame(param_rows)
            if not param_df.empty:
                param_df.columns = [f"param_{c}" for c in param_df.columns]
                df = pd.concat(
                    [df.reset_index(drop=True), param_df.reset_index(drop=True)],
                    axis=1,
                )

        # Traceability
        df["source_file"] = path.name

        # ---- Summary metadata for the pipeline ----
        metric_columns = [c for c in _METRIC_RENAME.values() if c in df.columns]
        row_count      = len(df)
        ok_count       = int(df["parse_ok"].sum()) if "parse_ok" in df.columns else row_count

        instrument: Optional[str] = None
        if "Instrument" in df.columns:
            inst_vals = df["Instrument"].dropna()
            if not inst_vals.empty:
                candidate = str(inst_vals.iloc[0]).strip()
                instrument = candidate if candidate else None

        summary: dict[str, Any] = {
            "batch_id":                 run_id,
            "parameter_names":          all_param_names,
            "metric_columns":           metric_columns,
            "instrument":               instrument,
            "row_count":                row_count,
            "successfully_parsed_rows": ok_count,
        }

        return ParsedArtifact(
            kind="optimization",
            run_id=run_id,
            source_path=path,
            df=df,
            summary=summary,
            warnings=warnings,
        )
