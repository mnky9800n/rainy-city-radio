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
   into asyncio happens via `plan_transition`, a sync method that hands
   off to `_plan_transition_async` via `run_coroutine_threadsafe` and
   blocks the feeder thread until a pause-duration is returned. The loop
   reference is captured in `run()`. M3.5 inline mode always returns 0
   (music plays under voice with ducking); M4.5 talk-break mode will
   return a positive duration so the music FIFO yields entirely while
   the voiced segment plays.

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
import concurrent.futures
import datetime as _dt
import logging
import random
import subprocess
from pathlib import Path

from rcr.jennifer.commercials import COMMERCIALS, Commercial
from rcr.jennifer.feeder import VoiceFeeder
from rcr.jennifer.player import JenniferPlayer
from rcr.jennifer.spots import SPOTS, Category, Spot, category_for_hour
from rcr.music.tracks import Track, load_library

log = logging.getLogger(__name__)

DEFAULT_SPOTS_DIR = Path("jennifer/spots")
DEFAULT_INTROS_DIR = Path("jennifer/track_intros")
DEFAULT_COMMERCIALS_DIR = Path("jennifer/commercials")

# Mean seconds between spots, with ±JITTER_S uniform noise. A real DJ would
# vary this further (denser when there's chat, sparser late night) but for
# v1 a simple jittered period is enough.
MEAN_INTERVAL_S = 600.0  # 10 min
JITTER_S = 180.0          # ±3 min

# Per-transition probabilities. Independent rolls — both can fire on the
# same transition (~20% of changes get both an outro and an intro).
DEFAULT_INTRO_CHANCE = 0.65
DEFAULT_OUTRO_CHANCE = 0.30

# How often (in track changes) the scheduler fires a talk-break instead of
# inline intros. 0 disables talk-breaks entirely. ~4 means a commercial
# segment every 4 tracks ≈ every 12-15 minutes given track lengths.
# When triggered, the music FIFO pauses for the duration of the commercial
# break (multiple commercials may play back-to-back; see below).
DEFAULT_TALK_BREAK_EVERY_N = 4

# Real radio commercial breaks contain 1-3 spots back-to-back; we copy
# that convention. The exact count is randomized per break, weighted
# toward 2 so most breaks have a couple of spots and occasional breaks
# are shorter or longer.
TALK_BREAK_COMMERCIAL_COUNT_CHOICES = (1, 2, 2, 2, 3)

# Small silent gap between commercials inside a break — about what real
# radio cuts to between spots. Implemented as part of the music-FIFO
# pause; voice playback just runs back-to-back through play_sequence.
INTER_COMMERCIAL_GAP_S = 0.5

# Sync wait on the planner coroutine from the music-feeder thread. Inline-
# intro planning is sub-millisecond (filesystem stat + task create); M4.5
# talk-break planning may pre-decode mp3s and take a few hundred ms. 30s is
# generous headroom — exceeding it means asyncio is hung and we should fall
# back to a no-pause transition rather than blocking the music feeder.
TRANSITION_PLAN_TIMEOUT_S = 30.0

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


def _baked_commercials(
    commercials_dir: Path,
) -> list[tuple[Commercial, Path]]:
    """Return every commercial whose voice+bed mp3 is baked, in catalog order."""
    if not commercials_dir.exists():
        return []
    out: list[tuple[Commercial, Path]] = []
    for c in COMMERCIALS:
        path = commercials_dir / f"{c.id}.mp3"
        if path.exists() and path.stat().st_size > 0:
            out.append((c, path))
    return out


