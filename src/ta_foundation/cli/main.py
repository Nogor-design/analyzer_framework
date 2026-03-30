from __future__ import annotations

import argparse
import traceback
from pathlib import Path
from typing import Any, Optional

from ta_foundation.reports.html.export_cards import export_exec_cards_to_png
from ta_foundation.core.registry import ParserRegistry, read_header_sample
from ta_foundation.core.pipeline import ingest_folder, derive_run_id
from ta_foundation.parsers.ninjatrader.trades_csv import NinjaTraderTradesCsvParser
from ta_foundation.parsers.ninjatrader.analysis_by_day_csv import NinjaTraderDailyAnalysisCsvParser
from ta_foundation.parsers.ninjatrader.summary_csv import NinjaTraderSummaryCsvParser
from ta_foundation.parsers.ninjatrader.settings_csv import NinjaTraderSettingsCsvParser
from ta_foundation.parsers.ninjatrader.minute_bars_last_txt import MinuteBarsLastTxtParser
from ta_foundation.parsers.ninjatrader.tick_last_txt import TickLastTxtParser
from ta_foundation.core.manifest import ManifestFileEntry, sha256_file, write_manifest
from ta_foundation.reports.html.config import load_report_configs, build_report_from_config
from ta_foundation.analysis.ma_structure import orchestrator as anchor_interaction_orchestrator
from ta_foundation.analysis.strategy_discovery import orchestrator as strategy_discovery_orchestrator
from ta_foundation.analysis.strategy_discovery.nt_template_generator import (
    generate_nt_template,
    generate_per_rule_templates,
)
from ta_foundation.analysis.strategy_discovery.pantheon_bot_v2_template import (
    generate_pantheon_v2_template,
)
from ta_foundation.analysis.strategy_discovery.pantheon_master_template import (
    generate_pantheon_master_template,
)
from ta_foundation.analysis.pattern_engine.orchestrator import compute_and_attach_pattern_engine



# --------------------------------------------------------
# Locate MA Anchor configuration
# --------------------------------------------------------
def _find_anchor_interaction_config(cfgs):

    for cfg in cfgs:

        # 1️⃣ check report-level config
        # rc = getattr(cfg, "report_config", None)
        # if isinstance(rc, dict):
        #     ai = rc.get("anchor_interaction")
        raw = getattr(cfg, "raw", None)
        if isinstance(raw, dict):
            ai = raw.get("anchor_interaction")
            if isinstance(ai, dict) and ai.get("enabled"):
                return ai

        # 2️⃣ check section options
        sections = getattr(cfg, "sections", []) or []
        for s in sections:
            if not isinstance(s, dict):
                continue

            opts = s.get("options") or {}
            if not isinstance(opts, dict):
                continue

            ai = opts.get("anchor_interaction")
            if isinstance(ai, dict) and ai.get("enabled"):
                return ai

    return None


def _find_strategy_discovery_config(cfgs):
    for cfg in cfgs:
        raw = getattr(cfg, "raw", None)
        if isinstance(raw, dict):
            sd = raw.get("strategy_discovery")
            if isinstance(sd, dict) and sd.get("enabled"):
                return sd
    return None


def _find_pattern_engine_config(cfgs):
    for cfg in cfgs:
        raw = getattr(cfg, "raw", None)
        if isinstance(raw, dict):
            pe = raw.get("pattern_engine")
            if isinstance(pe, dict) and (pe.get("enabled") or (pe.get("trade_pattern_audit") or {}).get("enabled")):
                return pe
    return None


