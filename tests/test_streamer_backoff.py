"""Tests for the streamer's exponential restart backoff.

The full streamer.run() loop is integration territory (real subprocess
+ real asyncio). We test the pure backoff curve here so the math is
locked: failure-count → seconds-to-wait.
"""

from __future__ import annotations

from rcr.streamer import (
    RESTART_BACKOFF_MAX_S,
    RESTART_BACKOFF_MIN_S,
    compute_backoff_seconds,
)


def test_backoff_zero_failures_returns_minimum():
    """First restart (or after a long successful session reset) uses the
    minimum 3s — no point waiting if there's nothing to back off from."""
    assert compute_backoff_seconds(0) == RESTART_BACKOFF_MIN_S


def test_backoff_negative_treated_as_zero():
    """Defensive: caller shouldn't pass negatives, but if they do, we
    floor to the minimum rather than blowing up."""
    assert compute_backoff_seconds(-3) == RESTART_BACKOFF_MIN_S


def test_backoff_curve_doubles_each_failure():
    """3 → 6 → 12 → 24 → 48 → ... until cap."""
    assert compute_backoff_seconds(1) == RESTART_BACKOFF_MIN_S * 2
    assert compute_backoff_seconds(2) == RESTART_BACKOFF_MIN_S * 4
    assert compute_backoff_seconds(3) == RESTART_BACKOFF_MIN_S * 8
    assert compute_backoff_seconds(4) == RESTART_BACKOFF_MIN_S * 16


def test_backoff_caps_at_max():
    """No matter how many consecutive failures, never wait more than
    RESTART_BACKOFF_MAX_S (5 min) — even broken systems get retried
    eventually, so a transient outage that exceeds 5 minutes recovers
    on the first valid window after."""
    # Huge failure count → capped
    assert compute_backoff_seconds(100) == RESTART_BACKOFF_MAX_S
    # Failure count just past the cap point
    high = compute_backoff_seconds(20)
    assert high == RESTART_BACKOFF_MAX_S


def test_backoff_max_is_reached_around_failure_7():
    """3 × 2^7 = 384, capped at 300. Earlier failures should still be
    under the cap. Sanity-check the breakover."""
    assert compute_backoff_seconds(6) < RESTART_BACKOFF_MAX_S  # 3 × 64 = 192
    assert compute_backoff_seconds(7) == RESTART_BACKOFF_MAX_S  # 3 × 128 = 384 → 300
