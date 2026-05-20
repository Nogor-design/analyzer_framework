from __future__ import annotations

import argparse
import time
import traceback
from pathlib import Path
from typing import Any, Optional

from ta_foundation.reports.html.export_cards import export_exec_cards_to_png
from ta_foundation.reports.text.export_summary_text import export_summary_text
from ta_foundation.reports.text.export_large_candle_regime_summary import export_large_candle_regime_summary
from ta_foundation.reports.text.export_strategy_momentum_text import export_strategy_momentum_text
from ta_foundation.reports.text.export_strategy_lifecycle_text import (
    export_strategy_lifecycle_json,
    export_strategy_lifecycle_text,
)
from ta_foundation.reports.text.export_strategy_session_momentum_text import export_strategy_session_momentum_text
from ta_foundation.reports.text.export_daily_winner_text import export_daily_winner_text
from ta_foundation.reports.text.export_deployment_board_text import export_deployment_board_text
from ta_foundation.reports.text.export_strategy_parameter_matrix_text import export_strategy_parameter_matrix_text
from ta_foundation.reports.text.export_weekly_leaderboard_text import export_weekly_leaderboard_text
from ta_foundation.reports.text.export_strategy_blueprints_json import export_strategy_blueprints_json
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
from ta_foundation.web.discovery_summary import write_sidecar_for_run
from ta_foundation.web.conditional_promotion import generate_conditional_probe_yamls



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


def _find_discovery_instrument(cfgs) -> str:
    for cfg in cfgs:
        raw = getattr(cfg, "raw", None)
        if isinstance(raw, dict):
            discovery = raw.get("discovery")
            if isinstance(discovery, dict):
                instrument = str(discovery.get("instrument") or "").strip()
                if instrument:
                    return instrument
    return ""


def _select_bars_1m_from_market(market: Any, preferred_root: str = ""):
    """Return 1-minute bars, preferring the YAML discovery instrument."""
    minute_bars = getattr(market, "minute_bars", None) or {}
    if not minute_bars:
        return None

    preferred = str(preferred_root or "").strip().upper()
    if preferred:
        merged = [
            df for (root, contract), df in minute_bars.items()
            if str(root).upper() == preferred and contract == ""
        ]
        if merged:
            return merged[0]
        specific = [
            df for (root, _contract), df in minute_bars.items()
            if str(root).upper() == preferred
        ]
        if specific:
            return specific[0]

    merged_any = [df for (_root, contract), df in minute_bars.items() if contract == ""]
    if merged_any:
        return merged_any[0]
    all_bars = list(minute_bars.values())
    return all_bars[0] if all_bars else None