def main() -> int:

    ap = argparse.ArgumentParser()

    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--recursive", action="store_true")
    ap.add_argument("--run-id-regex", default=None)
    ap.add_argument("--report-config", default=None)
    ap.add_argument("--include-run-images", action="store_true")
    ap.add_argument("--export-exec-cards-png", action="store_true")
    ap.add_argument("--exec-cards-dir", type=str, default=None)
    ap.add_argument("--market-data", default=None)

    args = ap.parse_args()

    in_dir = Path(args.input)
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    market_folder = Path(args.market_data) if args.market_data else in_dir

    registry = ParserRegistry(parsers=[
        NinjaTraderTradesCsvParser(),
        NinjaTraderDailyAnalysisCsvParser(),
        NinjaTraderSummaryCsvParser(),
        NinjaTraderSettingsCsvParser(),
        MinuteBarsLastTxtParser(),
        TickLastTxtParser(),
    ])

    # --------------------------------------------------------
    # INGEST
    # --------------------------------------------------------

    result = ingest_folder(
        in_dir,
        registry=registry,
        recursive=args.recursive,
        run_id_regex=args.run_id_regex,
        include_run_images=args.include_run_images,
        market_data_folder=market_folder,
    )

    # --------------------------------------------------------
    # LOAD REPORT CONFIG
    # --------------------------------------------------------

    cfg_path = Path(args.report_config) if args.report_config else None

    if cfg_path:
        print(f"[ta_foundation] Loading report YAML: {cfg_path}")

    cfgs = load_report_configs(cfg_path)

    print(f"[ta_foundation] Report configs loaded: {len(cfgs)}")

    # --------------------------------------------------------
    # RUN MA ANCHOR ENGINE
    # --------------------------------------------------------
    anchor_config = _find_anchor_interaction_config(cfgs)

    if anchor_config:

        print("[ta_foundation] Running MA Anchor analysis...")

        for run_id, pkg in result.packages.items():

            try:

                res = anchor_interaction_orchestrator.run_anchor_interaction_analysis(
                    pkg=pkg,
                    market=result.market,
                    options=anchor_config,
                )

                if res.get("ok"):
                    print(f"[ta_foundation] MA Anchor attached for {run_id}")
                else:
                    print(f"[ta_foundation] MA Anchor skipped for {run_id}: {res.get('reason')}")

            except Exception as e:
                reason = f"anchor_interaction_exception: {type(e).__name__}: {e}"
                if hasattr(anchor_interaction_orchestrator, "attach_anchor_interaction_failure"):
                    anchor_interaction_orchestrator.attach_anchor_interaction_failure(pkg=pkg, reason=reason,
                                                                                      options=anchor_config)

                reason = f"anchor_interaction_exception: {type(e).__name__}: {e}"
                if hasattr(anchor_interaction_orchestrator, "attach_anchor_interaction_failure"):
                    anchor_interaction_orchestrator.attach_anchor_interaction_failure(pkg=pkg, reason=reason, options=anchor_config)

                print(
                    f"[ta_foundation] WARNING anchor_interaction failed "
                    f"for {run_id}: {type(e).__name__}: {e}"
                )
                traceback.print_exc()

    else:

        print("[ta_foundation] No MA Anchor configuration detected.")

    # --------------------------------------------------------
    # RUN PATTERN ENGINE
    # Must run before Strategy Discovery so that pkg.assets["pattern_engine"]
    # (signals, outcomes, pattern_stats, patterns) is available when the
    # strategy discovery orchestrator builds the signal feature matrix.
    # --------------------------------------------------------
    pe_config = _find_pattern_engine_config(cfgs)

    if pe_config:
        print("[ta_foundation] Running Pattern Engine...")
        try:
            compute_and_attach_pattern_engine(
                packages=result.packages,
                market=result.market,
                options=pe_config,
            )
            print("[ta_foundation] Pattern Engine complete.")
        except Exception as e:
            print(
                f"[ta_foundation] WARNING pattern_engine failed: "
                f"{type(e).__name__}: {e}"
            )
            traceback.print_exc()
    else:
        print("[ta_foundation] No Pattern Engine configuration detected.")

    # --------------------------------------------------------
    # RUN STRATEGY DISCOVERY ENGINE
    # --------------------------------------------------------
    sd_config = _find_strategy_discovery_config(cfgs)

    if sd_config:
        print("[ta_foundation] Running Strategy Discovery analysis...")
        try:
            strategy_discovery_orchestrator.run_strategy_discovery(
                packages=result.packages,
                market=result.market,
                options=sd_config,
            )
            print("[ta_foundation] Strategy Discovery complete.")
        except Exception as e:
            print(
                f"[ta_foundation] WARNING strategy_discovery failed: "
                f"{type(e).__name__}: {e}"
            )
            traceback.print_exc()

        # Write NinjaTrader template XML files for each run
        try:
            cost_model = sd_config.get("cost_model") or {}
            tick_value = float(cost_model.get("tick_value") or 5.0)
            tick_size = float(sd_config.get("tick_size") or 0.25)
            for run_id, pkg in result.packages.items():
                sd = (getattr(pkg, "metadata", {}) or {}).get("derived", {}).get("strategy_discovery", {})
                if not sd:
                    continue
                tmpl = generate_nt_template(
                    sd,
                    run_id=run_id,
                    options={"tick_value": tick_value, "tick_size": tick_size},
                )
                safe_id = "".join(c if c.isalnum() or c in "-_." else "_" for c in str(run_id))
                xml_path = out_dir / f"nt_template_{safe_id}.xml"
                xml_path.write_text(tmpl.xml_str, encoding="utf-8")
                print(f"Wrote NT template: {xml_path}")
                if tmpl.warnings:
                    for w in tmpl.warnings:
                        print(f"  [NT template warning] {w}")

                # PantheonBotV2 companion template
                try:
                    v2_tmpl = generate_pantheon_v2_template(
                        sd,
                        run_id=str(run_id),
                        options={"tick_value": tick_value, "tick_size": tick_size},
                    )
                    v2_path = out_dir / f"nt_v2_{safe_id}.xml"
                    v2_path.write_text(v2_tmpl.xml_str, encoding="utf-8")
                    print(f"Wrote PantheonBotV2 template: {v2_path}")
                    if v2_tmpl.warnings:
                        for w in v2_tmpl.warnings:
                            print(f"  [PantheonBotV2 warning] {w}")
                except Exception as v2_exc:
                    print(
                        f"  [PantheonBotV2 template] failed: "
                        f"{type(v2_exc).__name__}: {v2_exc}"
                    )

                # PantheonMaster template
                try:
                    pm_tmpl = generate_pantheon_master_template(
                        sd,
                        run_id=str(run_id),
                        options={"tick_value": tick_value, "tick_size": tick_size},
                    )
                    pm_path = out_dir / f"nt_pantheon_master_{safe_id}.xml"
                    pm_path.write_text(pm_tmpl.xml_str, encoding="utf-8")
                    print(f"Wrote PantheonMaster template: {pm_path}")
                    if pm_tmpl.warnings:
                        for w in pm_tmpl.warnings:
                            print(f"  [PantheonMaster warning] {w}")
                except Exception as pm_exc:
                    print(
                        f"  [PantheonMaster template] failed: "
                        f"{type(pm_exc).__name__}: {pm_exc}"
                    )

                # Per-rule templates for market_discovery packages
                if str(run_id).startswith("__market_discovery__"):
                    try:
                        per_rule = generate_per_rule_templates(
                            sd,
                            run_id=run_id,
                            options={"tick_value": tick_value, "tick_size": tick_size},
                            max_rules=8,
                        )
                        rules_dir = out_dir / f"nt_rules_{safe_id}"
                        if per_rule:
                            rules_dir.mkdir(parents=True, exist_ok=True)
                        for pr in per_rule:
                            # Build a filename-safe label from the rule string
                            rule_slug = "".join(
                                c if c.isalnum() or c in "-_" else "_"
                                for c in pr.rule_str[:50]
                            ).strip("_")
                            rule_path = rules_dir / f"rule{pr.rule_rank:02d}_{rule_slug}.xml"
                            rule_path.write_text(pr.template.xml_str, encoding="utf-8")
                            print(
                                f"  Wrote per-rule template: rule{pr.rule_rank} "
                                f"S={pr.best_stop}/T={pr.best_target} -> {rule_path.name}"
                            )
                    except Exception as pr_exc:
                        print(
                            f"  [NT template] per-rule generation failed: "
                            f"{type(pr_exc).__name__}: {pr_exc}"
                        )
        except Exception as e:
            print(f"[ta_foundation] WARNING NT template generation failed: {type(e).__name__}: {e}")
            traceback.print_exc()

    else:
        print("[ta_foundation] No Strategy Discovery configuration detected.")

    # --------------------------------------------------------
    # BUILD REPORTS
    # --------------------------------------------------------

    written_reports = []
    resolved_configs = []

    for i, cfg in enumerate(cfgs):

        print(
            f"[ta_foundation] Report[{i}] resolved "
            f"output_filename={cfg.output_filename!r} "
            f"title={cfg.title!r}"
        )

        try:

            html, output_filename = build_report_from_config(
                result.packages,
                cfg,
                market=result.market,
            )

            out_path = out_dir / output_filename

            out_path.write_text(html, encoding="utf-8")

            print(f"Wrote: {out_path}")

            written_reports.append({
                "output_filename": output_filename,
                "output_path": str(out_path),
                "title": cfg.title,
            })

            resolved_configs.append({
                "title": cfg.title,
                "output_filename": cfg.output_filename,
                "sections": cfg.sections,
            })

        except Exception as e:
            print(
                f"[ta_foundation] ERROR rendering Report[{i}] "
                f"({cfg.title!r} -> {cfg.output_filename!r}): "
                f"{type(e).__name__}: {e}"
            )

            continue

    # --------------------------------------------------------
    # EXPORT CARDS
    # --------------------------------------------------------

    if args.export_exec_cards_png and written_reports:

        first_report_path = Path(written_reports[0]["output_path"])

        cards_dir = Path(args.exec_cards_dir) if args.exec_cards_dir else (out_dir / "cards")

        res = export_exec_cards_to_png(first_report_path, cards_dir)

        print(f"[ta_foundation] Exported {len(res.exported)} exec cards to: {res.output_dir}")

    # --------------------------------------------------------
    # WRITE MANIFEST
    # --------------------------------------------------------

    write_manifest(
        out_dir / "manifest.json",
        input_folder=in_dir,
        files_parsed=[],
        files_unparsed=result.unparsed_files,
        packages_warnings={rid: pkg.warnings for rid, pkg in result.packages.items()},
        extra={},
    )

    print(f"Wrote: {out_dir / 'manifest.json'}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())