def select_break_commercials(
    commercials_dir: Path,
    rng: random.Random,
    n: int,
) -> list[tuple[Commercial, Path]]:
    """Pick `n` distinct baked commercials for a single talk-break.

    Prefers category variety: round-robins through distinct categories
    first, only repeats a category once every category is used. Random
    within each category. Returns [] if nothing is baked yet (caller
    falls back to inline-intro mode).

    Pure-ish: filesystem read + rng; no time / feeder dependency.
    """
    all_baked = _baked_commercials(commercials_dir)
    if not all_baked:
        return []
    if n <= 0:
        return []
    if len(all_baked) <= n:
        # Catalog smaller than requested break — return everything shuffled.
        shuffled = list(all_baked)
        rng.shuffle(shuffled)
        return shuffled

    by_category: dict[str, list[tuple[Commercial, Path]]] = {}
    for entry in all_baked:
        by_category.setdefault(entry[0].category, []).append(entry)
    categories = list(by_category.keys())
    rng.shuffle(categories)

    picks: list[tuple[Commercial, Path]] = []
    used_ids: set[str] = set()

    # First pass: one per category, distinct ids.
    for cat in categories:
        if len(picks) >= n:
            break
        pick = rng.choice(by_category[cat])
        picks.append(pick)
        used_ids.add(pick[0].id)

    # If we still need more (more spots requested than categories available),
    # fill from the remaining pool — still no duplicate ids.
    while len(picks) < n:
        remaining = [e for e in all_baked if e[0].id not in used_ids]
        if not remaining:
            break
        pick = rng.choice(remaining)
        picks.append(pick)
        used_ids.add(pick[0].id)

    return picks


