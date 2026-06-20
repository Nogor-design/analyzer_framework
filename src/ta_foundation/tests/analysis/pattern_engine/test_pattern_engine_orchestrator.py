"""Regression tests for pattern_engine/orchestrator.py glue.

These guard two bugs fixed 2026-06-20 that hid because each scope wraps its body
in ``try/except Exception -> _attach_disabled_block`` (so a glue error becomes a
silently-disabled block, not a crash):

  1. Scope B (``market_discovery``) used ``events_exec_df`` without ever assigning
     it in that scope -> NameError -> the whole market-discovery pass was disabled.
  2. Scope A's ``build_pattern_clusters(...)`` call omitted the required keyword-only
     args ``signals_df``/``outcomes_df`` -> TypeError swallowed by an inner
     ``except: pass`` -> clusters were never built.

The heavy engine / Monte-Carlo / discovery / parquet calls are stubbed so the test
is hermetic and fast, but ``build_pattern_clusters`` is kept REAL so the call
signature is actually exercised. The assertions are deliberately strict: a scope
must come back NOT disabled with ``validation.ok == True`` (catches #1), and the
run_attached pass must actually produce a non-empty clusters frame (catches #2).
"""
from __future__ import annotations

import pandas as pd
import pytest

from ta_foundation.core.model import AnalysisPackage
from ta_foundation.analysis.pattern_engine import orchestrator as orch


# --------------------------------------------------------------------------- #
# Fakes / fixtures                                                            #
# --------------------------------------------------------------------------- #
def _small_df(n: int = 3) -> pd.DataFrame:
    return pd.DataFrame({"a": range(n), "b": range(n)})


def _outcomes_df() -> pd.DataFrame:
    """Per-signal outcomes carrying the columns build_pattern_clusters merges on
    (pattern_id) and aggregates (horizon, ret_ticks)."""
    rows = []
    for pid, base in [("p1", 3.0), ("p2", -1.0), ("p3", 6.0)]:
        for h in (5, 20):
            for i in range(4):
                rows.append({"pattern_id": pid, "horizon": h, "ret_ticks": base + i - 1.5})
    return pd.DataFrame(rows)


def _pattern_stats_df() -> pd.DataFrame:
    """Real columns build_pattern_clusters embeds on; >=2 patterns so clustering
    yields output."""
    return pd.DataFrame({
        "pattern_id": ["p1", "p2", "p3"],
        "avg_ticks": [4.0, -2.0, 8.0],
        "win_rate": [0.55, 0.40, 0.62],
        "p10": [-10.0, -12.0, -6.0],
        "p50": [2.0, -1.0, 5.0],
        "p90": [18.0, 9.0, 22.0],
        "n_signals": [120, 80, 200],
    })


def _fake_sweep_result(include_events_exec: bool) -> dict:
    res = {
        "diagnostics": {"ok": True, "issues": []},
        "patterns_df": _small_df(),
        "signals_df": _small_df(),
        "outcomes_df": _outcomes_df(),
        "pattern_stats_df": _pattern_stats_df(),
        "events_df": _small_df(),
    }
    # Deliberately omit events_exec_df in the market_discovery case so the test
    # exercises the orchestrator's ``res.get("events_exec_df", events_df)`` fallback.
    if include_events_exec:
        res["events_exec_df"] = _small_df()
    return res


class _FakeMarket:
    """Stand-in MarketDataStore — only get_bars is touched (and only in Scope B)."""
    def get_bars(self, *args, **kwargs):
        return _small_df(50)


@pytest.fixture(autouse=True)
def _stub_heavy_calls(monkeypatch):
    """Stub everything except build_pattern_clusters (kept real on purpose) and
    keep all disk IO out of the test."""
    monkeypatch.setattr(orch, "run_pattern_sweep",
                        lambda **kw: _fake_sweep_result(include_events_exec=True))
    monkeypatch.setattr(orch, "run_prop_monte_carlo",
                        lambda **kw: {"mc_summary_df": _small_df()})
    monkeypatch.setattr(orch, "run_prop_monte_carlo_regime",
                        lambda **kw: {"mc_regime_summary_df": _small_df(),
                                      "mc_slippage_surface_df": _small_df()})
    monkeypatch.setattr(orch, "compute_purged_walkforward_cv",
                        lambda **kw: {"oos_stats_df": _small_df()})
    monkeypatch.setattr(orch, "compute_market_discovery",
                        lambda **kw: {"discovery_events_df": _small_df(),
                                      "discovery_stats_df": _small_df(),
                                      "discovery_regime_stats_df": _small_df(),
                                      "discovery_stability_df": _small_df()})
    # No parquet to disk; in-memory caching in _write_and_cache_df still runs.
    monkeypatch.setattr(orch, "df_to_parquet", lambda **kw: None)


def _base_options(scope: str) -> dict:
    return {
        "enabled": True,
        "scopes": [scope],
        "instrument": "NQ",
        "contract": "06-26",
        "timeframe": "1m",
        "monte_carlo": {"seed": 7},
        "prop": {"trailing_drawdown_usd": 1500, "daily_loss_limit_usd": 1000, "tick_value_usd": 12.5},
    }


def _pe_block(pkg) -> dict:
    return pkg.metadata["derived"]["pattern_engine"]


# --------------------------------------------------------------------------- #
# Scope A — run_attached                                                       #
# --------------------------------------------------------------------------- #
def test_run_attached_scope_is_not_disabled_and_builds_clusters():
    pkg = AnalysisPackage(run_id="run1")
    packages = {"run1": pkg}

    orch.compute_and_attach_pattern_engine(
        packages=packages, market=_FakeMarket(), options=_base_options("run_attached"))

    block = _pe_block(pkg)
    # Guards both: a swallowed glue exception would flip the block to disabled.
    assert block.get("disabled") is not True, block.get("reason")
    assert block["diagnostics"]["validation"]["ok"] is True

    # Guards bug #2: build_pattern_clusters must have been called with the full
    # (signals_df, outcomes_df, pattern_stats_df, options) signature and produced
    # a non-empty clusters frame, cached in assets.
    clusters = pkg.assets["pattern_engine"].get("clusters")
    assert isinstance(clusters, pd.DataFrame)
    assert not clusters.empty


# --------------------------------------------------------------------------- #
# Scope B — market_discovery (the NameError path)                             #
# --------------------------------------------------------------------------- #
def test_market_discovery_scope_does_not_raise_nameerror(monkeypatch):
    # Make the sweep omit events_exec_df so we exercise the fallback, not luck.
    monkeypatch.setattr(orch, "run_pattern_sweep",
                        lambda **kw: _fake_sweep_result(include_events_exec=False))

    packages: dict = {}  # synthetic market-discovery package is created internally
    options = _base_options("market_discovery")
    options["market_discovery"] = {"instrument": "NQ", "contract": "06-26", "timeframe": "1m"}

    orch.compute_and_attach_pattern_engine(
        packages=packages, market=_FakeMarket(), options=options)

    synth_keys = [k for k in packages if str(k).startswith("__market_discovery__")]
    assert synth_keys, "market_discovery scope did not create a synthetic package"
    block = _pe_block(packages[synth_keys[0]])

    # Pre-fix this block was disabled with reason 'pattern_engine_exception: NameError ...'.
    reason = block.get("reason", "")
    assert block.get("disabled") is not True, reason
    assert "NameError" not in reason
    assert block["diagnostics"]["validation"]["ok"] is True
