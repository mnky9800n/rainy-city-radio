"""Tests for the music selector. Pure function → deterministic with a seeded RNG."""

from __future__ import annotations

from collections import Counter, deque
from pathlib import Path
from random import Random

import pytest

from rcr.music.selector import (
    ARC_BLOCK_SECONDS,
    ARC_MOODS,
    ARC_SEQUENCE,
    ArcState,
    recent_n,
    select,
)
from rcr.music.tracks import Track


def make_track(name: str, *, bpm: float, energy: int, mood=("chill",)) -> Track:
    return Track(
        path=Path(f"music/{name}.mp3"),
        bpm=bpm,
        energy=energy,
        mood=tuple(mood),
        duration=180.0,
        onset_strength=1.0,
        fictional_artist=f"Artist of {name}",
    )


@pytest.fixture
def lib():
    """A small library spanning the bpm/energy/mood space."""
    return [
        make_track("slow_chill", bpm=80, energy=2, mood=("chill", "rainy")),
        make_track("steady_groove", bpm=110, energy=3, mood=("groovy", "cinematic")),
        make_track("near_steady", bpm=118, energy=3, mood=("groovy",)),
        make_track("driving", bpm=130, energy=4, mood=("driving", "peak")),
        make_track("intense", bpm=145, energy=5, mood=("intense", "peak")),
        make_track("dreamy", bpm=85, energy=2, mood=("dreamy", "melancholy")),
    ]


def chill_arc(now: float = 0.0) -> ArcState:
    """An arc state whose phase at `now` is 'chill'."""
    return ArcState(started_at=now, block_seconds=ARC_BLOCK_SECONDS)


def peak_arc(now: float = 0.0) -> ArcState:
    """An arc state whose phase at `now` is 'peak'."""
    # peak is index 2 of the 5-step sequence; place `now` mid-peak.
    slice_dur = ARC_BLOCK_SECONDS / len(ARC_SEQUENCE)
    return ArcState(started_at=now - 2.5 * slice_dur, block_seconds=ARC_BLOCK_SECONDS)


def test_recent_n_clamps():
    assert recent_n(0) == 1
    assert recent_n(1) == 1
    assert recent_n(3) == 1
    assert recent_n(15) == 5
    assert recent_n(30) == 10
    assert recent_n(1000) == 10  # capped


def test_select_empty_library_raises():
    with pytest.raises(ValueError):
        select([], deque(), None, chill_arc(), Random(0))


def test_select_returns_one(lib):
    rng = Random(42)
    t = select(lib, deque(), None, chill_arc(), rng, now=0.0)
    assert t in lib


def test_ring_buffer_excludes_recent(lib):
    # With 6 tracks, recent_n = 6 // 3 = 2. Pick the 2 most recent.
    recent = deque([lib[0].path, lib[1].path])
    rng = Random(0)
    for _ in range(20):
        t = select(lib, recent, last=None, arc=chill_arc(), rng=rng, now=0.0)
        assert t.path not in recent


def test_ring_filter_bypassed_when_library_smaller_than_window(lib):
    tiny = lib[:2]
    # n_recent for size-2 lib is 1 by formula; ring has both tracks.
    recent = deque([tiny[0].path, tiny[1].path])
    rng = Random(0)
    t = select(tiny, recent, last=None, arc=chill_arc(), rng=rng, now=0.0)
    # We don't crash, and we still return something from the library.
    assert t in tiny


def test_continuity_prefers_close_bpm_and_energy(lib):
    last = make_track("anchor", bpm=110, energy=3, mood=("groovy",))
    rng = Random(7)
    # Run many times — every pick must be within ±15 BPM / ±1 energy of last.
    for _ in range(50):
        t = select(lib, deque(), last=last, arc=chill_arc(), rng=rng, now=0.0)
        assert abs(t.bpm - last.bpm) <= 15.0
        assert abs(t.energy - last.energy) <= 1


def test_continuity_relaxes_when_band_empty():
    # Last track is far from anything; band 1 (±15) is empty, band 2 (±25)
    # contains exactly one track.
    last = make_track("far", bpm=160, energy=5)
    pool = [
        make_track("close_enough", bpm=140, energy=4),  # in ±25, ±1
        make_track("nope", bpm=80, energy=2),
    ]
    rng = Random(0)
    for _ in range(20):
        t = select(pool, deque(), last=last, arc=chill_arc(), rng=rng, now=0.0)
        assert t.path.name == "close_enough.mp3"


def test_continuity_doesnt_apply_with_no_last(lib):
    # Without `last`, no continuity filter — any track can come up.
    rng = Random(1)
    seen = {select(lib, deque(), None, chill_arc(), rng, now=0.0).path
            for _ in range(200)}
    assert len(seen) >= 4  # we hit a healthy fraction of the library


def test_arc_phase_cycles_through_sequence():
    arc = ArcState(started_at=0.0, block_seconds=ARC_BLOCK_SECONDS)
    slice_dur = ARC_BLOCK_SECONDS / len(ARC_SEQUENCE)
    # Sample mid-slice for each phase, expect them in order.
    phases = [arc.phase(now=(i + 0.5) * slice_dur) for i in range(len(ARC_SEQUENCE))]
    assert tuple(phases) == ARC_SEQUENCE


def test_arc_weighting_biases_toward_phase_moods(lib):
    """Per-track: in 'peak' phase, peak-tagged tracks should be picked ~2x more
    often than non-peak tracks of comparable count.

    Aggregate share isn't the right comparison — with 2 peak tracks at 2x and
    4 others at 1x the aggregate shares are equal (4/8 each); the bias is in
    the per-track expected count.
    """
    rng = Random(123)
    counts: Counter[str] = Counter()
    for _ in range(4000):
        t = select(lib, deque(), last=None, arc=peak_arc(now=1000.0),
                   rng=rng, now=1000.0)
        counts[t.path.name] += 1

    peak_tagged = [t for t in lib if set(t.mood) & ARC_MOODS["peak"]]
    other = [t for t in lib if not (set(t.mood) & ARC_MOODS["peak"])]

    avg_peak = sum(counts[t.path.name] for t in peak_tagged) / len(peak_tagged)
    avg_other = sum(counts[t.path.name] for t in other) / len(other)

    # Expected ratio is 2.0; allow a generous slack for RNG noise at N=4000.
    ratio = avg_peak / avg_other
    assert 1.6 < ratio < 2.4, f"per-track peak/other ratio = {ratio:.2f}"


def test_continuity_and_recent_compose(lib):
    """Both filters apply together: recent excluded AND continuity respected."""
    last = make_track("anchor", bpm=130, energy=4, mood=("driving",))
    recent = deque([t.path for t in lib[:2]])  # exclude slow_chill, steady_groove
    rng = Random(99)
    for _ in range(50):
        t = select(lib, recent, last=last, arc=chill_arc(), rng=rng, now=0.0)
        assert t.path not in recent
        assert abs(t.bpm - last.bpm) <= 15.0 or abs(t.bpm - last.bpm) <= 25.0
