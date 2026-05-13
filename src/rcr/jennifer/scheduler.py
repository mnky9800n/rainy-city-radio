"""Decides *when* Jennifer talks and picks what she says.

Two parallel mechanisms drive Jennifer's airtime:

1. **Periodic spots** — every ~10 min (jittered), drawn from the static
   baked pool in `jennifer/spots/`. Category weights bias toward station
   IDs with patter and a time-of-day lore drop sprinkled in.

2. **Track-change intros/outros** (M3.5) — when `MusicFeeder` transitions
   to a new track, the scheduler rolls dice and may play:
       - an *outro* for the just-finished track (~30% of transitions)
       - an *intro* for the upcoming track (~65% of transitions)
   Both are pre-baked into `jennifer/track_intros/<stem>__<template>.mp3`
   by `tools/generate_intros.py`. The dice are independent, so ~20% of
   transitions get both.

   The bridge from `MusicFeeder` (which runs on a blocking IO thread)
   into asyncio happens via `track_change_callback`, which uses
   `loop.call_soon_threadsafe`. The loop reference is captured in `run()`.

Deferred:
    - Chat-reactive replies — M4.
    - Talk-break segments (commercials, monologues that pause the playlist) — M4.5.

`pick_spot` is pure (library, hour, rng) -> Spot|None for testability; the
loop in `JenniferScheduler.run()` is the only piece touching wall-clock
time and the voice feeder. Track-intro picking is similarly factored into
`pick_baked_intro_or_outro` for the same reason.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import logging
import random
from pathlib import Path

from rcr.jennifer.feeder import VoiceFeeder
from rcr.jennifer.player import JenniferPlayer
from rcr.jennifer.spots import SPOTS, Category, Spot, category_for_hour
from rcr.music.tracks import Track, load_library

log = logging.getLogger(__name__)

DEFAULT_SPOTS_DIR = Path("jennifer/spots")
DEFAULT_INTROS_DIR = Path("jennifer/track_intros")

# Mean seconds between spots, with ±JITTER_S uniform noise. A real DJ would
# vary this further (denser when there's chat, sparser late night) but for
# v1 a simple jittered period is enough.
MEAN_INTERVAL_S = 600.0  # 10 min
JITTER_S = 180.0          # ±3 min

# Per-transition probabilities. Independent rolls — both can fire on the
# same transition (~20% of changes get both an outro and an intro).
DEFAULT_INTRO_CHANCE = 0.65
DEFAULT_OUTRO_CHANCE = 0.30

# Probability of picking each category at any given tick. Anything that isn't
# the time-of-day lore is "always available"; the lore weight only applies
# when the hour's lore bucket actually has spots in the baked library.
CATEGORY_WEIGHTS: dict[Category, float] = {
    "station_id": 4.0,
    "patter": 3.0,
    # Time-of-day buckets aren't listed here — `pick_spot` substitutes the
    # right one for the current hour at weight `LORE_WEIGHT`.
}
LORE_WEIGHT = 3.0


def available_spots(spots_dir: Path) -> dict[str, Path]:
    """Return id -> mp3 path for every Spot in SPOTS that's been baked."""
    out: dict[str, Path] = {}
    for s in SPOTS:
        p = spots_dir / f"{s.id}.mp3"
        if p.exists() and p.stat().st_size > 0:
            out[s.id] = p
    return out


def pick_spot(
    available: dict[str, Path],
    hour: int,
    rng: random.Random,
) -> Spot | None:
    """Choose a spot for the current hour. None if nothing is baked yet."""
    if not available:
        return None
    lore_cat = category_for_hour(hour)
    weights: dict[Category, float] = dict(CATEGORY_WEIGHTS)
    weights[lore_cat] = LORE_WEIGHT

    candidates_by_cat: dict[Category, list[Spot]] = {}
    for s in SPOTS:
        if s.id in available and s.category in weights:
            candidates_by_cat.setdefault(s.category, []).append(s)

    if not candidates_by_cat:
        return None
    cats = list(candidates_by_cat.keys())
    cat_weights = [weights[c] for c in cats]
    cat = rng.choices(cats, weights=cat_weights, k=1)[0]
    return rng.choice(candidates_by_cat[cat])


