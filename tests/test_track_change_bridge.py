"""Integration tests for the MusicFeeder→Scheduler handshake bridge.

The bridge is the most novel concurrency primitive in the codebase: a sync
method (`JenniferScheduler.plan_transition`) called from the music-feeder
thread, which hops onto the asyncio loop via `run_coroutine_threadsafe`,
runs the async planner, and returns a pause-duration to the feeder.

We don't open a real FIFO here — the test mocks the player so the planner's
segment-picking and side-effect-enqueuing logic is verified without
touching ffmpeg or the actual voice queue. The `asyncio.to_thread` call
simulates the feeder thread crossing into asyncio territory.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from random import Random
from typing import Any

import pytest

import rcr.jennifer.scheduler as scheduler_mod
from rcr.jennifer.feeder import VoiceFeeder
from rcr.jennifer.scheduler import JenniferScheduler, select_break_commercials
from rcr.music.tracks import Track


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def make_track(name: str = "Test - Track", with_release: bool = False) -> Track:
    return Track(
        path=Path(f"music/{name}.mp3"),
        bpm=100.0, energy=3, mood=("chill",),
        duration=180.0, onset_strength=0.5,
        real_title=name.split(" - ", 1)[-1] if " - " in name else name,
        real_artist=name.split(" - ", 1)[0] if " - " in name else "Artist",
        release="Some Album" if with_release else None,
    )


def fake_bake_intros(tmp_path: Path, track_name: str, ids: list[str]) -> Path:
    intros_dir = tmp_path / "track_intros"
    intros_dir.mkdir(exist_ok=True)
    for tid in ids:
        (intros_dir / f"{track_name}__{tid}.mp3").write_bytes(b"fake-mp3-data")
    return intros_dir


class FakePlayer:
    """Stand-in for JenniferPlayer that records sequences instead of decoding."""

    def __init__(self):
        self.played: list[list[Path]] = []

    async def play_sequence(self, paths: list[Path]) -> None:
        self.played.append(list(paths))

    async def play_mp3(self, path: Path) -> None:
        self.played.append([path])


def make_scheduler(tmp_path: Path, intros_dir: Path | None = None,
                   *, intro_chance: float = 1.0, outro_chance: float = 1.0,
                   first_delay_s: float = 999.0) -> JenniferScheduler:
    """Construct a scheduler suitable for bridge testing.

    Bypasses run() — caller sets _loop manually. Default intro/outro chance
    is 1.0 so dice rolls don't add noise to tests of "did the bridge fire?".
    """
    voice_feeder = VoiceFeeder(tmp_path / "voice.fifo.unused")
    intros = intros_dir if intros_dir is not None else tmp_path / "empty_intros"
    intros.mkdir(exist_ok=True)
    s = JenniferScheduler(
        voice_feeder=voice_feeder,
        spots_dir=tmp_path / "spots",
        intros_dir=intros,
        rng=Random(0),
        first_delay_s=first_delay_s,
        intro_chance=intro_chance,
        outro_chance=outro_chance,
    )
    return s


# ---------------------------------------------------------------------------
# Bridge: pre-run / stopped guards
# ---------------------------------------------------------------------------

def test_plan_transition_before_loop_capture_returns_zero(tmp_path):
    """Called before scheduler.run() captures the asyncio loop, the bridge
    must return 0 without crashing. First-track events that race startup
    aren't worth crashing for."""
    s = make_scheduler(tmp_path)
    assert s._loop is None
    assert s.plan_transition(None, make_track()) == 0.0


async def test_plan_transition_after_stop_returns_zero(tmp_path):
    """Stopped scheduler returns 0 without running the planner — even if a
    feeder thread fires one more transition during shutdown."""
    s = make_scheduler(tmp_path)
    s._loop = asyncio.get_running_loop()
    s.player = FakePlayer()  # type: ignore[assignment]
    s.stop()
    result = await asyncio.to_thread(s.plan_transition, None, make_track())
    assert result == 0.0
    # And no segments should have been queued.
    await asyncio.sleep(0.05)
    assert s.player.played == []  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Bridge: happy path across threads