def main() -> int:
    import sys
    if any(arg == "--shadow-pass" for arg in sys.argv[1:]):
        return _shadow_pass_main(sys.argv[1:])
    if any(arg == "--shadow-health" for arg in sys.argv[1:]):
        return _shadow_health_main(sys.argv[1:])

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
    ap.add_argument("--db-path", default=None,
                    help="Path to DuckDB experiment registry file. "
                         "When provided, validation results are persisted automatically.")
    ap.add_argument("--hypothesis-id", default=None,
                    help="Pre-registered hypothesis ID. When provided, the report-config "
                         "YAML must contain a 'pre_registration:' block whose hypothesis_id "
                         "matches and whose params hash matches the ledger row. The run "
                         "aborts with a non-zero exit code on drift "
                         "(see docs/designs/agentic_phase_a_foundation.md A.3).")
    ap.add_argument("--ledger-db", default=None,
                    help="Path to research ledger SQLite file (default: "
                         ".ta_artifacts/research_ledger.db). Only consulted when "
                         "--hypothesis-id is provided.")
    ap.add_argument("--override-graveyard", default=None,
                    help="Override a graveyard-registry refusal with a written reason. "
                         "Required when the probe-identity hash near-matches an entry in "
                         "<output>/_graveyard_registry.json. The reason is logged to the "
                         "graveyard record for audit. Free-form, but should explain what "
                         "differs about the new run (e.g. 'wider-stop-budget-different-hypothesis').")
    ap.add_argument("--skip-registry", action="store_true",
                    help="Skip both the pre-run graveyard refusal check and the post-run "
                         "probe-registry update. Use for one-shot scripts or tests; never "
                         "in normal operation.")

    args = ap.parse_args()

    # ----- Pre-registration drift check (fail fast before any heavy work) ----
    if args.hypothesis_id:
        if not args.report_config:
            print("[ta_foundation] --hypothesis-id requires --report-config "
                  "pointing to a YAML with a 'pre_registration:' block.")
            return 2
        from ta_foundation.research_ledger import (
            DEFAULT_DB_PATH,
            check_yaml_file,
            get_repository,
        )
        ledger_db = Path(args.ledger_db) if args.ledger_db else DEFAULT_DB_PATH
        report_yaml_path = Path(args.report_config)
        if not report_yaml_path.is_file():
            print(f"[ta_foundation] --report-config not found: {report_yaml_path}")
            return 2
        repo = get_repository(ledger_db)
        try:
            report = check_yaml_file(repo, report_yaml_path, required=True)
        except ValueError as exc:
            print(f"[ta_foundation] Pre-registration block malformed: {exc}")
            return 2
        if report is None or not report.ok:
            reason = report.reason if report else "no pre_registration block"
            print(f"[ta_foundation] Pre-registration drift check FAILED: {reason}")
            repo.journal(
                role="cli",
                tool_name="pre_registration_drift_check",
                inputs={
                    "hypothesis_id": args.hypothesis_id,
                    "report_config": str(args.report_config),
                },
                output_summary=reason if report else "missing block",
                duration_ms=0,
                error="drift" if report and not report.ok else "missing_block",
            )
            return 3
        if report.hypothesis_id != args.hypothesis_id:
            print(f"[ta_foundation] --hypothesis-id={args.hypothesis_id!r} does not match "
                  f"YAML pre_registration.hypothesis_id={report.hypothesis_id!r}")
            return 3
        print(f"[ta_foundation] Pre-registration check OK for {report.hypothesis_id} "
              f"(params_hash={report.yaml_params_hash[:12] if report.yaml_params_hash else '?'})")

    run_start_ts = time.monotonic()

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
    discovery_instrument = _find_discovery_instrument(cfgs)

    # --------------------------------------------------------
    # GRAVEYARD REFUSAL CHECK (P0-HASH)
    # Computes probe-identity hash from the loaded YAML and refuses runs
    # that near-match an existing graveyard entry unless --override-graveyard
    # is provided with a written reason. See
    # docs/designs/real_edge_discovery_program.md → P0-HASH.
    # --------------------------------------------------------
    if not args.skip_registry and cfgs and cfg_path is not None:
        from ta_foundation.discovery_registry import (
            GraveyardRegistry,
            check_graveyard,
            compute_probe_identity,
        )
        from ta_foundation.discovery_registry.refusal import (
            build_yaml_resolver,
        )

        proposed_identity = compute_probe_identity(cfgs[0].raw)
        if proposed_identity.signals:
            graveyard = GraveyardRegistry(out_dir)
            resolver = build_yaml_resolver()
            hit = check_graveyard(
                proposed_identity,
                graveyard,
                identity_resolver=resolver,
            )
            if hit is not None:
                if args.override_graveyard:
                    print(f"[ta_foundation] Graveyard hit overridden: {hit.explain()}")
                    print(f"[ta_foundation] Override reason: {args.override_graveyard}")
                    graveyard.record_override(
                        h=hit.matched_record.hash,
                        reason=args.override_graveyard,
                        yaml_path=str(cfg_path).replace("\\", "/"),
                    )
                else:
                    print("[ta_foundation] REFUSED: probe identity matches a graveyarded entry.")
                    print(f"[ta_foundation]   {hit.explain()}")
                    print(
                        "[ta_foundation]   To proceed anyway, re-run with "
                        "--override-graveyard \"<reason explaining why this differs>\"."
                    )
                    return 4

            # ----------------------------------------------------
            # CUMULATIVE PENALTY (P0-CUMULATIVE)
            # Mutate hardening.n_hypotheses_tested to reflect cross-probe
            # cumulative count, family-filtered with decay. YAML value is
            # preserved under yaml_n_hypotheses_tested for audit.
            # ----------------------------------------------------
            from ta_foundation.discovery_registry.refusal import (
                compute_effective_n_hypotheses,
                inject_effective_penalty,
            )
            effective_info = compute_effective_n_hypotheses(out_dir, proposed_identity)
            mutations = inject_effective_penalty(cfgs[0].raw, effective_info)
            for m in mutations:
                print(
                    f"[ta_foundation]   cumulative penalty: {m['block']}.hardening."
                    f"n_hypotheses_tested {m['yaml_n']} -> {m['effective_n']} "
                    f"(family cumulative={m['cumulative_family']}, decay={m['decay_factor']})"
                )

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
        return _select_bars_1m_from_market(result.market, discovery_instrument)

    _regime_bars_cache = {}

    def _get_bars_with_regime():
        """Regime-labelled 1m bars, computed once and shared across every
        discovery module. Labels are derived from the full continuous series
        (never a session-sliced one); sweeps join trades to it by entry_time,
        so it only has to span the trade timestamps. Without this the hardening
        regime-scoping gate and the per-candidate regime breakdown stay inert.
        """
        if "bars" not in _regime_bars_cache:
            base = _get_bars_1m()
            labelled = None
            if base is not None and not base.empty:
                try:
                    from ta_foundation.analysis.strategy_discovery.regime import (
                        compute_bar_regime,
                    )
                    labelled = compute_bar_regime(base)
                except Exception as e:
                    print(
                        f"[ta_foundation] WARNING regime labelling failed: "
                        f"{type(e).__name__}: {e}"
                    )
            _regime_bars_cache["bars"] = labelled
        return _regime_bars_cache["bars"]

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
            if session_filter and key != "level_discovery":
                bars_1m = _apply_session_filter(bars_1m, session_filter)
                if bars_1m is None or bars_1m.empty:
                    print(f"[ta_foundation] {label} skipped: no bars after session filter.")
                    return
            elif session_filter and key == "level_discovery":
                print(
                    "[ta_foundation]   session_filter: preserving full bars for "
                    "level context; filtering emitted signals inside level_discovery."
                )
            import inspect
            run_kwargs = {"bars_1m": bars_1m, "config": cfg}
            if "bars_with_regime" in inspect.signature(run_fn).parameters:
                run_kwargs["bars_with_regime"] = _get_bars_with_regime()
            disc = run_fn(**run_kwargs)
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
    _run_discovery_module("ma_discovery",       "MA Discovery",       run_ma_discovery,       create_placeholder=True)
    _run_discovery_module("orb_discovery",      "ORB Discovery",      run_orb_discovery,      create_placeholder=True)
    _run_discovery_module("bb_discovery",       "BB Discovery",       run_bb_discovery,       create_placeholder=True)
    _run_discovery_module("breakout_discovery", "Breakout Discovery", run_breakout_discovery, create_placeholder=True)
    _run_discovery_module("pullback_discovery", "Pullback Discovery", run_pullback_discovery, create_placeholder=True)
    _run_discovery_module("level_discovery",    "Level Discovery",    run_level_discovery,    create_placeholder=True)
    _run_discovery_module("lcr_discovery",      "LCR Discovery",      run_lcr_discovery,      create_placeholder=True)
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
                db_path=args.db_path,
                output_dir=out_dir,
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

            # Write the Discovery sidecar JSON when the YAML opted in via a
            # top-level `discovery:` block. This is what powers the web
            # Discovery UI's Setup Cards and cross-stage promotion.
            discovery_block = (cfg.raw or {}).get("discovery") if isinstance(cfg.raw, dict) else None
            is_discovery_run = isinstance(discovery_block, dict) and bool(discovery_block.get("stage"))
            try:
                if isinstance(discovery_block, dict):
                    # Pull each family's per-block YAML config so the sidecar
                    # writer can recover the session_filter (and similar
                    # block-level fields) that the sweep orchestrators consume
                    # but don't echo back onto result rows.
                    family_block_keys = (
                        "candle_discovery", "ma_discovery", "orb_discovery",
                        "bb_discovery", "lcr_discovery", "breakout_discovery",
                        "pullback_discovery", "level_discovery",
                        "premarket_discovery", "large_candle_excursion",
                    )
                    family_configs: dict[str, dict] = {}
                    raw_cfg = cfg.raw or {}
                    if isinstance(raw_cfg, dict):
                        for key in family_block_keys:
                            block = raw_cfg.get(key)
                            if isinstance(block, dict):
                                # Strip the `_discovery` suffix to match the
                                # family ids the summary writer keys on.
                                family_id = key[:-len("_discovery")] if key.endswith("_discovery") else key
                                family_configs[family_id] = block
                    sidecar_path = write_sidecar_for_run(
                        packages=result.packages,
                        discovery_block=discovery_block,
                        report_html_path=out_path,
                        runtime_seconds=int(time.monotonic() - run_start_ts),
                        bars_1m=_get_bars_1m(),
                        family_configs=family_configs,
                    )
                    if sidecar_path is not None:
                        print(f"Wrote: {sidecar_path}")
                        if args.report_config:
                            generated = generate_conditional_probe_yamls(
                                parent_config_path=args.report_config,
                                sidecar_path=sidecar_path,
                                generated_dir=Path("discovery") / "generated",
                            )
                            if generated:
                                print(
                                    "[ta_foundation] generated conditional probes: "
                                    + ", ".join(str(p) for p in generated)
                                )
                    else:
                        print(
                            "[ta_foundation] discovery sidecar skipped: "
                            "discovery block missing stage/instrument or referencing unknown id."
                        )
            except Exception as _sc_exc:
                print(
                    f"[ta_foundation] WARNING: discovery sidecar write failed - "
                    f"{type(_sc_exc).__name__}: {_sc_exc}"
                )

            # Write a condensed plain-text summary alongside the HTML report.
            # This file is small and LLM-friendly — paste it directly into any
            # AI chat to discuss the run results without loading the full HTML.
            #
            # Discovery stages don't have backtest packages — every field would
            # render as an em-dash. The structured discovery sidecar already
            # carries the diagnostic data, so skip the placeholder TXT entirely.
            if is_discovery_run:
                print(
                    f"[ta_foundation] skipping text executive summary for "
                    f"discovery run ({discovery_block.get('stage')}); "
                    f"see the discovery sidecar JSON for diagnostics."
                )
            else:
                try:
                    txt_path = out_path.with_suffix(".txt")
                    export_summary_text(result.packages, txt_path, title=cfg.title)
                    print(f"Wrote: {txt_path}")
                except Exception as _txt_exc:
                    print(f"[ta_foundation] WARNING: text summary export failed - {_txt_exc}")

            try:
                lce_summary_path = out_path.with_name(
                    out_path.stem + "_executive_summary.md"
                )
                if export_large_candle_regime_summary(result.packages, lce_summary_path, title=cfg.title):
                    print(f"Wrote: {lce_summary_path}")
            except Exception as _lce_sum_exc:
                print(f"[ta_foundation] WARNING: large-candle executive summary export failed - {_lce_sum_exc}")

            try:
                blueprints_path = out_path.with_name(
                    out_path.stem + "_blueprints.json"
                )
                if export_strategy_blueprints_json(result.packages, blueprints_path, title=cfg.title):
                    print(f"Wrote: {blueprints_path}")
            except Exception as _bp_exc:
                print(f"[ta_foundation] WARNING: strategy blueprints JSON export failed - {_bp_exc}")

            # Write a condensed plain-text weekly leaderboard summary.
            # Options are pulled from the weekly_leaderboard_cards section in the
            # report config so the text matches what the card shows exactly.
            # Skipped for discovery runs (no trades -> empty placeholder).
            if not is_discovery_run:
                try:
                    weekly_opts: dict = {}
                    for sec in (cfg.sections or []):
                        if isinstance(sec, dict) and sec.get("id") == "weekly_leaderboard_cards":
                            weekly_opts = sec.get("options") or {}
                            break
                    weekly_txt_path = out_path.with_name(
                        out_path.stem + "_weekly_leaderboard.txt"
                    )
                    export_weekly_leaderboard_text(
                        result.packages,
                        weekly_txt_path,
                        options=weekly_opts,
                        title=cfg.title,
                    )
                    print(f"Wrote: {weekly_txt_path}")
                except Exception as _wk_exc:
                    print(f"[ta_foundation] WARNING: weekly leaderboard text export failed - {_wk_exc}")

            try:
                momentum_opts: dict | None = None
                for sec in (cfg.sections or []):
                    if isinstance(sec, dict) and sec.get("id") == "strategy_momentum_board":
                        momentum_opts = sec.get("options") or {}
                        break
                if momentum_opts is not None:
                    momentum_txt_path = out_path.with_name(
                        out_path.stem + "_strategy_momentum.txt"
                    )
                    export_strategy_momentum_text(
                        result.packages,
                        momentum_txt_path,
                        options=momentum_opts,
                        title=cfg.title,
                    )
                    print(f"Wrote: {momentum_txt_path}")
            except Exception as _mom_exc:
                print(f"[ta_foundation] WARNING: strategy momentum text export failed — {_mom_exc}")

            try:
                lifecycle_opts: dict | None = None
                for sec in (cfg.sections or []):
                    if isinstance(sec, dict) and sec.get("id") == "strategy_lifecycle_board":
                        lifecycle_opts = sec.get("options") or {}
                        break
                if lifecycle_opts is not None:
                    lifecycle_txt_path = out_path.with_name(
                        out_path.stem + "_strategy_lifecycle.txt"
                    )
                    export_strategy_lifecycle_text(
                        result.packages,
                        lifecycle_txt_path,
                        options=lifecycle_opts,
                        title=cfg.title,
                    )
                    print(f"Wrote: {lifecycle_txt_path}")
                    lifecycle_json_path = out_path.with_name(
                        out_path.stem + "_strategy_lifecycle.json"
                    )
                    export_strategy_lifecycle_json(
                        result.packages,
                        lifecycle_json_path,
                        options=lifecycle_opts,
                    )
                    print(f"Wrote: {lifecycle_json_path}")
            except Exception as _life_exc:
                print(f"[ta_foundation] WARNING: strategy lifecycle export failed - {_life_exc}")

            try:
                session_momentum_opts: dict | None = None
                for sec in (cfg.sections or []):
                    if isinstance(sec, dict) and sec.get("id") == "strategy_session_momentum_board":
                        session_momentum_opts = sec.get("options") or {}
                        break
                if session_momentum_opts is not None:
                    session_momentum_txt_path = out_path.with_name(
                        out_path.stem + "_strategy_session_momentum.txt"
                    )
                    export_strategy_session_momentum_text(
                        result.packages,
                        session_momentum_txt_path,
                        options=session_momentum_opts,
                        title=cfg.title,
                    )
                    print(f"Wrote: {session_momentum_txt_path}")
            except Exception as _sess_exc:
                print(f"[ta_foundation] WARNING: strategy session momentum text export failed — {_sess_exc}")

            try:
                parameter_matrix_opts: dict | None = None
                for sec in (cfg.sections or []):
                    if isinstance(sec, dict) and sec.get("id") == "strategy_parameter_matrix":
                        parameter_matrix_opts = sec.get("options") or {}
                        break
                if parameter_matrix_opts is not None:
                    parameter_matrix_txt_path = out_path.with_name(
                        out_path.stem + "_strategy_parameter_matrix.txt"
                    )
                    export_strategy_parameter_matrix_text(
                        result.packages,
                        parameter_matrix_txt_path,
                        options=parameter_matrix_opts,
                        title=cfg.title,
                    )
                    print(f"Wrote: {parameter_matrix_txt_path}")
            except Exception as _param_exc:
                print(f"[ta_foundation] WARNING: strategy parameter matrix text export failed â€” {_param_exc}")

            try:
                daily_winner_opts: dict | None = None
                for sec in (cfg.sections or []):
                    if isinstance(sec, dict) and sec.get("id") == "daily_winner_spotlight":
                        daily_winner_opts = sec.get("options") or {}
                        break
                if daily_winner_opts is not None:
                    daily_winner_txt_path = out_path.with_name(
                        out_path.stem + "_daily_winner.txt"
                    )
                    export_daily_winner_text(
                        result.packages,
                        daily_winner_txt_path,
                        options=daily_winner_opts,
                        title=cfg.title,
                    )
                    print(f"Wrote: {daily_winner_txt_path}")
            except Exception as _daily_exc:
                print(f"[ta_foundation] WARNING: daily winner text export failed — {_daily_exc}")

            try:
                deployment_board_opts: dict | None = None
                for sec in (cfg.sections or []):
                    if isinstance(sec, dict) and sec.get("id") in {"deployment_board_insight", "deployment_board_gods", "deployment_board_poster"}:
                        deployment_board_opts = sec.get("options") or {}
                        break
                if deployment_board_opts is not None:
                    deployment_board_txt_path = out_path.with_name(
                        out_path.stem + "_deployment_board.txt"
                    )
                    export_deployment_board_text(
                        result.packages,
                        deployment_board_txt_path,
                        options=deployment_board_opts,
                        title=cfg.title,
                    )
                    print(f"Wrote: {deployment_board_txt_path}")
            except Exception as _deploy_exc:
                print(f"[ta_foundation] WARNING: deployment board text export failed - {_deploy_exc}")

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

        try:
            res = export_exec_cards_to_png(first_report_path, cards_dir)
            print(f"[ta_foundation] Exported {len(res.exported)} exec cards to: {res.output_dir}")
            if res.skipped:
                for msg in res.skipped:
                    print(f"[ta_foundation] WARNING skipped card: {msg}")
        except Exception as exc:
            print(f"[ta_foundation] WARNING: exec-card PNG export failed — {exc}")
            print("[ta_foundation] The HTML report was still written successfully.")

    # --------------------------------------------------------
    # WRITE MANIFEST
    # --------------------------------------------------------

    # When the run was driven by a `discovery:` block, plain backtest manifests
    # are misleading: parsed_files / unparsed_files refer to NinjaTrader
    # exports, but discovery loads market data instead. Surface that
    # explicitly so the manifest doesn't look broken.
    manifest_extra: dict = {}
    if is_discovery_run:
        market = getattr(result, "market", None)
        minute_bar_keys: list = []
        total_bars = 0
        if market is not None and getattr(market, "minute_bars", None):
            for (root, contract), df in market.minute_bars.items():
                try:
                    n_bars = int(len(df))
                except TypeError:
                    n_bars = 0
                total_bars += n_bars
                minute_bar_keys.append({
                    "instrument_root": str(root or ""),
                    "contract": str(contract or ""),
                    "bar_count": n_bars,
                })
        manifest_extra["discovery"] = {
            "stage_id": str(discovery_block.get("stage")),
            "instrument": str(discovery_block.get("instrument") or ""),
            "note": (
                "This run is a discovery sweep over market data. "
                "parsed_files / unparsed_files refer to NinjaTrader backtest "
                "exports and are intentionally empty here. The actual diagnostic "
                "data lives in the per-stage discovery summary JSON sidecar."
            ),
            "market_data": {
                "minute_bar_artifacts": minute_bar_keys,
                "total_minute_bars": total_bars,
            },
        }

    write_manifest(
        out_dir / "manifest.json",
        input_folder=in_dir,
        files_parsed=[],
        files_unparsed=result.unparsed_files,
        packages_warnings={rid: pkg.warnings for rid, pkg in result.packages.items()},
        extra=manifest_extra,
    )

    print(f"Wrote: {out_dir / 'manifest.json'}")

    # --------------------------------------------------------
    # POST-RUN REGISTRY UPDATE (P0-HASH / P0-CUMULATIVE)
    # Append the just-written sidecar to the probe registry (and graveyard
    # registry if applicable). Idempotent — re-runs are no-ops.
    # --------------------------------------------------------
    if not args.skip_registry:
        from ta_foundation.discovery_registry.refusal import post_run_register
        post_run_register(out_dir)

    return 0


