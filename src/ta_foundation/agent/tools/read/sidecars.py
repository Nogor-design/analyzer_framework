"""Read tool: parse a run's sidecar (or other JSON artifact) and return its body.

Sidecars on disk are produced by the existing discovery pipeline. This tool
returns the parsed body. If it would exceed the 2KB inline limit the
decorator spills to disk and returns a path; the agent can choose to
re-fetch a narrower view if needed.
"""

from __future__ import annotations

import json
from pathlib import Path

from ta_foundation.agent.tools._decorators import journaled_tool
from ta_foundation.research_ledger.repository import Repository


@journaled_tool(
    name="read_sidecar",
    role="agent:read",
    description=(
        "Read and parse a JSON sidecar artifact. Use to inspect run-level "
        "diagnostics that aren't in the candidates table (e.g., parameter "
        "neighborhood matrices, fold distributions, raw entry-discovery rules)."
    ),
    schema={"path": {"type": "str", "min_length": 1}},
)
def read_sidecar(repo: Repository, *, path: str) -> dict:
    p = Path(path)
    if not p.exists():
        return {"found": False, "path": path}
    if not p.is_file():
        return {"found": False, "path": path, "reason": "not a file"}
    try:
        body = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"found": True, "path": path, "valid_json": False, "error": str(exc)}
    return {"found": True, "path": path, "valid_json": True, "body": body}
