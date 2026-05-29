"""
Synthetic Market Data Generator for Validation

Generates realistic OHLCV data with known edge patterns for framework validation.
Supports regime switching (trend_up, trend_down, range), large candles, pin bars, and RTH-only trading.

Usage:
    from ta_foundation.validation.synthetic_market_generator import MarketRegimeSimulator
    sim = MarketRegimeSimulator(n_bars=2880, base_price=4500.0)
    df = sim.generate_realistic_day_trading_data()
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta, time
from enum import Enum
from typing import Optional, Dict, Tuple
import pytz


class TradingRegime(Enum):
    """Market regime classification"""
    TREND_UP = "trend_up"
    TREND_DOWN = "trend_down"
    RANGE = "range"


class MarketRegimeSimulator:
    """
    Generates realistic day trading OHLCV data with switchable market regimes.

    RTH-only (7:30-16:00 Denver Time = 390 bars/day at 1-minute)
    Supports: NQ, ES, RTY (different base prices and volatility profiles)

    Regime characteristics:
    - TREND_UP: 70% up moves, 30% down, breakouts frequent, avg +0.02% bars
    - TREND_DOWN: 70% down moves, 30% up, breakdowns frequent, avg -0.02% bars
    - RANGE: 50/50 up/down, support/resistance bounces, mean-reverting, avg 0.0% bars
    """

    REGIME_WEIGHTS = {
        TradingRegime.TREND_UP: 0.40,
        TradingRegime.TREND_DOWN: 0.30,
        TradingRegime.RANGE: 0.30,
    }

    # Volatility profiles by instrument (per minute, %)
    VOLATILITY_PROFILES = {
        "NQ": 0.005,      # 0.5% per minute average move
        "ES": 0.004,      # 0.4% per minute
        "RTY": 0.006,     # 0.6% per minute (more volatile)
    }

    # Base prices for instruments
    BASE_PRICES = {
        "NQ": 4500.0,
        "ES": 5000.0,
        "RTY": 2300.0,
    }

    # Regime-specific returns (expected % move per bar, in decimal form)
    REGIME_RETURNS = {
        TradingRegime.TREND_UP: 0.0002,      # +0.02% per bar average
        TradingRegime.TREND_DOWN: -0.0002,   # -0.02% per bar average
        TradingRegime.RANGE: 0.0,             # 0% (mean-reverting)
    }

    # Large candle probabilities (20% chance in trends, 5% in ranges)
    LARGE_CANDLE_PROB_TREND = 0.20
    LARGE_CANDLE_PROB_RANGE = 0.05

    # Pin bar probability (15% in all regimes)
    PIN_BAR_PROB = 0.15

    def __init__(
        self,
        n_bars: int = 2880,  # 2 weeks @ 390 bars/trading day
        base_price: float = 4500.0,
        starting_time: Optional[datetime] = None,
        regime_switch_bars: int = 360,  # Switch every 6 hours (360 mins)
        instrument: str = "NQ",
        seed: Optional[int] = None,
    ):
        """
        Initialize market simulator.

        Args:
            n_bars: Total bars to generate
            base_price: Starting price (overrides OHLC offset if instrument specified)
            starting_time: Starting datetime (naive, will be localized to Denver)
            regime_switch_bars: Bars before regime change
            instrument: "NQ", "ES", or "RTY"
            seed: Random seed for reproducibility
        """
        self.n_bars = n_bars
        self.base_price = base_price
        self.regime_switch_bars = regime_switch_bars
        self.instrument = instrument.upper()
        self.tz = pytz.timezone("America/Denver")

        if seed is not None:
            np.random.seed(seed)

        # Set starting time (default to previous Friday 7:30 Denver time)
        if starting_time is None:
            today = datetime.now(self.tz)
            # Find last Friday
            days_back = (today.weekday() - 4) % 7
            if days_back == 0 and today.hour < 7.5:  # Before RTH today
                days_back = 7
            last_friday = today - timedelta(days=days_back)
            self.starting_time = last_friday.replace(hour=7, minute=30, second=0, microsecond=0)
        else:
            self.starting_time = starting_time.astimezone(self.tz) if starting_time.tzinfo else self.tz.localize(starting_time)

        # Volatility for this instrument
        self.volatility = self.VOLATILITY_PROFILES.get(self.instrument, 0.18)

        # Current state
        self.current_price = base_price
        self.current_regime = self._select_regime()
        self.bars_in_regime = 0
        self.high_in_regime = base_price
        self.low_in_regime = base_price

    def _select_regime(self) -> TradingRegime:
        """Randomly select next regime based on weights"""
        regimes = list(self.REGIME_WEIGHTS.keys())
        weights = list(self.REGIME_WEIGHTS.values())
        return np.random.choice(regimes, p=weights)

    def _is_rth_time(self, dt: datetime) -> bool:
        """Check if time is within RTH (7:30-16:00 Denver)"""
        if dt.weekday() >= 5:  # Weekend
            return False
        hour, minute = dt.hour, dt.minute
        return (hour > 7 or (hour == 7 and minute >= 30)) and hour < 16

    def _next_rth_bar_time(self, dt: datetime) -> datetime:
        """Get next valid RTH bar time (skip overnight/weekends)"""
        dt = dt + timedelta(minutes=1)

        while not self._is_rth_time(dt):
            if dt.hour >= 16:
                # Past market close, jump to next day 7:30
                dt = dt.replace(hour=7, minute=30) + timedelta(days=1)
            elif dt.weekday() >= 5:
                # Weekend, jump to Monday 7:30
                days_until_monday = (7 - dt.weekday()) % 7
                if days_until_monday == 0:
                    days_until_monday = 1
                dt = dt + timedelta(days=days_until_monday)
                dt = dt.replace(hour=7, minute=30)
            else:
                # Before market open (before 7:30)
                dt = dt.replace(hour=7, minute=30)

        return dt

    def _generate_ohlcv_bar(self) -> Dict:
        """
        Generate a single OHLCV bar with regime-aware characteristics.

        Returns:
            Dict with keys: open, high, low, close, volume, regime
        """
        # Check if regime should switch
        self.bars_in_regime += 1
        if self.bars_in_regime >= self.regime_switch_bars:
            self.current_regime = self._select_regime()
            self.bars_in_regime = 0
            self.high_in_regime = self.current_price
            self.low_in_regime = self.current_price

        open_price = self.current_price

        # Base return from regime (in decimal form)
        regime_return = self.REGIME_RETURNS[self.current_regime]

        # Volatility component (1-minute bar, in decimal form)
        z_score = np.random.standard_normal()
        vol_component = z_score * (self.volatility / np.sqrt(252 * 390))  # Annualized to 1-min

        # Determine bar direction (regime-aware)
        if self.current_regime == TradingRegime.TREND_UP:
            up_prob = 0.70
        elif self.current_regime == TradingRegime.TREND_DOWN:
            up_prob = 0.30
        else:  # RANGE
            up_prob = 0.50

        bar_direction = 1 if np.random.random() < up_prob else -1

        # Check for large candle
        large_candle_prob = self.LARGE_CANDLE_PROB_TREND if self.current_regime != TradingRegime.RANGE else self.LARGE_CANDLE_PROB_RANGE
        is_large_candle = np.random.random() < large_candle_prob
        large_candle_mult = 1.8 if is_large_candle else 1.0

        # Check for pin bar
        is_pin_bar = np.random.random() < self.PIN_BAR_PROB

        if is_pin_bar:
            # Pin bar: small body, long wick
            body_return = regime_return * 0.3 + vol_component * 0.5
            if bar_direction > 0:
                # Up close, long lower wick
                low_return = -0.0008 * large_candle_mult
                high_return = body_return + 0.0002
            else:
                # Down close, long upper wick
                low_return = body_return - 0.0002
                high_return = 0.0008 * large_candle_mult
        else:
            # Normal bar with high/low extension
            body_return = (regime_return + vol_component * bar_direction) * large_candle_mult
            wick_extension = abs(vol_component) * 0.4 * large_candle_mult

            if bar_direction > 0:
                high_return = body_return + wick_extension
                low_return = -wick_extension * 0.5
            else:
                high_return = wick_extension * 0.5
                low_return = body_return - wick_extension

        # Cap extreme moves (max single bar: 0.1%)
        body_return = np.clip(body_return, -0.001, 0.001)
        high_return = np.clip(high_return, -0.001, 0.001)
        low_return = np.clip(low_return, -0.001, 0.001)

        # Calculate OHLC
        high_price = open_price * (1 + high_return)
        low_price = open_price * (1 + low_return)
        close_price = open_price * (1 + body_return)

        # Ensure high/low bounds
        high_price = max(high_price, open_price, close_price)
        low_price = min(low_price, open_price, close_price)

        # Update regime high/low
        self.high_in_regime = max(self.high_in_regime, high_price)
        self.low_in_regime = min(self.low_in_regime, low_price)

        # Volume: higher in large candles and volatile bars
        vol_multiplier = 1.5 if is_large_candle else 1.0
        vol_multiplier *= (1.0 + abs(vol_component) * 2)
        vol_multiplier = np.clip(vol_multiplier, 0.5, 3.0)
        base_volume = 500000 + np.random.uniform(-100000, 100000)
        volume = int(base_volume * vol_multiplier)

        # Update current price for next bar
        self.current_price = close_price

        return {
            "open": open_price,
            "high": high_price,
            "low": low_price,
            "close": close_price,
            "volume": volume,
            "regime": self.current_regime.value,
        }

    def generate_realistic_day_trading_data(self) -> pd.DataFrame:
        """
        Generate realistic day trading OHLCV data (RTH-only, 1-minute bars).

        Returns:
            DataFrame with columns: datetime, open, high, low, close, volume, regime
        """
        bars = []
        current_time = self.starting_time

        for _ in range(self.n_bars):
            current_time = self._next_rth_bar_time(current_time)
            bar_data = self._generate_ohlcv_bar()
            bar_data["datetime"] = current_time
            bars.append(bar_data)

        df = pd.DataFrame(bars)
        df = df[["datetime", "open", "high", "low", "close", "volume", "regime"]]
        df["datetime"] = df["datetime"].dt.tz_localize(None)  # Remove tz info for consistency

        return df.reset_index(drop=True)

    def generate_with_known_edge(self, edge_type: str = "large_candle_breakout") -> pd.DataFrame:
        """
        Generate data with a known exploitable edge for validation testing.

        Args:
            edge_type: "large_candle_breakout", "pin_bar_reversal", or "trend_continuation"

        Returns:
            DataFrame with edge pattern annotations
        """
        df = self.generate_realistic_day_trading_data()

        if edge_type == "large_candle_breakout":
            # Annotate large candles: profitable breakout follows
            df["body_pct"] = (df["close"] - df["open"]).abs() / df["open"] * 100
            df["has_edge"] = df["body_pct"] > 0.25

        elif edge_type == "pin_bar_reversal":
            # Annotate pin bars: reversal follows
            df["wick_ratio"] = (df["high"] - df["low"]) / (df["close"] - df["open"]).abs()
            df["body_pct"] = (df["close"] - df["open"]).abs() / df["open"] * 100
            df["has_edge"] = (df["wick_ratio"] > 3) & (df["body_pct"] < 0.15)

        elif edge_type == "trend_continuation":
            # Annotate trend bars: continuation in same direction
            df["prev_close"] = df["close"].shift(1)
            df["is_up_bar"] = df["close"] > df["open"]
            df["prev_trend"] = df["is_up_bar"].shift(1)
            df["has_edge"] = df["is_up_bar"] == df["prev_trend"]

        return df


def generate_multi_instrument_data(
    instruments: Optional[list] = None,
    n_days: int = 10,
    n_bars_per_day: int = 390,  # RTH only
    output_dir: Optional[str] = None,
    seed: Optional[int] = None,
) -> Dict[str, pd.DataFrame]:
    """
    Generate synthetic data for multiple instruments with correlated regimes.

    Args:
        instruments: List of instrument codes (default: ["NQ", "ES", "RTY"])
        n_days: Number of trading days (excludes weekends)
        n_bars_per_day: Minutes per trading day (default 390 = 7:30-16:00 Denver)
        output_dir: If provided, save each instrument's data as CSV
        seed: Random seed for reproducibility

    Returns:
        Dict mapping instrument -> DataFrame
    """
    if instruments is None:
        instruments = ["NQ", "ES", "RTY"]

    if seed is not None:
        np.random.seed(seed)

    data = {}
    n_bars = n_days * n_bars_per_day

    # Generate shared regime signal for correlation
    regime_signal = np.random.choice([0, 1, 2], size=n_bars, p=[0.40, 0.30, 0.30])

    for instrument in instruments:
        # Deterministic seed per instrument, normalized to valid range
        instrument_seed = None
        if seed is not None:
            instrument_seed = (seed + abs(hash(instrument))) % (2**31 - 1)

        sim = MarketRegimeSimulator(
            n_bars=n_bars,
            base_price=MarketRegimeSimulator.BASE_PRICES[instrument],
            instrument=instrument,
            seed=instrument_seed,
        )

        df = sim.generate_realistic_day_trading_data()

        # Inject some correlation by modifying regimes
        regime_map = {0: "trend_up", 1: "trend_down", 2: "range"}
        df["regime"] = [regime_map[r] for r in regime_signal[:len(df)]]

        data[instrument] = df

        if output_dir:
            import os
            os.makedirs(output_dir, exist_ok=True)
            output_file = os.path.join(output_dir, f"{instrument}_synthetic.csv")
            df.to_csv(output_file, index=False)
            print(f"Saved {instrument} data to {output_file}")

    return data


if __name__ == "__main__":
    """
    Example usage: Generate 2 weeks of synthetic data for NQ, ES, RTY
    """
    print("Generating synthetic market data for validation...")

    # Single instrument example
    sim_nq = MarketRegimeSimulator(n_bars=2880, instrument="NQ", seed=42)
    df_nq = sim_nq.generate_realistic_day_trading_data()
    print(f"\nNQ Data (2 weeks, {len(df_nq)} bars):")
    print(df_nq.head(10))
    print(f"Price range: {df_nq['low'].min():.2f} - {df_nq['high'].max():.2f}")
    print(f"Regimes: {df_nq['regime'].value_counts().to_dict()}")

    # Multi-instrument example
    multi_data = generate_multi_instrument_data(
        instruments=["NQ", "ES", "RTY"],
        n_days=10,
        seed=42,
    )

    for inst, df in multi_data.items():
        print(f"\n{inst} Summary:")
        print(f"  Bars: {len(df)}")
        print(f"  Price: {df['close'].iloc[0]:.2f} → {df['close'].iloc[-1]:.2f} ({(df['close'].iloc[-1]/df['close'].iloc[0]-1)*100:.2f}%)")
        print(f"  Volatility: {df['close'].pct_change().std()*100:.2f}%")
        print(f"  Regimes: {df['regime'].value_counts().to_dict()}")
