"""Read tool over on-disk market data coverage.

This is a lightweight scan of the configured market-data folder. It does not
load bars into memory — it just reports which (instrument, contract) datasets
exist, when their files were last modified, and roughly how big they are.
The Hypothesis Author uses this to avoid proposing probes for instruments
that have no current data.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ta_foundation.agent.tools._decorators import journaled_tool
from ta_foundation.research_ledger.repository import Repository


# NinjaTrader convention: e.g. "NQ 03-26.Last.txt" or "NQ 03-26.Bid.txt".
# The instrument root is the leading non-space token.
_NT_FILE_RE = re.compile(
    r"^(?P<root>[A-Z0-9]{1,6})\s+(?P<contract>\d{2}-\d{2})\.(?P<feed>Last|Bid|Ask)\.txt$"
)


@journaled_tool(
    name="get_market_data_coverage",
    role="agent:read",
    description=(
        "Scan the configured market-data folder and report which "
        "(instrument, contract) datasets are present. Returns one row per "
        "matched file with size and last-modified time."
    ),
    schema={
        "market_data_root": {"type": "str", "min_length": 1},
        "instrument": {"type": "str", "required": False},
        "limit": {"type": "int", "required": False, "default": 50, "min": 1, "max": 200},
    },
)
def get_market_data_coverage(
    repo: Repository,
    *,
    market_data_root: str,
    instrument: Optional[str] = None,
    limit: int = 50,
) -> dict:
    root = Path(market_data_root)
    if not root.exists():
        return {"root": str(root), "exists": False, "files": []}

    files_out: list[dict] = []
    instrument_filter = instrument.upper() if instrument else None

    for dirpath, _dirs, filenames in os.walk(root):
        for fname in filenames:
            m = _NT_FILE_RE.match(fname)
            if not m:
                continue
            inst_root = m.group("root")
            if instrument_filter and inst_root != instrument_filter:
                continue
            full = Path(dirpath) / fname
            try:
                stat = full.stat()
            except OSError:
                continue
            mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
            files_out.append({
                "instrument": inst_root,
                "contract": m.group("contract"),
                "feed": m.group("feed"),
                "size_bytes": stat.st_size,
                "modified_utc": mtime.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "path": str(full),
            })
            if len(files_out) >= limit:
                break
        if len(files_out) >= limit:
            break

    files_out.sort(key=lambda r: (r["instrument"], r["contract"], r["feed"]))
    by_instrument: dict[str, int] = {}
    for r in files_out:
        by_instrument[r["instrument"]] = by_instrument.get(r["instrument"], 0) + 1

    return {
        "root": str(root),
        "exists": True,
        "n_files": len(files_out),
        "by_instrument": by_instrument,
        "files": files_out,
    }
