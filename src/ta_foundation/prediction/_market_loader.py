"""Shared utility: parse .Last.txt files from a directory into a MarketDataStore."""
from __future__ import annotations

import sys
from pathlib import Path


def load_market_store(
    market_data_dir: Path,
    instrument: str,
    contract: str,
    allowed_instruments: list[tuple[str, str]] | None = None,
    *,
    load_ticks: bool = True,
):
    """
    Parse minute bars (and optionally ticks) for the requested
    `(instrument, contract)` from `market_data_dir`.

    `load_ticks=False` skips the `Tick.Last.txt` files entirely. Tick data
    is large (hundreds of MB to several GB per contract) and not needed by
    the daily or horizon prediction agents — both work off resampled bars.
    """
    from ta_foundation.marketdata.store import MarketDataStore
    from ta_foundation.parsers.ninjatrader.minute_bars_last_txt import MinuteBarsLastTxtParser
    from ta_foundation.parsers.ninjatrader.tick_last_txt import TickLastTxtParser

    if not market_data_dir.exists():
        print(f"market_data directory not found: {market_data_dir}", file=sys.stderr)
        sys.exit(1)

    # Default: only load the primary instrument/contract
    if allowed_instruments is None:
        allowed_instruments = [(instrument, contract)]
    allowed_prefixes = tuple(
        f"{instr} {cont}".lower() for instr, cont in allowed_instruments
    )

    market      = MarketDataStore()
    bars_parser = MinuteBarsLastTxtParser()
    tick_parser = TickLastTxtParser()
    loaded_bars = False

    for path in sorted(market_data_dir.glob("*.txt")):
        if allowed_prefixes and not path.name.lower().startswith(allowed_prefixes):
            continue
        try:
            header = path.read_text(encoding="utf-8", errors="replace")[:512]
        except Exception:
            header = ""

        if bars_parser.can_parse(path, header):
            art = bars_parser.parse(path, run_id=None)
            if art.df is not None and not art.df.empty:
                market.ingest_artifact(art)
                loaded_bars = True
                date_range = f"  [{art.df['dt'].min().date()} -> {art.df['dt'].max().date()}]"
                print(f"Loaded bars:  {path.name}  ({len(art.df)} rows){date_range}", file=sys.stderr)
            continue

        if not load_ticks:
            continue

        if tick_parser.can_parse(path, header):
            art = tick_parser.parse(path, run_id=None)
            if art.df is not None and not art.df.empty:
                market.ingest_artifact(art)
                print(f"Loaded ticks: {path.name}  ({len(art.df)} rows)", file=sys.stderr)

    market.finalize()

    if not loaded_bars:
        expected = f"{instrument} {contract}.Last.txt"
        print(
            f"WARNING: No minute bar file found in {market_data_dir}\n"
            f"  Expected a file named like: {expected}\n"
            f"  Run TaFoundationMinuteBarExporter or export bars from NinjaTrader first.",
            file=sys.stderr,
        )

    return market