def _shadow_pass_main(argv: list[str]) -> int:
    """Forward-shadow pass entry point. Invoked when --shadow-pass is set.

    Loads bars from --market-data, then iterates every candidate currently
    in triage_state='shadow', emits/insets shadow_signals rows, and resolves
    any open positions. See docs/designs/agentic_phase_d_forward_observation.md.
    """
    import pandas as pd
    from ta_foundation.research_ledger import DEFAULT_DB_PATH, get_repository
    from ta_foundation.shadow import FolderBarsDataSource, run_shadow_pass

    ap = argparse.ArgumentParser(prog="ta_foundation --shadow-pass")
    ap.add_argument("--shadow-pass", action="store_true", required=True,
                    help="Run a forward-shadow pass over enrolled candidates.")
    ap.add_argument("--market-data", required=True,
                    help="Folder containing NinjaTrader minute-bar exports.")
    ap.add_argument("--ledger-db", default=None,
                    help="Path to research ledger SQLite file "
                         "(default: .ta_artifacts/research_ledger.db).")
    ap.add_argument("--candidate-id", default=None,
                    help="Restrict the pass to a single candidate.")
    ap.add_argument("--until", default=None,
                    help="ISO timestamp clamp for the bar window (default: no clamp).")
    args = ap.parse_args(argv)

    ledger_db = Path(args.ledger_db) if args.ledger_db else DEFAULT_DB_PATH
    repo = get_repository(ledger_db)
    loader = FolderBarsDataSource(Path(args.market_data))

    until_ts = pd.Timestamp(args.until) if args.until else None
    report = run_shadow_pass(
        repo=repo,
        bars_loader=loader,
        until_ts=until_ts,
        candidate_id=args.candidate_id,
    )
    print(f"[shadow-pass] candidates={report.n_candidates} "
          f"new_signals={report.total_signals_inserted} "
          f"resolved={report.total_signals_resolved} "
          f"no_fill={report.total_signals_no_fill}")
    for cr in report.candidates:
        if cr.error:
            print(f"  - {cr.candidate_id} ERROR: {cr.error}")
        elif cr.skipped_reason:
            print(f"  - {cr.candidate_id} skipped: {cr.skipped_reason}")
        else:
            print(f"  - {cr.candidate_id} bars={cr.bars_seen} "
                  f"new={cr.signals_inserted} dup={cr.signals_duplicate} "
                  f"transitions={cr.transitions}")
    return 0


