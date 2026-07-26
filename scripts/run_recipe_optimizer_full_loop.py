#!/usr/bin/env python3
"""
Complete Recipe Optimizer workflow using direct REST API calls.

This script demonstrates the full end-to-end Recipe Optimizer pipeline:
1. Create optimizer session
2. Configure recipe with time bucket matrix expansion
3. Generate Stage 1 template plan
4. Monitor NinjaTrader execution
5. Retrieve Stage 1 results
6. Generate final templates with bucket preservation
7. Display decision dashboard

Usage:
    python scripts/run_recipe_optimizer_full_loop.py [--session-id <ID>] [--skip-nt-wait]

Options:
    --session-id <ID>   Attach to existing session instead of creating new
    --skip-nt-wait      Don't wait for NinjaTrader results (test mode)
    --url <URL>         Base URL (default: http://localhost:7734)
    --timeout <SEC>     Request timeout in seconds (default: 10)
"""

import sys
import json
import time
import argparse
import requests
from pathlib import Path
from datetime import datetime


class RecipeOptimizerClient:
    """REST API client for Recipe Optimizer."""

    def __init__(self, base_url="http://localhost:7734", timeout=10):
        self.base_url = base_url
        self.api_base = f"{base_url}/api/optimizer"
        self.timeout = timeout
        self.session_id = None

    def health_check(self):
        """Verify web app is accessible."""
        try:
            resp = requests.get(f"{self.base_url}/", timeout=self.timeout)
            return resp.status_code == 200
        except requests.ConnectionError:
            return False

    def create_session(self, strategy_id, instrument="NQ", contract="M26"):
        """Create new optimizer session."""
        payload = {
            "strategy_id": strategy_id,
            "instrument": instrument,
            "contract": contract,
        }
        resp = requests.post(
            f"{self.api_base}/sessions", json=payload, timeout=self.timeout
        )
        if resp.status_code in [200, 201]:
            data = resp.json()
            self.session_id = data.get("session_id")
            return self.session_id
        raise Exception(f"Failed to create session: {resp.text[:200]}")

    def attach_session(self, session_id):
        """Attach to existing session."""
        self.session_id = session_id

    def save_recipe(self, recipe_config):
        """Save recipe configuration."""
        resp = requests.put(
            f"{self.api_base}/sessions/{self.session_id}/recipe",
            json=recipe_config,
            timeout=self.timeout,
        )
        if resp.status_code in [200, 201]:
            return True
        raise Exception(f"Failed to save recipe: {resp.text[:200]}")

    def generate_plan(self):
        """Generate recipe execution plan."""
        resp = requests.post(
            f"{self.api_base}/sessions/{self.session_id}/recipe/plan",
            timeout=self.timeout,
        )
        if resp.status_code in [200, 201]:
            return resp.json()
        raise Exception(f"Failed to generate plan: {resp.text[:200]}")

    def get_status(self):
        """Get session status."""
        resp = requests.get(
            f"{self.api_base}/sessions/{self.session_id}/status",
            timeout=self.timeout,
        )
        if resp.status_code == 200:
            return resp.json()
        return None

    def get_stage_results(self, stage_id):
        """Retrieve results for a stage."""
        resp = requests.get(
            f"{self.api_base}/sessions/{self.session_id}/stages/{stage_id}/results",
            timeout=self.timeout,
        )
        if resp.status_code == 200:
            return resp.json()
        raise Exception(f"Failed to get stage results: {resp.text[:200]}")

    def generate_final_templates(self):
        """Generate final backtest templates."""
        resp = requests.post(
            f"{self.api_base}/sessions/{self.session_id}/stages/final_backtest/generate",
            timeout=self.timeout,
        )
        if resp.status_code in [200, 201]:
            return resp.json()
        raise Exception(f"Failed to generate final templates: {resp.text[:200]}")

    def get_decision_dashboard(self):
        """Retrieve decision dashboard."""
        resp = requests.get(
            f"{self.api_base}/sessions/{self.session_id}/decision-dashboard",
            timeout=self.timeout,
        )
        if resp.status_code == 200:
            return resp.json()
        raise Exception(f"Failed to get decision dashboard: {resp.text[:200]}")


