from __future__ import annotations

import argparse
from pathlib import Path

from ta_foundation.core.registry import ParserRegistry
from ta_foundation.core.pipeline import ingest_folder
from ta_foundation.parsers.ninjatrader.trades_csv import NinjaTraderTradesCsvParser
from ta_foundation.parsers.ninjatrader.analysis_by_day_csv import NinjaTraderDailyAnalysisCsvParser
from ta_foundation.parsers.ninjatrader.summary_csv import NinjaTraderSummaryCsvParser

from ta_foundation.reports.html.comparison_report import build_comparison_report
from ta_foundation.core.manifest import ManifestFileEntry, sha256_file, write_manifest
from ta_foundation.core.registry import read_header_sample
from ta_foundation.core.pipeline import ingest_folder, derive_run_id
from ta_foundation.reports.html.config import load_report_config, build_report_from_config





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


    args = ap.parse_args()

    in_dir = Path(args.input)
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    registry = ParserRegistry(parsers=[
        NinjaTraderTradesCsvParser(),
        NinjaTraderDailyAnalysisCsvParser(),
        NinjaTraderSummaryCsvParser(),
    ])

    # Ingest (multi-run)
    result = ingest_folder(
        in_dir,
        registry=registry,
        recursive=args.recursive,
        run_id_regex=args.run_id_regex,
    )



    # Build report
    cfg_path = Path(args.report_config) if args.report_config else None
    cfg = load_report_config(cfg_path)

    html, output_filename = build_report_from_config(result.packages, cfg)
    out_path = out_dir / output_filename
    out_path.write_text(html, encoding="utf-8")

    # Write unparsed list (optional)
    if result.unparsed_files:
        (out_dir / "unparsed_files.txt").write_text(
            "\n".join(str(p) for p in result.unparsed_files),
            encoding="utf-8"
        )

    # Build manifest file list with hashes and parser attribution
    pattern = "**/*{.csv,.xml}" if args.recursive else "*{.csv,.xml}"
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