# ---------------------------------------------------------------------------

async def test_plan_transition_bridges_to_asyncio_and_fires_play_sequence(tmp_path):
    """The realistic case: feeder thread calls plan_transition, asyncio loop
    is in another thread. Bridge must hop into asyncio, run the planner,
    enqueue segments on the player, return 0 for inline mode."""
    track = make_track("Test - Foo")
    intros_dir = fake_bake_intros(tmp_path, "Test - Foo", ["intro_simple"])
    s = make_scheduler(tmp_path, intros_dir, intro_chance=1.0, outro_chance=0.0)
    s._loop = asyncio.get_running_loop()
    fake = FakePlayer()
    s.player = fake  # type: ignore[assignment]

    # Feeder thread crossing into asyncio:
    pause_s = await asyncio.to_thread(s.plan_transition, None, track)

    assert pause_s == 0.0  # M3.5 inline mode

    # The async planner fire-and-forgets a play_sequence task. Yield to let
    # it run.
    await asyncio.sleep(0.05)
    assert len(fake.played) == 1
    assert len(fake.played[0]) == 1
    assert "Test - Foo__intro_simple" in fake.played[0][0].name


async def test_plan_transition_with_no_baked_content_returns_zero_no_play(tmp_path):
    """Dice rolled to fire intros but nothing's baked — planner returns 0
    and doesn't enqueue empty segments."""
    s = make_scheduler(tmp_path, intro_chance=1.0, outro_chance=1.0)
    s._loop = asyncio.get_running_loop()
    fake = FakePlayer()
    s.player = fake  # type: ignore[assignment]

    pause_s = await asyncio.to_thread(s.plan_transition, None, make_track())
    assert pause_s == 0.0
    await asyncio.sleep(0.05)
    assert fake.played == []


async def test_plan_transition_includes_outro_when_prev_present(tmp_path):
    """With both prev and current and baked content for both, segment list
    should have outro(prev) first then intro(current)."""
    intros_dir = fake_bake_intros(tmp_path, "Artist - Prev", ["outro_simple"])
    fake_bake_intros(tmp_path, "Artist - Next", ["intro_simple"])
    # Note: same dir, two tracks' content.
    s = make_scheduler(tmp_path, intros_dir, intro_chance=1.0, outro_chance=1.0)
    s._loop = asyncio.get_running_loop()
    fake = FakePlayer()
    s.player = fake  # type: ignore[assignment]

    prev = make_track("Artist - Prev")
    current = make_track("Artist - Next")
    pause_s = await asyncio.to_thread(s.plan_transition, prev, current)
    assert pause_s == 0.0
    await asyncio.sleep(0.05)
    assert len(fake.played) == 1
    names = [p.name for p in fake.played[0]]
    assert any("outro_simple" in n for n in names)
    assert any("intro_simple" in n for n in names)
    # Outro of just-finished plays before intro of next.
    outro_idx = next(i for i, n in enumerate(names) if "outro_" in n)
    intro_idx = next(i for i, n in enumerate(names) if "intro_" in n)
    assert outro_idx < intro_idx


# ---------------------------------------------------------------------------
# Bridge: timeout fallback
# ---------------------------------------------------------------------------

async def test_plan_transition_inline_when_talk_break_disabled(tmp_path):
    """talk_break_every_n=0 means never fire talk-breaks; inline only."""
    intros_dir = fake_bake_intros(tmp_path, "Test - Foo", ["intro_simple"])
    s = make_scheduler(tmp_path, intros_dir, intro_chance=1.0, outro_chance=0.0)
    s.talk_break_every_n = 0
    s._loop = asyncio.get_running_loop()
    fake = FakePlayer()
    s.player = fake  # type: ignore[assignment]
    track = make_track("Test - Foo")
    # Even after many transitions the planner should never go talk-break.
    for _ in range(10):
        pause_s = await asyncio.to_thread(s.plan_transition, None, track)
        assert pause_s == 0.0
    await asyncio.sleep(0.05)


