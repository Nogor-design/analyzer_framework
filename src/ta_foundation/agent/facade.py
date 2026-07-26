from __future__ import annotations

import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from ta_foundation.core.registry import ParserRegistry
from ta_foundation.core.pipeline import ingest_folder, IngestResult
from ta_foundation.parsers.ninjatrader.trades_csv import NinjaTraderTradesCsvParser
from ta_foundation.parsers.ninjatrader.analysis_by_day_csv import NinjaTraderDailyAnalysisCsvParser
from ta_foundation.parsers.ninjatrader.summary_csv import NinjaTraderSummaryCsvParser
from ta_foundation.parsers.ninjatrader.settings_csv import NinjaTraderSettingsCsvParser
from ta_foundation.parsers.ninjatrader.optimization_csv import NinjaTraderOptimizationCsvParser
from ta_foundation.parsers.ninjatrader.minute_bars_last_txt import MinuteBarsLastTxtParser
from ta_foundation.parsers.ninjatrader.tick_last_txt import TickLastTxtParser

from ta_foundation.analysis.pattern_engine.orchestrator import compute_and_attach_pattern_engine
from ta_foundation.analysis.strategy_discovery import orchestrator as strategy_discovery_orchestrator
from ta_foundation.reports.html.config import load_report_configs, build_report_from_config
from ta_foundation.reports.text.export_summary_text import export_summary_text

# Import discovery modules
from ta_foundation.analysis.entry_strategies.sweep import run_candle_discovery
from ta_foundation.analysis.entry_strategies.ma_sweep import run_ma_discovery
from ta_foundation.analysis.entry_strategies.orb_sweep import run_orb_discovery
from ta_foundation.analysis.entry_strategies.bb_sweep import run_bb_discovery
from ta_foundation.analysis.entry_strategies.breakout_sweep import run_breakout_discovery
from ta_foundation.analysis.entry_strategies.pullback_sweep import run_pullback_discovery
from ta_foundation.analysis.entry_strategies.level_sweep import run_level_discovery
from ta_foundation.analysis.entry_strategies.lcr_sweep import run_lcr_discovery
from ta_foundation.analysis.entry_strategies.premarket_sweep import run_premarket_predictor

