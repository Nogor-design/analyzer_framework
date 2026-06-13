"""Survival-filtered daily-lineup head-to-head (links 2+3 integration).

The survival harness (``analysis/risk/survival.py``) showed ~half a pool blows an
APEX 50k account at 1 NQ contract. The selector spine ranks edges but never knew
about account survival. This ties them together and asks the product question:

  Does pre-filtering the candidate universe to APEX-survivable templates improve
  the production selector's OUT-OF-SAMPLE survival (max drawdown / Sharpe) without
  giving up expectancy?

It replays every baseline + composite selector on (a) the full pool and (b) the
survival-filtered pool over the same walk-forward calendar, and prints the two
metric tables side by side. Pure read over data we already produce.

Usage:
  python scripts/apex_daily_lineup.py [SESSION_ID] [--firm APEX] [--size 50000]
      [--scale 1.0] [--require-pass]
"""
from __future__ import annotations

import argparse
from pathlib import Path

from ta_foundation.analysis.selection.baselines import DEFAULT_BASELINES
from ta_foundation.analysis.selection.loader import (
    load_candidates_from_session,
    survivable_template_ids,
)
from ta_foundation.analysis.selection.replay import compare_selectors
from ta_foundation.analysis.selection.scoring import composite_selector

_SROOT = Path(".ta_artifacts/web_optimizer/sessions")


def _fmt(s: dict) -> str:
    sh = s.get("daily_sharpe")
    return (f"{s['n_test_days']:>4}d  net {s['net']:>9.0f}  exp/day {s['expectancy_daily']:>7.0f}  "
            f"hit {s['hit_rate']*100:>4.0f}%  maxDD {s['max_drawdown']:>8.0f}  "
            f"sharpe {('%.2f' % sh) if sh is not None else '  n/a'}")


def _table(title: str, results: dict) -> None:
    print(f"\n{title}")
    for name in sorted(results, key=lambda n: (results[n].get('daily_sharpe') or -9)):
        print(f"  {name:<22}{_fmt(results[name])}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("session", nargs="?", default="opt_a09359e6b60b")
    ap.add_argument("--firm", default="APEX")
    ap.add_argument("--size", default="50000")
    ap.add_argument("--scale", type=float, default=1.0, help="1.0=NQ, 0.1=MNQ")
    ap.add_argument("--require-pass", action="store_true",
                    help="keep only templates that also clear the profit target alive")
    args = ap.parse_args()

    sdir = Path(args.session) if Path(args.session).exists() else _SROOT / args.session
    cands, regime_by_day = load_candidates_from_session(sdir)
    if not cands:
        print("no candidates; check session path")
        return 1

    keep = survivable_template_ids(
        sdir, firm=args.firm, account_size=args.size,
        dollar_scale=args.scale, require_pass=args.require_pass,
    )
    filtered = [c for c in cands if c.template_id in keep]

    selectors = dict(DEFAULT_BASELINES)
    selectors["composite_v1"] = composite_selector

    full = compare_selectors(cands, selectors, regime_by_day=regime_by_day)
    filt = compare_selectors(filtered, selectors, regime_by_day=regime_by_day)

    scale_lbl = "NQ" if args.scale == 1.0 else ("MNQ" if args.scale == 0.1 else f"x{args.scale}")
    print(f"=== Survival-filtered daily-lineup head-to-head ===")
    print(f"session {sdir.name}  firm {args.firm} {args.size}  scale {scale_lbl}  "
          f"{'(pass-required)' if args.require_pass else '(survive-required)'}")
    print(f"universe: full={len(cands)} templates  ->  survival-filtered={len(filtered)}")

    _table("FULL pool:", full)
    _table(f"SURVIVAL-FILTERED pool ({len(filtered)} templates):", filt)

    # Decision read: did filtering help the production (best-survival) selector?
    def best(results):
        return max(results, key=lambda n: (results[n].get("daily_sharpe") or -9))
    bf, bff = best(full), best(filt)
    print(f"\nBest-survival selector: full='{bf}' (sharpe "
          f"{full[bf].get('daily_sharpe')}), filtered='{bff}' (sharpe "
          f"{filt[bff].get('daily_sharpe')}).")
    print("Read: a higher Sharpe / shallower maxDD on the filtered pool means the "
          "survival pre-filter is a free survival win; same-or-better expectancy means "
          "it costs nothing. This is the candidate-universe gate to add before selection.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