async def test_plan_transition_fires_talk_break_every_n_when_commercial_baked(tmp_path):
    """When talk_break_every_n=2 and a commercial is baked, the 2nd transition
    fires a talk-break (non-zero pause) and resets the counter."""
    import shutil
    from rcr.jennifer.commercials import COMMERCIALS
    # Use a real baked spot from disk as a stand-in commercial mp3 — we just
    # need an actual mp3 ffprobe can read.
    src_pool = list(Path("jennifer/spots").glob("*.mp3")) if Path("jennifer/spots").exists() else []
    if not src_pool or not COMMERCIALS:
        pytest.skip("no baked spots or no COMMERCIALS catalog to test against")
    commercials_dir = tmp_path / "commercials"
    commercials_dir.mkdir()
    # Stamp the catalog's first commercial id on a real mp3.
    test_id = COMMERCIALS[0].id
    shutil.copyfile(src_pool[0], commercials_dir / f"{test_id}.mp3")

    intros_dir = fake_bake_intros(tmp_path, "Test - Foo", ["intro_simple"])
    s = make_scheduler(tmp_path, intros_dir, intro_chance=1.0, outro_chance=0.0)
    s.commercials_dir = commercials_dir
    s.talk_break_every_n = 2
    s._loop = asyncio.get_running_loop()
    fake = FakePlayer()
    s.player = fake  # type: ignore[assignment]
    track = make_track("Test - Foo")

    # First transition: not yet at the threshold → inline mode, pause=0.
    pause1 = await asyncio.to_thread(s.plan_transition, None, track)
    assert pause1 == 0.0
    # Second transition: hits threshold + commercial baked → talk-break.
    pause2 = await asyncio.to_thread(s.plan_transition, track, track)
    assert pause2 > 0.0, "talk-break should return positive duration"
    # Counter must reset; third transition is inline again.
    pause3 = await asyncio.to_thread(s.plan_transition, track, track)
    assert pause3 == 0.0


async def test_plan_transition_falls_back_to_inline_when_no_commercial_baked(tmp_path):
    """talk_break_every_n triggers but commercials_dir is empty → inline mode."""
    intros_dir = fake_bake_intros(tmp_path, "Test - Foo", ["intro_simple"])
    s = make_scheduler(tmp_path, intros_dir, intro_chance=1.0, outro_chance=0.0)
    s.commercials_dir = tmp_path / "no_commercials"  # doesn't exist
    s.talk_break_every_n = 1  # every transition is a talk-break opportunity
    s._loop = asyncio.get_running_loop()
    fake = FakePlayer()
    s.player = fake  # type: ignore[assignment]
    track = make_track("Test - Foo")
    # Should always return 0 (inline fallback), since nothing is baked.
    for _ in range(5):
        pause_s = await asyncio.to_thread(s.plan_transition, None, track)
        assert pause_s == 0.0


async def test_plan_transition_times_out_gracefully(tmp_path, monkeypatch):
    """If the async planner hangs longer than the configured timeout, the
    sync bridge bails and returns 0 instead of blocking the feeder thread."""
    # Shrink the timeout so the test is fast.
    monkeypatch.setattr(scheduler_mod, "TRANSITION_PLAN_TIMEOUT_S", 0.2)

    s = make_scheduler(tmp_path)
    s._loop = asyncio.get_running_loop()
    s.player = FakePlayer()  # type: ignore[assignment]

    # Replace the async planner with one that hangs forever.
    async def hang(prev: Any, current: Any) -> float:
        await asyncio.sleep(60)
        return 99.0

    monkeypatch.setattr(s, "_plan_transition_async", hang)

    start = time.monotonic()
    pause_s = await asyncio.to_thread(s.plan_transition, None, make_track())
    elapsed = time.monotonic() - start

    assert pause_s == 0.0
    # Should bail within roughly the timeout, well under 1s.
    assert elapsed < 1.0, f"plan_transition took {elapsed:.2f}s, expected < 1s"


