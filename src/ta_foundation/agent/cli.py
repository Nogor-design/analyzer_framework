"""ta_foundation agent CLI.

Subcommands:

    triage-pass   Triage untriaged candidates (Phase B.1).
    analyze       DEPRECATED — the Lead Strategist flow from before Phase B
                  scoping. Kept importable for one cycle; replaced by
                  role-scoped subgraphs in Phase B.

The triage-pass subcommand wires the Ollama-backed LLM call into the
role-internal `run_triage_pass`. Tests inject a stub LLM directly and bypass
this CLI.
"""

from __future__ import annotations

import argparse
import json
import sys
import warnings
from pathlib import Path
from typing import Optional


def _cmd_triage_pass(args: argparse.Namespace) -> int:
    from ta_foundation.agent.roles.triage import (
        make_ollama_llm_call,
        run_triage_pass,
    )
    from ta_foundation.research_ledger import DEFAULT_DB_PATH, get_repository

    db_path = Path(args.ledger_db) if args.ledger_db else DEFAULT_DB_PATH
    repo = get_repository(db_path)
    llm_call = make_ollama_llm_call(model=args.model, temperature=args.temperature)
    report = run_triage_pass(
        repo,
        llm_call=llm_call,
        limit=args.limit,
        max_retries=args.max_retries,
        triaged_by=args.triaged_by,
    )
    print(json.dumps(report.to_dict(), indent=2))
    return 0 if report.hitl_flagged == 0 else 1


def _cmd_daily_pass(args: argparse.Namespace) -> int:
    from ta_foundation.agent.roles.triage import make_ollama_llm_call
    from ta_foundation.agent.scheduler import daily_pass
    from ta_foundation.research_ledger import DEFAULT_DB_PATH, get_repository

    db_path = Path(args.ledger_db) if args.ledger_db else DEFAULT_DB_PATH
    repo = get_repository(db_path)
    llm_call = make_ollama_llm_call(model=args.model, temperature=args.temperature)
    report = daily_pass(
        repo, llm_call=llm_call,
        triage_limit=args.triage_limit,
        post_mortem_limit=args.post_mortem_limit,
        max_retries=args.max_retries,
    )
    print(json.dumps(report.to_dict(), indent=2))
    return 0 if not report.errors else 1


def _cmd_weekly_pass(args: argparse.Namespace) -> int:
    from ta_foundation.agent.roles.triage import make_ollama_llm_call
    from ta_foundation.agent.scheduler import weekly_pass
    from ta_foundation.research_ledger import DEFAULT_DB_PATH, get_repository

    db_path = Path(args.ledger_db) if args.ledger_db else DEFAULT_DB_PATH
    repo = get_repository(db_path)
    llm_call = make_ollama_llm_call(model=args.model, temperature=args.temperature)
    report = weekly_pass(repo, llm_call=llm_call, max_retries=args.max_retries)
    print(json.dumps(report.to_dict(), indent=2))
    return 0 if not report.errors else 1


def _cmd_shadow_scribe_pass(args: argparse.Namespace) -> int:
    """Phase D.3 — narrate today's shadow-health numbers into prose."""
    from ta_foundation.agent.roles.scribe import run_shadow_health_pass
    from ta_foundation.agent.roles.triage import make_ollama_llm_call
    from ta_foundation.research_ledger import DEFAULT_DB_PATH, get_repository

    db_path = Path(args.ledger_db) if args.ledger_db else DEFAULT_DB_PATH
    repo = get_repository(db_path)
    llm_call = make_ollama_llm_call(model=args.model, temperature=args.temperature)
    report = run_shadow_health_pass(
        repo,
        llm_call=llm_call,
        for_date=args.for_date,
        trailing_window=args.trailing_window,
        open_position_age_warn_hours=args.open_age_warn_hours,
        max_retries=args.max_retries,
    )
    print(json.dumps(report.to_dict(), indent=2))
    return 0 if not report.hitl_flagged else 1