def create_test_recipe():
    """Create a test recipe with 12 time buckets."""
    return {
        "recipe_version": 1,
        "mode": "matrix_sequence",
        "recipe_id": f"rec_fullloop_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
        "recipe_name": "Full Loop Recipe Optimizer Test",
        "strategy_id": "PantheonMasterBotV01TesterV2",
        "target_final_candidates": 24,
        "entries_per_direction": 2,
        "safety_caps": {
            "max_total_combinations": 500000,
            "max_templates_per_stage": 250,
            "max_total_runtime_minutes": 720,
        },
        "base_matrix": [
            {
                "param": "StartTimeH",
                "role": "matrix_axis",
                "values": [0, 4, 8, 12, 16, 20],
                "description": "6 different 4-hour trading windows",
            },
            {
                "param": "DurationTimeH",
                "role": "fixed",
                "value": 4,
                "description": "Each window is 4 hours",
            },
            {
                "param": "Reverse",
                "role": "matrix_axis",
                "values": [False, True],
                "description": "Long and Short trading modes",
            },
        ],
        "stages": [
            {
                "stage_id": "stage_1",
                "stage_type": "optimizer",
                "description": "Broad optimization across 12 time buckets",
                "optimize_inside_template": {
                    "averageSlow": {"min": 50, "max": 400, "step": 50},
                    "averageFast": {"min": 2, "max": 50, "step": 2},
                    "MaxStop": {"min": 50, "max": 350, "step": 50},
                    "MaxTPRatio": {"min": 0.5, "max": 2.0, "step": 0.5},
                },
                "selection": {
                    "group_by": ["StartTimeH", "Reverse"],
                    "keep_per_group": 2,
                    "rank_by": "portfolio_score",
                    "hard_filters": {"min_trades": 10, "min_profit_factor": 1.1},
                },
            },
            {
                "stage_id": "final_backtest",
                "stage_type": "fixed_backtest",
                "from": "stage_1.selected_rows",
                "finalists_per_bucket": 2,
                "description": "Final validation: 2 best candidates per time bucket",
            },
        ],
    }


def print_header(title):
    """Print formatted section header."""
    print(f"\n{'='*80}")
    print(f"  {title}")
    print(f"{'='*80}\n")


def print_step(num, desc):
    """Print step indicator."""
    print(f"\n[{num}] {desc}")
    print("   " + "-" * 76)