# ---------------------------------------------------------------------------
# select_break_commercials — multi-commercial picker for talk-breaks
# ---------------------------------------------------------------------------

def _stamp_fake_commercial(commercials_dir: Path, commercial_id: str) -> Path:
    """Write a placeholder mp3 at <id>.mp3 so the selector treats it as baked.
    File contents don't matter for the selector — only existence + non-zero."""
    commercials_dir.mkdir(exist_ok=True)
    p = commercials_dir / f"{commercial_id}.mp3"
    p.write_bytes(b"fake-mp3-data")
    return p


def test_select_break_returns_empty_when_no_commercials_baked(tmp_path):
    assert select_break_commercials(tmp_path / "nope", Random(0), n=2) == []


def test_select_break_returns_empty_for_zero_n(tmp_path):
    """Defensive: caller asks for 0 commercials → empty list, not a fallback."""
    from rcr.jennifer.commercials import COMMERCIALS
    cdir = tmp_path / "c"
    _stamp_fake_commercial(cdir, COMMERCIALS[0].id)
    assert select_break_commercials(cdir, Random(0), n=0) == []


def test_select_break_returns_distinct_picks(tmp_path):
    """No duplicate commercial.id across the returned picks."""
    from rcr.jennifer.commercials import COMMERCIALS
    cdir = tmp_path / "c"
    # Stamp 6 from the catalog onto disk.
    for c in COMMERCIALS[:6]:
        _stamp_fake_commercial(cdir, c.id)
    picks = select_break_commercials(cdir, Random(0), n=3)
    assert len(picks) == 3
    ids = [c.id for c, _ in picks]
    assert len(set(ids)) == 3


def test_select_break_handles_n_larger_than_library(tmp_path):
    """Asking for more commercials than exist → returns everything available."""
    from rcr.jennifer.commercials import COMMERCIALS
    cdir = tmp_path / "c"
    for c in COMMERCIALS[:2]:
        _stamp_fake_commercial(cdir, c.id)
    picks = select_break_commercials(cdir, Random(0), n=5)
    assert len(picks) == 2
    assert {c.id for c, _ in picks} == {COMMERCIALS[0].id, COMMERCIALS[1].id}


def test_select_break_prefers_category_variety(tmp_path):
    """When commercials from multiple categories are available, the picker
    should fill across distinct categories before repeating any one."""
    from rcr.jennifer.commercials import COMMERCIALS
    cdir = tmp_path / "c"
    # Stamp commercials across all 5 categories so the picker has variety.
    # Pick 2 from each available category to give the picker choice within.
    by_cat: dict[str, list] = {}
    for c in COMMERCIALS:
        by_cat.setdefault(c.category, []).append(c)
    for cat, items in by_cat.items():
        for c in items[:2]:
            _stamp_fake_commercial(cdir, c.id)
    # Ask for 3 — should get one per category, all distinct.
    rng = Random(0)
    picks = select_break_commercials(cdir, rng, n=3)
    assert len(picks) == 3
    categories = [c.category for c, _ in picks]
    # All three picks must be from DIFFERENT categories (since we have >=3 cats
    # available, the variety pass fills before any category repeats).
    assert len(set(categories)) == 3


def test_select_break_repeats_categories_when_n_exceeds_distinct_cats(tmp_path):
    """If n > number of distinct categories available, pad with non-cat-matched
    picks (still no duplicate commercial ids)."""
    from rcr.jennifer.commercials import COMMERCIALS
    cdir = tmp_path / "c"
    # Stamp 6 commercials but all from the same category — easiest: take the
    # first 6 of a single category.
    first_cat = COMMERCIALS[0].category
    same_cat = [c for c in COMMERCIALS if c.category == first_cat][:6]
    for c in same_cat:
        _stamp_fake_commercial(cdir, c.id)
    picks = select_break_commercials(cdir, Random(0), n=3)
    assert len(picks) == 3
    ids = [c.id for c, _ in picks]
    assert len(set(ids)) == 3  # still no duplicates
