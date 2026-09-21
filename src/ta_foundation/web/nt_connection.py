"""Read-only view of NinjaTrader's market-data connection state.

Why this exists
---------------
The Strategy Analyzer cannot run a backtest without a market-data feed to
source bars from. With the feed down, ``RunCommand.CanExecute`` returns false
no matter how warm, open, focused or correctly-templated the Analyzer is, and
the dispatch fails with::

    Strategy Analyzer Run command was not executable after 12 attempts.

That refusal was misdiagnosed for weeks as Analyzer state. It was established
on 2026-09-20 as a disconnected feed: the refusal was reproduced on two
instruments, removed by reconnecting, after which every dispatch succeeded on
the first attempt with zero retries. See
``D:\\trading-capability-hub\\docs\\audits\\NT_ANALYZER_RUN_REFUSAL_ROOT_CAUSE_2026-09-20.md``.

NinjaTrader runs a nightly connection cycle at 01:00. When that reconnect
fails the feed can stay down all day while NinjaTrader still looks perfectly
healthy -- the process is alive, the Control Center is up, the AddOn answers,
and templates load cleanly. Only the run refuses.

This module READS NinjaTrader's own log. It places no orders, enables no
connection and touches no account. Reconnecting is a manual Control Center
action by design.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

NT_LOG_DIR = Path.home() / "Documents" / "NinjaTrader 8" / "log"

# e.g. "Primary connection=Connected, Price feed=Connection lost"
RE_STATE = re.compile(
    r"Primary connection=(?P<primary>[A-Za-z ]+?),\s*Price feed=(?P<feed>[A-Za-z ]+)"
)

_HEALTHY = "connected"


class FeedDownError(RuntimeError):
    """The market-data feed is not connected, so a dispatch cannot succeed."""


def log_path(day: date | None = None) -> Path:
    """Path to NinjaTrader's English log for ``day`` (default today)."""
    day = day or date.today()
    return NT_LOG_DIR / f"log.{day:%Y%m%d}.00000.en.txt"


def last_connection_state(day: date | None = None) -> tuple[str, str] | None:
    """Return ``(primary, price_feed)`` from the most recent state line.

    Returns ``None`` when the log is absent or contains no state line -- which
    is a real and common case (a day with no connection events logged), and is
    deliberately NOT treated as "down". See :func:`check_feed_connected`.
    """
    path = log_path(day)
    try:
        text = path.read_text(errors="ignore")
    except OSError:
        return None

    last: tuple[str, str] | None = None
    for line in text.splitlines():
        m = RE_STATE.search(line)
        if m:
            last = (m.group("primary").strip(), m.group("feed").strip())
    return last


def check_feed_connected(day: date | None = None) -> tuple[bool, str]:
    """Decide whether to dispatch. Returns ``(ok_to_dispatch, reason)``.

    Policy is **hard fail**: a feed observed down blocks the dispatch outright
    rather than warning and trying anyway. Against a down feed all four
    re-dispatches are certain to fail, so the backoff (20s/45s/90s) spends four
    minutes to reach a foregone conclusion and then reports "confirm it is
    logged in", which is true and irrelevant.

    The one concession to the log being a proxy rather than the live socket:
    *absence* of evidence is not evidence of a down feed. If no state line
    exists we allow the dispatch and say so, because blocking on a silent log
    would refuse runs that would have worked. We only hard-fail on a state line
    that positively says the feed is down.
    """
    state = last_connection_state(day)

    if state is None:
        return True, (
            f"no connection state logged in {log_path(day).name}; "
            "proceeding (absence of evidence is not evidence of a down feed)"
        )

    primary, feed = state
    if feed.lower() == _HEALTHY and primary.lower() == _HEALTHY:
        return True, f"feed healthy (Primary={primary}, Price feed={feed})"

    return False, (
        f"market-data feed is NOT connected (Primary={primary}, Price feed={feed}). "
        "The Strategy Analyzer cannot run a backtest without a feed, so every "
        "dispatch would be refused with 'Run command was not executable'. "
        "Reconnect by hand in the NinjaTrader Control Center "
        "(Connections -> your connection -> Connect), then re-run. "
        f"Source: {log_path(day)}"
    )


def require_feed_connected(day: date | None = None) -> str:
    """Hard-fail wrapper: raise :class:`FeedDownError` unless the feed is up."""
    ok, reason = check_feed_connected(day)
    if not ok:
        raise FeedDownError(reason)
    return reason


if __name__ == "__main__":  # quick manual probe
    ok, reason = check_feed_connected()
    print(f"ok_to_dispatch={ok}\n{reason}")
