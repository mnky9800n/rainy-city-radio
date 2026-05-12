"""Decides *when* Jennifer talks and picks what she says.

M3 step 2 scope:
    - Periodic spot every ~10 min (with jitter), pulled from the baked pool
      in jennifer/spots/<id>.mp3.
    - Category weights bias toward station IDs (most frequent), with patter
      mixed in and a time-of-day lore drop swapped in occasionally.

Deferred to a later sub-step:
    - Track-aware intros ("That was X. Up next, Y.") — needs a track-change
      event from MusicFeeder that doesn't exist yet.
    - Chat-reactive replies — M4.

The `pick_spot` function is pure (library, hour, rng) -> Spot|None so it's
trivially unit-testable; the loop in `JenniferScheduler.run()` is the only
piece that touches wall-clock time and the voice feeder.
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

log = logging.getLogger(__name__)

DEFAULT_SPOTS_DIR = Path("jennifer/spots")

# Mean seconds between spots, with ±JITTER_S uniform noise. A real DJ would
# vary this further (denser when there's chat, sparser late night) but for
# v1 a simple jittered period is enough.
MEAN_INTERVAL_S = 600.0  # 10 min
JITTER_S = 180.0          # ±3 min

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


class JenniferScheduler:
    def __init__(
        self,
        voice_feeder: VoiceFeeder,
        spots_dir: Path = DEFAULT_SPOTS_DIR,
        *,
        rng: random.Random | None = None,
        mean_interval_s: float = MEAN_INTERVAL_S,
        jitter_s: float = JITTER_S,
        first_delay_s: float | None = None,
    ):
        self.voice_feeder = voice_feeder
        self.spots_dir = spots_dir
        self.rng = rng or random.Random()
        self.mean_interval_s = mean_interval_s
        self.jitter_s = jitter_s
        # Quick first spot so listeners hear Jennifer near the top of the stream.
        self.first_delay_s = first_delay_s if first_delay_s is not None else 30.0
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        log.info("jennifer scheduler starting; spots_dir=%s", self.spots_dir)
        # Initial delay before the very first spot.
        if await self._sleep_or_stop(self.first_delay_s):
            return
        while not self._stop.is_set():
            await self._tick()
            interval = self._next_interval()
            log.debug("next spot in %.0fs", interval)
            if await self._sleep_or_stop(interval):
                return

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