def pick_baked_intro_or_outro(
    track: Track,
    kind: str,
    intros_dir: Path,
    rng: random.Random,
) -> Path | None:
    """Find baked intro/outro mp3s for `track` and pick one at random.

    Returns None if no matching mp3s exist (track not baked yet, or no
    template of that kind produced a non-None render for this track).
    Pure(ish): the filesystem read is the only impurity, no time or
    voice-feeder dependency.
    """
    if kind not in ("intro", "outro"):
        raise ValueError(f"kind must be intro or outro, got {kind!r}")
    if not intros_dir.exists():
        return None
    # Filenames look like: "<track-stem>__<template-id>.mp3"
    prefix = f"{track.name}__{kind}_"
    candidates = [
        p for p in intros_dir.iterdir()
        if p.is_file() and p.suffix == ".mp3" and p.name.startswith(prefix)
           and p.stat().st_size > 0
    ]
    return rng.choice(candidates) if candidates else None


def select_transition_segments(
    prev: Track | None,
    current: Track,
    intros_dir: Path,
    rng: random.Random,
    intro_chance: float,
    outro_chance: float,
) -> list[Path]:
    """Roll dice and return the ordered list of voice mp3s to play at a transition.

    Pure-ish (filesystem read + rng); the scheduler hands the result to
    `JenniferPlayer.play_sequence`. Outro of the just-finished track plays
    first (if rolled and baked), then intro of the new track (same).
    Either or both may be absent — empty list means a silent transition.
    """
    segments: list[Path] = []
    if prev is not None and rng.random() < outro_chance:
        outro = pick_baked_intro_or_outro(prev, "outro", intros_dir, rng)
        if outro is not None:
            segments.append(outro)
    if rng.random() < intro_chance:
        intro = pick_baked_intro_or_outro(current, "intro", intros_dir, rng)
        if intro is not None:
            segments.append(intro)
    return segments


