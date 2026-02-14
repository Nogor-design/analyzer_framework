from __future__ import annotations

import argparse
import pathlib
from pathlib import Path


from ta_foundation.reports.html.export_cards import export_exec_cards_to_png
from ta_foundation.core.registry import ParserRegistry
from ta_foundation.core.pipeline import ingest_folder
from ta_foundation.parsers.ninjatrader.trades_csv import NinjaTraderTradesCsvParser
from ta_foundation.parsers.ninjatrader.analysis_by_day_csv import NinjaTraderDailyAnalysisCsvParser
from ta_foundation.parsers.ninjatrader.summary_csv import NinjaTraderSummaryCsvParser
from ta_foundation.parsers.ninjatrader.settings_csv import NinjaTraderSettingsCsvParser

from ta_foundation.reports.html.comparison_report import build_comparison_report
from ta_foundation.core.manifest import ManifestFileEntry, sha256_file, write_manifest
from ta_foundation.core.registry import read_header_sample
from ta_foundation.core.pipeline import ingest_folder, derive_run_id
from ta_foundation.reports.html.config import load_report_config, build_report_from_config
from ta_foundation.parsers.ninjatrader.minute_bars_last_txt import MinuteBarsLastTxtParser
from ta_foundation.parsers.ninjatrader.tick_last_txt import TickLastTxtParser





def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="Folder containing NinjaTrader CSV exports")
    ap.add_argument("--output", required=True, help="Output folder")
    ap.add_argument("--recursive", action="store_true", help="Search for CSVs recursively")
    ap.add_argument(
        "--run-id-regex",
        default=None,
        help="Optional regex with a capture group to extract run_id from filenames (uses group 1).",
    )
    ap.add_argument(
        "--report-config",
        default=None,
        help="Path to report YAML config (e.g., report.yaml). If omitted, defaults are used.",
    )

    # wherever argparse is defined:
    ap.add_argument(
        "--include-run-images",
        action="store_true",
        help="If set, embed <run_id>.png/jpg/webp/gif images found in the input folder into HTML sections (as base64).",
    )

    ap.add_argument(
        "--export-exec-cards-png",
        action="store_true",
        help="Export each Executive Strategy Profile card as a PNG (requires Playwright).",
    )
    ap.add_argument(
        "--exec-cards-dir",
        type=str,
        default=None,
        help="Output directory for exported exec card PNGs. Default: <output>/cards",
    )
    ap.add_argument("--market-data", default=None, help="Shared folder containing *.Last.txt minute bars")

    args = ap.parse_args()

    in_dir = Path(args.input)
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    market_folder = Path(args.market_data) if args.market_data else None
    registry = ParserRegistry(parsers=[
        NinjaTraderTradesCsvParser(),
        NinjaTraderDailyAnalysisCsvParser(),
        NinjaTraderSummaryCsvParser(),
        NinjaTraderSettingsCsvParser(),
        MinuteBarsLastTxtParser(),
        TickLastTxtParser(),
    ])

    # Ingest (multi-run)
    result = ingest_folder(
        in_dir,
        registry=registry,
        recursive=args.recursive,
        run_id_regex=args.run_id_regex,
        include_run_images=args.include_run_images,  # NEW
        market_data_folder=market_folder,  # if you add this param
    )



    # Build report
    cfg_path = Path(args.report_config) if args.report_config else None
    cfg = load_report_config(cfg_path)
    # print("market loaded:", 0 if result.market is None else len(result.market.minute_bars))

    html, output_filename = build_report_from_config(result.packages, cfg, market=result.market)
    out_path = out_dir / output_filename
    out_path.write_text(html, encoding="utf-8")

    if args.export_exec_cards_png:


        cards_dir = Path(args.exec_cards_dir) if args.exec_cards_dir else (Path(args.output) / "cards")
        res = export_exec_cards_to_png(Path(out_path), cards_dir)

        print(f"[ta_foundation] Exported {len(res.exported)} exec cards to: {res.output_dir}")
        if res.skipped:
            print("[ta_foundation] Some cards failed to export:")
            for s in res.skipped:
                print("  -", s)

    # Write unparsed list (optional)
    if result.unparsed_files:
        (out_dir / "unparsed_files.txt").write_text(
            "\n".join(str(p) for p in result.unparsed_files),
            encoding="utf-8"
        )

    # Build manifest file list with hashes and parser attribution
    pattern = "**/*.csv" if args.recursive else "*.csv"
    all_csvs = sorted(in_dir.glob(pattern))

    parsed_entries: list[ManifestFileEntry] = []
    for p in all_csvs:
        header = read_header_sample(p)
        parser = registry.find_parser(p, header)
        if parser is None:
            continue

        run_id = derive_run_id(p, run_id_regex=args.run_id_regex)
        parsed_entries.append(
            ManifestFileEntry(
                path=str(p),
                sha256=sha256_file(p),
                size_bytes=p.stat().st_size,
                run_id=run_id,
                parser_kind=getattr(parser, "kind", None),
                parser_name=parser.__class__.__name__,
            )
        )

    warnings_by_run = {rid: pkg.warnings for rid, pkg in result.packages.items()}

    write_manifest(
        out_dir / "manifest.json",
        input_folder=in_dir,
        files_parsed=parsed_entries,
        files_unparsed=result.unparsed_files,
        packages_warnings=warnings_by_run,
        extra={
            "run_id_regex": args.run_id_regex,
            "report": {
                "type": "html",
                "self_contained": True,
                "embedded_images": True,
                "output_filename": output_filename,
                "output_path": str(out_path),
                "report_config_path": str(cfg_path) if cfg_path else None,
            },
            # Optional but recommended: save the resolved config used to build the report
            "report_config_resolved": {
                "title": cfg.title,
                "output_filename": cfg.output_filename,
                "sections": cfg.sections,
            },
        },
    )

    print(f"Wrote: {out_path}")
    print(f"Wrote: {out_dir / 'manifest.json'}")
    if result.unparsed_files:
        print(f"Unparsed files: {len(result.unparsed_files)} (see unparsed_files.txt)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