def _cmd_authoring_pass(args: argparse.Namespace) -> int:
    from ta_foundation.agent.roles.triage import make_ollama_llm_call
    from ta_foundation.agent.scheduler import authoring_pass
    from ta_foundation.research_ledger import DEFAULT_DB_PATH, get_repository

    db_path = Path(args.ledger_db) if args.ledger_db else DEFAULT_DB_PATH
    repo = get_repository(db_path)
    llm_call = make_ollama_llm_call(model=args.model, temperature=args.temperature)
    report = authoring_pass(
        repo, llm_call=llm_call,
        n_proposals=args.n_proposals,
        session_quota=args.session_quota,
        weekly_quota=args.weekly_quota,
        max_retries=args.max_retries,
    )
    print(json.dumps(report.to_dict(), indent=2))
    return 0 if not report.errors else 1


def _cmd_operator_pass(args: argparse.Namespace) -> int:
    from ta_foundation.agent.scheduler import operator_pass
    from ta_foundation.research_ledger import DEFAULT_DB_PATH, get_repository

    db_path = Path(args.ledger_db) if args.ledger_db else DEFAULT_DB_PATH
    repo = get_repository(db_path)
    accepted_dir = Path(args.accepted_dir) if args.accepted_dir else None
    report = operator_pass(
        repo,
        input_dir=args.input_dir,
        output_dir=args.output_dir,
        accepted_dir=accepted_dir,
        market_data_root=args.market_data,
        ledger_db=args.ledger_db,
        limit=args.limit,
        timeout_seconds=args.timeout_seconds,
    )
    print(json.dumps(report.to_dict(), indent=2))
    return 0 if not report.errors else 1


def _cmd_weekly_authoring_pass(args: argparse.Namespace) -> int:
    from ta_foundation.agent.roles.triage import make_ollama_llm_call
    from ta_foundation.agent.scheduler import weekly_authoring_pass
    from ta_foundation.research_ledger import DEFAULT_DB_PATH, get_repository

    db_path = Path(args.ledger_db) if args.ledger_db else DEFAULT_DB_PATH
    repo = get_repository(db_path)
    llm_call = make_ollama_llm_call(model=args.model, temperature=args.temperature)
    accepted_dir = Path(args.accepted_dir) if args.accepted_dir else None
    report = weekly_authoring_pass(
        repo,
        llm_call=llm_call,
        input_dir=args.input_dir,
        output_dir=args.output_dir,
        n_proposals=args.n_proposals,
        session_quota=args.session_quota,
        weekly_quota=args.weekly_quota,
        author_max_retries=args.author_max_retries,
        accepted_dir=accepted_dir,
        market_data_root=args.market_data,
        ledger_db=args.ledger_db,
        operator_limit=args.operator_limit,
        timeout_seconds=args.timeout_seconds,
    )
    print(json.dumps(report.to_dict(), indent=2))
    return 0 if not report.errors else 1


def _cmd_inbox(args: argparse.Namespace) -> int:
    from ta_foundation.agent.inbox import (
        accept_draft, list_drafts, reject_draft, show_draft,
    )
    from ta_foundation.research_ledger import DEFAULT_DB_PATH, get_repository

    db_path = Path(args.ledger_db) if args.ledger_db else DEFAULT_DB_PATH
    repo = get_repository(db_path)

    if args.inbox_action == "list":
        drafts = list_drafts()
        if not drafts:
            print("(inbox empty)")
            return 0
        for d in drafts:
            tag = "  [LINT_FAIL]" if d.is_lint_failure else ""
            print(f"{d.draft_id}  ({d.artifact_type}){tag}")
        return 0
    if args.inbox_action == "show":
        body = show_draft(args.draft_id)
        if body is None:
            print(f"unknown draft: {args.draft_id}", file=sys.stderr)
            return 2
        print(body)
        return 0
    if args.inbox_action == "accept":
        out = accept_draft(repo, args.draft_id, accepted_by=args.accepted_by)
        print(json.dumps(out, indent=2))
        return 0 if out.get("ok") else 1
    if args.inbox_action == "reject":
        out = reject_draft(repo, args.draft_id, reason=args.reason,
                            rejected_by=args.rejected_by)
        print(json.dumps(out, indent=2))
        return 0 if out.get("ok") else 1
    return 2