class JenniferScheduler:
    def __init__(
        self,
        voice_feeder: VoiceFeeder,
        spots_dir: Path = DEFAULT_SPOTS_DIR,
        intros_dir: Path = DEFAULT_INTROS_DIR,
        *,
        rng: random.Random | None = None,
        mean_interval_s: float = MEAN_INTERVAL_S,
        jitter_s: float = JITTER_S,
        first_delay_s: float | None = None,
        intro_chance: float = DEFAULT_INTRO_CHANCE,
        outro_chance: float = DEFAULT_OUTRO_CHANCE,
        # Dev-only: fire synthetic transitions on a timer instead of waiting
        # for real track changes. When set, the orchestrator should also stop
        # wiring MusicFeeder.on_track_change so the test loop is the sole
        # source of transitions (otherwise voice queue gets crowded).
        test_intros_interval_s: float | None = None,
        test_intros_music_dir: Path | None = None,
    ):
        self.voice_feeder = voice_feeder
        self.player = JenniferPlayer(voice_feeder)
        self.spots_dir = spots_dir
        self.intros_dir = intros_dir
        self.rng = rng or random.Random()
        self.mean_interval_s = mean_interval_s
        self.jitter_s = jitter_s
        # Quick first spot so listeners hear Jennifer near the top of the stream.
        self.first_delay_s = first_delay_s if first_delay_s is not None else 30.0
        self.intro_chance = intro_chance
        self.outro_chance = outro_chance
        self.test_intros_interval_s = test_intros_interval_s
        self.test_intros_music_dir = test_intros_music_dir
        self._stop = asyncio.Event()
        self._loop: asyncio.AbstractEventLoop | None = None

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        log.info("jennifer scheduler starting; spots_dir=%s intros_dir=%s",
                 self.spots_dir, self.intros_dir)
        # Capture the loop reference so the music-feeder thread can bridge
        # track-change events back into asyncio via call_soon_threadsafe;
        # also hand it to the voice feeder so its blocking-write thread can
        # signal playback-ack futures back to the player.
        self._loop = asyncio.get_running_loop()
        self.voice_feeder.set_event_loop(self._loop)

        # Dev-only: kick off the synthetic-transition test loop alongside
        # the periodic-spot loop. They share the voice queue, which is fine.
        if (self.test_intros_interval_s is not None
                and self.test_intros_music_dir is not None):
            asyncio.create_task(
                self._test_intros_loop(
                    self.test_intros_interval_s, self.test_intros_music_dir,
                ),
                name="jennifer_test_intros",
            )

        # Initial delay before the very first spot.
        if await self._sleep_or_stop(self.first_delay_s):
            return
        while not self._stop.is_set():
            await self._tick()
            interval = self._next_interval()
            log.debug("next spot in %.0fs", interval)
            if await self._sleep_or_stop(interval):
                return

    async def _test_intros_loop(self, interval_s: float, music_dir: Path) -> None:
        """Dev mode: fire synthetic prev→current transitions every interval_s.

        Bypasses MusicFeeder entirely. Useful for verifying intro/outro bake
        coverage and ducking behavior without waiting for natural 3-5min
        track transitions. The orchestrator should NOT also wire the music
        feeder's track-change callback in this mode (otherwise both sources
        emit transitions and the voice queue gets crowded).
        """
        library = load_library(music_dir)
        if not library:
            log.warning("test intros: no tagged tracks in %s; loop won't fire",
                        music_dir)
            return
        log.info(
            "test intros: firing synthetic transitions every %.0fs across "
            "%d tracks", interval_s, len(library),
        )
        prev: Track | None = None
        while not self._stop.is_set():
            if await self._sleep_or_stop(interval_s):
                return
            current = self.rng.choice(library)
            log.info("test transition: %s → %s",
                     prev.name if prev else "(none)", current.name)
            try:
                await self._play_transition(prev, current)
            except Exception:
                log.exception("test transition failed")
            prev = current

    def track_change_callback(self, prev: Track | None, current: Track) -> None:
        """Thread-safe entry point for `MusicFeeder.on_track_change`.

        Called from the music-feeder thread *before* the new track starts
        flowing. We hop back onto the asyncio loop and schedule the
        intro/outro playback there so all voice-feeder access stays on the
        async side.
        """
        if self._loop is None:
            # Scheduler hasn't entered run() yet — drop the event silently.
            # First-track events that race startup aren't worth crashing for.
            return
        if self._stop.is_set():
            return
        self._loop.call_soon_threadsafe(self._handle_track_change, prev, current)

    def _handle_track_change(self, prev: Track | None, current: Track) -> None:
        # Runs on the asyncio loop. Fire-and-forget the transition task.
        asyncio.create_task(
            self._play_transition(prev, current),
            name=f"jennifer_transition:{current.name}",
        )

    async def _play_transition(self, prev: Track | None, current: Track) -> None:
        """Pick + play the outro/intro segments for a track change."""
        segments = select_transition_segments(
            prev, current, self.intros_dir, self.rng,
            self.intro_chance, self.outro_chance,
        )
        if not segments:
            log.debug("transition %s→%s: silent (no rolled/baked segments)",
                      prev.name if prev else "(none)", current.name)
            return
        log.info("transition: %s", " → ".join(p.name for p in segments))
        try:
            await self.player.play_sequence(segments)
        except Exception:
            log.exception("transition playback failed for %s", current.name)

    async def _tick(self) -> None:
        available = available_spots(self.spots_dir)
        if not available:
            log.warning(
                "no baked spots in %s — run `python -m rcr.tools.generate_spots`",
                self.spots_dir,
            )
            return
        hour = _dt.datetime.now().hour
        spot = pick_spot(available, hour, self.rng)
        if spot is None:
            return
        try:
            await self.player.play_mp3(available[spot.id])
        except Exception:
            # Don't let one bad spot kill the scheduler — log and move on.
            log.exception("failed playing spot %s", spot.id)

    def _next_interval(self) -> float:
        return max(30.0, self.mean_interval_s + self.rng.uniform(-self.jitter_s, self.jitter_s))

    async def _sleep_or_stop(self, seconds: float) -> bool:
        """Sleep `seconds`, or return True early if stop() was called."""
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
            return True
        except asyncio.TimeoutError:
            return False
