from __future__ import annotations

"""Tests for walk-forward window planning."""

import pytest

from ta_foundation.optimization.walkforward import (
    WalkForwardError,
    plan_walk_forward_windows,
)


def test_basic_three_windows_adjacent():
    # window_days is the from→to delta. Stepping back 10 days at a time
    # from 2026-04-30 yields 2026-04-20, 2026-04-10, 2026-03-31.
    windows = plan_walk_forward_windows(
        anchor_date="2026-04-30", window_days=10, count=3, gap_days=0,
    )
    assert [(w.from_date, w.to_date) for w in windows] == [
        ("2026-03-31", "2026-04-10"),  # oldest first
        ("2026-04-10", "2026-04-20"),
        ("2026-04-20", "2026-04-30"),
    ]
    assert [w.index for w in windows] == [0, 1, 2]


def test_gap_between_windows():
    windows = plan_walk_forward_windows(
        anchor_date="2026-05-01", window_days=5, count=2, gap_days=2,
    )
    # Most recent: 2026-04-26 .. 2026-05-01
    # Previous: ends 2026-04-26 - 2 = 2026-04-24, length 5, from = 2026-04-19
    assert [(w.from_date, w.to_date) for w in windows] == [
        ("2026-04-19", "2026-04-24"),
        ("2026-04-26", "2026-05-01"),
    ]


def test_skip_window_overlapping_is_range():
    # Anchor 2026-05-01, window=10, count=3 -> would produce
    # [2026-04-01..2026-04-11, 2026-04-11..2026-04-21, 2026-04-21..2026-05-01]
    # If skip_overlap_with = (2026-04-15, 2026-04-25), the middle and
    # last windows overlap. Both excluded.
    windows = plan_walk_forward_windows(
        anchor_date="2026-05-01", window_days=10, count=3, gap_days=0,
        skip_overlap_with=("2026-04-15", "2026-04-25"),
    )
    assert [(w.from_date, w.to_date) for w in windows] == [
        ("2026-04-01", "2026-04-11"),
    ]
    assert windows[0].index == 0


def test_rejects_bad_inputs():
    with pytest.raises(WalkForwardError):
        plan_walk_forward_windows(anchor_date="2026-05-01", window_days=0, count=1)
    with pytest.raises(WalkForwardError):
        plan_walk_forward_windows(anchor_date="2026-05-01", window_days=10, count=0)
    with pytest.raises(WalkForwardError):
        plan_walk_forward_windows(anchor_date="2026-05-01", window_days=10, count=1, gap_days=-1)
    with pytest.raises(WalkForwardError):
        plan_walk_forward_windows(anchor_date="nope", window_days=10, count=1)
    with pytest.raises(WalkForwardError):
        # skip range reversed
        plan_walk_forward_windows(
            anchor_date="2026-05-01", window_days=10, count=1,
            skip_overlap_with=("2026-05-01", "2026-04-01"),
        )


def test_windows_are_chronological_and_indexed_in_order():
    windows = plan_walk_forward_windows(
        anchor_date="2026-06-01", window_days=7, count=5,
    )
    for i in range(len(windows) - 1):
        assert windows[i].to_date <= windows[i + 1].from_date or \
               windows[i].to_date == windows[i + 1].from_date
        assert windows[i].index == i
