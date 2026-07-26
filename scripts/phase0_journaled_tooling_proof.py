"""Phase 0 - Runbook A step 4: journaled-tooling end-to-end proof.

Drives one fresh test hypothesis through the journaled write tools against a
SANDBOX copy of the research ledger - never the canonical DB. Proves the
chain author_probe -> run_probe(fast_probe) -> record_candidates_for_run ->
promote_to_hardening -> run_probe(hardened) -> request_locked_holdout ->
run_probe(locked_holdout) and that the one-shot holdout lock rejects a
second attempt and the preconditions actually fire.

Reconciles the runbook self-contradiction (Runbook A step 4 says "register a
test hypothesis"; the "does NOT do" section forbids new hypotheses / spent
locks) by doing all of it against a throwaway DB copy.

Usage:
    # make the sandbox copy first (a failed proof never touches prod):
    cp .ta_artifacts/research_ledger.db .ta_artifacts/research_ledger.sandbox.db
    python scripts/phase0_journaled_tooling_proof.py \\
        --ledger-db .ta_artifacts/research_ledger.sandbox.db \\
        --market-data D:/MarketData \\
        --input-dir inputs/nt_exports/probe

The script refuses to run against a path that looks like the canonical DB.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml as _yaml

from ta_foundation.agent.tools.write.author_probe import author_probe
from ta_foundation.agent.tools.write.promote import (
    promote_to_hardening,
    request_locked_holdout,
)
from ta_foundation.agent.tools.write.run_probe import (
    record_candidates_for_run,
    run_probe,
)
from ta_foundation.research_ledger import get_repository
from ta_foundation.research_ledger.sidecar_parser import parse_summary_sidecar

REPO_ROOT = Path(__file__).resolve().parents[1]

# Known-good discovery configs proven by the Phase 0 defect-#4 probe work.
FAST_BASE_YAML = REPO_ROOT / "discovery" / "04_nq_ny_open_orb_failure_reclaim_probe.yaml"
HARDENED_BASE_YAML = (
    REPO_ROOT
    / "discovery"
    / "04_nq_ny_open_orb_failure_reclaim_body_midpoint_locked_hardening.yaml"
)

# Pre-registered hypothesis facts. params must satisfy the orb_failure_reclaim
# family whitelist; the values mirror the locked-hardening rule.
HYPO_PARAMS = {
    "fill_mode": "body_midpoint",
    "orb_minutes": 5,
    "reclaim_within_bars": 1,
    "stop_ticks": 20,
    "sweep_min_ticks": 4,
    "target_ticks": 150,
}
HYPO_FAMILY = "orb_failure_reclaim"
HYPO_INSTRUMENT = "NQ"
HYPO_TIMEFRAME = "1m"
HYPO_SESSION = "ny_open_0730_1000_denver"
HYPO_DIRECTION = "both"
HYPO_MECHANISM = (
    "Phase 0 tooling proof. A NY-open opening range that sweeps outside its "
    "extreme then reclaims back inside traps breakout participants; their "
    "forced exits power the reverse move, entered on a body-midpoint pullback."
)

findings: list[str] = []


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def banner(text: str) -> None:
    print("\n" + "=" * 72)
    print(text)
    print("=" * 72)


def show(label: str, payload: object) -> None:
    print(f"\n--- {label} ---")
    print(json.dumps(payload, indent=2, default=str)[:2000])


def unwrap(payload: dict) -> dict:
    """Return the journaled tool's `result`, loading the spill file when the
    output was truncated to disk by the @journaled_tool decorator."""
    if not payload.get("ok"):
        return payload
    if payload.get("truncated"):
        full = json.loads(Path(payload["artifact_path"]).read_text(encoding="utf-8"))
        return full.get("result") or {}
    return payload.get("result") or {}


def write_probe_yaml(base_yaml: Path, dest: Path, hypothesis_id: str) -> Path:
    """Copy a known-good discovery config and inject a pre_registration block
    whose params hash-match the registered hypothesis so the CLI drift check
    passes. The discovery config itself is left untouched."""
    cfg = _yaml.safe_load(base_yaml.read_text(encoding="utf-8")) or {}
    cfg["pre_registration"] = {
        "hypothesis_id": hypothesis_id,
        "family": HYPO_FAMILY,
        "instrument": HYPO_INSTRUMENT,
        "timeframe": HYPO_TIMEFRAME,
        "session_window": HYPO_SESSION,
        "direction": HYPO_DIRECTION,
        "params": HYPO_PARAMS,
        "pre_reg_mechanism": HYPO_MECHANISM,
    }
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(_yaml.safe_dump(cfg, sort_keys=True), encoding="utf-8")
    return dest


def newest_sidecar(artifact_dir: Path) -> Path | None:
    cands = sorted(
        artifact_dir.rglob("*_summary.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return cands[0] if cands else None


def ingest_run_candidates(repo, run_id: str, sidecar: Path) -> dict:
    """Parse a discovery sidecar and feed it to the journaled
    record_candidates_for_run tool. Mints candidate_ids the way the backfill
    importer does (c_<run_short>_<rank:03d>) - the sidecar parser itself does
    NOT emit candidate_id, so a caller must."""
    parsed = parse_summary_sidecar(sidecar)
    run_short = run_id.split("_")[-1]
    cand_dicts = []
    for c in parsed.candidates:
        rank = c["rank_in_run"]
        cand_dicts.append(
            {
                "candidate_id": f"c_{run_short}_{rank:03d}",
                "rank_in_run": rank,
                "params": c.get("params") or {},
                "gate_verdict": c.get("gate_verdict") or "pending",
                "gate_reasons": c.get("gate_reasons"),
                "n_trades_dev": c.get("n_trades_dev"),
                "pf_dev": c.get("pf_dev"),
                "expectancy_dev": c.get("expectancy_dev"),
                "n_trades_oos": c.get("n_trades_oos"),
                "pf_oos": c.get("pf_oos"),
                "expectancy_oos": c.get("expectancy_oos"),
                "n_trades_holdout": c.get("n_trades_holdout"),
                "pf_holdout": c.get("pf_holdout"),
                "expectancy_holdout": c.get("expectancy_holdout"),
                "slippage_stress_pass": c.get("slippage_stress_pass"),
            }
        )
    return unwrap(record_candidates_for_run(repo, run_id=run_id, candidates=cand_dicts))


def do_run_probe(repo, *, hypothesis_id, yaml_path, mode, input_dir, output_dir,
                 market_data, ledger_db) -> dict:
    """Invoke the journaled run_probe tool and abort the proof on subprocess
    failure (the breakage IS the finding, so surface it loudly)."""
    payload = run_probe(
        repo,
        hypothesis_id=hypothesis_id,
        yaml_path=str(yaml_path),
        mode=mode,
        input_dir=str(input_dir),
        output_dir=str(output_dir),
        market_data_root=str(market_data),
        ledger_db=str(ledger_db),
        timeout_seconds=3600,
    )
    result = unwrap(payload)
    if not payload.get("ok"):
        banner(f"run_probe({mode}) REJECTED before subprocess")
        show("rejection", payload)
        sys.exit(2)
    if not result.get("ok_subprocess"):
        banner(f"run_probe({mode}) subprocess FAILED")
        show("result", result)
        sys.exit(3)
    return result


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ledger-db", required=True,
                    help="Path to the SANDBOX ledger copy.")
    ap.add_argument("--market-data", default="D:/MarketData")
    ap.add_argument("--input-dir", default="inputs/nt_exports/probe")
    ap.add_argument("--output-base", default="outputs/phase0_proof")
    args = ap.parse_args()

    ledger_db = Path(args.ledger_db)
    if ledger_db.name == "research_ledger.db":
        print("REFUSING to run against the canonical ledger. Pass a sandbox copy.")
        return 2
    if not ledger_db.is_file():
        print(f"sandbox ledger not found: {ledger_db}")
        return 2

    repo = get_repository(ledger_db)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    hypothesis_id = f"h_phase0proof_{stamp}"
    out_base = Path(args.output_base)

    banner(f"Phase 0 journaled-tooling proof  |  sandbox={ledger_db}")
    print(f"hypothesis_id = {hypothesis_id}")

    # ---- 1. author_probe --------------------------------------------------
    banner("STEP 1  author_probe")
    p = author_probe(
        repo,
        hypothesis_id=hypothesis_id,
        family=HYPO_FAMILY,
        instrument=HYPO_INSTRUMENT,
        timeframe=HYPO_TIMEFRAME,
        session_window=HYPO_SESSION,
        direction=HYPO_DIRECTION,
        params=HYPO_PARAMS,
        mechanism=HYPO_MECHANISM,
        registered_by="phase0:proof",
    )
    res = unwrap(p)
    show("author_probe", p)
    if not p.get("ok") or not res.get("registered"):
        print("author_probe failed - aborting")
        return 3
    findings.append("author_probe: OK - hypothesis row + generated YAML written.")

    # ---- 2. build runnable probe YAMLs -----------------------------------
    banner("STEP 2  build runnable probe YAMLs (config + pre_registration)")
    proof_dir = REPO_ROOT / "discovery" / "_phase0_proof"
    fast_yaml = write_probe_yaml(
        FAST_BASE_YAML, proof_dir / f"{hypothesis_id}__fast.yaml", hypothesis_id)
    hardened_yaml = write_probe_yaml(
        HARDENED_BASE_YAML, proof_dir / f"{hypothesis_id}__hardened.yaml",
        hypothesis_id)
    print(f"fast     -> {fast_yaml}")
    print(f"hardened -> {hardened_yaml}")
    findings.append(
        "author_probe's own generated YAML (discovery/generated/<id>.yaml) "
        "carries ONLY a pre_registration block - no discovery sweep config - "
        "so it is NOT runnable as-is; this script had to graft the block onto "
        "a known-good config by hand. DEFECT.")

    # ---- 3. run_probe(fast_probe) + record candidates --------------------
    banner("STEP 3  run_probe(fast_probe)")
    t0 = time.monotonic()
    fast = do_run_probe(
        repo, hypothesis_id=hypothesis_id, yaml_path=fast_yaml, mode="fast_probe",
        input_dir=args.input_dir, output_dir=out_base / "fast",
        market_data=args.market_data, ledger_db=ledger_db)
    print(f"fast run_id={fast['run_id']}  ({time.monotonic() - t0:.0f}s)")
    sidecar = newest_sidecar(Path(fast["artifact_dir"]))
    if sidecar is None:
        print("no sidecar produced by fast probe - aborting")
        return 3
    print(f"sidecar: {sidecar}")
    ing = ingest_run_candidates(repo, fast["run_id"], sidecar)
    show("record_candidates_for_run (fast)", ing)
    fast_cands = repo.list_candidates(run_id=fast["run_id"], limit=200)
    verdicts: dict[str, int] = {}
    for c in fast_cands:
        verdicts[c.gate_verdict] = verdicts.get(c.gate_verdict, 0) + 1
    print(f"fast candidates: {len(fast_cands)}  verdicts={verdicts}")
    fast_survivors = [c for c in fast_cands if c.gate_verdict == "survivor"]
    if not fast_survivors:
        findings.append(
            f"fast_probe produced {len(fast_cands)} candidates but 0 survivors "
            f"(verdicts={verdicts}). sidecar_parser._derive_gate_verdict only "
            "maps tier ids qualified/strong->survivor, but the discovery "
            "pipeline emits tier id 'high_quality' -> falls through to "
            "'pending'. promote_to_hardening can NEVER fire from a fast "
            "probe. DEFECT.")

    # ---- 4. precondition demo: promote a non-survivor (must be rejected) --
    banner("STEP 4  precondition demo - promote_to_hardening on a pending candidate")
    if fast_cands:
        pend = next((c for c in fast_cands if c.gate_verdict != "survivor"),
                    fast_cands[0])
        rej = promote_to_hardening(
            repo, candidate_id=pend.candidate_id,
            reason="phase0 proof: expect this to be rejected, not a survivor",
            promoted_by="phase0:proof")
        show(f"promote_to_hardening({pend.candidate_id})", rej)
        if not rej.get("ok") and rej.get("code") == "not_a_survivor":
            findings.append(
                "promote_to_hardening precondition 'not_a_survivor' fires "
                "correctly on a pending candidate. OK.")
        else:
            findings.append(
                "promote_to_hardening did NOT reject a pending candidate as "
                "expected. DEFECT.")

    # ---- 5. run_probe(hardened) + record candidates ----------------------
    banner("STEP 5  run_probe(hardened)")
    t0 = time.monotonic()
    hard = do_run_probe(
        repo, hypothesis_id=hypothesis_id, yaml_path=hardened_yaml,
        mode="hardened", input_dir=args.input_dir,
        output_dir=out_base / "hardened", market_data=args.market_data,
        ledger_db=ledger_db)
    print(f"hardened run_id={hard['run_id']}  ({time.monotonic() - t0:.0f}s)")
    h_sidecar = newest_sidecar(Path(hard["artifact_dir"]))
    if h_sidecar is None:
        print("no sidecar produced by hardened probe - aborting")
        return 3
    print(f"sidecar: {h_sidecar}")
    h_ing = ingest_run_candidates(repo, hard["run_id"], h_sidecar)
    show("record_candidates_for_run (hardened)", h_ing)
    hard_cands = repo.list_candidates(run_id=hard["run_id"], limit=200)
    hard_survivors = [c for c in hard_cands if c.gate_verdict == "survivor"]
    print(f"hardened candidates: {len(hard_cands)}  survivors: {len(hard_survivors)}")
    if not hard_survivors:
        findings.append(
            "hardened run produced 0 survivors - cannot exercise "
            "promote_to_hardening / request_locked_holdout. DEFECT or the "
            "rule decayed below the gates.")
        banner("PROOF INCOMPLETE - no survivor to promote")
        emit_findings(repo)
        return 1
    survivor = hard_survivors[0]
    print(f"survivor candidate: {survivor.candidate_id}  "
          f"pf_dev={survivor.pf_dev} n_dev={survivor.n_trades_dev} "
          f"pf_oos={survivor.pf_oos} n_oos={survivor.n_trades_oos}")
    findings.append(
        "hardened run_probe + record_candidates_for_run produced a survivor "
        "candidate with dev+oos metrics. OK.")

    # ---- 6. promote_to_hardening (survivor) ------------------------------
    banner("STEP 6  promote_to_hardening on the survivor")
    pr = promote_to_hardening(
        repo, candidate_id=survivor.candidate_id,
        reason="phase0 proof: survivor cleared hardening gates; promote it",
        promoted_by="phase0:proof")
    show("promote_to_hardening", pr)
    after = repo.get_candidate(survivor.candidate_id)
    ok_promote = pr.get("ok") and after and after.triage_state == "hardening_queue"
    findings.append(
        "promote_to_hardening: OK - triage_state set to hardening_queue."
        if ok_promote else
        "promote_to_hardening did NOT set hardening_queue. DEFECT.")

    # ---- 7. request_locked_holdout - one-shot lock -----------------------
    banner("STEP 7  request_locked_holdout - one-shot lock")
    lock1 = request_locked_holdout(
        repo, candidate_id=survivor.candidate_id, requested_by="phase0:proof")
    show("request_locked_holdout (attempt 1)", lock1)
    lock2 = request_locked_holdout(
        repo, candidate_id=survivor.candidate_id, requested_by="phase0:proof")
    show("request_locked_holdout (attempt 2)", lock2)
    r1 = unwrap(lock1)
    r2 = unwrap(lock2)
    if r1.get("lock_acquired") is True and r2.get("lock_acquired") is False:
        findings.append(
            "request_locked_holdout: OK - one-shot lock acquired once, second "
            "attempt correctly returned lock_acquired=False.")
    else:
        findings.append(
            f"request_locked_holdout one-shot lock MISBEHAVED: "
            f"attempt1={r1.get('lock_acquired')} attempt2={r2.get('lock_acquired')}. "
            "DEFECT.")

    # ---- 8. run_probe(locked_holdout) ------------------------------------
    banner("STEP 8  run_probe(locked_holdout)")
    t0 = time.monotonic()
    hold = do_run_probe(
        repo, hypothesis_id=hypothesis_id, yaml_path=hardened_yaml,
        mode="locked_holdout", input_dir=args.input_dir,
        output_dir=out_base / "locked_holdout", market_data=args.market_data,
        ledger_db=ledger_db)
    print(f"locked_holdout run_id={hold['run_id']}  ({time.monotonic() - t0:.0f}s)")
    hold_run = repo.get_run(hold["run_id"])
    findings.append(
        f"run_probe(locked_holdout): OK - runs row written with "
        f"mode={hold_run.mode!r}. NOTE: run_probe's `mode` is a pure ledger "
        "label; it does not change the CLI invocation - the locked-holdout "
        "config lives entirely in the YAML.")

    emit_findings(repo)
    banner("PROOF COMPLETE")
    return 0


def emit_findings(repo) -> None:
    banner("FINDINGS")
    for i, f in enumerate(findings, 1):
        print(f"{i}. {f}\n")
    rows = repo.conn.execute(
        "SELECT role, tool_name, error FROM tool_journal "
        "WHERE role LIKE 'agent:%' OR role LIKE 'phase0%' "
        "ORDER BY journal_id DESC LIMIT 20").fetchall()
    print("--- recent tool_journal rows (newest first) ---")
    for r in rows:
        print(f"  {r['role']:<26} {r['tool_name']:<28} "
              f"error={r['error']}")


if __name__ == "__main__":
    raise SystemExit(main())