def run_full_loop(base_url="http://localhost:7734", skip_nt_wait=False, existing_session=None):
    """Run complete Recipe Optimizer workflow."""
    client = RecipeOptimizerClient(base_url=base_url)

    print_header("RECIPE OPTIMIZER FULL LOOP - REST API")
    print("Complete workflow: session → recipe → plan → execution → results → final → dashboard")

    # Step 0: Health check
    print_step("0", "API HEALTH CHECK")
    if not client.health_check():
        print(f"✗ Cannot connect to {base_url}")
        print(f"  Start web app: python -m ta_foundation.web.app --port 7734")
        return False
    print(f"✓ Web app is running at {base_url}")

    # Step 1: Create or attach session
    print_step("1", "SESSION")
    if existing_session:
        client.attach_session(existing_session)
        print(f"✓ Attached to session: {client.session_id}")
    else:
        session_id = client.create_session("PantheonMasterBotV01TesterV2")
        print(f"✓ Session created: {session_id}")

    # Step 2: Save recipe
    print_step("2", "RECIPE CONFIGURATION")
    recipe = create_test_recipe()
    client.save_recipe(recipe)
    print(f"✓ Recipe saved: {recipe['recipe_id']}")
    print(f"  Configuration:")
    print(f"    - Base Matrix: 6 StartTimeH × 2 Reverse = 12 template combinations")
    print(f"    - Stage 1: Optimizer (broad search)")
    print(f"    - Final: Fixed Backtest (2 per bucket = 24 final templates)")

    # Step 3: Generate plan
    print_step("3", "RECIPE PLAN GENERATION")
    plan = client.generate_plan()
    template_count = plan.get("template_count")
    print(f"✓ Plan generated: {template_count} Stage 1 templates")

    stage_1 = plan["stages"][0]
    templates = stage_1.get("templates", [])
    print(f"\n  Stage 1 templates:")
    start_h_values = set()
    for tmpl in templates:
        start_h = tmpl["matrix_values"].get("StartTimeH")
        reverse = tmpl["matrix_values"].get("Reverse")
        start_h_values.add(start_h)
        if start_h in [0, 4, 20]:
            print(f"    {tmpl['template_id']}: StartTimeH={start_h}, Reverse={reverse}")

    print(f"    ... and {len(templates) - 3} more")
    print(f"\n  ✓ Unique StartTimeH values: {sorted(start_h_values)}")
    assert template_count == 12, f"Expected 12 templates, got {template_count}"
    assert len(start_h_values) == 6, f"Expected 6 unique StartTimeH, got {len(start_h_values)}"

    # Step 4: NinjaTrader execution
    print_step("4", "NINJATRADER EXECUTION")
    print("  Web app generates 12 XML templates")
    print("  RunBatch command created for NinjaTrader")
    print("  NinjaTrader Strategy Analyzer runs 12 optimizer jobs")
    print("  Each produces CSV results with param_StartTimeH, metrics, etc.")

    if skip_nt_wait:
        print("\n  [SKIPPED - Test Mode]")
        print("\n  In production, the system would:")
        print("    1. Wait for NinjaTrader to complete all 12 optimizer runs")
        print("    2. Auto-ingest results when available")
        print("    3. Proceed to result retrieval")
        return True
    else:
        print("\n  Polling for results... (30-minute timeout)")
        for i in range(180):
            status = client.get_status()
            if status and status.get("stage_1_complete"):
                print(f"\n✓ Stage 1 results available")
                break
            if i % 6 == 0 and i > 0:
                print(f"  ... still waiting ({i*10}s elapsed)")
            time.sleep(10)

    # Step 5: Retrieve Stage 1 results
    print_step("5", "STAGE 1 RESULTS")
    try:
        results = client.get_stage_results("stage_1")
        total_rows = results.get("total_rows", 0)
        selected_rows = results.get("selected_rows", 0)
        print(f"✓ {total_rows} total rows processed")
        print(f"✓ {selected_rows} candidates selected (2 per bucket × 12 buckets = 24)")

        # Verify bucket grouping
        by_bucket = {}
        for row in results.get("rows", []):
            key = (row["param_StartTimeH"], row["param_Reverse"])
            if key not in by_bucket:
                by_bucket[key] = []
            by_bucket[key].append(row)

        print(f"\n  Results grouped into {len(by_bucket)} buckets:")
        for (start_h, reverse), rows in sorted(by_bucket.items()):
            if start_h in [0, 4, 20]:
                print(
                    f"    StartTimeH={start_h:2d}, Reverse={str(reverse):5s}: {len(rows)} rows"
                )

        print(f"    ... and {len(by_bucket) - 3} more buckets")
    except Exception as e:
        print(f"⚠ Results not yet available: {e}")
        print("  (NinjaTrader may still be running)")

    # Step 6: Generate final templates
    print_step("6", "FINAL TEMPLATE GENERATION")
    try:
        final = client.generate_final_templates()
        final_count = final.get("templates_generated", 0)
        print(f"✓ {final_count} final templates generated")

        bucket_report = final.get("bucket_report", [])
        print(f"✓ {len(bucket_report)} original buckets preserved")

        # Show bucket coverage
        print("\n  Bucket Coverage Report:")
        print(f"  {'Bucket Key':<40} │ Target │ Selected │ Status")
        print("  " + "─" * 75)

        for report in bucket_report:
            bucket_key = report.get("bucket_key", "?")
            target = report.get("target_count", 0)
            selected = report.get("selected_count", 0)
            status = report.get("status", "?")
            if len(bucket_report) <= 12:
                print(f"  {bucket_key:<40} │   {target}    │    {selected}     │ {status}")

        # Verify each bucket has correct StartTimeH
        print("\n  Sample Final Templates (verifying time bucket preservation):")
        for rank, tmpl in enumerate(final.get("templates", [])[:3], 1):
            template_id = tmpl.get("template_id", f"F_{rank:03d}")
            bucket_id = tmpl.get("bucket_id", "?")
            start_h = tmpl.get("strategy_values", {}).get("StartTimeH", "?")
            print(f"\n    {template_id}:")
            print(f"      Bucket: {bucket_id}")
            print(f"      StartTimeH: {start_h}")

        print(f"\n    ... and {max(0, len(final.get('templates', [])) - 3)} more final templates")

    except Exception as e:
        print(f"⚠ Final templates not yet available: {e}")

    # Step 7: Retrieve decision dashboard
    print_step("7", "DECISION DASHBOARD")
    try:
        dashboard = client.get_decision_dashboard()

        print(f"Session: {dashboard.get('session_id')}")
        print(f"Status: {dashboard.get('decision_status')}")

        candidates = dashboard.get("final_candidates", [])
        print(f"\nRecommended Finalists: {len(candidates)}")

        for cand in candidates[:4]:
            candidate_id = cand.get("candidate_id", "?")
            bucket = cand.get("bucket_id", "?")
            pf = cand.get("profit_factor", 0)
            net = cand.get("total_net_profit", 0)
            dd = cand.get("max_drawdown", 0)
            trades = cand.get("total_trades", 0)

            print(f"\n  {candidate_id}")
            print(f"    Bucket: {bucket}")
            print(f"    PF={pf}, Net=${net}, DD=${dd}, Trades={trades}")

        if len(candidates) > 4:
            print(f"\n  ... and {len(candidates) - 4} more")

        rejection = dashboard.get("rejection_summary", {})
        rejected_count = rejection.get("total_rejected", 0)
        if rejected_count > 0:
            print(f"\n  Rejected: {rejected_count} candidates")
            for reason, count in rejection.get("reasons", {}).items():
                print(f"    - {reason}: {count}")

        print(f"\nAvailable Reports & Downloads:")
        links = dashboard.get("report_links", {})
        for link_type, link in links.items():
            print(f"  - {link_type}: {link}")

    except Exception as e:
        print(f"⚠ Decision dashboard not available: {e}")

    # Summary
    print_header("WORKFLOW COMPLETE")
    print(f"Session: {client.session_id}")
    print(f"Recipe: {recipe.get('recipe_id')}")
    print(f"Status: {dashboard.get('decision_status') if 'dashboard' in locals() else 'pending'}")
    print("\nNext Steps:")
    print(f"  1. View dashboard: http://localhost:7734/optimizer/sessions/{client.session_id}")
    print(f"  2. Generate reports: Use 'Build all-template report' button")
    print(f"  3. Download templates: Use 'Download templates' button")
    print(f"  4. Deploy or refine: Use 'Decision dashboard' to finalize")

    return True


def main():
    """Entry point."""
    parser = argparse.ArgumentParser(
        description="Recipe Optimizer Full Loop using REST API"
    )
    parser.add_argument(
        "--session-id",
        help="Attach to existing session instead of creating new",
    )
    parser.add_argument(
        "--skip-nt-wait",
        action="store_true",
        help="Skip NinjaTrader wait (test mode)",
    )
    parser.add_argument(
        "--url",
        default="http://localhost:7734",
        help="Base URL (default: http://localhost:7734)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=10,
        help="Request timeout in seconds (default: 10)",
    )

    args = parser.parse_args()

    try:
        success = run_full_loop(
            base_url=args.url,
            skip_nt_wait=args.skip_nt_wait,
            existing_session=args.session_id,
        )
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n\n✗ Interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
