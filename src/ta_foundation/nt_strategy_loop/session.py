from __future__ import annotations

"""Strategy-loop session model.

Owns the durable folder layout described in
`docs/designs/autonomous_ninjatrader_strategy_loop.md`:

    .ta_artifacts/nt_strategy_lab/sessions/<session_id>/
        session.json
        strategy_spec.json
        source_request.md
        attempts/attempt_NNN/...
        compile_clean/
        optimizer/
        decisions/
        manifest.json
"""

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_LAB_ROOT = Path(".ta_artifacts") / "nt_strategy_lab" / "sessions"


@dataclass
class StrategyLoopSession:
    session_id: str
    session_dir: Path
    strategy_name: str
    compile_mode: str
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))

    @property
    def attempts_dir(self) -> Path:
        return self.session_dir / "attempts"

    def attempt_dir(self, attempt: int) -> Path:
        return self.attempts_dir / f"attempt_{attempt:03d}"

    @property
    def compile_clean_dir(self) -> Path:
        return self.session_dir / "compile_clean"

    @property
    def optimizer_dir(self) -> Path:
        return self.session_dir / "optimizer"

    @property
    def optimizer_output_dir(self) -> Path:
        return self.optimizer_dir / "nt_output"

    @property
    def decisions_dir(self) -> Path:
        return self.session_dir / "decisions"

    def ensure_dirs(self, *, first_attempt: int = 1) -> None:
        for directory in (
            self.attempt_dir(first_attempt),
            self.compile_clean_dir,
            self.optimizer_output_dir,
            self.decisions_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    def write_spec(self, spec: dict[str, Any]) -> Path:
        path = self.session_dir / "strategy_spec.json"
        path.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
        return path

    def write_source_request(self, body: str) -> Path:
        path = self.session_dir / "source_request.md"
        path.write_text(body, encoding="utf-8")
        return path

    def write_manifest(self, *, decision: str, artifacts: dict[str, Any]) -> Path:
        manifest = {
            "schema_version": 1,
            "session_id": self.session_id,
            "strategy_name": self.strategy_name,
            "compile_mode": self.compile_mode,
            "decision": decision,
            "created_at": self.created_at,
            "artifacts": artifacts,
        }
        payload = json.dumps(manifest, indent=2) + "\n"
        (self.session_dir / "manifest.json").write_text(payload, encoding="utf-8")
        (self.session_dir / "session.json").write_text(payload, encoding="utf-8")
        return self.session_dir / "manifest.json"


def create_session(
    *,
    lab_root: str | Path = DEFAULT_LAB_ROOT,
    strategy_name: str,
    compile_mode: str,
) -> StrategyLoopSession:
    session_id = _session_id(strategy_name)
    session_dir = Path(lab_root) / session_id
    return StrategyLoopSession(
        session_id=session_id,
        session_dir=session_dir,
        strategy_name=strategy_name,
        compile_mode=compile_mode,
    )


def _session_id(strategy_name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", strategy_name).strip("_").lower() or "strategy"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"loop_{stamp}_{slug}"


def latest_session(root: str | Path = DEFAULT_LAB_ROOT) -> Path | None:
    sessions = [path for path in Path(root).glob("loop_*") if path.is_dir()]
    if not sessions:
        return None
    return max(sessions, key=lambda path: path.stat().st_mtime)