class AnalysisFacade:
    """
    A high-level facade for interacting with the ta_foundation pipeline.
    Designed for use by agents and high-level automation scripts.
    """

    def __init__(self):
        self.registry = ParserRegistry(parsers=[
            NinjaTraderTradesCsvParser(),
            NinjaTraderDailyAnalysisCsvParser(),
            NinjaTraderSummaryCsvParser(),
            NinjaTraderSettingsCsvParser(),
            NinjaTraderOptimizationCsvParser(),
            MinuteBarsLastTxtParser(),
            TickLastTxtParser(),
        ])
        self.ingest_result: Optional[IngestResult] = None

    def ingest(
        self,
        input_path: str,
        recursive: bool = False,
        market_data_path: Optional[str] = None,
        load_tick_data: bool = True
    ) -> Dict[str, Any]:
        """Ingest a folder of backtest runs and market data."""
        in_dir = Path(input_path)
        market_dir = Path(market_data_path) if market_data_path else in_dir
        
        self.ingest_result = ingest_folder(
            in_dir,
            registry=self.registry,
            recursive=recursive,
            market_data_folder=market_dir,
            load_tick_data=load_tick_data,
        )
        
        return {
            "n_packages": len(self.ingest_result.packages),
            "run_ids": list(self.ingest_result.packages.keys()),
            "market_instruments": list(self.ingest_result.market.minute_bars.keys()) if self.ingest_result.market else [],
            "warnings": [w["message"] for pkg in self.ingest_result.packages.values() for w in pkg.warnings]
        }

    def run_discovery(self, discovery_type: str, config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Run a discovery module (candle, ma, orb, bb, breakout, pullback, level, lcr, premarket).
        """
        if not self.ingest_result:
            return {"error": "No data ingested. Call ingest() first."}

        modules = {
            "candle": (run_candle_discovery, "candle_discovery"),
            "ma": (run_ma_discovery, "ma_discovery"),
            "orb": (run_orb_discovery, "orb_discovery"),
            "bb": (run_bb_discovery, "bb_discovery"),
            "breakout": (run_breakout_discovery, "breakout_discovery"),
            "pullback": (run_pullback_discovery, "pullback_discovery"),
            "level": (run_level_discovery, "level_discovery"),
            "lcr": (run_lcr_discovery, "lcr_discovery"),
            "premarket": (run_premarket_predictor, "premarket_discovery"),
        }

        if discovery_type not in modules:
            return {"error": f"Unknown discovery type: {discovery_type}. Available: {list(modules.keys())}"}

        run_fn, key = modules[discovery_type]
        
        # Get 1m bars
        # This is a bit simplified compared to main.py's _select_bars_1m_from_market
        # We'll just take the first one available or from config instrument
        instrument = config.get("instrument")
        bars_1m = None
        if self.ingest_result.market and self.ingest_result.market.minute_bars:
            if instrument:
                bars_1m = next((df for (root, _), df in self.ingest_result.market.minute_bars.items() if str(root).upper() == instrument.upper()), None)
            if bars_1m is None:
                bars_1m = next(iter(self.ingest_result.market.minute_bars.values()))

        if bars_1m is None:
            return {"error": "No minute bars available for discovery."}

        try:
            results = run_fn(bars_1m=bars_1m, config=config)
            # Attach to all packages
            for pkg in self.ingest_result.packages.values():
                pkg.metadata.setdefault("derived", {})[key] = results
            
            return {
                "ok": True,
                "n_results": results.get("n_results", 0),
                "n_combinations": results.get("n_combinations_run", 0),
                "key": key
            }
        except Exception as e:
            return {"error": f"Discovery failed: {str(e)}", "traceback": traceback.format_exc()}

    def run_pattern_engine(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Run the pattern engine on ingested packages."""
        if not self.ingest_result:
            return {"error": "No data ingested."}
        
        try:
            compute_and_attach_pattern_engine(
                packages=self.ingest_result.packages,
                market=self.ingest_result.market,
                options=config
            )
            return {"ok": True, "message": "Pattern Engine attached to packages."}
        except Exception as e:
            return {"error": str(e), "traceback": traceback.format_exc()}

    def run_strategy_discovery(self, config: Dict[str, Any], db_path: Optional[str] = None) -> Dict[str, Any]:
        """Run strategy discovery on ingested packages."""
        if not self.ingest_result:
            return {"error": "No data ingested."}
        
        try:
            strategy_discovery_orchestrator.run_strategy_discovery(
                packages=self.ingest_result.packages,
                market=self.ingest_result.market,
                options=config,
                db_path=db_path
            )
            return {"ok": True, "message": "Strategy Discovery complete."}
        except Exception as e:
            return {"error": str(e), "traceback": traceback.format_exc()}

    def generate_reports(self, output_path: str, report_config_path: Optional[str] = None) -> Dict[str, Any]:
        """Generate HTML and TXT reports."""
        if not self.ingest_result:
            return {"error": "No data ingested."}
        
        out_dir = Path(output_path)
        out_dir.mkdir(parents=True, exist_ok=True)
        
        cfgs = load_report_configs(report_config_path)
        written = []
        
        for cfg in cfgs:
            html, filename = build_report_from_config(
                self.ingest_result.packages,
                cfg,
                market=self.ingest_result.market,
                optimization_store=self.ingest_result.optimization_store
            )
            
            out_file = out_dir / filename
            out_file.write_text(html, encoding="utf-8")
            written.append(str(out_file))
            
            # Text summary
            txt_file = out_file.with_suffix(".txt")
            export_summary_text(self.ingest_result.packages, txt_file, title=cfg.title)
            written.append(str(txt_file))
            
        return {"ok": True, "written_files": written}
