"""Pure track-selection function.

Inputs: the tagged library, a ring buffer of recently-played paths, the last
track, the current arc state, and an RNG. Output: the next Track. No I/O, no
clocks (except via the explicit `now` argument), no global state — easy to
unit-test deterministically.

Selection algorithm (matches docs/architecture.md):
    1. Exclude the most-recent N tracks (ring-buffer filter).
       N = min(10, max(1, len(library) // 3)).
    2. Apply BPM/energy continuity vs. `last`. Tightest band first
       (±15 BPM, ±1 energy); relax stepwise (±25 BPM, then ±2 energy)
       until at least one candidate matches.
    3. Weight remaining candidates: tracks whose mood overlaps the current
       arc phase's mood set get a 2× bonus.
    4. Weighted random pick.

If the library is smaller than the recent-N window, the ring filter is
ignored. If continuity has no candidates even at the loosest band, we keep
the post-recent-filter pool unchanged.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from random import Random
from time import monotonic

from rcr.music.tracks import Track

ARC_SEQUENCE: tuple[str, ...] = ("chill", "mid", "peak", "mid", "chill")
ARC_BLOCK_SECONDS = 40 * 60  # one full chill→…→chill cycle

# Which mood tags count as "in phase" for each arc state. Tracks matching
# any of these tags get a weight bonus when the arc is in that phase.
ARC_MOODS: dict[str, frozenset[str]] = {
    "chill": frozenset(("chill", "dreamy", "rainy", "melancholy")),
    "mid":   frozenset(("groovy", "cinematic", "uplifting", "playful")),
    "peak":  frozenset(("peak", "intense", "driving", "menacing")),
}

ARC_BONUS = 2.0
BPM_TOL_RELAXATION_BANDS: tuple[tuple[float, int], ...] = (
    (15.0, 1),
    (25.0, 1),
    (25.0, 2),
)


@dataclass
class ArcState:
    """Soft 40-min mood arc.

    Phase indexing is purely a function of `now - started_at`. No mutation;
    persisted state is a single timestamp.
    """
    started_at: float = field(default_factory=monotonic)
    block_seconds: float = ARC_BLOCK_SECONDS

    def phase(self, now: float | None = None) -> str:
        if now is None:
            now = monotonic()
        elapsed = (now - self.started_at) % self.block_seconds
        slice_dur = self.block_seconds / len(ARC_SEQUENCE)
        idx = int(elapsed / slice_dur)
        # Defensive: idx == len(ARC_SEQUENCE) at exact period boundaries.
        return ARC_SEQUENCE[min(idx, len(ARC_SEQUENCE) - 1)]


def recent_n(library_size: int) -> int:
    return min(10, max(1, library_size // 3))


def select(
    library: list[Track],
    ring_buffer: deque[Path],
    last: Track | None,
    arc: ArcState,
    rng: Random,
    now: float | None = None,
) -> Track:
    if not library:
        raise ValueError("library is empty")

    # Step 1: ring-buffer filter
    n_recent = recent_n(len(library))
    recent = set(list(ring_buffer)[-n_recent:])
    pool = [t for t in library if t.path not in recent]
    if not pool:
        pool = list(library)  # library smaller than window — bypass

    # Step 2: BPM/energy continuity
    if last is not None:
        for bpm_tol, energy_tol in BPM_TOL_RELAXATION_BANDS:
            cont = [t for t in pool
                    if abs(t.bpm - last.bpm) <= bpm_tol
                    and abs(t.energy - last.energy) <= energy_tol]
            if cont:
                pool = cont
                break
        # If even the loosest band yields nothing, fall through with `pool`
        # unchanged. (Rare: the user has tracks that are wildly different
        # from each other.)

    # Step 3: arc weighting
    target_moods = ARC_MOODS.get(arc.phase(now), frozenset())
    weights = [
        ARC_BONUS if (target_moods & set(t.mood)) else 1.0
        for t in pool
    ]

    # Step 4: weighted pick
    return rng.choices(pool, weights=weights, k=1)[0]