def _cmd_analyze(args: argparse.Namespace) -> int:
    warnings.warn(
        "The 'analyze' subcommand uses the deprecated Lead Strategist graph "
        "(ta_foundation.agent.graph). It will be replaced by role-scoped "
        "subgraphs in Phase B. See docs/designs/agentic_research_program.md.",
        DeprecationWarning,
        stacklevel=2,
    )
    from langchain_core.messages import HumanMessage
    from langgraph.checkpoint.sqlite import SqliteSaver
    from ta_foundation.agent.graph import build_graph

    initial_state = {
        "messages": [HumanMessage(content=args.request)],
        "todos": [], "current_task": None,
        "instrument": args.instrument, "timeframe": args.timeframe,
        "run_id": None, "artifacts": [], "status": "starting",
    }
    config = {"configurable": {
        "thread_id": args.thread_id, "model_name": args.model,
    }}
    with SqliteSaver.from_conn_string(args.state_db) as memory:
        app = build_graph(checkpointer=memory)
        print(f"[deprecated analyze] Request: {args.request}")
        for event in app.stream(initial_state, config=config):
            for node_name, output in event.items():
                print(f"\n[Node: {node_name}]")
                if "messages" in output:
                    last_msg = output["messages"][-1]
                    if hasattr(last_msg, "content") and last_msg.content:
                        print(f"Agent: {last_msg.content}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="ta_foundation.agent", description=__doc__)
    sub = ap.add_subparsers(dest="command", required=True)

    # ---- triage-pass ----
    tp = sub.add_parser(
        "triage-pass",
        help="Triage untriaged candidates using the Ollama-backed LLM.",
    )
    tp.add_argument("--limit", type=int, default=25,
                    help="Max candidates to process this pass.")
    tp.add_argument("--max-retries", type=int, default=2,
                    help="Per-candidate LLM retries before HITL flag.")
    tp.add_argument("--model", default="llama3.1",
                    help="Ollama model name.")
    tp.add_argument("--temperature", type=float, default=0.0)
    tp.add_argument("--ledger-db", default=None,
                    help="Path to research_ledger.db.")
    tp.add_argument("--triaged-by", default="agent:triage")
    tp.set_defaults(func=_cmd_triage_pass)

    # ---- daily-pass ----
    dp = sub.add_parser(
        "daily-pass",
        help="Triage then write post-mortems for new graveyard candidates.",
    )
    dp.add_argument("--triage-limit", type=int, default=25)
    dp.add_argument("--post-mortem-limit", type=int, default=25)
    dp.add_argument("--max-retries", type=int, default=2)
    dp.add_argument("--model", default="llama3.1")
    dp.add_argument("--temperature", type=float, default=0.0)
    dp.add_argument("--ledger-db", default=None)
    dp.set_defaults(func=_cmd_daily_pass)

    # ---- weekly-pass ----
    wp = sub.add_parser(
        "weekly-pass",
        help="Compose the weekly research letter for the trailing ISO week.",
    )
    wp.add_argument("--max-retries", type=int, default=2)
    wp.add_argument("--model", default="llama3.1")
    wp.add_argument("--temperature", type=float, default=0.0)
    wp.add_argument("--ledger-db", default=None)
    wp.set_defaults(func=_cmd_weekly_pass)

    # ---- shadow-scribe-pass ----
    ssp = sub.add_parser(
        "shadow-scribe-pass",
        help=("Compose the daily shadow-health letter (Phase D.3 prose layer "
              "on top of the D.2 numbers report)."),
    )
    ssp.add_argument("--for-date", default=None,
                     help="Denver session day to narrate (YYYY-MM-DD). "
                          "Defaults to today (Denver local).")
    ssp.add_argument("--trailing-window", type=int, default=30)
    ssp.add_argument("--open-age-warn-hours", type=int, default=24,
                     help="Open shadow signals older than this trigger an "
                          "anomaly flag the letter must surface.")
    ssp.add_argument("--max-retries", type=int, default=2)
    ssp.add_argument("--model", default="llama3.1")
    ssp.add_argument("--temperature", type=float, default=0.0)
    ssp.add_argument("--ledger-db", default=None)
    ssp.set_defaults(func=_cmd_shadow_scribe_pass)

    # ---- authoring-pass ----
    ap_authoring = sub.add_parser(
        "authoring-pass",
        help="Propose new pre-registered hypotheses (C.1 Hypothesis Author).",
    )
    ap_authoring.add_argument("--n-proposals", type=int, default=5)
    ap_authoring.add_argument("--session-quota", type=int, default=5)
    ap_authoring.add_argument("--weekly-quota", type=int, default=25)
    ap_authoring.add_argument("--max-retries", type=int, default=1)
    ap_authoring.add_argument("--model", default="llama3.1")
    ap_authoring.add_argument("--temperature", type=float, default=0.2,
                                help="Slightly non-zero default; authoring is creative.")
    ap_authoring.add_argument("--ledger-db", default=None)
    ap_authoring.set_defaults(func=_cmd_authoring_pass)

    # ---- operator-pass ----
    op = sub.add_parser(
        "operator-pass",
        help="Drain accepted-hypothesis queue (C.2 Sweep Operator).",
    )
    op.add_argument("--input-dir", required=True,
                    help="NinjaTrader export folder passed to the discovery CLI.")
    op.add_argument("--output-dir", required=True,
                    help="Where each run's artifacts will be written.")
    op.add_argument("--accepted-dir", default=None,
                    help="Override for runs/proposals_accepted (default).")
    op.add_argument("--market-data", default=None,
                    help="Optional --market-data root passed through to the CLI.")
    op.add_argument("--ledger-db", default=None)
    op.add_argument("--limit", type=int, default=25,
                    help="Max hypotheses to process this pass.")
    op.add_argument("--timeout-seconds", type=int, default=1800,
                    help="Subprocess timeout for each run_probe invocation.")
    op.set_defaults(func=_cmd_operator_pass)

    # ---- weekly-authoring-pass ----
    wap = sub.add_parser(
        "weekly-authoring-pass",
        help=("Author proposals, then drain accepted hypotheses (C.4 graph: "
              "Author → inbox HITL → Operator)."),
    )
    wap.add_argument("--n-proposals", type=int, default=5)
    wap.add_argument("--session-quota", type=int, default=5)
    wap.add_argument("--weekly-quota", type=int, default=25)
    wap.add_argument("--author-max-retries", type=int, default=1)
    wap.add_argument("--input-dir", required=True)
    wap.add_argument("--output-dir", required=True)
    wap.add_argument("--accepted-dir", default=None)
    wap.add_argument("--market-data", default=None)
    wap.add_argument("--operator-limit", type=int, default=25)
    wap.add_argument("--timeout-seconds", type=int, default=1800)
    wap.add_argument("--model", default="llama3.1")
    wap.add_argument("--temperature", type=float, default=0.2)
    wap.add_argument("--ledger-db", default=None)
    wap.set_defaults(func=_cmd_weekly_authoring_pass)

    # ---- inbox ----
    ib = sub.add_parser(
        "inbox",
        help="List, show, accept, or reject Scribe drafts.",
    )
    ib.add_argument("--ledger-db", default=None)
    ib_sub = ib.add_subparsers(dest="inbox_action", required=True)
    ib_sub.add_parser("list")
    ib_show = ib_sub.add_parser("show")
    ib_show.add_argument("draft_id")
    ib_accept = ib_sub.add_parser("accept")
    ib_accept.add_argument("draft_id")
    ib_accept.add_argument("--accepted-by", default="human:operator")
    ib_reject = ib_sub.add_parser("reject")
    ib_reject.add_argument("draft_id")
    ib_reject.add_argument("--reason", required=True)
    ib_reject.add_argument("--rejected-by", default="human:operator")
    ib.set_defaults(func=_cmd_inbox)

    # ---- analyze (deprecated) ----
    az = sub.add_parser("analyze",
                         help="DEPRECATED Lead Strategist flow. Use triage-pass instead.")
    az.add_argument("--request", type=str, required=True)
    az.add_argument("--instrument", type=str)
    az.add_argument("--timeframe", type=str, default="5m")
    az.add_argument("--thread-id", type=str, default="default-thread")
    az.add_argument("--state-db", type=str, default="agent_state.db")
    az.add_argument("--model", type=str, default="llama3.1")
    az.set_defaults(func=_cmd_analyze)

    return ap


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