def probe_mp3_duration_s(path: Path) -> float:
    """Return the duration of an mp3 in seconds, via ffprobe.

    Used by the talk-break planner to know how long the music FIFO needs
    to pause. ffprobe is part of the ffmpeg toolchain we already depend on,
    so this is essentially free (no new dependency).
    """
    proc = subprocess.run(
        [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True, text=True, check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffprobe failed for {path}: rc={proc.returncode} "
            f"stderr={proc.stderr.strip()!r}"
        )
    return float(proc.stdout.strip())


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
        commercials_dir: Path = DEFAULT_COMMERCIALS_DIR,
        *,
        rng: random.Random | None = None,
        mean_interval_s: float = MEAN_INTERVAL_S,
        jitter_s: float = JITTER_S,
        first_delay_s: float | None = None,
        intro_chance: float = DEFAULT_INTRO_CHANCE,
        outro_chance: float = DEFAULT_OUTRO_CHANCE,
        talk_break_every_n: int = DEFAULT_TALK_BREAK_EVERY_N,
        # Dev-only: fire synthetic transitions on a timer instead of waiting
        # for real track changes. When set, the orchestrator should also stop
        # wiring MusicFeeder.transition_planner so the test loop is the sole
        # source of transitions (otherwise voice queue gets crowded).
        test_intros_interval_s: float | None = None,
        test_intros_music_dir: Path | None = None,
    ):
        self.voice_feeder = voice_feeder
        self.player = JenniferPlayer(voice_feeder)
        self.spots_dir = spots_dir
        self.intros_dir = intros_dir
        self.commercials_dir = commercials_dir
        self.rng = rng or random.Random()
        self.mean_interval_s = mean_interval_s
        self.jitter_s = jitter_s
        # Quick first spot so listeners hear Jennifer near the top of the stream.
        self.first_delay_s = first_delay_s if first_delay_s is not None else 30.0
        self.intro_chance = intro_chance
        self.outro_chance = outro_chance
        self.talk_break_every_n = talk_break_every_n
        self._tracks_since_talk_break = 0
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

    def plan_transition(self, prev: Track | None, current: Track) -> float:
        """Sync entry point for `MusicFeeder.transition_planner`.

        Called from the music-feeder thread *before* the new track starts
        flowing. Returns the number of seconds the music FIFO should pause
        (write silence) before playing `current`. M3.5 inline-intro mode
        returns 0.0 and side-effect-enqueues the voice segments onto the
        player; M4.5 talk-break mode will compute total voiced-segment
        duration and return it.

        Bridges to the asyncio loop via `run_coroutine_threadsafe`; the
        feeder thread blocks until the planner coroutine completes or the
        configured timeout fires.
        """
        if self._loop is None or self._stop.is_set():
            # Scheduler hasn't entered run() yet (or has been stopped) —
            # quietly return "no pause." First-track events that race
            # startup aren't worth crashing for.
            return 0.0
        coro = self._plan_transition_async(prev, current)
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return fut.result(timeout=TRANSITION_PLAN_TIMEOUT_S)
        except concurrent.futures.TimeoutError:
            log.warning("transition planner timed out; falling back to no pause")
            return 0.0
        except Exception:
            log.exception("transition planner crashed; falling back to no pause")
            return 0.0

    async def _plan_transition_async(
        self, prev: Track | None, current: Track,
    ) -> float:
        """Coroutine half of `plan_transition`. Runs on the asyncio loop.

        Two modes:

        1. **Talk-break (M4.5):** every `talk_break_every_n` transitions,
           pick a baked commercial, return its duration so the music
           FIFO pauses entirely while the commercial plays alone over
           the rain bed.
        2. **Inline intro (M3.5 default):** otherwise pick outro/intro
           segments and fire them fire-and-forget; music keeps playing
           under the ducked voice. Returns 0 (no pause).

        Talk-break falls back to inline mode if no commercials are baked
        yet — the wiring is forward-compatible with an empty commercial
        catalog (which is the current state on most hosts).
        """
        self._tracks_since_talk_break += 1
        if (self.talk_break_every_n > 0
                and self._tracks_since_talk_break >= self.talk_break_every_n):
            n_spots = self.rng.choice(TALK_BREAK_COMMERCIAL_COUNT_CHOICES)
            picks = select_break_commercials(self.commercials_dir, self.rng, n_spots)
            if picks:
                self._tracks_since_talk_break = 0
                return await self._fire_talk_break(picks)
            log.debug(
                "talk-break opportunity (%d since last) but no baked "
                "commercials in %s — falling back to inline mode",
                self._tracks_since_talk_break, self.commercials_dir,
            )

        # Inline-intro mode.
        segments = select_transition_segments(
            prev, current, self.intros_dir, self.rng,
            self.intro_chance, self.outro_chance,
        )
        if not segments:
            log.debug("transition %s→%s: silent (no rolled/baked segments)",
                      prev.name if prev else "(none)", current.name)
            return 0.0
        log.info("transition: %s", " → ".join(p.name for p in segments))
        # Fire-and-forget: segments queue on the voice feeder and play
        # back-to-back, paced naturally by the pipe-buffer backpressure.
        # The music feeder doesn't wait for them — inline mode means they
        # overlap with the start of `current` under the sidechain ducker.
        asyncio.create_task(
            self.player.play_sequence(segments),
            name=f"jennifer_transition:{current.name}",
        )
        return 0.0

    async def _fire_talk_break(
        self, picks: list[tuple[Commercial, Path]],
    ) -> float:
        """Kick off a multi-commercial break and return its total duration.

        Duration is sum of each commercial's mp3 length + a small inter-
        commercial gap, matching real-radio cadence. Voice playback runs
        through `play_sequence`; the music FIFO pauses for the whole
        window. No ducking — the commercials own the air.
        """
        if not picks:
            return 0.0
        durations: list[float] = []
        for commercial, mp3 in picks:
            try:
                d = await asyncio.to_thread(probe_mp3_duration_s, mp3)
            except Exception:
                log.exception("ffprobe failed for %s; aborting talk-break", mp3)
                return 0.0
            durations.append(d)
        gap_total = INTER_COMMERCIAL_GAP_S * (len(picks) - 1)
        total = sum(durations) + gap_total
        labels = " → ".join(f"{c.id} ({c.character}, {d:.1f}s)"
                            for (c, _), d in zip(picks, durations))
        log.info(
            "talk-break: %d spot%s, %.1fs total — %s",
            len(picks), "" if len(picks) == 1 else "s", total, labels,
        )
        asyncio.create_task(
            self.player.play_sequence([p[1] for p in picks]),
            name="jennifer_talk_break",
        )
        return total

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
