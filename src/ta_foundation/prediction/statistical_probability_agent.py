"""
Statistical probability agent — conditional-frequency baseline with
empirical-Bayes smoothing and a fallback hierarchy.

For every historical bar `i` in the input series with a fully-observable
horizon (i.e., `i + horizon < asof_idx`), we record:

    features   : (session_label, regime_label)
    outcome    : (direction, return_pts, mfe_pts, mae_pts, threshold_label)

To predict at `asof_idx`, we look up the bucket that matches the asof
features. If the bucket has too few samples we escalate through a fallback
hierarchy: full bucket → session-only → all-history. The class probabilities
are shrunk toward the broader baseline using a Dirichlet-multinomial
smoothing prior of strength `smoothing_alpha`.

This agent is deterministic, has no LLM dependency, and is leakage-safe:
each historical sample's horizon ends strictly before the asof index.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .horizon_features import (
    NEUTRAL_ATR_THRESHOLD,
    compute_lite_outcome,
    compute_session_regime_atr,
)
from .horizon_models import CandleHorizonPrediction
from .session_classifier import SessionConfig

DEFAULT_AGENT_ID = "statistical_probability_v1"
DEFAULT_METHOD = "conditional_frequency_v1"


@dataclass
class StatisticalProbabilityAgentConfig:
    session_config: Optional[SessionConfig] = None
    atr_period: int = 14
    regime_sma_period: int = 50
    regime_atr_band_frac: float = 0.5    # band = band_frac * atr around SMA
    min_samples_local: int = 8
    min_samples_global: int = 20
    smoothing_alpha: float = 5.0
    upside_threshold_atr: float = 1.0
    downside_threshold_atr: float = 1.0
    history_lookback_bars: int = 5000     # cap on how far back to look
    bucket_features: Tuple[str, ...] = ("session", "regime")


@dataclass
class _HistoryRow:
    session: str
    regime: str
    direction: str
    return_pts: float
    mfe_pts: float
    mae_pts: float
    threshold_label: str


@dataclass
class _AsofFeatures:
    session: str
    regime: str
    prior_atr: float
    asof_close: float


# ---------------------------------------------------------------------------
# Public agent
# ---------------------------------------------------------------------------

class StatisticalProbabilityAgent:
    """
    Conditional-frequency baseline. Stateless across calls; pass a fresh
    bar series each time.
    """

    def __init__(
        self,
        config: Optional[StatisticalProbabilityAgentConfig] = None,
        agent_id: str = DEFAULT_AGENT_ID,
    ) -> None:
        self.config = config or StatisticalProbabilityAgentConfig()
        self.agent_id = agent_id

    def predict(
        self,
        bars: pd.DataFrame,
        asof_idx: int,
        horizon_candles: int,
        instrument: str,
        contract: str,
        timeframe: str,
    ) -> CandleHorizonPrediction:
        """
        Build a horizon prediction for the bar at `asof_idx`.

        `bars` must have columns `dt` (tz-aware), `open`, `high`, `low`,
        `close`. The series must be sorted ascending by `dt`. `asof_idx` is
        the integer index of the asof bar within `bars`.
        """
        if bars is None or len(bars) == 0:
            raise ValueError("predict: bars is empty")
        if horizon_candles <= 0:
            raise ValueError(f"horizon_candles must be > 0, got {horizon_candles}")
        if asof_idx < 0 or asof_idx >= len(bars):
            raise ValueError(
                f"asof_idx={asof_idx} out of range for bars of length {len(bars)}"
            )

        cfg = self.config
        bars = bars.reset_index(drop=True)

        # Step 1: derive per-bar features (session + regime + ATR)
        feats = _compute_features(bars, cfg)

        # Step 2: at asof, capture features used for bucket lookup
        asof_feats = _AsofFeatures(
            session=feats["session"].iloc[asof_idx],
            regime=feats["regime"].iloc[asof_idx],
            prior_atr=float(feats["atr"].iloc[asof_idx]),
            asof_close=float(bars.iloc[asof_idx]["close"]),
        )

        # Step 3: build the history table. Only samples whose horizon ends
        # strictly before asof_idx are eligible (walk-forward leakage guard).
        history = _build_history_table(bars, feats, asof_idx, horizon_candles, cfg)

        if len(history) < cfg.min_samples_global:
            return self._abstain(
                instrument=instrument,
                contract=contract,
                timeframe=timeframe,
                horizon_candles=horizon_candles,
                bars=bars,
                asof_idx=asof_idx,
                asof_feats=asof_feats,
                reason="insufficient_samples",
                sample_size=len(history),
            )

        # Step 4: lookup bucket with fallback hierarchy
        matched, fallback_level = _lookup_bucket(history, asof_feats, cfg)

        if len(matched) < cfg.min_samples_global:
            # Even the global fallback is too small; abstain
            return self._abstain(
                instrument=instrument,
                contract=contract,
                timeframe=timeframe,
                horizon_candles=horizon_candles,
                bars=bars,
                asof_idx=asof_idx,
                asof_feats=asof_feats,
                reason="insufficient_samples",
                sample_size=len(matched),
                fallback_level=fallback_level,
            )

        # Step 5: compute smoothed estimates
        estimates = _aggregate_bucket(
            matched=matched,
            prior=history,
            alpha=cfg.smoothing_alpha,
        )

        # Step 6: assemble the CandleHorizonPrediction
        bucket_label = _bucket_label(timeframe, asof_feats, horizon_candles, fallback_level)

        upside_pts = cfg.upside_threshold_atr * asof_feats.prior_atr
        downside_pts = cfg.downside_threshold_atr * asof_feats.prior_atr

        pred = CandleHorizonPrediction(
            agent_id=self.agent_id,
            instrument=instrument,
            contract=contract,
            timeframe=timeframe,
            asof_timestamp=pd.Timestamp(bars.iloc[asof_idx]["dt"]).isoformat(),
            session_label=asof_feats.session,
            horizon_candles=int(horizon_candles),

            bullish_probability=estimates["p_bull"],
            bearish_probability=estimates["p_bear"],
            neutral_probability=estimates["p_neu"],
            confidence=max(estimates["p_bull"], estimates["p_bear"], estimates["p_neu"]),

            expected_return_points=estimates["expected_return"],
            expected_return_atr=(
                estimates["expected_return"] / asof_feats.prior_atr
                if asof_feats.prior_atr > 0 else 0.0
            ),
            median_return_points=estimates["median_return"],
            p10_return_points=estimates["p10"],
            p25_return_points=estimates["p25"],
            p75_return_points=estimates["p75"],
            p90_return_points=estimates["p90"],

            expected_mfe_points=estimates["expected_mfe"],
            expected_mae_points=estimates["expected_mae"],
            upside_threshold_points=upside_pts,
            downside_threshold_points=downside_pts,
            upside_threshold_probability=estimates["p_up_first"],
            downside_threshold_probability=estimates["p_down_first"],
            neither_threshold_probability=estimates["p_neither"],

            predicted_volatility=estimates["return_std"],
            sample_size=int(len(matched)),
            effective_sample_size=float(len(matched) + cfg.smoothing_alpha),
            method_used=DEFAULT_METHOD,
            fallback_level=int(fallback_level),
            calibration_bucket=bucket_label,
            feature_snapshot={
                "session": asof_feats.session,
                "regime": asof_feats.regime,
                "prior_atr": asof_feats.prior_atr,
                "asof_close": asof_feats.asof_close,
            },
            reasoning_summary=(
                f"Conditional frequency over {len(matched)} historical samples "
                f"(fallback_level={fallback_level}) on session={asof_feats.session}, "
                f"regime={asof_feats.regime}; smoothed with alpha={cfg.smoothing_alpha:g} "
                f"toward {len(history)}-sample timeframe baseline."
            ),
        )
        pred.normalize_direction_probs()
        return pred

    # ------------------------------------------------------------------
    def _abstain(
        self,
        instrument: str,
        contract: str,
        timeframe: str,
        horizon_candles: int,
        bars: pd.DataFrame,
        asof_idx: int,
        asof_feats: _AsofFeatures,
        reason: str,
        sample_size: int = 0,
        fallback_level: int = 99,
    ) -> CandleHorizonPrediction:
        return CandleHorizonPrediction(
            agent_id=self.agent_id,
            instrument=instrument,
            contract=contract,
            timeframe=timeframe,
            asof_timestamp=pd.Timestamp(bars.iloc[asof_idx]["dt"]).isoformat(),
            session_label=asof_feats.session,
            horizon_candles=int(horizon_candles),
            bullish_probability=0.0,
            bearish_probability=0.0,
            neutral_probability=0.0,
            confidence=0.0,
            sample_size=int(sample_size),
            effective_sample_size=0.0,
            method_used=DEFAULT_METHOD,
            fallback_level=int(fallback_level),
            calibration_bucket=_bucket_label(timeframe, asof_feats, horizon_candles, fallback_level),
            feature_snapshot={
                "session": asof_feats.session,
                "regime": asof_feats.regime,
                "prior_atr": asof_feats.prior_atr,
            },
            reasoning_summary=f"Abstained: {reason} (samples={sample_size}).",
            abstain=True,
            abstain_reason=reason,
        )


# ---------------------------------------------------------------------------
# Feature derivation
# ---------------------------------------------------------------------------

def _compute_features(
    bars: pd.DataFrame,
    cfg: StatisticalProbabilityAgentConfig,
) -> pd.DataFrame:
    """
    Per-bar features used both for asof lookup and for historical bucketing:
    session, regime, atr — delegating to `horizon_features` for the shared
    definitions so the analogue agent and statistical agent never drift.
    """
    return compute_session_regime_atr(
        bars,
        atr_period=cfg.atr_period,
        sma_period=cfg.regime_sma_period,
        atr_band_frac=cfg.regime_atr_band_frac,
        session_config=cfg.session_config,
    )


# ---------------------------------------------------------------------------
# History table construction
# ---------------------------------------------------------------------------

def _build_history_table(
    bars: pd.DataFrame,
    feats: pd.DataFrame,
    asof_idx: int,
    horizon: int,
    cfg: StatisticalProbabilityAgentConfig,
) -> pd.DataFrame:
    """
    For every i where `i + horizon < asof_idx`, compute a lite outcome.
    Uses raw numpy arrays for speed.
    """
    closes = bars["close"].astype(float).to_numpy()
    highs = bars["high"].astype(float).to_numpy()
    lows = bars["low"].astype(float).to_numpy()
    atr_arr = feats["atr"].to_numpy()
    sessions = feats["session"].to_numpy()
    regimes = feats["regime"].to_numpy()

    # Walk-forward eligibility: outcome ends strictly before asof_idx.
    # Sample i has outcome over (i+1, i+horizon), so need i + horizon < asof_idx
    # → i < asof_idx - horizon.
    end = asof_idx - horizon

    # Warmup so ATR / SMA / regime are stable
    warmup = max(cfg.atr_period, cfg.regime_sma_period)
    start = max(warmup, end - cfg.history_lookback_bars)

    if end <= start:
        return pd.DataFrame(columns=[
            "session", "regime", "direction",
            "return_pts", "mfe_pts", "mae_pts", "threshold_label",
        ])

    rows: List[_HistoryRow] = []
    for i in range(start, end):
        prior_atr = float(atr_arr[i])
        if prior_atr <= 0.0:
            continue
        outcome = compute_lite_outcome(
            closes=closes,
            highs=highs,
            lows=lows,
            asof_idx=i,
            horizon=horizon,
            prior_atr=prior_atr,
            upside_atr=cfg.upside_threshold_atr,
            downside_atr=cfg.downside_threshold_atr,
        )
        if outcome is None:
            continue
        direction, ret_pts, mfe, mae, label = outcome
        rows.append(_HistoryRow(
            session=str(sessions[i]),
            regime=str(regimes[i]),
            direction=direction,
            return_pts=ret_pts,
            mfe_pts=mfe,
            mae_pts=mae,
            threshold_label=label,
        ))

    if not rows:
        return pd.DataFrame(columns=[
            "session", "regime", "direction",
            "return_pts", "mfe_pts", "mae_pts", "threshold_label",
        ])

    return pd.DataFrame([r.__dict__ for r in rows])


# ---------------------------------------------------------------------------
# Bucket lookup with fallback hierarchy
# ---------------------------------------------------------------------------

def _lookup_bucket(
    history: pd.DataFrame,
    asof: _AsofFeatures,
    cfg: StatisticalProbabilityAgentConfig,
) -> Tuple[pd.DataFrame, int]:
    """
    Walk the fallback hierarchy. Returns (matched_frame, fallback_level).

    Levels:
      0 → session AND regime
      1 → session only
      2 → all history (timeframe-only baseline)
    """
    full = history[
        (history["session"] == asof.session)
        & (history["regime"] == asof.regime)
    ]
    if len(full) >= cfg.min_samples_local:
        return full, 0

    sess = history[history["session"] == asof.session]
    if len(sess) >= cfg.min_samples_local:
        return sess, 1

    return history, 2


# ---------------------------------------------------------------------------
# Bucket aggregation with empirical-Bayes smoothing
# ---------------------------------------------------------------------------

def _aggregate_bucket(
    matched: pd.DataFrame,
    prior: pd.DataFrame,
    alpha: float,
) -> Dict[str, float]:
    n_local = len(matched)
    n_prior = len(prior)

    # Direction
    p_bull = _smooth_probability(matched, prior, "direction", "bullish", alpha)
    p_bear = _smooth_probability(matched, prior, "direction", "bearish", alpha)
    p_neu = _smooth_probability(matched, prior, "direction", "neutral", alpha)

    # Threshold
    p_up_first = _smooth_probability(matched, prior, "threshold_label", "upside_first", alpha)
    p_down_first = _smooth_probability(matched, prior, "threshold_label", "downside_first", alpha)
    # both_same_bar is rare; fold half into each side
    p_both = _smooth_probability(matched, prior, "threshold_label", "both_same_bar", alpha)
    p_up_first += 0.5 * p_both
    p_down_first += 0.5 * p_both
    p_neither = max(0.0, 1.0 - p_up_first - p_down_first)

    # Return distribution: smooth the moments by mixing local + prior
    # weighted by EB strength.
    returns = matched["return_pts"].to_numpy(dtype=float)
    prior_returns = prior["return_pts"].to_numpy(dtype=float)

    expected_return = _smooth_mean(returns, prior_returns, alpha)
    return_std = _smooth_std(returns, prior_returns, alpha)

    if n_local > 0:
        # Percentiles always come from local — they are robust enough at >= 8 samples
        pcts = np.percentile(returns, [10, 25, 50, 75, 90])
    else:
        pcts = np.percentile(prior_returns, [10, 25, 50, 75, 90])

    expected_mfe = _smooth_mean(
        matched["mfe_pts"].to_numpy(dtype=float),
        prior["mfe_pts"].to_numpy(dtype=float),
        alpha,
    )
    expected_mae = _smooth_mean(
        matched["mae_pts"].to_numpy(dtype=float),
        prior["mae_pts"].to_numpy(dtype=float),
        alpha,
    )

    return {
        "p_bull": float(p_bull),
        "p_bear": float(p_bear),
        "p_neu": float(p_neu),
        "p_up_first": float(max(0.0, min(1.0, p_up_first))),
        "p_down_first": float(max(0.0, min(1.0, p_down_first))),
        "p_neither": float(max(0.0, min(1.0, p_neither))),
        "expected_return": float(expected_return),
        "median_return": float(pcts[2]),
        "p10": float(pcts[0]),
        "p25": float(pcts[1]),
        "p75": float(pcts[3]),
        "p90": float(pcts[4]),
        "expected_mfe": float(expected_mfe),
        "expected_mae": float(expected_mae),
        "return_std": float(return_std),
        "n_local": int(n_local),
        "n_prior": int(n_prior),
    }


def _smooth_probability(
    matched: pd.DataFrame,
    prior: pd.DataFrame,
    column: str,
    value: str,
    alpha: float,
) -> float:
    """
    Dirichlet-multinomial smoothing:
        p_smooth = (count_local + alpha * p_prior) / (n_local + alpha)
    where p_prior is computed from `prior`. With alpha large, p_smooth shrinks
    toward p_prior; with alpha=0, p_smooth = count_local / n_local.
    """
    n_local = len(matched)
    n_prior = len(prior)
    count_local = int((matched[column] == value).sum()) if n_local > 0 else 0
    count_prior = int((prior[column] == value).sum()) if n_prior > 0 else 0

    p_prior = count_prior / n_prior if n_prior > 0 else 1.0 / 3.0
    return (count_local + alpha * p_prior) / max(1e-9, n_local + alpha)


def _smooth_mean(
    local: np.ndarray,
    prior: np.ndarray,
    alpha: float,
) -> float:
    """
    Mean estimator with EB shrinkage to the prior mean.
    """
    n_local = len(local)
    if n_local == 0 and len(prior) == 0:
        return 0.0
    p_mean = float(prior.mean()) if len(prior) > 0 else 0.0
    if n_local == 0:
        return p_mean
    l_mean = float(local.mean())
    return (n_local * l_mean + alpha * p_mean) / (n_local + alpha)


def _smooth_std(local: np.ndarray, prior: np.ndarray, alpha: float) -> float:
    """
    Pooled stdev with shrinkage toward prior stdev. Keeps the predicted_volatility
    field meaningful even on small local samples.
    """
    if len(local) <= 1 and len(prior) <= 1:
        return 0.0
    p_std = float(prior.std(ddof=1)) if len(prior) > 1 else 0.0
    if len(local) <= 1:
        return p_std
    l_std = float(local.std(ddof=1))
    n = len(local)
    return (n * l_std + alpha * p_std) / (n + alpha)


def _bucket_label(
    timeframe: str,
    asof: _AsofFeatures,
    horizon: int,
    fallback_level: int,
) -> str:
    return (
        f"tf={timeframe},session={asof.session},regime={asof.regime},"
        f"horizon={horizon},fallback={fallback_level}"
    )
