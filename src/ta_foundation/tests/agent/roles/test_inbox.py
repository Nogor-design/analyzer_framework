"""Tests for the HITL inbox CLI surface (Phase B.5)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ta_foundation.agent import inbox as inbox_mod
from ta_foundation.agent.roles import scribe as scribe_mod
from ta_foundation.research_ledger import Repository, get_repository


@pytest.fixture(autouse=True)
def _isolate_paths(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(scribe_mod, "INBOX_ROOT", tmp_path / "inbox")
    monkeypatch.setattr(inbox_mod, "INBOX_ROOT", tmp_path / "inbox")
    monkeypatch.setattr(inbox_mod, "REJECTED_ROOT", tmp_path / "rejected")
    monkeypatch.setattr(inbox_mod, "LETTERS_FINAL_DIR", tmp_path / "letters")
    # write_post_mortem writes to Path("discovery/graveyard") — cd to tmp.
    monkeypatch.chdir(tmp_path)
    from ta_foundation.agent.tools.write import post_mortem as pm_mod
    monkeypatch.setattr(pm_mod, "GRAVEYARD_DIR", tmp_path / "discovery/graveyard")
    return tmp_path


@pytest.fixture()
def repo(tmp_path: Path) -> Repository:
    return get_repository(tmp_path / "ledger.db")


def _seed_graveyard_with_draft(repo: Repository, *, cid: str = "c_inbox_001") -> Path:
    if repo.get_hypothesis("h_x") is None:
        repo.register_hypothesis(
            hypothesis_id="h_x", family="vwap_reject_fade",
            instrument="NQ", timeframe="5m",
            params={"min_distance_ticks": 4, "stop_ticks": 8, "target_ticks": 24},
            mechanism=("A reasonable mechanism paragraph that comfortably exceeds the "
                       "50-character minimum the repository enforces."),
            registered_by="test",
        )
        repo.start_run(run_id="r_x", hypothesis_id="h_x", mode="hardened",
                       config_hash="abc", yaml_path="x", artifact_dir="x")
        repo.complete_run("r_x")
    repo.record_candidate(
        candidate_id=cid, run_id="r_x", rank_in_run=1, params={},
        gate_verdict="rejected", n_trades_dev=47, pf_dev=1.62,
    )
    repo.set_triage(candidate_id=cid, state="graveyard",
                    reason="failed adjusted t-test under multiple-comparison correction",
                    triaged_by="test")
    draft = scribe_mod._post_mortem_inbox_dir() / f"{cid}.md"
    draft.parent.mkdir(parents=True, exist_ok=True)
    body = (
        "---\n"
        "type: post_mortem\n"
        "cites:\n"
        f"  - candidate_id: {cid}\n"
        "---\n\n"
        f"# Post-mortem for {cid}\n\n"
        f"Candidate {cid} reached PF=1.62 across 47 trades on dev. "
        "Adjusted t-test failed under multiple-comparison correction; "
        "sample below fund-grade threshold. Mechanism plausible but underpowered.\n"
    )
    draft.write_text(body, encoding="utf-8")
    return draft


def _seed_weekly_letter_draft(repo: Repository, *, week: str = "2026-W19") -> Path:
    target = scribe_mod.INBOX_ROOT / "weekly_letters" / f"{week}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        f"---\ntype: weekly_letter\nweek_iso: {week}\ncites: []\n---\n"
        f"# Weekly letter for {week}\n\nNothing happened this week.\n",
        encoding="utf-8",
    )
    return target


# ===========================================================================
# list / show
# ===========================================================================


def test_list_empty(repo: Repository) -> None:
    assert inbox_mod.list_drafts() == []


def test_list_finds_both_types(repo: Repository) -> None:
    _seed_graveyard_with_draft(repo, cid="c_a")
    _seed_weekly_letter_draft(repo)
    drafts = inbox_mod.list_drafts()
    types = {d.artifact_type for d in drafts}
    assert "post_mortem" in types
    assert "weekly_letter" in types


def test_show_returns_body(repo: Repository) -> None:
    _seed_graveyard_with_draft(repo, cid="c_a")
    body = inbox_mod.show_draft("post_mortems/c_a")
    assert body is not None
    assert "c_a" in body


def test_show_unknown_returns_none(repo: Repository) -> None:
    assert inbox_mod.show_draft("post_mortems/c_missing") is None


def test_list_marks_lint_failure(repo: Repository) -> None:
    target = scribe_mod._post_mortem_inbox_dir() / "c_x_LINT_FAIL.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("---\ntype: post_mortem_lint_failure\n---\n# fail\n",
                       encoding="utf-8")
    drafts = inbox_mod.list_drafts()
    assert len(drafts) == 1
    assert drafts[0].is_lint_failure is True


# ===========================================================================
# accept
# ===========================================================================


def test_accept_post_mortem_moves_to_graveyard(repo: Repository) -> None:
    _seed_graveyard_with_draft(repo, cid="c_inbox_001")
    out = inbox_mod.accept_draft(repo, "post_mortems/c_inbox_001",
                                   accepted_by="human:test")
    assert out["ok"]
    final = Path(out["final_path"])
    assert final.exists()
    # Inbox copy gone.
    assert not (scribe_mod._post_mortem_inbox_dir() / "c_inbox_001.md").exists()
    # Journal entry recorded by the underlying write tool.
    rows = repo.list_journal(tool_name="write_post_mortem")
    assert len(rows) >= 1


def test_accept_weekly_letter_moves_to_letters(repo: Repository) -> None:
    _seed_weekly_letter_draft(repo, week="2026-W19")
    out = inbox_mod.accept_draft(repo, "weekly_letters/2026-W19",
                                   accepted_by="human:test")
    assert out["ok"]
    final = Path(out["final_path"])
    assert final.exists()
    rows = repo.list_journal(tool_name="inbox.accept_weekly_letter")
    assert len(rows) >= 1


def test_accept_unknown_draft_fails(repo: Repository) -> None:
    out = inbox_mod.accept_draft(repo, "post_mortems/c_missing",
                                   accepted_by="human:test")
    assert not out["ok"]
    assert out["code"] == "unknown_draft"


def test_accept_lint_failure_draft_refused(repo: Repository) -> None:
    target = scribe_mod._post_mortem_inbox_dir() / "c_y_LINT_FAIL.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("---\n---\n# x\n", encoding="utf-8")
    out = inbox_mod.accept_draft(repo, "post_mortems/c_y",
                                   accepted_by="human:test")
    assert not out["ok"]
    assert out["code"] == "cannot_accept_lint_failure"


# ===========================================================================
# reject
# ===========================================================================


def test_reject_moves_to_rejected(repo: Repository) -> None:
    _seed_graveyard_with_draft(repo, cid="c_rej")
    out = inbox_mod.reject_draft(
        repo, "post_mortems/c_rej",
        reason="tone too speculative; revise mechanism paragraph",
        rejected_by="human:test",
    )
    assert out["ok"]
    rejected_path = Path(out["rejected_path"])
    assert rejected_path.exists()
    assert not (scribe_mod._post_mortem_inbox_dir() / "c_rej.md").exists()


def test_reject_short_reason_refused(repo: Repository) -> None:
    _seed_graveyard_with_draft(repo, cid="c_rej")
    out = inbox_mod.reject_draft(repo, "post_mortems/c_rej",
                                   reason="too short", rejected_by="human:test")
    assert not out["ok"]
    assert out["code"] == "reason_too_short"


def test_reject_unknown_draft(repo: Repository) -> None:
    out = inbox_mod.reject_draft(repo, "post_mortems/c_none",
                                   reason="reason long enough here",
                                   rejected_by="human:test")
    assert not out["ok"]
    assert out["code"] == "unknown_draft"


def test_reject_journals_reason(repo: Repository) -> None:
    _seed_graveyard_with_draft(repo, cid="c_rej")
    inbox_mod.reject_draft(repo, "post_mortems/c_rej",
                             reason="revise to remove speculative tone",
                             rejected_by="human:test")
    rows = repo.list_journal(tool_name="inbox.reject")
    assert rows
    inputs = json.loads(rows[0].inputs_json)
    assert "revise to remove speculative" in inputs["reason"]