def _shadow_health_main(argv: list[str]) -> int:
    """Render the daily shadow-health report (Phase D.2).

    Reads the research ledger, aggregates per-candidate shadow statistics
    for the chosen Denver session day, and emits a deterministic markdown
    document. Default output path is
    ``runs/<YYYY-MM-DD>/shadow_health.md`` relative to the current
    working directory.
    """
    from ta_foundation.research_ledger import DEFAULT_DB_PATH, get_repository
    from ta_foundation.shadow import compute_daily_health, render_health_markdown

    ap = argparse.ArgumentParser(prog="ta_foundation --shadow-health")
    ap.add_argument("--shadow-health", action="store_true", required=True,
                    help="Render the daily shadow-health report.")
    ap.add_argument("--for-date", default=None,
                    help="Denver session day to summarise (YYYY-MM-DD). "
                         "Defaults to today (Denver local).")
    ap.add_argument("--trailing-window", type=int, default=30,
                    help="Trailing trade count for rolling PF/win-rate.")
    ap.add_argument("--out", default=None,
                    help="Output markdown path. Default: "
                         "runs/<YYYY-MM-DD>/shadow_health.md")
    ap.add_argument("--ledger-db", default=None,
                    help="Path to research ledger SQLite file.")
    args = ap.parse_args(argv)

    ledger_db = Path(args.ledger_db) if args.ledger_db else DEFAULT_DB_PATH
    repo = get_repository(ledger_db)
    report = compute_daily_health(
        repo,
        for_date=args.for_date,
        trailing_window=int(args.trailing_window),
    )
    body = render_health_markdown(report)

    out_path = (
        Path(args.out)
        if args.out
        else Path("runs") / report.report_date / "shadow_health.md"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(body, encoding="utf-8")
    print(f"[shadow-health] {report.report_date} "
          f"candidates={report.candidates_active} "
          f"signals_today={report.total_signals_today} "
          f"resolved_today={report.total_resolved_today} "
          f"open={report.total_open_positions} "
          f"net_pnl_today=${report.total_net_pnl_today:.2f} "
          f"anomalies={len(report.anomalies)} -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
