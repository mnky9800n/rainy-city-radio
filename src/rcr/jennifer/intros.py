"""Templated track intro/outro lines.

Each template is a pure function `(Track) -> str | None`. None means the
template doesn't apply to this track (e.g., a template that references the
release name returns None for tracks without one), so the bake tool can
skip cleanly without producing a sidecar that mentions a missing field.

Render is deterministic: same Track in, same string out. That's the
contract that lets the voicer cache work — `sha256(text + voice_id)` keys
into `jennifer/voices/`, so re-baking the same track yields cache hits and
no ElevenLabs cost.

Variety: each track typically has 5-8 applicable templates depending on
which optional fields its sidecar carries (release, mood, etc.). At
playback time the scheduler picks one at random per transition, so the
same track played twice in a row gets different framing.

Tone reference: see `docs/lore.md`. Earnest + dry-humor Jennifer. No
overproduced "COMING UP HOT TRACKS ALL NIGHT" energy.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal

from rcr.music.tracks import Track

Kind = Literal["intro", "outro"]


@dataclass(frozen=True)
class IntroTemplate:
    id: str
    kind: Kind
    render: Callable[[Track], str | None]


def _first_mood(t: Track) -> str | None:
    return t.mood[0] if t.mood else None


# ---------------------------------------------------------------------------
# Intros — talk about the *upcoming* track
# ---------------------------------------------------------------------------

def intro_simple(t: Track) -> str | None:
    if not t.display_artist:
        return None
    return f"Up next on 99X — {t.display_title}, by {t.display_artist}."


def intro_release(t: Track) -> str | None:
    if not (t.release and t.display_artist):
        return None
    return f"From the {t.release} record, here's {t.display_title}, by {t.display_artist}."


def intro_mood(t: Track) -> str | None:
    m = _first_mood(t)
    if not (m and t.display_artist):
        return None
    return f"Keeping it {m}. Here's {t.display_artist} with {t.display_title}."


def intro_vibe(t: Track) -> str | None:
    if not t.display_artist:
        return None
    return f"{t.display_artist} — {t.display_title}. Let it sit with you."


def intro_cult_flavor(t: Track) -> str | None:
    if not t.display_artist:
        return None
    return (
        f"Rain's not letting up. Neither are we. "
        f"Here's {t.display_title}, by {t.display_artist}."
    )


# ---------------------------------------------------------------------------
# Outros — talk about the *just-finished* track
# ---------------------------------------------------------------------------

def outro_simple(t: Track) -> str | None:
    if not t.display_artist:
        return None
    return f"That was {t.display_title}, by {t.display_artist}."


def outro_release(t: Track) -> str | None:
    if not (t.release and t.display_artist):
        return None
    return f"{t.display_title}, off the {t.release} record. {t.display_artist} on 99X."


def outro_mood(t: Track) -> str | None:
    m = _first_mood(t)
    if not (m and t.display_artist):
        return None
    return f"{t.display_artist} with {t.display_title} — a {m} one."


def outro_thanks(t: Track) -> str | None:
    # When real_artist is set we're playing a CC track; the thanks line both
    # honors attribution and reads as natural radio. For Suno tracks the
    # fictional artist is in-universe, so the thanks reads as Jennifer
    # appreciating a colleague — also fine.
    if not t.display_artist:
        return None
    return f"Big thanks to {t.display_artist} for {t.display_title}. 99X."


def outro_lore(t: Track) -> str | None:
    if not t.display_artist:
        return None
    return f"{t.display_artist}, with {t.display_title}. The rain knows."


ALL_TEMPLATES: tuple[IntroTemplate, ...] = (
    IntroTemplate("intro_simple", "intro", intro_simple),
    IntroTemplate("intro_release", "intro", intro_release),
    IntroTemplate("intro_mood", "intro", intro_mood),
    IntroTemplate("intro_vibe", "intro", intro_vibe),
    IntroTemplate("intro_cult_flavor", "intro", intro_cult_flavor),
    IntroTemplate("outro_simple", "outro", outro_simple),
    IntroTemplate("outro_release", "outro", outro_release),
    IntroTemplate("outro_mood", "outro", outro_mood),
    IntroTemplate("outro_thanks", "outro", outro_thanks),
    IntroTemplate("outro_lore", "outro", outro_lore),
)

INTROS: tuple[IntroTemplate, ...] = tuple(t for t in ALL_TEMPLATES if t.kind == "intro")
OUTROS: tuple[IntroTemplate, ...] = tuple(t for t in ALL_TEMPLATES if t.kind == "outro")


def applicable(track: Track, kind: Kind | None = None) -> tuple[IntroTemplate, ...]:
    """Return the templates that produce a non-None render for `track`.

    Pass kind="intro" or kind="outro" to filter; default returns both.
    """
    pool = ALL_TEMPLATES if kind is None else tuple(
        t for t in ALL_TEMPLATES if t.kind == kind
    )
    return tuple(t for t in pool if t.render(track) is not None)
