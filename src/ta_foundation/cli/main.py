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
from ta_foundation.parsers.ninjatrader.optimization_csv import NinjaTraderOptimizationCsvParser
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
from ta_foundation.analysis.entry_strategies.sweep import run_candle_discovery
from ta_foundation.analysis.entry_strategies.ma_sweep import run_ma_discovery
from ta_foundation.analysis.entry_strategies.orb_sweep import run_orb_discovery
from ta_foundation.analysis.entry_strategies.bb_sweep import run_bb_discovery
from ta_foundation.analysis.entry_strategies.breakout_sweep import run_breakout_discovery
from ta_foundation.analysis.entry_strategies.pullback_sweep import run_pullback_discovery
from ta_foundation.analysis.entry_strategies.level_sweep import run_level_discovery
from ta_foundation.analysis.entry_strategies.lcr_sweep import run_lcr_discovery
from ta_foundation.analysis.entry_strategies.premarket_sweep import run_premarket_predictor
from ta_foundation.analysis.large_candle_excursion import run_large_candle_excursion
from ta_foundation.analysis.large_candle_excursion.downstream_reports import (
    build_large_candle_excursion_discovery,
    build_large_candle_excursion_findings,
)



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


def _find_generic_discovery_config(cfgs, key: str):
    for cfg in cfgs:
        raw = getattr(cfg, "raw", None)
        if isinstance(raw, dict):
            d = raw.get(key)
            if isinstance(d, dict) and d.get("enabled"):
                return d
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
    ap.add_argument("--no-tick-data", action="store_true",
                    help="Skip loading tick data files (and cache). "
                         "Use when only minute bars are needed (e.g. LCR/candle/MA/BB discovery).")

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
        NinjaTraderOptimizationCsvParser(),
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
        load_tick_data=not args.no_tick_data,
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
                    anchor_interaction_orchestrator.attach_anchor_interaction_failure(
                        pkg=pkg, reason=reason, options=anchor_config)
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
    # ENTRY STRATEGY DISCOVERY ENGINES
    # All seven modules share the same pipeline:
    #   select merged 1m bars → run sweep → attach results to packages
    # --------------------------------------------------------

    def _get_bars_1m():
        """Return the best available 1-minute bar DataFrame from the market store."""
        merged = [df for (root, contract), df in result.market.minute_bars.items()
                  if contract == ""]
        if merged:
            return merged[0]
        all_bars = list(result.market.minute_bars.values())
        return all_bars[0] if all_bars else None

    def _apply_session_filter(bars, session_filter: dict):
        """
        Slice bars to a session window.

        session_filter keys:
          hour_from    int  — start hour (inclusive), default 0
          minute_from  int  — start minute within hour_from, default 0
          hour_to      int  — end hour (exclusive), default 24

        Example — NY premarket + open (06:00–09:59 Denver):
          session_filter: {hour_from: 6, minute_from: 0, hour_to: 10}
        Example — RTH only (07:30–15:59):
          session_filter: {hour_from: 7, minute_from: 30, hour_to: 16}
        """
        if not session_filter or bars is None or bars.empty:
            return bars
        import pandas as _pd
        hour_from   = int(session_filter.get("hour_from", 0))
        minute_from = int(session_filter.get("minute_from", 0))
        hour_to     = int(session_filter.get("hour_to", 24))

        if "dt" in bars.columns:
            dt_s = _pd.to_datetime(bars["dt"])
        else:
            dt_s = _pd.to_datetime(bars.index)

        hour   = dt_s.dt.hour
        minute = dt_s.dt.minute
        if minute_from > 0:
            start_mask = (hour > hour_from) | ((hour == hour_from) & (minute >= minute_from))
        else:
            start_mask = hour >= hour_from
        mask = start_mask & (hour < hour_to)
        filtered = bars[mask.values].reset_index(drop=True)
        n_orig = len(bars)
        n_filt = len(filtered)
        if n_filt < n_orig:
            print(f"[ta_foundation]   session_filter: {n_orig:,} -> {n_filt:,} bars "
                  f"({hour_from:02d}:{minute_from:02d}-{hour_to:02d}:00)")
        return filtered

    def _run_discovery_module(key, label, run_fn, create_placeholder=False):
        """
        Run one entry-strategy discovery module.

        Parameters
        ----------
        key               : metadata key and YAML config block name
        label             : human-readable name for log messages
        run_fn            : callable(bars_1m, config) → results dict
        create_placeholder: if True and no packages exist, inject a synthetic
                            package so report sections can still render
                            (used for candle_discovery in market-data-only runs)
        """
        cfg = _find_generic_discovery_config(cfgs, key)
        if not cfg:
            print(f"[ta_foundation] No {label} configuration detected.")
            return
        print(f"[ta_foundation] Running {label}...")
        try:
            bars_1m = _get_bars_1m()
            if bars_1m is None or bars_1m.empty:
                print(f"[ta_foundation] {label} skipped: no minute bars in market store.")
                return
            # Optional session filter — slice bars to a time window before sweeping
            session_filter = cfg.get("session_filter")
            if session_filter:
                bars_1m = _apply_session_filter(bars_1m, session_filter)
                if bars_1m is None or bars_1m.empty:
                    print(f"[ta_foundation] {label} skipped: no bars after session filter.")
                    return
            disc = run_fn(bars_1m=bars_1m, config=cfg)
            print(f"[ta_foundation] {label} complete: "
                  f"{disc.get('n_combinations_run', 0)} combos, "
                  f"{disc.get('n_results', 0)} results.")
            for pkg in result.packages.values():
                pkg.metadata.setdefault("derived", {})[key] = disc
            if create_placeholder and not result.packages:
                import pandas as _pd
                from ta_foundation.core.model import AnalysisPackage
                placeholder = AnalysisPackage(
                    run_id=f"__{key}__",
                    trades=_pd.DataFrame(),
                    daily=_pd.DataFrame(),
                    summary=None,
                    settings=_pd.DataFrame(),
                )
                placeholder.metadata.setdefault("derived", {})[key] = disc
                result.packages[f"__{key}__"] = placeholder
        except Exception as e:
            print(f"[ta_foundation] WARNING {key} failed: {type(e).__name__}: {e}")
            traceback.print_exc()

    _run_discovery_module("candle_discovery",   "Candle Discovery",   run_candle_discovery,   create_placeholder=True)
    _run_discovery_module("ma_discovery",       "MA Discovery",       run_ma_discovery)
    _run_discovery_module("orb_discovery",      "ORB Discovery",      run_orb_discovery)
    _run_discovery_module("bb_discovery",       "BB Discovery",       run_bb_discovery)
    _run_discovery_module("breakout_discovery", "Breakout Discovery", run_breakout_discovery)
    _run_discovery_module("pullback_discovery", "Pullback Discovery", run_pullback_discovery)
    _run_discovery_module("level_discovery",    "Level Discovery",    run_level_discovery)
    _run_discovery_module("lcr_discovery",     "LCR Discovery",      run_lcr_discovery)
    _run_discovery_module("premarket_discovery", "Pre-Market Predictor", run_premarket_predictor, create_placeholder=True)

    # --------------------------------------------------------
    # RUN LARGE CANDLE FORWARD EXCURSION ENGINE
    # Separate from _run_discovery_module so we can pass tick data.
    # --------------------------------------------------------
    lce_cfg = _find_generic_discovery_config(cfgs, "large_candle_excursion")
    if lce_cfg:
        print("[ta_foundation] Running Large Candle Excursion analysis...")
        try:
            bars_1m_lce = _get_bars_1m()
            if bars_1m_lce is None or bars_1m_lce.empty:
                print("[ta_foundation] Large Candle Excursion skipped: no minute bars.")
            else:
                session_filter_lce = lce_cfg.get("session_filter")
                if session_filter_lce:
                    bars_1m_lce = _apply_session_filter(bars_1m_lce, session_filter_lce)

                # Provide tick data when requested
                ticks_lce = None
                if lce_cfg.get("tick_analysis", {}).get("use_tick_data", False):
                    for _key, _tdf in result.market.ticks.items():
                        if _tdf is not None and not _tdf.empty:
                            ticks_lce = _tdf
                            break

                lce_disc = run_large_candle_excursion(
                    bars_1m=bars_1m_lce,
                    config=lce_cfg,
                    ticks=ticks_lce,
                )
                print(
                    f"[ta_foundation] Large Candle Excursion complete: "
                    f"{lce_disc.get('n_combinations_run', 0)} combos, "
                    f"{lce_disc.get('n_results', 0)} results."
                )
                for pkg in result.packages.values():
                    pkg.metadata.setdefault("derived", {})["large_candle_excursion"] = lce_disc

                if not result.packages:
                    import pandas as _pd
                    from ta_foundation.core.model import AnalysisPackage
                    placeholder = AnalysisPackage(
                        run_id="__large_candle_excursion__",
                        trades=_pd.DataFrame(),
                        daily=_pd.DataFrame(),
                        summary=None,
                        settings=_pd.DataFrame(),
                    )
                    placeholder.metadata.setdefault("derived", {})["large_candle_excursion"] = lce_disc
                    result.packages["__large_candle_excursion__"] = placeholder
        except Exception as e:
            print(f"[ta_foundation] WARNING large_candle_excursion failed: {type(e).__name__}: {e}")
            traceback.print_exc()
    else:
        print("[ta_foundation] No Large Candle Excursion configuration detected.")

    # --------------------------------------------------------
    # RUN LARGE CANDLE EXCURSION DOWNSTREAM REPORT ANALYTICS
    # --------------------------------------------------------
    lce_findings_cfg = _find_generic_discovery_config(cfgs, "large_candle_excursion_findings")
    lce_discovery_cfg = _find_generic_discovery_config(cfgs, "large_candle_excursion_discovery")
    if lce_findings_cfg or lce_discovery_cfg:
        source_lce = None
        for pkg in result.packages.values():
            source_lce = (getattr(pkg, "metadata", {}) or {}).get("derived", {}).get("large_candle_excursion")
            if source_lce:
                break

        if lce_findings_cfg:
            findings = build_large_candle_excursion_findings(source_lce, lce_findings_cfg)
            for pkg in result.packages.values():
                pkg.metadata.setdefault("derived", {})["large_candle_excursion_findings"] = findings
            print(
                f"[ta_foundation] Large Candle Excursion Findings ready: "
                f"{len(findings.get('top_discoveries', []))} discoveries."
            )

        if lce_discovery_cfg:
            discovery = build_large_candle_excursion_discovery(source_lce, lce_discovery_cfg)
            for pkg in result.packages.values():
                pkg.metadata.setdefault("derived", {})["large_candle_excursion_discovery"] = discovery
            print(
                f"[ta_foundation] Large Candle Excursion Discovery ready: "
                f"{len(discovery.get('final_discoveries', []))} final discoveries."
            )

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
                optimization_store=result.optimization_store,
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
