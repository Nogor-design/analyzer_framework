"""Phase 0 verification - defects #9 / #10 / #11 fixed.

Proves the *journaled discovery pipeline* now produces a survivor candidate
UNAIDED - no hand-grafted YAMLs, no manual sidecar ingestion. Runs the real
Sweep Operator (`run_one_hypothesis`) with the real `run_probe` tool against
a SANDBOX copy of the ledger.

The chain exercised:
  - author_probe emits a *runnable* discovery config            (defect #9)
  - the Operator ingests the sidecar run_probe produced          (defect #10)
  - the candidate lands with a promotable gate_verdict           (defect #11)

Before the fixes this same path retired the hypothesis as `no_trades`.

Usage:
    python scripts/phase0_journaled_pipeline_check.py \\
        --ledger-db .ta_artifacts/research_ledger.sandbox_check.db
"""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from ta_foundation.agent.roles.sweep_operator import run_one_hypothesis
from ta_foundation.agent.tools.write.author_probe import author_probe
from ta_foundation.agent.tools.write.run_probe import run_probe
from ta_foundation.research_ledger import get_repository

REPO_ROOT = Path(__file__).resolve().parents[1]
CANONICAL_DB = REPO_ROOT / ".ta_artifacts" / "research_ledger.db"

ORB_PARAMS = {
    "fill_mode": "body_midpoint",
    "orb_minutes": 5,
    "sweep_min_ticks": 4,
    "reclaim_within_bars": 1,
    "stop_ticks": 20,
    "target_ticks": 150,
}
MECHANISM = (
    "Phase 0 pipeline check. A NY-open opening range that sweeps outside its "
    "extreme then reclaims back inside traps breakout participants; their "
    "forced exits power the reverse move, entered on a body-midpoint pullback."
)


def banner(text: str) -> None:
    print("\n" + "=" * 72)
    print(text)
    print("=" * 72)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ledger-db",
                    default=".ta_artifacts/research_ledger.sandbox_check.db",
                    help="Sandbox ledger path (will be overwritten from canonical).")
    ap.add_argument("--market-data", default="D:/MarketData")
    ap.add_argument("--input-dir", default="inputs/nt_exports/probe")
    ap.add_argument("--output-dir", default="outputs/phase0_pipeline_check")
    args = ap.parse_args()

    ledger_db = Path(args.ledger_db)
    if ledger_db.name == "research_ledger.db":
        print("REFUSING to run against the canonical ledger.")
        return 2
    if not CANONICAL_DB.is_file():
        print(f"canonical ledger not found: {CANONICAL_DB}")
        return 2

    ledger_db.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(CANONICAL_DB, ledger_db)
    banner(f"Phase 0 pipeline check  |  sandbox={ledger_db}")
    print("sandbox copied fresh from canonical")

    repo = get_repository(ledger_db)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    hypothesis_id = f"h_phase0check_{stamp}"

    # ---- 1. author_probe -> runnable config (defect #9) ------------------
    banner("STEP 1  author_probe (expect a runnable config)")
    authored = author_probe(
        repo,
        hypothesis_id=hypothesis_id,
        family="orb_failure_reclaim",
        instrument="NQ",
        timeframe="1m",
        session_window="ny_open_0730_1000_denver",
        direction="both",
        params=ORB_PARAMS,
        mechanism=MECHANISM,
        registered_by="phase0:check",
    )
    if not authored.get("ok"):
        print(f"author_probe failed: {authored}")
        return 3
    result = authored["result"]
    yaml_path = result["yaml_path"]
    print(f"hypothesis_id = {hypothesis_id}")
    print(f"yaml_path     = {yaml_path}")
    print(f"runnable      = {result.get('runnable')}")
    if not result.get("runnable"):
        print(f"FAIL: author_probe did not emit a runnable config: "
              f"{result.get('config_note')}")
        return 3

    # ---- 2. run the real Operator (defects #10 + #11) --------------------
    banner("STEP 2  run_one_hypothesis (real Sweep Operator + real run_probe)")
    rec = run_one_hypothesis(
        repo,
        hypothesis_id=hypothesis_id,
        yaml_path=yaml_path,
        input_dir=args.input_dir,
        output_dir=args.output_dir,
        run_probe_call=run_probe,
        market_data_root=args.market_data,
        ledger_db=str(ledger_db),
        timeout_seconds=3600,
    )
    print(f"operator status       = {rec.status}")
    print(f"fast run_id           = {rec.fast_run_id}")
    print(f"hardened run_id       = {rec.hardened_run_id}")
    print(f"n_candidates_fast     = {rec.n_candidates_fast}")
    print(f"n_survivors_fast      = {rec.n_survivors_fast}")
    print(f"n_candidates_hardened = {rec.n_candidates_hardened}")
    print(f"n_survivors_hardened  = {rec.n_survivors_hardened}")
    for note in rec.notes:
        print(f"  note: {note}")
    if rec.error:
        print(f"  error: {rec.error}")

    # ---- 3. verdict ------------------------------------------------------
    banner("VERDICT")
    survivors = repo.list_candidates(
        hypothesis_id=hypothesis_id, gate_verdict="survivor", limit=50)
    all_cands = repo.list_candidates(hypothesis_id=hypothesis_id, limit=200)
    print(f"ledger candidates for hypothesis: {len(all_cands)}")
    print(f"ledger survivors for hypothesis:  {len(survivors)}")
    for s in survivors:
        print(f"  survivor {s.candidate_id}: pf_dev={s.pf_dev} "
              f"n_dev={s.n_trades_dev} pf_oos={s.pf_oos} n_oos={s.n_trades_oos}")

    if survivors:
        banner("PASS - the journaled pipeline produced a survivor UNAIDED "
               "(#9 + #10 + #11)")
        return 0
    banner("FAIL - no survivor; the journaled pipeline is still broken")
    return 1


if __name__ == "__main__":
    sys.exit(main())
