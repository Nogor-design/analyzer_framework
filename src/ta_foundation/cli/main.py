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