"""
Analogue probability agent — distance-weighted K nearest neighbors.

The Phase 1 statistical agent buckets historical samples by exact (session,
regime) match and computes empirical conditional frequencies. That is robust
but coarse — every sample inside a bucket is treated equally, regardless of
how similar its market state was to the asof state.

This Phase 2 agent improves on that in two ways:

  1. **Continuous similarity.** Each historical sample is given a 4-dim
     feature vector (atr_zscore, trend_slope, body_atr, momentum). The
     asof sample is scored against historical samples by L2 distance.
     Closer historical samples get more weight.

  2. **Soft fallback hierarchy.** Filtering is exact on session and regime
     by default, but if too few neighbors survive the filter the agent
     drops constraints in this order: regime → session → unfiltered.
     `fallback_level` is reported.

The output is the same `CandleHorizonPrediction` shape produced by the
statistical agent, so both can be scored and ranked uniformly.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .horizon_features import (
    NEUTRAL_ATR_THRESHOLD,
    compute_atr,
    compute_lite_outcome,
    compute_session_regime_atr,
)
from .horizon_models import CandleHorizonPrediction
from .session_classifier import SessionConfig

DEFAULT_AGENT_ID = "analogue_probability_v1"
DEFAULT_METHOD = "weighted_knn_v1"


@dataclass
class AnalogueProbabilityAgentConfig:
    session_config: Optional[SessionConfig] = None

    # Per-bar feature parameters
    atr_period: int = 14
    atr_long_period: int = 100
    sma_period: int = 50
    momentum_period: int = 5

    # Regime label parameters (used for filtering, same definition as stat agent)
    regime_atr_band_frac: float = 0.5

    # KNN parameters
    k: int = 30
    bandwidth: float = 0.75            # Gaussian kernel bandwidth (in feature units)
    min_k_local: int = 8               # min usable neighbors before falling back
    min_k_global: int = 20             # min neighbors at the broadest fallback before abstaining

    # Filter constraints
    require_session_match: bool = True
    require_regime_match: bool = True

    # Smoothing toward unfiltered baseline
    smoothing_alpha: float = 5.0

    # Threshold first-hit definitions (in ATR units)
    upside_threshold_atr: float = 1.0
    downside_threshold_atr: float = 1.0

    # History capping
    history_lookback_bars: int = 5000


@dataclass
class _AsofState:
    session: str
    regime: str
    prior_atr: float
    asof_close: float
    feature_vec: np.ndarray   # shape (4,)


@dataclass
class _NeighborRow:
    idx: int
    feature_vec: np.ndarray
    session: str
    regime: str
    direction: str
    return_pts: float
    mfe_pts: float
    mae_pts: float
    threshold_label: str


# ---------------------------------------------------------------------------
# Public agent
# ---------------------------------------------------------------------------

class AnalogueProbabilityAgent:
    """
    Distance-weighted KNN baseline. Stateless; pass a fresh bar series each
    call. Walk-forward leakage is enforced by only sampling history with
    `i + horizon < asof_idx`.
    """

    def __init__(
        self,
        config: Optional[AnalogueProbabilityAgentConfig] = None,
        agent_id: str = DEFAULT_AGENT_ID,
    ) -> None:
        self.config = config or AnalogueProbabilityAgentConfig()
        self.agent_id = agent_id

    # ------------------------------------------------------------------
    def predict(
        self,
        bars: pd.DataFrame,
        asof_idx: int,
        horizon_candles: int,
        instrument: str,
        contract: str,
        timeframe: str,
    ) -> CandleHorizonPrediction:
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

        feats = _compute_analogue_features(bars, cfg)

        asof = _AsofState(
            session=feats["session"].iloc[asof_idx],
            regime=feats["regime"].iloc[asof_idx],
            prior_atr=float(feats["atr"].iloc[asof_idx]),
            asof_close=float(bars.iloc[asof_idx]["close"]),
            feature_vec=feats[list(_FEATURE_COLS)].iloc[asof_idx].to_numpy(dtype=float),
        )

        # Asof feature vector contains NaNs only if the warmup wasn't reached.
        if not np.all(np.isfinite(asof.feature_vec)) or asof.prior_atr <= 0.0:
            return self._abstain(
                instrument, contract, timeframe, horizon_candles,
                bars, asof_idx, asof, "configuration_error",
                sample_size=0, fallback_level=99,
            )

        history = _build_neighbor_pool(bars, feats, asof_idx, horizon_candles, cfg)
        if len(history) < cfg.min_k_global:
            return self._abstain(
                instrument, contract, timeframe, horizon_candles,
                bars, asof_idx, asof, "insufficient_samples",
                sample_size=len(history), fallback_level=99,
            )

        # Apply filter hierarchy until we have enough neighbors
        filtered, fallback_level = _filter_with_fallback(history, asof, cfg)
        if len(filtered) < cfg.min_k_global:
            return self._abstain(
                instrument, contract, timeframe, horizon_candles,
                bars, asof_idx, asof, "insufficient_samples",
                sample_size=len(filtered), fallback_level=fallback_level,
            )

        # Pick top-K by distance, weight via Gaussian kernel
        neighbors, weights = _select_top_k_weighted(
            filtered, asof.feature_vec, cfg.k, cfg.bandwidth
        )
        eff_n = float(weights.sum())

        if len(neighbors) < cfg.min_k_local:
            # Pool has enough samples but the K-nearest have collapsed weight —
            # likely the asof is far from all historical samples. Don't abstain
            # entirely; tag low_confidence and use a wider neighbor set with
            # uniform weights as a safe fallback.
            neighbors = filtered
            weights = np.ones(len(filtered), dtype=float)
            eff_n = float(weights.sum())

        estimates = _aggregate_weighted(neighbors, weights, history, cfg.smoothing_alpha)

        upside_pts = cfg.upside_threshold_atr * asof.prior_atr
        downside_pts = cfg.downside_threshold_atr * asof.prior_atr

        bucket_label = (
            f"tf={timeframe},session={asof.session},regime={asof.regime},"
            f"horizon={horizon_candles},fallback={fallback_level}"
        )

        pred = CandleHorizonPrediction(
            agent_id=self.agent_id,
            instrument=instrument,
            contract=contract,
            timeframe=timeframe,
            asof_timestamp=pd.Timestamp(bars.iloc[asof_idx]["dt"]).isoformat(),
            session_label=asof.session,
            horizon_candles=int(horizon_candles),

            bullish_probability=estimates["p_bull"],
            bearish_probability=estimates["p_bear"],
            neutral_probability=estimates["p_neu"],
            confidence=max(estimates["p_bull"], estimates["p_bear"], estimates["p_neu"]),

            expected_return_points=estimates["expected_return"],
            expected_return_atr=(
                estimates["expected_return"] / asof.prior_atr
                if asof.prior_atr > 0 else 0.0
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
            sample_size=int(len(neighbors)),
            effective_sample_size=eff_n,
            method_used=DEFAULT_METHOD,
            fallback_level=int(fallback_level),
            calibration_bucket=bucket_label,
            feature_snapshot={
                "session": asof.session,
                "regime": asof.regime,
                "prior_atr": asof.prior_atr,
                "asof_close": asof.asof_close,
                "feature_vec": asof.feature_vec.tolist(),
                "feature_names": list(_FEATURE_COLS),
            },
            reasoning_summary=(
                f"Weighted KNN on {len(neighbors)} neighbors "
                f"(eff_n={eff_n:.1f}, fallback_level={fallback_level}); "
                f"bandwidth={cfg.bandwidth:g}, k={cfg.k}, "
                f"session={asof.session}, regime={asof.regime}."
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
        asof: _AsofState,
        reason: str,
        sample_size: int,
        fallback_level: int,
    ) -> CandleHorizonPrediction:
        return CandleHorizonPrediction(
            agent_id=self.agent_id,
            instrument=instrument,
            contract=contract,
            timeframe=timeframe,
            asof_timestamp=pd.Timestamp(bars.iloc[asof_idx]["dt"]).isoformat(),
            session_label=asof.session,
            horizon_candles=int(horizon_candles),
            bullish_probability=0.0,
            bearish_probability=0.0,
            neutral_probability=0.0,
            confidence=0.0,
            sample_size=int(sample_size),
            effective_sample_size=0.0,
            method_used=DEFAULT_METHOD,
            fallback_level=int(fallback_level),
            calibration_bucket=(
                f"tf={timeframe},session={asof.session},regime={asof.regime},"
                f"horizon={horizon_candles},fallback={fallback_level}"
            ),
            feature_snapshot={
                "session": asof.session,
                "regime": asof.regime,
                "prior_atr": asof.prior_atr,
            },
            reasoning_summary=f"Abstained: {reason} (samples={sample_size}).",
            abstain=True,
            abstain_reason=reason,
        )


# ---------------------------------------------------------------------------
# Per-bar feature computation (analogue-specific 4-dim vector)
# ---------------------------------------------------------------------------

_FEATURE_COLS = ("atr_zscore", "trend_slope", "body_atr", "momentum_norm")


def _compute_analogue_features(
    bars: pd.DataFrame,
    cfg: AnalogueProbabilityAgentConfig,
) -> pd.DataFrame:
    """
    Compute per-bar features for KNN distance + filtering. Base columns
    (session, regime, atr) come from the shared `horizon_features` module
    so the analogue agent and statistical agent always use identical
    bucket semantics. The 4-dim continuous feature vector is layered on
    top here.

    Warm-up bars carry NaNs in the continuous columns; they are filtered
    out at neighbor-pool construction time.
    """
    base = compute_session_regime_atr(
        bars,
        atr_period=cfg.atr_period,
        sma_period=cfg.sma_period,
        atr_band_frac=cfg.regime_atr_band_frac,
        session_config=cfg.session_config,
    )

    n = len(bars)
    closes = bars["close"].astype(float).to_numpy()
    opens = bars["open"].astype(float).to_numpy()
    highs = bars["high"].astype(float).to_numpy()
    lows = bars["low"].astype(float).to_numpy()

    atr = base["atr"].to_numpy()
    atr_long = compute_atr(highs, lows, closes, cfg.atr_long_period)
    sma = pd.Series(closes).rolling(cfg.sma_period, min_periods=1).mean().to_numpy()

    safe_atr = np.where(atr > 0, atr, np.nan)
    atr_zscore = atr / np.where(atr_long > 0, atr_long, np.nan)
    trend_slope = (closes - sma) / safe_atr
    body_atr = (closes - opens) / safe_atr

    momentum = np.full(n, np.nan, dtype=float)
    for i in range(cfg.momentum_period, n):
        denom = cfg.momentum_period * safe_atr[i]
        if np.isfinite(denom) and denom > 0:
            momentum[i] = (closes[i] - closes[i - cfg.momentum_period]) / denom

    return pd.DataFrame({
        "session": base["session"],
        "regime": base["regime"],
        "atr": atr,
        "atr_zscore": atr_zscore,
        "trend_slope": trend_slope,
        "body_atr": body_atr,
        "momentum_norm": momentum,
    })


# ---------------------------------------------------------------------------
# Neighbor pool construction
# ---------------------------------------------------------------------------

def _build_neighbor_pool(
    bars: pd.DataFrame,
    feats: pd.DataFrame,
    asof_idx: int,
    horizon: int,
    cfg: AnalogueProbabilityAgentConfig,
) -> List[_NeighborRow]:
    """
    For every i where `i + horizon < asof_idx`, compute the lite outcome and
    keep its (feature_vec, session, regime, outcome) snapshot.
    """
    closes = bars["close"].astype(float).to_numpy()
    highs = bars["high"].astype(float).to_numpy()
    lows = bars["low"].astype(float).to_numpy()
    atr_arr = feats["atr"].to_numpy()
    sessions = feats["session"].to_numpy()
    regimes = feats["regime"].to_numpy()
    feat_vecs = feats[list(_FEATURE_COLS)].to_numpy(dtype=float)

    end = asof_idx - horizon
    warmup = max(
        cfg.atr_period, cfg.atr_long_period, cfg.sma_period, cfg.momentum_period
    )
    start = max(warmup, end - cfg.history_lookback_bars)

    rows: List[_NeighborRow] = []
    if end <= start:
        return rows

    for i in range(start, end):
        prior_atr = float(atr_arr[i])
        if prior_atr <= 0.0:
            continue
        if not np.all(np.isfinite(feat_vecs[i])):
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
        rows.append(_NeighborRow(
            idx=i,
            feature_vec=feat_vecs[i].copy(),
            session=str(sessions[i]),
            regime=str(regimes[i]),
            direction=direction,
            return_pts=ret_pts,
            mfe_pts=mfe,
            mae_pts=mae,
            threshold_label=label,
        ))
    return rows


# ---------------------------------------------------------------------------
# Filtering with fallback hierarchy
# ---------------------------------------------------------------------------

def _filter_with_fallback(
    pool: List[_NeighborRow],
    asof: _AsofState,
    cfg: AnalogueProbabilityAgentConfig,
) -> Tuple[List[_NeighborRow], int]:
    """
    Levels:
      0 → session AND regime
      1 → session only (or regime only when only regime is required)
      2 → unfiltered

    The agent reports the level at which it found `min_k_local` survivors.
    """
    if cfg.require_session_match and cfg.require_regime_match:
        full = [r for r in pool if r.session == asof.session and r.regime == asof.regime]
        if len(full) >= cfg.min_k_local:
            return full, 0
    if cfg.require_session_match:
        sess = [r for r in pool if r.session == asof.session]
        if len(sess) >= cfg.min_k_local:
            return sess, 1
    if cfg.require_regime_match:
        reg = [r for r in pool if r.regime == asof.regime]
        if len(reg) >= cfg.min_k_local:
            return reg, 1
    return pool, 2


# ---------------------------------------------------------------------------
# Distance-weighted top-K selection
# ---------------------------------------------------------------------------

def _select_top_k_weighted(
    pool: List[_NeighborRow],
    asof_vec: np.ndarray,
    k: int,
    bandwidth: float,
) -> Tuple[List[_NeighborRow], np.ndarray]:
    """
    Pick the K nearest neighbors by L2 distance and return them with Gaussian
    kernel weights w_i = exp(-d_i² / (2 * h²)).
    """
    if not pool:
        return [], np.empty(0, dtype=float)

    pool_vecs = np.stack([r.feature_vec for r in pool])
    diffs = pool_vecs - asof_vec[None, :]
    dists = np.linalg.norm(diffs, axis=1)

    if k >= len(pool):
        order = np.argsort(dists)
    else:
        idx_part = np.argpartition(dists, k)[:k]
        order = idx_part[np.argsort(dists[idx_part])]

    selected = [pool[i] for i in order]
    sel_dists = dists[order]
    h = max(bandwidth, 1e-9)
    weights = np.exp(-(sel_dists ** 2) / (2.0 * h * h))

    # Defensive: if weights collapse to ~0 (asof very far from neighbors),
    # fall back to uniform weights so we still get a usable estimate.
    if weights.sum() <= 1e-9:
        weights = np.ones_like(weights)

    return selected, weights


# ---------------------------------------------------------------------------
# Weighted aggregation with EB shrinkage to baseline
# ---------------------------------------------------------------------------

def _aggregate_weighted(
    neighbors: List[_NeighborRow],
    weights: np.ndarray,
    baseline_pool: List[_NeighborRow],
    alpha: float,
) -> Dict[str, float]:
    """
    Weighted estimates over `neighbors`, smoothed toward unweighted baseline
    statistics computed on `baseline_pool`. Smoothing strength `alpha` is
    measured in equivalent-sample-size units.
    """
    n_baseline = len(baseline_pool)
    eff_n = float(weights.sum())

    # Direction
    p_bull = _smooth_weighted_prob(neighbors, weights, "direction", "bullish",
                                   baseline_pool, alpha, eff_n)
    p_bear = _smooth_weighted_prob(neighbors, weights, "direction", "bearish",
                                   baseline_pool, alpha, eff_n)
    p_neu = _smooth_weighted_prob(neighbors, weights, "direction", "neutral",
                                  baseline_pool, alpha, eff_n)

    # Threshold
    p_up_first = _smooth_weighted_prob(neighbors, weights, "threshold_label",
                                       "upside_first", baseline_pool, alpha, eff_n)
    p_down_first = _smooth_weighted_prob(neighbors, weights, "threshold_label",
                                         "downside_first", baseline_pool, alpha, eff_n)
    p_both = _smooth_weighted_prob(neighbors, weights, "threshold_label",
                                   "both_same_bar", baseline_pool, alpha, eff_n)
    p_up_first += 0.5 * p_both
    p_down_first += 0.5 * p_both
    p_neither = max(0.0, 1.0 - p_up_first - p_down_first)

    # Returns / MFE / MAE
    returns = np.array([n.return_pts for n in neighbors], dtype=float)
    mfes = np.array([n.mfe_pts for n in neighbors], dtype=float)
    maes = np.array([n.mae_pts for n in neighbors], dtype=float)

    base_returns = np.array([n.return_pts for n in baseline_pool], dtype=float)
    base_mfes = np.array([n.mfe_pts for n in baseline_pool], dtype=float)
    base_maes = np.array([n.mae_pts for n in baseline_pool], dtype=float)

    expected_return = _smooth_weighted_mean(returns, weights, base_returns, alpha)
    expected_mfe = _smooth_weighted_mean(mfes, weights, base_mfes, alpha)
    expected_mae = _smooth_weighted_mean(maes, weights, base_maes, alpha)

    if returns.size > 0:
        # Use weighted percentiles (keeps the distribution sharp in directions
        # where neighbor weight concentrates)
        pcts = _weighted_percentiles(returns, weights, [10, 25, 50, 75, 90])
        return_std = _weighted_std(returns, weights)
    elif base_returns.size > 0:
        pcts = np.percentile(base_returns, [10, 25, 50, 75, 90])
        return_std = float(base_returns.std(ddof=1)) if base_returns.size > 1 else 0.0
    else:
        pcts = np.zeros(5)
        return_std = 0.0

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
    }


def _smooth_weighted_prob(
    neighbors: List[_NeighborRow],
    weights: np.ndarray,
    column: str,
    value: str,
    baseline: List[_NeighborRow],
    alpha: float,
    eff_n: float,
) -> float:
    """
    Beta-binomial style smoothing on a weighted neighbor average:
        p_smooth = (eff_n * p_local + alpha * p_baseline) / (eff_n + alpha)
    where p_local is the weighted fraction of neighbors with column==value
    and p_baseline is the unweighted fraction in `baseline`.
    """
    if eff_n <= 0:
        p_local = 0.0
    else:
        match = np.array([1.0 if getattr(n, column) == value else 0.0 for n in neighbors])
        p_local = float((match * weights).sum() / eff_n)

    n_base = len(baseline)
    if n_base == 0:
        p_base = 1.0 / 3.0
    else:
        match_b = sum(1 for n in baseline if getattr(n, column) == value)
        p_base = match_b / n_base

    return (eff_n * p_local + alpha * p_base) / max(1e-9, eff_n + alpha)


def _smooth_weighted_mean(
    values: np.ndarray,
    weights: np.ndarray,
    baseline: np.ndarray,
    alpha: float,
) -> float:
    eff_n = float(weights.sum())
    if eff_n <= 0:
        local_mean = 0.0
    else:
        local_mean = float((values * weights).sum() / eff_n)
    if baseline.size == 0:
        base_mean = 0.0
    else:
        base_mean = float(baseline.mean())
    return (eff_n * local_mean + alpha * base_mean) / max(1e-9, eff_n + alpha)


def _weighted_std(values: np.ndarray, weights: np.ndarray) -> float:
    eff_n = float(weights.sum())
    if eff_n <= 1e-9 or values.size <= 1:
        return 0.0
    mean = (values * weights).sum() / eff_n
    var = ((values - mean) ** 2 * weights).sum() / eff_n
    return float(np.sqrt(max(0.0, var)))


def _weighted_percentiles(
    values: np.ndarray,
    weights: np.ndarray,
    percentiles: List[float],
) -> np.ndarray:
    """
    Weighted percentiles via cumulative-weight interpolation. Falls back to
    unweighted percentiles if weights sum to ~0.
    """
    if values.size == 0:
        return np.zeros(len(percentiles))
    if weights.sum() <= 1e-9:
        return np.percentile(values, percentiles)
    order = np.argsort(values)
    sorted_vals = values[order]
    sorted_w = weights[order]
    cum = np.cumsum(sorted_w)
    cum_norm = cum / cum[-1]
    targets = np.array(percentiles, dtype=float) / 100.0
    return np.interp(targets, cum_norm, sorted_vals)
