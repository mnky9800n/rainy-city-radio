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
from rcr.jennifer.spot_player import play_mp3
from rcr.jennifer.spots import SPOTS, Category, Spot, category_for_hour
from rcr.music.tracks import Track

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
    ):
        self.voice_feeder = voice_feeder
        self.spots_dir = spots_dir
        self.intros_dir = intros_dir
        self.rng = rng or random.Random()
        self.mean_interval_s = mean_interval_s
        self.jitter_s = jitter_s
        # Quick first spot so listeners hear Jennifer near the top of the stream.
        self.first_delay_s = first_delay_s if first_delay_s is not None else 30.0
        self.intro_chance = intro_chance
        self.outro_chance = outro_chance
        self._stop = asyncio.Event()
        self._loop: asyncio.AbstractEventLoop | None = None

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        log.info("jennifer scheduler starting; spots_dir=%s intros_dir=%s",
                 self.spots_dir, self.intros_dir)
        # Capture the loop reference so the music-feeder thread can bridge
        # track-change events back into asyncio via call_soon_threadsafe.
        self._loop = asyncio.get_running_loop()
        # Initial delay before the very first spot.
        if await self._sleep_or_stop(self.first_delay_s):
            return
        while not self._stop.is_set():
            await self._tick()
            interval = self._next_interval()
            log.debug("next spot in %.0fs", interval)
            if await self._sleep_or_stop(interval):
                return

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
        """Roll dice for outro (prev) + intro (current); play either or both."""
        if prev is not None and self.rng.random() < self.outro_chance:
            mp3 = pick_baked_intro_or_outro(prev, "outro", self.intros_dir, self.rng)
            if mp3 is not None:
                log.info("transition outro: %s", mp3.name)
                try:
                    await play_mp3(self.voice_feeder, mp3)
                except Exception:
                    log.exception("outro playback failed for %s", prev.name)
            else:
                log.debug("no baked outro for %s", prev.name)
        if self.rng.random() < self.intro_chance:
            mp3 = pick_baked_intro_or_outro(current, "intro", self.intros_dir, self.rng)
            if mp3 is not None:
                log.info("transition intro: %s", mp3.name)
                try:
                    await play_mp3(self.voice_feeder, mp3)
                except Exception:
                    log.exception("intro playback failed for %s", current.name)
            else:
                log.debug("no baked intro for %s", current.name)

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
            await play_mp3(self.voice_feeder, available[spot.id])
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
